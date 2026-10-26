#!/usr/bin/env python3
# filename: noaa_capture_check.py
# description: SPEC_e4_noaa_release R12-1 checks on one capture-only nrjxl still, on the Mac (no Spotter, no backend).
"""
Check one nrjxl still pulled from a unit after a capture-only run (bmcam002 on
update day, bmcam004 in the rehearsal) against SPEC_e4 R12-1:
  1. crop 1600x900 at [1504, 846] and params[26] = 10000 (native density);
  2. it parses with the nvd `staging` decoder (backend/app/services/nrjxl.py at
     the sha deployed that morning) and its JPEG XL stream decodes with djxl to
     a 1600x900 image, no error;
  4. 0 rfb: the run log's "[RC] send plan (NO transmit)" line says fmt=nrjxl and
     carries no "rfb=" (rc_progressive_jpeg.py prints rfb only on a fallback).
Check 3 (the --print-config hash) is a runbook step, not this tool.

Inputs:  --nvd DIR   a `git archive <staging sha> backend/app/services/nrjxl.py`
                     export (stdlib only; nothing from the backend is imported
                     beyond that one file)
         BLOB        the .nrjxl file
         --log FILE  the run's stdout log (for check 4)
Outputs: one line per check, then CHECK PASS / CHECK FAIL.
Exit:    0 all checks pass, 1 any check fails, 2 usage / file error.

Example (Mac):
  git -C ~/Documents/GitHub/nereus-vision-dev archive <staging sha> \
      backend/app/services/nrjxl.py | tar -x -C /tmp/nvd_<sha>
  /usr/bin/python3 -I tools/noaa_capture_check.py --nvd /tmp/nvd_<sha> \
      --log capture_check.log images/<stem>.nrjxl
Known limitations: needs djxl on PATH (Homebrew jpeg-xl). Checks one blob; it
does not judge how the picture looks (the runbook says to open it and look).
"""

import argparse
import os
import re
import struct
import subprocess
import sys
import tempfile

WANT_CROP = (1504, 846, 1600, 900)
WANT_SCALE = 10000
WANT_CODED = (1600, 900)


def png_size(path):
    with open(path, "rb") as f:
        head = f.read(24)
    if head[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("djxl output is not a PNG")
    return struct.unpack(">II", head[16:24])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--nvd", required=True, help="nvd staging export holding backend/app/services/nrjxl.py")
    ap.add_argument("--log", default=None, help="the capture run's stdout log (check 4)")
    ap.add_argument("blob")
    args = ap.parse_args(argv)

    services = os.path.join(args.nvd, "backend", "app", "services")
    if not os.path.isfile(os.path.join(services, "nrjxl.py")):
        print(f"[CHECK][ERROR] no backend/app/services/nrjxl.py under {args.nvd}", file=sys.stderr)
        return 2
    sys.path.insert(0, services)
    import nrjxl  # the nvd staging decoder

    try:
        with open(args.blob, "rb") as f:
            header, payloads = nrjxl.parse(f.read())
    except OSError as exc:
        print(f"[CHECK][ERROR] {exc}", file=sys.stderr)
        return 2
    except nrjxl.NrjxlError as exc:
        print(f"[CHECK] parse: FAIL ({exc})")
        print("CHECK FAIL")
        return 1

    ok = True
    c1 = header.crop == WANT_CROP and header.output_scale_x1e4 == WANT_SCALE
    print(f"[CHECK] 1 crop={list(header.crop)} params[26]={header.output_scale_x1e4} "
          f"(want {list(WANT_CROP)}, {WANT_SCALE}): {'PASS' if c1 else 'FAIL'}")
    ok &= c1

    c2 = header.is_rgb and len(payloads) == 1 and header.coded_size == WANT_CODED
    detail = f"rgb={header.is_rgb} streams={len(payloads)} coded={header.coded_size}"
    if c2:
        with tempfile.TemporaryDirectory() as tmp:
            jxl, png = os.path.join(tmp, "still.jxl"), os.path.join(tmp, "still.png")
            with open(jxl, "wb") as f:
                f.write(payloads[0])
            r = subprocess.run(["djxl", jxl, png], capture_output=True, text=True)
            if r.returncode != 0:
                c2, detail = False, f"{detail} djxl exit {r.returncode}: {r.stderr.strip()[-200:]}"
            else:
                size = png_size(png)
                c2 = size == WANT_CODED
                detail = f"{detail} decoded={size[0]}x{size[1]}"
    print(f"[CHECK] 2 decode with nvd staging nrjxl.py + djxl: {detail}: {'PASS' if c2 else 'FAIL'}")
    ok &= c2

    if args.log:
        with open(args.log, "r", errors="replace") as f:
            plan = [ln for ln in f if "[RC] send plan (NO transmit)" in ln]
        c4 = bool(plan) and "fmt=nrjxl" in plan[-1] and not re.search(r"\brfb=", plan[-1])
        print(f"[CHECK] 4 0 rfb: {plan[-1].strip() if plan else 'no send-plan line in the log'}: "
              f"{'PASS' if c4 else 'FAIL'}")
        ok &= c4
    else:
        print("[CHECK] 4 0 rfb: not checked (no --log)")
        ok = False

    print(f"CHECK {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

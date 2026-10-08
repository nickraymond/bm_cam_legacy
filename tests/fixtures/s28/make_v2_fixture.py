#!/usr/bin/env python3
# filename: make_v2_fixture.py
# description: Sprint28 B3a: rebuild the profile-v2 fixture blob_v2_bmcam004_57521.nrjxl from bmcam004 media 57521 with the PRODUCTION geometry (still.crop origin 1504,846, native 4608x2592), taken from 57521's own v1 header.
"""
Why: the first v2 fixture (385395c) was encoded from the DNG unpacked from 57521's v1 nrjxl,
which is crop-sized, so its params[1..4] said 0,0 / 1600x900. A unit reads the FULL-sensor DNG
at still.crop, so production v2 carries the real origin / native, as v1 does (backend question,
2026-10-06). This builder runs the production B3a steps (coding_gains / headroom_x10000 /
rgb_codes / write_ppm, then choose_rate: fill 0.97, cap 195, cjxl e5 VarDCT) on the stored crop
mosaic and gives choose_rate the REAL crop_xywh and native size from the v1 header.
Inputs:  --dng (57521 __unpacked.dng, 1600x900), --v1 (57521's v1 .nrjxl: geometry + colour).
Outputs: blob_v2_bmcam004_57521.nrjxl + blobs_v2.json (sha256, d, msgs, geometry) here.
Example: .venv-dev/bin/python tests/fixtures/s28/make_v2_fixture.py --dng X__unpacked.dng --v1 X.nrjxl
Limits: the mosaic is the LOSSY decode of the v1 blob (a pipeline fixture, not a quality reference).
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO, "tools"))


class _Budget:
    seconds_per_message = 1.3

    @staticmethod
    def remaining_s():
        return 100000.0

    @staticmethod
    def messages_fit(n):
        return True


def main(argv=None):
    import rc_raw_jxl as X
    from s28_b3a_e2e_check import meta_from_nrjxl
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--dng", required=True)
    ap.add_argument("--v1", required=True)
    args = ap.parse_args(argv)
    with open(args.v1, "rb") as fh:
        v1 = fh.read()
    head, _ = X.unpack_container(v1)
    p = head["params"]
    crop_xywh = [p[1], p[2], head["w"], head["h"]]
    native = (p[3], p[4])
    meta = meta_from_nrjxl(args.v1)
    colour = X.colour_params(meta)
    crop = X.read_dng_crop(args.dng, [0, 0, head["w"], head["h"]])
    assert crop["cfa"] == head["cfa"] and (crop["black"], crop["white"]) == (head["black"], head["white"])
    g = X.coding_gains(colour)
    hx = X.headroom_x10000(crop["mosaic"], crop["cfa"], crop["black"], crop["white"], g)
    work = tempfile.mkdtemp(prefix="v2fix_")
    X.write_ppm(os.path.join(work, X.RGB_PPM),
                X.rgb_codes(crop["mosaic"], crop["cfa"], crop["black"], crop["white"], g, hx))
    slim = dict(X.slim_crop(crop), native_w=native[0], native_h=native[1], headroom_x10000=hx)
    cfg = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb")
    res = X.choose_rate(slim, None, colour, cfg, crop_xywh=crop_xywh, budget=_Budget,
                        message_cap=195, chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=195,
                        work_dir=work, cjxl=shutil.which("cjxl"), runner=X.run_capped,
                        log=lambda *_: None)
    blob = res["blob"]
    h2, _ = X.unpack_container(blob)
    assert h2["params"][1:5] == [p[1], p[2], p[3], p[4]], h2["params"][1:5]
    name = "blob_v2_bmcam004_57521.nrjxl"
    with open(os.path.join(HERE, name), "wb") as fh:
        fh.write(blob)
    cjxl_v = subprocess.run([shutil.which("cjxl"), "--version"], capture_output=True,
                            text=True).stdout.split("\n")[0]
    info = {"container": "profile v2 (DESIGN_B3a.md §2, method 20)",
            "blobs": {name: {"sha256": hashlib.sha256(blob).hexdigest(), "bytes": len(blob),
                             "distance": res["distance"], "attempts": res["attempts"],
                             "msgs_384": res["message_count"], "headroom_x10000": hx,
                             "crop_xywh": crop_xywh, "native_wh": list(native),
                             "source": "bmcam004 media 57521 (SPOT-31593C 2026-10-05T16:00:29Z): the "
                                       "crop mosaic unpacked from its v1 nrjxl (lossy), geometry + "
                                       "colour params from that v1 header (sha256 "
                                       f"{hashlib.sha256(v1).hexdigest()})",
                             "encoder": f"rc_raw_jxl B3a production steps, {cjxl_v}, e5",
                             "builder": "tests/fixtures/s28/make_v2_fixture.py"}}}
    with open(os.path.join(HERE, "blobs_v2.json"), "w") as fh:
        json.dump(info, fh, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    print(json.dumps(info["blobs"][name], indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())

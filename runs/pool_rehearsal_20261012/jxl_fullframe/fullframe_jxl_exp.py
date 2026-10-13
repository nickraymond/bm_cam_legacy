#!/usr/bin/env python3
# filename: fullframe_jxl_exp.py
# description: EM quick experiment 2026-10-12 PM (not for the pool): is FULL-FRAME raw -> JPEG XL viable on the Pi Zero 2W?
"""
Runs ON the Pi (bmcam004), outside pool_runs/. For each given calibration DNG (+ its .metadata.json):
  prep  = the PRODUCTION B3a path of BM_Devel_Pi/rc_raw_jxl.py at crop (0,0,4608,2592) instead of the 1600x900 crop:
          read_dng_crop -> colour_params -> coding_gains -> headroom_x10000 -> rgb_codes (bilinear demosaic, sqrt 12-bit)
          -> write_ppm (P6, maxval 4095)
  enc   = cjxl with the production argv (cjxl_rgb_command: -m 0 -e 5 --num_threads=0) at two settings:
            "d1"       -d 1.0
            "lossless" -d 0  (if VarDCT refuses lossless, retried with -m 1 and recorded)
          watched every 50 ms: VmPeak / VmHWM from /proc; killed if VmPeak > 300 MiB (EM stop rule) or > 900 s.
  check = djxl -> PPM, compared with the source PPM in row strips (np.memmap, no full-frame float arrays):
          12-bit code domain: max |err|, mean |err|, PSNR (peak 4095);
          linear raw domain (v = (code/4095)^2, i.e. (raw-black)/(white-black) x g/s): PSNR (peak 1).
Outputs: <work>/results.jsonl (one JSON line per frame x setting) + <work>/run.log. The .ppm/.jxl/decoded files are
deleted after each frame (only results kept). Coordinates: native sensor px, full frame.
Usage (on the Pi): python3 fullframe_jxl_exp.py <work_dir> <dng> [<dng> ...]
Limits: Pi clock timings; VmPeak is virtual (the EM bar), VmHWM is resident; prep time is the Python demosaic.
"""
import json
import os
import subprocess
import sys
import time

sys.path.insert(0, "/home/pi/BM_Devel_Pi")
import numpy as np            # noqa: E402
import rc_raw_jxl as R        # noqa: E402

FULL = (0, 0, 4608, 2592)
VMPEAK_STOP_KB = 300 * 1024
EXTRA = os.environ.get("CJXL_EXTRA", "").split()   # e.g. "--streaming_input --streaming_output" (variant run)
TIMEOUT_S = 900


def log(work, msg):
    line = f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {msg}"
    print(line, flush=True)
    with open(os.path.join(work, "run.log"), "a") as f:
        f.write(line + "\n")


def self_hwm_kb():
    with open("/proc/self/status") as f:
        for line in f:
            if line.startswith("VmHWM:"):
                return int(line.split()[1])
    return None


def run_watched(cmd):
    """-> dict(rc, seconds, vmpeak_kb, vmhwm_kb, killed)"""
    t0 = time.monotonic()
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL)
    peak = hwm = 0
    killed = None
    while p.poll() is None:
        try:
            with open(f"/proc/{p.pid}/status") as f:
                for line in f:
                    if line.startswith("VmPeak:"):
                        peak = max(peak, int(line.split()[1]))
                    elif line.startswith("VmHWM:"):
                        hwm = max(hwm, int(line.split()[1]))
        except OSError:
            pass
        if peak > VMPEAK_STOP_KB:
            p.kill(); killed = "vmpeak>300MiB"
        elif time.monotonic() - t0 > TIMEOUT_S:
            p.kill(); killed = "timeout"
        time.sleep(0.05)
    out, err = p.communicate()
    return {"rc": p.returncode, "seconds": round(time.monotonic() - t0, 2), "vmpeak_kb": peak,
            "vmhwm_kb": hwm, "killed": killed, "stderr_tail": err.decode(errors="replace")[-300:]}


def ppm_map(path):
    """-> (memmap uint16 h x w x 3 big-endian values as native, maxval)"""
    with open(path, "rb") as f:
        head = b""
        fields = []
        while len(fields) < 4:
            ch = f.read(1)
            head += ch
            if ch.isspace():
                tok = head.strip()
                if tok:
                    fields.append(tok)
                head = b""
        off = f.tell()
    assert fields[0] == b"P6", fields
    w, h, maxval = int(fields[1]), int(fields[2]), int(fields[3])
    dt = np.dtype(">u2") if maxval > 255 else np.dtype("u1")
    return np.memmap(path, dtype=dt, mode="r", offset=off, shape=(h, w, 3)), maxval


def compare(src_ppm, dec_ppm, strip=64):
    a, amax = ppm_map(src_ppm)
    b, bmax = ppm_map(dec_ppm)
    assert a.shape == b.shape, (a.shape, b.shape)
    scale = amax / bmax if bmax != amax else 1.0
    n = 0; sabs = 0.0; sse = 0.0; sse_lin = 0.0; mx = 0.0
    for y in range(0, a.shape[0], strip):
        x = a[y:y + strip].astype(np.float64)
        z = b[y:y + strip].astype(np.float64) * scale
        d = z - x
        mx = max(mx, float(np.abs(d).max()))
        sabs += float(np.abs(d).sum()); sse += float((d * d).sum())
        dl = (z / amax) ** 2 - (x / amax) ** 2
        sse_lin += float((dl * dl).sum())
        n += d.size
    mse, mse_lin = sse / n, sse_lin / n
    psnr = float("inf") if mse == 0 else 20 * np.log10(amax / np.sqrt(mse))
    psnr_lin = float("inf") if mse_lin == 0 else 10 * np.log10(1.0 / mse_lin)
    return {"src_maxval": amax, "dec_maxval": bmax, "max_abs_code": mx, "mean_abs_code": round(sabs / n, 4),
            "psnr_code_db": round(psnr, 2) if np.isfinite(psnr) else "inf",
            "psnr_linear_db": round(psnr_lin, 2) if np.isfinite(psnr_lin) else "inf"}


def main():
    work, dngs = sys.argv[1], sys.argv[2:]
    os.makedirs(work, exist_ok=True)
    cjxl, djxl = "/usr/bin/cjxl", "/usr/bin/djxl"
    for dng in dngs:
        stem = os.path.basename(dng)[:-4]
        meta = json.load(open(dng[:-4] + ".metadata.json"))
        t0 = time.monotonic()
        crop = R.read_dng_crop(dng, FULL)
        mp = R.colour_params(meta)
        g = R.coding_gains(mp)
        hx = R.headroom_x10000(crop["mosaic"], crop["cfa"], crop["black"], crop["white"], g)
        rgb = R.rgb_codes(crop["mosaic"], crop["cfa"], crop["black"], crop["white"], g, hx)
        ppm = os.path.join(work, stem + ".ppm")
        R.write_ppm(ppm, rgb)
        shape = list(rgb.shape)
        del rgb, crop
        prep = {"prep_s": round(time.monotonic() - t0, 2), "python_vmhwm_kb": self_hwm_kb(),
                "ppm_bytes": os.path.getsize(ppm), "shape": shape, "headroom_x10000": hx}
        log(work, f"{stem} prep {prep}")
        for name, dist in (("d1", 1.0), ("lossless", 0.0)):
            out = os.path.join(work, f"{stem}_{name}.jxl")
            cmd = R.cjxl_rgb_command(cjxl, ppm, out, dist, 5) + EXTRA
            r = run_watched(cmd)
            argv_used = cmd
            if r["rc"] != 0 and name == "lossless" and not r["killed"]:
                cmd = R.cjxl_command(cjxl, ppm, out, dist, 5) + EXTRA  # modular (-m 1), the planes argv
                r2 = run_watched(cmd)
                r2["first_try_vardct"] = {k: r[k] for k in ("rc", "seconds", "stderr_tail")}
                r, argv_used = r2, cmd
            rec = {"frame": stem, "setting": name + ("+" + "+".join(EXTRA) if EXTRA else ""), "argv": argv_used[3:], **prep, **r,
                   "jxl_bytes": os.path.getsize(out) if os.path.exists(out) else None}
            if r["rc"] == 0 and os.path.exists(out):
                dec = os.path.join(work, f"{stem}_{name}_dec.ppm")
                t1 = time.monotonic()
                d = subprocess.run([djxl, out, dec], capture_output=True)
                rec["djxl_s"] = round(time.monotonic() - t1, 2)
                if d.returncode == 0:
                    rec.update(compare(ppm, dec))
                else:
                    rec["djxl_err"] = d.stderr.decode(errors="replace")[-200:]
                for f in (dec, out):
                    if os.path.exists(f):
                        os.remove(f)
            log(work, json.dumps(rec))
            with open(os.path.join(work, "results.jsonl"), "a") as f:
                f.write(json.dumps(rec) + "\n")
        os.remove(ppm)
    log(work, "done")


if __name__ == "__main__":
    main()

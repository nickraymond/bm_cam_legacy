#!/usr/bin/env python3
# filename: s28_b3a_search_calib.py
# description: Sprint28 B3a byte-search calibration (bmcam004 B0: 3 attempts every run, search 28 s of 30): measure cjxl VarDCT bytes(d) on real frames, then REPLAY the production rc_raw_jxl.choose_rate on those curves to get the attempts distribution per prior / slope / accept window.
"""
measure: for one frame (a TG-7 ORF via the rig mapping, or an IMX708 DNG + params), build
         the PRODUCTION B3a input (rc_raw_jxl.coding_gains / headroom_x10000 / rgb_codes) at
         1600x900 and record e5 bytes at a d grid (and e4 at 3 points, with times) ->
         <out>/curves/<label>.json. cjxl bytes do not depend on the host's speed (the same libjxl
         version gives the same bytes); times are this host's.
simulate: for every curve, run the REAL choose_rate (fill 0.97, cap 195, the walk's guards) with
         a runner that returns bytes(d) by log-log interpolation of the curve and a fake clock
         that charges --e5-s per encode (bmcam004 B0: 9.5 s); report the attempts distribution,
         the fill, the search seconds and rfb, for the current prior and the candidates.
Example:
  PYTHONPATH=$RIG/src:$RIG <rig venv python> tools/s28_b3a_search_calib.py measure --rig $RIG \\
      --orf X.orf --label tg7_X --out runs/s28_b3a_search_20261006
  python3 tools/s28_b3a_search_calib.py simulate --out runs/s28_b3a_search_20261006
"""

import argparse
import glob
import json
import math
import os
import shutil
import subprocess
import sys
import tempfile
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))
GRID = [1.2, 1.4, 1.6, 1.8, 2.0, 2.2, 2.4, 2.6, 2.8, 3.0, 3.3, 3.6, 4.0, 4.5, 5.0, 6.0, 7.5]
E4_GRID = [2.0, 2.6, 3.4]


def measure(args):
    import numpy as np
    import rc_raw_jxl as X
    if args.orf:
        sys.path[:0] = [os.path.join(args.rig, "src"), args.rig]
        from compression_study.preview_loss import tg7_budget as TG
        from host_tools.tg7.orf_io import read_orf
        f = read_orf(args.orf)
        scene, _, _ = TG.map_to_imx(f, (1600, 900))
        mos, cfa, black, white = scene["mosaic"], scene["cfa"], scene["black"], scene["white"]
        g3 = np.asarray(scene["gains"], np.float64)
        meta = {"ExposureTime": float(f.exposure_s) * 1e6, "AnalogueGain": float(f.iso) / 100,
                "ColourGains": [float(g3[0] / g3[1]), float(g3[2] / g3[1])],
                "ColourCorrectionMatrix": [float(v) for v in np.asarray(scene["ccm"]).ravel()],
                "DigitalGain": 1.0}
        src = {"orf": args.orf, "note": "TG-7 resampled to IMX708 geometry, an approximation"}
    else:
        sys.path.insert(0, os.path.join(REPO, "tools"))
        from s28_b3a_e2e_check import meta_from_nrjxl
        crop = X.read_dng_crop(args.dng, [int(v) for v in args.crop.split(",")])
        mos, cfa, black, white = crop["mosaic"], crop["cfa"], crop["black"], crop["white"]
        meta = meta_from_nrjxl(args.params_from) if args.params_from else json.load(open(args.metadata))
        src = {"dng": args.dng}
    g = X.coding_gains(X.colour_params(meta))
    hx = X.headroom_x10000(mos, cfa, black, white, g)
    work = tempfile.mkdtemp(prefix="s28cal_")
    ppm = os.path.join(work, X.RGB_PPM)
    X.write_ppm(ppm, X.rgb_codes(mos, cfa, black, white, g, hx))
    cjxl = shutil.which("cjxl")

    def enc(d, e):
        dst = os.path.join(work, "x.jxl")
        t0 = time.monotonic()
        subprocess.run(X.cjxl_rgb_command(cjxl, ppm, dst, d, e) + ["--quiet"], check=True,
                       capture_output=True)
        return os.path.getsize(dst), round(time.monotonic() - t0, 3)
    e5 = [[d, *enc(d, 5)] for d in GRID]
    e4 = [[d, *enc(d, 4)] for d in E4_GRID]
    out = {"label": args.label, "source": src, "headroom_x10000": hx,
           "cjxl": subprocess.run([cjxl, "--version"], capture_output=True, text=True).stdout.split("\n")[0],
           "e5": e5, "e4": e4}
    os.makedirs(os.path.join(args.out, "curves"), exist_ok=True)
    with open(os.path.join(args.out, "curves", f"{args.label}.json"), "w") as fh:
        json.dump(out, fh, indent=1)
    shutil.rmtree(work, ignore_errors=True)
    print(f"[CAL] {args.label}: {[(d, b) for d, b, _ in e5]}", flush=True)
    return 0


def interp_bytes(curve, d):
    """log-log linear interpolation (extrapolation at the ends) of bytes(d)."""
    pts = sorted((math.log(p[0]), math.log(p[1])) for p in curve)
    x = math.log(d)
    i = 1
    while i < len(pts) - 1 and pts[i][0] < x:
        i += 1
    (x0, y0), (x1, y1) = pts[i - 1], pts[i]
    return math.exp(y0 + (y1 - y0) * (x - x0) / (x1 - x0))


class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def simulate_one(curve, prior=None, slack=None, encode_max_s=30, e5_s=9.5, e4_s=1.5, cfg_extra=None):
    """-> dict(attempts, fill, d, seconds, rfb) of the REAL choose_rate on this curve."""
    import rc_raw_jxl as X
    saved = (X.PRIOR_RGB, X.SEARCH_FILL_SLACK, getattr(X, "SEARCH_FILL_SLACK_RGB", None))
    if prior:
        X.PRIOR_RGB = prior
    if slack is not None:
        X.SEARCH_FILL_SLACK = slack
        if hasattr(X, "SEARCH_FILL_SLACK_RGB"):
            X.SEARCH_FILL_SLACK_RGB = slack
    clock = _Clock()

    class Budget:
        seconds_per_message = 1.3

        @staticmethod
        def remaining_s():
            return 100000.0

        @staticmethod
        def messages_fit(n):
            return True
    e4 = {round(p[0], 3): p[1] for p in curve.get("e4", [])}

    def runner(cmd, *, timeout_s, stdout_path, stderr_path):
        d = float(cmd[cmd.index("-d") + 1])
        e = int(cmd[cmd.index("-e") + 1])
        n = interp_bytes(curve["e5"], d)
        if e == 4:      # e4 bytes / e5 bytes at the nearest measured d
            k = min(e4, key=lambda x: abs(x - d)) if e4 else None
            n *= (e4[k] / interp_bytes(curve["e5"], k)) if k else 1.0
        sec = e4_s if e == 4 else e5_s
        if sec > timeout_s:
            clock.t += timeout_s
            return {"rc": -9, "kind": "time", "seconds": timeout_s, "peak_rss_kb": None}
        clock.t += sec
        with open(cmd[2], "wb") as fh:
            fh.write(b"\xff\x0a" + b"x" * max(0, int(n) - 2))
        return {"rc": 0, "kind": "ok", "seconds": sec, "peak_rss_kb": None}
    cfg = dict(X.DEFAULT_CONFIG, format="nrjxl", layout="rgb", encode_max_s=encode_max_s,
               rgb_encode_max_s=encode_max_s, **(cfg_extra or {}))
    crop = {"cfa": "BGGR", "black": 64, "white": 1023, "native_w": 4608, "native_h": 2592,
            "w": 1600, "h": 900, "headroom_x10000": curve.get("headroom_x10000", 10000)}
    with tempfile.TemporaryDirectory() as work:
        open(os.path.join(work, X.RGB_PPM), "wb").write(b"P6\n1 1\n4095\n" + b"\0" * 6)
        try:
            r = X.choose_rate(crop, None, X.colour_params({
                "ExposureTime": 1, "AnalogueGain": 1, "ColourGains": [1, 1],
                "ColourCorrectionMatrix": [1, 0, 0, 0, 1, 0, 0, 0, 1]}), cfg,
                crop_xywh=[1504, 846, 1600, 900], budget=Budget, message_cap=195,
                chunk_b64_chars=384, reserve_msgs=0, fallback_msgs=195, work_dir=work,
                cjxl="/usr/bin/cjxl", runner=runner, clock=clock, log=lambda *_: None)
            res = {"attempts": r["attempts"], "fill": round(len(r["blob"]) / 56160, 3),
                   "d": r["distance"], "seconds": round(clock.t, 1), "rfb": None,
                   "efforts": [e.get("effort", 5) for e in r["attempt_log"]]}
        except X.RawFallback as exc:
            res = {"attempts": len(exc.attempt_log), "fill": None, "d": None,
                   "seconds": round(clock.t, 1), "rfb": exc.code}
    X.PRIOR_RGB, X.SEARCH_FILL_SLACK = saved[0], saved[1]
    if saved[2] is not None:
        X.SEARCH_FILL_SLACK_RGB = saved[2]
    return res


def simulate(args):
    curves = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(args.out, "curves", "*.json")))]
    target = 0.97 * 56160
    # the fit: per frame, the d that lands on the target and the local slope there
    fits = []
    for c in curves:
        lo, hi = 0.5, 15.0
        for _ in range(40):
            mid = math.sqrt(lo * hi)
            if interp_bytes(c["e5"], mid) > target:
                lo = mid
            else:
                hi = mid
        d_t = hi
        k = -math.log(interp_bytes(c["e5"], d_t * 1.1) / interp_bytes(c["e5"], d_t / 1.1)) / math.log(1.21)
        fits.append((c["label"], d_t, k))
    ds = sorted(f[1] for f in fits)
    ks = sorted(f[2] for f in fits)
    med = lambda v: v[len(v) // 2]  # noqa: E731
    report = {"frames": len(curves), "d_at_target": {"min": round(ds[0], 3), "p50": round(med(ds), 3),
                                                      "max": round(ds[-1], 3)},
              "local_K_at_target": {"min": round(ks[0], 3), "p50": round(med(ks), 3), "max": round(ks[-1], 3)},
              "per_frame": [{"label": l, "d_target": round(d, 3), "K": round(k, 3)} for l, d, k in fits]}
    variants = json.loads(args.variants) if args.variants else {}
    import rc_raw_jxl as X
    variants = {"current": {}, **variants}
    out = {}
    for name, v in variants.items():
        prior = tuple(v["prior"]) if "prior" in v else None
        rs = [simulate_one(c, prior=prior, slack=v.get("slack"), encode_max_s=v.get("encode_max_s", 30),
                           e5_s=args.e5_s, e4_s=args.e4_s) for c in curves]
        effs = {}
        for r in rs:
            for e in r.get("efforts", []):
                effs[e] = effs.get(e, 0) + 1
        att = {}
        for r in rs:
            key = r["rfb"] or r["attempts"]
            att[str(key)] = att.get(str(key), 0) + 1
        secs = sorted(r["seconds"] for r in rs)
        fills = sorted(r["fill"] for r in rs if r["fill"])
        out[name] = {"variant": v, "attempts": att, "encodes_by_effort": effs,
                     "search_s": {"p50": med(secs), "max": secs[-1]},
                     "fill": {"min": fills[0] if fills else None, "p50": med(fills) if fills else None},
                     "rfb": sum(1 for r in rs if r["rfb"])}
        print(f"[SIM] {name}: attempts {att} efforts {effs} search s p50 {med(secs)} max {secs[-1]} fill min "
              f"{out[name]['fill']['min']} p50 {out[name]['fill']['p50']}", flush=True)
    report["simulations"] = out
    report["prior_now"] = list(X.PRIOR_RGB)
    with open(os.path.join(args.out, "search_sim.json"), "w") as fh:
        json.dump(report, fh, indent=1)
    print(json.dumps({k: report[k] for k in ("frames", "d_at_target", "local_K_at_target")}, indent=1))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("measure")
    m.add_argument("--rig")
    m.add_argument("--orf")
    m.add_argument("--dng")
    m.add_argument("--params-from")
    m.add_argument("--metadata")
    m.add_argument("--crop", default="1504,846,1600,900")
    m.add_argument("--label", required=True)
    m.add_argument("--out", required=True)
    s = sub.add_parser("simulate")
    s.add_argument("--out", required=True)
    s.add_argument("--variants", help='JSON {name: {"prior": [B, A, D, K], "slack": x, "encode_max_s": n}}')
    s.add_argument("--e5-s", type=float, default=9.5)
    s.add_argument("--e4-s", type=float, default=1.5)
    args = ap.parse_args(argv)
    return measure(args) if args.cmd == "measure" else simulate(args)


if __name__ == "__main__":
    sys.exit(main())

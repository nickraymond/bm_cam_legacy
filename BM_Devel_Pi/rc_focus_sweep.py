#!/usr/bin/env python3
# filename: rc_focus_sweep.py
# description: Focus sweep — one full-native still (JPEG + DNG) per lens position, per-frame focus metrics, best lens position.
"""
Focus sweep (Nick 2026-10-13, 11:00 AM): find the lens position that is sharpest
for the scene in front of the camera (ChArUco board at ~1 m), from the BM
console today (pool `sweep` verb) and from a field remote command later.

REUSABLE: nothing here knows about the pool, the UART or a supervisor. The caller
hands in a capture function; the post-pool production verb (supervisor + backend
allow-list) reuses this module with the production capture.

  capture_fn(out_dir, stem, lens_position) -> dict with at least
      jpg (path), dng (path|None), metadata (libcamera --metadata dict),
      capture_s (float)
  The pool passes RealCamera.capture_still: the PRODUCTION native argv
  (rc_capture.run_raw_capture_once, 4608x2592 q95 + --raw) with manual focus
  (--autofocus-mode manual --lens-position X). Each rpicam-still call sets the
  lens itself, so nothing needs "restoring" after a sweep: the next ordinary
  capture requests the run's own lens position again.

ORDER: all captures first (back to back, ~3.6 s each measured with --raw,
runs/s28_ladder_20261004/RESULTS.md:15), THEN the metrics, so the light and
the board move as little as possible across the sweep.

FOCUS METRICS (per frame, on the JPEG):
  crop      centred 1024x1024 in NATIVE JPEG pixel coordinates
            (x=(W-1024)//2, y=(H-1024)//2 on 4608x2592 = (1792, 784)), luma.
  lapvar    variance of the 4-neighbour Laplacian (higher = sharper)
  tenengrad mean of the squared 3x3 Sobel gradient magnitude (higher = sharper)
  best_lp   the requested lens position with the highest lapvar (tenengrad's
            best is reported next to it; if they disagree, look at the frames).
  Both are RELATIVE within one sweep (same scene, same exposure); never compare
  numbers across sweeps or scenes. JPEG compression adds a little texture; the
  DNG is kept for a raw-domain re-analysis on the Mac.

OUTPUT: <out_dir>/sweep_manifest.json (rewritten atomically after every frame, so
a crash leaves what was done), <UTC>_sweep_lp<X.XX>.jpg/.dng/.metadata.json per
position (same names as the 10/13 pool sweep, so host globs keep working).

Assumptions: numpy + Pillow on the unit (both already required by nrjxl). The
metric decode of a 4608x2592 JPEG on a Pi Zero 2 W is ASSUMED ~1-2 s per frame
(not measured); 11 positions ~ 11 x 3.6 s capture + ~15-20 s metrics ~ 1 min.

Example (Mac, metrics on files you already have):
  python3 rc_focus_sweep.py --metrics a.jpg b.jpg

Known limitations: the metric looks only at the centre crop (where the board
is expected); exposure is not locked across positions (AE runs per capture, as
in production), so read AG/exposure in the manifest when metrics are close.
"""

import json
import math
import os
import time
from datetime import datetime, timezone

DEFAULT_LENS_LIST = (0.0, 0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 1.6, 1.8, 1.82)
LP_MIN, LP_MAX = 0.0, 15.0          # IMX708 lens position (dioptres; 0 = infinity)
MAX_POSITIONS = 12
METRIC_CROP = 1024
SCHEMA = "bmcam_focus_sweep_v1"
META_KEYS = ("LensPosition", "AfState", "ExposureTime", "AnalogueGain", "DigitalGain",
             "Lux", "SensorTimestamp", "ScalerCrop", "FrameDuration", "ColourGains")


class SweepSpecError(ValueError):
    pass


def _num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def lens_list_from(lp=None, lo=None, hi=None, step=None):
    """The lens positions of one sweep, validated. lp = explicit list; or lo/hi/step
    (inclusive, all three); neither = DEFAULT_LENS_LIST. Raises SweepSpecError."""
    if lp is not None and any(v is not None for v in (lo, hi, step)):
        raise SweepSpecError("give lp OR lo/hi/step, not both")
    if lp is not None:
        if not isinstance(lp, (list, tuple)) or not 1 <= len(lp) <= MAX_POSITIONS:
            raise SweepSpecError(f"lp is a list of 1..{MAX_POSITIONS} numbers")
        out = list(lp)
    elif any(v is not None for v in (lo, hi, step)):
        if not all(_num(v) for v in (lo, hi, step)):
            raise SweepSpecError("lo, hi and step are all required numbers")
        if step <= 0 or hi < lo:
            raise SweepSpecError("need step > 0 and hi >= lo")
        n = int(math.floor((hi - lo) / step + 1e-9)) + 1
        if n > MAX_POSITIONS:
            raise SweepSpecError(f"lo..hi/step gives {n} positions > {MAX_POSITIONS}")
        out = [round(lo + i * step, 4) for i in range(n)]
    else:
        out = list(DEFAULT_LENS_LIST)
    for v in out:
        if not _num(v) or not LP_MIN <= v <= LP_MAX:
            raise SweepSpecError(f"lens position {v!r} outside {LP_MIN}..{LP_MAX}")
    return [round(float(v), 4) for v in out]


def with_manual_focus(capture_settings, lens_position):
    """Copy of rc_capture settings with manual focus at lens_position: the
    camera_controls island on, focus = {manual, lens_position}; every other block
    (white balance, exposure, exposure_profile) untouched."""
    import copy
    cs = copy.deepcopy(capture_settings or {})
    controls = dict(cs.get("camera_controls") or {})
    controls["enabled"] = True
    controls["focus"] = {"enabled": True, "mode": "manual",
                         "lens_position": round(float(lens_position), 4)}
    cs["camera_controls"] = controls
    return cs


def focus_metrics(jpeg_path, crop=METRIC_CROP):
    """-> {lapvar, tenengrad, crop_xywh (native px), metric_s}. Luma decode (PIL
    draft "L" at full size: no colour conversion), centred crop, numpy only."""
    import numpy as np
    from PIL import Image
    t0 = time.monotonic()
    with Image.open(jpeg_path) as im:
        im.draft("L", im.size)
        w, h = im.size
        c = min(crop, w, h)
        x, y = (w - c) // 2, (h - c) // 2
        g = np.asarray(im.convert("L").crop((x, y, x + c, y + c)), dtype=np.float32)
    lap = (g[:-2, 1:-1] + g[2:, 1:-1] + g[1:-1, :-2] + g[1:-1, 2:] - 4.0 * g[1:-1, 1:-1])
    gx = ((g[:-2, 2:] + 2 * g[1:-1, 2:] + g[2:, 2:]) - (g[:-2, :-2] + 2 * g[1:-1, :-2] + g[2:, :-2]))
    gy = ((g[2:, :-2] + 2 * g[2:, 1:-1] + g[2:, 2:]) - (g[:-2, :-2] + 2 * g[:-2, 1:-1] + g[:-2, 2:]))
    return {"lapvar": round(float(lap.var()), 3),
            "tenengrad": round(float((gx * gx + gy * gy).mean()), 3),
            "crop_xywh": [x, y, c, c], "metric_s": round(time.monotonic() - t0, 2)}


def _write_manifest(path, data):
    try:
        import atomic_io
        atomic_io.write_text(path, json.dumps(data, indent=1, sort_keys=True, default=str))
    except ImportError:                      # standalone use off the unit
        tmp = path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(data, fh, indent=1, sort_keys=True, default=str)
        os.replace(tmp, path)


def _iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def run_sweep(capture_fn, lens_list, out_dir, *, log=print, metric_fn=focus_metrics,
              stop_fn=None, context=None, clock=time.monotonic):
    """Capture every position, then score every frame. Never raises for a frame:
    a failed capture/metric is recorded and the sweep goes on. -> summary dict
    (also written as <out_dir>/sweep_manifest.json)."""
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "sweep_manifest.json")
    t0 = clock()
    data = {"schema": SCHEMA, "started_utc": _iso(), "out_dir": out_dir,
            "lens_list": list(lens_list), "metric": {"crop": METRIC_CROP,
            "coords": "native JPEG px, centred", "lapvar": "var(4-neighbour Laplacian)",
            "tenengrad": "mean(Sobel gx^2+gy^2)"}, "context": context or {}, "frames": []}
    log(f"[FOCUS] sweep {len(lens_list)} positions {','.join(f'{v:g}' for v in lens_list)} "
        f"-> {out_dir}")
    for i, lp in enumerate(lens_list):
        if stop_fn and stop_fn():
            data["stopped"] = True
            log("[FOCUS] stop requested: sweep cut short")
            break
        rec = {"i": i + 1, "lens_requested": lp, "t_utc": _iso()}
        stem = f"{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}_sweep_lp{lp:.2f}"
        try:
            cap = capture_fn(out_dir, stem, lp)
            meta = cap.get("metadata") or {}
            rec.update(jpg=os.path.basename(cap["jpg"]),
                       dng=os.path.basename(cap["dng"]) if cap.get("dng") else None,
                       jpg_bytes=_size(cap["jpg"]), dng_bytes=_size(cap.get("dng")),
                       capture_s=cap.get("capture_s"),
                       metadata={k: meta.get(k) for k in META_KEYS if k in meta})
            log(f"[FOCUS] {i + 1}/{len(lens_list)} lp={lp:g} -> metadata "
                f"LensPosition={meta.get('LensPosition')} exp={meta.get('ExposureTime')} "
                f"AG={meta.get('AnalogueGain')} ({cap.get('capture_s')} s)")
        except Exception as exc:
            rec["error"] = f"capture {type(exc).__name__}: {exc}"
            log(f"[FOCUS][ERR] lp={lp:g}: {rec['error']}")
        data["frames"].append(rec)
        _write_manifest(path, data)
    data["capture_wall_s"] = round(clock() - t0, 1)
    for rec in data["frames"]:
        if "error" in rec:
            continue
        try:
            rec["focus"] = metric_fn(os.path.join(out_dir, rec["jpg"]))
        except Exception as exc:
            rec["metric_error"] = f"{type(exc).__name__}: {exc}"
            log(f"[FOCUS][ERR] metric lp={rec['lens_requested']:g}: {rec['metric_error']}")
    scored = [r for r in data["frames"] if "focus" in r]
    data["n_ok"] = sum(1 for r in data["frames"] if "error" not in r)
    data["n"] = len(lens_list)
    data["best_lp"] = (max(scored, key=lambda r: r["focus"]["lapvar"])["lens_requested"]
                       if scored else None)
    data["best_lp_tenengrad"] = (max(scored, key=lambda r: r["focus"]["tenengrad"])
                                 ["lens_requested"] if scored else None)
    data["wall_s"] = round(clock() - t0, 1)
    data["ended_utc"] = _iso()
    _write_manifest(path, data)
    table = " ".join(f"{r['lens_requested']:g}:{r['focus']['lapvar']:.0f}" for r in scored)
    log(f"[FOCUS] done {data['n_ok']}/{data['n']} in {data['wall_s']} s; best lp (lapvar) "
        f"{data['best_lp']}, (tenengrad) {data['best_lp_tenengrad']}; lapvar {table}")
    return data


def _size(path):
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Focus metrics on JPEG files (see module doc).")
    ap.add_argument("--metrics", nargs="+", required=True, metavar="JPEG")
    args = ap.parse_args(argv)
    for path in args.metrics:
        print(json.dumps({"file": path, **focus_metrics(path)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

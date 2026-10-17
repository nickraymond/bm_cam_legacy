#!/usr/bin/env python3
# filename: pool_calib_crosscheck.py
# description: Compare two pool_calib_pipeline.py results for the SAME camera (e.g. IMX708 DNG vs JPEG) against the pre-registered cross-check bar.
"""
pool_calib_crosscheck.py — does a second input path (the IMX708 JPEG, i.e. the Pi ISP output) give the
same lens model as the primary one (the DNG)? Mac side, reads two finished pipeline camera folders.

Purpose
  The pool049 calibration runs on the DNGs; the JPEGs are run once as a check (Nick, 2026-10-17:
  "DNG, JPEG as a check"). This scores the difference against the bar pre-registered in
  docs/pool_calib_prereg_20261016.md (spec rev 2, R3). A JPEG result never changes the DNG verdict: a
  disagreement is reported as a finding (an ISP effect), and the DNG calibration JSON still ships.

Inputs
  primary    <pipeline run>/<camera>/   the reference run (DNG): models.json + intrinsics_full_*.json
  check      <pipeline run>/<camera>/   the cross-check run (JPEG), same camera, same frame size
  --out DIR  where to write crosscheck.json + crosscheck.md (default: the primary camera folder)

Bar (R3, set before any run)
  |dfx|/fx <= 0.5 %; |dcx| and |dcy| <= 5 px; ray difference at the primary run's B4 frame probes <= 1 px.
  Chosen model vs chosen model. Ray difference = the distance between the two models' undistorted rays at
  each probe pixel, in px at the primary's f_ref (the literal / metric reading, as d33).

Outputs
  crosscheck.json, crosscheck.md; exit 0 within the bar, 3 outside it (a finding, not a failure), 2 bad input.

Example
  .venv-dev/bin/python tools/pool_calib_crosscheck.py \\
      ~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/<run>_dng/imx708 \\
      ~/NereusData/bm_cam_legacy/2026-10-17_pool049_calib/<run>_jpg/imx708

Known limitations
  Compares the two chosen models; if the runs chose different model families, that is reported, and the
  ray difference then includes the model-family difference too.
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import pool_calib_models as pm  # noqa: E402

BAR = {"rel_fx": 0.005, "c_px": 5.0, "ray_px": 1.0, "doc": "docs/pool_calib_prereg_20261016.md (R3)"}


def load_camera(cam_dir):
    """<run>/<camera>/ -> {"chosen", "K", "D", "size", "probes" (B4 frame probes), "f_ref"}."""
    with open(os.path.join(cam_dir, "models.json")) as f:
        m = json.load(f)
    full = sorted(glob.glob(os.path.join(cam_dir, "intrinsics_full_*.json")))
    if not full:
        raise ValueError(f"{cam_dir}: no intrinsics_full_*.json")
    with open(full[0]) as f:
        k = json.load(f)
    ch = m["chosen"]
    return {"chosen": ch, "K": np.array(m["models"][ch]["K"], float), "D": np.array(m["models"][ch]["D"], float),
            "size": tuple(k["image_size"]), "f_ref": float(m["f_ref_px"]),
            "probes": [p for p in m["probes"] if p["group"] == "frame"], "dir": cam_dir}


def compare(a, b, bar=BAR):
    """Primary a vs check b (load_camera dicts) -> result dict with per-item pass and an overall verdict."""
    if a["size"] != b["size"]:
        raise ValueError(f"frame sizes differ: {a['size']} vs {b['size']}")
    pts = np.array([[p["x"], p["y"]] for p in a["probes"]], float)
    ray = pm.model_disagreement_px([{"model": a["chosen"], "K": a["K"], "D": a["D"]},
                                    {"model": b["chosen"], "K": b["K"], "D": b["D"]}], pts, a["f_ref"])
    rel_fx = (b["K"][0, 0] - a["K"][0, 0]) / a["K"][0, 0]
    dcx, dcy = b["K"][0, 2] - a["K"][0, 2], b["K"][1, 2] - a["K"][1, 2]
    items = {
        "fx": {"value": float(rel_fx), "limit": bar["rel_fx"], "pass": bool(abs(rel_fx) <= bar["rel_fx"])},
        "cx": {"value": float(dcx), "limit": bar["c_px"], "pass": bool(abs(dcx) <= bar["c_px"])},
        "cy": {"value": float(dcy), "limit": bar["c_px"], "pass": bool(abs(dcy) <= bar["c_px"])},
        "ray_at_B4_probes": {"value": float(np.max(ray)) if np.all(np.isfinite(ray)) else None,
                             "per_probe": {p["name"]: (float(r) if np.isfinite(r) else None)
                                           for p, r in zip(a["probes"], ray)},
                             "limit": bar["ray_px"], "pass": bool(np.all(ray <= bar["ray_px"]))},
    }
    return {"bar": bar, "primary": a["dir"], "check": b["dir"], "models": [a["chosen"], b["chosen"]],
            "same_model_family": a["chosen"] == b["chosen"],
            "primary_K": a["K"].tolist(), "check_K": b["K"].tolist(),
            "primary_D": a["D"].tolist(), "check_D": b["D"].tolist(),
            "items": items, "within_bar": all(v["pass"] for v in items.values())}


def to_md(r):
    i = r["items"]
    fmt = (lambda v: "n/a" if v is None else f"{v:.3g}")  # noqa: E731
    L = ["# Cross-check: primary vs check input (same camera)", "",
         f"Primary `{r['primary']}` ({r['models'][0]}) vs check `{r['check']}` ({r['models'][1]}). "
         f"Bar: {r['bar']['doc']}.", "",
         "| item | value | limit | within |", "|---|---|---|---|",
         f"| Δfx / fx | {100 * i['fx']['value']:+.3f} % | ±{100 * r['bar']['rel_fx']:.1f} % | {i['fx']['pass']} |",
         f"| Δcx | {i['cx']['value']:+.2f} px | ±{r['bar']['c_px']:.0f} px | {i['cx']['pass']} |",
         f"| Δcy | {i['cy']['value']:+.2f} px | ±{r['bar']['c_px']:.0f} px | {i['cy']['pass']} |",
         f"| ray difference at the B4 probes (max) | {fmt(i['ray_at_B4_probes']['value'])} px | "
         f"{r['bar']['ray_px']:.0f} px | {i['ray_at_B4_probes']['pass']} |", "",
         "Per probe (px): " + ", ".join(f"{k} {fmt(v)}" for k, v in i["ray_at_B4_probes"]["per_probe"].items()), "",
         f"Distortion primary {np.round(r['primary_D'], 5).tolist()} vs check {np.round(r['check_D'], 5).tolist()}", "",
         f"**{'Within the bar' if r['within_bar'] else 'OUTSIDE the bar: reported as a finding (ISP effect); the primary calibration still ships'}.**"]
    return "\n".join(L) + "\n"


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("primary")
    ap.add_argument("check")
    ap.add_argument("--out")
    a = ap.parse_args(argv)
    try:
        r = compare(load_camera(os.path.expanduser(a.primary)), load_camera(os.path.expanduser(a.check)))
    except (OSError, ValueError, KeyError) as exc:
        print(f"[crosscheck] REFUSED: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    out = os.path.expanduser(a.out or a.primary)
    os.makedirs(out, exist_ok=True)
    with open(os.path.join(out, "crosscheck.json"), "w") as f:
        json.dump(r, f, indent=2)
    md = to_md(r)
    with open(os.path.join(out, "crosscheck.md"), "w") as f:
        f.write(md)
    print(md)
    print(f"[crosscheck] wrote {os.path.join(out, 'crosscheck.json')} and crosscheck.md")
    return 0 if r["within_bar"] else 3


if __name__ == "__main__":
    sys.exit(main())

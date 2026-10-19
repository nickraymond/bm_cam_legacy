#!/usr/bin/env python3
# filename: export_calibration.py
# description: Export one self-describing calibration JSON per camera (+ cv2 undistort maps) from finished pool_calib_pipeline runs.
"""
export_calibration.py: the shipped per-camera calibration file for consumers (E6 density spec, shoot-out).

Purpose
  Turn a finished pipeline camera folder into calibration_<camera>_flatport_<date>.json: model, K, distortion,
  production crop, verdicts and bars, flags, validated board-distance range, use status, limits and provenance.
  Also writes cv2 undistort maps (CV_16SC2, .npz) to a data folder; they rebuild from the JSON.

Inputs
  OUT_JSON_DIR MAPS_DIR NAME=CAM_DIR [NAME=CAM_DIR ...] --prereg-sha SHA --git-sha SHA
  [--dive-date YYYYMMDD] [--calibrated YYYY-MM-DD]
  A camera whose pre-registered bars FAIL is exported with use = "NOT VALIDATED".

Example
  .venv-dev/bin/python tools/pool_calib_post/export_calibration.py runs/pool_calib_20261016 $D/undistort_maps \\
      imx708=$D/20261017_152713_pool049_imx708_dng/imx708 n6=$D/20261017_153029_pool049_openmv_raw/n6 \\
      ae3=$D/20261017_153029_pool049_openmv_raw/ae3 --prereg-sha c3356f49+dda46166 --git-sha <sha>

Known limitations
  The rig / port / dive strings are for the pool049 nereus002 run (--rig, --port, --dive override them).
"""
import argparse
import hashlib
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pool_calib_pipeline as pp  # noqa: E402

LIMITS = [
    "valid near the calibrated board distances, in fresh water (seawater ~+0.5 % fx)",
    "pinhole + distortion approximates a flat port (refraction is non-central)",
    "only the observed area (upper ~60 % of the frame on pool049) is measured; elsewhere the model extrapolates",
    "individual distortion coefficients are NOT identified by this coverage: on pool049 the IMX708 DNG and JPEG runs "
    "give very different rational coefficients with matching K and matching rays where the board was; use the "
    "model as a whole (rays), never single coefficients",
]


def export(name, cam_dir, out_json_dir, maps_dir, prereg_sha, git_sha, rig, port, dive, dive_date, calibrated):
    import cv2
    run_dir = os.path.dirname(cam_dir.rstrip("/"))
    summ = json.load(open(os.path.join(run_dir, "summary.json")))
    r = [c for c in summ["cameras"] if c["camera"] == os.path.basename(cam_dir.rstrip("/"))][0]
    man = json.load(open(os.path.join(run_dir, "run_manifest.json")))
    k = r["intrinsics_full"]
    K, D, size, model = np.array(k["camera_matrix"]), np.array(k["dist_coeffs"]), tuple(k["image_size"]), k["model"]
    m1, m2 = pp.undistort_maps(model, K, D, size)
    f1, f2 = cv2.convertMaps(m1, m2, cv2.CV_16SC2)
    mp = os.path.join(maps_dir, f"undistort_{name}_{size[0]}x{size[1]}.npz")
    np.savez_compressed(mp, map1=f1, map2=f2)
    passed = r["verdict_full_frame"] == "PASS" or r["verdict_production_crop"] == "PASS"
    doc = {
        "status": "PROVISIONAL until QE passes the tool code (bm PR #184)",
        "use": ("validated: see verdicts" if passed else
                "NOT VALIDATED (pre-registered bars FAIL: see bars). Do not apply as a full-frame or crop correction. "
                "fx/fy/cx/cy are well determined inside the observed region (bootstrap std in 'bootstrap'); the "
                "distortion outside the observed region is extrapolated."),
        "camera": name, "rig": rig, "port": port, "medium": "fresh water (pool)", "dive": dive, "calibrated": calibrated,
        "model": model, "opencv_api": k["opencv_api"], "dist_coeffs_order": k["dist_coeffs_order"],
        "image_size": list(size), "camera_matrix": K.tolist(), "dist_coeffs": D.tolist(),
        "fx": k["fx"], "fy": k["fy"], "cx": k["cx"], "cy": k["cy"], "coordinates": k["coordinates"],
        "production_crop": r.get("intrinsics_crop"),
        "heldout_rms_px": r["heldout_rms_px"][model], "views_used": r["views_used"],
        "usable_poses": r["screen"]["distinct_set"],
        "validated_board_distance_m": [r["distance_m"]["min"], r["distance_m"]["max"]],
        "verdict_full_frame": r["verdict_full_frame"], "verdict_production_crop": r["verdict_production_crop"],
        "bars": r["bars"], "flags": r.get("flags"), "bootstrap": r.get("bootstrap_stats"),
        "fov_deg_h_v": k.get("fov_deg_h_v_from_edge_rays"), "fx_over_nominal_in_air": k.get("fx_over_nominal_in_air"),
        "nominal_fx_in_air_px": k.get("nominal_fx_in_air_px"), "nominal_fx_in_air_source": k.get("nominal_fx_in_air_source"),
        "mask": r.get("mask"), "models_reading": man.get("models_reading"),
        "how_to_undistort": ("cv2.fisheye.initUndistortRectifyMap(K, D, I, K, size, CV_32FC1)" if model == "fisheye"
                             else "cv2.initUndistortRectifyMap(K, D, None, K, size, cv2.CV_32FC1)") + " then cv2.remap",
        "limits": LIMITS,
        "source": {"run_dir": run_dir, "camera_dir": cam_dir, "pipeline_git": man.get("git"),
                   "prereg": "docs/pool_calib_prereg_20261016.md", "prereg_commit": prereg_sha,
                   "export_git_sha": git_sha, "export_tool": "tools/pool_calib_post/export_calibration.py",
                   "undistort_maps_npz": mp, "undistort_maps_sha256": hashlib.sha256(open(mp, "rb").read()).hexdigest()},
    }
    out = os.path.join(out_json_dir, f"calibration_{name}_flatport_{dive_date}.json")
    with open(out, "w") as fh:
        json.dump(pp.jsonable(doc), fh, indent=2)
    print(f"{name}: {model} fx {k['fx']:.1f} -> {out} ({os.path.getsize(out)} B); maps {mp} "
          f"({os.path.getsize(mp) >> 20} MiB)")
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1], epilog=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("out_json_dir")
    ap.add_argument("maps_dir")
    ap.add_argument("cams", nargs="+", help="NAME=CAM_DIR")
    ap.add_argument("--prereg-sha", required=True)
    ap.add_argument("--git-sha", required=True)
    ap.add_argument("--dive-date", default="20261016", help="YYYYMMDD, for the file name")
    ap.add_argument("--calibrated", default="2026-10-17", help="date the calibration was computed")
    ap.add_argument("--rig", default="nereus002 pool rig")
    ap.add_argument("--port", default="flat port (pool housing)")
    ap.add_argument("--dive", default="pool049_20261016T212527Z (Fri 2026-10-16 ChArUco re-dive)")
    a = ap.parse_args(argv)
    os.makedirs(a.out_json_dir, exist_ok=True)
    os.makedirs(os.path.expanduser(a.maps_dir), exist_ok=True)
    for item in a.cams:
        name, _, cam_dir = item.partition("=")
        export(name, os.path.expanduser(cam_dir), a.out_json_dir, os.path.expanduser(a.maps_dir), a.prereg_sha,
               a.git_sha, a.rig, a.port, a.dive, a.dive_date, a.calibrated)
    return 0


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
# filename: sprint17_denoise_sweep.py
# description: Sprint17 — sweep --denoise at a fixed preset, with 1:1 crops for judging.
"""
Sprint17 denoise sweep (SPEC D-S17-4/5): the encoder knob most likely to
matter underwater, and still completely untested.

ONE VARIABLE. Everything except `--denoise` is held identical: same preset,
same crop, same sensor mode, same output size, same fps, same bitrate cap,
same camera controls, back-to-back on one unit.

WHY THE BITRATE CAP IS HELD HIGH: at a cap the scene cannot fill, no clip is
bitrate-limited, so differences come from the ISP rather than from the rate
controller robbing one setting to pay another. The achieved bitrate then
becomes a READING rather than a setting — a denoiser that removes real
detail spends fewer bits, and that shows up in the CSV.

WHY 1:1 CROPS: denoise changes exactly the thing a scaled thumbnail destroys.
The sheet therefore shows NATIVE-PIXEL centre crops at 100 %, never resized
(manifesto rule 13). The whole-frame poster is kept alongside for context.

INPUTS
  --repo DIR       checkout to import the runtime from
  --preset NAME    preset to hold fixed (default: whatever the unit's YAML says)
  --seconds N      per clip (default 30)
  --modes LIST     comma list (default auto,off,cdn_off,cdn_fast,cdn_hq)
  --bitrate N      cap in Mbps (default: the preset's recommendation)
  --crop-px N      side of the 1:1 centre crop (default 640)

OUTPUTS  (run folder, self-contained)
  results.csv                per mode: size, achieved Mbps, sharpness proxy,
                             encode_s, CMA, temp
  <mode>.mp4                 the clip (the real evidence — judge motion here)
  <mode>_frame<N>.jpg        full frames at fixed timestamps
  <mode>_crop<N>.jpg         1:1 NATIVE-PIXEL centre crops, never resized
  run_manifest.json          argv per mode, encoder build, host, geometry

EXAMPLE (on the Pi, camera free)
  python3 tools/sprint17_denoise_sweep.py --repo ~/repos/bm_cam_legacy_sprint17

ASSUMPTIONS
  - Camera FREE; refuses to fight the production recorder.
  - Sequential clips: the scene must hold still for ~3 min. A scene that
    changes mid-sweep invalidates the comparison, so the CSV carries each
    clip's mean luma and the tool WARNS if it drifts.

KNOWN LIMITATIONS
  - The sharpness proxy (variance of a Laplacian-like kernel) rewards noise as
    well as detail — that is precisely why it cannot replace looking at the
    crops. It is a tie-breaker, not a verdict (manifesto rule 14).
"""

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

FRAME_TIMES = (5, 15, 25)      # seconds into the clip; avoids the AGC ramp


def sh(argv, timeout=600):
    started = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.monotonic() - started
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT", time.monotonic() - started


def meminfo(key):
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(key):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def frame_metrics(path):
    """(sharpness_proxy, mean_luma) for one JPEG, stdlib+PIL only.

    Sharpness proxy = variance of a 3x3 Laplacian over the luma plane,
    downscaled first so the number is comparable across resolutions. Higher =
    more high-frequency content, which is detail AND noise together — read it
    next to the crops, never instead of them.
    """
    try:
        from PIL import Image, ImageFilter
    except ImportError:
        return None, None
    try:
        with Image.open(path) as im:
            g = im.convert("L")
            mean = sum(g.histogram()[i] * i for i in range(256)) / max(
                1, g.width * g.height)
            lap = g.filter(ImageFilter.Kernel(
                (3, 3), [0, 1, 0, 1, -4, 1, 0, 1, 0], scale=1, offset=128))
            hist = lap.histogram()
            n = sum(hist)
            if not n:
                return None, round(mean, 1)
            m = sum(h * i for i, h in enumerate(hist)) / n
            var = sum(h * (i - m) ** 2 for i, h in enumerate(hist)) / n
            return round(var, 1), round(mean, 1)
    except OSError:
        return None, None


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--repo", default=here)
    ap.add_argument("--config", default="/home/pi/BM_Devel_Pi/camera_schedule.yaml")
    ap.add_argument("--preset", default="")
    ap.add_argument("--seconds", type=float, default=30.0)
    ap.add_argument("--modes", default="auto,off,cdn_off,cdn_fast,cdn_hq")
    ap.add_argument("--bitrate", type=float, default=None)
    ap.add_argument("--crop-px", type=int, default=640)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    for name in ("rpicam-vid", "libcamera-vid"):
        if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
            print(f"[DN][ERROR] {name} is running — stop the recording cycle "
                  "first. This tool will not fight the recorder for the camera.")
            return 3

    sys.path.insert(0, os.path.join(args.repo, "BM_Devel_Pi"))
    import video_geometry as vg
    import video_recorder as vr

    encoder = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
    ffmpeg = shutil.which("ffmpeg")
    if not encoder or not ffmpeg:
        print(f"[DN][ERROR] need an encoder and ffmpeg ({encoder!r}, {ffmpeg!r})")
        return 2

    # Start from the unit's OWN config so the sweep holds the unit's real
    # geometry fixed, not a guess at it.
    vcfg = vr.load_video_config(args.config)
    if args.preset:
        vcfg["preset"] = args.preset
        vcfg["crop_native_xywh"] = vcfg["output"] = vcfg["sensor_mode"] = None
    vcfg["clip_minutes"] = args.seconds / 60.0
    if args.bitrate:
        vcfg["bitrate_mbps"] = args.bitrate
    vcfg = vr.validate_video_config(vcfg)
    geo = vcfg["geometry"]

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or os.path.expanduser(f"~/sprint17_denoise_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    modes = [m.strip() for m in args.modes.split(",") if m.strip()]
    bad = [m for m in modes if m not in vr.ENCODER_DENOISE]
    if bad:
        print(f"[DN][ERROR] unknown denoise mode(s) {bad}; known: "
              f"{sorted(x for x in vr.ENCODER_DENOISE if x)}")
        return 2

    print(f"[DN] Sprint17 denoise sweep — {len(modes)} modes x {args.seconds:.0f}s")
    print(f"[DN] HELD FIXED: preset={geo['preset']} {geo['output_wh'][0]}x"
          f"{geo['output_wh'][1]} @{geo['fps']}fps mode={geo['sensor_mode']} "
          f"cap={vcfg['bitrate_mbps']}Mbps crop={tuple(geo['crop_native_xywh'])}")
    print(f"[DN] out={out_dir}")

    fields = ["denoise", "bytes", "achieved_mbps", "pct_of_cap", "encode_s",
              "sharpness_proxy", "mean_luma", "cma_free_kb", "temp_c", "rc"]
    rows, argv_by_mode = [], {}

    for mode in modes:
        enc = dict(vcfg["encoder"])
        enc["denoise"] = "" if mode == "auto" else mode
        sweep_cfg = dict(vcfg)
        sweep_cfg["encoder"] = enc

        base = os.path.join(out_dir, mode)
        part, mp4 = base + ".h264", base + ".mp4"
        argv, _ = vr.build_encoder_command(
            {"capture_backend": "rpicam"}, sweep_cfg, part, binary=encoder)
        argv = list(argv)
        if mode == "auto":
            pass                      # baseline: the flag is genuinely absent
        argv_by_mode[mode] = argv
        print(f"[DN] === denoise={mode}")

        rc, out, encode_s = sh(argv, timeout=args.seconds + 180)
        row = {f: "" for f in fields}
        row.update({"denoise": mode, "rc": rc, "encode_s": round(encode_s, 1),
                    "cma_free_kb": meminfo("CmaFree"), "temp_c": cpu_temp_c()})
        with open(base + ".log", "w") as f:
            f.write("COMMAND: " + " ".join(argv) + "\n" + (out or ""))

        if os.path.exists(part) and os.path.getsize(part) > 0:
            sh(vr.build_mux_command(ffmpeg, geo["fps"], part, mp4))
            os.remove(part)
        if os.path.exists(mp4):
            row["bytes"] = os.path.getsize(mp4)
            achieved = row["bytes"] * 8 / args.seconds / 1e6
            row["achieved_mbps"] = round(achieved, 2)
            row["pct_of_cap"] = round(100 * achieved / float(vcfg["bitrate_mbps"]))

            sharp, luma = [], []
            for i, t in enumerate(FRAME_TIMES):
                frame = f"{base}_frame{i}.jpg"
                crop = f"{base}_crop{i}.jpg"
                sh([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                    "-ss", str(t), "-i", mp4, "-frames:v", "1", "-q:v", "2",
                    frame])
                if os.path.exists(frame):
                    # 1:1 CENTRE CROP, never resized — this is the tile that
                    # actually shows what denoise did.
                    n = args.crop_px
                    sh([ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
                        "-i", frame, "-vf",
                        f"crop={n}:{n}:(in_w-{n})/2:(in_h-{n})/2",
                        "-q:v", "2", crop])
                    s, l = frame_metrics(crop)
                    if s is not None:
                        sharp.append(s)
                    if l is not None:
                        luma.append(l)
            if sharp:
                row["sharpness_proxy"] = round(sum(sharp) / len(sharp), 1)
            if luma:
                row["mean_luma"] = round(sum(luma) / len(luma), 1)

        rows.append(row)
        print(f"[DN]     -> {row['bytes']} B  {row['achieved_mbps']} Mbps "
              f"({row['pct_of_cap']}% of cap)  sharp={row['sharpness_proxy']} "
              f"luma={row['mean_luma']}  {row['temp_c']}C rc={rc}")

    with open(os.path.join(out_dir, "results.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    lumas = [r["mean_luma"] for r in rows if r["mean_luma"] != ""]
    drift = (max(lumas) - min(lumas)) if len(lumas) > 1 else 0
    if drift > 12:
        print(f"[DN][WARN] mean luma drifted {drift:.0f} levels across the "
              "sweep — the SCENE changed mid-run, so these clips are not a "
              "clean one-variable comparison. Re-run with stable light.")

    with open(os.path.join(out_dir, "run_manifest.json"), "w") as f:
        json.dump({
            "tool": "sprint17_denoise_sweep.py", "sprint": "Sprint17",
            "utc": datetime.now(timezone.utc).isoformat(),
            "host": os.uname().nodename, "repo": args.repo,
            "held_fixed": {
                "preset": geo["preset"], "crop_native_xywh": list(geo["crop_native_xywh"]),
                "sensor_mode": geo["sensor_mode"], "output": list(geo["output_wh"]),
                "fps": geo["fps"], "bitrate_mbps": vcfg["bitrate_mbps"],
                "seconds": args.seconds},
            "swept": {"denoise": modes},
            "frame_times_s": list(FRAME_TIMES),
            "crop_px_1to1": args.crop_px,
            "luma_drift_levels": drift,
            "argv": {k: " ".join(v) for k, v in argv_by_mode.items()},
        }, f, indent=2)

    print(f"[DN] done. results: {os.path.join(out_dir, 'results.csv')}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

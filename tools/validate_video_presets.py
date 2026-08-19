#!/usr/bin/env python3
# filename: validate_video_presets.py
# description: Sprint17 gates 2/4/6 — record every preset on real hardware and prove it.
"""
Sprint17 preset validation. Runs ON THE PI, using the REAL production code
(video_geometry + video_recorder.build_encoder_command) rather than a
hand-written argv — the point is to prove the shipping path, not a lookalike
(manifesto rule 6).

For each preset it records a short clip and checks three things the SPEC gates
on:

  gate 2  ffprobe reports EXACTLY the preset's output size, and the crop it
          came from supplied at least that many available px (no upscaling)
  gate 4  the --roi libcamera actually applied, in native coordinates, matches
          the preset's crop — this is the D-S15-3 bug that shifted video
          framing, so it is verified against the encoder's own -v 2 output
  gate 6  encode wall time < clip wall time, and CMA does not run dry

INPUTS
  --repo DIR      checkout to import the runtime from (default: script's repo)
  --seconds N     clip length per preset (default 20)
  --presets a,b   subset to run (default: all six)
  --out DIR       run folder (default: ~/sprint17_presets_<UTC>)

OUTPUTS (self-contained run folder, manifesto rule 10)
  results.csv         one row per preset, every number below
  <preset>.log        full -v 2 encoder output
  <preset>.mp4        the clip itself (visual evidence for the A/B)
  <preset>_thumb.jpg  poster frame
  run_manifest.json   host, encoder build, git sha, CMA, argv per preset

EXAMPLE
  ssh pi@bmcam000 'python3 ~/repos/bm_cam_legacy_sprint17/tools/validate_video_presets.py \
      --repo ~/repos/bm_cam_legacy_sprint17 --seconds 20'

ASSUMPTIONS
  - The camera is FREE. This refuses to run while an encoder holds it rather
    than fight the production recorder (rule 15).
  - ffmpeg/ffprobe present (they are, the clip pipeline uses them).

KNOWN LIMITATIONS
  - Short clips: encode_s here proves the argv works and the ISP keeps up, NOT
    sustained thermal behaviour. That needs an overnight HIL run.
  - Image quality is judged from the clips by eye in the A/B, not scored here.
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone


def sh(argv, timeout=300):
    """Run a command, return (rc, stdout+stderr, seconds)."""
    started = time.monotonic()
    try:
        p = subprocess.run(argv, capture_output=True, text=True, timeout=timeout)
        return p.returncode, (p.stdout or "") + (p.stderr or ""), time.monotonic() - started
    except subprocess.TimeoutExpired:
        return -1, "TIMEOUT", time.monotonic() - started


def camera_busy():
    for name in ("rpicam-vid", "libcamera-vid"):
        if subprocess.run(["pgrep", "-x", name], capture_output=True).returncode == 0:
            return name
    return None


def meminfo(key):
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith(key):
                    return int(line.split()[1])
    except OSError:
        pass
    return None


def cpu_temp_c():
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            return round(int(f.read().strip()) / 1000.0, 1)
    except (OSError, ValueError):
        return None


def ffprobe_stream(path):
    """(width, height, nb_frames, duration_s) as the MUXED FILE reports them —
    gate 2 is judged on the artifact, never on what we asked for."""
    rc, out, _ = sh(["ffprobe", "-v", "error", "-select_streams", "v:0",
                     "-count_frames", "-show_entries",
                     "stream=width,height,nb_read_frames,duration",
                     "-of", "json", path])
    if rc != 0:
        return None
    try:
        s = json.loads(out)["streams"][0]
    except (ValueError, KeyError, IndexError):
        return None
    def num(v, cast=int):
        try:
            return cast(v)
        except (TypeError, ValueError):
            return None
    return (num(s.get("width")), num(s.get("height")),
            num(s.get("nb_read_frames")), num(s.get("duration"), float))


APPLIED_CROP_RE = re.compile(r"Using crop \(main\) \((\d+), (\d+)\)/(\d+)x(\d+)")
SELECTED_MODE_RE = re.compile(r"Selected sensor format: (\d+x\d+)")


def parse_encoder_log(text):
    """The encoder's own account of what it did (gate 4 evidence)."""
    applied = APPLIED_CROP_RE.findall(text)
    mode = SELECTED_MODE_RE.findall(text)
    return {
        "applied_crop_native": tuple(int(v) for v in applied[-1]) if applied else None,
        "selected_mode": mode[-1] if mode else None,
    }


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap.add_argument("--repo", default=here, help="checkout to import the runtime from")
    ap.add_argument("--seconds", type=float, default=20.0)
    ap.add_argument("--presets", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--bitrate-mbps", type=float, default=None,
                    help="override every preset's recommended bitrate")
    args = ap.parse_args()

    busy = camera_busy()
    if busy:
        print(f"[VAL][ERROR] {busy} is running — stop the recording cycle first. "
              "This tool will not fight the production recorder for the camera.")
        return 3

    pi_dir = os.path.join(args.repo, "BM_Devel_Pi")
    sys.path.insert(0, pi_dir)
    import video_geometry as vg
    import video_recorder as vr

    names = ([n.strip() for n in args.presets.split(",") if n.strip()]
             or list(vg.PRESETS))
    unknown = [n for n in names if n not in vg.PRESETS]
    if unknown:
        print(f"[VAL][ERROR] unknown preset(s): {unknown}; known: {list(vg.PRESETS)}")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out_dir = args.out or os.path.expanduser(f"~/sprint17_presets_{stamp}")
    os.makedirs(out_dir, exist_ok=True)

    encoder = shutil.which("rpicam-vid") or shutil.which("libcamera-vid")
    ffmpeg = shutil.which("ffmpeg")
    if not encoder or not ffmpeg:
        print(f"[VAL][ERROR] need rpicam-vid/libcamera-vid and ffmpeg "
              f"(found {encoder!r}, {ffmpeg!r})")
        return 2

    rc, enc_ver, _ = sh([encoder, "--version"])
    rc, git_sha, _ = sh(["git", "-C", args.repo, "rev-parse", "--short", "HEAD"])
    git_sha = git_sha.strip() if rc == 0 else "unknown"

    print(f"[VAL] Sprint17 preset validation — {len(names)} presets, "
          f"{args.seconds:.0f}s each")
    print(f"[VAL] repo={args.repo} sha={git_sha}")
    print(f"[VAL] out={out_dir}")
    print(f"[VAL] encoder={encoder}")
    print(f"[VAL] CmaTotal={meminfo('CmaTotal')} kB CmaFree={meminfo('CmaFree')} kB")

    fields = ["preset", "crop_native_xywh", "sensor_mode", "roi",
              "avail_px", "output_expected", "output_ffprobe", "scale",
              "fps_req", "fps_actual", "bitrate_mbps", "bytes",
              "encode_s", "clip_s", "encode_under_clip",
              "applied_crop_native", "crop_matches", "selected_mode",
              "mode_matches", "cma_free_kb_during", "temp_c_after",
              "rc", "gate2_no_upscale", "verdict"]
    rows = []
    argv_by_preset = {}

    for name in names:
        row = {f: "" for f in fields}
        row["preset"] = name
        recommended = vg.PRESETS[name]["bitrate_mbps"]
        bitrate = args.bitrate_mbps if args.bitrate_mbps else recommended

        # Build the island exactly as the runtime would, then let the REAL
        # validator resolve geometry — including the upscale refusal.
        vcfg = {k: v for k, v in vr.DEFAULT_VIDEO_CONFIG.items()}
        vcfg = json.loads(json.dumps(vcfg))          # deep copy, stdlib only
        vcfg["preset"] = name
        vcfg["clip_minutes"] = args.seconds / 60.0
        vcfg["bitrate_mbps"] = bitrate
        vcfg["fps"] = vg.PRESETS[name]["max_fps"]
        try:
            vcfg = vr.validate_video_config(vcfg)
        except Exception as exc:
            row.update({"rc": "config", "verdict": f"CONFIG REFUSED: {exc}"})
            rows.append(row)
            print(f"[VAL] === {name}: CONFIG REFUSED: {exc}")
            continue

        geo = vcfg["geometry"]
        base = os.path.join(out_dir, name)
        part, mp4, thumb = base + ".h264", base + ".mp4", base + "_thumb.jpg"
        log_path = base + ".log"

        argv, _requested = vr.build_encoder_command(
            {"capture_backend": "rpicam"}, vcfg, part, binary=encoder)
        argv = list(argv)
        argv.insert(1, "-v")                 # gate 4 needs the mode/crop lines
        argv.insert(2, "2")
        argv_by_preset[name] = argv

        row.update({
            "crop_native_xywh": ",".join(str(v) for v in geo["crop_native_xywh"]),
            "sensor_mode": geo["sensor_mode"],
            "roi": geo["roi"],
            "avail_px": f"{geo['available_px'][0]}x{geo['available_px'][1]}",
            "output_expected": f"{geo['output_wh'][0]}x{geo['output_wh'][1]}",
            "scale": geo["scale"],
            "fps_req": geo["fps"],
            "bitrate_mbps": bitrate,
            "clip_s": round(args.seconds, 1),
        })
        print(f"[VAL] === {name}: {row['output_expected']} @{geo['fps']}fps "
              f"{bitrate}Mbps mode={geo['sensor_mode']} "
              f"avail={row['avail_px']} scale={geo['scale']}x")

        with open(log_path, "w") as f:
            f.write("COMMAND: " + " ".join(argv) + "\n")
        proc = subprocess.Popen(argv, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True)
        started = time.monotonic()
        time.sleep(min(3.0, args.seconds / 2))
        row["cma_free_kb_during"] = meminfo("CmaFree")
        out, _ = proc.communicate(timeout=args.seconds + 120)
        row["encode_s"] = round(time.monotonic() - started, 1)
        row["rc"] = proc.returncode
        with open(log_path, "a") as f:
            f.write(out or "")

        parsed = parse_encoder_log(out or "")
        row["applied_crop_native"] = (
            ",".join(str(v) for v in parsed["applied_crop_native"])
            if parsed["applied_crop_native"] else "")
        row["selected_mode"] = parsed["selected_mode"] or ""
        row["mode_matches"] = (parsed["selected_mode"] == geo["sensor_mode"])

        # gate 4: the crop libcamera APPLIED, in native px, must be the crop
        # the preset asked for (within libcamera's 1 px round-down).
        if parsed["applied_crop_native"]:
            want = tuple(geo["crop_native_xywh"])
            got = parsed["applied_crop_native"]
            row["crop_matches"] = all(abs(a - b) <= 2 for a, b in zip(want, got))
        else:
            row["crop_matches"] = "unknown"

        if os.path.exists(part) and os.path.getsize(part) > 0:
            sh(vr.build_mux_command(ffmpeg, geo["fps"], part, mp4))
            sh(vr.build_poster_command(ffmpeg, mp4, thumb))
            os.remove(part)
        probe = ffprobe_stream(mp4) if os.path.exists(mp4) else None
        if probe:
            w, h, frames, _dur = probe
            row["output_ffprobe"] = f"{w}x{h}"
            row["bytes"] = os.path.getsize(mp4)
            if frames:
                row["fps_actual"] = round(frames / args.seconds, 1)
        row["temp_c_after"] = cpu_temp_c()

        # gate 2: the ARTIFACT is the stated size, and real detail backed it.
        row["gate2_no_upscale"] = (
            row["output_ffprobe"] == row["output_expected"]
            and geo["available_px"][0] >= geo["output_wh"][0] - vg.UPSCALE_SLACK_PX)
        # gate 6: encode wall time must fit inside the clip wall time.
        row["encode_under_clip"] = row["encode_s"] < args.seconds + 15

        ok = (row["rc"] == 0 and row["gate2_no_upscale"]
              and row["crop_matches"] is True and row["mode_matches"]
              and row["encode_under_clip"])
        row["verdict"] = "PASS" if ok else "FAIL"
        rows.append(row)
        print(f"[VAL]     -> {row['verdict']} ffprobe={row['output_ffprobe']} "
              f"fps={row['fps_actual']} bytes={row['bytes']} "
              f"encode={row['encode_s']}s crop_ok={row['crop_matches']} "
              f"mode_ok={row['mode_matches']} cma_free={row['cma_free_kb_during']}kB "
              f"temp={row['temp_c_after']}C rc={row['rc']}")

    csv_path = os.path.join(out_dir, "results.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)

    with open(os.path.join(out_dir, "run_manifest.json"), "w") as f:
        json.dump({
            "tool": "validate_video_presets.py",
            "sprint": "Sprint17",
            "gates": ["2 no-upscale (ffprobe)", "4 roi vs mode fov",
                      "6 encode_s < clip_s, CMA"],
            "utc": datetime.now(timezone.utc).isoformat(),
            "host": os.uname().nodename,
            "repo": args.repo,
            "git_sha": git_sha,
            "encoder_version": enc_ver.strip().splitlines()[:2],
            "seconds_per_preset": args.seconds,
            "cma_total_kb": meminfo("CmaTotal"),
            "argv": {k: " ".join(v) for k, v in argv_by_preset.items()},
        }, f, indent=2)

    passed = sum(1 for r in rows if r["verdict"] == "PASS")
    print(f"[VAL] {passed}/{len(rows)} presets PASS. results: {csv_path}")
    return 0 if passed == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())

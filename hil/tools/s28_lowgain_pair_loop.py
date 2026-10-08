#!/usr/bin/env python3
# filename: s28_lowgain_pair_loop.py
# description: Sprint28 sunrise low-gain test (runs ON the unit): every --interval-s a back-to-back pair of PRODUCTION still captures (rc_capture.run_raw_capture_once: --raw, the unit's own camera controls), profile auto then low_gain; keeps the still.crop RAW (16-bit PGM), a still.crop crop of the ISP JPEG and the libcamera metadata; no transmit.
"""
Started by hil/tools/hil_s28_lowgain_sunrise.sh (desk), which copies this file to the unit and
runs it from the deployed app dir (/home/pi/BM_Devel_Pi, needs #133's rc_exposure_profile.py).

Per pair (start-to-start every --interval-s, default 180 s):
  1. auto     = the production capture argv with NO exposure profile (today);
  2. low_gain = the same argv + the exposure profile {low_gain, max_shutter_us, max_gain}
     (rc_exposure_profile: --tuning-file = the patched copy of the unit's tuning file).
  Camera controls (focus / WB / ...) and still.crop come from the unit's OWN config: v2
  camera_config.yaml rendered IN MEMORY to <out>/rendered_camera_schedule.yaml (no tmpfs
  render, no LKG write), else the v1 camera_schedule.yaml.
  Kept per capture (<out>/pairs/<n>_<profile>/): raw_crop.pgm (still.crop of the DNG, 16-bit,
  maxval = WhiteLevel, native px), isp_crop.jpg (the same crop of rpicam's JPEG, q95),
  metadata.json (libcamera --metadata), capture_info.json (rc_capture's dict incl. the
  exposure_* fields), stderr.log (rpicam, incl. libcamera's "tuning file" line). The 24 MB DNG
  and the full JPEG are deleted unless --keep-dng / --keep-jpeg.
  Row in <out>/pairs.csv per capture: utc, pair, profile, ok, why, ExposureTime, AnalogueGain,
  DigitalGain, Lux, ColourGains, ColourTemperature, the tuning file libcamera logged, capture s.
Safety:
  - REFUSES to start if a camera / runtime process is running (the operator stops the runtime and
    disarms cron FIRST, as for R0); re-checks before every pair and SKIPS the pair if busy.
  - Never edits cron: the crontab is hashed at start and at the end; both go in the manifest
    (a change during the run is flagged, not undone).
  - SIGINT (Ctrl-C) / SIGTERM / SIGHUP: finishes or abandons the current capture (rpicam dies with
    the terminal's SIGINT; run_raw_capture_once removes partial files), writes the manifest,
    exits 0. No process is left behind (checked and recorded).
Outputs: <out>/run_manifest.json, pairs.csv, pairs/, loop.log.
Example (on the unit): cd /home/pi/BM_Devel_Pi && python3 /home/pi/s28lg/s28_lowgain_pair_loop.py \\
           --out /home/pi/s28lg/run_<ts> --interval-s 180 --max-shutter-us 30000 --max-gain 16
Limits: two captures are ~2-3 s apart, so the light may differ slightly within a pair; the
Lux value is libcamera's estimate; no bus / power control here (the bus is held on by the TE).
"""

import argparse
import csv
import hashlib
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone

STOP = {"flag": False, "why": None}
BUSY_RE = "[r]c_progressive_jp[e]g|[r]c_run_capture_cycle|[r]c_supervisor|[r]picam-|[l]ibcamera-|[c]jxl"
CSV_KEYS = ["utc", "pair", "profile", "ok", "why", "exposure_us", "analogue_gain", "digital_gain",
            "lux", "colour_gain_r", "colour_gain_b", "colour_temperature", "tuning_file_logged",
            "exposure_profile_applied", "exposure_tuning_file_used", "capture_s", "dir"]


def utc():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(out, msg):
    line = f"{utc()} [lowgain] {msg}"
    print(line, flush=True)
    with open(os.path.join(out, "loop.log"), "a") as fh:
        fh.write(line + "\n")


def busy():
    r = subprocess.run(["pgrep", "-af", BUSY_RE], capture_output=True, text=True)
    me = str(os.getpid())
    return [l for l in r.stdout.splitlines() if l and not l.startswith(me + " ")]


def crontab_sha():
    r = subprocess.run(["crontab", "-l"], capture_output=True, text=True)
    text = r.stdout if r.returncode == 0 else ""
    active = [l for l in text.splitlines() if "rc_run_capture_cycle" in l and not l.lstrip().startswith("#")]
    return hashlib.sha256(text.encode()).hexdigest()[:16], active


def on_signal(signum, _frame):
    STOP["flag"], STOP["why"] = True, signal.Signals(signum).name


def unit_config(app_dir, out):
    """-> (v1-shaped config path for the readers, source). v2 rendered in memory, no tmpfs."""
    sys.path.insert(0, app_dir)
    import config_v2
    v1 = os.path.join(app_dir, "camera_schedule.yaml")
    v2 = os.path.join(app_dir, config_v2.V2_NAME)
    if os.path.exists(v2):
        cfg = config_v2.load_config(v2)
        path = os.path.join(out, "rendered_camera_schedule.yaml")
        with open(path, "w") as fh:
            fh.write(config_v2.render_v1_text(cfg.base))
        return path, f"v2 {v2} (hash {config_v2.config_hash(cfg.base)}), rendered in memory"
    return v1, f"v1 {v1}"


def metadata_fields(meta_path, stderr_path):
    md = {}
    try:
        with open(meta_path) as fh:
            md = json.load(fh)
    except (OSError, ValueError, TypeError):
        pass
    tuning = ""
    try:
        with open(stderr_path, errors="replace") as fh:
            for line in fh:
                if "tuning file" in line.lower():
                    tuning = line.strip().split("tuning file", 1)[-1].strip(" :")
    except OSError:
        pass
    gains = md.get("ColourGains") or [None, None]
    return {"exposure_us": md.get("ExposureTime"), "analogue_gain": md.get("AnalogueGain"),
            "digital_gain": md.get("DigitalGain"), "lux": md.get("Lux"),
            "colour_gain_r": gains[0], "colour_gain_b": gains[1],
            "colour_temperature": md.get("ColourTemperature"), "tuning_file_logged": tuning}


def capture(args, rc, X, P, settings, base_settings, crop_xywh, pair_dir, profile):
    os.makedirs(pair_dir, exist_ok=True)
    stem = os.path.join(pair_dir, "native")
    native = stem + ".jpg"
    cmd, _backend = P._select_camera_command(base_settings["capture_backend"])
    t0 = time.monotonic()
    info, dng, why = rc.run_raw_capture_once(
        cmd, native, base_settings["source_width"], base_settings["source_height"],
        base_settings["source_jpeg_quality"], stem, settings=settings)
    row = {"profile": profile, "ok": info is not None and dng is not None, "why": why or "",
           "capture_s": round(time.monotonic() - t0, 2), "dir": os.path.basename(pair_dir)}
    if info is not None:
        with open(os.path.join(pair_dir, "capture_info.json"), "w") as fh:
            json.dump(info, fh, indent=1, default=str)
        row.update(exposure_profile_applied=info.get("exposure_profile_applied", ""),
                   exposure_tuning_file_used=info.get("exposure_tuning_file_used", ""))
        meta = info.get("metadata_json")
        if meta and os.path.exists(meta):
            shutil.copyfile(meta, os.path.join(pair_dir, "metadata.json"))
        shutil.copyfile(info["stderr_log"], os.path.join(pair_dir, "stderr.log"))
        row.update(metadata_fields(os.path.join(pair_dir, "metadata.json"),
                                   os.path.join(pair_dir, "stderr.log")))
    if dng:
        crop = X.read_dng_crop(dng, crop_xywh)
        X.write_pgm(os.path.join(pair_dir, "raw_crop.pgm"), crop["mosaic"], maxval=crop["white"])
        with open(os.path.join(pair_dir, "raw_crop.json"), "w") as fh:
            json.dump({"crop_xywh": crop_xywh, "cfa": crop["cfa"], "black": crop["black"],
                       "white": crop["white"], "native_w": crop["native_w"],
                       "native_h": crop["native_h"]}, fh)
        if not args.keep_dng:
            os.remove(dng)
    if os.path.exists(native):
        from PIL import Image
        x, y, w, h = crop_xywh
        Image.open(native).crop((x, y, x + w, y + h)).save(
            os.path.join(pair_dir, "isp_crop.jpg"), quality=95)
        if not args.keep_jpeg:
            os.remove(native)
    return row


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--out", required=True)
    ap.add_argument("--app-dir", default="/home/pi/BM_Devel_Pi")
    ap.add_argument("--interval-s", type=int, default=180)
    ap.add_argument("--max-shutter-us", type=int, default=30000)
    ap.add_argument("--max-gain", type=float, default=16.0)
    ap.add_argument("--max-pairs", type=int, default=0, help="0 = until stopped")
    ap.add_argument("--min-free-mb", type=int, default=1500)
    ap.add_argument("--keep-dng", action="store_true")
    ap.add_argument("--keep-jpeg", action="store_true")
    args = ap.parse_args(argv)
    os.makedirs(os.path.join(args.out, "pairs"), exist_ok=True)
    for s in (signal.SIGINT, signal.SIGTERM, signal.SIGHUP):
        signal.signal(s, on_signal)
    # the loop writes its OWN pid (bmcam004 2026-10-06: the wrapper's `$!` after setsid named the
    # wrong / no process, so status / stop failed); removed at the end
    pid_path = os.path.join(args.out, "loop.pid")
    with open(pid_path, "w") as fh:
        fh.write(f"{os.getpid()}\n")
    try:
        return _run(args)
    finally:
        try:
            os.remove(pid_path)
        except OSError:
            pass


def _run(args):

    b = busy()
    if b:
        log(args.out, "REFUSING: camera/runtime processes are running (stop the runtime and "
                      "disarm cron first): " + " | ".join(b))
        return 2
    sha0, active = crontab_sha()
    if active:
        log(args.out, f"WARNING: crontab has {len(active)} ACTIVE rc_run_capture_cycle line(s): a "
                      "cycle could start mid-test and own the camera. Disarm cron first (this "
                      "script never edits it).")
    sys.path.insert(0, args.app_dir)
    os.chdir(args.app_dir)
    try:
        import rc_capture as rc
        import rc_exposure_profile  # noqa: F401  (#133 must be deployed)
        import rc_progressive_jpeg as P
        import rc_raw_jxl as X
    except ImportError as exc:
        log(args.out, f"REFUSING: the app dir lacks a module ({exc}); deploy #133 + #120 first")
        return 2
    cfg_path, cfg_src = unit_config(args.app_dir, args.out)
    base = P.resolve_rc_settings(cfg_path)
    base["config_path"] = cfg_path
    controls = P._load_camera_controls_island(cfg_path)
    crop_xywh = list(base["crop_native_xywh"])
    exp = controls.get("exposure") if isinstance(controls, dict) else None
    if isinstance(exp, dict) and exp.get("enabled") and (exp.get("shutter_us") or exp.get("analogue_gain")):
        log(args.out, "WARNING: the unit's camera_controls fix shutter/gain: both members of a pair "
                      "use them, so low_gain cannot act (config_validate refuses this combination)")
    lowgain = {"profile": "low_gain", "max_shutter_us": args.max_shutter_us,
               "max_gain": args.max_gain}
    manifest = {"script": os.path.abspath(__file__), "host": socket.gethostname(),
                "started_utc": utc(), "config": cfg_src, "camera_controls": controls,
                "still_crop": crop_xywh, "interval_s": args.interval_s, "low_gain": lowgain,
                "crontab_sha_start": sha0, "crontab_active_cycle_lines": active,
                "software_sha": getattr(P, "get_software_sha", lambda: None)()}
    mpath = os.path.join(args.out, "run_manifest.json")

    def write_manifest(**extra):
        manifest.update(extra)
        with open(mpath, "w") as fh:
            json.dump(manifest, fh, indent=1, default=str)
    write_manifest()
    log(args.out, f"start: {cfg_src}; crop {crop_xywh}; every {args.interval_s} s; low_gain "
                  f"{lowgain}; crontab {sha0}")
    csv_path = os.path.join(args.out, "pairs.csv")
    new_csv = not os.path.exists(csv_path)
    pair, done, skipped = 0, 0, 0
    with open(csv_path, "a", newline="") as fh:
        wr = csv.DictWriter(fh, fieldnames=CSV_KEYS, extrasaction="ignore")
        if new_csv:
            wr.writeheader()
        while not STOP["flag"] and (args.max_pairs == 0 or pair < args.max_pairs):
            t_start = time.monotonic()
            pair += 1
            free_mb = shutil.disk_usage(args.out).free // 2 ** 20
            if free_mb < args.min_free_mb:
                log(args.out, f"STOP: {free_mb} MB free < {args.min_free_mb} MB")
                break
            b = busy()
            if b:
                skipped += 1
                log(args.out, f"pair {pair}: SKIPPED, camera busy: {' | '.join(b)}")
            else:
                for profile in ("auto", "low_gain"):
                    if STOP["flag"]:
                        break
                    settings = {"camera_controls": controls} if controls else {}
                    if profile == "low_gain":
                        settings["exposure_profile"] = lowgain
                    d = os.path.join(args.out, "pairs", f"{pair:03d}_{profile}")
                    try:
                        row = capture(args, rc, X, P, settings or None, base, crop_xywh, d, profile)
                    except Exception as exc:                 # one bad capture never ends the run
                        row = {"profile": profile, "ok": False,
                               "why": f"{type(exc).__name__}: {exc}", "dir": os.path.basename(d)}
                    row.update(utc=utc(), pair=pair)
                    wr.writerow(row)
                    fh.flush()
                    log(args.out, f"pair {pair} {profile}: ok={row['ok']} {row.get('why', '')} "
                                  f"exp={row.get('exposure_us')} again={row.get('analogue_gain')} "
                                  f"lux={row.get('lux')} tuning={row.get('tuning_file_logged')}")
                done += 1
            while not STOP["flag"] and time.monotonic() - t_start < args.interval_s:
                time.sleep(1)
    sha1, _ = crontab_sha()
    left = busy()
    write_manifest(ended_utc=utc(), stopped_by=STOP["why"] or "limit", pairs_attempted=pair,
                   pairs_captured=done, pairs_skipped_busy=skipped, crontab_sha_end=sha1,
                   crontab_unchanged=sha1 == sha0, processes_left=left)
    log(args.out, f"end ({STOP['why'] or 'limit'}): {done} pair(s), {skipped} skipped; crontab "
                  f"{'unchanged' if sha1 == sha0 else 'CHANGED during the run'}; processes left: "
                  f"{left or 'none'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

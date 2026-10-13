#!/usr/bin/env python3
# filename: pool_sequence.py
# description: Pool test 2026-10-13 — calibration DNG burst, 3 h still+video loop, marks and console preview, driven by bmcam/cmd.
"""
Pool test sequence for bmcam003 / bmcam004 (Tue 2026-10-13, Nick's approved plan).

PURPOSE
  One process that owns the camera and the Spotter UART for the pool session
  (the production supervisor is NOT running; tools/pool_mode.sh swaps the
  @reboot line). Commands arrive on bmcam/cmd from the nereus000 console as
  `bm pub bmcam/cmd {json} 1 1`; every answer goes to the Spotter USB console
  (spotter/printf, zero cellular quota). NOTHING is transmitted over cellular.

  1. CALIBRATION (`cal`): every interval_s (10) for duration_s (1200), auto-stop.
     Full native 4608x2592 JPEG + DNG of the SAME exposure.
  2. LOOP (`loop`): every period_s (600) for iterations (18), auto-stop:
       a) one production still (native JPEG + DNG) -> the pjpg
          (<stem>_compressed.jpg, production ladder under the 195-msg cap) and
          the nrjxl (rc_raw_jxl.encode_still, B3a) from that one capture
       b) a 30 s 1280x720 10 fps full-sensor clip (wide_720p geometry) at
          2.0 Mbit/s
  3. After the loop: idle with the Pi up (Wi-Fi download). Never power_halt.

  Commands (JSON <= 248 B, console id range): {"id":N,"c":"pool","v":<verb>,...}
    status | cal [duration_s, interval_s] | loop [iterations, period_s]
    mark note=<[A-Za-z0-9_:./+,-]{1,40}> | stop | preview [lines="a-b,c"]
  Every command is answered once: `[<host>] OK id=<N> pool <verb> ...` or
  `[<host>] ERR id=<N> pool <verb> <reason>`. A repeated id (live + ~60 s mote
  replay) re-prints the first answer with ` dup` and does not run again.
  Auto-start: no `loop` within --loop-autostart-min (30) of calibration end ->
  the loop starts anyway (logged + a console line). `stop` while idle cancels
  a pending auto-start.

PRODUCTION PARITY (CLAUDE.md §6)
  Settings: the unit's EFFECTIVE config, read-only: config_v2.load_for_boot
  (camera_config.yaml) ⊕ the command overlay (bm_command_state_v2.json) via
  supervisor_config.resolve — the same merge the supervisor runs — rendered
  to <run>/effective_camera_schedule.yaml (never to the production tmpfs
  render, never to camera_config.yaml or the state file). Focus, AG lock,
  shutter cap (exposure_profile low_gain) all come from it; nothing hardcoded.
  Capture: rc_capture.run_raw_capture_once — the production native argv
  (native_capture_command(raw=True)), ONE attempt, 30 s watchdog, no <WS>
  telemetry (which would open the UART). Measured on bmcam004: median 3.62 s
  per capture with --raw (runs/s28_ladder_20261004/RESULTS.md:15); the s28
  pair loop ran captures 5 s start-to-start with --keep-dng
  (runs/s28_lowgain_sunrise_20261006/gate.log:27-28). So one process per frame
  fits a 10 s cadence with ~6 s spare; rpicam-still --timelapse would be
  faster but is a different capture path (not production), so it is not used.
  pjpg: rc_jpeg_encoder.prepare_source + rc_quality_selector.select_quality
  (production ladder, cap, chunking). nrjxl: rc_raw_jxl.encode_still with the
  effective still_raw cfg; layout forced to B3a ("rgb", --nrjxl-layout).
  Video: video_recorder.build_encoder_command (always --mode, --roi of the
  mode FOV) on video_geometry preset wide_720p at fps 10, the effective
  camera controls, plus the low_gain --tuning-file (production video does not
  apply the profile; we do so stills and clip share exposure; retried once
  without it on failure, logged). Bitrate 2.0 Mbit/s = the V1 720p look
  rehearsal (`--bitrate 2000000`, hil/gates/V1_720p_look_rehearsal.md:24 on
  branch claude/underwater-video-feasibility-df934d @333ca7a1); the France cam1
  30 s 720p source measured 1.9 Mbit/s = 7,174,297 B
  (runs/video_feasibility_20261008/encode_sweep_france_cam1_30s.txt:14, same branch).

OUTPUTS  /home/pi/pool_runs/<YYYYMMDD_HHMMSS UTC>_<host>/
  calibration/<UTC>_cal_NNNN.{jpg,dng,metadata.json,stdout.log,stderr.log}
  loop/<UTC>_loop_NN_still.{jpg,dng,...}  <...>_still_compressed.jpg (pjpg)
       <...>_still_compressed.nrjxl (+ .capture_metadata.json)  <UTC>_loop_NN_video.mp4
  preview/<UTC>_preview_<img_id>.{jpg (native), _preview.jpg}
  effective_camera_schedule.yaml, run_manifest.json, pool.log, console.log

EXAMPLES
  On the Pi (TE): python3 pool_sequence.py                       # waits for commands
  Bench compressed: send cal {"duration_s":120,"interval_s":10}, loop {"iterations":3,"period_s":120}
  Mac smoke: python3 pool_sequence.py --fake --run-root /tmp/pool --exit-when-done

KNOWN LIMITATIONS
  - Pi clock: filenames use the Pi clock (no Spotter time read); nereus000
    timestamps the console lines independently.
  - A preview waits for the camera (up to ~35 s behind a loop clip).
  - 0.031 s/line console pacing over ~250 lines is not yet measured (Sprint13
    measured 0 drops at 0.05 s / 123 lines); the crc + resend covers drops.
  - Storage estimates use the measured DNG size (24,029,068 B,
    runs/s28_ladder_20261004/gate.log:51) and an ASSUMED 8 MB native JPEG.
"""

import argparse
import collections
import copy
import json
import math
import os
import queue
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import command_wire  # noqa: E402
import rc_preview  # noqa: E402

SCHEMA = "bmcam_pool_run_v1"
DEFAULT_RUN_ROOT = "/home/pi/pool_runs"
DEFAULT_CONFIG_DIR = "/home/pi/BM_Devel_Pi"
TOPIC = "bmcam/cmd"

DEFAULT_CAL_DURATION_S = 1200
DEFAULT_CAL_INTERVAL_S = 10
DEFAULT_LOOP_ITERATIONS = 18
DEFAULT_LOOP_PERIOD_S = 600
DEFAULT_VIDEO_S = 30
DEFAULT_VIDEO_FPS = 10
DEFAULT_VIDEO_PRESET = "wide_720p"
DEFAULT_VIDEO_BITRATE_MBPS = 2.0      # V1 720p rehearsal, see module doc
DEFAULT_MIN_FREE_MB = 1024            # margin kept free after a phase's estimate
DEFAULT_AUTOSTART_MIN = 30
LENS_MIN, LENS_MAX = 0.6, 1.0         # Nick 10/13: flat port, hyperfocal ~0.8 (dioptres)
SWEEP_LENS = (0.6, 0.7, 0.8, 0.9, 1.0)
DEFAULT_ARGS_FILE = "/home/pi/pool_args"   # extra CLI args, one place for cron + start
SIGTERM_JOIN_S = 150                  # > one loop iteration (~70 s assumed) with margin

EST_DNG_B = 24_029_068                # MEASURED, runs/s28_ladder_20261004/gate.log:51-60
EST_NATIVE_JPG_B = 8_000_000          # ASSUMPTION (q95 4608x2592), upper-ish bound
EST_SMALL_B = 400_000                 # pjpg + nrjxl + sidecars + logs per still (assumed)

VERBS = ("status", "cal", "loop", "mark", "stop", "preview", "sweep")
FIELDS = {"status": {}, "stop": {}, "sweep": {},
          "cal": {"duration_s": (10, 7200), "interval_s": (2, 600)},
          "loop": {"iterations": (1, 100), "period_s": (30, 7200)},
          "mark": {"note": "str"}, "preview": {"lines": "lines"}}
RE_NOTE = __import__("re").compile(r"[A-Za-z0-9_:./+,-]{1,40}\Z")

METADATA_KEYS = ("ScalerCrop", "AnalogueGain", "DigitalGain", "ExposureTime", "LensPosition",
                 "ColourGains", "ColourTemperature", "Lux", "FrameDuration", "AfState",
                 "AeLocked", "SensorTimestamp")


def utc_now():
    return datetime.now(timezone.utc)


def iso(dt=None):
    return (dt or utc_now()).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def ts_name(dt=None):
    return (dt or utc_now()).strftime("%Y%m%dT%H%M%SZ")


def free_mb(path):
    return int(shutil.disk_usage(path).free // (1024 * 1024))


def wifi_info():
    """(ssid, ip) for `status` — read-only (iwgetid / nmcli / hostname -I). Pool mode
    never applies network config: NetworkManager autoconnect owns wlan0 (EM 10/12:
    network_ap.sh default would fall back to the open AP and block ssh)."""
    def run(argv):
        try:
            return subprocess.run(argv, capture_output=True, text=True, timeout=2).stdout.strip()
        except Exception:
            return ""
    ssid = run(["iwgetid", "-r"])
    if not ssid:
        for row in run(["nmcli", "-t", "-f", "active,ssid", "dev", "wifi"]).splitlines():
            if row.startswith("yes:"):
                ssid = row[4:]
                break
    ip = (run(["hostname", "-I"]).split() or [""])[0]
    return (ssid or "none").replace(" ", "_")[:32], ip or "none"


def file_bytes(path):
    try:
        return os.path.getsize(path) if path else None
    except OSError:
        return None


def lens_arg(text):
    """argparse type for --lens-position: a float in LENS_MIN..LENS_MAX."""
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"{text!r} is not a number")
    if not (LENS_MIN <= value <= LENS_MAX) or value != value:
        raise argparse.ArgumentTypeError(f"{value} outside {LENS_MIN}..{LENS_MAX}")
    return value


def with_lens(capture_settings, lens_position):
    """Copy of the capture settings with MANUAL focus at lens_position (dioptres):
    camera_controls on, focus = {manual, lens_position}; every other control block
    untouched (each keeps its own enabled flag). None -> unchanged."""
    if lens_position is None:
        return capture_settings
    cs = copy.deepcopy(capture_settings or {})
    controls = dict(cs.get("camera_controls") or {})
    controls["enabled"] = True
    controls["focus"] = {"enabled": True, "mode": "manual",
                         "lens_position": round(float(lens_position), 4)}
    cs["camera_controls"] = controls
    return cs


def lens_of(capture_settings):
    """The manual lens position these settings will request, else None (AF/none)."""
    controls = (capture_settings or {}).get("camera_controls") or {}
    focus = controls.get("focus") or {}
    if not controls.get("enabled") or focus.get("enabled") is False:
        return None
    if str(focus.get("mode") or "").lower() != "manual":
        return None
    try:
        return round(float(focus.get("lens_position")), 4)
    except (TypeError, ValueError):
        return None


def fmt_lens(value):
    return "auto" if value is None else f"{value:g}"


# ---------------------------------------------------------------------------
# log + manifest (CLAUDE.md §9/§10: visible, self-contained)
# ---------------------------------------------------------------------------

class Tee:
    """stdout -> terminal/cron log AND pool.log, so every rc_* debug print lands
    in the run folder too."""

    def __init__(self, stream, path):
        self.stream = stream
        self.fh = open(path, "a", encoding="utf-8", buffering=1)
        self.lock = threading.Lock()

    def write(self, text):
        with self.lock:
            try:
                self.stream.write(text)
            except Exception:
                pass
            self.fh.write(text)

    def flush(self):
        with self.lock:
            try:
                self.stream.flush()
            except Exception:
                pass
            self.fh.flush()


def log(msg):
    print(f"{iso()} [POOL] {msg}", flush=True)


class Manifest:
    """run_manifest.json, rewritten atomically after every change."""

    def __init__(self, path, data):
        self.path = path
        self.data = data
        self.lock = threading.Lock()
        self.save()

    def save(self):
        import atomic_io
        with self.lock:
            text = json.dumps(self.data, indent=1, sort_keys=True, default=str)
            atomic_io.write_text(self.path, text)

    def append(self, key, item):
        with self.lock:
            self.data.setdefault(key, []).append(item)
        self.save()

    def set(self, key, value):
        with self.lock:
            self.data[key] = value
        self.save()

    def event(self, kind, **kw):
        self.append("events", dict(t_utc=iso(), kind=kind, **kw))


# ---------------------------------------------------------------------------
# effective config (read-only)
# ---------------------------------------------------------------------------

def load_effective(config_dir, run_dir):
    """-> dict(settings, capture_settings, raw_cfg, video_encoder, hash, ...).
    Reads camera_config.yaml + the v2 state (never writes either); renders the
    effective values into the RUN folder for the v1 loaders."""
    import config_v2
    import rc_progressive_jpeg as rc
    v1 = os.path.join(config_dir, "camera_schedule.yaml")
    v2 = os.path.join(config_dir, config_v2.V2_NAME)
    lkg = os.path.join(config_dir, config_v2.LKG_NAME)
    out = {"config_dir": config_dir, "lines": []}
    render = os.path.join(run_dir, "effective_camera_schedule.yaml")
    if os.path.exists(v2):
        import config_validate
        import supervisor_config
        boot = config_v2.load_for_boot(v1, v2, lkg)
        out["lines"].extend(boot.lines)
        if boot.level == "safe_minimal":
            raise RuntimeError("config: SAFE-MINIMAL (no usable config): " + "; ".join(boot.lines))
        base = dict(boot.values)
        state_path = supervisor_config.state_path_for(base)
        state = config_v2.read_state(state_path)
        env = config_validate.probe_env([base.get("schedule.timezone")])
        eff = supervisor_config.resolve(base, state, env=env)
        out["lines"].extend(eff.lines)
        import atomic_io
        atomic_io.write_text(render, supervisor_config.render_values(eff.values))
        out.update(level=boot.level, hash=eff.hash, base_hash=config_v2.config_hash(base),
                   overlay=dict(eff.overlay), dropped=[p for p, _v, _w in eff.dropped],
                   state_path=state_path,
                   effective_values={k: eff.values.get(k) for k in sorted(eff.values)
                                     if k.startswith(("camera.", "still.", "video."))})
    else:
        # v1-only unit: copy the file; no overlay merge (logged loudly).
        shutil.copyfile(v1, render)
        import hashlib
        with open(v1, "rb") as fh:
            out.update(level="v1", hash=hashlib.sha256(fh.read()).hexdigest()[:8],
                       overlay={}, dropped=[], state_path=None)
        out["lines"].append(f"[CFG][WARN] no {v2}: running the v1 file WITHOUT the command overlay")
    out["render"] = render
    settings = rc.resolve_rc_settings(render)
    out["settings"] = settings
    out["capture_settings"] = rc._capture_settings(settings)
    import rc_raw_jxl
    try:
        out["raw_cfg"] = rc_raw_jxl.load_raw_config(render)
    except ValueError as exc:
        out["lines"].append(f"[CFG][ERR] still_raw unreadable ({exc}); registry defaults")
        out["raw_cfg"] = dict(rc_raw_jxl.DEFAULT_CONFIG)
    try:
        import video_recorder
        out["video_encoder"] = dict(video_recorder.load_video_config(render).get("encoder") or {})
    except Exception as exc:
        out["lines"].append(f"[CFG][WARN] video island unreadable ({exc}); no encoder knobs")
        out["video_encoder"] = {}
    return out


# ---------------------------------------------------------------------------
# camera (real + fake share one interface)
# ---------------------------------------------------------------------------

class CaptureError(RuntimeError):
    pass


def applied_from_metadata(meta):
    return {k: meta.get(k) for k in METADATA_KEYS if k in (meta or {})}


class RealCamera:
    def __init__(self, cfg, video_s, video_fps, video_preset, video_bitrate_mbps):
        import rc_capture
        import video_geometry
        self.rc_capture = rc_capture
        self.s = cfg["settings"]
        self.capture_settings = cfg["capture_settings"]
        self.still_cmd, self.backend = rc_capture._select_camera_command(self.s["capture_backend"])
        if self.still_cmd is None:
            raise RuntimeError(f"no rpicam-still/libcamera-still (backend {self.backend})")
        geo = video_geometry.resolve_geometry({"preset": video_preset, "fps": video_fps})
        self.vcfg = {"geometry": geo, "clip_minutes": float(video_s) / 60.0,
                     "bitrate_mbps": float(video_bitrate_mbps),
                     "encoder": cfg.get("video_encoder") or {}}
        self.video_s = float(video_s)
        self.profile_args, self.profile_info = rc_capture._exposure_profile_args(
            self.capture_settings or {})

    def describe(self):
        g = self.vcfg["geometry"]
        return {"still_command": self.still_cmd, "backend": self.backend,
                "capture_settings": self.capture_settings,
                "video": {"preset": g["preset"], "output_wh": g["output_wh"],
                          "sensor_mode": g["sensor_mode"], "roi": g["roi"], "fps": g["fps"],
                          "crop_native_xywh": g["crop_native_xywh"],
                          "bitrate_mbps": self.vcfg["bitrate_mbps"], "seconds": self.video_s,
                          "encoder": self.vcfg["encoder"],
                          "exposure_profile_args": self.profile_args}}

    def capture_still(self, out_dir, stem, lens_position=None):
        """lens_position: a one-capture override (sweep); None = the run's settings."""
        native = os.path.join(out_dir, stem + ".jpg")
        t0 = time.monotonic()
        settings = (with_lens(self.capture_settings, lens_position)
                    if lens_position is not None else self.capture_settings)
        info, dng, why = self.rc_capture.run_raw_capture_once(
            self.still_cmd, native, self.s["source_width"], self.s["source_height"],
            self.s["source_jpeg_quality"], os.path.join(out_dir, stem),
            settings=settings)
        dt = time.monotonic() - t0
        if info is None:
            raise CaptureError(f"capture failed: {why} ({dt:.1f} s)")
        meta = self.rc_capture._load_libcamera_metadata_json(info.get("metadata_json"))
        return {"jpg": native, "dng": dng, "dng_why": why, "metadata_json": info.get("metadata_json"),
                "metadata": meta, "capture_s": round(dt, 2),
                "command": info.get("capture_command"),
                "requested": info.get("requested_camera_controls")}

    def _run(self, argv, log_path, timeout_s):
        t0 = time.monotonic()
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(f"$ {' '.join(argv)}\n")
            fh.flush()
            try:
                rc = subprocess.run(argv, stdout=fh, stderr=subprocess.STDOUT,
                                    timeout=timeout_s, check=False).returncode
            except subprocess.TimeoutExpired:
                rc = 124
        return rc, round(time.monotonic() - t0, 2)

    def record_video(self, out_dir, stem):
        import video_recorder
        vid_cmd, _ = video_recorder._select_video_command(self.s["capture_backend"])
        part = os.path.join(out_dir, stem + ".h264.part")
        mp4 = os.path.join(out_dir, stem + ".mp4")
        log_path = os.path.join(out_dir, stem + ".video.log")
        controls = (self.capture_settings or {}).get("camera_controls")
        # Same doctrine as rc_capture / record_one_clip: a control the camera app
        # refuses must not cost the clip. Ladder: everything -> focus only (the pool's
        # lens position matters most) -> geometry + bitrate only.
        ladder = [(controls, list(self.profile_args), None)]
        focus = (controls or {}).get("focus")
        if focus and (self.profile_args or set(controls) - {"enabled", "focus"}):
            ladder.append(({"enabled": True, "focus": focus}, [],
                           "retried with focus only (no tuning file / other controls)"))
        if controls or self.profile_args:
            ladder.append((None, [], "retried without camera controls / tuning file"))
        rec_s, fallback, rc = 0.0, None, None
        for i, (ctl, extra, why) in enumerate(ladder):
            if i:
                fallback = f"rc={rc}: {why}"
                log(f"[VID][WARN] clip {stem}: {fallback}")
                if os.path.exists(part):
                    os.remove(part)
            argv, requested = video_recorder.build_encoder_command(
                self.s, self.vcfg, part, binary=vid_cmd, controls=ctl)
            argv = list(argv) + extra
            rc, more = self._run(argv, log_path, self.video_s + 30)
            rec_s += more
            if rc == 0 and file_bytes(part):
                break
        if rc != 0 or not file_bytes(part):
            raise CaptureError(f"video encode failed rc={rc} part_bytes={file_bytes(part)}")
        ffmpeg = shutil.which("ffmpeg")
        out_path = mp4
        mux_s = 0.0
        if ffmpeg:
            tmp = mp4 + ".tmp"
            mrc, mux_s = self._run(video_recorder.build_mux_command(
                ffmpeg, self.vcfg["geometry"]["fps"], part, tmp), log_path, 120)
            if mrc == 0 and file_bytes(tmp):
                os.rename(tmp, mp4)
                os.remove(part)
            else:
                out_path = os.path.join(out_dir, stem + ".h264")
                os.rename(part, out_path)
                log(f"[VID][WARN] mux failed rc={mrc}; kept raw {out_path}")
        else:
            out_path = os.path.join(out_dir, stem + ".h264")
            os.rename(part, out_path)
            log(f"[VID][WARN] no ffmpeg; kept raw {out_path}")
        return {"path": out_path, "bytes": file_bytes(out_path), "record_s": rec_s,
                "mux_s": mux_s, "argv": argv, "fallback": fallback, "requested": requested}


class FakeCamera:
    """Mac / unit-test camera: a synthetic native JPEG (real size, so the real
    crop/pjpg/preview code runs), a stub DNG, libcamera-shaped metadata."""

    def __init__(self, cfg=None, capture_s=0.05, video_s=0.05, fail_on=()):
        self.capture_s = capture_s
        self.video_s = video_s
        self.fail_on = set(fail_on)
        self.calls = 0
        self.lens = None                     # main() sets the run's lens position
        self.lenses = []                     # what each capture requested (tests)

    def describe(self):
        return {"fake": True, "lens": self.lens}

    def capture_still(self, out_dir, stem, lens_position=None):
        from PIL import Image, ImageDraw
        self.calls += 1
        if self.calls in self.fail_on:
            raise CaptureError(f"fake failure on call {self.calls}")
        time.sleep(self.capture_s)
        img = Image.new("RGB", (4608, 2592), (20, 60 + (self.calls * 7) % 120, 90))
        d = ImageDraw.Draw(img)
        for i in range(0, 4608, 256):
            d.rectangle([i, 900, i + 128, 1700], fill=(200, (i // 16) % 255, 40))
        native = os.path.join(out_dir, stem + ".jpg")
        img.save(native, quality=85)
        dng = os.path.join(out_dir, stem + ".dng")
        with open(dng, "wb") as fh:
            fh.write(b"FAKE-DNG" * 128)
        lens = lens_position if lens_position is not None else self.lens
        self.lenses.append(lens)
        meta = {"ScalerCrop": [0, 0, 4608, 2592], "AnalogueGain": 1.12, "ExposureTime": 20000,
                "LensPosition": 1.82 if lens is None else lens, "DigitalGain": 1.0}
        meta_path = os.path.join(out_dir, stem + ".metadata.json")
        with open(meta_path, "w") as fh:
            json.dump(meta, fh)
        return {"jpg": native, "dng": dng, "dng_why": None, "metadata_json": meta_path,
                "metadata": meta, "capture_s": self.capture_s, "command": ["fake"],
                "requested": {}}

    def record_video(self, out_dir, stem):
        time.sleep(self.video_s)
        path = os.path.join(out_dir, stem + ".mp4")
        with open(path, "wb") as fh:
            fh.write(b"\x00\x00\x00\x18ftypmp42" + b"\x00" * 4096)
        return {"path": path, "bytes": file_bytes(path), "record_s": self.video_s, "mux_s": 0.0,
                "argv": ["fake"], "fallback": None, "requested": {}}


# ---------------------------------------------------------------------------
# still products: pjpg + nrjxl from ONE capture
# ---------------------------------------------------------------------------

class PoolBudget:
    """CycleBudget stand-in: nothing is transmitted, so time never limits the
    ladder; the 195-message cap does (exactly what the uplink would allow)."""

    def __init__(self, pace_s, seconds=3600.0):
        self.seconds_per_message = float(pace_s)
        self._t0 = time.monotonic()
        self._end = self._t0 + seconds

    def remaining_s(self):
        return max(0.0, self._end - time.monotonic())

    def elapsed_s(self):
        return time.monotonic() - self._t0

    def has_time_for(self, seconds):
        return self.remaining_s() >= seconds

    def messages_fit(self, n):
        return self.remaining_s() >= n * self.seconds_per_message


def real_nrjxl(dng, metadata, raw_cfg, settings, out_dir, stem, budget, fallback_msgs):
    import rc_raw_jxl
    work = rc_raw_jxl.work_dir_for(out_dir, stem)
    try:
        return rc_raw_jxl.encode_still(
            dng, metadata, raw_cfg, crop_xywh=settings["crop_native_xywh"], budget=budget,
            message_cap=settings["message_cap"], chunk_b64_chars=settings["pacing_chunk_b64_chars"],
            reserve_msgs=0, fallback_msgs=fallback_msgs, work_dir=work)
    finally:
        rc_raw_jxl.remove_quietly(work)


def fake_nrjxl(dng, metadata, raw_cfg, settings, out_dir, stem, budget, fallback_msgs):
    time.sleep(0.02)
    blob = b"NR" + b"\x01" * 40_000
    return {"blob": blob, "distance": 1.4, "message_count": 140, "attempts": 1,
            "encode_s": 0.02, "attempt_log": [], "timings": {}}


def still_products(cap, settings, raw_cfg, out_dir, stem, nrjxl_fn):
    """-> (pjpg dict, nrjxl dict). Each side records its own error; one failing
    never costs the other."""
    import atomic_io
    from rc_jpeg_encoder import prepare_source
    from rc_quality_selector import select_quality
    budget = PoolBudget(settings["pacing_delay_seconds"])
    pj, nr = {}, {}
    try:
        t0 = time.monotonic()
        source = prepare_source(cap["jpg"], settings["crop_native_xywh"], settings["output_width"])
        sel = select_quality(source, budget, ladder=settings["quality_ladder"],
                             message_cap=settings["message_cap"],
                             chunk_b64_chars=settings["pacing_chunk_b64_chars"])
        enc = sel["encode"]
        path = os.path.join(out_dir, stem + "_compressed.jpg")
        atomic_io.write_bytes(path, enc["jpeg_data"])
        pj = {"path": path, "bytes": enc["jpeg_bytes"], "q": sel["quality"],
              "msgs": enc["message_count"], "fits": sel["fits"], "reason": sel["reason"],
              "wh": list(source.size), "s": round(time.monotonic() - t0, 2)}
        atomic_io.write_text(path + ".capture_metadata.json", json.dumps(
            dict(pj, img_format="pjpg", crop_native_xywh=list(settings["crop_native_xywh"]),
                 native=cap["jpg"], applied=applied_from_metadata(cap["metadata"]),
                 attempt_log=sel["attempt_log"]), sort_keys=True))
        log(f"pjpg {os.path.basename(path)} q{pj['q']} {pj['bytes']} B {pj['msgs']} msgs "
            f"({pj['s']} s)")
    except Exception as exc:
        pj = {"error": f"{type(exc).__name__}: {exc}"}
        log(f"[ERR] pjpg failed: {pj['error']}")
    if not cap.get("dng"):
        nr = {"error": f"no DNG ({cap.get('dng_why')})"}
        log(f"[ERR] nrjxl skipped: {nr['error']}")
        return pj, nr
    try:
        t0 = time.monotonic()
        res = nrjxl_fn(cap["dng"], cap["metadata"], raw_cfg, settings, out_dir, stem, budget,
                       pj.get("msgs") or 0)
        import rc_raw_jxl
        path = os.path.join(out_dir, stem + rc_raw_jxl.CONTENT_SUFFIX)
        atomic_io.write_bytes(path, res["blob"])
        nr = {"path": path, "bytes": len(res["blob"]), "distance": res.get("distance"),
              "msgs": res.get("message_count"), "attempts": res.get("attempts"),
              "encode_s": res.get("encode_s"), "s": round(time.monotonic() - t0, 2),
              "layout": raw_cfg.get("layout"), "effort": raw_cfg.get("effort")}
        atomic_io.write_text(path + ".capture_metadata.json", json.dumps(
            dict(nr, img_format="nrjxl", crop_native_xywh=list(settings["crop_native_xywh"]),
                 native=cap["jpg"], applied=applied_from_metadata(cap["metadata"]),
                 attempt_log=res.get("attempt_log"), timings=res.get("timings")),
            sort_keys=True, default=str))
        log(f"nrjxl {os.path.basename(path)} {nr['bytes']} B d={nr['distance']} "
            f"{nr['msgs']} msgs ({nr['s']} s)")
    except Exception as exc:
        detail = getattr(exc, "detail", None)
        nr = {"error": f"{type(exc).__name__}: {getattr(exc, 'code', '')} {detail or exc}"}
        log(f"[ERR] nrjxl failed: {nr['error']}")
    return pj, nr


# ---------------------------------------------------------------------------
# UART link (real + fake)
# ---------------------------------------------------------------------------

class SerialLink:
    """Shared port: one reader thread (RawPubScanner, the daemon's decoder);
    every write from the controller's main thread (CommandDaemon contract)."""

    def __init__(self, config_path, topic=TOPIC):
        import serial
        from bm_frame_decoder import RawPubScanner
        from bm_serial import BristlemouthSerial, load_uart_config
        port, baud = load_uart_config(config_path)
        self.port_desc = f"{port}@{baud}"
        self.ser = serial.Serial(port, baud, timeout=0.1)
        self.bm = BristlemouthSerial(uart=self.ser, network_type=1)
        self.scanner = RawPubScanner(topic=topic)
        self.topic = topic
        self.inbound = queue.Queue()
        self.stop_evt = threading.Event()
        self.read_errors = 0
        self.thread = None

    def start(self):
        from spotter_time_sync import _build_subscribe_frame
        try:
            self.ser.reset_input_buffer()
        except Exception:
            pass
        frame = _build_subscribe_frame(self.topic.encode("utf-8"))
        self.ser.write(frame)
        log(f"subscribed {self.topic} on {self.port_desc} ({len(frame)} B frame)")
        self.thread = threading.Thread(target=self._reader, name="pool-uart-reader", daemon=True)
        self.thread.start()

    def _reader(self):
        while not self.stop_evt.is_set():
            try:
                chunk = self.ser.read(256)
            except Exception as exc:
                self.read_errors += 1
                log(f"[WARN] uart read error #{self.read_errors}: {exc}")
                time.sleep(0.5)
                continue
            if chunk:
                for payload in self.scanner.feed(chunk):
                    self.inbound.put(payload)

    def poll(self):
        out = []
        while True:
            try:
                out.append(self.inbound.get_nowait())
            except queue.Empty:
                return out

    def print_line(self, line):
        self.bm.spotter_print(line)

    def close(self):
        self.stop_evt.set()
        if self.thread:
            self.thread.join(timeout=2)
        try:
            self.ser.close()
        except Exception:
            pass


class FakeLink:
    """Scripted inbound payloads [(t_s, payload)], console lines kept in memory.
    `drop` = predicate(line) -> True drops that console line (wire tests)."""

    def __init__(self, script=(), clock=time.monotonic, drop=None):
        self.script = sorted(script, key=lambda x: x[0])
        self.clock = clock
        self.t0 = None
        self.lines = []
        self.drop = drop
        self.dropped = []

    def start(self):
        self.t0 = self.clock()
        log("fake link started")

    def poll(self):
        now = self.clock() - (self.t0 or self.clock())
        out = []
        while self.script and self.script[0][0] <= now:
            payload = self.script.pop(0)[1]
            if isinstance(payload, dict):
                payload = json.dumps(payload, separators=(",", ":"))
            out.append(payload.encode("ascii") if isinstance(payload, str) else payload)
        return out

    def print_line(self, line):
        if self.drop and self.drop(line):
            self.dropped.append(line)
            return
        self.lines.append(line)

    def close(self):
        pass


# ---------------------------------------------------------------------------
# command parsing
# ---------------------------------------------------------------------------

class CmdError(Exception):
    def __init__(self, cid, verb, reason):
        super().__init__(reason)
        self.cid, self.verb, self.reason = cid, verb, reason


def parse_command(payload):
    """bytes -> (id, verb, fields). Raises CmdError (cid None = unanswerable)."""
    try:
        data = command_wire.strict_loads(payload)
    except Exception as exc:
        raise CmdError(None, None, f"not strict JSON: {exc}")
    cid = data.get("id")
    if not isinstance(cid, int) or isinstance(cid, bool) or not 0 <= cid <= command_wire.MAX_ID:
        raise CmdError(None, None, "id must be an integer")
    if len(payload) > command_wire.MAX_JSON_BYTES:
        raise CmdError(cid, None, f"{len(payload)}B>{command_wire.MAX_JSON_BYTES}B")
    if data.get("c") != "pool":
        raise CmdError(cid, str(data.get("c"))[:12], "pool_mode: only c=pool runs (production "
                       "commands resume after pool_mode.sh off)")
    verb = data.get("v")
    if verb not in VERBS:
        raise CmdError(cid, None, f"unknown verb {str(verb)[:16]!r}; verbs: {'|'.join(VERBS)}")
    fields = {k: v for k, v in data.items() if k not in ("id", "c", "v")}
    spec = FIELDS[verb]
    for key, value in fields.items():
        if key not in spec:
            raise CmdError(cid, verb, f"key {key[:16]} not taken by {verb}")
        rule = spec[key]
        if rule == "str":
            if not isinstance(value, str) or not RE_NOTE.match(value):
                raise CmdError(cid, verb, f"{key} is 1-40 of [A-Za-z0-9_:./+,-] (use _ for spaces)")
        elif rule == "lines":
            if not isinstance(value, str) or not rc_preview.RE_LINES.match(value):
                raise CmdError(cid, verb, "lines is like 3-7,12")
        else:
            lo, hi = rule
            if not isinstance(value, int) or isinstance(value, bool) or not lo <= value <= hi:
                raise CmdError(cid, verb, f"{key} must be an integer {lo}..{hi}")
    if verb == "mark" and "note" not in fields:
        raise CmdError(cid, verb, "mark needs note")
    return cid, verb, fields


# ---------------------------------------------------------------------------
# the controller
# ---------------------------------------------------------------------------

class PoolController:
    """Main thread: commands in, console out (single writer). Phase + preview
    work in threads that share one camera lock."""

    def __init__(self, args, link, camera, cfg, run_dir, manifest, host,
                 nrjxl_fn=real_nrjxl, clock=time.monotonic, sleep_fn=time.sleep):
        self.args = args
        self.link = link
        self.camera = camera
        self.cfg = cfg
        self.run_dir = run_dir
        self.manifest = manifest
        self.host = host
        self.nrjxl_fn = nrjxl_fn
        self.clock = clock
        self.sleep_fn = sleep_fn
        self.t_start = clock()
        self.camera_lock = threading.Lock()
        self.state_lock = threading.Lock()
        self.prio = collections.deque()      # acks / notices
        self.bulk = collections.deque()      # preview lines
        self.last_send = 0.0
        self.acks = {}
        self.phase = "idle"                  # idle | cal | loop
        self.worker = None
        self.stop_evt = threading.Event()
        self.cal_started = self.cal_done = False
        self.loop_started = self.loop_done = False
        self.cal_count = self.cal_target = 0
        self.loop_count = self.loop_target = 0
        self.autostart_at = None
        self.last_error = None
        self.marks = 0
        self.preview_busy = False
        self.preview_ids = set()
        self.last_preview = None             # {"img_id", "lines"}
        self.pending = {}                    # cid -> verb answered later (preview, sweep)
        # The manual lens position every capture of this run requests (None = AF /
        # no focus control); --lens-position overrides the effective config.
        self.lens = lens_of(cfg.get("capture_settings"))
        self.console_log = open(os.path.join(run_dir, "console.log"), "a", encoding="utf-8")
        for sub in ("calibration", "loop", "preview", "sweep"):
            os.makedirs(os.path.join(run_dir, sub), exist_ok=True)

    # ---- console ---------------------------------------------------------
    def say(self, text, bulk=False):
        line = command_wire.console_line(self.host, text)
        (self.bulk if bulk else self.prio).append(line)

    def _send_one(self, line):
        try:
            self.link.print_line(line)
        except Exception as exc:
            log(f"[WARN] console write failed: {exc}")
        self.console_log.write(f"{iso()} {line}\n")
        self.console_log.flush()

    def pump_console(self):
        """Send at most one line if the pacing slot is open. Acks first."""
        if not self.prio and not self.bulk:
            return False
        now = self.clock()
        if now - self.last_send < self.args.preview_line_s:
            return False
        line = self.prio.popleft() if self.prio else self.bulk.popleft()
        self._send_one(line)
        self.last_send = now
        return True

    # ---- status ------------------------------------------------------------
    def free(self):
        return free_mb(self.run_dir)

    def status_text(self):
        # Most useful first: console_line cuts anything past 240 chars.
        ssid, ip = wifi_info()
        return (f"phase={self.phase} cal={self.cal_count}/{self.cal_target} "
                f"loop={self.loop_count}/{self.loop_target} lp={fmt_lens(self.lens)} "
                f"run={os.path.basename(self.run_dir)} "
                f"free={self.free()}MB marks={self.marks} "
                f"err={(self.last_error or 'none')[:30].replace(' ', '_')} ip={ip} ssid={ssid} "
                f"up={int(self.clock() - self.t_start)}s"
                + (f" autostart_in={int(self.autostart_at - self.clock())}s"
                   if self.autostart_at else ""))

    # ---- commands ----------------------------------------------------------
    def ack(self, cid, verb, ok, text):
        word = "OK" if ok else "ERR"
        body = f"{word} id={cid} pool {verb or '?'}" + (f" {text}" if text else "")
        line = command_wire.console_line(self.host, body)
        self.acks[cid] = line
        self.prio.append(line)
        return line

    def handle_payload(self, payload):
        try:
            cid, verb, fields = parse_command(payload)
        except CmdError as exc:
            if exc.cid is None:
                log(f"[WARN] unanswerable payload ({exc.reason}): {payload[:80]!r}")
                self.manifest.event("cmd_unanswerable", reason=exc.reason,
                                    payload=payload[:120].decode("ascii", "replace"))
                return
            if exc.cid in self.acks:
                self._dup(exc.cid)
                return
            line = self.ack(exc.cid, exc.verb, False, exc.reason)
            self.manifest.append("commands", {"t_utc": iso(), "id": exc.cid, "verb": exc.verb,
                                              "ack": line, "dup": False})
            log(f"command id={exc.cid} rejected: {exc.reason}")
            return
        if cid in self.acks:
            self._dup(cid)
            return
        log(f"command id={cid} {verb} {fields}")
        try:
            ok, text = getattr(self, f"cmd_{verb}")(cid, fields)
        except Exception as exc:
            ok, text = False, f"internal {type(exc).__name__}: {exc}"
            log(f"[ERR] command id={cid} raised: {traceback.format_exc()}")
        if ok is None:
            # preview / sweep: answered later by their thread; reserve the id now
            self.acks[cid] = None
            self.pending[cid] = verb
            self.manifest.append("commands", {"t_utc": iso(), "id": cid, "verb": verb,
                                              "fields": fields, "ack": "pending", "dup": False})
            return
        line = self.ack(cid, verb, ok, text)
        self.manifest.append("commands", {"t_utc": iso(), "id": cid, "verb": verb,
                                          "fields": fields, "ack": line, "dup": False})

    def _dup(self, cid):
        line = self.acks.get(cid)
        if line is None:
            line = command_wire.console_line(
                self.host, f"OK id={cid} pool {self.pending.get(cid, 'preview')} in_progress")
        self.prio.append(line + " dup")
        log(f"command id={cid} duplicate (replay): re-printed its answer, not re-run")
        self.manifest.append("commands", {"t_utc": iso(), "id": cid, "dup": True, "ack": line})

    def cmd_status(self, cid, f):
        return True, self.status_text()

    def cmd_mark(self, cid, f):
        self.marks += 1
        mark = {"t_utc": iso(), "id": cid, "note": f["note"], "phase": self.phase,
                "cal_frame": self.cal_count, "loop_iteration": self.loop_count,
                "t_run_s": round(self.clock() - self.t_start, 1)}
        self.manifest.append("marks", mark)
        log(f"MARK #{self.marks} {f['note']} (phase={self.phase})")
        return True, f"#{self.marks} {f['note']} phase={self.phase} t={mark['t_utc']}"

    def cmd_stop(self, cid, f):
        if self.phase in ("cal", "loop", "sweep"):
            self.stop_evt.set()
            return True, f"phase={self.phase} stopping after the current step"
        if self.autostart_at is not None:
            self.autostart_at = None
            self.manifest.event("autostart_cancelled", id=cid)
            return True, "phase=idle autostart=cancelled"
        return True, "phase=idle nothing_running"

    def _space_check(self, need_b):
        free = self.free()
        need_mb = int(math.ceil(need_b / 1048576)) + self.args.min_free_mb
        return free >= need_mb, need_mb, free

    FRAME_EXT = (".jpg", ".dng", ".mp4", ".h264", ".nrjxl")

    def frames_in(self, sub):
        """Frames already on disk in <run>/<sub> (logs/sidecars don't count)."""
        try:
            return sum(1 for n in os.listdir(os.path.join(self.run_dir, sub))
                       if n.endswith(self.FRAME_EXT))
        except OSError:
            return 0

    def refused_reuse(self, sub, what):
        """EM 10/12: one run folder = one session. Never mix in-air rehearsal frames
        with the pool's; a new run needs a process restart (pool_mode.sh stop/start)."""
        if self.frames_in(sub):
            return (f"refused: run already has {what} frames, restart pool mode "
                    "(pool_mode.sh stop/start) for a new run")
        return None

    def cmd_cal(self, cid, f):
        if self.phase != "idle":
            return False, f"busy phase={self.phase}"
        why = self.refused_reuse("calibration", "calibration")
        if why:
            return False, why
        dur = f.get("duration_s", self.args.cal_duration_s)
        every = f.get("interval_s", self.args.cal_interval_s)
        frames = max(1, dur // every)
        ok, need, free = self._space_check(frames * (EST_DNG_B + EST_NATIVE_JPG_B + EST_SMALL_B))
        if not ok:
            return False, f"space need={need}MB free={free}MB (min_free_mb={self.args.min_free_mb})"
        self._start("cal", frames, every, dur, cid)
        return True, f"start frames={frames} every={every}s dur={dur}s free={free}MB"

    def loop_estimate_b(self, iterations):
        video_b = self.args.video_bitrate_mbps * 1e6 / 8 * self.args.video_s * 1.15
        dng = EST_DNG_B if self.args.loop_keep_dng else 0
        return iterations * (dng + EST_NATIVE_JPG_B + EST_SMALL_B + video_b)

    def cmd_loop(self, cid, f, auto=False):
        if self.phase != "idle":
            return False, f"busy phase={self.phase}"
        why = self.refused_reuse("loop", "loop")
        if why:
            return False, why
        n = f.get("iterations", self.args.loop_iterations)
        period = f.get("period_s", self.args.loop_period_s)
        ok, need, free = self._space_check(self.loop_estimate_b(n))
        if not ok:
            return False, f"space need={need}MB free={free}MB (min_free_mb={self.args.min_free_mb})"
        self.autostart_at = None
        self._start("loop", n, period, n * period, cid, auto=auto)
        return True, f"start iterations={n} every={period}s free={free}MB" + (" AUTO" if auto else "")

    def cmd_preview(self, cid, f):
        if "lines" in f:
            if not self.last_preview:
                return False, "no_last_preview"
            lines = self.last_preview["lines"]
            total = len(lines) - 2
            try:
                seqs = rc_preview.parse_line_spec(f["lines"], total)
            except ValueError as exc:
                return False, f"lines {exc}"
            for text in rc_preview.resend_lines(lines, seqs):
                self.say(text, bulk=True)
            self.manifest.event("preview_resend", id=cid, img_id=self.last_preview["img_id"],
                                seqs=seqs)
            eta = rc_preview.expected_seconds(len(seqs) + 2, self.args.preview_line_s)
            return True, f"resend img={self.last_preview['img_id']} lines={len(seqs)} eta={eta}s"
        if self.preview_busy:
            return False, "busy preview_in_progress"
        self.preview_busy = True
        threading.Thread(target=self._preview_worker, args=(cid,), name="pool-preview",
                         daemon=True).start()
        return None, None

    # ---- sweep (focus check) -------------------------------------------------
    def cmd_sweep(self, cid, f):
        """One full-res still (JPEG + DNG) at each SWEEP_LENS into sweep/<HHMMSS>/;
        later captures use the run's lens again (each rpicam-still sets it)."""
        if self.phase != "idle":
            return False, f"busy phase={self.phase}"
        if self.preview_busy:
            return False, "busy preview_in_progress"
        ok, need, free = self._space_check(
            len(SWEEP_LENS) * (EST_DNG_B + EST_NATIVE_JPG_B + EST_SMALL_B))
        if not ok:
            return False, f"space need={need}MB free={free}MB (min_free_mb={self.args.min_free_mb})"
        with self.state_lock:
            self.phase = "sweep"
        self.stop_evt.clear()
        self.worker = threading.Thread(target=self._sweep_worker, args=(cid,),
                                       name="pool-sweep", daemon=True)
        self.worker.start()
        return None, None

    def _sweep_worker(self, cid):
        """QE nit (#160): whatever happens (ENOSPC on makedirs / the manifest, a camera
        surprise), the id gets ONE answer and the phase returns to idle, so cal/loop
        are never blocked behind a dead sweep."""
        line = None
        try:
            line = self._sweep_body(cid)
        except Exception as exc:
            self.last_error = f"sweep {type(exc).__name__}: {exc}"
            log(f"[ERR] sweep id={cid} crashed: {traceback.format_exc()}")
            if self.acks.get(cid) is None:
                line = self.ack(cid, "sweep", False, f"crashed {type(exc).__name__}: {exc}"[:120])
        finally:
            with self.state_lock:
                self.phase = "idle"
        try:
            self.manifest.append("commands", {"t_utc": iso(), "id": cid, "verb": "sweep",
                                              "ack": line, "dup": False, "late_answer": True})
        except Exception as exc:
            log(f"[ERR] manifest write failed after sweep id={cid}: {exc}")

    def _sweep_body(self, cid):
        """The sweep itself. -> the answer line (already queued)."""
        t0 = self.clock()
        name = utc_now().strftime("%H%M%S")
        out_dir = os.path.join(self.run_dir, "sweep", name)
        n = 2
        while os.path.exists(out_dir):
            out_dir = os.path.join(self.run_dir, "sweep", f"{name}_{n}")
            n += 1
        os.makedirs(out_dir)
        frames, got = [], []
        log(f"SWEEP id={cid}: lens {','.join(f'{x:g}' for x in SWEEP_LENS)} -> {out_dir}")
        for lp in SWEEP_LENS:
            if self.stop_evt.is_set():
                break
            rec = {"lens_requested": lp, "t_utc": iso()}
            try:
                with self.camera_lock:
                    cap = self.camera.capture_still(out_dir, f"{ts_name()}_sweep_lp{lp:.2f}",
                                                    lens_position=lp)
                applied = applied_from_metadata(cap["metadata"])
                rec.update(jpg=os.path.basename(cap["jpg"]),
                           dng=os.path.basename(cap["dng"]) if cap.get("dng") else None,
                           jpg_bytes=file_bytes(cap["jpg"]), dng_bytes=file_bytes(cap["dng"]),
                           capture_s=cap["capture_s"], applied=applied)
                got.append(applied.get("LensPosition"))
            except Exception as exc:
                rec["error"] = f"{type(exc).__name__}: {exc}"
                self.last_error = f"sweep lp={lp}: {rec['error']}"
                log(f"[ERR] {self.last_error}")
                got.append(None)
            frames.append(rec)
        ok_n = sum(1 for r in frames if "error" not in r)
        dt = round(self.clock() - t0, 1)
        rel = os.path.relpath(out_dir, self.run_dir)
        self.manifest.append("sweeps", {"id": cid, "dir": rel, "frames": frames, "wall_s": dt,
                                        "run_lens": self.lens})
        got_txt = ",".join("x" if g is None else f"{float(g):.2f}" for g in got)
        if ok_n:
            line = self.ack(cid, "sweep", True,
                            f"dir={rel} n={ok_n}/{len(SWEEP_LENS)} "
                            f"req={','.join(f'{x:g}' for x in SWEEP_LENS)} got={got_txt} "
                            f"t={dt}s back_to_lp={fmt_lens(self.lens)}")
        else:
            line = self.ack(cid, "sweep", False, f"no frames ({(self.last_error or '')[:80]})")
        log(f"SWEEP id={cid} done: {ok_n}/{len(SWEEP_LENS)} in {dt} s, metadata lens {got_txt}")
        return line

    # ---- preview -------------------------------------------------------------
    def _preview_worker(self, cid):
        t0 = self.clock()
        try:
            if self.camera_lock.locked():
                self.say(f"pool preview id={cid} waiting for camera (phase={self.phase})")
            with self.camera_lock:
                now = utc_now()
                img_id = rc_preview.new_img_id(self.preview_ids, now)
                self.preview_ids.add(img_id)
                out_dir = os.path.join(self.run_dir, "preview")
                stem = f"{ts_name(now)}_preview_{img_id}"
                cap = self.camera.capture_still(out_dir, stem)
            if cap.get("dng"):
                os.remove(cap["dng"])        # the preview needs the JPEG only
            s = self.cfg["settings"]
            jpeg, q, attempts, wh = rc_preview.preview_from_native(
                cap["jpg"], s["crop_native_xywh"], s["output_width"],
                out_wh=self.args.preview_wh, quality=self.args.preview_q,
                max_bytes=self.args.preview_max_bytes)
            with open(os.path.join(out_dir, stem + "_preview.jpg"), "wb") as fh:
                fh.write(jpeg)
            lines = rc_preview.build_lines(self.host, self.host, img_id, jpeg, wh[0], wh[1], q,
                                           chunk_chars=self.args.preview_chunk)
            self.last_preview = {"img_id": img_id, "lines": lines}
            eta = rc_preview.expected_seconds(len(lines), self.args.preview_line_s)
            line = self.ack(cid, "preview", True,
                            f"img={img_id} n={len(lines) - 2} bytes={len(jpeg)} q={q} eta={eta}s")
            for text in lines:
                self.say(text, bulk=True)
            rec = {"t_utc": iso(), "id": cid, "img_id": img_id, "native": cap["jpg"],
                   "bytes": len(jpeg), "q": q, "attempts": attempts, "wh": list(wh),
                   "lines": len(lines) - 2, "console_eta_s": eta,
                   "prepare_s": round(self.clock() - t0, 2), "capture_s": cap["capture_s"],
                   "lens_requested": self.lens,
                   "applied": applied_from_metadata(cap["metadata"])}
            self.manifest.append("previews", rec)
            log(f"preview {img_id}: {len(jpeg)} B q{q} {len(lines) - 2} lines, "
                f"ready in {rec['prepare_s']} s, console ~{eta} s")
        except Exception as exc:
            self.last_error = f"preview {type(exc).__name__}: {exc}"
            line = self.ack(cid, "preview", False, self.last_error[:120])
            log(f"[ERR] {self.last_error}\n{traceback.format_exc()}")
        finally:
            self.preview_busy = False
        self.manifest.append("commands", {"t_utc": iso(), "id": cid, "verb": "preview",
                                          "ack": line, "dup": False, "late_answer": True})

    # ---- phases -----------------------------------------------------------
    def _start(self, phase, count, period, duration, cid, auto=False):
        self.stop_evt.clear()
        self.phase = phase
        if phase == "cal":
            self.cal_started, self.cal_count, self.cal_target = True, 0, count
        else:
            self.loop_started, self.loop_count, self.loop_target = True, 0, count
        self.manifest.event(f"{phase}_start", id=cid, count=count, period_s=period,
                            duration_s=duration, auto=auto, free_mb=self.free())
        log(f"PHASE {phase} start: {count} x every {period} s (id={cid}{' AUTO' if auto else ''})")
        self.worker = threading.Thread(target=self._phase_worker, args=(phase, count, period),
                                       name=f"pool-{phase}", daemon=True)
        self.worker.start()

    def _phase_worker(self, phase, count, period):
        t0 = self.clock()
        k, skipped, stopped = 0, 0, False
        body = self._cal_frame if phase == "cal" else self._loop_iteration
        try:
            while k < count:
                slot = t0 + k * period
                while self.clock() < slot and not self.stop_evt.is_set():
                    self.sleep_fn(min(0.2, max(0.0, slot - self.clock())))
                if self.stop_evt.is_set():
                    stopped = True
                    break
                if self.free() < self.args.min_free_mb:
                    self.last_error = f"low_space free={self.free()}MB < {self.args.min_free_mb}MB"
                    log(f"[ERR] {phase} stopped: {self.last_error}")
                    self.say(f"pool {phase} STOPPED {self.last_error}")
                    stopped = True
                    break
                body(k, round(self.clock() - slot, 2))
                k += 1
                now = self.clock()
                while k < count and t0 + (k + 1) * period <= now:
                    log(f"[WARN] {phase} slot {k} skipped (behind by {now - t0 - k * period:.1f} s)")
                    self.manifest.event(f"{phase}_slot_skipped", k=k)
                    k += 1
                    skipped += 1
        except Exception as exc:
            self.last_error = f"{phase} {type(exc).__name__}: {exc}"
            log(f"[ERR] {phase} worker died: {traceback.format_exc()}")
            stopped = True
        done_n = self.cal_count if phase == "cal" else self.loop_count
        self.manifest.event(f"{phase}_end", done=done_n, target=count, skipped=skipped,
                            stopped=stopped, wall_s=round(self.clock() - t0, 1),
                            free_mb=self.free())
        log(f"PHASE {phase} end: {done_n}/{count} done, {skipped} skipped, stopped={stopped}")
        self.say(f"pool {phase} done {done_n}/{count} skipped={skipped} stopped={int(stopped)} "
                 f"free={self.free()}MB")
        with self.state_lock:
            self.phase = "idle"
            if phase == "cal":
                self.cal_done = True
                if not self.loop_started and self.args.loop_autostart_min > 0:
                    self.autostart_at = self.clock() + self.args.loop_autostart_min * 60
                    log(f"loop auto-start armed: in {self.args.loop_autostart_min} min "
                        "unless a loop/stop command comes first")
            else:
                self.loop_done = True
                log("loop finished: idling with the Pi up (Wi-Fi download); no halt in pool mode")

    def _cal_frame(self, k, late_s):
        out_dir = os.path.join(self.run_dir, "calibration")
        stem = f"{ts_name()}_cal_{k + 1:04d}"
        rec = {"k": k + 1, "t_utc": iso(), "slot_late_s": late_s, "lens_requested": self.lens}
        t0 = self.clock()
        try:
            with self.camera_lock:
                cap = self.camera.capture_still(out_dir, stem)
            rec.update(jpg=os.path.basename(cap["jpg"]), dng=os.path.basename(cap["dng"] or "")
                       or None, jpg_bytes=file_bytes(cap["jpg"]), dng_bytes=file_bytes(cap["dng"]),
                       capture_s=cap["capture_s"], applied=applied_from_metadata(cap["metadata"]))
            if not cap.get("dng"):
                rec["warn"] = f"no DNG ({cap.get('dng_why')})"
            self.cal_count += 1
        except Exception as exc:
            rec["error"] = f"{type(exc).__name__}: {exc}"
            self.last_error = f"cal frame {k + 1}: {rec['error']}"
            log(f"[ERR] {self.last_error}")
        rec["frame_s"] = round(self.clock() - t0, 2)
        rec["free_mb"] = self.free()
        self.manifest.append("calibration", rec)
        log(f"cal {k + 1}/{self.cal_target}: {rec.get('jpg')} {rec.get('dng_bytes')} B DNG "
            f"{rec['frame_s']} s late={late_s}s free={rec['free_mb']}MB "
            f"AG={rec.get('applied', {}).get('AnalogueGain')} "
            f"exp={rec.get('applied', {}).get('ExposureTime')} "
            f"lens req={fmt_lens(self.lens)} got={rec.get('applied', {}).get('LensPosition')}"
            + (f" ERROR {rec['error']}" if "error" in rec else ""))

    def _loop_iteration(self, i, late_s):
        out_dir = os.path.join(self.run_dir, "loop")
        base = f"{ts_name()}_loop_{i + 1:02d}"
        rec = {"i": i + 1, "t_utc": iso(), "slot_late_s": late_s, "lens_requested": self.lens}
        t0 = self.clock()
        cap = None
        try:
            with self.camera_lock:
                cap = self.camera.capture_still(out_dir, base + "_still")
            rec["still"] = {"jpg": os.path.basename(cap["jpg"]), "jpg_bytes": file_bytes(cap["jpg"]),
                            "dng_bytes": file_bytes(cap["dng"]), "capture_s": cap["capture_s"],
                            "applied": applied_from_metadata(cap["metadata"])}
        except Exception as exc:
            rec["still"] = {"error": f"{type(exc).__name__}: {exc}"}
            self.last_error = f"loop {i + 1} still: {rec['still']['error']}"
            log(f"[ERR] {self.last_error}")
        t1 = self.clock()
        try:
            with self.camera_lock:
                rec["video"] = self.camera.record_video(out_dir, base + "_video")
            rec["video"]["path"] = os.path.basename(rec["video"]["path"])
        except Exception as exc:
            rec["video"] = {"error": f"{type(exc).__name__}: {exc}"}
            self.last_error = f"loop {i + 1} video: {rec['video']['error']}"
            log(f"[ERR] {self.last_error}")
        rec["video_s"] = round(self.clock() - t1, 2)
        if cap is not None:
            t2 = self.clock()
            pj, nr = still_products(cap, self.cfg["settings"], self.cfg["raw_cfg"], out_dir,
                                    base + "_still", self.nrjxl_fn)
            rec["pjpg"], rec["nrjxl"] = pj, nr
            for d in (pj, nr):
                if "path" in d:
                    d["path"] = os.path.basename(d["path"])
                if "error" in d:
                    self.last_error = f"loop {i + 1}: {d['error']}"
            rec["encode_s"] = round(self.clock() - t2, 2)
            if cap.get("dng") and not self.args.loop_keep_dng:
                os.remove(cap["dng"])
        rec["iteration_s"] = round(self.clock() - t0, 2)
        rec["free_mb"] = self.free()
        self.loop_count += 1
        self.manifest.append("loop", rec)
        v = rec.get("video", {})
        log(f"loop {i + 1}/{self.loop_target}: iteration {rec['iteration_s']} s "
            f"(still {rec.get('still', {}).get('capture_s')} s, video {rec['video_s']} s "
            f"{v.get('bytes')} B, encode {rec.get('encode_s')} s) free={rec['free_mb']}MB")
        self.say(f"pool loop {i + 1}/{self.loop_target} {rec['iteration_s']}s "
                 f"pjpg={rec.get('pjpg', {}).get('bytes')} nrjxl={rec.get('nrjxl', {}).get('bytes')} "
                 f"mp4={v.get('bytes')} free={rec['free_mb']}MB")

    # ---- main loop --------------------------------------------------------
    def tick(self):
        for payload in self.link.poll():
            self.handle_payload(payload)
        with self.state_lock:
            fire = (self.autostart_at is not None and self.clock() >= self.autostart_at
                    and self.phase == "idle" and not self.loop_started)
            if fire:
                self.autostart_at = None
        if fire:
            log(f"[WARN] LOOP AUTO-START: no loop command within {self.args.loop_autostart_min} "
                "min of calibration end")
            ok, text = self.cmd_loop(0, {}, auto=True)
            self.manifest.event("loop_autostart", ok=ok, text=text)
            self.say(f"pool loop AUTOSTART {'ok' if ok else 'FAILED'} {text}")
        sent = self.pump_console()
        return sent

    def idle_done(self):
        """True when nothing is left to do (fake/smoke --exit-when-done only)."""
        return (self.loop_done and self.phase == "idle" and not self.preview_busy
                and not self.prio and not self.bulk)

    def run(self, exit_when_done=False, max_runtime_s=None, stop_flag=None):
        while True:
            self.tick()
            if stop_flag is not None and stop_flag.is_set():
                # QE nit (#152): let the current step finish -- a loop iteration is
                # ~70 s (clip + nrjxl) -- so it lands in the manifest with no
                # .h264.part / nrjxl work dir left behind. A cut is logged loudly.
                log(f"SIGTERM: phase={self.phase}: finishing the current step "
                    f"(up to {SIGTERM_JOIN_S} s), then exiting")
                self.stop_evt.set()
                cut = False
                if self.worker and self.worker.is_alive():
                    self.worker.join(timeout=SIGTERM_JOIN_S)
                    cut = self.worker.is_alive()
                if cut:
                    log(f"[ERR] SIGTERM: the {self.phase} step did not finish in {SIGTERM_JOIN_S} s;"
                        " exiting anyway: partial files (.h264.part, .nrjxl_work_*) may remain "
                        "and that step is missing from the manifest")
                self.manifest.event("sigterm", phase=self.phase, step_cut=cut)
                while self.pump_console() or self.prio:
                    self.sleep_fn(self.args.preview_line_s)
                return 0
            if exit_when_done and self.idle_done():
                return 0
            if max_runtime_s is not None and self.clock() - self.t_start > max_runtime_s:
                log("[WARN] --max-runtime-s reached")
                self.stop_evt.set()
                if self.worker:
                    self.worker.join(timeout=30)
                return 3
            self.sleep_fn(0.005 if (self.prio or self.bulk) else 0.05)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def default_fake_script():
    """Compressed Mac smoke: preview, cal, a mark, a replayed id, loop, resend."""
    return [
        (0.5, {"id": 90001, "c": "pool", "v": "status"}),
        (1.0, {"id": 90002, "c": "pool", "v": "preview"}),
        (3.0, {"id": 90003, "c": "pool", "v": "cal", "duration_s": 12, "interval_s": 3}),
        (4.0, {"id": 90004, "c": "pool", "v": "mark", "note": "card_1m"}),
        (5.0, {"id": 90003, "c": "pool", "v": "cal", "duration_s": 12, "interval_s": 3}),
        (6.0, {"id": 90005, "c": "pool", "v": "loop"}),
        (7.0, {"id": 90006, "c": "get", "k": ["camera.focus.mode"]}),
        (8.0, {"id": 90007, "c": "pool", "v": "preview", "lines": "2-3,5"}),
        (17.0, {"id": 90008, "c": "pool", "v": "loop", "iterations": 2, "period_s": 30}),
        (18.0, {"id": 90008, "c": "pool", "v": "loop", "iterations": 2, "period_s": 30}),
        (20.0, {"id": 90009, "c": "pool", "v": "mark", "note": "card_2m"}),
        (21.0, {"id": 90010, "c": "pool", "v": "status"}),
        (22.0, {"id": 90011, "c": "pool", "v": "preview"}),
    ]


def other_rc_processes():
    try:
        out = subprocess.run(["pgrep", "-af", "rc_progressive_jpeg.py|rc_run_capture_cycle.sh"],
                             capture_output=True, text=True, timeout=5).stdout
    except Exception:
        return []
    me = str(os.getpid())
    return [ln for ln in out.splitlines() if ln and not ln.startswith(me + " ")
            and "pgrep" not in ln]


def build_parser():
    p = argparse.ArgumentParser(description="Pool test sequence (see module doc).")
    p.add_argument("--run-root", default=DEFAULT_RUN_ROOT)
    p.add_argument("--config-dir", default=DEFAULT_CONFIG_DIR,
                   help="where camera_config.yaml / camera_schedule.yaml live (read-only)")
    p.add_argument("--print-config", action="store_true",
                   help="resolve the effective config, print its hash + camera settings, exit")
    p.add_argument("--cal-duration-s", type=int, default=DEFAULT_CAL_DURATION_S)
    p.add_argument("--cal-interval-s", type=int, default=DEFAULT_CAL_INTERVAL_S)
    p.add_argument("--loop-iterations", type=int, default=DEFAULT_LOOP_ITERATIONS)
    p.add_argument("--loop-period-s", type=int, default=DEFAULT_LOOP_PERIOD_S)
    p.add_argument("--loop-autostart-min", type=float, default=DEFAULT_AUTOSTART_MIN,
                   help="start the loop this long after calibration ends if no loop command "
                        "came (0 = never)")
    p.add_argument("--loop-keep-dng", dest="loop_keep_dng", action="store_true", default=True,
                   help="keep each loop still's DNG (default; the nrjxl reference)")
    p.add_argument("--no-loop-keep-dng", dest="loop_keep_dng", action="store_false")
    p.add_argument("--nrjxl-layout", default="rgb", choices=("rgb", "bayer4", "effective"),
                   help="rgb = B3a (pool spec); effective = whatever the unit's config says")
    p.add_argument("--video-s", type=float, default=DEFAULT_VIDEO_S)
    p.add_argument("--video-fps", type=int, default=DEFAULT_VIDEO_FPS)
    p.add_argument("--video-preset", default=DEFAULT_VIDEO_PRESET)
    p.add_argument("--video-bitrate-mbps", type=float, default=DEFAULT_VIDEO_BITRATE_MBPS)
    p.add_argument("--min-free-mb", type=int, default=DEFAULT_MIN_FREE_MB,
                   help="kept free after a phase's estimate; a phase stops below it")
    p.add_argument("--preview-w", type=int, default=rc_preview.DEFAULT_PREVIEW_WH[0])
    p.add_argument("--preview-h", type=int, default=rc_preview.DEFAULT_PREVIEW_WH[1])
    p.add_argument("--preview-q", type=int, default=rc_preview.DEFAULT_QUALITY)
    p.add_argument("--preview-max-bytes", type=int, default=rc_preview.DEFAULT_MAX_BYTES)
    p.add_argument("--preview-chunk", type=int, default=rc_preview.DEFAULT_CHUNK_CHARS)
    p.add_argument("--preview-line-s", type=float, default=rc_preview.DEFAULT_LINE_S)
    p.add_argument("--fake", action="store_true", help="fake camera + scripted link (Mac smoke)")
    p.add_argument("--fake-script", default=None, help="JSON [[t_s, {cmd}], ...] for --fake")
    p.add_argument("--exit-when-done", action="store_true",
                   help="exit after the loop finishes (smoke only; the Pi stays up by default)")
    p.add_argument("--max-runtime-s", type=float, default=None)
    p.add_argument("--force", action="store_true",
                   help="start even if an RC cycle process is running (it may own the UART)")
    p.add_argument("--lens-position", type=lens_arg, default=None,
                   help=f"manual focus (dioptres, {LENS_MIN}..{LENS_MAX}) for EVERY capture "
                        "(cal, loop still + clip, preview); default = the effective config")
    p.add_argument("--args-file", default=os.environ.get("POOL_ARGS_FILE", DEFAULT_ARGS_FILE),
                   help="extra args read first (CLI wins); written by pool_mode.sh args")
    p.add_argument("--check-args", action="store_true",
                   help="validate the args (incl. the args file), print them, exit 0/2")
    return p


def resolve_argv(argv):
    """-> (args, file_argv, file_error). The args file's tokens go FIRST so the
    command line wins. A bad args file never stops the pool: it is ignored and
    reported loudly (console + manifest), the command line still applies."""
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    pre, _ = parser.parse_known_args(argv)
    file_argv, file_error = [], None
    path = pre.args_file
    if path and os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                file_argv = shlex.split(fh.read(), comments=True)
            parse_or_raise(file_argv)
        except Exception as exc:
            file_error = f"{path}: {exc}"
            file_argv = []
    return parser.parse_args(file_argv + argv), file_argv, file_error


def parse_or_raise(argv):
    """parse_args that raises ValueError instead of exiting."""
    class _P(argparse.ArgumentParser):
        def error(self, message):
            raise ValueError(message)
    p = build_parser()
    p.__class__ = _P
    return p.parse_args(argv)


def make_run_dir(run_root, host):
    os.makedirs(run_root, exist_ok=True)
    name = f"{utc_now().strftime('%Y%m%d_%H%M%S')}_{host}"
    path = os.path.join(run_root, name)
    n = 1
    while os.path.exists(path):
        n += 1
        path = os.path.join(run_root, f"{name}_{n}")
    os.makedirs(path)
    return path


def apply_lens(cfg, lens_position):
    """--lens-position over the effective config (in place). -> (lens, source)."""
    if lens_position is not None:
        cfg["capture_settings"] = with_lens(cfg.get("capture_settings"), lens_position)
        return lens_of(cfg["capture_settings"]), "cli --lens-position"
    return lens_of(cfg.get("capture_settings")), "effective config"


def main(argv=None, link=None, camera=None, nrjxl_fn=None, host=None):
    args, file_argv, file_error = resolve_argv(argv)
    args.preview_wh = (args.preview_w, args.preview_h)
    if args.check_args:
        print(json.dumps({"args_file": args.args_file, "args_file_argv": file_argv,
                          "args_file_error": file_error, "lens_position": args.lens_position},
                         sort_keys=True))
        if file_error:
            print(f"ARGS_FILE_REJECTED {file_error}")
            return 2
        print("ARGS_OK")
        return 0
    from rc_telemetry import get_hostname
    host = host or get_hostname()
    if args.print_config:
        import tempfile
        tmp = tempfile.mkdtemp(prefix="pool_cfg_")
        cfg = load_effective(args.config_dir, tmp)
        lens, lens_source = apply_lens(cfg, args.lens_position)
        print(f"POOL_LENS_POSITION={fmt_lens(lens)} ({lens_source})")
        for line in cfg["lines"]:
            print(line)
        print(json.dumps({"hash": cfg["hash"], "level": cfg["level"], "overlay": cfg["overlay"],
                          "capture_settings": cfg["capture_settings"],
                          "crop_native_xywh": cfg["settings"]["crop_native_xywh"],
                          "raw_layout": cfg["raw_cfg"].get("layout"),
                          "still_format": cfg["raw_cfg"].get("format")}, sort_keys=True,
                         default=str))
        print(f"POOL_CONFIG_HASH={cfg['hash']}")
        shutil.rmtree(tmp, ignore_errors=True)
        return 0

    run_dir = make_run_dir(args.run_root, host)
    sys.stdout = Tee(sys.stdout, os.path.join(run_dir, "pool.log"))
    log(f"pool sequence start host={host} run={run_dir} pid={os.getpid()} fake={args.fake}")
    if not args.fake and not args.force:
        others = other_rc_processes()
        if others:
            log("[ERR] an RC cycle is running (it owns the camera/UART); reboot with "
                "pool_mode.sh on, or --force:\n  " + "\n  ".join(others))
            return 3

    if args.fake:
        settings = {"crop_native_xywh": (1504, 846, 1600, 900), "output_width": 1600,
                    "quality_ladder": [85, 80, 75, 70, 65, 60, 55, 50, 45, 40, 35, 30],
                    "message_cap": 195, "pacing_chunk_b64_chars": 384,
                    "pacing_delay_seconds": 1.3}
        cfg = {"hash": "fake0000", "level": "fake", "overlay": {}, "lines": [],
               "settings": settings, "capture_settings": None,
               "raw_cfg": {"layout": "rgb", "format": "nrjxl"}, "render": None}
    else:
        cfg = load_effective(args.config_dir, run_dir)
    for line in cfg["lines"]:
        log(line)
    if file_argv:
        log(f"args file {args.args_file}: {' '.join(file_argv)}")
    if file_error:
        log(f"[ERR] ARGS FILE REJECTED (ignored; command-line args still apply): {file_error}")
    lens, lens_source = apply_lens(cfg, args.lens_position)
    cfg["lens"] = {"value": lens, "source": lens_source}
    log(f"LENS: every capture requests lens_position={fmt_lens(lens)} ({lens_source})")
    if link is None:
        if args.fake:
            script = default_fake_script()
            if args.fake_script:
                with open(args.fake_script) as fh:
                    script = [tuple(x) for x in json.load(fh)]
            link = FakeLink(script)
        else:
            link = SerialLink(cfg["render"])
    # Subscribe BEFORE the slower camera setup: the mote replays held commands
    # ~60 s after the Pi boots; payloads queue in the reader until the loop runs.
    link.start()
    if args.nrjxl_layout != "effective" and cfg["raw_cfg"].get("layout") != args.nrjxl_layout:
        log(f"[WARN] nrjxl layout: effective config says {cfg['raw_cfg'].get('layout')!r}; "
            f"pool forces {args.nrjxl_layout!r} (--nrjxl-layout)")
        cfg["raw_cfg"] = dict(cfg["raw_cfg"], layout=args.nrjxl_layout)
    if camera is None:
        camera = FakeCamera() if args.fake else RealCamera(
            cfg, args.video_s, args.video_fps, args.video_preset, args.video_bitrate_mbps)
    if isinstance(camera, FakeCamera):
        camera.lens = lens
    if nrjxl_fn is None:
        nrjxl_fn = fake_nrjxl if args.fake else real_nrjxl
    free0 = free_mb(run_dir)
    manifest = Manifest(os.path.join(run_dir, "run_manifest.json"), {
        "schema": SCHEMA, "host": host, "run_dir": run_dir, "started_utc": iso(),
        "clock_note": "Pi clock (no Spotter time read); t_run_s/monotonic timings are exact",
        "argv": sys.argv if argv is None else argv, "args": {k: v for k, v in vars(args).items()},
        "software_sha": _software_sha(), "fake": args.fake,
        "config": {k: cfg.get(k) for k in ("hash", "base_hash", "level", "overlay", "dropped",
                                           "state_path", "render", "effective_values")},
        "capture_settings": cfg.get("capture_settings"),
        "lens": cfg.get("lens"), "args_file": args.args_file, "args_file_argv": file_argv,
        "args_file_error": file_error,
        "still": {"crop_native_xywh": cfg["settings"]["crop_native_xywh"],
                  "output_width": cfg["settings"]["output_width"],
                  "quality_ladder": cfg["settings"]["quality_ladder"],
                  "message_cap": cfg["settings"]["message_cap"], "raw_cfg": cfg["raw_cfg"]},
        "camera": camera.describe(),
        "storage": {"free_mb_at_start": free0, "min_free_mb": args.min_free_mb,
                    "est_dng_b": EST_DNG_B, "est_native_jpg_b_assumed": EST_NATIVE_JPG_B},
        "events": [], "commands": [], "marks": [], "calibration": [], "loop": [], "previews": [],
        "sweeps": []})
    full_need = (args.cal_duration_s // args.cal_interval_s) * (EST_DNG_B + EST_NATIVE_JPG_B
                                                                 + EST_SMALL_B)
    ctl = PoolController(args, link, camera, cfg, run_dir, manifest, host, nrjxl_fn=nrjxl_fn)
    full_need += ctl.loop_estimate_b(args.loop_iterations)
    full_mb = int(full_need / 1048576) + args.min_free_mb
    log(f"storage: free={free0} MB; full session needs ~{full_mb} MB "
        f"(cal {args.cal_duration_s}s/{args.cal_interval_s}s + loop {args.loop_iterations} + "
        f"{args.min_free_mb} MB margin) -> {'OK' if free0 >= full_mb else 'SHORT: phases may refuse'}")
    log(f"config hash={cfg['hash']} level={cfg['level']} overlay={sorted(cfg['overlay'])}")
    log(f"camera: {json.dumps(camera.describe(), default=str)}")

    stop_flag = threading.Event()
    try:
        signal.signal(signal.SIGTERM, lambda *_: stop_flag.set())
    except ValueError:
        pass                       # not the main thread (tests)
    if file_error:
        ctl.say(f"pool ARGS FILE REJECTED (ignored) {file_error[:150]}")
    ctl.say(f"pool ready run={os.path.basename(run_dir)} cfg={cfg['hash']} lp={fmt_lens(lens)} free={free0}MB "
            f"need~{full_mb}MB verbs={'|'.join(VERBS)}")
    try:
        rc = ctl.run(exit_when_done=args.exit_when_done, max_runtime_s=args.max_runtime_s,
                     stop_flag=stop_flag)
    finally:
        manifest.set("ended_utc", iso())
        manifest.set("final_status", ctl.status_text())
        link.close()
    log(f"pool sequence exit rc={rc}: {ctl.status_text()}")
    return rc


def _software_sha():
    try:
        from rc_telemetry import get_software_sha
        return get_software_sha()
    except Exception:
        return None


if __name__ == "__main__":
    sys.exit(main())

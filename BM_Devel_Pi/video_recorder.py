#!/usr/bin/env python3
# filename: video_recorder.py
# description: Sprint15 video mode — config island, geometry, per-clip recorder loop.
"""
Sprint15 video mode (capture_mode: video) — D-S15-1..4.

This module owns:
  - the `video:` YAML island (loader + loud validation; defaults are the
    SPEC-locked values, so a missing island resolves to the shipped config)
  - geometry DERIVED from the stills keys (SPEC constraint 4): the
    progressive_jpeg.crop native-coordinate box -> libcamera --roi
    fractions, and the stills output size -> --width/--height. There are
    NO video geometry keys.
  - the libcamera-vid/rpicam-vid argv builder (camera controls ride the
    SAME builder the stills path uses: _camera_controls_from_settings)
  - run_video_mode(): the per-clip record loop (D-S15-2), filled in by
    tracker chunk 2.

Coordinate systems (manifesto rule 12):
  - progressive_jpeg.crop x/y/w/h: NATIVE 4608x2592 sensor-equivalent px
  - libcamera --roi x,y,w,h: fractions of the full sensor field (0..1)
  - --width/--height: encoded OUTPUT pixels (H.264 requires even values)

Example (defaults): crop (1504, 846, 1600, 900) ->
  --roi 0.326389,0.326389,0.347222,0.347222  --width 1000 --height 562

Known limitations:
  - Encoder/muxer subprocesses are injected for tests; only the bench
    proves the real ones (SPEC gates 2-3).
"""

import copy
import os
import shutil
import subprocess
import time
from datetime import datetime, timezone

import video_ring
from process_image_v2 import _camera_controls_from_settings
from rc_power_halt import perform_power_halt

# Native sensor-equivalent frame every crop is expressed in (IMX708 full).
NATIVE_W, NATIVE_H = 4608, 2592

DEFAULT_VIDEO_DIR = "/home/pi/BM_Devel_Pi/videos"

# SPEC-locked defaults (Nick 2026-08-17). A missing/partial island resolves
# to exactly these, so `capture_mode: video` alone is a valid production
# switch (constraint 10).
DEFAULT_VIDEO_CONFIG = {
    "clip_minutes": 5.0,     # per-clip length (constraint 7); fractional OK for bench
    "fps": 15,
    "bitrate_mbps": 2.0,
    "session_minutes": 0,    # 0 = record until power loss; >0 = N min then normal halt
    "dir": DEFAULT_VIDEO_DIR,
    "storage": {
        "max_used_pct": 75.0,   # ring cap primary knob (D-S15-5)
        "min_free_gb": 10.0,    # absolute floor backstop; stricter wins
        "ring_dry_run": False,
    },
    "ui": {
        "enabled": True,
        "port": 8080,
    },
}


# ---------------------------------------------------------------------------
# video: island — loader + validation
# ---------------------------------------------------------------------------

def _strip_yaml_value(value):
    return value.strip().strip('"').strip("'")


def _parse_number(name, value, lo, hi, integer=False):
    """Strict numeric parse with a loud, named failure (SPEC: no silent
    fallback on nonsense values — a typo must fail at config time)."""
    try:
        num = float(value)
    except Exception:
        raise ValueError(f"video.{name} must be a number, got {value!r}")
    if integer:
        if num != int(num):
            raise ValueError(f"video.{name} must be an integer, got {value!r}")
        num = int(num)
    if not (lo <= num <= hi):
        raise ValueError(f"video.{name} must be in {lo}..{hi}, got {num}")
    return num


def _parse_strict_bool(name, value):
    text = str(value).strip().lower()
    if text in {"true", "yes", "1", "on"}:
        return True
    if text in {"false", "no", "0", "off"}:
        return False
    raise ValueError(f"video.{name} must be true or false, got {value!r}")


def validate_video_config(cfg):
    """Range-check a video config dict in place; raises ValueError, loudly
    named per key. Bounds are sanity guards, not tuning advice — the locked
    defaults sit well inside every range."""
    cfg["clip_minutes"] = _parse_number(
        "clip_minutes", cfg["clip_minutes"], 0.05, 60)
    cfg["fps"] = _parse_number("fps", cfg["fps"], 1, 30, integer=True)
    cfg["bitrate_mbps"] = _parse_number(
        "bitrate_mbps", cfg["bitrate_mbps"], 0.1, 25)
    cfg["session_minutes"] = _parse_number(
        "session_minutes", cfg["session_minutes"], 0, 1440, integer=True)
    if not str(cfg["dir"]).strip():
        raise ValueError("video.dir must not be empty")
    st = cfg["storage"]
    st["max_used_pct"] = _parse_number(
        "storage.max_used_pct", st["max_used_pct"], 10, 95)
    st["min_free_gb"] = _parse_number(
        "storage.min_free_gb", st["min_free_gb"], 0, 1000)
    st["ring_dry_run"] = _parse_strict_bool(
        "storage.ring_dry_run", st["ring_dry_run"])
    ui = cfg["ui"]
    ui["enabled"] = _parse_strict_bool("ui.enabled", ui["enabled"])
    ui["port"] = _parse_number("ui.port", ui["port"], 1, 65535, integer=True)
    return cfg


def load_video_config(config_path):
    """Parse the `video:` island from camera_schedule.yaml.

    Same tiny hand parser style as load_camera_schedule (no PyYAML on the
    field unit). Missing file or island -> SPEC defaults. Invalid values
    raise ValueError naming the exact video.<key>.
    """
    cfg = copy.deepcopy(DEFAULT_VIDEO_CONFIG)
    cfg["source"] = "defaults"
    if not config_path or not os.path.exists(config_path):
        return validate_video_config(cfg)

    in_video = False
    subsection = None
    saw_key = False

    with open(config_path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip("\n").rstrip()
            if not line.strip():
                continue
            indent = len(line) - len(line.lstrip(" \t"))
            stripped = line.strip()

            if stripped.endswith(":") and ":" not in stripped[:-1]:
                name = stripped[:-1].strip()
                if indent == 0:
                    in_video = (name == "video")
                    subsection = None
                elif in_video:
                    subsection = name
                continue

            if ":" not in stripped:
                continue
            if indent == 0:
                # Top-level key: value line ends any open section.
                in_video = False
                subsection = None
                continue
            if not in_video:
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_value(value)

            if indent <= 2:
                subsection = None
                if key == "clip_minutes":
                    cfg["clip_minutes"] = value
                elif key == "fps":
                    cfg["fps"] = value
                elif key == "bitrate_mbps":
                    cfg["bitrate_mbps"] = value
                elif key == "session_minutes":
                    cfg["session_minutes"] = value
                elif key == "dir":
                    cfg["dir"] = value
                else:
                    print(f"[VID][WARN] unknown key video.{key} ignored")
                    continue
                saw_key = True
                continue

            if subsection == "storage":
                if key in ("max_used_pct", "min_free_gb", "ring_dry_run"):
                    cfg["storage"][key] = value
                    saw_key = True
                else:
                    print(f"[VID][WARN] unknown key video.storage.{key} ignored")
                continue

            if subsection == "ui":
                if key in ("enabled", "port"):
                    cfg["ui"][key] = value
                    saw_key = True
                else:
                    print(f"[VID][WARN] unknown key video.ui.{key} ignored")
                continue

    if saw_key:
        cfg["source"] = "yaml"
    return validate_video_config(cfg)


def print_video_settings(vcfg):
    """One loud line per fact, print-config style."""
    print(f"[VID] video island (source={vcfg['source']}): "
          f"clip_minutes={vcfg['clip_minutes']} fps={vcfg['fps']} "
          f"bitrate_mbps={vcfg['bitrate_mbps']} "
          f"session_minutes={vcfg['session_minutes']}")
    st, ui = vcfg["storage"], vcfg["ui"]
    print(f"[VID] storage ring: max_used_pct={st['max_used_pct']} "
          f"min_free_gb={st['min_free_gb']} dry_run={st['ring_dry_run']} "
          f"dir={vcfg['dir']}")
    print(f"[VID] ui: enabled={ui['enabled']} port={ui['port']}")


# ---------------------------------------------------------------------------
# Geometry — derived from the stills keys (D-S15-3), never restated
# ---------------------------------------------------------------------------

def crop_xywh_to_roi(crop_xywh, native_wh=(NATIVE_W, NATIVE_H)):
    """NATIVE-coordinate crop box -> libcamera --roi normalized fractions.

    Input coords are native 4608x2592 sensor-equivalent pixels — the SAME
    progressive_jpeg.crop values the stills path crops with, so a YAML (or
    roi-command) crop change moves both paths together (constraint 4).
    """
    x, y, w, h = [int(v) for v in crop_xywh]
    nw, nh = int(native_wh[0]), int(native_wh[1])
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        raise ValueError(
            f"crop x/y must be >= 0 and w/h > 0, got {(x, y, w, h)}")
    if x + w > nw or y + h > nh:
        raise ValueError(
            f"crop {(x, y, w, h)} exceeds native frame {nw}x{nh}")
    return f"{x / nw:.6f},{y / nh:.6f},{w / nw:.6f},{h / nh:.6f}"


def even_video_output_size(output_size):
    """Round the stills output size DOWN to even dims (H.264 requirement).

    Returns ((w, h), adjusted). Today's 1000x562 passes through untouched;
    a crop/output combination that lands odd is corrected (never upscaled)
    with a loud note instead of failing the boot — a video unit must
    record, not brick, on a geometry nit (constraint 5 doctrine).
    """
    w, h = int(output_size[0]), int(output_size[1])
    w2, h2 = w - (w % 2), h - (h % 2)
    if w2 <= 0 or h2 <= 0:
        raise ValueError(f"output size {w}x{h} too small for H.264")
    adjusted = (w2, h2) != (w, h)
    if adjusted:
        print(f"[VID][WARN] output {w}x{h} -> {w2}x{h2} "
              f"(H.264 needs even dimensions; rounded down)")
    return (w2, h2), adjusted


def _select_video_command(capture_backend):
    """rpicam-vid/libcamera-vid resolution, mirroring the stills
    _select_camera_command backend semantics (bmcam000: libcamera-vid
    verified present, Sprint15 setup)."""
    backend = (capture_backend or "auto").strip().lower()
    if backend in {"auto", "rpicam"}:
        cmd = shutil.which("rpicam-vid")
        if cmd:
            return cmd, "rpicam"
    if backend in {"auto", "rpicam", "libcamera"}:
        cmd = shutil.which("libcamera-vid")
        if cmd:
            return cmd, "libcamera"
    raise RuntimeError(
        "No supported video command found. Expected rpicam-vid or "
        f"libcamera-vid for capture_backend={capture_backend!r}.")


def build_encoder_command(settings, vcfg, h264_path, *, binary=None,
                          controls=None):
    """argv for ONE clip's encoder process (D-S15-2).

    - geometry: settings['crop_native_xywh'] -> --roi, settings
      ['output_size'] -> --width/--height (evened). BOTH come from the
      stills keys via resolve_rc_settings — no video geometry exists.
    - camera controls: the SAME _camera_controls_from_settings builder the
      stills capture uses (focus/AWB/exposure parity, constraint 4).
    - --inline repeats SPS/PPS headers so the raw .h264.part muxes cleanly
      even when a clip is cut mid-stream.

    Returns (argv, requested_controls_metadata).
    """
    if binary is None:
        binary, _ = _select_video_command(settings["capture_backend"])
    duration_ms = int(round(float(vcfg["clip_minutes"]) * 60 * 1000))
    (w, h), _ = even_video_output_size(settings["output_size"])
    roi = crop_xywh_to_roi(settings["crop_native_xywh"])
    argv = [
        binary,
        "-n",                       # no preview (headless field unit)
        "-t", str(duration_ms),
        "--codec", "h264",
        "--inline",
        "--width", str(w),
        "--height", str(h),
        "--framerate", str(int(vcfg["fps"])),
        "--bitrate", str(int(round(float(vcfg["bitrate_mbps"]) * 1_000_000))),
        "--roi", roi,
        "-o", h264_path,
    ]
    controls_args, requested = _camera_controls_from_settings(
        {"camera_controls": controls} if controls else None)
    argv.extend(controls_args)
    return argv, requested


# ---------------------------------------------------------------------------
# Naming (D-S15-4) + crash-debris sweep (D-S15-2)
# ---------------------------------------------------------------------------

def clip_basename(now_utc, output_wh, fps):
    """`<UTC>_video_<WxH>_<fps>fps` — lexicographic order == chronological
    order, which is what the ring prunes by and the UI sorts by."""
    ts = now_utc.strftime("%Y-%m-%dT%H-%M-%SZ")
    return f"{ts}_video_{output_wh[0]}x{output_wh[1]}_{int(fps)}fps"


def sweep_boot_debris(video_dir, log_fn=print):
    """Delete orphaned .part/.tmp files at boot (crash debris, D-S15-2).

    A hard power cut mid-clip leaves an unambiguous suffix behind; this
    sweep is what keeps the crash contract's 'at most the in-flight clip'
    promise. Completed finals are never touched. Returns count removed.
    """
    removed = 0
    try:
        names = sorted(os.listdir(video_dir))
    except FileNotFoundError:
        return 0
    for name in names:
        if not (name.endswith(".part") or name.endswith(".tmp")):
            continue
        path = os.path.join(video_dir, name)
        if not os.path.isfile(path):
            continue
        try:
            size = os.path.getsize(path)
            os.remove(path)
            removed += 1
            log_fn(f"[VID] boot sweep: removed crash debris {name} ({size} B)")
        except OSError as exc:
            log_fn(f"[VID][WARN] boot sweep could not remove {name}: {exc}")
    if removed == 0:
        log_fn("[VID] boot sweep: no crash debris")
    return removed


# ---------------------------------------------------------------------------
# Clip pipeline (D-S15-2): record -> mux -> poster -> fsync -> atomic rename
# ---------------------------------------------------------------------------

# Watchdog margins over the nominal clip length — an encoder that overruns
# its own -t is wedged and gets killed so the NEXT clip can self-heal.
ENCODER_TIMEOUT_MARGIN_S = 60
MUX_TIMEOUT_S = 180          # -c copy of a ~75 MB clip on Pi Zero SD I/O
POSTER_TIMEOUT_S = 60
PAUSE_RECHECK_S = 60         # ring-paused poll interval
FAILED_CLIP_RETRY_S = 10     # breather after a failed clip (no tight spin
                             # while e.g. another process holds the camera)


def _default_run(argv, timeout_s):
    """Run one pipeline subprocess with a hard timeout.

    Returns (returncode, duration_s); -1 return code on timeout (process
    killed). Injected/faked by every unit test — only the bench runs this.
    """
    started = time.monotonic()
    try:
        result = subprocess.run(
            argv, timeout=timeout_s, check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        duration = time.monotonic() - started
        if result.returncode != 0:
            tail = (result.stderr or "").strip().splitlines()[-3:]
            print(f"[VID][ERROR] {os.path.basename(argv[0])} rc="
                  f"{result.returncode}: {' | '.join(tail)}")
        return result.returncode, duration
    except subprocess.TimeoutExpired:
        print(f"[VID][ERROR] {os.path.basename(argv[0])} TIMEOUT after "
              f"{timeout_s:.0f}s (killed)")
        return -1, time.monotonic() - started


def _fsync_path(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _remove_quiet(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def build_mux_command(ffmpeg_binary, fps, part_path, mp4_tmp_path):
    """ffmpeg stream-copy mux: raw .h264.part -> .mp4.tmp. No re-encode,
    no faststart rewrite (a second full-file pass would stretch the
    clip-boundary gap; Safari scrubs via Range requests, D-S15-9)."""
    return [
        ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y",
        "-framerate", str(int(fps)),
        "-i", part_path,
        "-c", "copy",
        mp4_tmp_path,
    ]


def build_poster_command(ffmpeg_binary, mp4_tmp_path, thumb_tmp_path):
    """First-frame poster JPEG (the GoPro-style gallery tile, D-S15-9)."""
    return [
        ffmpeg_binary, "-hide_banner", "-loglevel", "error", "-y",
        "-i", mp4_tmp_path,
        "-frames:v", "1", "-q:v", "4",
        thumb_tmp_path,
    ]


def record_one_clip(settings, vcfg, video_dir, *, encoder_binary,
                    ffmpeg_binary, controls=None, run_fn=_default_run,
                    now_fn=lambda: datetime.now(timezone.utc)):
    """One clip through the full crash-safe pipeline (D-S15-2).

    record .h264.part -> mux .mp4.tmp -> poster _thumb.jpg.tmp -> fsync
    -> atomic rename to finals -> delete the .part. Any failure cleans
    its debris and returns ok=False — the caller starts the next clip
    (per-clip processes self-heal at the boundary; nothing retries
    in-place).

    Returns a dict: ok, stage, basename, mp4/thumb paths, bytes,
    encode_s, boundary_s, requested camera-control metadata.
    """
    (w, h), _ = even_video_output_size(settings["output_size"])
    base = clip_basename(now_fn(), (w, h), vcfg["fps"])
    part = os.path.join(video_dir, base + ".h264.part")
    mp4_tmp = os.path.join(video_dir, base + ".mp4.tmp")
    mp4 = os.path.join(video_dir, base + ".mp4")
    thumb_tmp = os.path.join(video_dir, base + "_thumb.jpg.tmp")
    thumb = os.path.join(video_dir, base + "_thumb.jpg")

    result = {"ok": False, "stage": "encode", "basename": base,
              "mp4": None, "thumb": None, "bytes": 0,
              "encode_s": 0.0, "boundary_s": 0.0}

    argv, requested = build_encoder_command(
        settings, vcfg, part, binary=encoder_binary, controls=controls)
    result["requested_controls"] = requested
    clip_s = float(vcfg["clip_minutes"]) * 60.0
    print(f"[VID] clip start: {base} ({clip_s:.0f}s nominal, "
          f"{w}x{h}@{vcfg['fps']}fps {vcfg['bitrate_mbps']}Mbps)")

    rc_enc, encode_s = run_fn(argv, clip_s + ENCODER_TIMEOUT_MARGIN_S)
    result["encode_s"] = encode_s
    part_bytes = os.path.getsize(part) if os.path.exists(part) else 0
    if rc_enc != 0 or part_bytes <= 0:
        print(f"[VID][ERROR] encode failed for {base} "
              f"(rc={rc_enc}, part_bytes={part_bytes}); clip dropped")
        _remove_quiet(part)
        return result

    boundary_started = time.monotonic()
    result["stage"] = "mux"
    rc_mux, _ = run_fn(
        build_mux_command(ffmpeg_binary, vcfg["fps"], part, mp4_tmp),
        MUX_TIMEOUT_S)
    mp4_bytes = os.path.getsize(mp4_tmp) if os.path.exists(mp4_tmp) else 0
    if rc_mux != 0 or mp4_bytes <= 0:
        print(f"[VID][ERROR] mux failed for {base} "
              f"(rc={rc_mux}, mp4_bytes={mp4_bytes}); clip dropped")
        _remove_quiet(part)
        _remove_quiet(mp4_tmp)
        return result

    # Poster failure is non-fatal: a clip without a gallery tile beats no
    # clip. The UI falls back to a nameplate for missing thumbs.
    result["stage"] = "poster"
    rc_thumb, _ = run_fn(
        build_poster_command(ffmpeg_binary, mp4_tmp, thumb_tmp),
        POSTER_TIMEOUT_S)
    have_thumb = rc_thumb == 0 and os.path.exists(thumb_tmp) \
        and os.path.getsize(thumb_tmp) > 0
    if not have_thumb:
        print(f"[VID][WARN] poster extraction failed for {base} "
              "(clip kept without thumb)")
        _remove_quiet(thumb_tmp)

    # Crash contract: fsync BEFORE the atomic rename — after this block a
    # hard power cut can only ever cost the NEXT (in-flight) clip.
    result["stage"] = "finalize"
    _fsync_path(mp4_tmp)
    os.rename(mp4_tmp, mp4)
    if have_thumb:
        _fsync_path(thumb_tmp)
        os.rename(thumb_tmp, thumb)
    _remove_quiet(part)          # raw stream is a duplicate of the mp4
    _fsync_path(video_dir)       # persist the renames themselves

    result.update({
        "ok": True, "stage": "done", "mp4": mp4,
        "thumb": thumb if have_thumb else None, "bytes": mp4_bytes,
        "boundary_s": time.monotonic() - boundary_started,
    })
    print(f"[VID] clip done: {os.path.basename(mp4)} ({mp4_bytes} B, "
          f"encode={encode_s:.1f}s boundary={result['boundary_s']:.1f}s"
          f"{'' if have_thumb else ', NO THUMB'})")
    return result


# ---------------------------------------------------------------------------
# Clip loop (D-S15-2 / D-S15-8)
# ---------------------------------------------------------------------------

def _resolve_controls(settings):
    """Same controls resolution as the stills capture (_default_capture):
    command overlay wins, else the YAML island. Lazy import breaks the
    rc_progressive_jpeg <-> video_recorder cycle."""
    controls = settings.get("camera_controls_override")
    if controls is None:
        import rc_progressive_jpeg as rc
        controls = rc._load_camera_controls_island(settings["config_path"])
    return controls or None


def run_video_mode(settings, *, transmit=False, bm_commands_cfg=None,
                   command_state=None, bench_commands=False,
                   max_clips=None, run_fn=_default_run,
                   encoder_binary=None, ffmpeg_binary=None,
                   now_fn=lambda: datetime.now(timezone.utc),
                   clock=time.monotonic, sleep_fn=time.sleep,
                   halt_fn=perform_power_halt,
                   on_clip_fn=None, on_pause_fn=None):
    """The video runtime (D-S15-1/2/8): sweep debris, then loop
    ring-check -> record clip -> boundary work until the session ends
    (session_minutes > 0), max_clips is reached (bench), or power dies.

    on_clip_fn(clip_result, ring_result): chunk-3 boundary hook (sidecar,
    manifest, status message, daemon drain). on_pause_fn(ring_result):
    chunk-3 pause telemetry. Both optional and never allowed to kill the
    loop — recording is the mission.

    Returns 0 (the loop only ends by design; per-clip failures self-heal).
    """
    vcfg = settings["video"]
    video_dir = vcfg["dir"]
    os.makedirs(video_dir, exist_ok=True)

    if encoder_binary is None:
        encoder_binary, backend = _select_video_command(
            settings["capture_backend"])
        print(f"[VID] encoder: {encoder_binary} ({backend})")
    if ffmpeg_binary is None:
        ffmpeg_binary = shutil.which("ffmpeg")
        if not ffmpeg_binary:
            raise RuntimeError("ffmpeg not found; video mode cannot mux")

    controls = _resolve_controls(settings)
    sweep_boot_debris(video_dir)

    session_s = float(vcfg["session_minutes"]) * 60.0
    session_deadline = clock() + session_s if session_s > 0 else None
    print(f"[VID] session: "
          f"{'continuous (until power loss)' if session_deadline is None else f'{session_s:.0f}s then halt'}"
          f"{f', max_clips={max_clips}' if max_clips else ''} dir={video_dir}")

    clips_done = 0
    clips_failed = 0
    session_expired = False
    try:
        while True:
            if session_deadline is not None and clock() >= session_deadline:
                session_expired = True
                print(f"[VID] session_minutes reached "
                      f"({clips_done} clips); normal halt path")
                break
            # max_clips bounds ATTEMPTS (bench/test bail-out): a
            # persistently failing encoder must end a bounded bench run,
            # not spin it forever. Production (max_clips=None) retries
            # indefinitely — self-heal at the boundary is the design.
            if max_clips is not None and (clips_done + clips_failed) >= max_clips:
                print(f"[VID] max_clips={max_clips} attempts reached; stopping")
                break

            ring_result = video_ring.ensure_room(
                video_dir, vcfg["storage"])
            if ring_result["paused"]:
                if on_pause_fn is not None:
                    try:
                        on_pause_fn(ring_result)
                    except Exception as exc:
                        print(f"[VID][WARN] pause hook failed: {exc}")
                print(f"[VID][PAUSE] storage over limit "
                      f"(used={ring_result['used_pct']}% "
                      f"free={ring_result['free_gb']}GiB); recheck in "
                      f"{PAUSE_RECHECK_S}s")
                sleep_fn(PAUSE_RECHECK_S)
                continue

            clip_result = record_one_clip(
                settings, vcfg, video_dir,
                encoder_binary=encoder_binary, ffmpeg_binary=ffmpeg_binary,
                controls=controls, run_fn=run_fn, now_fn=now_fn)
            if clip_result["ok"]:
                clips_done += 1
            else:
                clips_failed += 1
                print(f"[VID] failed clip; retrying in "
                      f"{FAILED_CLIP_RETRY_S}s")
                sleep_fn(FAILED_CLIP_RETRY_S)
            if on_clip_fn is not None:
                try:
                    on_clip_fn(clip_result, ring_result)
                except Exception as exc:
                    print(f"[VID][WARN] clip boundary hook failed: {exc}")
    finally:
        print(f"[VID] loop end: {clips_done} clips ok, "
              f"{clips_failed} failed")
        if session_expired:
            # D-S15-8: the OPTIONAL Pi-side duty-cycle lever reuses the
            # normal cycle-end halt machinery untouched.
            halt_result = halt_fn(
                enabled=settings["power_halt_enabled"],
                dry_run=settings["power_halt_dry_run"],
                mode=settings["power_halt_mode"],
                script_path=settings["power_halt_script_path"],
            )
            print(f"[VID] halt: {halt_result['action']}")
    return 0

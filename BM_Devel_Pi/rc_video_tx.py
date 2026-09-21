#!/usr/bin/env python3
# filename: rc_video_tx.py
# description: Sprint22 — one boot = record a short clip, fit it to the message budget, send it, halt.
"""
Sprint22 Phase 2 — the "short video over Spotter" cycle.

Selected by `capture_mode: "video"` PLUS `video_tx.enabled: true`. With the
island absent or disabled (the default) a video unit is the Sprint15 continuous
recorder exactly as before — this module is never imported.

One cycle, same shape as the stills cycle (boot -> work -> halt):

  Spotter time  -> the Pi has no RTC; filenames and lane timing need real UTC
  record        -> video_recorder.record_one_clip, the SAME crash-safe pipeline
                   and geometry as the recorder; the full-quality clip stays on
                   the SD card (field debugging) and the ring still applies
  fit           -> rc_video_clip: last `duration_s`, scaled to video_tx.output,
                   2-pass x264 into the message budget
  lane wait     -> rc_transmit_phase, when its island is enabled: keep the
                   burst off the 5-minute cellular blackout boundaries
  send          -> rc_transmit.transmit_video_clip, CELLULAR-ONLY enforced here
  halt          -> rc_power_halt, in `finally`, on every path

The message budget for a clip = min(video_tx.message_cap, what the cycle's time
budget can still pace) — the same "how many messages can I afford" idea as the
progressive JPEG, solved with arithmetic instead of a quality ladder.

NOT in this module (v1, stated so nobody assumes it): the BM command daemon
(no remote commands are picked up during a video_tx cycle) and scheduled
transmit windows beyond the existing gate.

`video_tx:` island (camera_schedule.yaml), every key optional:

  video_tx:
    enabled: false       # true = this unit sends one clip per boot
    duration_s: 5        # seconds of video SENT (the tail of the recording)
    lead_in_s: 2         # extra seconds recorded first: AE/AWB settle, discarded
    output: "480x270"    # send geometry; never above the recording's
    fps: 10
    message_cap: 126     # hard ceiling on chunk messages per clip
    preset: "medium"     # x264 preset (bmcam004 2026-09-21: best SSIM, same time)
"""

import os
import shutil
import time
from datetime import datetime, timezone

import rc_transmit_phase
import rc_video_clip
import video_recorder
import video_ring
from rc_power_halt import perform_power_halt
from rc_time_budget import CycleBudget
from rc_transmit import VIDEO_OVERHEAD_MSGS, transmit_video_clip
from rc_uplink_messages import format_crop

DEFAULT_VIDEO_TX_CONFIG = {
    "enabled": False,
    "duration_s": 5.0,
    "lead_in_s": 2.0,
    "output": "480x270",
    "fps": 10,
    "message_cap": 126,
    "preset": "medium",
}
X264_PRESETS = ("ultrafast", "superfast", "veryfast", "faster", "fast", "medium")
RAW_BYTES_PER_B64_CHAR = 0.75
WORK_DIR = "/tmp/rc_video_tx"          # tmpfs on the unit; never the SD card


def _strip(value):
    return value.split("#", 1)[0].strip().strip('"').strip("'")


def load_video_tx_config(config_path):
    """Parse the `video_tx:` island. Missing file/island -> defaults (disabled).
    Invalid values raise ValueError naming the exact video_tx.<key>."""
    cfg = dict(DEFAULT_VIDEO_TX_CONFIG)
    cfg["source"] = "defaults"
    if config_path and os.path.exists(config_path):
        in_island = False
        with open(config_path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                indent = len(line) - len(line.lstrip(" \t"))
                if indent == 0:
                    in_island = line.strip() == "video_tx:"
                    continue
                if not in_island or ":" not in line:
                    continue
                key, value = line.strip().split(":", 1)
                key = key.strip()
                if key in DEFAULT_VIDEO_TX_CONFIG:
                    cfg[key] = _strip(value)
                    cfg["source"] = "yaml"
                else:
                    raise ValueError(f"video_tx.{key} is not a known key "
                                     f"(known: {', '.join(DEFAULT_VIDEO_TX_CONFIG)})")
    return validate_video_tx_config(cfg)


def validate_video_tx_config(cfg):
    enabled = cfg["enabled"]
    if isinstance(enabled, str):
        if enabled.lower() not in ("true", "false"):
            raise ValueError(f"video_tx.enabled must be true or false, got {enabled!r}")
        enabled = enabled.lower() == "true"
    cfg["enabled"] = bool(enabled)

    def number(key, lo, hi, integer=False):
        try:
            value = float(cfg[key])
        except (TypeError, ValueError):
            raise ValueError(f"video_tx.{key} must be a number, got {cfg[key]!r}")
        if not lo <= value <= hi:
            raise ValueError(f"video_tx.{key}={value:g} outside {lo:g}..{hi:g}")
        cfg[key] = int(value) if integer else value

    number("duration_s", 1, 30)
    number("lead_in_s", 0, 10)
    number("fps", 1, 30, integer=True)
    number("message_cap", 8, 1000, integer=True)
    try:
        w, h = (int(v) for v in str(cfg["output"]).lower().split("x"))
        if w < 16 or h < 16 or w % 2 or h % 2:
            raise ValueError
    except ValueError:
        raise ValueError(f"video_tx.output must be even 'WxH', got {cfg['output']!r}")
    cfg["output_wh"] = (w, h)
    if cfg["preset"] not in X264_PRESETS:
        raise ValueError(f"video_tx.preset must be one of {X264_PRESETS}, got {cfg['preset']!r}")
    return cfg


def print_video_tx_settings(cfg):
    w, h = cfg["output_wh"]
    print(f"[VTX] video_tx: enabled={cfg['enabled']} send {cfg['duration_s']:g}s "
          f"(+{cfg['lead_in_s']:g}s lead-in) {w}x{h}@{cfg['fps']}fps cap={cfg['message_cap']} msgs "
          f"preset={cfg['preset']} source={cfg['source']}")


def _default_tx_open(config_path):
    """The production UART, with the network type FORCED to cellular-only on
    every message. The bare BristlemouthSerial default is 0x01 = cellular with
    IRIDIUM fallback; a clip must never fall back to satellite, whatever the
    YAML says."""
    from process_image_v2 import _get_bm_serial, apply_bm_serial_runtime_settings
    apply_bm_serial_runtime_settings(configure_serial=True)
    bm = _get_bm_serial()
    return lambda data: bm.spotter_tx(data, network_type="cellular_only")


def _default_gate(config_path):
    from spotter_time_sync import should_transmit_now_from_schedule
    return should_transmit_now_from_schedule(config_path)


def _default_close():
    from process_image_v2 import close_bm_serial
    close_bm_serial()


def _cpu_temp_text():
    try:
        from process_image_v2 import get_cpu_temperature
        return f"{float(get_cpu_temperature()):.1f}"
    except Exception:
        return "na"


def _start_metadata(settings):
    try:
        from process_image_v2 import get_hostname, get_software_sha
        return {"timezone": settings.get("timezone"), "software_sha": get_software_sha(),
                "hostname": get_hostname()}
    except Exception:
        return {"timezone": settings.get("timezone")}


def run_video_tx_cycle(settings, vtx, *, transmit=False, skip_time_window=False,
                       gate_fn=_default_gate, record_fn=video_recorder.record_one_clip,
                       fit_fn=rc_video_clip.fit_clip_to_budget, tx_open_fn=_default_tx_open,
                       bm_close_fn=_default_close, halt_fn=perform_power_halt,
                       ensure_room_fn=video_ring.ensure_room, sleep_fn=time.sleep,
                       clock=time.monotonic, now_fn=lambda: datetime.now(timezone.utc),
                       encoder_binary=None, ffmpeg_binary=None):
    """Run one record -> fit -> send cycle. Returns a summary dict; the halt
    runs in `finally` on every path and never raises."""
    vcfg = settings["video"]
    summary = {"transmit": transmit, "clip": None, "fit": None, "transmit_result": None,
               "schedule_allowed": True, "stage": "start", "error": None, "halt_result": None}
    budget = CycleBudget(settings["budget_seconds"], settings["pacing_delay_seconds"], clock=clock)
    print(f"[VTX] cycle start: budget={settings['budget_seconds']}s "
          f"pacing={settings['pacing_delay_seconds']}s/msg transmit={transmit}")
    opened = False
    try:
        # 1. Spotter time first: no RTC, so until this read the clock can be
        #    years off — and the clip's filename is its capture time.
        gate_info, gate_mono = None, clock()
        if transmit:
            summary["stage"] = "time_gate"
            opened = True                       # the gate opens the UART
            allowed, gate_info = gate_fn(settings["config_path"])
            gate_mono = clock()
            print(f"[VTX] schedule gate: {gate_info.get('reason')}")
            if settings.get("enforce_time_window") and not skip_time_window and not allowed:
                summary["schedule_allowed"] = False
                return summary

        # 2. Record with the recorder's own pipeline; the 1080p clip stays on SD.
        summary["stage"] = "record"
        video_dir = vcfg["dir"]
        os.makedirs(video_dir, exist_ok=True)
        video_recorder.sweep_boot_debris(video_dir)
        ring = ensure_room_fn(video_dir, vcfg["storage"])
        if ring.get("paused"):
            raise RuntimeError(f"SD storage over its limit (used={ring.get('used_pct')}% "
                               f"free={ring.get('free_gb')}GiB); not recording. Free space or "
                               f"raise video.storage.max_used_pct")
        if encoder_binary is None:
            encoder_binary, _ = video_recorder._select_video_command(settings["capture_backend"])
        if ffmpeg_binary is None:
            ffmpeg_binary = shutil.which("ffmpeg")
            if not ffmpeg_binary:
                raise RuntimeError("ffmpeg not found on PATH; install it (apt install ffmpeg)")
        record_s = vtx["duration_s"] + vtx["lead_in_s"]
        short = dict(vcfg, clip_minutes=record_s / 60.0)
        clip = record_fn(settings, short, video_dir, encoder_binary=encoder_binary,
                         ffmpeg_binary=ffmpeg_binary,
                         controls=video_recorder._resolve_controls(settings))
        summary["clip"] = {k: clip.get(k) for k in ("ok", "stage", "basename", "mp4", "bytes")}
        if not clip.get("ok"):
            raise RuntimeError(f"recording failed at stage {clip.get('stage')!r}; nothing to send")

        # 3. Budget: what the cap allows AND what the time budget can still pace.
        summary["stage"] = "fit"
        chunk_chars = int(settings["pacing_chunk_b64_chars"])
        raw_per_msg = int(chunk_chars * RAW_BYTES_PER_B64_CHAR)
        affordable = budget.max_messages_now() - VIDEO_OVERHEAD_MSGS
        budget_msgs = min(int(vtx["message_cap"]), affordable)
        print(f"[VTX] message budget: cap={vtx['message_cap']} affordable_now={affordable} "
              f"-> {budget_msgs} chunk msgs ({budget_msgs * raw_per_msg} B)")
        geo = vcfg["geometry"]
        fit = fit_fn(clip["mp4"], WORK_DIR, width=vtx["output_wh"][0], height=vtx["output_wh"][1],
                     fps=vtx["fps"], duration_s=vtx["duration_s"], budget_msgs=budget_msgs,
                     raw_bytes_per_msg=raw_per_msg, source_wh=tuple(geo["output_wh"]),
                     preset=vtx["preset"], ffmpeg_binary=ffmpeg_binary, clock=clock)
        payload = fit.pop("payload")
        summary["fit"] = fit
        print(f"[VTX] payload: {fit['bytes']} B = {fit['msgs']} msgs ({fit['used_pct']}% of budget) "
              f"{fit['pass2_tries']} pass-2 tries, prescale {fit['prescale_s']}s encode {fit['encode_s']}s"
              f"{', TRIMMED ' + str(fit['frames_trimmed']) + ' frames' if fit['frames_trimmed'] else ''}")

        # The file name IS the capture time: the recorder's basename starts with it.
        stamp = clip["basename"].split("_video_")[0]
        file_name = f"{stamp}_video_{vtx['duration_s']:g}s.h264"
        send_args = dict(
            payload=payload, file_name=file_name, fps=vtx["fps"], dur=vtx["duration_s"],
            res=f"{vtx['output_wh'][0]}x{vtx['output_wh'][1]}",
            crop=format_crop(geo.get("crop_native_xywh")), br=fit["target_kbps"],
            chunk_b64_chars=chunk_chars, delay_seconds=settings["pacing_delay_seconds"],
            start_metadata=_start_metadata(settings), cpu_temp_text=_cpu_temp_text(),
            current_timestamp=now_fn().strftime("%Y-%m-%dT%H:%M:%SZ"),
            sleep_fn=sleep_fn, clock=clock)

        if not transmit:
            print(f"[VTX] send plan (NO transmit): {file_name} {fit['msgs']} chunks "
                  f"+{VIDEO_OVERHEAD_MSGS} (START, chunk-0 repeat, END)")
            summary["stage"] = "done_no_transmit"
            return summary

        # 4. Keep the burst inside one cellular lane when the island is on.
        summary["stage"] = "lane_wait"
        phase_cfg = settings.get("transmit_phase_cfg") or {}
        if phase_cfg.get("enabled"):
            burst_s = rc_transmit_phase.burst_seconds_for(
                fit["msgs"] + 1, settings["pacing_delay_seconds"])      # +1: the chunk-0 repeat
            grid_clock = rc_transmit_phase.acquire_grid_clock(gate_info, gate_mono, clock=clock)
            plan = rc_transmit_phase.plan_from_clock(grid_clock, burst_s, phase_cfg)
            print(rc_transmit_phase.describe_plan(plan, burst_s))
            wait_s = plan["wait_s"]
            if wait_s > 0 and not budget.has_time_for(wait_s + burst_s):
                print(f"[VTX][WARN] skipping the {wait_s:.0f}s lane wait: only "
                      f"{budget.remaining_s():.0f}s of budget left; sending unscheduled")
                wait_s = 0.0
            if wait_s > 0:
                sleep_fn(wait_s)
            summary["transmit_phase"] = {"reason": plan.get("reason"), "wait_s": wait_s,
                                         "burst_s": burst_s}

        # 5. Send.
        summary["stage"] = "transmit"
        opened = True
        result = transmit_video_clip(tx_open_fn(settings["config_path"]), budget, **send_args)
        summary["transmit_result"] = result
        if result["refused_reason"]:
            print(f"[VTX][ERROR] clip NOT sent — {result['refused_reason']}. The recording is "
                  f"on the SD card: {clip['mp4']}")
        else:
            print(f"[VTX] transmit done: sent={result['sent']}/{result['planned']} "
                  f"complete={result['complete_send']} repeat={result['repeat_sent']} "
                  f"uart={result['uart_duration_sec']:.1f}s file={file_name}")
        summary["stage"] = "done"
        return summary

    except Exception as exc:
        summary["error"] = f"{type(exc).__name__}: {exc}"
        print(f"[VTX][ERROR] cycle failed at stage {summary['stage']!r}: {summary['error']}")
        return summary
    finally:
        if opened:
            try:
                bm_close_fn()
            except Exception as exc:
                print(f"[VTX][WARN] BM serial close failed: {exc}")
        summary["halt_result"] = halt_fn(
            enabled=settings["power_halt_enabled"], dry_run=settings["power_halt_dry_run"],
            mode=settings["power_halt_mode"], script_path=settings["power_halt_script_path"])
        print(f"[VTX] cycle end: stage={summary['stage']} elapsed={budget.elapsed_s():.1f}s of "
              f"{settings['budget_seconds']}s; halt={summary['halt_result']['action']}")

#!/usr/bin/env python3
# filename: rc_progressive_jpeg.py
# description: Sprint08 progressive-JPEG release-candidate (RC) entry point.
"""
Sprint08 progressive-JPEG RC entry script — P0 skeleton.

Purpose
-------
The config-gated RC runtime path (sprint spec section 2, module M7 shell).
The known-good HEIC path (main_pi_camera.py) stays byte-untouched; this
script is only ever invoked when camera_schedule.yaml sets
capture_mode: progressive_jpeg.

P0 scope (current state)
------------------------
Load camera_schedule.yaml, resolve and print every RC setting, exit.
NO capture, NO encode, NO serial/BM-bus access, NO halt, NO cron.
Later sprint rows (P1-P7) wire in the M1-M6 modules behind this entry.

Inputs
------
  --config-path   camera_schedule.yaml
                  (default: /home/pi/BM_Devel_Pi/camera_schedule.yaml)

Outputs
-------
  Resolved-settings lines on stdout, "[RC]" prefixed.
  Exit 0 on success; exit 2 on config load/validation failure (loud).

Example
-------
  python3 rc_progressive_jpeg.py --config-path camera_schedule.yaml

Assumptions / known limitations
-------------------------------
  - Pacing/chunking come from the existing bm_serial YAML block (single
    source of truth, parsed by bm_serial.load_bm_serial_config). If PyYAML
    is unavailable that loader returns {}, and the same defaults the
    production send path uses (300 chars/msg, 5 s/msg) apply — flagged in
    the output as source=default.
  - YAML defaults are the Sprint07 Pi-validated settings (spec section 4);
    a missing progressive_jpeg block resolves to the approved RC values.
"""

import argparse
import sys

from spotter_time_sync import (
    load_camera_schedule,
    resolve_timezone,
    validate_schedule,
)
from bm_serial import load_bm_serial_config
from process_image_v2 import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS,
)
from rc_jpeg_encoder import output_size_for_crop
# Ladder computation lives in the pure M3 module (P3); re-exported here so
# entry-script callers keep one import point.
from rc_quality_selector import compute_quality_ladder  # noqa: F401

DEFAULT_CONFIG_PATH = "/home/pi/BM_Devel_Pi/camera_schedule.yaml"


def resolve_pacing(config_path):
    """Return chunk/delay pacing from the bm_serial YAML block with the
    production defaults as fallback (same behavior as the HEIC send path)."""
    bm_cfg = load_bm_serial_config(config_path)

    chunk_from_yaml = bm_cfg.get("image_buffer_size") is not None
    delay_from_yaml = bm_cfg.get("image_transmit_delay_seconds") is not None

    try:
        chunk_b64_chars = int(bm_cfg.get("image_buffer_size", DEFAULT_BUFFER_SIZE))
    except Exception:
        chunk_b64_chars = DEFAULT_BUFFER_SIZE
        chunk_from_yaml = False
    try:
        delay_seconds = float(
            bm_cfg.get("image_transmit_delay_seconds", DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS)
        )
    except Exception:
        delay_seconds = DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS
        delay_from_yaml = False

    return {
        "chunk_b64_chars": chunk_b64_chars,
        "delay_seconds": delay_seconds,
        "source": "yaml" if (chunk_from_yaml and delay_from_yaml) else "default",
    }


def resolve_rc_settings(config_path):
    """Load + validate config and return one flat resolved-settings dict."""
    cfg = load_camera_schedule(config_path)
    validate_schedule(cfg)
    pacing = resolve_pacing(config_path)

    ladder = compute_quality_ladder(
        cfg.progressive_jpeg_q_max,
        cfg.progressive_jpeg_q_min,
        cfg.progressive_jpeg_q_step,
    )

    budget_seconds = int(cfg.progressive_jpeg_max_run_time_min) * 60
    # Informational only at P0: how many paced messages the whole budget
    # could hold if nothing else consumed time. M1 owns the real accounting.
    budget_messages_if_transmit_only = int(budget_seconds // pacing["delay_seconds"]) if pacing["delay_seconds"] > 0 else None

    return {
        "config_path": config_path,
        "capture_mode": cfg.capture_mode,
        "q_max": int(cfg.progressive_jpeg_q_max),
        "q_min": int(cfg.progressive_jpeg_q_min),
        "q_step": int(cfg.progressive_jpeg_q_step),
        "quality_ladder": ladder,
        "max_run_time_min": int(cfg.progressive_jpeg_max_run_time_min),
        "budget_seconds": budget_seconds,
        "message_cap": int(cfg.progressive_jpeg_message_cap),
        "budget_messages_if_transmit_only": budget_messages_if_transmit_only,
        "pacing_chunk_b64_chars": pacing["chunk_b64_chars"],
        "pacing_delay_seconds": pacing["delay_seconds"],
        "pacing_source": pacing["source"],
        "power_halt_enabled": bool(cfg.power_halt_enabled),
        "power_halt_dry_run": bool(cfg.power_halt_dry_run),
        "power_halt_mode": cfg.power_halt_mode,
        "power_halt_script_path": cfg.power_halt_script_path,
        "timezone": resolve_timezone(cfg),
        "transmit_window": f"{cfg.transmit_start}-{cfg.transmit_end}",
        # RC frozen geometry (S07 byte-validated) — the RC's OWN crop keys,
        # deliberately separate from the HEIC image_pipeline crop. Output
        # height derives from the crop aspect via the M2 rounding rule.
        "crop_native_xywh": (
            cfg.progressive_jpeg_crop_x,
            cfg.progressive_jpeg_crop_y,
            cfg.progressive_jpeg_crop_w,
            cfg.progressive_jpeg_crop_h,
        ),
        "output_size": output_size_for_crop(
            cfg.progressive_jpeg_crop_w,
            cfg.progressive_jpeg_crop_h,
            cfg.progressive_jpeg_output_width,
        ),
        "capture_backend": cfg.image_pipeline_capture_backend,
    }


def print_resolved_settings(s):
    """Print the resolved RC settings, one loud line per fact."""
    print("[RC] Sprint08 progressive-JPEG RC — P0 config skeleton (no behavior)")
    print(f"[RC] config_path={s['config_path']}")

    if s["capture_mode"] == "progressive_jpeg":
        print("[RC] capture_mode=progressive_jpeg (RC path selected)")
    else:
        print(f"[RC] capture_mode={s['capture_mode']} (RC inactive; known-good HEIC path owns this cycle)")

    print(
        f"[RC] quality ladder: q_max={s['q_max']} q_min={s['q_min']} step={s['q_step']} "
        f"-> {s['quality_ladder']}"
    )
    print(f"[RC] cycle budget: max_run_time_min={s['max_run_time_min']} ({s['budget_seconds']} s)")
    print(f"[RC] message cap: {s['message_cap']} msgs (field-tested hard cap)")
    print(
        f"[RC] pacing (bm_serial block, source={s['pacing_source']}): "
        f"chunk_b64_chars={s['pacing_chunk_b64_chars']} delay_s={s['pacing_delay_seconds']}"
    )
    print(
        f"[RC] derived (informational): transmit-only budget holds "
        f"{s['budget_messages_if_transmit_only']} paced msgs; M1 owns real accounting"
    )
    print(
        f"[RC] power_halt: enabled={s['power_halt_enabled']} "
        f"dry_run={s['power_halt_dry_run']} mode={s['power_halt_mode']} "
        f"script={s['power_halt_script_path']}"
    )
    print(f"[RC] schedule: window={s['transmit_window']} tz={s['timezone']}")
    print(
        f"[RC] geometry (native coords, frozen): crop_xywh={s['crop_native_xywh']} "
        f"output={s['output_size'][0]}x{s['output_size'][1]} backend={s['capture_backend']}"
    )
    print("[RC] P0 skeleton complete — no capture/encode/transmit performed.")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Sprint08 progressive-JPEG RC entry (P0: resolve + print config only)."
    )
    parser.add_argument(
        "--config-path",
        default=DEFAULT_CONFIG_PATH,
        help="Path to camera_schedule.yaml",
    )
    args = parser.parse_args(argv)

    try:
        settings = resolve_rc_settings(args.config_path)
    except Exception as exc:
        print(f"[RC][ERROR] config load/validation failed: {exc}", file=sys.stderr)
        return 2

    print_resolved_settings(settings)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

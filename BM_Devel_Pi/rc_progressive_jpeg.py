#!/usr/bin/env python3
# filename: rc_progressive_jpeg.py
# description: Sprint08 progressive-JPEG release-candidate entry point (M7 orchestrator).
"""
Sprint08 progressive-JPEG RC entry script — M7 cycle orchestrator.

Config-gated runtime path (capture_mode: progressive_jpeg). The known-good
HEIC path (main_pi_camera.py) is untouched; this script wires the tested RC
modules into one cycle:

  CycleBudget (M1, starts at process start)
    -> schedule gate (transmit runs only; reuses production Spotter-time gate)
    -> WS wake heartbeat (transmit runs only)
    -> native capture (reuses production _run_native_full_capture:
       watchdog, retries, WS error telemetry, exact libcamera args)
    -> M2 prepare_source (in-process crop+lanczos, S07 byte-validated)
    -> M3 select_quality (ladder step-down vs budget + 195-msg cap)
    -> persist JPEG + metadata sidecar + CSV log
    -> M5 transmit (complete or bounded-incomplete; --transmit only)
    -> M6 power halt (per power_halt config; runs in finally)

CLI safety ladder (guardrail sequence: capture-only -> compress-only -> transmit):
  --print-config            resolve + print settings, no cycle (P0 behavior)
  (default)                 capture + encode + report the send plan; NO BM bus
  --capture-only            stop after native capture + prepare
  --compress-only NATIVE    skip camera; run the ladder on an existing native
  --transmit                the ONLY flag that touches the BM bus
  --skip-time-window        bench override for the Spotter-time gate
  --output-dir DIR          where the final JPEG + sidecar land (default images/)

Exit codes: 0 = cycle ran as designed (incl. an intentional bounded/incomplete
send), 1 = runtime failure (capture/encode), 2 = config error.

Assumptions / known limitations:
  - Capture side matches production (native 4608x2592 q95 via
    libcamera-still/rpicam-still; CMA constraints per S07). camera_controls
    YAML island is NOT applied by the RC capture (bench config has it
    disabled; note for future hardening).
  - Capture-retry WS telemetry (production behavior) can touch the BM bus on
    capture errors even without --transmit, exactly like the HEIC path.
"""

import argparse
import os
import sys
import time
from datetime import datetime, timezone

from spotter_time_sync import (
    load_camera_schedule,
    resolve_timezone,
    should_transmit_now_from_schedule,
    validate_schedule,
)
from bm_serial import load_bm_serial_config
from process_image_v2 import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS,
    IMAGE_DIRECTORY,
    _get_bm_serial,
    _load_libcamera_metadata_json,
    _run_native_full_capture,
    _select_camera_command,
    apply_bm_serial_runtime_settings,
    close_bm_serial,
    collect_storage_health,
    debug_print,
    generate_filename,
    get_cpu_temperature,
    get_hostname,
    get_software_sha,
    log_message,
    send_wake_status,
    update_capture_metadata,
)
from rc_jpeg_encoder import output_size_for_crop, prepare_source
from rc_power_halt import perform_power_halt
# Ladder computation lives in the pure M3 module; re-exported here so entry
# script callers keep one import point.
from rc_quality_selector import compute_quality_ladder, select_quality  # noqa: F401
from rc_time_budget import CycleBudget
from rc_transmit import transmit_progressive_image

DEFAULT_CONFIG_PATH = "/home/pi/BM_Devel_Pi/camera_schedule.yaml"


# ---------------------------------------------------------------------------
# Config resolution (P0)
# ---------------------------------------------------------------------------

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
    budget_messages_if_transmit_only = (
        int(budget_seconds // pacing["delay_seconds"]) if pacing["delay_seconds"] > 0 else None
    )

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
        "window_start": cfg.transmit_start,
        "window_end": cfg.transmit_end,
        # RC frozen geometry (S07 byte-validated) — the RC's OWN crop keys.
        "crop_native_xywh": (
            cfg.progressive_jpeg_crop_x,
            cfg.progressive_jpeg_crop_y,
            cfg.progressive_jpeg_crop_w,
            cfg.progressive_jpeg_crop_h,
        ),
        "output_width": int(cfg.progressive_jpeg_output_width),
        "output_size": output_size_for_crop(
            cfg.progressive_jpeg_crop_w,
            cfg.progressive_jpeg_crop_h,
            cfg.progressive_jpeg_output_width,
        ),
        # Capture side matches the production image_pipeline source settings.
        "capture_backend": cfg.image_pipeline_capture_backend,
        "source_width": int(cfg.image_pipeline_source_width),
        "source_height": int(cfg.image_pipeline_source_height),
        "source_jpeg_quality": int(cfg.image_pipeline_source_jpeg_quality),
        "enforce_time_window": bool(cfg.enforce_time_window),
    }


def print_resolved_settings(s):
    """Print the resolved RC settings, one loud line per fact."""
    print("[RC] Sprint08 progressive-JPEG RC — resolved settings")
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


# ---------------------------------------------------------------------------
# Cycle pieces (each injectable for off-device tests)
# ---------------------------------------------------------------------------

def _default_capture(settings, output_dir):
    """Native full capture via the production watchdog path. Returns
    (native_path, capture_info, image_stem)."""
    command, backend = _select_camera_command(settings["capture_backend"])
    if command is None:
        raise RuntimeError(
            f"RC requires libcamera-still/rpicam-still; backend={backend!r} unsupported"
        )
    os.makedirs(output_dir, exist_ok=True)
    image_stem = os.path.splitext(generate_filename())[0]  # "<ts>_image"
    native_path = os.path.join(output_dir, f"{image_stem}_native_full.jpg")
    log_prefix = os.path.join(output_dir, f"{image_stem}_native_full")

    capture_info = _run_native_full_capture(
        command=command,
        native_image_path=native_path,
        source_width=settings["source_width"],
        source_height=settings["source_height"],
        jpeg_quality=settings["source_jpeg_quality"],
        log_prefix=log_prefix,
        settings=None,  # camera_controls island not applied by the RC (documented)
    )
    return native_path, capture_info, image_stem


def _default_bm_open(config_path):
    """Apply bm_serial runtime settings and return the production tx callable."""
    apply_bm_serial_runtime_settings(configure_serial=True)
    return _get_bm_serial().spotter_tx


def _cpu_temp_text():
    try:
        return f"{get_cpu_temperature():.1f}"
    except Exception:
        return "na"


def run_cycle(
    settings,
    *,
    transmit=False,
    capture_only=False,
    native_path=None,
    skip_time_window=False,
    output_dir=IMAGE_DIRECTORY,
    capture_fn=_default_capture,
    bm_open_fn=_default_bm_open,
    bm_close_fn=close_bm_serial,
    wake_fn=send_wake_status,
    halt_fn=perform_power_halt,
    sleep_fn=time.sleep,
    clock=time.monotonic,
):
    """Run one RC cycle. Returns a summary dict; raises only on runtime failure
    before the halt (the halt itself runs in finally and never raises)."""
    summary = {
        "budget_seconds": settings["budget_seconds"],
        "transmit": transmit,
        "selection": None,
        "transmit_result": None,
        "halt_result": None,
        "native_path": native_path,
        "final_path": None,
        "schedule_allowed": True,
    }

    # M1: ONE budget, charged from here on.
    budget = CycleBudget(
        settings["budget_seconds"], settings["pacing_delay_seconds"], clock=clock
    )
    print(f"[RC] cycle start: budget={settings['budget_seconds']}s "
          f"pacing={settings['pacing_delay_seconds']}s/msg")

    try:
        # Schedule gate — transmit runs only (manual/bench modes must not
        # touch the BM bus; the Spotter-time read opens the UART).
        if transmit and not skip_time_window and settings["enforce_time_window"]:
            allowed, info = should_transmit_now_from_schedule(settings["config_path"])
            summary["schedule_allowed"] = allowed
            print(f"[RC] schedule gate: {info.get('reason')}")
            if not allowed:
                try:
                    wake_fn(
                        action="skip_win",
                        timezone_name=settings["timezone"],
                        local_time=info.get("local_time"),
                        window_start=settings["window_start"],
                        window_end=settings["window_end"],
                        image_res_key=f"{settings['output_size'][0]}x{settings['output_size'][1]}",
                        image_quality=settings["q_max"],
                        reason="window",
                    )
                except Exception as exc:
                    debug_print(f"Wake status send failed, continuing safely: {exc}")
                return summary

        if transmit:
            try:
                wake_fn(
                    action="cap",
                    timezone_name=settings["timezone"],
                    local_time=None,
                    window_start=settings["window_start"],
                    window_end=settings["window_end"],
                    image_res_key=f"{settings['output_size'][0]}x{settings['output_size'][1]}",
                    image_quality=settings["q_max"],
                    reason=None,
                )
            except Exception as exc:
                debug_print(f"Wake status send failed, continuing safely: {exc}")

        # Capture (or reuse an existing native in --compress-only).
        capture_info = {}
        if native_path is None:
            native_path, capture_info, image_stem = capture_fn(settings, output_dir)
            summary["native_path"] = native_path
        else:
            image_stem = os.path.splitext(os.path.basename(native_path))[0]
            if image_stem.endswith("_native_full"):
                image_stem = image_stem[: -len("_native_full")]
        print(f"[RC] native ready: {native_path} "
              f"({os.path.getsize(native_path)} B, elapsed={budget.elapsed_s():.1f}s)")

        # M2 prepare (once per cycle; every ladder attempt reuses it).
        source = prepare_source(
            native_path, settings["crop_native_xywh"], settings["output_width"]
        )
        print(f"[RC] source prepared: {source.size[0]}x{source.size[1]} "
              f"(elapsed={budget.elapsed_s():.1f}s)")

        if capture_only:
            print("[RC] --capture-only: stopping before encode/transmit.")
            return summary

        # M3 adaptive selection.
        selection = select_quality(
            source,
            budget,
            q_max=settings["q_max"],
            q_min=settings["q_min"],
            q_step=settings["q_step"],
            message_cap=settings["message_cap"],
            chunk_b64_chars=settings["pacing_chunk_b64_chars"],
        )
        summary["selection"] = {
            k: selection[k] for k in ("quality", "attempts", "fits", "reason")
        }
        summary["selection"]["attempt_log"] = selection["attempt_log"]
        for a in selection["attempt_log"]:
            print(f"[RC] attempt q{a['quality']}: {a['jpeg_bytes']} B, "
                  f"{a['message_count']} msgs, over_cap={a['over_cap']}, "
                  f"budget_fit={a['budget_fit']}")
        print(f"[RC] selection: quality={selection['quality']} "
              f"attempts={selection['attempts']} fits={selection['fits']} "
              f"reason={selection['reason']}")

        if selection["encode"] is None:
            raise RuntimeError("no encode possible within budget (attempts=0)")

        encode = selection["encode"]
        final_name = f"{image_stem}_compressed.jpg"
        final_path = os.path.join(output_dir, final_name)
        with open(final_path, "wb") as f:
            f.write(encode["jpeg_data"])
        summary["final_path"] = final_path
        print(f"[RC] final JPEG: {final_path} ({encode['jpeg_bytes']} B, "
              f"{encode['message_count']} msgs, sha256={encode['jpeg_sha256'][:16]}...)")

        # Sidecar metadata (production pattern) + libcamera metadata for END.
        libcamera_metadata = _load_libcamera_metadata_json(capture_info.get("metadata_json"))
        storage_health = collect_storage_health()
        try:
            capture_metadata = update_capture_metadata(final_path, {
                "software_sha": get_software_sha(),
                "hostname": get_hostname(),
                "metadata_schema": "bmcam_runtime_sidecar_v1",
                "metadata_source": "rc_progressive_jpeg",
                "utc_capture_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "capture_mode": "progressive_jpeg",
                "img_format": "pjpg",
                "jpeg_quality_used": selection["quality"],
                "enc_attempts": selection["attempts"],
                "fits": selection["fits"],
                "selector_reason": selection["reason"],
                "attempt_log": selection["attempt_log"],
                "jpeg_bytes": encode["jpeg_bytes"],
                "base64_chars": encode["base64_len"],
                "message_count": encode["message_count"],
                "jpeg_sha256": encode["jpeg_sha256"],
                "crop_native_xywh": list(settings["crop_native_xywh"]),
                "output_size": list(settings["output_size"]),
                "native_path": native_path,
                **{k: v for k, v in capture_info.items() if k != "requested_camera_controls"},
                **libcamera_metadata,
                **storage_health,
            }) or {}
        except Exception as exc:
            debug_print(f"RC sidecar update failed, continuing safely: {exc}")
            capture_metadata = libcamera_metadata

        est_minutes = encode["message_count"] * settings["pacing_delay_seconds"] / 60.0
        if not transmit:
            print(f"[RC] send plan (NO transmit): {encode['message_count']} chunks "
                  f"(+2 START/END) at q{selection['quality']}, "
                  f"est {est_minutes:.1f} min, fits={selection['fits']}")
            return summary

        # M5 transmit (complete or bounded).
        start_metadata = {
            "image_res_key": f"{settings['output_size'][0]}x{settings['output_size'][1]}",
            "timezone": settings["timezone"],
            "window_start": settings["window_start"],
            "window_end": settings["window_end"],
            "software_sha": get_software_sha(),
            "hostname": get_hostname(),
            **storage_health,
        }
        tx = bm_open_fn(settings["config_path"])
        result = transmit_progressive_image(
            tx,
            budget,
            jpeg_data=encode["jpeg_data"],
            compressed_file_name=final_name,
            quality=selection["quality"],
            enc_attempts=selection["attempts"],
            fits=selection["fits"],
            selector_reason=selection["reason"],
            chunk_b64_chars=settings["pacing_chunk_b64_chars"],
            delay_seconds=settings["pacing_delay_seconds"],
            start_metadata=start_metadata,
            capture_metadata=capture_metadata,
            cpu_temp_text=_cpu_temp_text(),
            software_sha=get_software_sha(),
            hostname=get_hostname(),
            sleep_fn=sleep_fn,
            clock=clock,
        )
        summary["transmit_result"] = result
        print(f"[RC] transmit done: sent={result['sent']}/{result['planned']} "
              f"complete={result['complete_send']} "
              f"incomplete_emitted={result['incomplete_emitted']} "
              f"uart={result['uart_duration_sec']:.1f}s")

        try:
            update_capture_metadata(final_path, {
                "transmit_success": result["complete_send"],
                "sent_buffers": result["sent"],
                "planned_buffers": result["planned"],
                "transmit_duration_sec": result["uart_duration_sec"],
            })
        except Exception as exc:
            debug_print(f"RC post-transmit sidecar update failed: {exc}")

        try:
            log_message(
                datetime.now(),
                final_name,
                os.path.getsize(native_path) if native_path and os.path.exists(native_path) else 0,
                encode["jpeg_bytes"],
                selection["quality"],
                result["sent"],
                budget.elapsed_s() / 60.0,
                True,
                float(_cpu_temp_text()) if _cpu_temp_text() != "na" else 0.0,
            )
        except Exception as exc:
            debug_print(f"RC CSV log failed, continuing safely: {exc}")

        return summary

    finally:
        if transmit:
            try:
                bm_close_fn()
            except Exception as exc:
                debug_print(f"BM serial close failed: {exc}")
        # M6: halt runs on success AND failure/exhaustion paths (never raises).
        summary["halt_result"] = halt_fn(
            enabled=settings["power_halt_enabled"],
            dry_run=settings["power_halt_dry_run"],
            mode=settings["power_halt_mode"],
            script_path=settings["power_halt_script_path"],
        )
        print(f"[RC] cycle end: elapsed={budget.elapsed_s():.1f}s of "
              f"{settings['budget_seconds']}s; halt={summary['halt_result']['action']}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv=None, **cycle_overrides):
    parser = argparse.ArgumentParser(
        description="Sprint08 progressive-JPEG RC cycle (config-gated; see module docstring)."
    )
    parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH,
                        help="Path to camera_schedule.yaml")
    parser.add_argument("--print-config", action="store_true",
                        help="Resolve + print settings, run nothing")
    parser.add_argument("--capture-only", action="store_true",
                        help="Stop after native capture + prepare (no encode/transmit)")
    parser.add_argument("--compress-only", metavar="NATIVE_JPG", default=None,
                        help="Skip camera; run the ladder on an existing native JPEG")
    parser.add_argument("--transmit", action="store_true",
                        help="Send over the BM bus (the only flag that touches it)")
    parser.add_argument("--skip-time-window", action="store_true",
                        help="Bench override: skip the Spotter-time transmit gate")
    parser.add_argument("--output-dir", default=IMAGE_DIRECTORY,
                        help="Directory for final JPEG + sidecar")
    args = parser.parse_args(argv)

    try:
        settings = resolve_rc_settings(args.config_path)
    except Exception as exc:
        print(f"[RC][ERROR] config load/validation failed: {exc}", file=sys.stderr)
        return 2

    if args.print_config:
        print_resolved_settings(settings)
        return 0

    if settings["capture_mode"] != "progressive_jpeg":
        print(f"[RC] capture_mode={settings['capture_mode']} — RC inactive; "
              "known-good HEIC path owns this cycle. Nothing to do.")
        return 0

    print_resolved_settings(settings)
    try:
        run_cycle(
            settings,
            transmit=args.transmit,
            capture_only=args.capture_only,
            native_path=args.compress_only,
            skip_time_window=args.skip_time_window,
            output_dir=args.output_dir,
            **cycle_overrides,
        )
    except Exception as exc:
        print(f"[RC][ERROR] cycle failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

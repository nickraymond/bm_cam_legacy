#!/usr/bin/env python3
# filename: rc_progressive_jpeg.py
# description: Sprint08 progressive-JPEG release-candidate entry point (M7 orchestrator).
"""
Sprint08 progressive-JPEG RC entry script — M7 cycle orchestrator.

Config-gated runtime path (capture_mode: progressive_jpeg). The known-good
HEIC path (main_pi_camera.py) is untouched; this script wires the tested RC
modules into one cycle:

  CycleBudget (M1, starts at process start)
    -> schedule gate (transmit runs only; reuses production Spotter-time gate;
       Sprint11: its Spotter UTC read is also the C2 grid clock)
    -> WS wake heartbeat (transmit runs only)
    -> native capture (reuses production _run_native_full_capture:
       watchdog, retries, WS error telemetry, exact libcamera args)
    -> M2 prepare_source (in-process crop+lanczos, S07 byte-validated)
    -> M3 select_quality (ladder step-down vs budget + 195-msg cap)
    -> persist JPEG + metadata sidecar + CSV log
    -> C2 phase wait: park until the burst fits a clean lane on the
       5-minute UTC blackout grid (rc_transmit_phase; island-gated)
    -> M5 transmit (complete or bounded-incomplete; --transmit only)
    -> C3 deferred-ack flush, then C4 bounded post-transmit listen tail
    -> M6 power halt (per power_halt config; runs in finally)

Sprint11 reordered this to CAPTURE-FIRST: the 90 s pre-capture listen
window is gone (D2). It pushed transmit start from ~:01:00 to ~:03:10 and
put a 194 s burst through the :05:00 blackout at ~62 % through, and it
listened at the one time commands never arrive (finding 006). Commands now
apply from cached state on the NEXT boot.

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
import shutil
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
from command_daemon import load_bm_commands_config
from command_state import CommandState
import rc_command_hooks as cmd_hooks
import rc_media_id
import rc_transmit_phase
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
from rc_quality_selector import (  # noqa: F401
    compute_quality_ladder,
    parse_ladder_spec,
    select_quality,
)
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

    # Explicit multi-segment ladder wins when configured; q_max/q_min/step
    # remain the uniform-step fallback.
    ladder_spec = (cfg.progressive_jpeg_quality_ladder or "").strip()
    if ladder_spec:
        try:
            ladder = parse_ladder_spec(ladder_spec)
            ladder_source = "explicit"
        except Exception:
            # Sprint15: video mode never encodes JPEGs, so a broken ladder
            # string must not fail-closed a video unit (HEIC-gate doctrine).
            # Stills mode keeps the loud failure.
            if cfg.capture_mode != "video":
                raise
            print("[RC][WARN] bad quality ladder ignored in video mode; "
                  "using computed fallback")
            ladder = compute_quality_ladder(
                cfg.progressive_jpeg_q_max,
                cfg.progressive_jpeg_q_min,
                cfg.progressive_jpeg_q_step,
            )
            ladder_source = "computed"
    else:
        ladder = compute_quality_ladder(
            cfg.progressive_jpeg_q_max,
            cfg.progressive_jpeg_q_min,
            cfg.progressive_jpeg_q_step,
        )
        ladder_source = "computed"

    budget_seconds = int(cfg.progressive_jpeg_max_run_time_min) * 60
    budget_messages_if_transmit_only = (
        int(budget_seconds // pacing["delay_seconds"]) if pacing["delay_seconds"] > 0 else None
    )

    return {
        "config_path": config_path,
        "capture_mode": cfg.capture_mode,
        # q_max/q_min derive from the RESOLVED ladder so telemetry and wake
        # status always reflect what the selector actually walks.
        "q_max": ladder[0],
        "q_min": ladder[-1],
        "q_step": int(cfg.progressive_jpeg_q_step),
        "quality_ladder": ladder,
        "ladder_source": ladder_source,
        "max_run_time_min": int(cfg.progressive_jpeg_max_run_time_min),
        "budget_seconds": budget_seconds,
        "message_cap": int(cfg.progressive_jpeg_message_cap),
        "budget_messages_if_transmit_only": budget_messages_if_transmit_only,
        # v2 `src`: None = capture from the camera (shipped behaviour).
        # Only the src command sets this; there is no YAML key, so a unit
        # can never boot into reference-image mode by config accident.
        "source_image_path": None,
        "pacing_chunk_b64_chars": pacing["chunk_b64_chars"],
        "pacing_delay_seconds": pacing["delay_seconds"],
        "pacing_source": pacing["source"],
        "power_halt_enabled": bool(cfg.power_halt_enabled),
        "power_halt_dry_run": bool(cfg.power_halt_dry_run),
        "power_halt_mode": cfg.power_halt_mode,
        "power_halt_script_path": cfg.power_halt_script_path,
        # Sprint12: overlay stamps these "command hlt=N"/"command twn=N"
        # so the boot log states unambiguously WHO set the halt/window.
        "power_halt_source": "yaml",
        "window_source": "yaml",
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
        # Sprint10 media-id island (rc_media_id): absent/off == legacy wire.
        "media_gid_enabled": bool(
            rc_media_id.load_media_gid_config(config_path)["enabled"]),
        # Sprint11 C2 island (rc_transmit_phase): absent/off == unscheduled.
        "transmit_phase_cfg": rc_transmit_phase.load_transmit_phase_config(
            config_path),
    }


def print_resolved_settings(s):
    """Print the resolved RC settings, one loud line per fact."""
    print("[RC] Sprint08 progressive-JPEG RC — resolved settings")
    print(f"[RC] config_path={s['config_path']}")

    if s["capture_mode"] == "progressive_jpeg":
        print("[RC] capture_mode=progressive_jpeg (RC path selected)")
    elif s["capture_mode"] == "video":
        print("[RC] capture_mode=video (Sprint15 video path selected)")
    else:
        print(f"[RC] capture_mode={s['capture_mode']} (RC inactive; known-good HEIC path owns this cycle)")

    print(
        f"[RC] quality ladder ({s['ladder_source']}): q_max={s['q_max']} "
        f"q_min={s['q_min']} -> {s['quality_ladder']}"
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
        f"script={s['power_halt_script_path']} "
        f"source={s.get('power_halt_source', 'yaml')}"
    )
    print(f"[RC] schedule: window={s['transmit_window']} tz={s['timezone']} "
          f"source={s.get('window_source', 'yaml')}")
    ph = s.get("transmit_phase_cfg") or {}
    if ph.get("enabled"):
        lane = rc_transmit_phase.usable_lane_seconds(
            ph["grid_seconds"], ph["post_boundary_guard_s"],
            ph["pre_boundary_guard_s"])
        max_burst = s["message_cap"] * s["pacing_delay_seconds"]
        print(f"[RC] transmit_phase (C2): ON grid={ph['grid_seconds']:.0f}s "
              f"guards={ph['post_boundary_guard_s']:.0f}/"
              f"{ph['pre_boundary_guard_s']:.0f}s lane={lane:.0f}s")
        # The D3 config rule, checked at print-config time so a bad
        # (delay, cap) pair is caught on the bench, not from the gap
        # pattern in an overnight run.
        verdict = "fits" if max_burst <= lane else "DOES NOT FIT"
        print(f"[RC] transmit_phase rule: cap {s['message_cap']} x "
              f"{s['pacing_delay_seconds']}s = {max_burst:.0f}s vs lane "
              f"{lane:.0f}s -> {verdict}")
        if max_burst > lane:
            print("[RC][WARN] worst-case burst exceeds the clean lane; it "
                  "WILL cross a blackout (DESIGN D3)")
    else:
        print("[RC] transmit_phase (C2): OFF (unscheduled transmit)")
    print(
        f"[RC] geometry (native coords, frozen): crop_xywh={s['crop_native_xywh']} "
        f"output={s['output_size'][0]}x{s['output_size'][1]} backend={s['capture_backend']}"
    )


# ---------------------------------------------------------------------------
# Cycle pieces (each injectable for off-device tests)
# ---------------------------------------------------------------------------

def _load_camera_controls_island(config_path):
    """Return the nested image_pipeline.camera_controls block (production
    behavior parity — bmcam000 uses manual focus lens_position via this
    island). Best-effort: needs PyYAML; missing/unparseable -> {}."""
    try:
        import yaml

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}
        controls = (data.get("image_pipeline") or {}).get("camera_controls")
        return controls if isinstance(controls, dict) else {}
    except Exception as exc:
        debug_print(f"camera_controls island unavailable ({exc}); capturing without controls")
        return {}


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

    # Apply the same camera_controls the HEIC path applies (e.g. bmcam000's
    # manual focus); _run_native_full_capture already handles the fallback
    # retry without controls if the camera app rejects them. Sprint10:
    # a command-overlay override (D13) replaces the YAML island when set.
    controls = settings.get("camera_controls_override")
    if controls is None:
        controls = _load_camera_controls_island(settings["config_path"])
    capture_settings = {"camera_controls": controls} if controls else None

    capture_info = _run_native_full_capture(
        command=command,
        native_image_path=native_path,
        source_width=settings["source_width"],
        source_height=settings["source_height"],
        jpeg_quality=settings["source_jpeg_quality"],
        log_prefix=log_prefix,
        settings=capture_settings,
    )
    return native_path, capture_info, image_stem


def _default_bm_open(config_path):
    """Apply bm_serial runtime settings and return the production tx callable."""
    apply_bm_serial_runtime_settings(configure_serial=True)
    return _get_bm_serial().spotter_tx


def _apply_command_overlay(settings, state):
    """Command overlay (D13) with this module's island loader bound in."""
    return cmd_hooks.apply_command_overlay(
        settings, state, _load_camera_controls_island
    )


def _cpu_temp_text():
    try:
        return f"{get_cpu_temperature():.1f}"
    except Exception:
        return "na"


NATIVE_W, NATIVE_H = 4608, 2592


def _jpeg_dims(path):
    """(width, height) from the first JPEG SOF marker, or None.

    Deliberately dependency-free (no PIL): this runs before the pipeline
    proper and must not be able to fail for import reasons on a field unit.
    """
    with open(path, "rb") as fh:
        data = fh.read()
    i = 2
    while i < len(data) - 9:
        if data[i] != 0xFF:
            i += 1
            continue
        marker = data[i + 1]
        if marker in (0xC0, 0xC1, 0xC2, 0xC3):
            h = (data[i + 5] << 8) | data[i + 6]
            w = (data[i + 7] << 8) | data[i + 8]
            return w, h
        if marker in (0xD8, 0xD9):
            i += 2
            continue
        i += 2 + ((data[i + 2] << 8) | data[i + 3])
    return None


def stage_source_image(rel_or_abs_path, output_dir):
    """Copy a reference native into the cycle's output dir and return the copy.

    TWO failures from soak finding 009 are designed out here, because both
    cost a full overnight run:

    1. CONSUMABLE INPUT. The pipeline renames/consumes the native it is
       handed, so pointing it straight at the committed reference destroys
       the reference and every later cycle dies on a missing file. We always
       hand it a per-cycle COPY and never the original.
    2. WRONG DIMENSIONS. The raw reference_reef_coral_*.jpg files are
       4000x3000; the pipeline requires exactly 4608x2592 and rejects them.
       The original failure was a `find | head -1` picking a prep artifact.
       We validate dimensions HERE, before any work, and fail with a message
       that names the file and both sizes — not 100 s later inside the ladder.
    """
    path = rel_or_abs_path
    if os.path.isabs(path):
        candidates = [path]
    else:
        # SRC_TABLE paths are repo-relative, but the deployed runtime is a
        # FLAT app dir (/home/pi/BM_Devel_Pi) while reference_images/ lives
        # at the repo root. Search the plausible roots in priority order so
        # the same table works in a dev checkout and on a field unit.
        # BMCAM_REFERENCE_ROOT wins, so a unit with images on external
        # storage needs no code change.
        app_dir = os.path.dirname(os.path.abspath(__file__))
        roots = []
        env_root = os.environ.get("BMCAM_REFERENCE_ROOT")
        if env_root:
            roots.append(env_root)
        roots += [
            app_dir,                        # images deployed INTO the app dir
            os.path.dirname(app_dir),       # dev checkout / repo root
        ]
        candidates = [os.path.join(r, path) for r in roots]
    for candidate in candidates:
        if os.path.exists(candidate):
            path = candidate
            break
    else:
        raise FileNotFoundError(
            f"src reference image not found: {rel_or_abs_path}\n"
            f"  tried: {candidates}\n"
            f"  fix: deploy reference_images/ into the app dir, or set "
            f"BMCAM_REFERENCE_ROOT"
        )
    dims = _jpeg_dims(path)
    if dims != (NATIVE_W, NATIVE_H):
        raise ValueError(
            f"src reference {os.path.basename(path)} is {dims}, expected "
            f"({NATIVE_W}, {NATIVE_H}) — use the prepared "
            f"synthetic_native_4608x2592.jpg, not the raw scene file"
        )
    os.makedirs(output_dir, exist_ok=True)
    stem = time.strftime("refsrc_%Y%m%dT%H%M%SZ", time.gmtime())
    dest = os.path.join(output_dir, f"{stem}_native_full.jpg")
    shutil.copy2(path, dest)
    return dest


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
    bm_commands_cfg=None,
    command_state=None,
    bench_commands=False,
    daemon_factory=cmd_hooks.default_daemon_factory,
    grid_clock_fn=rc_transmit_phase.acquire_grid_clock,
):
    """Run one RC cycle. Returns a summary dict; raises only on runtime failure
    before the halt (the halt itself runs in finally and never raises).

    Sprint10: when the bm_commands island is enabled AND the cycle may
    touch the BM bus (transmit, or the explicit --bench-commands bench
    flag), a CommandDaemon owns the port for the whole cycle: subscribe
    at start, shared-port time sync, command pickup in transmit pacing
    slots, final drain, stop before halt (D11/D12).
    Disabled (default) leaves the cycle byte-identical to Sprint08/09.

    Sprint11 changes the ORDER, not the contract: no pre-capture listen
    (C1/D2), a phase wait before transmit (C2/D1), acks deferred out of
    the burst (C3/D5), and a bounded listen tail after it (C4/D6). C2-C4
    are island-gated; C1 is unconditional.
    """
    summary = {
        "budget_seconds": settings["budget_seconds"],
        "transmit": transmit,
        "selection": None,
        "transmit_result": None,
        "halt_result": None,
        "native_path": native_path,
        "final_path": None,
        "schedule_allowed": True,
        "command_events": [],
    }
    # Sprint12: a consumed one-shot trigger rides settings into the summary
    # so the run artifact records what fired this cycle.
    if settings.get("trigger"):
        summary["trigger"] = settings["trigger"]

    daemon = None
    use_daemon = bool(
        bm_commands_cfg
        and bm_commands_cfg.get("enabled")
        and command_state is not None
        and (transmit or bench_commands)
    )
    if use_daemon:
        daemon = daemon_factory(settings, bm_commands_cfg, command_state)
        daemon.start()

    # M1: ONE budget, charged from here on.
    budget = CycleBudget(
        settings["budget_seconds"], settings["pacing_delay_seconds"], clock=clock
    )
    print(f"[RC] cycle start: budget={settings['budget_seconds']}s "
          f"pacing={settings['pacing_delay_seconds']}s/msg")

    try:
        # Schedule gate — transmit runs only (manual/bench modes must not
        # touch the BM bus; the Spotter-time read opens the UART). With
        # the daemon active the gate reads Spotter time over the SHARED
        # port instead of opening its own (D11).
        gate_info, gate_mono = None, clock()
        if transmit and not skip_time_window and settings["enforce_time_window"]:
            allowed, info = should_transmit_now_from_schedule(
                settings["config_path"],
                **cmd_hooks.gate_kwargs_for(daemon, settings)
            )
            # Sprint11 C2: the gate's Spotter read is also the grid clock.
            # Pin it to a monotonic instant HERE and extrapolate later; the
            # transmit decision happens minutes after this read.
            gate_info, gate_mono = info, clock()
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

        # Sprint11 C1/D2: capture-first. There is NO pre-capture listen
        # window any more — it moved transmit start from ~:01:00 to
        # ~:03:10, which put a 194 s burst straight through the :05:00
        # blackout boundary at ~62 % through (measured first-gap mean
        # 65.5 %). Commands now apply from cached state on the NEXT boot,
        # which is already how `win` behaved. The listening moved to the
        # bounded post-transmit tail, where finding 006 says the mailbox
        # drain actually arrives.

        # Sprint10 v2: `src` command can substitute a committed reference
        # native for the camera capture (field debug — separates "camera
        # broken" from "link broken" without a site visit). CLI
        # --compress-only still wins, so bench use is unaffected.
        if native_path is None and settings.get("source_image_path"):
            native_path = stage_source_image(
                settings["source_image_path"], output_dir
            )
            summary["source_image"] = settings["source_image_path"]
            summary["source_image_staged"] = native_path
            print(f"[RC] src override: camera SKIPPED, using reference "
                  f"{settings['source_image_path']}")

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
            ladder=settings["quality_ladder"],
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
            # Bench-commands mode: no image transmit, but late commands
            # still ack + persist for the next cycle.
            cmd_hooks.drain_now(daemon, summary, clock=clock)
            # Sprint13: without a transmit there is no C4 tail, which
            # left a bench_commands cycle listening for only ~15 s —
            # untestable from a console (found on bmcam003, 2026-08-01).
            # Bench cycles now hold the same bounded listen window.
            # Gated on bench_commands so every production path (transmit
            # and plain no-transmit runs) stays byte-identical.
            if bench_commands and daemon is not None:
                cmd_hooks.post_transmit_listen(
                    daemon, bm_commands_cfg or {}, summary, budget,
                    clock=clock, sleep_fn=sleep_fn,
                )
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
        media_gid = None
        if settings.get("media_gid_enabled"):
            media_gid = rc_media_id.next_gid()
            print(f"[RC] media gid: {media_gid} (chunks <I{media_gid}.i>)")

        # --- Sprint11 C2: wait for a clean lane on the 5-minute grid -----
        # Everything above this line is cycle-relative; this is the ONE
        # place that reasons in absolute UTC (DESIGN D1).
        phase_cfg = settings.get("transmit_phase_cfg") or {}
        if phase_cfg.get("enabled"):
            burst_s = rc_transmit_phase.burst_seconds_for(
                encode["message_count"], settings["pacing_delay_seconds"],
                incomplete=not selection["fits"],
            )
            grid_clock = grid_clock_fn(
                gate_info, gate_mono, daemon=daemon, clock=clock)
            plan = rc_transmit_phase.plan_from_clock(
                grid_clock, burst_s, phase_cfg)
            print(rc_transmit_phase.describe_plan(plan, burst_s))
            wait_s = plan["wait_s"]
            # A wait spends the SAME budget the transmit needs. Waiting into
            # a budget that can no longer hold the burst would truncate the
            # image mid-send via the per-chunk guard — the exact failure
            # this sprint exists to remove. Sending at a bad phase loses
            # ~7 chunks; a truncated send loses the tail of the image.
            if wait_s > 0 and not budget.has_time_for(wait_s + burst_s):
                print(f"[PHASE][WARN] skipping the {wait_s:.0f}s lane wait: "
                      f"only {budget.remaining_s():.0f}s of budget left, "
                      f"burst needs {burst_s:.0f}s. Transmitting now, "
                      f"unscheduled.")
                plan["reason"] = "skipped_no_budget"
                plan["skipped_wait_s"] = wait_s
                plan["wait_s"] = wait_s = 0.0
                plan["start_phase_s"] = plan["phase_s"]
                plan["end_phase_s"] = plan["phase_s"] + burst_s
            if wait_s > 0:
                sleep_fn(wait_s)
            summary["transmit_phase"] = {
                k: plan[k] for k in ("reason", "wait_s", "phase_s",
                                     "start_phase_s", "end_phase_s",
                                     "fits_lane", "crosses_boundary")
            }
            summary["transmit_phase"]["burst_s"] = burst_s
            summary["transmit_phase"]["clock_source"] = plan.get("clock_source")

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
            # Sprint11 C3/D5: with defer_acks_during_transmit, no ack is
            # submitted between the first and last chunk — an ack shares
            # the same 2-slot cellular queue as the image.
            ack_drain_fn=cmd_hooks.make_ack_drain_fn(
                daemon, summary, clock=clock,
                defer=bool((bm_commands_cfg or {}).get(
                    "defer_acks_during_transmit"))),
            pending_pump_fn=cmd_hooks.make_pending_pump_fn(daemon, summary),
            media_gid=media_gid,
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

        # Sprint11 C3: the image is off the wire — release the deferred
        # acks now, before the tail, so they are not delayed by it.
        cmd_hooks.flush_acks(daemon, summary, clock=clock, sleep_fn=sleep_fn,
                             label="post-transmit ack flush")
        # Sprint11 C4/D6: bounded listen tail. This is when the mailbox
        # drain our own transmit triggered actually arrives (finding 006).
        cmd_hooks.post_transmit_listen(
            daemon, bm_commands_cfg or {}, summary, budget,
            clock=clock, sleep_fn=sleep_fn,
        )

        return summary

    finally:
        # Last command pickup + reader stop before the port closes.
        cmd_hooks.shutdown(daemon, summary, debug_print,
                           clock=clock, sleep_fn=sleep_fn)
        if transmit or daemon is not None:
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
        # summary holds the budget the cycle actually charged; a win
        # command re-overlays settings mid-cycle but never rebuilds the
        # running CycleBudget (Phase B nit, 2026-07-27).
        print(f"[RC] cycle end: elapsed={budget.elapsed_s():.1f}s of "
              f"{summary['budget_seconds']}s; halt={summary['halt_result']['action']}")


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
    parser.add_argument("--bench-commands", action="store_true",
                        help="BENCH ONLY: run the command daemon (subscribe + "
                             "acks DO touch the BM bus) without image transmit. "
                             "Requires bm_commands.enabled in YAML.")
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

    # Sprint10 command overlay (D13/D14): with the island enabled, the
    # persisted command state overrides YAML values in EVERY mode (a
    # field fix must govern bench captures too); the daemon itself only
    # runs when the cycle may touch the bus (--transmit/--bench-commands).
    bm_commands_cfg = load_bm_commands_config(args.config_path)
    command_state = None
    if bm_commands_cfg["enabled"]:
        command_state = CommandState(path=bm_commands_cfg["state_path"])
        print(f"[CMD] bm_commands enabled: topic={bm_commands_cfg['topic']} "
              f"tail={bm_commands_cfg['post_transmit_listen_s']}s "
              f"defer_acks={bm_commands_cfg['defer_acks_during_transmit']} "
              f"state={command_state.path} (loaded from "
              f"{command_state.load_info['source']})")
        settings = _apply_command_overlay(settings, command_state)

    # Sprint15 (D-S15-1): capture_mode video dispatches to the video runtime
    # right after config load + command-overlay resolution. Cron line, lock,
    # and overlay doctrine unchanged; a video unit and a stills unit differ
    # by one YAML value. Lazy import keeps the stills path untouched.
    if settings["capture_mode"] == "video":
        import video_recorder
        try:
            settings["video"] = video_recorder.load_video_config(args.config_path)
        except Exception as exc:
            print(f"[RC][ERROR] video config load/validation failed: {exc}",
                  file=sys.stderr)
            return 2
        print_resolved_settings(settings)
        video_recorder.print_video_settings(settings["video"])
        if args.print_config:
            return 0
        try:
            return video_recorder.run_video_mode(
                settings,
                transmit=args.transmit,
                bm_commands_cfg=bm_commands_cfg,
                command_state=command_state,
                bench_commands=args.bench_commands,
            )
        except Exception as exc:
            print(f"[RC][ERROR] video mode failed: {exc}", file=sys.stderr)
            return 1

    if args.print_config:
        print_resolved_settings(settings)
        return 0

    if settings["capture_mode"] != "progressive_jpeg":
        print(f"[RC] capture_mode={settings['capture_mode']} — RC inactive; "
              "known-good HEIC path owns this cycle. Nothing to do.")
        return 0

    # Sprint12: consume a pending one-shot trg (D-S12-3/4/5). Only a
    # --transmit boot services it; the flags force the one-shot window
    # bypass and (trg 1) the capture-only path.
    trigger_flags = {"skip_time_window": False, "capture_only": False}
    if command_state is not None:
        settings, trigger_flags = cmd_hooks.service_pending_trigger(
            settings, command_state, transmit=args.transmit)

    print_resolved_settings(settings)
    try:
        run_cycle(
            settings,
            transmit=args.transmit,
            capture_only=args.capture_only or trigger_flags["capture_only"],
            native_path=args.compress_only,
            skip_time_window=(args.skip_time_window
                              or trigger_flags["skip_time_window"]),
            output_dir=args.output_dir,
            bm_commands_cfg=bm_commands_cfg,
            command_state=command_state,
            bench_commands=args.bench_commands,
            **cycle_overrides,
        )
    except Exception as exc:
        print(f"[RC][ERROR] cycle failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

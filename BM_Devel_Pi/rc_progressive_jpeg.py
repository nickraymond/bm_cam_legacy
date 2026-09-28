#!/usr/bin/env python3
# filename: rc_progressive_jpeg.py
# description: Sprint08 progressive-JPEG release-candidate entry point (M7 orchestrator).
"""
Sprint08 progressive-JPEG RC entry script — M7 cycle orchestrator.

Config-gated runtime path (capture_mode: progressive_jpeg | video). The old
HEIC path (main_pi_camera.py) was deleted in Sprint26 S1; `capture_mode: heic`
now means "nothing to do" (see main()). This script wires the RC modules into
one cycle:

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
  --print-config --json     every config loader's output as one JSON line (Sprint26 S2b)
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
import copy
import json
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
import rc_heal
import rc_media_key
import rc_transmit_phase
import bm_port
from bm_port import (
    DEFAULT_BUFFER_SIZE,
    DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS,
    apply_bm_serial_runtime_settings,
)
from rc_capture import (
    _load_libcamera_metadata_json,
    _run_native_full_capture,
    _select_camera_command,
    generate_filename,
    update_capture_metadata,
)
from rc_telemetry import (
    IMAGE_DIRECTORY,
    collect_storage_health,
    debug_print,
    get_cpu_temperature,
    get_hostname,
    get_software_sha,
    log_message,
    send_wake_status,
)
from rc_jpeg_encoder import encode_progressive, output_size_for_crop, prepare_source
from rc_power_halt import perform_power_halt
from rc_port_owner import PortOwner
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


def _load_media_key_cfg(config_path):
    """media_key island. A YAML that still enables the retired 3-char media_gid
    gets a loud warning and is otherwise ignored (never a failed boot)."""
    rc_media_key.warn_retired_media_gid(config_path)
    return rc_media_key.load_media_key_config(config_path)


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
        # Sprint25 S4 rev 5 media key island (rc_media_key): absent/off == legacy.
        "media_key_cfg": _load_media_key_cfg(config_path),
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
        print(f"[RC] capture_mode={s['capture_mode']} (RC inactive: the heic path was retired in "
              "Sprint26; set capture_mode to progressive_jpeg or video)")

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
    return bm_port.get().spotter_tx


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


def _save_local_tail(daemon, summary, budget, *, bm_commands_cfg, clock, sleep_fn, supervised):
    """The end of a save_local action: deferred acks out, then the listen tail
    (per_boot only; in stay_on post_transmit_listen does nothing, the idle loop
    listens)."""
    cmd_hooks.flush_acks(daemon, summary, clock=clock, sleep_fn=sleep_fn,
                         label="save_local ack flush")
    cmd_hooks.post_transmit_listen(daemon, bm_commands_cfg or {}, summary, budget,
                                   clock=clock, sleep_fn=sleep_fn, supervised=supervised)


def _save_local_still(settings, summary, daemon, budget, *, supervised, source, native_path,
                      image_stem, capture_info, output_dir, time_source, transmit,
                      bm_commands_cfg, bm_open_fn, clock, sleep_fn, sent):
    """Sprint26 S3c still x save_local (DESIGN §4 Actions; PLAN_S3c.md J2 as
    amended): ONE encode of the prepared crop at still.save.quality (the ladder
    only exists to fit the uplink), saved atomically next to the native with its
    sidecar ("output": "save_local"). No START/chunks/END, no camera_log.csv
    row, no sent record. Pending heals go out (C14), then acks and the tail."""
    quality = int(supervised.save_quality)
    encode = encode_progressive(source, quality, settings["pacing_chunk_b64_chars"])
    final_name = f"{image_stem}_compressed.jpg"
    final_path = os.path.join(output_dir, final_name)
    import atomic_io
    from rc_capture import _json_safe_metadata
    atomic_io.write_bytes(final_path, encode["jpeg_data"])
    summary["final_path"] = final_path
    metadata = {
        "software_sha": get_software_sha(),
        "hostname": get_hostname(),
        "metadata_schema": "bmcam_runtime_sidecar_v1",
        "metadata_source": "rc_progressive_jpeg",
        "utc_capture_timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "capture_mode": "progressive_jpeg",
        "img_format": "pjpg",
        "output": "save_local",
        "time_source": time_source or "system",
        "jpeg_quality_used": quality,
        "jpeg_bytes": encode["jpeg_bytes"],
        "jpeg_sha256": encode["jpeg_sha256"],
        "crop_native_xywh": list(settings["crop_native_xywh"]),
        "output_size": list(settings["output_size"]),
        "native_path": native_path,
        **{k: v for k, v in capture_info.items() if k != "requested_camera_controls"},
        **_load_libcamera_metadata_json(capture_info.get("metadata_json")),
        **collect_storage_health(),
    }
    # The sidecar's own format (rc_capture.save_capture_metadata), written
    # atomically: for save_local this pair IS the product (§5 C13).
    atomic_io.write_text(final_path + ".capture_metadata.json",
                         json.dumps(_json_safe_metadata(metadata), sort_keys=True))
    summary["saved"] = {"quality": quality, "jpeg_bytes": encode["jpeg_bytes"],
                        "jpeg_sha256": encode["jpeg_sha256"],
                        "time_source": metadata["time_source"]}
    summary["stage"] = "saved"
    print(f"[RC] saved (save_local): {final_path} ({encode['jpeg_bytes']} B at q{quality}, "
          f"time_source={metadata['time_source']}) + native {native_path}")
    import rc_supervisor
    if rc_supervisor.save_local_heals(daemon, settings, summary, budget, transmit=transmit,
                                      tx_open_fn=bm_open_fn, clock=clock, sleep_fn=sleep_fn,
                                      run=supervised.run):
        sent = True
    summary["uplinked"] = sent
    _save_local_tail(daemon, summary, budget, bm_commands_cfg=bm_commands_cfg,
                     clock=clock, sleep_fn=sleep_fn, supervised=supervised)
    return summary


def _still_storage_guard(settings, summary, supervised, output_dir):
    """Sprint26 S3c (PLAN_S3c.md §5 C3-C5): the stills storage guard, before
    the capture of every supervisor stills action (both outputs; the legacy
    runtime never runs it). Prunes old stills against storage.* and
    sets supervised.storage_reason. -> True when the SD is still over a limit
    after pruning (a save_local action then refuses the capture; a transmit
    action only warns). Never raises: a guard failure must not cost the
    capture. The summary gains "storage" only when the limits were exceeded,
    so an under-limit action's summary is unchanged."""
    if supervised is None or supervised.storage_cfg is None:
        return False
    import rc_still_storage
    try:
        mk = rc_media_key.load_media_key_config(settings["config_path"])
        result = rc_still_storage.ensure_room(
            output_dir, supervised.storage_cfg, sent_dir=mk["sent_dir"],
            retain_days=mk["retain_days"])
    except Exception as exc:
        print(f"[STORE][WARN] stills storage guard skipped ({type(exc).__name__}: {exc})")
        supervised.storage_reason = None      # unknown: never a stale storage_full
        return False
    if result["over"]:
        summary["storage"] = result
    supervised.storage_reason = "storage_full" if result["full"] else None
    if result["full"] and supervised.output == "transmit":
        print("[STORE][WARN] SD still over its limit; a transmitting unit captures anyway "
              "(PLAN_S3c §5 C4)")
    return result["full"]


def still_action(
    settings, summary, daemon, budget,
    *,
    transmit,
    capture_only,
    native_path,
    skip_time_window,
    output_dir,
    capture_fn,
    bm_open_fn,
    wake_fn,
    sleep_fn,
    clock,
    bm_commands_cfg,
    bench_commands,
    grid_clock_fn,
    supervised=None,
):
    """The still action (Sprint26 S3a, DESIGN_supervisor.md §4 "Actions"): the body
    of one stills cycle, from the schedule gate to the listen tail, moved here
    verbatim from run_cycle. The daemon and the budget come from the caller; the
    caller also owns shutdown -> close -> halt. Returns the summary it fills."""
    # Schedule gate — transmit runs only (manual/bench modes must not
    # touch the BM bus; the Spotter-time read opens the UART). With
    # the daemon active the gate reads Spotter time over the SHARED
    # port instead of opening its own (D11).
    gate_info, gate_mono = None, clock()
    # Sprint26 W6: under the supervisor every transmitting action reads the
    # Spotter time (and steps the clock on drift), even when the window is
    # bypassed (trg, --skip-time-window); only the verdict is ignored then,
    # as the video cycle always did. Legacy skips the read on a bypass.
    bypass_verdict = supervised is not None and skip_time_window
    # Sprint26 S3c: a save_local action (supervisor only) saves instead of
    # sending; `sent` tracks whether it put anything on the uplink (§5 C1).
    save_local = supervised is not None and getattr(supervised, "save_local", False)
    time_source = None
    sent = False
    if transmit and settings["enforce_time_window"] and (not skip_time_window or bypass_verdict):
        gate_kwargs = (supervised.gate_kwargs(daemon, settings) if supervised is not None
                       else cmd_hooks.gate_kwargs_for(daemon, settings))
        allowed, info = should_transmit_now_from_schedule(
            settings["config_path"],
            **gate_kwargs
        )
        # Sprint11 C2: the gate's Spotter read is also the grid clock.
        # Pin it to a monotonic instant HERE and extrapolate later; the
        # transmit decision happens minutes after this read.
        gate_info, gate_mono = info, clock()
        cmd_hooks.boot_mark("spotter_utc_read")
        time_source = info.get("source_time")
        if save_local and info.get("spotter_time_error"):
            # S3c §5 C9: a wrong timestamp is recoverable, a lost picture is not.
            # The window is enforced only on a Spotter time.
            print(f"[RC][WARN] save_local: Spotter time read failed "
                  f"({info['spotter_time_error']}); saving on the Pi clock, window not enforced")
            allowed, time_source = True, "system"
        if bypass_verdict:
            print(f"[RC] schedule gate: {info.get('reason')} (window bypassed; time read only)")
            allowed = True
        summary["schedule_allowed"] = allowed
        if not bypass_verdict:
            print(f"[RC] schedule gate: {info.get('reason')}")
        if not allowed and supervised is not None and supervised.quiet_skip:
            # Sprint26 S3b H3: in stay_on only the first skip of a run of
            # scheduled skips sends <WS a=skip_win>; later ones log only.
            print("[RC] window skip: <WS> not sent (stay_on: the first skip of this run "
                  "already was)")
            return summary
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
            if supervised is not None:
                # Sprint26 W3 (DESIGN §4): the listen tail runs after a window
                # skip too, so a command sent this hour is not lost. Legacy
                # returns here with no tail.
                cmd_hooks.post_transmit_listen(
                    daemon, bm_commands_cfg or {}, summary, budget,
                    clock=clock, sleep_fn=sleep_fn, supervised=supervised,
                )
            return summary

    if save_local and transmit and not settings["enforce_time_window"]:
        # S3c §5 C9: the window is off, so the gate read nothing; filenames are
        # capture time and a Pi has no RTC.
        time_source = supervised.save_local_time_read(daemon, settings)

    # Sprint26 S3c: the stills storage guard (supervisor only; both outputs).
    full = _still_storage_guard(settings, summary, supervised, output_dir)
    # stay_on save_local: no per-action <WS> (the heartbeat is the liveness,
    # PLAN_S3c §5 C6); per_boot keeps today's a=cap as its one status line.
    wake_line = transmit and not (save_local and supervised.run == "stay_on")
    if save_local and full:
        # A save_local action refuses the capture on a full SD (a transmit
        # action only warns, _still_storage_guard). per_boot says so on the
        # wire; stay_on through the heartbeat's r=storage_full.
        summary["error"] = summary["stage"] = "storage_full"
        print("[RC][ERR] save_local: SD over its limit after pruning; capture refused")
        if wake_line:
            try:
                wake_fn(action="skip_err", timezone_name=settings["timezone"], local_time=None,
                        window_start=settings["window_start"],
                        window_end=settings["window_end"],
                        image_res_key=f"{settings['output_size'][0]}x{settings['output_size'][1]}",
                        image_quality=settings["q_max"], reason="storage_full")
                sent = True
            except Exception as exc:
                debug_print(f"Wake status send failed, continuing safely: {exc}")
        summary["uplinked"] = sent
        _save_local_tail(daemon, summary, budget, bm_commands_cfg=bm_commands_cfg,
                         clock=clock, sleep_fn=sleep_fn, supervised=supervised)
        return summary

    if wake_line:
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
            sent = True
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
        if save_local:
            summary["uplinked"] = sent
        return summary

    if save_local:
        return _save_local_still(
            settings, summary, daemon, budget, supervised=supervised, source=source,
            native_path=native_path, image_stem=image_stem, capture_info=capture_info,
            output_dir=output_dir, time_source=time_source, transmit=transmit,
            bm_commands_cfg=bm_commands_cfg, bm_open_fn=bm_open_fn, clock=clock,
            sleep_fn=sleep_fn, sent=sent)

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
                clock=clock, sleep_fn=sleep_fn, supervised=supervised,
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

    # Sprint25 S5: this wake's rsd heals go right before START; plan them
    # now so the lane plan below counts them. None without a daemon.
    heals = rc_heal.begin_wake(
        daemon, settings, summary,
        pump_fn=cmd_hooks.make_pending_pump_fn(daemon, summary))
    heal_msgs = heals.planned_msgs if heals is not None else 0

    # --- Sprint11 C2: wait for a clean lane on the 5-minute grid -----
    # Everything above this line is cycle-relative; this is the ONE
    # place that reasons in absolute UTC (DESIGN D1).
    phase_cfg = settings.get("transmit_phase_cfg") or {}
    if phase_cfg.get("enabled"):
        burst_s = rc_transmit_phase.burst_seconds_for(
            encode["message_count"] + heal_msgs, settings["pacing_delay_seconds"],
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

    media_key = rc_media_key.prepare_keyed_send(
        settings, gate_info=gate_info, daemon=daemon,
        stem=os.path.splitext(final_name)[0], fmt="pjpg", filename=final_name,
        payload=encode["jpeg_data"], payload_path=final_path,
        chunk_b64_chars=settings["pacing_chunk_b64_chars"])
    if supervised is not None:
        # The action log's media_key (the stills summary does not carry it;
        # adding it there would change every stills golden).
        supervised.media_key = media_key
    tx = bm_open_fn(settings["config_path"])
    cmd_hooks.boot_mark("transmit_start")
    if heals is not None and heal_msgs:
        # Reserve the image's whole burst: START + chunks + END (+ a=inc).
        heals.send_before_start(
            tx, budget,
            reserve_msgs=encode["message_count"] + 2 + (0 if selection["fits"] else 1),
            delay_seconds=settings["pacing_delay_seconds"], sleep_fn=sleep_fn)
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
        # Sprint26 W2 (DESIGN §4 "Acks"): the supervisor ALWAYS defers them
        # (video already did); legacy keeps the YAML switch.
        ack_drain_fn=cmd_hooks.make_ack_drain_fn(
            daemon, summary, clock=clock,
            defer=supervised is not None or bool((bm_commands_cfg or {}).get(
                "defer_acks_during_transmit"))),
        pending_pump_fn=cmd_hooks.make_pending_pump_fn(daemon, summary),
        media_key=media_key,
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
    # Sprint25 S5: one <HL> per key after END, paced, on the image's tx.
    if heals is not None:
        heals.send_status_after_end(
            tx, budget, wake_key=media_key,
            delay_seconds=settings["pacing_delay_seconds"], sleep_fn=sleep_fn)
    # Sprint11 C4/D6: bounded listen tail. This is when the mailbox
    # drain our own transmit triggered actually arrives (finding 006).
    cmd_hooks.post_transmit_listen(
        daemon, bm_commands_cfg or {}, summary, budget,
        clock=clock, sleep_fn=sleep_fn, supervised=supervised,
    )

    return summary


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
    bm_close_fn=bm_port.close,
    wake_fn=send_wake_status,
    halt_fn=perform_power_halt,
    sleep_fn=time.sleep,
    clock=time.monotonic,
    bm_commands_cfg=None,
    command_state=None,
    bench_commands=False,
    daemon_factory=cmd_hooks.default_daemon_factory,
    grid_clock_fn=rc_transmit_phase.acquire_grid_clock,
    supervised=None,
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

    def end_line():
        # summary holds the budget the cycle actually charged; a win
        # command re-overlays settings mid-cycle but never rebuilds the
        # running CycleBudget (Phase B nit, 2026-07-27).
        return (f"[RC] cycle end: elapsed={budget.elapsed_s():.1f}s of "
                f"{summary['budget_seconds']}s; halt={summary['halt_result']['action']}")

    def close_warn(exc):
        debug_print(f"BM serial close failed: {exc}")

    if supervised is None:
        # Sprint26 S3a: the port owner runs daemon start and shutdown -> close
        # -> halt. Legacy order kept: the daemon starts BEFORE the try, so a
        # UART failure here skips close and halt (PLAN_S3a.md G5).
        owner = PortOwner(settings, bm_close_fn=bm_close_fn, halt_fn=halt_fn,
                          clock=clock, sleep_fn=sleep_fn, log_fn=debug_print)
        owner.begin()
        daemon = None
        if cmd_hooks.should_run_daemon(bm_commands_cfg, command_state, transmit, bench_commands):
            daemon = owner.start_daemon(daemon_factory, bm_commands_cfg, command_state)

        # M1: ONE budget, charged from here on.
        budget = CycleBudget(
            settings["budget_seconds"], settings["pacing_delay_seconds"], clock=clock
        )
    else:
        # Sprint26 S3a supervisor: the boot owns the budget (anchored before the
        # daemon, G1), the daemon and shutdown -> close -> halt (always, G5).
        daemon, budget = supervised.start(
            summary, clock=clock, sleep_fn=sleep_fn, halt_fn=halt_fn,
            bm_close_fn=bm_close_fn, daemon_factory=daemon_factory, log_fn=debug_print,
            close_warn=close_warn, end_line=end_line)
        # W4: commands already queued apply THIS boot; then the pending trg.
        settings, trigger_flags = supervised.boot_drain(settings, summary, sleep_fn)
        skip_time_window = skip_time_window or trigger_flags["skip_time_window"]
        capture_only = capture_only or trigger_flags["capture_only"]
    print(f"[RC] cycle start: budget={settings['budget_seconds']}s "
          f"pacing={settings['pacing_delay_seconds']}s/msg")

    try:
        return still_action(
            settings, summary, daemon, budget,
            transmit=transmit, capture_only=capture_only, native_path=native_path,
            skip_time_window=skip_time_window, output_dir=output_dir,
            capture_fn=capture_fn, bm_open_fn=bm_open_fn, wake_fn=wake_fn,
            sleep_fn=sleep_fn, clock=clock, bm_commands_cfg=bm_commands_cfg,
            bench_commands=bench_commands, grid_clock_fn=grid_clock_fn,
            supervised=supervised,
        )

    finally:
        # Last command pickup + reader stop before the port closes, then
        # M6: halt runs on success AND failure/exhaustion paths (never raises).
        # Under the supervisor, run_per_boot does this after the action.
        if supervised is None:
            owner.finish(summary, close_port=transmit or daemon is not None,
                         close_warn=close_warn)
            print(end_line())


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

RUNTIMES = ("legacy", "supervisor")


def resolve_runtime(cli_value, boot):
    """Sprint26 S3a (PLAN_S3a.md G3): which runtime runs this boot.
    Precedence: --runtime > commands.runtime in the active config v2 file >
    legacy (v1-only units, safe fallbacks). -> (runtime, source)."""
    if cli_value:
        return cli_value, "cli"
    values = getattr(boot, "values", None) or {}
    value = values.get("commands.runtime")
    if value in RUNTIMES:
        return value, f"config ({boot.level})"
    return "legacy", "default"


def resolve_run_mode(boot, runtime):
    """Sprint26 S3b (PLAN_S3b.md H1): per_boot or stay_on, and the stay_on
    timers, from the active config v2 file. -> (run, interval_s, heartbeat_s).
    stay_on needs the supervisor: the loader refuses a FILE that pairs it with
    legacy, but `--runtime legacy` can still override the file, so that case
    runs per_boot here, loudly."""
    values = getattr(boot, "values", None) or {}
    run = values.get("mode.run", "per_boot")
    if run != "stay_on":
        return "per_boot", 0, 0
    if runtime != "supervisor":
        print("[RUN][WARN] mode.run stay_on needs the supervisor runtime; "
              f"runtime is {runtime}: running per_boot")
        return "per_boot", 0, 0
    return "stay_on", int(values.get("mode.interval_s", 0)), int(values.get("mode.heartbeat_s", 0))


def resolve_output(boot, runtime):
    """Sprint26 S3c (PLAN_S3c.md J1): transmit or save_local, from the active
    config v2 file. save_local needs the supervisor: the loader refuses a FILE
    that pairs it with legacy, but `--runtime legacy` can still override the
    file, so that case transmits here, loudly (the legacy runtime has no
    save_local; transmit is the only automatic revert until S4's cfm)."""
    values = getattr(boot, "values", None) or {}
    if values.get("mode.output", "transmit") != "save_local":
        return "transmit"
    if runtime != "supervisor":
        print("[OUTPUT][WARN] mode.output save_local needs the supervisor runtime; "
              f"runtime is {runtime}: TRANSMITTING this boot")
        return "transmit"
    return "save_local"


def configure_output(sup, boot, output):
    """S3c (PLAN_S3c.md §5 C2/C3): put the output and its knobs on the
    supervisor Boot, from the v2 values (registry defaults for a v1-only unit
    run with --runtime supervisor). The stills guard and the video ring share
    ONE limit pair, storage.* (registry v5; video.storage.* before S4)."""
    import config_registry as R
    values = getattr(boot, "values", None) or {}

    def value(path):
        return values.get(path, R.BY_PATH[path].default)
    sup.output = output
    sup.save_quality = int(value("still.save.quality"))
    sup.storage_cfg = {"max_used_pct": float(value("storage.max_used_pct")),
                       "min_free_gb": float(value("storage.min_free_gb")),
                       "ring_dry_run": bool(value("storage.ring_dry_run"))}
    return sup


def _stay_on_settings_fn(settings, reresolve_fn):
    """Fresh settings for each stay_on action: the YAML base + the command
    overlay as it is NOW (commands applied while idle govern the next action,
    §4 "Decision points"); a one-shot trg key never carries over."""
    def fresh():
        if reresolve_fn is not None:
            return reresolve_fn(copy.deepcopy(settings))
        return copy.deepcopy(settings)
    return fresh


def _heartbeat_fn(image_res_key, image_quality, action="idle", reason=None, reason_fn=None):
    """One <WS a=idle> (PLAN_S3b.md H5): today's wake-status fields, local
    time from the (Spotter-set) system clock. up=/cfg= arrive with W8 (S4).
    The crash-loop fallback sends the same line with a=crashloop (H7).
    reason_fn (S3c §5 C6): the reason is read at SEND time (storage_full while
    the SD is over its limit), not fixed when the heartbeat is built."""
    def send(settings):
        nonlocal reason
        if reason_fn is not None:
            reason = reason_fn()
        from zoneinfo import ZoneInfo
        from rc_telemetry import send_wake_status
        local = datetime.now(ZoneInfo(settings["timezone"])).isoformat()
        return send_wake_status(
            action=action, timezone_name=settings["timezone"], local_time=local,
            window_start=settings["window_start"], window_end=settings["window_end"],
            image_res_key=image_res_key(settings), image_quality=image_quality(settings),
            reason=reason)
    return send


def _crashloop_notice(sup, settings, image_res_key, image_quality):
    """H7: the crash-loop fallback's one <WS a=crashloop>, sent as soon as the
    port is up (before the action), so the backend sees why the unit went
    quiet. Never raises."""
    send = _heartbeat_fn(image_res_key, image_quality, action="crashloop", reason="restarts")

    def notice():
        try:
            send(settings)
            print("[RUN] crash-loop fallback: <WS a=crashloop> sent")
        except Exception as exc:
            print(f"[RUN][WARN] <WS a=crashloop> not sent ({type(exc).__name__}: {exc})")
    sup.on_process_start = notice


def _run_stay_on(sup, action_fn, settings, reresolve_fn, run_cfg, heartbeat_fn,
                 cycle_overrides, heal_tx_open_fn):
    """Hand the process to the stay_on loop with the same injected clock /
    sleep / halt / close / daemon factory the actions use (golden harness)."""
    import rc_supervisor
    _run, interval_s, heartbeat_s = run_cfg
    return rc_supervisor.run_stay_on(
        sup, action_fn, settings_fn=_stay_on_settings_fn(settings, reresolve_fn),
        interval_s=interval_s, heartbeat_s=heartbeat_s, heartbeat_fn=heartbeat_fn,
        clock=cycle_overrides.get("clock", time.monotonic),
        sleep_fn=cycle_overrides.get("sleep_fn", time.sleep),
        halt_fn=cycle_overrides.get("halt_fn", perform_power_halt),
        bm_close_fn=cycle_overrides.get("bm_close_fn", bm_port.close),
        daemon_factory=cycle_overrides.get("daemon_factory"),
        heal_tx_open_fn=heal_tx_open_fn)


def rc_supervisor_mod():
    import rc_supervisor
    return rc_supervisor


def main(argv=None, **cycle_overrides):
    cmd_hooks.boot_mark("main_entry")   # Sprint25 S3 benchmark segment
    parser = argparse.ArgumentParser(
        description="Sprint08 progressive-JPEG RC cycle (config-gated; see module docstring)."
    )
    parser.add_argument("--config-path", default=DEFAULT_CONFIG_PATH,
                        help="Path to camera_schedule.yaml")
    parser.add_argument("--print-config", action="store_true",
                        help="Resolve + print settings, run nothing")
    parser.add_argument("--config-format", choices=("auto", "v1", "v2"), default="auto",
                        help="auto (default): config v2 when camera_config.yaml sits "
                             "beside --config-path, else the v1 file; v1/v2 force one "
                             "(Sprint26 S2d)")
    parser.add_argument("--json", action="store_true",
                        help="With --print-config: print every config loader's output "
                             "as one JSON line (last line of stdout), run nothing")
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
    parser.add_argument("--bench-drop-chunks", default=None, metavar="N[,N...]",
                        help="BENCH ONLY (video_tx): do not put these clip chunk "
                             "indices on the wire (slots still paced) so the "
                             "backend holds a partial to heal (Sprint25 S5)")
    parser.add_argument("--skip-time-window", action="store_true",
                        help="Bench override: skip the Spotter-time transmit gate")
    parser.add_argument("--output-dir", default=IMAGE_DIRECTORY,
                        help="Directory for final JPEG + sidecar")
    parser.add_argument("--crashloop", action="store_true",
                        help="Set by rc_run_capture_cycle.sh after 5 stay_on restarts in "
                             "10 min (Sprint26 S3b H7): run per_boot with the halt forced "
                             "to dry-run and send one <WS a=crashloop>, so a wall-powered "
                             "unit stays reachable")
    parser.add_argument("--runtime", choices=RUNTIMES, default=None,
                        help="Override commands.runtime for this run: legacy (the S2 cycle "
                             "scripts) or supervisor (Sprint26 S3). Default: the config "
                             "value; v1-only units run legacy.")
    args = parser.parse_args(argv)
    # Sprint26 S2d (PLAN_S2.md G2): on a config-v2 unit the v1 loaders below
    # read a v1-shaped render of camera_config.yaml on tmpfs; with no v2 file
    # nothing changes. Never raises: a bad v2 file falls back (v1 file, then
    # last-known-good, then safe-minimal = nothing to do this boot).
    boot = None
    v1_config_path = args.config_path
    if args.config_format != "v1":
        import config_v2
        if args.config_format == "v2" and not os.path.exists(os.path.join(
                os.path.dirname(os.path.abspath(args.config_path)), config_v2.V2_NAME)):
            print(f"[RC][ERROR] --config-format v2: no {config_v2.V2_NAME} beside "
                  f"{args.config_path}", file=sys.stderr)
            return 2
        try:
            selected, boot = config_v2.select_for_legacy_runtime(
                args.config_path, args.config_format, persist=not args.print_config)
        except Exception as exc:      # never brick: the v1 file, as before S2
            print(f"[CFG][ERR] config v2 selection failed ({type(exc).__name__}: {exc}); "
                  f"running the v1 file {args.config_path}")
            selected = args.config_path
        if selected is None:
            print("[RC] SAFE-MINIMAL: no usable config; nothing to do this boot.")
            return 0
        args.config_path = selected
    if args.json:
        # Sprint26 S2b: machine-readable config for deploy/migration parity.
        # Before any other step, so it has zero side effects (no network
        # default, no daemon); exit 2 when the stills settings do not resolve,
        # like the text --print-config.
        if not args.print_config:
            print("[RC][ERROR] --json requires --print-config", file=sys.stderr)
            return 2
        import config_dump
        dump = config_dump.collect(args.config_path)
        print(config_dump.to_json_line(dump))
        return 2 if "error" in dump["resolved"] else 0
    runtime, runtime_source = resolve_runtime(args.runtime, boot)
    # Sprint26 S4 b.1 (PLAN_S4.md G1/G3): the supervisor on a migrated unit runs
    # the EFFECTIVE config (YAML ⊕ command overlay, validated), rendered to the
    # tmpfs file the v1 loaders read; the legacy runtime keeps base + v8 bindings.
    v9_eff, v9_base, v9_state = None, None, None
    if (runtime == "supervisor" and boot is not None and not args.print_config
            and args.config_path != v1_config_path):
        import supervisor_config
        v9_base = dict(boot.values or {})
        v9_source = {k: v for k, v in (getattr(boot.config, "source", None) or {}).items()
                     if v in ("yaml", "default")}
        try:
            env = __import__("config_validate").probe_env(
                [v9_base.get("schedule.timezone")])
            v9_eff = supervisor_config.apply(boot, args.config_path, env=env)
        except Exception as exc:        # never brick: base + v8 bindings, as in S3
            print(f"[CFG][ERR] effective config failed ({type(exc).__name__}: {exc}); "
                  "running the YAML base with the v8 overlay")
            v9_eff = None
        v9_state = None
        if v9_eff is not None and v9_eff.values.get("mode.media") != "video_logger":
            # S4 b.5 (PLAN_S4.md G10, consensus R23): ONE V9State for the process,
            # built BEFORE any port opens and even with commands off (the key that
            # turned them off is itself guarded). The v8 section folds once (G2);
            # a counted run (--transmit) increments the boot counter and
            # evaluates guarded reverts; a revert re-renders the effective config.
            try:
                import command_guards
                import command_state_v9
                import config_migrate
                v9_state = command_state_v9.V9State(
                    supervisor_config.state_path_for(v9_base),
                    trigger_validator=lambda kv, v: __import__("command_v9").check_persisted_kv(kv, v))
                for path, old, new in v9_state.fold_v8(config_migrate.overlay_from_v8):
                    print(f"[CMD] v8 fold: {path}: {old!r} -> {new!r}")
                if args.transmit:
                    reverted = command_guards.count_boot(v9_state)
                    print(f"[GUARD] boot {v9_state.boot_counter}: "
                          f"{len(v9_state.guarded)} guarded key(s) pending"
                          + (f", reverted {reverted}" if reverted else ""))
                    if reverted:
                        boot.values = v9_base
                        try:
                            v9_eff = supervisor_config.apply(boot, args.config_path, env=env)
                        except Exception as exc:
                            # S4b review R2-6: never run the reverted value again;
                            # fall back to the YAML base render.
                            print(f"[GUARD][ERR] re-render after the revert failed ({exc}); "
                                  "running the YAML base")
                            import atomic_io
                            atomic_io.write_text(args.config_path,
                                                 supervisor_config.render_values(v9_base))
                            v9_eff = supervisor_config.resolve(v9_base, None, env=env)
            except Exception as exc:     # never brick: the counter/revert is logged, the boot runs
                print(f"[GUARD][ERR] boot count / revert failed ({type(exc).__name__}: {exc})")
        if v9_eff is not None:
            boot.values = v9_eff.values      # mode/output read the effective config (G3)
    run_cfg = ("per_boot", 0, 0)
    output = "transmit"
    if not args.print_config:      # inspection output stays as before (settings goldens)
        print(f"[RUNTIME] {runtime} (source={runtime_source})")
        run_cfg = resolve_run_mode(boot, runtime)
        if args.crashloop:
            print("[RUN][WARN] crash-loop fallback: per_boot, halt forced to dry-run, one "
                  "<WS a=crashloop> (the wrapper saw 5 stay_on restarts in 10 min)")
            run_cfg = ("per_boot", 0, 0)
        if run_cfg[0] == "stay_on":
            print(f"[RUN] stay_on interval_s={run_cfg[1]} heartbeat_s={run_cfg[2]}")
        output = resolve_output(boot, runtime)
        if output != "transmit":
            print(f"[OUTPUT] {output}")
    bench_drop_chunks = None
    if args.bench_drop_chunks:
        try:
            bench_drop_chunks = sorted({int(x) for x in args.bench_drop_chunks.split(",")})
            if any(n < 0 for n in bench_drop_chunks):
                raise ValueError("negative index")
        except ValueError as exc:
            print(f"[RC][ERROR] --bench-drop-chunks {args.bench_drop_chunks!r}: {exc}",
                  file=sys.stderr)
            return 2

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
    reresolve_fn = None
    v9_on = v9_eff is not None and v9_state is not None
    if bm_commands_cfg["enabled"]:
        if v9_on:
            # S4 b.2c (PLAN_S4.md G1, B1): on the v9 path ONE V9State owns the
            # state file (heals, trigger, overlay, dedupe, guards); the v8
            # CommandState is never built here.
            import command_v9
            command_state = v9_state
        else:
            command_state = CommandState(path=bm_commands_cfg["state_path"])
        print(f"[CMD] bm_commands enabled: topic={bm_commands_cfg['topic']} "
              f"tail={bm_commands_cfg['post_transmit_listen_s']}s "
              f"defer_acks={bm_commands_cfg['defer_acks_during_transmit']} "
              f"state={command_state.path} (loaded from "
              f"{command_state.load_info['source']})")
        base_settings = copy.deepcopy(settings)
        if v9_eff is None:
            settings = _apply_command_overlay(settings, command_state)

        def _reresolve(current):
            """W4: the overlay re-read onto the YAML base after the boot drain.
            Only what main() adds AFTER the overlay is carried over (the video
            block); everything else comes fresh, so an override the new state
            no longer holds (e.g. camera_controls_override) is dropped."""
            fresh = _apply_command_overlay(copy.deepcopy(base_settings), command_state)
            if "video" in current:
                fresh["video"] = current["video"]
            return fresh
        reresolve_fn = _reresolve
        if v9_eff is not None:
            # b.1: the overlay reaches the settings through the render, re-built
            # from the state file at every decision point (no v8 bindings).
            reresolve_fn = supervisor_config.make_reresolve(
                v9_base, args.config_path, resolve_rc_settings, env=env)

    if args.crashloop:
        # H7: the halt is forced to dry-run for this boot, including after a
        # boot-drain re-resolve (an hlt command must not turn it back on).
        settings["power_halt_dry_run"] = True
        if reresolve_fn is not None:
            _inner_reresolve = reresolve_fn

            def reresolve_fn(current):
                return dict(_inner_reresolve(current), power_halt_dry_run=True)

    # Sprint16 (D-S16-3): apply the network boot default (fire-and-forget;
    # a WiFi problem must never cost a capture cycle). No island = no-op,
    # so pre-Sprint16 configs behave exactly as before. Skipped for
    # --print-config (inspection must have zero side effects).
    if not args.print_config:
        try:
            import network_config
            net_cfg = network_config.load_network_config(args.config_path)
            network_config.print_network_settings(net_cfg)
            network_config.apply_boot_default(net_cfg)
        except Exception as exc:
            print(f"[NET][WARN] network island skipped: {exc}")

    # Sprint15 (D-S15-1): capture_mode video dispatches to the video runtime
    # right after config load + command-overlay resolution. Cron line, lock,
    # and overlay doctrine unchanged; a video unit and a stills unit differ
    # by one YAML value. Lazy import keeps the stills path untouched.
    def _wire_v9(sup):
        """S4 (PLAN_S4.md G1): the v9 path on a supervisor Boot — replies,
        guards, the dispatcher, the media-aware trg kv builder (b.6a) and the
        other media's action for a one-shot media override (b.6b)."""
        if v9_eff is None:
            return
        sup.v9_replies = supervisor_config.v9_replies(v9_base, env=env)
        sup.guard_state = v9_state
        if not (v9_on and command_state is not None):
            return
        import rc_video_tx as _vtx
        import video_recorder as _vr

        def dry(fresh):
            return dict(fresh, power_halt_dry_run=True) if args.crashloop else fresh

        def one_shot(kv):
            path = supervisor_config.one_shot_render(v9_base, v9_state, kv, env=env)
            fresh = resolve_rc_settings(path)
            if kv.get("mode.media", sup.media) == "video":
                fresh["video"] = _vr.load_video_config(path)
                sup.one_shot_vtx = _vtx.load_video_tx_config(path)
            return dry(fresh)
        sup.one_shot_fn = one_shot
        sup.v9_dispatch_factory = lambda d: command_v9.Dispatcher(
            d, command_state, v9_base, env=env,
            service_key=command_v9.load_service_key(), base_source=v9_source)
        common = dict(transmit=args.transmit, bm_commands_cfg=bm_commands_cfg,
                      command_state=command_state, bench_commands=args.bench_commands)

        def alt_video(b, _settings=None):
            try:
                path = supervisor_config.one_shot_render(v9_base, v9_state,
                                                         {"mode.media": "video"}, env=env)
                s = resolve_rc_settings(path)
                s["video"] = _vr.load_video_config(path)
                vtx = _vtx.load_video_tx_config(path)
            except Exception as exc:
                raise rc_supervisor_mod().AltActionUnavailable(f"{type(exc).__name__}: {exc}")
            return _vtx.run_video_tx_cycle(dry(s), vtx, supervised=b,
                                           skip_time_window=args.skip_time_window,
                                           bench_drop_chunks=bench_drop_chunks, **common)

        def alt_still(b, _settings=None):
            try:
                path = supervisor_config.one_shot_render(v9_base, v9_state,
                                                         {"mode.media": "still"}, env=env)
                s = resolve_rc_settings(path)
            except Exception as exc:
                raise rc_supervisor_mod().AltActionUnavailable(f"{type(exc).__name__}: {exc}")
            return run_cycle(dry(s), supervised=b, capture_only=False,
                             skip_time_window=False, output_dir=args.output_dir,
                             **common, **cycle_overrides)
        sup.alt_actions = {"video": alt_video, "still": alt_still}
        eff = v9_eff.values
        sup.v9_limits = {k: eff[k] for k in ("commands.keepalive_s", "commands.keepalive_max_s",
                                             "commands.hold_max_min", "power.bus_always_on")}
        sup.video_duration_s = float(eff["video.send.duration_s"])
        inner = sup.reresolve_fn

        def reresolve(current):
            """S4b review R2-4: next-action keys that live outside `settings`
            (the clip config, the recording block, save quality, storage, the
            keep-alive limits) are re-read at every decision point too."""
            fresh = inner(current) if inner is not None else current
            now = supervisor_config.resolve(v9_base, supervisor_config.state_dict(v9_state),
                                            env=env).values
            sup.v9_limits = {k: now[k] for k in sup.v9_limits}
            sup._save_quality_base = int(now["still.save.quality"])
            sup.storage_cfg = {"max_used_pct": float(now["storage.max_used_pct"]),
                               "min_free_gb": float(now["storage.min_free_gb"]),
                               "ring_dry_run": bool(now["storage.ring_dry_run"])}
            sup.video_duration_s = float(now["video.send.duration_s"])
            if sup.media == "video" and now.get("mode.media") == "video":
                fresh["video"] = _vr.load_video_config(args.config_path)
                sup.current_vtx = _vtx.load_video_tx_config(args.config_path)
                sup.min_action_s = (float(sup.current_vtx["duration_s"])
                                    + float(sup.current_vtx["lead_in_s"])
                                    + rc_supervisor_mod().W10_VIDEO_MARGIN_S)
            return fresh
        sup.reresolve_fn = reresolve
        sup.alt_min_action_s = {
            "video": float(eff["video.send.duration_s"]) + float(eff["video.send.lead_in_s"])
            + rc_supervisor_mod().W10_VIDEO_MARGIN_S,
            "still": rc_supervisor_mod().W10_MIN_STILL_ACTION_S}

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

        # Sprint22: `video_tx.enabled: true` turns a video unit into "one boot =
        # record a short clip, send it over the BM uplink, halt". Island absent
        # or disabled (the default) -> the Sprint15 recorder below, unchanged.
        import rc_video_tx
        try:
            video_tx_cfg = rc_video_tx.load_video_tx_config(args.config_path)
        except Exception as exc:
            print(f"[RC][ERROR] video_tx config load/validation failed: {exc}",
                  file=sys.stderr)
            return 2
        rc_video_tx.print_video_tx_settings(video_tx_cfg)
        if video_tx_cfg["enabled"]:
            if args.print_config:
                return 0
            # Sprint25 S3: the one-clip video cycle now runs the command daemon
            # too (same D11 predicate as stills) — before S3 a video_tx wake
            # never listened, so no command (or heal request) could reach it.
            video_kwargs = dict(
                transmit=args.transmit, skip_time_window=args.skip_time_window,
                bm_commands_cfg=bm_commands_cfg, command_state=command_state,
                bench_commands=args.bench_commands, bench_drop_chunks=bench_drop_chunks)
            if runtime == "supervisor":
                import rc_supervisor
                sup = rc_supervisor.Boot(
                    settings, media="video", bm_commands_cfg=bm_commands_cfg,
                    command_state=command_state, transmit=args.transmit,
                    bench_commands=args.bench_commands, reresolve_fn=reresolve_fn)
                configure_output(sup, boot, output)
                _wire_v9(sup)
                w, h = video_tx_cfg["output_wh"]
                sup.min_action_s = (float(video_tx_cfg["duration_s"])
                                    + float(video_tx_cfg["lead_in_s"])
                                    + rc_supervisor.W10_VIDEO_MARGIN_S)
                if args.crashloop:
                    _crashloop_notice(sup, settings, lambda s: f"{w}x{h}", lambda s: None)
                if run_cfg[0] == "stay_on":
                    return _run_stay_on(
                        sup, lambda b, s: rc_video_tx.run_video_tx_cycle(
                            s, video_tx_cfg, supervised=b, **video_kwargs),
                        settings, sup.reresolve_fn, run_cfg,
                        _heartbeat_fn(lambda s: f"{w}x{h}", lambda s: None,
                                      reason_fn=lambda: sup.storage_reason), cycle_overrides,
                        rc_video_tx._default_tx_open)    # O5 heals: cellular-only, as clips
                # b.settings: a W10 extra action runs on the re-resolved overlay.
                summary = rc_supervisor.run_per_boot(
                    sup, lambda b: rc_video_tx.run_video_tx_cycle(
                        b.settings, video_tx_cfg, supervised=b, **video_kwargs))
            else:
                summary = rc_video_tx.run_video_tx_cycle(settings, video_tx_cfg, **video_kwargs)
            return 1 if summary.get("error") else 0

        if args.print_config:
            return 0
        if runtime == "supervisor":
            print("[RUNTIME] the recorder path (video_logger) runs the legacy code; it moves "
                  "under the supervisor in its own follow-up (PLAN_S3c.md §5 R1)")
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
        print(f"[RC] capture_mode={settings['capture_mode']} — RC inactive: the heic path was "
              "retired in Sprint26; set capture_mode to progressive_jpeg or video. Nothing to do.")
        return 0

    # Sprint12: consume a pending one-shot trg (D-S12-3/4/5). Only a
    # --transmit boot services it; the flags force the one-shot window
    # bypass and (trg 1) the capture-only path.
    trigger_flags = {"skip_time_window": False, "capture_only": False}
    if runtime == "supervisor" and args.capture_only and run_cfg[0] == "stay_on":
        # Sprint26 S3c (PLAN_S3c.md J9, §5 C11): --capture-only is a bench
        # one-shot; under the supervisor it runs per_boot (a stay_on loop would
        # drop the flag and capture forever).
        print("[RUN] --capture-only runs per_boot (one capture, then the halt as configured)")
        run_cfg = ("per_boot", 0, 0)
    # W4: under the supervisor the trg is serviced after the boot drain
    # (rc_supervisor.Boot.boot_drain), so a trg queued at boot fires this boot.
    if command_state is not None and runtime != "supervisor":
        settings, trigger_flags = cmd_hooks.service_pending_trigger(
            settings, command_state, transmit=args.transmit)

    print_resolved_settings(settings)
    cycle_kwargs = dict(
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
    try:
        if runtime == "supervisor":
            import rc_supervisor
            sup = rc_supervisor.Boot(
                settings, media="still", bm_commands_cfg=bm_commands_cfg,
                command_state=command_state, transmit=args.transmit,
                bench_commands=args.bench_commands, reresolve_fn=reresolve_fn)
            configure_output(sup, boot, output)
            _wire_v9(sup)
            still_rk = lambda s: f"{s['output_size'][0]}x{s['output_size'][1]}"  # noqa: E731
            if args.crashloop:
                _crashloop_notice(sup, settings, still_rk, lambda s: s["q_max"])
            if run_cfg[0] == "stay_on":
                stay_kwargs = {k: v for k, v in cycle_kwargs.items()
                               if k not in ("capture_only", "skip_time_window")}
                return _run_stay_on(
                    sup, lambda b, s: run_cycle(s, supervised=b, capture_only=False,
                                                skip_time_window=False, **stay_kwargs),
                    settings, sup.reresolve_fn, run_cfg,
                    _heartbeat_fn(still_rk, lambda s: s["q_max"],
                                  reason_fn=lambda: sup.storage_reason),
                    cycle_overrides, cycle_overrides.get("bm_open_fn", _default_bm_open))
            # run_cycle is looked up at call time (the golden harness wraps it).
            rc_supervisor.run_per_boot(
                sup, lambda b: run_cycle(b.settings, supervised=b, **cycle_kwargs))
        else:
            run_cycle(settings, **cycle_kwargs)
    except Exception as exc:
        print(f"[RC][ERROR] cycle failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

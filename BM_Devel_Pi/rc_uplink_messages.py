#!/usr/bin/env python3
# filename: rc_uplink_messages.py
# description: Sprint08 M4 — RC uplink message builders (START/END/incomplete wire fields).
"""
Sprint08 M4 — uplink message fields for the progressive-JPEG RC.

String builders ONLY: no serial, no hardware, no edits to the HEIC path's
builders in process_image_v2.py (their formatting helpers are imported and
reused; the HEIC messages stay byte-identical). The RC orchestrator (M7)
sends these strings over the existing spotter_tx path.

Wire additions (P4, Nick-approved; backend parsing is a SEPARATE
nereus-vision-dev/backend change — this module + tests define the fields):

  START `<START IMG> filename: F, timestamp: T, length: N, fmt=pjpg, q=13,
         att=2, cmp=1[, rsn=budget], rk=..., ...`   (285-byte budget)
  END   `<END IMG> filename: F, uart_duration_sec: S, sent_buffers: N,
         cpu_temp_c: C, fmt: pjpg, q: 13, att: 2, cmp: 1[, rsn: budget],
         <camera metadata>`                         (295-byte budget)
  INCOMPLETE (M5 emits before the bounded partial send; WS compact shape,
         new action `inc`):
        `<WS v=1 a=inc fmt=pjpg q=9 att=4 rsn=budget pln=128 snd=37 ...>`

Field semantics:
  fmt  image format, constant "pjpg" (progressive JPEG)
  q    JPEG quality actually used (HEIC cycles carried HEIC quality in the
       same key)
  att  encode attempts this cycle (M3 ladder walk length)
  cmp  1 = complete transmit planned/performed, 0 = incomplete cycle
  rsn  only when cmp=0: budget | cap | enc (see reason_code)
  pln  chunks the floor encode needs; snd = chunks the remaining budget allows

Assumption: RC fields are NEVER dropped by the payload-budget logic (they
are the point of the message); the existing low-value storage/context keys
drop first, same order as the HEIC START builder.
"""

from process_image_v2 import (
    _build_end_image_message,
    _clean_value,
    _start_metadata_pairs,
    compact_kv_message,
)

RC_IMAGE_FORMAT = "pjpg"

# M3 selector reason -> compact wire code. Anything unexpected maps to "err"
# so a future selector change can never build an unsendable message.
_REASON_CODES = {
    "no_fit_budget": "budget",
    "no_fit_cap": "cap",
    "no_time_for_encode": "enc",
}


def reason_code(selector_reason):
    """Map an M3 `reason` to its compact wire code."""
    return _REASON_CODES.get(str(selector_reason), "err")


def _rc_field_pairs(quality, enc_attempts, complete, reason):
    """The M4 field set shared by START and END. reason only rides when cmp=0."""
    pairs = [
        ("fmt", RC_IMAGE_FORMAT),
        ("q", int(quality)),
        ("att", int(enc_attempts)),
        ("cmp", 1 if complete else 0),
    ]
    if not complete:
        pairs.append(("rsn", _clean_value(reason if reason else "err", max_len=8)))
    return pairs


def build_rc_start_message(
    compressed_file_name,
    current_timestamp,
    num_buffers,
    *,
    quality,
    enc_attempts,
    complete,
    reason=None,
    start_metadata=None,
    max_payload_bytes=285,
):
    """Build the RC START IMG message (one unchunked BM message).

    Same wire shape and budget discipline as the HEIC START builder: base
    fields first, optional metadata dropped in the same fixed order when the
    budget is exceeded. The RC fields sit ahead of the optional metadata and
    are never dropped.
    """
    base_parts = [
        f"filename: {_clean_value(compressed_file_name, max_len=96)}",
        f"timestamp: {_clean_value(current_timestamp, max_len=32)}",
        f"length: {int(num_buffers)}",
    ]
    rc_parts = [
        f"{key}={_clean_value(value, max_len=12)}"
        for key, value in _rc_field_pairs(quality, enc_attempts, complete, reason)
    ]

    # Reuse the HEIC START metadata pairing; drop its "q" (the RC q above is
    # the JPEG quality actually used — one q key per message).
    optional = [(k, v) for k, v in _start_metadata_pairs(start_metadata) if k != "q"]

    # Same drop order as the HEIC builder (lowest-value fields first).
    drop_order = ["lg", "bf", "im", "st", "su", "tz", "hn", "ws", "we"]

    def render(selected_optional):
        parts = list(base_parts) + list(rc_parts)
        for key, value in selected_optional:
            max_len = 32
            if key == "sha":
                max_len = 12
            elif key == "tz":
                max_len = 24
            elif key in {"rk", "hn"}:
                max_len = 24
            parts.append(f"{key}={_clean_value(value, max_len=max_len)}")
        return "<START IMG> " + ", ".join(parts) + "\n"

    selected = list(optional)
    msg = render(selected)

    for key_to_drop in drop_order:
        if len(msg.encode("ascii", errors="ignore")) <= max_payload_bytes:
            break
        selected = [(k, v) for (k, v) in selected if k != key_to_drop]
        msg = render(selected)

    while len(msg.encode("ascii", errors="ignore")) > max_payload_bytes and selected:
        selected.pop()
        msg = render(selected)

    encoded = msg.encode("ascii", errors="ignore")
    if len(encoded) > max_payload_bytes:
        # Last-resort truncation, same safeguard as the HEIC builder. Base +
        # RC fields are short, so this should never fire in practice.
        encoded = encoded[: max_payload_bytes - 1] + b"\n"
        msg = encoded.decode("ascii", errors="ignore")

    return msg


def build_rc_end_message(
    compressed_file_name,
    *,
    uart_duration_sec,
    sent_buffers,
    cpu_temp_text,
    quality,
    enc_attempts,
    complete,
    reason=None,
    capture_metadata=None,
    max_payload_bytes=295,
):
    """Build the RC END IMG message.

    Wraps the existing END builder: core fields (never dropped) = the HEIC
    core + the RC fields; optional camera metadata stays budget-dropped
    exactly as before.
    """
    core_fields = [
        ("filename", compressed_file_name),
        ("uart_duration_sec", f"{float(uart_duration_sec):.1f}"),
        ("sent_buffers", int(sent_buffers)),
        ("cpu_temp_c", cpu_temp_text),
    ] + _rc_field_pairs(quality, enc_attempts, complete, reason)

    return _build_end_image_message(
        compressed_file_name,
        core_fields,
        capture_metadata=capture_metadata,
        max_payload_bytes=max_payload_bytes,
    )


def build_rc_incomplete_message(
    *,
    quality,
    enc_attempts,
    reason,
    planned_msgs,
    send_msgs,
    cpu_temp_text=None,
    software_sha=None,
    hostname=None,
    max_payload_bytes=280,
):
    """Build the distinct incomplete-cycle message (M5 emits it BEFORE the
    bounded partial send, so it arrives even if the budget dies mid-send).

    WS compact shape with new action `inc` — the backend probe/cycle-log tool
    already extracts WS key=value text. Telemetry extras (cpu/sha/hn) are
    caller-supplied so this builder stays pure; None values are omitted.
    """
    fields = [
        ("v", "1"),
        ("a", "inc"),
        ("fmt", RC_IMAGE_FORMAT),
        ("q", int(quality) if quality is not None else None),
        ("att", int(enc_attempts)),
        ("rsn", _clean_value(reason if reason else "err", max_len=8)),
        ("pln", int(planned_msgs) if planned_msgs is not None else None),
        ("snd", int(send_msgs)),
        ("ct", cpu_temp_text),
        ("sha", software_sha),
        ("hn", hostname),
    ]
    return compact_kv_message("WS", fields, max_payload_bytes=max_payload_bytes)

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
  END   byte-identical to the HEIC END (no RC fields — P4 revision, see
         build_rc_end_message docstring); camera metadata budget unchanged
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
    gid=None,
):
    """Build the RC START IMG message (one unchunked BM message).

    Same wire shape and budget discipline as the HEIC START builder: base
    fields first, optional metadata dropped in the same fixed order when the
    budget is exceeded. The RC fields sit ahead of the optional metadata and
    are never dropped.

    gid (Sprint10 media-id island): when set, a never-dropped `gid: xxx`
    base field binds this image's chunk group id to the filename, so
    backend parsers can attribute `<I{gid}.{i}>` chunks exactly. Absent
    by default — legacy wire is byte-identical.
    """
    base_parts = [
        f"filename: {_clean_value(compressed_file_name, max_len=96)}",
        f"timestamp: {_clean_value(current_timestamp, max_len=32)}",
        f"length: {int(num_buffers)}",
    ]
    if gid is not None:
        base_parts.append(f"gid: {_clean_value(gid, max_len=6)}")
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
    capture_metadata=None,
    max_payload_bytes=295,
):
    """Build the RC END IMG message — byte-identical to the HEIC END.

    P4 revision (Nick, 2026-07-25): the RC fields (fmt/q/att/cmp/rsn) ride in
    START and the a=inc message ONLY. END stays the pure metadata vehicle so
    the camera-metadata budget headroom is unchanged from production; the
    backend correlates START<->END by filename and reads actual-vs-planned
    from sent_buffers vs START's length.
    """
    core_fields = [
        ("filename", compressed_file_name),
        ("uart_duration_sec", f"{float(uart_duration_sec):.1f}"),
        ("sent_buffers", int(sent_buffers)),
        ("cpu_temp_c", cpu_temp_text),
    ]

    return _build_end_image_message(
        compressed_file_name,
        core_fields,
        capture_metadata=capture_metadata,
        max_payload_bytes=max_payload_bytes,
    )


# ---------------------------------------------------------------------------
# Sprint22 — video media group (docs/bm_media_wire_contract.md, signed off
# 2026-09-20). Additive: the still builders above are untouched.
# ---------------------------------------------------------------------------
RC_VIDEO_FORMAT = "h264"
# The backend length regex `(?:chunks|length|len|buffers?)` has no word
# boundary: a key containing any of these would be read as the chunk count.
FORBIDDEN_KEY_SUBSTRINGS = ("len", "chunks", "buffer")
# Still-only START metadata that means nothing for a clip.
_VIDEO_START_SKIP = {"q", "rk", "ws", "we"}


def format_crop(crop_native_xywh):
    """(x, y, w, h) in NATIVE sensor px -> the wire form `WxH+X+Y` (no commas).
    None -> "na": no camera behind this clip (a stored reference)."""
    if crop_native_xywh is None:
        return "na"
    x, y, w, h = (int(v) for v in crop_native_xywh)
    return f"{w}x{h}+{x}+{y}"


def build_rc_video_start_message(
    file_name,
    current_timestamp,
    num_buffers,
    *,
    fps,
    dur,
    res,
    crop=None,
    crf=None,
    br=None,
    complete=True,
    start_metadata=None,
    max_payload_bytes=285,
):
    """Build the video START IMG message (wire contract section 3).

    `length` = planned UNIQUE chunks (the chunk-0 repeat is not counted).
    fmt/fps/dur/res/crop/(br|crf)/cmp are never dropped; optional metadata
    drops in the still builder's order. Exactly one of `br` (target kbps) or
    `crf` names the rate control that produced the payload. Raises instead of
    truncating: a video START that does not fit is a bug, not data.
    """
    if (crf is None) == (br is None):
        raise ValueError("video START needs exactly one of crf= or br=")
    rate_pair = ("crf", int(crf)) if crf is not None else ("br", int(round(float(br))))
    fps_text = str(int(fps)) if float(fps).is_integer() else f"{float(fps):.2f}"

    base_parts = [
        f"filename: {_clean_value(file_name, max_len=96)}",
        f"timestamp: {_clean_value(current_timestamp, max_len=32)}",
        f"length: {int(num_buffers)}",
    ]
    video_pairs = [
        ("fmt", RC_VIDEO_FORMAT), ("fps", fps_text), ("dur", f"{float(dur):.1f}"),
        ("res", res), ("crop", crop), rate_pair, ("cmp", 1 if complete else 0),
    ]
    for key, _ in video_pairs:
        if any(bad in key for bad in FORBIDDEN_KEY_SUBSTRINGS):
            raise ValueError(f"START key {key!r} collides with the backend "
                             f"length regex {FORBIDDEN_KEY_SUBSTRINGS}")
    video_parts = [f"{k}={_clean_value(v, max_len=20)}" for k, v in video_pairs]
    optional = [(k, v) for k, v in _start_metadata_pairs(start_metadata)
                if k not in _VIDEO_START_SKIP]
    drop_order = ["lg", "bf", "im", "st", "su", "tz", "hn", "ws", "we"]

    def render(selected):
        parts = base_parts + video_parts + [
            f"{k}={_clean_value(v, max_len=12 if k == 'sha' else 24)}"
            for k, v in selected]
        return "<START IMG> " + ", ".join(parts) + "\n"

    selected = list(optional)
    msg = render(selected)
    for key_to_drop in drop_order:
        if len(msg.encode("ascii")) <= max_payload_bytes:
            return msg
        selected = [(k, v) for k, v in selected if k != key_to_drop]
        msg = render(selected)
    while len(msg.encode("ascii")) > max_payload_bytes and selected:
        selected.pop()
        msg = render(selected)
    if len(msg.encode("ascii")) > max_payload_bytes:
        raise ValueError(f"video START base fields exceed {max_payload_bytes} B: "
                         f"{len(msg.encode('ascii'))} B -- shorten the filename")
    return msg


def build_rc_video_end_message(
    file_name,
    *,
    uart_duration_sec,
    sent_buffers,
    cpu_temp_text,
    fmt=RC_VIDEO_FORMAT,
    max_payload_bytes=295,
):
    """Video END = the production END plus one core field `fmt` right after
    `filename` (contract section 4). `sent_buffers` = UNIQUE chunks sent."""
    core_fields = [
        ("filename", file_name),
        ("fmt", fmt),
        ("uart_duration_sec", f"{float(uart_duration_sec):.1f}"),
        ("sent_buffers", int(sent_buffers)),
        ("cpu_temp_c", cpu_temp_text),
    ]
    return _build_end_image_message(file_name, core_fields,
                                    max_payload_bytes=max_payload_bytes)


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

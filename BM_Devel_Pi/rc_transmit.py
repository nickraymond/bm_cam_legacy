#!/usr/bin/env python3
# filename: rc_transmit.py
# description: Sprint08 M5 — RC transmit loop with bounded (incomplete-cycle) partial send.
"""
Sprint08 M5 — RC transmit path: complete send + the incomplete-cycle
bounded partial send (sprint spec section 2).

P5 decisions (Nick-approved):
  - RC-ONLY send loop. process_image_v2.send_buffers() and bm_serial.py are
    completely untouched — the known-good HEIC send path stays byte-identical.
    The chunk framing here (`<I{i}>{chunk}\\n`, START-sleep-chunk-sleep-END
    pacing) mirrors production exactly and is pinned by test.
  - START `length` = PLANNED chunks (the full image), never the bounded
    count. A bounded partial send therefore looks to the backend exactly
    like the partial-arrival state Sprint07 P4 validated (renders a preview
    from the received prefix); cmp=0/rsn + the a=inc message (pln/snd) say
    it was intentional. END `sent_buffers` reports what actually went out.

Incomplete-cycle flow (M3 said the floor doesn't fit):
  1. send_n = clamp(max_messages_now() - 3, 0, planned)   # -3 reserves paced
     slots for the a=inc message, START, and END
  2. emit `a=inc` FIRST (the diagnosis arrives even if the rest dies)
  3. if send_n == 0: stop cleanly (no START/END with no room for chunks)
  4. else START(cmp=0, rsn) -> chunks 0..send_n-1 -> END(sent_buffers=actual)

Both paths run a per-chunk guard — budget.messages_fit(2) (this chunk +
END) — so a mid-send stall stops the loop and still closes with an honest
END. Everything is injectable (tx, sleep, clock) for zero-sleep tests; the
orchestrator (M7) passes the real spotter_tx / time functions.

Known limitations: the a=inc message is charged as one paced slot; WS
messages are small but share the same uplink.
"""

import base64
import time
from datetime import datetime, timezone

from rc_media_id import chunk_prefix
from rc_uplink_messages import (
    build_rc_end_message,
    build_rc_incomplete_message,
    build_rc_start_message,
    build_rc_video_end_message,
    build_rc_video_start_message,
    reason_code,
)

# Paced slots reserved ahead of the bounded chunk send: a=inc + START + END.
INCOMPLETE_OVERHEAD_MSGS = 3


def split_base64_chunks(jpeg_data, chunk_b64_chars):
    """Base64-encode the JPEG and split into transmit chunks (production sizes:
    300 b64 chars = 225 raw bytes per chunk)."""
    chunk_b64_chars = int(chunk_b64_chars)
    if chunk_b64_chars < 1:
        raise ValueError(f"chunk_b64_chars must be >= 1, got {chunk_b64_chars}")
    b64 = base64.b64encode(jpeg_data).decode("ascii")
    return [b64[i:i + chunk_b64_chars] for i in range(0, len(b64), chunk_b64_chars)]


def transmit_progressive_image(
    tx,
    budget,
    *,
    jpeg_data,
    compressed_file_name,
    quality,
    enc_attempts,
    fits,
    selector_reason=None,
    chunk_b64_chars,
    delay_seconds,
    start_metadata=None,
    capture_metadata=None,
    cpu_temp_text=None,
    software_sha=None,
    hostname=None,
    current_timestamp=None,
    sleep_fn=time.sleep,
    clock=time.monotonic,
    ack_drain_fn=None,
    pending_pump_fn=None,
    media_gid=None,
):
    """Send one RC image over the BM uplink; bounded when it doesn't fit.

    tx: callable(bytes) — the orchestrator passes BristlemouthSerial.spotter_tx.
    ack_drain_fn: optional callable(max_n) -> int (Sprint10 D12). Called
    once per chunk pacing slot; when it reports it sent an ack, one extra
    paced sleep keeps the wire at the Sprint09 1 msg/s rate. Image
    framing/pacing is unchanged when None (the pre-Sprint10 wire).
    pending_pump_fn: optional callable() -> int (Sprint11 C3/D5). Called
    once per chunk pacing slot to parse + PERSIST inbound commands WITHOUT
    touching the wire, so a command that lands mid-burst still governs the
    next boot while its ack waits until the image is finished. Adds no
    paced slot and no message: burst length is unchanged, which is the
    whole point — an ack riding a pacing slot silently lengthens the burst
    and can push its tail onto a blackout boundary.
    media_gid: optional 3-char group id (rc_media_id island). When set,
    chunks go out as `<I{gid}.{i}>` and START carries `gid:` — exact
    chunk->image attribution for non-FIFO backends. None = legacy wire.
    Returns {planned, send_target, sent, started, complete_send,
             incomplete_emitted, uart_duration_sec}.
    """
    delay_seconds = float(delay_seconds)
    chunks = split_base64_chunks(jpeg_data, chunk_b64_chars)
    planned = len(chunks)
    wire_reason = reason_code(selector_reason) if not fits else None

    if current_timestamp is None:
        current_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    uart_start = clock()
    incomplete_emitted = False

    if fits:
        send_target = planned
    else:
        send_target = max(0, min(planned, budget.max_messages_now() - INCOMPLETE_OVERHEAD_MSGS))
        inc_msg = build_rc_incomplete_message(
            quality=quality,
            enc_attempts=enc_attempts,
            reason=wire_reason,
            planned_msgs=planned,
            send_msgs=send_target,
            cpu_temp_text=cpu_temp_text,
            software_sha=software_sha,
            hostname=hostname,
        )
        tx(inc_msg.encode("ascii"))
        incomplete_emitted = True
        sleep_fn(delay_seconds)

        if send_target == 0:
            # No room for START + chunks + END: the a=inc diagnosis is the
            # whole story. Stop cleanly.
            return {
                "planned": planned,
                "send_target": 0,
                "sent": 0,
                "started": False,
                "complete_send": False,
                "incomplete_emitted": True,
                "uart_duration_sec": clock() - uart_start,
            }

    # START announces the PLANNED chunk count (P5 decision: length = planned).
    start_msg = build_rc_start_message(
        compressed_file_name,
        current_timestamp,
        planned,
        quality=quality,
        enc_attempts=enc_attempts,
        complete=fits,
        reason=wire_reason,
        start_metadata=start_metadata,
        gid=media_gid,
    )
    tx(start_msg.encode("ascii"))
    sleep_fn(delay_seconds)

    sent = 0
    for i in range(send_target):
        # Per-chunk guard: this chunk + the closing END must still fit.
        if not budget.messages_fit(2):
            break
        tx(f"{chunk_prefix(i, media_gid)}{chunks[i]}\n".encode("ascii"))
        sent += 1
        sleep_fn(delay_seconds)
        # Sprint11 C3: persist inbound commands mid-burst, wire untouched.
        if pending_pump_fn is not None:
            pending_pump_fn()
        # Sprint10 D12: at most one command ack rides each pacing slot;
        # it consumes its own paced sleep so the drain rate is unchanged.
        # Guard keeps room for the closing END after the ack.
        if ack_drain_fn is not None and budget.messages_fit(2):
            if ack_drain_fn(1):
                sleep_fn(delay_seconds)

    end_msg = build_rc_end_message(
        compressed_file_name,
        uart_duration_sec=clock() - uart_start,
        sent_buffers=sent,
        cpu_temp_text=cpu_temp_text if cpu_temp_text is not None else "na",
        capture_metadata=capture_metadata,
    )
    tx(end_msg.encode("ascii"))

    return {
        "planned": planned,
        "send_target": send_target,
        "sent": sent,
        "started": True,
        "complete_send": bool(fits) and sent == planned,
        "incomplete_emitted": incomplete_emitted,
        "uart_duration_sec": clock() - uart_start,
    }


# ---------------------------------------------------------------------------
# Sprint22 — video media group (docs/bm_media_wire_contract.md)
# ---------------------------------------------------------------------------
# Paced slots a clip needs besides its chunks: START + the chunk-0 repeat + END.
VIDEO_OVERHEAD_MSGS = 3


def video_burst_messages(payload, chunk_b64_chars):
    """Every paced message one clip puts on the wire."""
    return len(split_base64_chunks(payload, chunk_b64_chars)) + VIDEO_OVERHEAD_MSGS


def transmit_video_clip(
    tx,
    budget,
    *,
    payload,
    file_name,
    fps,
    dur,
    res,
    crop,
    br=None,
    crf=None,
    chunk_b64_chars,
    delay_seconds,
    start_metadata=None,
    cpu_temp_text=None,
    current_timestamp=None,
    sleep_fn=time.sleep,
    clock=time.monotonic,
):
    """Send one H.264 clip: START, chunks, chunk 0 AGAIN, END (contract sections 1 + 5).

    Separate from transmit_progressive_image on purpose — that loop and its
    wire are pinned byte-identical by test and carry JPEG-only logic (the
    bounded partial send, `a=inc`). Same framing (`<I{i}>{chunk}\\n`, legacy
    form, no gid) and the same pacing pattern (sleep after START and after
    every chunk, not after END).

    A clip that does not fit the remaining budget is REFUSED before START —
    never truncated silently (the encoder already sized it; not fitting here
    means the budget moved). Once started, the per-chunk guard still keeps
    room for END, so a stall closes the group honestly with
    sent_buffers < length and the backend stores a partial clip.

    Returns {planned, sent, started, complete_send, repeat_sent, refused_reason,
             uart_duration_sec}.
    """
    delay_seconds = float(delay_seconds)
    chunks = split_base64_chunks(payload, chunk_b64_chars)
    planned = len(chunks)
    uart_start = clock()
    result = {"planned": planned, "sent": 0, "started": False, "complete_send": False,
              "repeat_sent": False, "refused_reason": None, "uart_duration_sec": 0.0}

    if planned < 1:
        result["refused_reason"] = "empty_payload"
        return result
    if not budget.messages_fit(planned + VIDEO_OVERHEAD_MSGS):
        result["refused_reason"] = (
            f"budget: clip needs {planned + VIDEO_OVERHEAD_MSGS} paced messages, "
            f"{budget.max_messages_now()} fit in the {budget.remaining_s():.0f}s left")
        return result

    if current_timestamp is None:
        current_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    tx(build_rc_video_start_message(
        file_name, current_timestamp, planned, fps=fps, dur=dur, res=res, crop=crop,
        br=br, crf=crf, complete=True, start_metadata=start_metadata,
    ).encode("ascii"))
    result["started"] = True
    sleep_fn(delay_seconds)

    sent = 0
    for i in range(planned):
        # This chunk + the closing END must still fit.
        if not budget.messages_fit(2):
            break
        tx(f"{chunk_prefix(i, None)}{chunks[i]}\n".encode("ascii"))
        sent += 1
        sleep_fn(delay_seconds)

    # Chunk 0 carries SPS/PPS: lose it and nothing decodes. The backend dedupes
    # by index, so a second copy costs one message and needs no backend change.
    if sent == planned and budget.messages_fit(2):
        tx(f"{chunk_prefix(0, None)}{chunks[0]}\n".encode("ascii"))
        result["repeat_sent"] = True
        sleep_fn(delay_seconds)

    tx(build_rc_video_end_message(
        file_name, uart_duration_sec=clock() - uart_start, sent_buffers=sent,
        cpu_temp_text=cpu_temp_text if cpu_temp_text is not None else "na",
    ).encode("ascii"))

    result.update({"sent": sent, "complete_send": sent == planned,
                   "uart_duration_sec": clock() - uart_start})
    return result

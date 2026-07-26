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

from rc_uplink_messages import (
    build_rc_end_message,
    build_rc_incomplete_message,
    build_rc_start_message,
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
):
    """Send one RC image over the BM uplink; bounded when it doesn't fit.

    tx: callable(bytes) — the orchestrator passes BristlemouthSerial.spotter_tx.
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
    )
    tx(start_msg.encode("ascii"))
    sleep_fn(delay_seconds)

    sent = 0
    for i in range(send_target):
        # Per-chunk guard: this chunk + the closing END must still fit.
        if not budget.messages_fit(2):
            break
        tx(f"<I{i}>{chunks[i]}\n".encode("ascii"))
        sent += 1
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

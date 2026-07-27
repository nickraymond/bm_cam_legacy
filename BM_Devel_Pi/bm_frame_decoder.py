#!/usr/bin/env python3
# filename: bm_frame_decoder.py
# description: Sprint10 — inbound BM serial frame decoder (COBS + CRC16 + pub parse).
"""
Sprint10 command daemon — the inbound half of the BM serial framing.

The repo has always ENCODED frames (bm_serial.py: packet -> CRC16 at
bytes 2-3 -> COBS -> 0x00 delimiter) but never decoded them — the only
inbound consumer (spotter_time_sync) pattern-scans the raw stream (Q1).
This module adds the strict reverse path for command frames:

  raw UART bytes -> split on 0x00 -> COBS decode -> CRC16 verify
                 -> parse BM pub frame -> (node_id, topic, payload)

Pub frame layout (mirrors bm_serial.get_pub_header, both directions):
  [0]      type (0x02 = pub)
  [1]      reserved/flags (not checked)
  [2:4]    CRC16 LE, computed over the packet with these bytes zeroed
  [4:12]   node id, u64 LE
  [12:14]  pub header bytes (outbound uses 01 01; NOT checked inbound —
           the mote-side value is unverified until Phase B)
  [14:16]  topic length, u16 LE
  [16:16+n]    topic
  [16+n:]      payload

Robustness rules (a garbled frame must never crash the listener):
  - decode functions return None on malformed input, never raise
  - FrameAccumulator bounds its buffer; overlong garbage is dropped
  - every drop is counted by cause (stats) so Phase A/B tests and field
    logs can tell corruption from silence

Pure module: no serial, no threads, no camera.

Example:
  >>> acc = FrameAccumulator(node_id=0x123, topic=b"bmcam/cmd")
  >>> frames = acc.feed(uart_chunk)      # -> [payload_bytes, ...]

Known limitations: inbound layout beyond type 0x02 is assumed to mirror
the outbound shape; Phase B (`bm pub` from the Spotter CLI) is the
checkpoint that verifies real mote traffic parses. Non-matching frames
are counted, not errors — the bus carries plenty of other traffic.
"""

# Max bytes of un-delimited garbage to buffer before dropping. Real
# frames are small (topic + payload << 1 KB); 8 KB of no-delimiter data
# means we are reading noise or a firehose we don't understand.
MAX_BUFFER_BYTES = 8192

FRAME_TYPE_PUB = 0x02
_HEADER_LEN = 16  # type(4 incl CRC) + node_id(8) + pub bytes(2) + topic_len(2)


def crc16(seed, src):
    """Same CRC as bm_serial.BristlemouthSerial.crc (kept byte-identical)."""
    for i in src:
        e = (seed ^ i) & 0xFF
        f = e ^ ((e << 4) & 0xFF)
        seed = (seed >> 8) ^ (((f << 8) & 0xFFFF) ^ ((f << 3) & 0xFFFF)) ^ (f >> 4)
    return seed


def cobs_decode(data):
    """Decode one COBS block (no 0x00 delimiter included).

    Returns the decoded bytes, or None if the block is malformed
    (embedded zero, or a code byte pointing past the end).
    """
    if not data:
        return None
    out = bytearray()
    idx = 0
    length = len(data)
    while idx < length:
        code = data[idx]
        if code == 0:
            return None  # zeros are delimiters; never valid inside a block
        end = idx + code
        if end > length:
            return None  # code points past the block: truncated/corrupt
        chunk = data[idx + 1 : end]
        if 0 in chunk:
            return None
        out += chunk
        idx = end
        # A maximal (0xFF) code means "254 bytes, no implicit zero".
        if code != 0xFF and idx < length:
            out.append(0)
    return bytes(out)


def verify_crc(packet):
    """True if the CRC16 at bytes 2-3 matches (computed with them zeroed)."""
    if len(packet) < 4:
        return False
    stored = packet[2] | (packet[3] << 8)
    scratch = bytearray(packet)
    scratch[2] = 0
    scratch[3] = 0
    return crc16(0, scratch) == stored


def parse_pub_frame(packet):
    """Parse a CRC-valid packet as a BM pub frame.

    Returns {"type", "node_id", "topic", "payload"} or None if the
    packet is not a well-formed pub frame. CRC is NOT checked here —
    call verify_crc first (FrameAccumulator does both).
    """
    if len(packet) < _HEADER_LEN:
        return None
    frame_type = packet[0]
    if frame_type != FRAME_TYPE_PUB:
        return None
    topic_len = packet[14] | (packet[15] << 8)
    topic_end = _HEADER_LEN + topic_len
    if topic_len == 0 or topic_end > len(packet):
        return None
    return {
        "type": frame_type,
        "node_id": int.from_bytes(packet[4:12], "little"),
        "topic": bytes(packet[_HEADER_LEN:topic_end]),
        "payload": bytes(packet[topic_end:]),
    }


class FrameAccumulator:
    """Incremental decoder: feed raw UART chunks, get command payloads.

    Splits the stream on 0x00 delimiters, strictly decodes each block,
    and returns the payloads of CRC-valid pub frames whose topic matches
    `topic`. Everything else on the bus is counted and skipped.
    """

    def __init__(self, topic, max_buffer=MAX_BUFFER_BYTES):
        if isinstance(topic, str):
            topic = topic.encode("utf-8")
        self.topic = topic
        self.max_buffer = max_buffer
        self._buffer = bytearray()
        self.stats = {
            "blocks": 0,          # 0x00-delimited blocks seen
            "cobs_errors": 0,     # blocks that failed COBS decode
            "crc_errors": 0,      # decoded packets with bad CRC
            "non_pub": 0,         # CRC-valid but not a parseable pub frame
            "other_topic": 0,     # pub frames for topics we don't want
            "matched": 0,         # command payloads returned
            "overflow_drops": 0,  # buffer-bound garbage drops
        }

    def feed(self, chunk):
        """Consume raw bytes; return a list of matching frame payloads."""
        payloads = []
        if not chunk:
            return payloads
        self._buffer.extend(chunk)

        while True:
            delim = self._buffer.find(b"\x00")
            if delim < 0:
                break
            block = bytes(self._buffer[:delim])
            del self._buffer[: delim + 1]
            if not block:
                continue  # back-to-back delimiters / idle line noise
            self.stats["blocks"] += 1
            payload = self._decode_block(block)
            if payload is not None:
                payloads.append(payload)

        if len(self._buffer) > self.max_buffer:
            # No delimiter in max_buffer bytes: drop the front, keep the
            # tail (a real frame boundary may still be coming).
            drop = len(self._buffer) - self.max_buffer
            del self._buffer[:drop]
            self.stats["overflow_drops"] += 1
        return payloads

    def _decode_block(self, block):
        packet = cobs_decode(block)
        if packet is None:
            self.stats["cobs_errors"] += 1
            return None
        if not verify_crc(packet):
            self.stats["crc_errors"] += 1
            return None
        frame = parse_pub_frame(packet)
        if frame is None:
            self.stats["non_pub"] += 1
            return None
        if frame["topic"] != self.topic:
            self.stats["other_topic"] += 1
            return None
        self.stats["matched"] += 1
        return frame["payload"]

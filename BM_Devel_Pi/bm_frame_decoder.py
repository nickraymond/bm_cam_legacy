#!/usr/bin/env python3
# filename: bm_frame_decoder.py
# description: Sprint10 — inbound BM serial frame decoder (COBS + CRC16 + pub parse).
"""
Sprint10 command daemon — the inbound half of the BM serial framing.

PHASE B FINDING (bmcam003 bench, 2026-07-27, raw UART capture): the
wire is ASYMMETRIC. Pi -> mote is COBS-encoded + 0x00-delimited
(bm_serial.py, unchanged). But mote -> Pi arrives RAW: pub packets with
zero bytes inline, NO COBS, NO delimiter, frames back-to-back. That is
why production only ever needed a pattern-scan (Q1), and why this
module's original COBS inbound path counted cobs_errors on real
traffic. Captured ground truth lives in tests/test_bm_frame_decoder.py
(REAL_FRAMES — bytes off the bmcam003 UART).

Inbound decoding is therefore RawPubScanner:

  raw UART bytes -> find "01 01 + u16len + topic" signature
                 -> frame start = idx-12 (type4 + node_id8), byte0==0x02
                 -> frame END found by CRC scan: extend byte-by-byte
                    until CRC16 (bytes 2-3, zeroed) matches — the raw
                    format has NO payload length field, so the CRC is
                    the only end marker
                 -> payload = bytes between topic and CRC-matched end

Raw pub frame layout (verified against captured frames, CRC checked):
  [0]      type (0x02 = pub)
  [1]      flags/reserved (0x00 observed; not checked)
  [2:4]    CRC16 LE over the whole frame with these bytes zeroed
  [4:12]   PUBLISHER node id, u64 LE (Spotter bridge on bench captures)
  [12:14]  01 01
  [14:16]  topic length, u16 LE
  [16:16+n]    topic
  [16+n:]      payload (no length field — see CRC scan above)

Robustness rules (a garbled frame must never crash the listener):
  - scanners/decoders return None/[] on malformed input, never raise
  - buffers are bounded; overlong garbage is dropped and counted
  - a CRC-scan false positive (P ~= 1/65536 per candidate end) yields a
    truncated payload that fails JSON parse downstream -> unackable
    drop + operator re-send; accepted for v1

The COBS helpers (cobs_encode's mirror cobs_decode, verify_crc,
parse_pub_frame) stay: the OUTBOUND direction really is COBS, and
mote-side tools (tools/mock_mote.py) use them to decode daemon output.

Pure module: no serial, no threads, no camera.

Example:
  >>> scanner = RawPubScanner(topic=b"bmcam/cmd")
  >>> payloads = scanner.feed(uart_chunk)   # -> [payload_bytes, ...]
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


def build_raw_pub_frame(node_id, topic, payload):
    """Encode a mote->Pi RAW pub frame (the format RawPubScanner reads).

    For tests and tools/mock_mote.py — the real producer is the mote's
    serial bridge. Layout verified against bmcam003 captures 2026-07-27.
    """
    if isinstance(topic, str):
        topic = topic.encode("utf-8")
    if isinstance(payload, str):
        payload = payload.encode("utf-8")
    frame = bytearray(
        bytes([FRAME_TYPE_PUB, 0x00, 0x00, 0x00])
        + int(node_id).to_bytes(8, "little")
        + b"\x01\x01"
        + len(topic).to_bytes(2, "little")
        + topic
        + payload
    )
    crc = crc16(0, frame)
    frame[2] = crc & 0xFF
    frame[3] = (crc >> 8) & 0xFF
    return bytes(frame)


class RawPubScanner:
    """Incremental decoder for the RAW mote->Pi stream (see module
    docstring): feed raw UART chunks, get payloads for one topic.

    No delimiters exist, so frames are located by the pub-header
    signature and terminated by CRC scan. Partial frames stay buffered
    until more bytes arrive or the per-frame bound trips.
    """

    # A real command frame is header(16) + topic + a <=384-char payload;
    # 1 KB of growth past a candidate start without a CRC match means
    # the candidate is noise (or the frame was corrupted in transit).
    MAX_FRAME_BYTES = 1024

    def __init__(self, topic, max_buffer=MAX_BUFFER_BYTES):
        if isinstance(topic, str):
            topic = topic.encode("utf-8")
        self.topic = topic
        # Signature: pub bytes + topic_len + topic (see layout above).
        self._sig = b"\x01\x01" + len(topic).to_bytes(2, "little") + topic
        self.max_buffer = max_buffer
        self._buffer = bytearray()
        self.stats = {
            "candidates": 0,       # signature hits examined
            "matched": 0,          # CRC-verified frames returned
            "bad_start": 0,        # signature without a valid frame head
            "crc_scan_fail": 0,    # frame bound exceeded with no CRC match
            "overflow_drops": 0,   # buffer-bound garbage drops
        }

    def feed(self, chunk):
        """Consume raw bytes; return payloads of CRC-valid topic frames."""
        payloads = []
        if chunk:
            self._buffer.extend(chunk)
            while True:
                payload, made_progress = self._scan_once()
                if payload is not None:
                    payloads.append(payload)
                if not made_progress:
                    break
        if len(self._buffer) > self.max_buffer:
            drop = len(self._buffer) - self.max_buffer
            del self._buffer[:drop]
            self.stats["overflow_drops"] += 1
        return payloads

    def _scan_once(self):
        """Find and extract one frame. Returns (payload|None, progress).
        progress=True means bytes were consumed and scanning should
        continue; False means wait for more input."""
        buf = self._buffer
        sig_idx = buf.find(self._sig)
        if sig_idx < 0:
            # Keep a tail that could hold a partial signature next feed.
            keep = len(self._sig) + 12
            if len(buf) > keep:
                del buf[: len(buf) - keep]
            return None, False

        start = sig_idx - 12  # type(4) + publisher node id(8)
        if start < 0 or buf[start] != FRAME_TYPE_PUB:
            self.stats["bad_start"] += 1
            del buf[: sig_idx + 1]  # skip this signature hit
            return None, True

        self.stats["candidates"] += 1
        payload_start = sig_idx + len(self._sig)
        stored = buf[start + 2] | (buf[start + 3] << 8)

        # CRC scan: extend the frame end until the stored CRC matches.
        # crc16 is byte-serial, so the prefix is computed once and each
        # additional byte is O(1).
        scratch_head = bytes(buf[start:start + 2]) + b"\x00\x00" + bytes(
            buf[start + 4:payload_start])
        crc = crc16(0, scratch_head)
        end = payload_start
        while end < len(buf):
            if crc == stored:
                break
            crc = crc16(crc, buf[end:end + 1])
            end += 1
        if crc == stored:
            payload = bytes(buf[payload_start:end])
            del buf[:end]
            self.stats["matched"] += 1
            return payload, True

        if len(buf) - start > self.MAX_FRAME_BYTES:
            # No CRC match within bounds: corrupted or false signature.
            self.stats["crc_scan_fail"] += 1
            del buf[: sig_idx + 1]
            return None, True
        return None, False  # frame still arriving; wait for more bytes


class FrameAccumulator:
    """Incremental decoder for the COBS + 0x00-delimited direction.

    NOTE (Phase B): this is the Pi -> mote wire format. The mote -> Pi
    stream is RAW (use RawPubScanner). This class serves mote-side
    tools/tests that consume the Pi's output (e.g. tools/mock_mote.py).

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

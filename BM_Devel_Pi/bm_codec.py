#!/usr/bin/env python3
# filename: bm_codec.py
# description: Sprint26 S1 — the ONE copy of the BM serial CRC16 + COBS codec.
"""
BM serial framing primitives, in one place.

Until Sprint26 S1 the same CRC16 and COBS encoder lived in three files
(bm_serial.BristlemouthSerial.crc/cobs_encode, spotter_time_sync._crc/
_cobs_encode, bm_frame_decoder.crc16/cobs_decode). They were identical by
inspection; tests/test_bm_codec.py proves this module reproduces the old code
byte for byte against a frozen copy of it. The old names stay as aliases, so no
caller changes.

  crc16(seed, data)        CRC16 as the BM serial bridge computes it
  cobs_encode(data)        COBS block, WITHOUT the 0x00 frame delimiter
  cobs_decode(block)       inverse of cobs_encode; None on a malformed block
  finalize_packet(packet)  write the CRC into bytes 2-3 of `packet` (a bytearray,
                           mutated in place, CRC computed with them zeroed as the
                           caller left them), COBS-encode, append 0x00

Wire direction (Phase B, bmcam003 2026-07-27): Pi -> mote frames are COBS +
0x00-delimited; mote -> Pi frames arrive RAW (see bm_frame_decoder).
"""


def crc16(seed, data):
    """CRC16 over `data` starting from `seed` (byte-serial: chaining is valid)."""
    for byte in data:
        e = (seed ^ byte) & 0xFF
        f = e ^ ((e << 4) & 0xFF)
        seed = (seed >> 8) ^ (((f << 8) & 0xFFFF) ^ ((f << 3) & 0xFFFF)) ^ (f >> 4)
    return seed


def cobs_encode(data):
    """COBS-encode `data` (no trailing delimiter). A run of 254 non-zero bytes
    is closed with a 0xFF code; an input ending in a zero keeps its final
    overhead byte, exactly as the original bm_serial encoder did."""
    final_zero = True
    out = bytearray()
    idx = 0
    search_start = 0
    for byte in data:
        if byte == 0:
            final_zero = True
            out.append(idx - search_start + 1)
            out += data[search_start:idx]
            search_start = idx + 1
        elif idx - search_start == 0xFD:
            final_zero = False
            out.append(0xFF)
            out += data[search_start:idx + 1]
            search_start = idx + 1
        idx += 1
    if idx != search_start or final_zero:
        out.append(idx - search_start + 1)
        out += data[search_start:idx]
    return bytes(out)


def cobs_decode(block):
    """Decode one COBS block (no 0x00 delimiter included).

    Returns the decoded bytes, or None if the block is malformed (embedded
    zero, or a code byte pointing past the end)."""
    if not block:
        return None
    out = bytearray()
    idx = 0
    length = len(block)
    while idx < length:
        code = block[idx]
        if code == 0:
            return None  # zeros are delimiters; never valid inside a block
        end = idx + code
        if end > length:
            return None  # code points past the block: truncated/corrupt
        chunk = block[idx + 1:end]
        if 0 in chunk:
            return None
        out += chunk
        idx = end
        # A maximal (0xFF) code means "254 bytes, no implicit zero".
        if code != 0xFF and idx < length:
            out.append(0)
    return bytes(out)


def finalize_packet(packet):
    """CRC into bytes 2-3 of `packet` (mutated), then COBS + 0x00 delimiter."""
    checksum = crc16(0, packet)
    packet[2] = checksum & 0xFF
    packet[3] = (checksum >> 8) & 0xFF
    return cobs_encode(packet) + b"\x00"

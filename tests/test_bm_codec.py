#!/usr/bin/env python3
# filename: test_bm_codec.py
# description: Sprint26 S1 — bm_codec reproduces the three retired CRC/COBS copies byte for byte.
"""
bm_codec (Sprint26 S1) replaced three copies of the BM serial CRC16 + COBS code.
This test keeps a FROZEN verbatim copy of the old code (bm_serial's encoder and
crc, bm_frame_decoder's decoder, as of development cdadc95) and checks the new
module against it on thousands of packets: random lengths 0-1200 B, zero-heavy
and zero-free runs, and every length around the 254-byte COBS block boundary
(398 B chunk frames cross it). Also: decode(encode(x)) == x, and the aliases the
old call sites still use point at bm_codec.

Run (repo root):
  python3 -m unittest tests.test_bm_codec -v
"""

import os
import random
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import bm_codec  # noqa: E402


# --- frozen copy of the retired code (do not edit) ------------------------------------------

def _old_cobs_encode(in_bytes):          # bm_serial.BristlemouthSerial.cobs_encode
    final_zero = True
    out_bytes = bytearray()
    idx = 0
    search_start_idx = 0
    for in_char in in_bytes:
        if in_char == 0:
            final_zero = True
            out_bytes.append(idx - search_start_idx + 1)
            out_bytes += in_bytes[search_start_idx:idx]
            search_start_idx = idx + 1
        else:
            if idx - search_start_idx == 0xFD:
                final_zero = False
                out_bytes.append(0xFF)
                out_bytes += in_bytes[search_start_idx: idx + 1]
                search_start_idx = idx + 1
        idx += 1
    if idx != search_start_idx or final_zero:
        out_bytes.append(idx - search_start_idx + 1)
        out_bytes += in_bytes[search_start_idx:idx]
    return bytes(out_bytes)


def _old_crc(seed, src):                 # bm_serial.BristlemouthSerial.crc
    e, f = 0, 0
    for i in src:
        e = (seed ^ i) & 0xFF
        f = e ^ ((e << 4) & 0xFF)
        seed = (seed >> 8) ^ (((f << 8) & 0xFFFF) ^ ((f << 3) & 0xFFFF)) ^ (f >> 4)
    return seed


def _old_finalize(packet):               # bm_serial / spotter_time_sync finalize
    checksum = _old_crc(0, packet)
    packet[2] = checksum & 0xFF
    packet[3] = (checksum >> 8) & 0xFF
    return _old_cobs_encode(packet) + b"\x00"


def _old_cobs_decode(data):              # bm_frame_decoder.cobs_decode
    if not data:
        return None
    out = bytearray()
    idx = 0
    length = len(data)
    while idx < length:
        code = data[idx]
        if code == 0:
            return None
        end = idx + code
        if end > length:
            return None
        chunk = data[idx + 1: end]
        if 0 in chunk:
            return None
        out += chunk
        idx = end
        if code != 0xFF and idx < length:
            out.append(0)
    return bytes(out)


# --- corpus -----------------------------------------------------------------------------------

def corpus():
    rng = random.Random(2626)
    cases = [b"", b"\x00", b"\x00\x00", b"\x01", b"\xff" * 300, b"\x00" * 300]
    for n in list(range(0, 12)) + list(range(250, 262)) + list(range(505, 515)) + [398, 412, 1200]:
        cases.append(bytes(rng.randrange(1, 256) for _ in range(n)))        # no zeros
        cases.append(bytes(rng.randrange(0, 256) for _ in range(n)))        # any byte
        cases.append(bytes(0 if rng.random() < 0.3 else rng.randrange(1, 256)
                           for _ in range(n)))                                # zero-heavy
        cases.append(bytes([rng.randrange(1, 256) for _ in range(max(n - 1, 0))]) + b"\x00")
    for _ in range(3000):
        n = rng.randrange(0, 1201)
        p0 = rng.choice((0.0, 0.004, 0.05, 0.5))
        cases.append(bytes(0 if rng.random() < p0 else rng.randrange(1, 256) for _ in range(n)))
    return cases


class TestBmCodecMatchesRetiredCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cases = corpus()

    def test_encode_matches(self):
        for data in self.cases:
            self.assertEqual(bm_codec.cobs_encode(bytearray(data)), _old_cobs_encode(bytearray(data)),
                             f"len={len(data)}")

    def test_crc_matches_including_chaining(self):
        for data in self.cases:
            self.assertEqual(bm_codec.crc16(0, data), _old_crc(0, data))
            half = len(data) // 2
            self.assertEqual(bm_codec.crc16(bm_codec.crc16(0, data[:half]), data[half:]),
                             _old_crc(0, data))

    def test_finalize_matches_and_mutates_the_same(self):
        for data in self.cases:
            if len(data) < 4:
                continue
            new_pkt, old_pkt = bytearray(data), bytearray(data)
            self.assertEqual(bm_codec.finalize_packet(new_pkt), _old_finalize(old_pkt))
            self.assertEqual(new_pkt, old_pkt)

    def test_decode_matches_and_round_trips(self):
        for data in self.cases:
            encoded = bm_codec.cobs_encode(bytearray(data))
            self.assertEqual(bm_codec.cobs_decode(encoded), _old_cobs_decode(encoded))
            if data:
                self.assertEqual(bm_codec.cobs_decode(encoded), data, f"len={len(data)}")
        for bad in (b"", b"\x05ab", b"\x03a\x00b", b"\x00abc"):
            self.assertIsNone(bm_codec.cobs_decode(bad))

    def test_old_call_sites_use_bm_codec(self):
        import bm_frame_decoder
        import bm_serial
        import spotter_time_sync
        self.assertIs(spotter_time_sync._crc, bm_codec.crc16)
        self.assertIs(spotter_time_sync._cobs_encode, bm_codec.cobs_encode)
        self.assertIs(spotter_time_sync._finalize_packet, bm_codec.finalize_packet)
        self.assertIs(bm_frame_decoder.crc16, bm_codec.crc16)
        self.assertIs(bm_frame_decoder.cobs_decode, bm_codec.cobs_decode)
        bm = bm_serial.BristlemouthSerial(uart=object(), network_type=0x02)
        pkt = bytearray(b"\x02\x00\x00\x00hello\x00world")
        self.assertEqual(bm.finalize_packet(bytearray(pkt)), bm_codec.finalize_packet(bytearray(pkt)))
        self.assertEqual(bm.cobs_encode(bytes(pkt)), bm_codec.cobs_encode(bytes(pkt)))
        self.assertEqual(bm.crc(0, pkt), bm_codec.crc16(0, pkt))


if __name__ == "__main__":
    unittest.main()

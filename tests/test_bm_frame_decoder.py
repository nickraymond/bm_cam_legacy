#!/usr/bin/env python3
# filename: test_bm_frame_decoder.py
# description: Sprint10 §2/§4 — inbound frame decoder + garbled/partial handling.
"""
Sprint10 — tests for bm_frame_decoder.py.

Round-trips frames built by the PRODUCTION encoder (bm_serial.py's
BristlemouthSerial) through the new inbound decoder, so both directions
are pinned to the same wire format. Covers the TRACKER §4 unit
"partial/garbled frame handling": frames split across reads, corrupted
bytes, junk between frames, wrong topics, buffer-overflow bounding —
the stream must always recover on the next clean frame and never raise.

Run (repo root; serial stubbed, no UART):
  python3 -m unittest tests.test_bm_frame_decoder -v
"""

import os
import sys
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

from bm_serial import BristlemouthSerial  # noqa: E402
from bm_frame_decoder import (  # noqa: E402
    FrameAccumulator,
    RawPubScanner,
    build_raw_pub_frame,
    cobs_decode,
    crc16,
    parse_pub_frame,
    verify_crc,
)

TOPIC = b"bmcam/cmd"
NODE_ID = 0xC0FFEEEEF0CACC1A

# GROUND TRUTH: raw bytes captured off the bmcam003 UART (mote->Pi)
# 2026-07-27 during Phase B — `bm pub bmcam/cmd <data> 1 1` from the
# Spotter console SPOT-33507C. Publisher node id is the Spotter bridge
# c3c564b91856226c. NOT COBS-encoded, no 0x00 delimiter.
REAL_FRAMES = {
    b"hello": bytes.fromhex(
        "0200189" + "2" + "6c225618b964c5c3" + "0101" + "0900"
        + b"bmcam/cmd".hex() + b"hello".hex()
    ),
    b'{"id":101,"c":"ping"}': bytes.fromhex(
        "0200d5d7" + "6c225618b964c5c3" + "0101" + "0900"
        + b"bmcam/cmd".hex() + b'{"id":101,"c":"ping"}'.hex()
    ),
}


class _DummyUart:
    """bm_serial only needs .write for encoding; capture it."""

    def __init__(self):
        self.written = b""

    def write(self, data):
        self.written += data
        return len(data)


def make_pub_frame(payload, topic=TOPIC, node_id=NODE_ID):
    """Encode a pub frame EXACTLY as production bm_serial does."""
    bm = BristlemouthSerial(uart=_DummyUart(), node_id=node_id, network_type=0x02)
    packet = (
        bm.get_pub_header()
        + len(topic).to_bytes(2, "little")
        + topic
        + payload
    )
    return bm.finalize_packet(packet)  # COBS + trailing 0x00


class TestCobsRoundTrip(unittest.TestCase):
    def _round_trip(self, raw):
        bm = BristlemouthSerial(uart=_DummyUart(), node_id=1, network_type=0x02)
        encoded = bm.cobs_encode(bytearray(raw))
        self.assertEqual(cobs_decode(encoded), raw)

    def test_simple(self):
        self._round_trip(b"hello")

    def test_embedded_zeros(self):
        self._round_trip(b"a\x00b\x00\x00c")

    def test_trailing_zero(self):
        self._round_trip(b"abc\x00")

    def test_all_zeros(self):
        self._round_trip(b"\x00" * 10)

    def test_long_run_over_254(self):
        self._round_trip(bytes([7]) * 600)

    def test_binary_sweep(self):
        self._round_trip(bytes(range(256)) * 3)

    def test_malformed_returns_none(self):
        self.assertIsNone(cobs_decode(b""))
        self.assertIsNone(cobs_decode(b"\x05ab"))       # code past end
        self.assertIsNone(cobs_decode(b"\x03a\x00b"))   # embedded zero
        self.assertIsNone(cobs_decode(b"\x00abc"))      # zero code byte


class TestCrcAndParse(unittest.TestCase):
    def test_crc_matches_production_encoder(self):
        frame = make_pub_frame(b'{"id":1,"c":"ping"}')
        packet = cobs_decode(frame[:-1])  # strip 0x00 delimiter
        self.assertIsNotNone(packet)
        self.assertTrue(verify_crc(packet))

    def test_crc_rejects_flipped_bit(self):
        frame = make_pub_frame(b'{"id":1,"c":"ping"}')
        packet = bytearray(cobs_decode(frame[:-1]))
        packet[-1] ^= 0x01
        self.assertFalse(verify_crc(bytes(packet)))

    def test_parse_pub_frame_fields(self):
        payload = b'{"id":417,"c":"roi","v":2}'
        packet = cobs_decode(make_pub_frame(payload)[:-1])
        frame = parse_pub_frame(packet)
        self.assertEqual(frame["type"], 0x02)
        self.assertEqual(frame["node_id"], NODE_ID)
        self.assertEqual(frame["topic"], TOPIC)
        self.assertEqual(frame["payload"], payload)

    def test_parse_rejects_short_and_non_pub(self):
        self.assertIsNone(parse_pub_frame(b"\x02\x00\x00"))
        packet = bytearray(cobs_decode(make_pub_frame(b"x")[:-1]))
        packet[0] = 0x03  # subscribe type
        self.assertIsNone(parse_pub_frame(bytes(packet)))

    def test_parse_rejects_bad_topic_len(self):
        packet = bytearray(cobs_decode(make_pub_frame(b"x")[:-1]))
        packet[14] = 0xFF
        packet[15] = 0xFF
        self.assertIsNone(parse_pub_frame(bytes(packet)))


class TestFrameAccumulator(unittest.TestCase):
    def setUp(self):
        self.acc = FrameAccumulator(topic=TOPIC)

    def test_single_frame_one_chunk(self):
        payload = b'{"id":417,"c":"roi","v":2}'
        self.assertEqual(self.acc.feed(make_pub_frame(payload)), [payload])
        self.assertEqual(self.acc.stats["matched"], 1)

    def test_frame_split_across_reads(self):
        payload = b'{"id":1,"c":"win","v":3}'
        frame = make_pub_frame(payload)
        got = []
        for i in range(len(frame)):  # worst case: byte-at-a-time UART reads
            got += self.acc.feed(frame[i : i + 1])
        self.assertEqual(got, [payload])

    def test_multiple_frames_one_chunk(self):
        p1, p2 = b'{"id":1,"c":"ping"}', b'{"id":2,"c":"foc","v":1}'
        chunk = make_pub_frame(p1) + make_pub_frame(p2)
        self.assertEqual(self.acc.feed(chunk), [p1, p2])

    def test_junk_between_frames_recovers(self):
        payload = b'{"id":5,"c":"awb","v":2}'
        chunk = b"\xde\xad\xbe\xef\x00" + make_pub_frame(payload) + b"noise"
        self.assertEqual(self.acc.feed(chunk), [payload])
        self.assertGreaterEqual(
            self.acc.stats["cobs_errors"] + self.acc.stats["crc_errors"], 1
        )

    def test_corrupted_frame_dropped_stream_recovers(self):
        payload = b'{"id":6,"c":"exp","v":1}'
        bad = bytearray(make_pub_frame(b'{"id":7,"c":"exp","v":2}'))
        bad[6] ^= 0xFF  # corrupt mid-frame, delimiter intact
        got = self.acc.feed(bytes(bad) + make_pub_frame(payload))
        self.assertEqual(got, [payload])
        self.assertEqual(
            self.acc.stats["cobs_errors"] + self.acc.stats["crc_errors"], 1
        )

    def test_other_topic_counted_not_matched(self):
        frame = make_pub_frame(b"12345678", topic=b"spotter/utc-time")
        self.assertEqual(self.acc.feed(frame), [])
        self.assertEqual(self.acc.stats["other_topic"], 1)

    def test_empty_blocks_and_idle_zeros_ignored(self):
        self.assertEqual(self.acc.feed(b"\x00\x00\x00"), [])
        self.assertEqual(self.acc.stats["blocks"], 0)

    def test_overflow_bounded_and_recovers(self):
        # 3x the buffer bound of delimiter-free garbage...
        self.acc.feed(bytes([1]) * (3 * self.acc.max_buffer))
        self.assertGreaterEqual(self.acc.stats["overflow_drops"], 1)
        # ...must still leave the stream able to sync on the next frame.
        payload = b'{"id":8,"c":"ping"}'
        got = self.acc.feed(b"\x00" + make_pub_frame(payload))
        self.assertEqual(got, [payload])

    def test_binary_payload_survives(self):
        payload = bytes(range(256))
        self.assertEqual(self.acc.feed(make_pub_frame(payload)), [payload])

    def test_hostile_stream_never_raises(self):
        import random

        rng = random.Random(1234)  # deterministic
        for _ in range(200):
            chunk = bytes(rng.randrange(256) for _ in range(rng.randrange(300)))
            self.acc.feed(chunk)  # must not raise
        payload = b'{"id":9,"c":"ping"}'
        got = self.acc.feed(b"\x00" + make_pub_frame(payload))
        self.assertEqual(got, [payload])

    def test_str_topic_constructor(self):
        acc = FrameAccumulator(topic="bmcam/cmd")
        payload = b'{"id":10,"c":"ping"}'
        self.assertEqual(acc.feed(make_pub_frame(payload)), [payload])


class TestRawPubScanner(unittest.TestCase):
    """The REAL inbound path (Phase B finding: mote->Pi is raw)."""

    BRIDGE_NODE = 0xC3C564B91856226C

    def setUp(self):
        self.scanner = RawPubScanner(topic=TOPIC)

    def test_real_captured_frames_decode(self):
        # The exact bytes observed on bmcam003's UART must decode.
        for payload, frame in REAL_FRAMES.items():
            scanner = RawPubScanner(topic=TOPIC)
            self.assertEqual(scanner.feed(frame), [payload], payload)
            self.assertEqual(scanner.stats["matched"], 1)

    def test_builder_matches_captured_bytes(self):
        # Our raw-frame builder must reproduce the real capture exactly.
        built = build_raw_pub_frame(self.BRIDGE_NODE, TOPIC, b"hello")
        self.assertEqual(built, REAL_FRAMES[b"hello"])

    def test_frame_split_byte_at_a_time(self):
        frame = build_raw_pub_frame(1, TOPIC, b'{"id":7,"c":"win","v":3}')
        got = []
        for i in range(len(frame)):
            got += self.scanner.feed(frame[i:i + 1])
        self.assertEqual(got, [b'{"id":7,"c":"win","v":3}'])

    def test_back_to_back_frames_no_delimiter(self):
        # Exactly as captured: two frames concatenated in one read.
        p1, p2 = b'{"id":1,"c":"ping"}', b'{"id":2,"c":"roi","v":2}'
        chunk = (build_raw_pub_frame(3, TOPIC, p1)
                 + build_raw_pub_frame(3, TOPIC, p2))
        self.assertEqual(self.scanner.feed(chunk), [p1, p2])

    def test_junk_around_frames(self):
        frame = build_raw_pub_frame(3, TOPIC, b'{"id":9,"c":"ping"}')
        got = self.scanner.feed(b"\xde\xad\x00\xbe\xef" + frame)
        self.assertEqual(got, [b'{"id":9,"c":"ping"}'])

    def test_corrupt_crc_frame_dropped_stream_recovers(self):
        bad = bytearray(build_raw_pub_frame(3, TOPIC, b'{"id":1,"c":"ping"}'))
        bad[-1] ^= 0xFF  # corrupt payload -> CRC never matches
        good = build_raw_pub_frame(3, TOPIC, b'{"id":2,"c":"ping"}')
        # Push enough padding to trip the per-frame bound, then a clean frame.
        got = self.scanner.feed(bytes(bad) + b"\x55" * 1100 + good)
        self.assertEqual(got, [b'{"id":2,"c":"ping"}'])
        self.assertGreaterEqual(self.scanner.stats["crc_scan_fail"], 1)

    def test_other_topic_ignored(self):
        frame = build_raw_pub_frame(3, b"spotter/utc-time", b"12345678")
        self.assertEqual(self.scanner.feed(frame), [])
        self.assertEqual(self.scanner.stats["matched"], 0)

    def test_empty_payload(self):
        frame = build_raw_pub_frame(3, TOPIC, b"")
        self.assertEqual(self.scanner.feed(frame), [b""])

    def test_binary_payload_with_zeros(self):
        payload = bytes(range(64))
        frame = build_raw_pub_frame(3, TOPIC, payload)
        self.assertEqual(self.scanner.feed(frame), [payload])

    def test_buffer_stays_bounded_under_garbage(self):
        # Signature-less garbage self-trims to a small tail; the buffer
        # must never grow unbounded and the next real frame must decode.
        self.scanner.feed(bytes([0x37]) * (3 * self.scanner.max_buffer))
        self.assertLessEqual(len(self.scanner._buffer), self.scanner.max_buffer)
        frame = build_raw_pub_frame(3, TOPIC, b'{"id":5,"c":"ping"}')
        self.assertEqual(self.scanner.feed(frame), [b'{"id":5,"c":"ping"}'])

    def test_hostile_stream_never_raises_and_recovers(self):
        import random

        rng = random.Random(99)
        for _ in range(200):
            self.scanner.feed(bytes(rng.randrange(256)
                                    for _ in range(rng.randrange(300))))
        frame = build_raw_pub_frame(3, TOPIC, b'{"id":6,"c":"ping"}')
        self.assertEqual(self.scanner.feed(frame), [b'{"id":6,"c":"ping"}'])


class TestCrc16Function(unittest.TestCase):
    def test_matches_bm_serial_crc(self):
        bm = BristlemouthSerial(uart=_DummyUart(), node_id=1, network_type=0x02)
        for data in (b"", b"a", b"hello world", bytes(range(256))):
            self.assertEqual(crc16(0, data), bm.crc(0, data))


if __name__ == "__main__":
    unittest.main()

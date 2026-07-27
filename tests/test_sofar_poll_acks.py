#!/usr/bin/env python3
# filename: test_sofar_poll_acks.py
# description: Sprint10 §6/§7 — unit tests for the sensor-data ack poller.
"""
Sprint10 — tests for tools/sofar_poll_acks.py (no network, no token).

Pins the ack-extraction rules against real uplink traffic shapes: hex
decode (Sprint09 Q2), acks recognized, image chunks / status lines /
garbage never misparsed as acks.

Run (repo root):
  python3 -m unittest tests.test_sofar_poll_acks -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import sofar_poll_acks as spa  # noqa: E402

ACK = '{"id":801,"ok":1,"st":{"roi":0,"foc":0,"awb":0,"exp":0,"win":0}}'


class TestDecodeValue(unittest.TestCase):
    def test_hex_roundtrip(self):
        self.assertEqual(spa.decode_value(ACK.encode().hex()), ACK)

    def test_non_hex_returns_none(self):
        self.assertIsNone(spa.decode_value("not-hex!"))
        # odd-length hex string
        self.assertIsNone(spa.decode_value("abc"))


class TestExtractAck(unittest.TestCase):
    def test_real_ack_extracted(self):
        ack = spa.extract_ack(ACK)
        self.assertEqual(ack["id"], 801)
        self.assertEqual(ack["ok"], 1)
        self.assertEqual(ack["st"]["roi"], 0)

    def test_error_ack_extracted(self):
        ack = spa.extract_ack('{"id":202,"ok":0,"e":"val","st":{}}')
        self.assertEqual((ack["id"], ack["ok"], ack["e"]), (202, 0, "val"))

    def test_image_chunk_not_an_ack(self):
        self.assertIsNone(spa.extract_ack("<I17>aGVsbG8gd29ybGQ="))

    def test_status_line_not_an_ack(self):
        self.assertIsNone(spa.extract_ack("START,q80,115"))

    def test_json_without_id_ok_not_an_ack(self):
        self.assertIsNone(spa.extract_ack('{"lat":1.0,"lon":2.0}'))
        self.assertIsNone(spa.extract_ack('{"id":"abc","ok":1}'))

    def test_none_and_garbage(self):
        self.assertIsNone(spa.extract_ack(None))
        self.assertIsNone(spa.extract_ack("{{{"))
        self.assertIsNone(spa.extract_ack("[1,2,3]"))


if __name__ == "__main__":
    unittest.main()

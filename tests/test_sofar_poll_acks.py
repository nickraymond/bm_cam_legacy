#!/usr/bin/env python3
# filename: test_sofar_poll_acks.py
# description: Sprint10 §6/§7 / Sprint26 S4 c.3 — unit tests for the sensor-data ack poller (v9 slim acks, <CF>).
"""
Tests for tools/sofar_poll_acks.py (no network, no token).

Pins the ack-extraction rules against real uplink traffic shapes: hex
decode (Sprint09 Q2), v9 slim acks recognized and printed with
`h e k s d v` (no `st`), <CF> lines parsed (h, n, err/reverted head,
key=value@src items, %XX unescaped) — both built with the unit's own
command_wire builders — and image chunks / status lines / <HL> /
garbage never misparsed as acks.

Run (repo root):
  python3 -m unittest tests.test_sofar_poll_acks -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_wire as W  # noqa: E402
import sofar_poll_acks as spa  # noqa: E402

ACK = W.build_ack(1_000_801, True, h="a41c09e2")


class TestDecodeValue(unittest.TestCase):
    def test_hex_roundtrip(self):
        self.assertEqual(spa.decode_value(ACK.encode().hex()), ACK)

    def test_non_hex_returns_none(self):
        self.assertIsNone(spa.decode_value("not-hex!"))
        # odd-length hex string
        self.assertIsNone(spa.decode_value("abc"))


class TestExtractAck(unittest.TestCase):
    def test_slim_ack_extracted(self):
        ack = spa.extract_ack(ACK)
        self.assertEqual(ack, {"id": 1_000_801, "ok": 1, "h": "a41c09e2"})

    def test_error_ack_extracted(self):
        ack = spa.extract_ack(W.build_ack(1_000_202, False, h="a41c09e2", e="val",
                                          k="still.crop"))
        self.assertEqual((ack["id"], ack["ok"], ack["e"], ack["k"]),
                         (1_000_202, 0, "val", "still.crop"))

    def test_cf_and_hl_not_acks(self):
        self.assertIsNone(spa.extract_ack(W.build_cf("a41c09e2", [("mode.run", "per_boot")])[0]))
        self.assertIsNone(spa.extract_ack("<HL v=1 key=k1 a=sent n=3 r=ok id=100001>"))

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


class TestFmt(unittest.TestCase):
    def test_v9_fields_printed_no_st(self):
        line = spa.fmt("T", {"id": 1_000_001, "ok": 1, "h": "a41c09e2", "s": 1,
                             "d": 1, "v": 30}, "53171fa3d81a8e6f")
        self.assertEqual(line, "T  id=1000001 ok=1 h=a41c09e2 s=1 d=1 v=30 "
                               "node=53171fa3d81a8e6f")
        line = spa.fmt("T", {"id": 1_000_002, "ok": 0, "e": "key", "k": "roi"})
        self.assertEqual(line, "T  id=1000002 ok=0 e=key k=roi")
        self.assertNotIn("st=", spa.fmt("T", {"id": 1, "ok": 1, "st": {"roi": 2}}))


class TestExtractCf(unittest.TestCase):
    def test_get_answer_with_sources(self):
        line = W.build_cf("a41c09e2", [("still.crop", [0, 0, 4608, 2592], ("cmd", 1_000_751)),
                                       ("schedule.timezone", "America/New_York", "default"),
                                       ("mode.run", "per_boot", "yaml")])[0]
        cf = spa.extract_cf(line)
        self.assertEqual(cf["h"], "a41c09e2")
        self.assertIsNone(cf["n"])
        self.assertEqual(cf["items"], [("still.crop", "0,0,4608,2592", "c1000751"),
                                       ("schedule.timezone", "America/New_York", "d"),
                                       ("mode.run", "per_boot", None)])
        self.assertIn("still.crop=0,0,4608,2592@c1000751", spa.fmt_cf("T", cf))

    def test_escaped_value_round_trips(self):
        line = W.build_cf("a41c09e2", [("video.record.framing", "a b@c%")])[0]
        self.assertEqual(spa.extract_cf(line)["items"][0][1], "a b@c%")

    def test_multi_part(self):
        items = [(f"camera.image_processing.k{i}", "x" * 40) for i in range(12)]
        parts = W.build_cf("a41c09e2", items)
        self.assertGreater(len(parts), 1)
        cfs = [spa.extract_cf(p) for p in parts]
        self.assertEqual([c["n"] for c in cfs],
                         [(i, len(parts)) for i in range(1, len(parts) + 1)])
        self.assertEqual(sum(len(c["items"]) for c in cfs), 12)

    def test_err_and_reverted_heads(self):
        err = spa.extract_cf(W.build_cf("a41c09e2", [], head=[("err", "overlay"),
                                                             ("k", "still.crop")])[0])
        self.assertEqual((err["err"], err["k"], err["items"]),
                         ("overlay", "still.crop", []))
        rev = spa.extract_cf(W.build_cf("a41c09e2", [], head=[
            ("reverted", "mode.output"), ("lim", "3boots"), ("ref", 1_000_007)])[0])
        self.assertEqual((rev["reverted"], rev["lim"], rev["ref"]),
                         ("mode.output", "3boots", "1000007"))
        self.assertIn("reverted=mode.output", spa.fmt_cf("T", rev))

    def test_not_cf(self):
        for text in (None, "<CFX v=1>", "<HL v=1 key=k a=sent>", ACK, "<CF v=1 h=1"):
            self.assertIsNone(spa.extract_cf(text), text)


class TestReplies(unittest.TestCase):
    def test_rows_classified(self):
        def row(text):
            return {"timestamp": "T", "value": text.encode().hex(),
                    "bristlemouth_node_id": "0x53171fa3d81a8e6f"}
        rows = [row(ACK), row(W.build_cf("a41c09e2", [("mode.run", "stay_on")])[0]),
                row("<HL v=1 key=k1 a=sent n=3 r=ok id=100001>"), row("<I3>abcd")]
        out = spa.replies_from_rows(rows)
        self.assertEqual([k for _, k, _, _ in out], ["ack", "cf"])
        self.assertEqual(out[0][3], "53171fa3d81a8e6f")

    def test_tag_fields(self):
        self.assertEqual(spa.tag_fields("<HL v=1 key=k1 a=sent n=3 r=ok id=100001 w=w9>\n",
                                        "HL"),
                         {"v": "1", "key": "k1", "a": "sent", "n": "3", "r": "ok",
                          "id": "100001", "w": "w9"})
        self.assertIsNone(spa.tag_fields("<CF v=1 h=x>", "HL"))


class TestNormalizeNodeId(unittest.TestCase):
    def test_real_phase_c_format(self):
        # Exact format observed in Phase C acks 801/802 (2026-07-27).
        self.assertEqual(spa.normalize_node_id("0x53171fa3d81a8e6f"),
                         "53171fa3d81a8e6f")

    def test_bare_and_case(self):
        self.assertEqual(spa.normalize_node_id("53171FA3D81A8E6F"),
                         "53171fa3d81a8e6f")

    def test_missing(self):
        self.assertIsNone(spa.normalize_node_id(None))
        self.assertIsNone(spa.normalize_node_id(""))


if __name__ == "__main__":
    unittest.main()

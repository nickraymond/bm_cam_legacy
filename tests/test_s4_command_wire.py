#!/usr/bin/env python3
# filename: test_s4_command_wire.py
# description: Sprint26 S4 a.2 — commands v9 wire: hostile input, id ranges, verb shapes, 248 B budget, HMAC, slim ack, <CF>, console lines.
"""
Sprint26 S4 commit a.2 (PLAN_S4.md a.2, G8, G9; DESIGN §6.2 strict JSON, §6.3 sig).

Pins: every hostile payload in the table is refused the way the table says
(no ack for undecodable input, e:<code> otherwise); the 248 B JSON limit
counts `sig`; every §6.2 id-range edge; HMAC sign/verify incl. tampering;
the slim ack's exact bytes; <CF> escaping and chunking; ASCII console lines.

Run (repo root):
  python3 -m unittest tests.test_s4_command_wire -v
"""

import json
import os
import random
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_messages  # noqa: E402
import command_wire as W  # noqa: E402

KEY = bytes(range(32))


def dec(payload):
    if isinstance(payload, dict):
        payload = W.encode_command(payload)
    return W.decode(payload, parse_rsd=command_messages.parse_rsd)


class Unackable(unittest.TestCase):
    CASES = [
        b"not json", b"[1,2]", b'"str"', b"", b"{",
        b'{"c":"ping"}',                                   # no id
        b'{"id":true,"c":"ping"}',                         # bool id
        b'{"id":-1,"c":"ping"}', b'{"id":4294967296,"c":"ping"}',
        b'{"id":7.0,"c":"ping"}', b'{"id":"7","c":"ping"}',
        b'{"id":7,"c":"set","kv":{"camera.exposure.ev":NaN}}',
        b'{"id":7,"c":"set","kv":{"camera.exposure.ev":Infinity}}',
        b'{"id":7,"c":"set","kv":{"camera.exposure.ev":-Infinity}}',
        b'{"id":7,"c":"set","kv":{"camera.exposure.ev":1e999}}',
        b'{"id":7,"id":8,"c":"ping"}',                     # duplicate key
        b'{"id":7,"c":"set","kv":{"a.b":1,"a.b":2}}',      # duplicate key in kv
        '{"id":7,"c":"ping","x":"é"}'.encode("utf-8"),  # non-ASCII
        "{\"id\":7,\"c\":\"ping\"} ".encode("utf-8"),
        b'{"id":7,"c":"set","kv":{"a.b":' + b"9" * 30 + b"}}",   # > 2^53
    ]

    def test_table(self):
        for payload in self.CASES:
            with self.assertRaises(W.Unackable, msg=payload):
                dec(payload)

    def test_str_payload_non_ascii(self):
        with self.assertRaises(W.Unackable):
            W.decode('{"id":7,"c":"ping","to":"cоn"}')


class Rejections(unittest.TestCase):
    def reject(self, payload, code, key=None):
        with self.assertRaises(W.Rejected, msg=payload) as ctx:
            dec(payload)
        self.assertEqual(ctx.exception.code, code, payload)
        if key is not None:
            self.assertEqual(ctx.exception.key, key, payload)
        return ctx.exception

    def test_table(self):
        R = self.reject
        R({"id": 0, "c": "ping"}, "id")
        R({"id": 150_000_000 + 60_000_000, "c": "ping"}, "id")        # 2.1e8 gap
        R({"id": 1_999_999_999, "c": "ping"}, "id")
        R({"id": 7, "c": "roi", "v": 1}, "cmd")                       # v8 verb
        R({"id": 7, "c": "cfg"}, "cmd")
        R({"id": 7, "c": 5}, "cmd")
        R({"id": 7, "c": "ping", "v": 0}, "key", "v")                 # unknown field
        R({"id": 7, "c": "set"}, "val", "kv")
        R({"id": 7, "c": "set", "kv": {}}, "val", "kv")
        R({"id": 7, "c": "set", "kv": [1]}, "val", "kv")
        R({"id": 7, "c": "set", "kv": {"mode.run": "stay on"}}, "val", "mode.run")  # space
        R({"id": 7, "c": "set", "kv": {"bad key": 1}}, "val")
        R({"id": 7, "c": "set", "kv": {"x": "a" * 49}}, "val", "x")
        R({"id": 7, "c": "set", "kv": {"still.crop": [1, 2, 3, 4, 5]}}, "val", "still.crop")
        R({"id": 7, "c": "set", "kv": {"still.crop": []}}, "val", "still.crop")
        R({"id": 7, "c": "set", "kv": {"still.crop": [1, "a"]}}, "val", "still.crop")
        R({"id": 7, "c": "set", "kv": {"still.crop": [[1]]}}, "val", "still.crop")
        R({"id": 7, "c": "set", "kv": {"camera": {"focus": 1}}}, "val", "camera")    # depth 3
        R({"id": 7, "c": "set", "kv": {"a": 1}, "b": "ABCDEF01"}, "val", "b")
        R({"id": 7, "c": "set", "kv": {"a": 1}, "sig": "ABCDEF0123456789"}, "auth", "sig")
        R({"id": 7, "c": "set", "kv": {"a": 1}, "sig": "abc"}, "auth", "sig")
        R({"id": 7, "c": "get"}, "val", "k")
        R({"id": 7, "c": "get", "k": "mode"}, "val", "k")
        R({"id": 7, "c": "get", "k": ["a", "b", "c", "d", "e"]}, "val", "k")
        R({"id": 7, "c": "get", "k": ["mode"], "to": "cell"}, "val", "to")
        R({"id": 7, "c": "reset"}, "val", "k")
        R({"id": 7, "c": "reset", "k": ["a"], "all": 1}, "val", "k")
        R({"id": 7, "c": "reset", "all": True}, "val", "all")
        R({"id": 7, "c": "reset", "all": 2}, "val", "all")
        R({"id": 7, "c": "cfm"}, "val", "ref")
        R({"id": 7, "c": "cfm", "ref": 0}, "val", "ref")
        R({"id": 7, "c": "cfm", "ref": True}, "val", "ref")
        R({"id": 7, "c": "trg"}, "val", "v")
        R({"id": 7, "c": "trg", "v": 5}, "val", "v")
        R({"id": 7, "c": "trg", "v": True}, "val", "v")
        R({"id": 7, "c": "trg", "v": 0, "kv": {"d": 8}}, "val", "kv")
        R({"id": 7, "c": "hld", "v": -1}, "val", "v")
        R({"id": 7, "c": "hld", "v": 1441}, "val", "v")
        R({"id": 7, "c": "hld", "v": 30.0}, "val", "v")
        R({"id": 7, "c": "wap"}, "val", "v")
        R({"id": 7, "c": "rsd"}, "val")
        R({"id": 7, "c": "rsd", "h": [["abc", "1"]]}, "val")          # bad media key
        R({"id": 7, "c": "rsd", "x": 1, "sig": "ABC"}, "auth", "sig")     # any verb may sign

    def test_unclean_key_names_are_not_echoed(self):
        exc = self.reject({"id": 7, "c": "ping", "we ird": 1}, "key")
        self.assertIsNone(exc.key)

    def test_kv_is_capped(self):
        kv = {f"k{i}": i for i in range(W.MAX_KV + 1)}
        with self.assertRaises((W.Rejected, W.Unackable)):
            dec({"id": 7, "c": "set", "kv": kv})


class Accepted(unittest.TestCase):
    def test_verbs(self):
        ok = [
            {"id": 7, "c": "ping"}, {"id": 7, "c": "ping", "to": "con"},
            {"id": 7, "c": "help"},
            {"id": 7, "c": "get", "k": ["mode", "video.send"]},
            {"id": 7, "c": "get", "k": ["journal"], "to": "con"},
            {"id": 1_000_000, "c": "set", "kv": {"video.send.duration_s": 8,
                                                  "mode.run": "stay_on"}},
            {"id": 7, "c": "set", "kv": {"camera.white_balance.gains": [1.62, 1.91],
                                         "camera.exposure.ev": -0.0,
                                         "camera.focus.mode": None,
                                         "schedule.window.enabled": False,
                                         "still.crop": [1904, 1071, 800, 450]},
             "b": "a41c09e2"},
            {"id": 7, "c": "reset", "k": ["video.send.duration_s"]},
            {"id": 7, "c": "reset", "all": 1},
            {"id": 7, "c": "cfm", "ref": 1_000_123},
            {"id": 7, "c": "trg", "v": 2, "kv": {"d": 8, "o": "transmit"}},
            {"id": 7, "c": "trg", "v": 0},
            {"id": 7, "c": "hld", "v": 30}, {"id": 7, "c": "hld", "v": 0},
            {"id": 7, "c": "wap", "v": 1},
            {"id": 100_002, "c": "rsd", "h": [["k3a9zq", "17,40-42"]]},
            {"id": 100_003, "c": "rsd", "x": 1},
        ]
        for data in ok:
            cmd = dec(data)
            self.assertEqual((cmd.id, cmd.verb), (data["id"], data["c"]))
        self.assertEqual(dec(ok[-2]).rsd, {"h": [["k3a9zq", [17, 40, 41, 42]]]})

    def test_negative_zero_is_zero(self):
        cmd = dec({"id": 7, "c": "set", "kv": {"camera.exposure.ev": -0.0}})
        self.assertEqual(cmd.fields["kv"]["camera.exposure.ev"], 0.0)

    def test_int_stays_int(self):
        cmd = dec(b'{"id":7,"c":"set","kv":{"video.send.message_cap":80}}')
        self.assertIs(type(cmd.fields["kv"]["video.send.message_cap"]), int)


class IdRanges(unittest.TestCase):
    def test_edges(self):
        edges = {1: "console", 99_999: "console", 100_000: "heal", 999_999: "heal",
                 1_000_000: "remote", 99_999_999: "remote", 100_000_000: "service",
                 199_999_999: "service", 200_000_000: None, 1_999_999_999: None,
                 2_000_000_000: "conductor", 2**32 - 1: "conductor", 0: None}
        for cid, name in edges.items():
            self.assertEqual(W.id_range(cid), name, cid)

    def test_lanes_and_high_water(self):
        self.assertEqual(W.CELLULAR_RANGES, ("remote", "service"))
        self.assertEqual(W.HIGH_WATER_RANGES, ("remote", "service"))


class Budget(unittest.TestCase):
    def _padded(self, total, signed):
        """A set command whose compact JSON is exactly `total` bytes: values
        of 1..48 chars spread over as many kv keys as needed."""
        for nkeys in range(1, W.MAX_KV + 1):
            kv = {f"k{i}": "a" for i in range(nkeys)}
            base = {"id": 100_000_001, "c": "set", "kv": kv}
            if signed:
                base["sig"] = "0" * 16
            pad = total - len(W.encode_command(base))
            if 0 <= pad <= 47 * nkeys:
                for i in range(nkeys):
                    add = min(47, pad)
                    kv[f"k{i}"] = "a" * (1 + add)
                    pad -= add
                text = W.encode_command(base)
                self.assertEqual(len(text), total)
                return text
        self.fail("could not pad")

    def test_248_passes_249_refused_signed_and_unsigned(self):
        for signed in (False, True):
            dec(self._padded(248, signed))
            with self.assertRaises(W.Rejected) as ctx:
                dec(self._padded(249, signed))
            self.assertEqual(ctx.exception.code, "val")

    def test_console_line_limit(self):
        self.assertEqual(W.MAX_CONSOLE_LINE_BYTES, 270)
        line = "bm pub bmcam/cmd " + "x" * 248 + " 1 1\n"
        self.assertEqual(len(line), 270)


class Signatures(unittest.TestCase):
    def signed(self, data):
        data = dict(data)
        data["sig"] = W.sign(data, KEY)
        return data

    def test_round_trip_through_the_wire(self):
        data = self.signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}})
        cmd = dec(W.encode_command(data))
        self.assertTrue(W.verify_sig(cmd.raw, KEY))

    def test_key_order_and_float_text_do_not_matter(self):
        data = self.signed({"id": 100_000_002, "c": "set",
                            "kv": {"b.x": 1.5, "a.y": [1.62, 1.91]}})
        reordered = '{"sig":"%s","kv":{"a.y":[1.62,1.91],"b.x":1.5},"c":"set","id":100000002}' \
            % data["sig"]
        self.assertTrue(W.verify_sig(dec(reordered).raw, KEY))

    def test_tampering(self):
        data = self.signed({"id": 100_000_003, "c": "set", "kv": {"uplink.chunk_chars": 320}})
        for mutate in (lambda d: d["kv"].update({"uplink.chunk_chars": 321}),
                       lambda d: d.update(id=100_000_004),
                       lambda d: d.update(sig=d["sig"][:-1] + ("0" if d["sig"][-1] != "0" else "1")),
                       lambda d: d.update(sig=d["sig"].upper()),
                       lambda d: d.pop("sig")):
            bad = json.loads(json.dumps(data))
            mutate(bad)
            self.assertFalse(W.verify_sig(bad, KEY))
        self.assertFalse(W.verify_sig(data, bytes(32)))
        self.assertFalse(W.verify_sig(data, b""))
        self.assertFalse(W.verify_sig(data, None))

    def test_short_key_refused(self):
        with self.assertRaises(ValueError):
            W.sign({"id": 1}, b"short")


class Replies(unittest.TestCase):
    def test_ack_bytes(self):
        self.assertEqual(W.build_ack(7, True, h="a41c09e2"), '{"id":7,"ok":1,"h":"a41c09e2"}')
        self.assertEqual(W.build_ack(8, False, h="a41c09e2", e="xk",
                                     k="camera.white_balance.mode"),
                         '{"id":8,"ok":0,"h":"a41c09e2","e":"xk","k":"camera.white_balance.mode"}')
        self.assertEqual(W.build_ack(9, True, h="a41c09e2", s=1), '{"id":9,"ok":1,"h":"a41c09e2","s":1}')
        self.assertEqual(W.build_ack(9, True, h="a41c09e2", d=1), '{"id":9,"ok":1,"h":"a41c09e2","d":1}')
        self.assertEqual(W.build_ack(10, True, h="a41c09e2", v=30), '{"id":10,"ok":1,"h":"a41c09e2","v":30}')
        self.assertEqual(W.build_ack(11, False), '{"id":11,"ok":0,"e":"err"}')
        self.assertLessEqual(len(W.build_ack(99_999_999, True, h="a41c09e2")), 45)

    def test_cf_escaping(self):
        self.assertEqual(W.cf_value([1504, 846, 1600, 900]), "1504,846,1600,900")
        self.assertEqual(W.cf_value(None), "null")
        self.assertEqual(W.cf_value(True), "1")
        self.assertEqual(W.cf_value("a b%c>d=e"), "a%20b%25c%3Ed%3De")
        self.assertEqual(W.cf_source("yaml"), "")
        self.assertEqual(W.cf_source("default"), "@d")
        self.assertEqual(W.cf_source(("cmd", 1_000_005)), "@c1000005")

    def test_cf_single_part(self):
        out = W.build_cf("a41c09e2", [("mode.media", "still"), ("mode.run", "per_boot", "yaml"),
                                      ("still.crop", [1904, 1071, 800, 450], ("cmd", 1_000_005))])
        self.assertEqual(out, ["<CF v=1 h=a41c09e2 mode.media=still mode.run=per_boot "
                               "still.crop=1904,1071,800,450@c1000005>"])

    def test_cf_chunks_and_charset(self):
        items = [(f"camera.image_processing.k{i}", "v" * 20) for i in range(30)]
        out = W.build_cf("a41c09e2", items, head=[("reverted", "mode.output")])
        self.assertGreater(len(out), 1)
        for i, msg in enumerate(out, 1):
            self.assertLessEqual(len(msg), W.MAX_CF_BYTES)
            self.assertIn(f" n={i}/{len(out)} reverted=mode.output ", msg)
            self.assertTrue(msg.startswith("<CF v=1 h=a41c09e2") and msg.endswith(">"))
            self.assertEqual(msg.count("<"), 1)
            self.assertEqual(msg.count(">"), 1)
        joined = " ".join(out)
        for key, _ in items:
            self.assertIn(key + "=", joined)

    def test_cf_non_ascii_escapes_are_distinct(self):
        self.assertNotEqual(W.cf_value("\u0100"), W.cf_value("\u0000"))
        self.assertEqual(W.cf_value("\u00e9"), "%C3%A9")

    def test_cf_cut_never_splits_an_escape(self):
        for n in range(200, 280):
            out = W.build_cf("a41c09e2", [("k", "a" * n + " " * 40)])
            body = out[0][:-2]                      # drop "~>"
            self.assertNotRegex(body, r"%[0-9A-F]?$", n)

    def test_rejected_is_a_normal_exception(self):
        exc = W.Rejected(7, "val", "k", "why")
        self.assertEqual(exc.args[:2], (7, "val"))
        {exc}                                      # hashable

    def test_cf_giant_item_is_cut_not_split(self):
        out = W.build_cf("a41c09e2", [("k", "x" * 1000)])
        self.assertEqual(len(out), 1)
        self.assertLessEqual(len(out[0]), W.MAX_CF_BYTES)
        self.assertTrue(out[0].endswith("~>"))

    def test_console_ascii(self):
        line = W.console_line("bmcam003", "OK id=7 café\n\ttab")
        self.assertTrue(all(32 <= ord(c) < 127 for c in line))
        self.assertTrue(line.startswith("[bmcam003] OK id=7"))
        self.assertLessEqual(len(W.console_line("h", "x" * 1000)), W.MAX_CONSOLE_CHARS)


class Fuzz(unittest.TestCase):
    def test_random_bytes_never_escape(self):
        rng = random.Random(26)
        alphabet = b'{}[]":,0123456789.-eE+ abcdefghijklmnopqrstuvwxyzNIaf\\\x00\xff'
        for _ in range(3000):
            payload = bytes(rng.choice(alphabet) for _ in range(rng.randint(0, 80)))
            try:
                dec(payload)
            except (W.Unackable, W.Rejected):
                pass

    def test_mutated_valid_commands_never_escape(self):
        rng = random.Random(9)
        seeds = [W.encode_command(d).encode() for d in (
            {"id": 1_000_001, "c": "set", "kv": {"still.crop": [1, 2, 3, 4], "d": 8.5}},
            {"id": 100_001, "c": "rsd", "h": [["k3a9zq", "1-3,9"]]},
            {"id": 7, "c": "trg", "v": 2, "kv": {"m": 80}})]
        for _ in range(3000):
            b = bytearray(rng.choice(seeds))
            for _ in range(rng.randint(1, 4)):
                b[rng.randrange(len(b))] = rng.randrange(256)
            try:
                cmd = dec(bytes(b))
                self.assertIn(cmd.verb, W.VERBS)
            except (W.Unackable, W.Rejected):
                pass


if __name__ == "__main__":
    unittest.main()

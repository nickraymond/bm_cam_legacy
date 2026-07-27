#!/usr/bin/env python3
# filename: test_command_messages.py
# description: Sprint10 §1/§4 — parser accept/reject matrix + ack-string pins.
"""
Sprint10 — tests for command_messages.py.

The accept/reject matrix here is the TRACKER §4 "parser accept/reject
matrix" artifact: every accept path for all six commands, and a hostile-
input sweep (bad JSON, wrong types, bool aliases, out-of-range ids,
unknown commands/indices) proving the parser never raises and classifies
every failure with the right error code + ackability.

Ack tests pin the exact wire strings (GUI + backend read these) and the
Sprint09 size constraint (ack << 384-char uplink chunk).

Run (repo root; pure module, no hardware):
  python3 -m unittest tests.test_command_messages -v
"""

import json
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_tables as ct  # noqa: E402
from command_messages import (  # noqa: E402
    ERR_CMD,
    ERR_ID,
    ERR_JSON,
    ERR_VAL,
    MAX_COMMAND_ID,
    build_ack,
    parse_command,
)

FACTORY = dict(ct.DEFAULT_SETTINGS)


class TestParserAccepts(unittest.TestCase):
    def test_every_command_every_valid_index_accepted(self):
        for cmd in ct.COMMANDS:
            for v in ct.table_for(cmd):
                payload = json.dumps({"id": 1, "c": cmd, "v": v})
                result = parse_command(payload)
                self.assertTrue(result["ok"], f"{cmd}[{v}]: {result}")
                self.assertEqual(result["cmd"], cmd)
                self.assertEqual(result["value"], v)
                self.assertEqual(result["id"], 1)
                self.assertIsNone(result["error"])

    def test_bytes_payload_accepted(self):
        result = parse_command(b'{"id": 417, "c": "roi", "v": 2}')
        self.assertTrue(result["ok"])
        self.assertEqual((result["id"], result["cmd"], result["value"]), (417, "roi", 2))

    def test_ping_may_omit_value(self):
        result = parse_command('{"id": 5, "c": "ping"}')
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 0)

    def test_unknown_extra_keys_tolerated(self):
        # Forward compatibility: extra fields must not reject the command.
        result = parse_command('{"id": 7, "c": "win", "v": 3, "future": "x"}')
        self.assertTrue(result["ok"])

    def test_id_bounds_accepted(self):
        for cid in (0, MAX_COMMAND_ID):
            result = parse_command(json.dumps({"id": cid, "c": "ping"}))
            self.assertTrue(result["ok"], f"id={cid}")


class TestParserRejects(unittest.TestCase):
    def _reject(self, payload, error, ackable):
        result = parse_command(payload)
        self.assertFalse(result["ok"], f"{payload!r} unexpectedly accepted")
        self.assertEqual(result["error"], error, f"{payload!r}: {result}")
        if ackable:
            self.assertIsNotNone(result["id"], f"{payload!r} should be ackable")
        else:
            self.assertIsNone(result["id"], f"{payload!r} should be unackable")
        return result

    # --- unackable: bad JSON ---
    def test_not_json(self):
        self._reject(b"\x00\xff garbled \x02", ERR_JSON, ackable=False)
        self._reject("not json at all", ERR_JSON, ackable=False)
        self._reject("", ERR_JSON, ackable=False)
        self._reject('{"id": 1, "c": "roi"', ERR_JSON, ackable=False)  # truncated

    def test_json_but_not_object(self):
        self._reject("[1, 2, 3]", ERR_JSON, ackable=False)
        self._reject('"roi"', ERR_JSON, ackable=False)
        self._reject("417", ERR_JSON, ackable=False)

    def test_invalid_utf8_bytes(self):
        self._reject(b'{"id": 1, "c": "\xff\xfe"}', ERR_JSON, ackable=False)

    def test_none_payload(self):
        self._reject(None, ERR_JSON, ackable=False)

    # --- unackable: bad id ---
    def test_missing_id(self):
        self._reject('{"c": "roi", "v": 2}', ERR_ID, ackable=False)

    def test_id_wrong_type(self):
        self._reject('{"id": "417", "c": "roi", "v": 2}', ERR_ID, ackable=False)
        self._reject('{"id": 4.5, "c": "roi", "v": 2}', ERR_ID, ackable=False)
        self._reject('{"id": true, "c": "roi", "v": 2}', ERR_ID, ackable=False)
        self._reject('{"id": null, "c": "roi", "v": 2}', ERR_ID, ackable=False)

    def test_id_out_of_range(self):
        self._reject('{"id": -1, "c": "roi", "v": 2}', ERR_ID, ackable=False)
        self._reject(
            json.dumps({"id": MAX_COMMAND_ID + 1, "c": "roi", "v": 2}),
            ERR_ID,
            ackable=False,
        )

    # --- ackable: bad command ---
    def test_unknown_command(self):
        self._reject('{"id": 9, "c": "reboot", "v": 0}', ERR_CMD, ackable=True)
        self._reject('{"id": 9, "c": "ROI", "v": 0}', ERR_CMD, ackable=True)

    def test_missing_or_nonstring_command(self):
        self._reject('{"id": 9, "v": 0}', ERR_CMD, ackable=True)
        self._reject('{"id": 9, "c": 1, "v": 0}', ERR_CMD, ackable=True)
        self._reject('{"id": 9, "c": null, "v": 0}', ERR_CMD, ackable=True)

    # --- ackable: bad value ---
    def test_out_of_table_index(self):
        self._reject('{"id": 9, "c": "roi", "v": 99}', ERR_VAL, ackable=True)
        self._reject('{"id": 9, "c": "win", "v": -1}', ERR_VAL, ackable=True)
        self._reject('{"id": 9, "c": "ping", "v": 1}', ERR_VAL, ackable=True)

    def test_value_wrong_type(self):
        self._reject('{"id": 9, "c": "roi", "v": "2"}', ERR_VAL, ackable=True)
        self._reject('{"id": 9, "c": "roi", "v": 2.0}', ERR_VAL, ackable=True)
        self._reject('{"id": 9, "c": "roi", "v": true}', ERR_VAL, ackable=True)
        self._reject('{"id": 9, "c": "roi", "v": null}', ERR_VAL, ackable=True)

    def test_missing_value_on_settings_command(self):
        for cmd in ct.SETTINGS_COMMANDS:
            self._reject(json.dumps({"id": 9, "c": cmd}), ERR_VAL, ackable=True)

    def test_parser_never_raises_on_hostile_bytes(self):
        hostile = [
            b"\x00" * 64,
            b"{" * 1000,
            b'{"id": 1e309, "c": "roi", "v": 2}',  # float overflow -> inf
            bytes(range(256)),
            "🌊🐟".encode("utf-8"),
        ]
        for payload in hostile:
            result = parse_command(payload)  # must not raise
            self.assertFalse(result["ok"], f"{payload[:20]!r} accepted")


class TestAckBuilder(unittest.TestCase):
    def test_applied_ack_exact_string(self):
        st = {"roi": 2, "foc": 0, "awb": 0, "exp": 0, "win": 0}
        self.assertEqual(
            build_ack(417, True, st),
            '{"id":417,"ok":1,"st":{"roi":2,"foc":0,"awb":0,"exp":0,"win":0}}',
        )

    def test_reject_ack_carries_error_code(self):
        ack = json.loads(build_ack(9, False, FACTORY, error=ERR_VAL))
        self.assertEqual(ack["ok"], 0)
        self.assertEqual(ack["e"], "val")
        self.assertEqual(ack["st"], FACTORY)

    def test_reject_without_code_defaults_to_err(self):
        ack = json.loads(build_ack(9, False, FACTORY))
        self.assertEqual(ack["e"], "err")

    def test_missing_settings_keys_filled_from_defaults(self):
        ack = json.loads(build_ack(1, True, {"roi": 4}))
        self.assertEqual(ack["st"], {"roi": 4, "foc": 0, "awb": 0, "exp": 0, "win": 0})

    def test_st_always_complete_and_int(self):
        ack = json.loads(build_ack(1, True, {"roi": 1, "win": "3"}))
        self.assertEqual(sorted(ack["st"]), sorted(ct.SETTINGS_COMMANDS))
        for value in ack["st"].values():
            self.assertIsInstance(value, int)

    def test_ack_fits_uplink_chunk_at_worst_case(self):
        # Sprint09 locked 384 b64 chars/msg; worst-case ack must fit one
        # message with huge margin.
        worst = build_ack(
            MAX_COMMAND_ID,
            False,
            {k: 9 for k in ct.SETTINGS_COMMANDS},
            error=ERR_CMD,
        )
        self.assertLess(len(worst.encode("ascii")), 384)

    def test_roundtrip_with_parser_errors(self):
        # End-to-end shape: parse a bad-value command, ack it, re-parse ack.
        result = parse_command('{"id": 31, "c": "awb", "v": 42}')
        self.assertFalse(result["ok"])
        ack = json.loads(build_ack(result["id"], False, FACTORY, error=result["error"]))
        self.assertEqual(ack["id"], 31)
        self.assertEqual(ack["e"], "val")


if __name__ == "__main__":
    unittest.main()

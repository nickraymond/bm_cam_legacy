#!/usr/bin/env python3
# filename: test_s4_replies.py
# description: Sprint26 S4 W8a (b.2b) — v9 replies: slim ack only for remote/service ids, one ASCII console line per answer, hash failure never fails an answer.
"""
Sprint26 S4 commit b.2b (PLAN_S4.md G7, G8, G13; DESIGN §6.1 reply lanes).

Pins: a remote or service id gets a cellular slim ack AND a console line; a
console, heal, conductor or out-of-range id gets the console line only; the
line says OK/REJECTED, the id, what happened and cfg=<hash>; a duplicate is
marked; a hash_fn failure still answers. The daemon routes through the policy
only when `v9` is set (legacy/v8 acks unchanged).

Run (repo root):
  python3 -m unittest tests.test_s4_replies -v
"""

import json
import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_replies as CR  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402


def policy(h="a41c09e2"):
    return CR.V9Replies("bmcam003", lambda: h)


class Lanes(unittest.TestCase):
    def test_remote_and_service_get_a_cellular_ack(self):
        for cid in (1_000_000, 99_999_999, 100_000_000, 199_999_999):
            ack, lines = policy().reply(cid, True, None, {"cmd": "ping"})
            self.assertEqual(json.loads(ack), {"id": cid, "ok": 1, "h": "a41c09e2"})
            self.assertEqual(lines, [f"[bmcam003] OK id={cid} ping cfg=a41c09e2"])

    def test_other_ranges_are_console_only(self):
        for cid in (1, 99_999, 100_000, 999_999, 2_000_000_000, 0, 150_000_000 * 2):
            ack, lines = policy().reply(cid, True, None, {"cmd": "ping"})
            self.assertIsNone(ack, cid)
            self.assertEqual(len(lines), 1)

    def test_rejection_duplicate_and_fields(self):
        ack, lines = policy().reply(1_000_001, False, "val", {"cmd": "roi"}, key="still.crop")
        self.assertEqual(json.loads(ack), {"id": 1_000_001, "ok": 0, "h": "a41c09e2",
                                           "e": "val", "k": "still.crop"})
        self.assertIn("REJECTED id=1000001", lines[0])
        self.assertIn("e=val k=still.crop", lines[0])
        ack, lines = policy().reply(1_000_002, True, None, {"cmd": "ping"}, duplicate=True)
        self.assertEqual(json.loads(ack)["d"], 1)
        self.assertIn("duplicate", lines[0])
        ack, _ = policy().reply(1_000_003, True, None, {}, staged=True, granted=30)
        self.assertEqual(json.loads(ack), {"id": 1_000_003, "ok": 1, "h": "a41c09e2",
                                           "s": 1, "v": 30})

    def test_texts(self):
        cases = [({"cmd": "trg", "value": 2}, "trg 2 armed: capture + output per mode"),
                 ({"cmd": "trg", "value": 0}, "trg 0: pending trigger cancelled"),
                 ({"cmd": "rsd", "value": {"x": 1}}, "every pending heal cancelled"),
                 ({"cmd": "rsd", "value": {"h": [["k3a9zq", [1]]]}}, "1 heal(s) queued"),
                 ({"cmd": "wap", "value": 1}, "wap 1: network change dispatched"),
                 ({"cmd": "help"}, "help (reference follows)"),
                 ({"cmd": "txd", "value": 1}, "txd=1 (next action)")]
        for result, want in cases:
            _ack, lines = policy().reply(7, True, None, result)
            self.assertIn(want, lines[0], result)
            self.assertTrue(all(32 <= ord(c) < 127 for c in lines[0]))

    def test_hash_failure_still_answers(self):
        def boom():
            raise OSError("state gone")
        p = CR.V9Replies("h", boom)
        ack, lines = quiet(p.reply, 1_000_001, True, None, {"cmd": "ping"})
        self.assertEqual(json.loads(ack), {"id": 1_000_001, "ok": 1})
        self.assertNotIn("cfg=", lines[0])


class FakeUart:
    timeout = 0.1


class FakeBm:
    def __init__(self):
        self.uart = FakeUart()
        self.tx, self.console = [], []

    def spotter_tx(self, text):
        self.tx.append(text)

    def spotter_print(self, text):
        self.console.append(text)


class Daemon(unittest.TestCase):
    def daemon(self, v9):
        tmp = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, tmp, True)
        d = CommandDaemon(FakeBm(), quiet(CommandState, path=os.path.join(tmp, "s.json")))
        d.v9 = policy() if v9 else None
        return d

    def feed(self, d, *payloads):
        for p in payloads:
            d._inbound.put(json.dumps(p).encode())
        quiet(d.process_pending)
        quiet(d.drain_console, sleep_fn=lambda s: None)
        t = [0.0]

        def clock():
            t[0] += 5.0
            return t[0]
        while d.pending_acks:
            quiet(d.drain_acks, clock=clock)

    def test_v9_routes_by_range(self):
        d = self.daemon(v9=True)
        self.feed(d, {"id": 7, "c": "ping"}, {"id": 1_000_001, "c": "ping"},
                  {"id": 1_000_002, "c": "roi", "v": 99})
        self.assertEqual(d.bm.tx, ['{"id":1000001,"ok":1,"h":"a41c09e2"}',
                                   '{"id":1000002,"ok":0,"h":"a41c09e2","e":"val"}'])
        self.assertEqual(len(d.bm.console), 3)
        self.assertIn("REJECTED id=1000002", d.bm.console[2])

    def test_v8_unchanged(self):
        d = self.daemon(v9=False)
        self.feed(d, {"id": 7, "c": "ping"})
        self.assertEqual(len(d.bm.tx), 1)
        self.assertIn('"st":', d.bm.tx[0])
        self.assertEqual(d.bm.console, [])


if __name__ == "__main__":
    unittest.main()

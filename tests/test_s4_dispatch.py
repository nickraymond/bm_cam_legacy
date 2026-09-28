#!/usr/bin/env python3
# filename: test_s4_dispatch.py
# description: Sprint26 S4 b.2c — the v9 dispatcher: every verb and rejection path, dedupe v2 + cellular rule, high-water, CAS, D15, journal.
"""
Sprint26 S4 commit b.2c (PLAN_S4.md G5, G8, G9, G13; DESIGN §6.1–6.2; R16).

A real CommandDaemon (fake BM port) with a V9State in a temp dir and
command_v9.Dispatcher; payloads go in as bytes, and the test reads the
cellular acks, console lines, state file and journal.

Run (repo root):
  python3 -m unittest tests.test_s4_dispatch -v
"""

import json
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import atomic_io  # noqa: E402
import command_replies  # noqa: E402
import command_state_v9 as S  # noqa: E402
import command_v9 as V  # noqa: E402
import command_wire as W  # noqa: E402
import config_journal  # noqa: E402
import config_registry as R  # noqa: E402
import supervisor_config as SC  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_replies import FakeBm  # noqa: E402

KEY = bytes(range(32))


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class Rig:
    def __init__(self, case, base_over=None, service_key=KEY):
        self.dir = tempfile.mkdtemp(prefix="v9disp_")
        case.addCleanup(shutil.rmtree, self.dir, True)
        self.state_path = os.path.join(self.dir, "bm_command_state_v2.json")
        self.base = R.defaults()
        self.base.update({"mode.media": "still", "commands.enabled": True,
                          "commands.runtime": "supervisor",
                          "commands.state_path": self.state_path})
        self.base.update(base_over or {})
        self.clock = Clock()
        self.new_process(service_key)

    def new_process(self, service_key=KEY):
        self.state = S.V9State(self.state_path, log=lambda *_: None)
        self.daemon = CommandDaemon(FakeBm(), self.state)
        self.daemon.v9 = command_replies.V9Replies("bmcam003", SC.make_hash_fn(self.base))
        self.daemon.v9_dispatch = V.Dispatcher(self.daemon, self.state, self.base,
                                               clock=self.clock, service_key=service_key)
        self.daemon.wap_action_fn = mock.Mock()
        self.daemon.heal_validate_fn = lambda key, ns: (key != "zzzzzz", "no_record")

    def send(self, *cmds):
        events = []
        for c in cmds:
            payload = c if isinstance(c, (bytes, str)) else W.encode_command(c)
            self.daemon._inbound.put(payload.encode() if isinstance(payload, str) else payload)
            events += quiet(self.daemon.process_pending)
        return events

    def acks(self):
        """The JSON acks queued since the last call (<CF> lines are dropped)."""
        out = [json.loads(a) for a in self.daemon._acks if a.startswith("{")]
        self.daemon._acks.clear()
        return out

    def lines(self):
        out = list(self.daemon._console)
        self.daemon._console.clear()
        return out

    def hash(self):
        return SC.make_hash_fn(self.base)()

    def journal(self):
        return config_journal.read(config_journal.path_beside(self.state_path))


def signed(data):
    data = dict(data)
    data["sig"] = W.sign(data, KEY)
    return data


class Verbs(unittest.TestCase):
    def test_ping_help_lanes(self):
        r = Rig(self)
        r.send({"id": 7, "c": "ping"}, {"id": 1_000_001, "c": "help"})
        self.assertEqual(r.acks(), [{"id": 1_000_001, "ok": 1, "h": r.hash()}])
        lines = r.lines()
        self.assertIn("OK id=7 ping", lines[0])
        self.assertTrue(any(line.startswith("v9 commands") for line in lines))

    def test_set_short_names_and_hash(self):
        r = Rig(self)
        h0 = r.hash()
        r.send({"id": 1_000_001, "c": "set", "kv": {"r": [768, 432, 3072, 1728], "m": 150,
                                                      "e": -1.0}})
        ack = r.acks()[0]
        self.assertEqual(ack["ok"], 1)
        self.assertNotEqual(ack["h"], h0)
        self.assertEqual(ack["h"], r.hash())
        self.assertEqual(S.V9State(r.state_path).overlay,
                         {"still.crop": [768, 432, 3072, 1728], "still.message_cap": 150,
                          "camera.exposure.ev": -1.0})
        self.assertIn("still.message_cap: 195 -> 150", r.lines()[0])
        self.assertEqual({e["key"]: e["src"] for e in r.journal()},
                         {"still.crop": "remote", "still.message_cap": "remote",
                          "camera.exposure.ev": "remote"})

    def test_m_follows_the_media(self):
        r = Rig(self, base_over={"mode.media": "video"})
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 90}})
        self.assertEqual(S.V9State(r.state_path).overlay, {"video.send.message_cap": 90})

    def test_set_rejections(self):
        r = Rig(self)
        cases = [
            ({"no.such": 1}, "key", "no.such"),
            ({"c": [1, 2, 3, 4]}, "key", "c"),                          # retired short name
            ({"still.message_cap": True}, "val", "still.message_cap"),
            ({"still.message_cap": 9999}, "val", "still.message_cap"),
            ({"camera.exposure.ev": "x"}, "val", "camera.exposure.ev"),
            ({"power.halt.script_path": "/tmp/x"}, "lock", "power.halt.script_path"),
            ({"commands.runtime": "legacy"}, "lock", "commands.runtime"),
            ({"uplink.chunk_chars": 320}, "auth", "uplink.chunk_chars"),
            ({"camera.white_balance.mode": "manual"}, "xk", "camera.white_balance.mode"),
            ({"still.crop": [4000, 2000, 1000, 900]}, "xk", "still.crop"),
            ({"r": [1, 1, 10, 10], "still.crop": [1, 1, 10, 10]}, "key", "still.crop"),
            ({"schedule.timezone": "Mars/Olympus"}, "val", "schedule.timezone"),   # no env: zoneinfo
        ]
        cid = 1_000_100
        before = open(r.state_path).read() if os.path.exists(r.state_path) else None
        h0 = r.hash()
        for kv, code, key in cases:
            cid += 1
            r.send({"id": cid, "c": "set", "kv": kv})
            ack = r.acks()[0]
            self.assertEqual((ack["ok"], ack.get("e"), ack.get("k")), (0, code, key), kv)
        self.assertEqual(S.V9State(r.state_path).overlay, {})
        self.assertEqual(r.hash(), h0)
        self.assertNotEqual(before, open(r.state_path).read())       # answers are cached

    def test_env_zone_probe(self):
        r = Rig(self)
        r.daemon.v9_dispatch.env = {"ffmpeg": True, "timezones_ok": {"America/Los_Angeles"}}
        r.send({"id": 1_000_001, "c": "set", "kv": {"schedule.timezone": "America/New_York"}})
        self.assertEqual(r.acks()[0]["ok"], 1)
        r.send({"id": 1_000_002, "c": "set", "kv": {"schedule.timezone": "Mars/Olympus"}})
        self.assertEqual(r.acks()[0]["e"], "xk")

    def test_halt_false_is_not_guarded(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": False,
                                                      "mode.output": "transmit"}})
        self.assertEqual(r.acks()[0]["ok"], 1)

    def test_service_with_a_good_sig_applies_guarded(self):
        r = Rig(self)
        r.send(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}))
        self.assertEqual(r.acks()[0]["ok"], 1)            # guarded_revert (test_s4_guards)
        r2 = Rig(self, service_key=None)
        r2.send(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}))
        self.assertEqual(r2.acks()[0]["e"], "auth")

    def test_reset(self):
        r = Rig(self)
        h0 = r.hash()
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150, "e": -1.0, "f": "auto"}})
        r.send({"id": 1_000_002, "c": "reset", "k": ["m"]})
        self.assertEqual(sorted(S.V9State(r.state_path).overlay),
                         ["camera.exposure.ev", "camera.focus.mode"])
        r.send({"id": 1_000_003, "c": "reset", "k": ["camera"]})           # a group
        self.assertEqual(S.V9State(r.state_path).overlay, {})
        self.assertEqual(r.hash(), h0)
        r.send({"id": 1_000_004, "c": "set", "kv": {"m": 150}},
               {"id": 1_000_005, "c": "reset", "all": 1})
        self.assertEqual(S.V9State(r.state_path).overlay, {})
        r.acks()
        r.send({"id": 1_000_006, "c": "reset", "k": ["nope"]})
        self.assertEqual(r.acks()[0]["e"], "key")

    def test_reset_keeps_a_valid_config(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"camera.white_balance.gains": [1.6, 1.9],
                                                      "b": "manual"}})
        r.send({"id": 1_000_002, "c": "reset", "k": ["camera.white_balance.gains"]})
        acks = r.acks()
        self.assertEqual((acks[0]["ok"], acks[1].get("e")), (1, "xk"))

    def test_cas(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}, "b": "00000000"})
        self.assertEqual(r.acks()[0]["e"], "cas")
        r.send({"id": 1_000_002, "c": "set", "kv": {"m": 150}, "b": r.hash()})
        self.assertEqual(r.acks()[0]["ok"], 1)

    def test_trg(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2})
        self.assertEqual(S.V9State(r.state_path).pending_trigger,
                         {"id": 1_000_001, "value": 2, "kv": {}})
        r.send({"id": 1_000_002, "c": "trg", "v": 0})
        self.assertIsNone(S.V9State(r.state_path).pending_trigger)
        r.send({"id": 1_000_003, "c": "trg", "v": 2, "kv": {"d": 8}})
        self.assertEqual(r.acks()[-1]["e"], "key")

    def test_rsd(self):
        r = Rig(self)
        r.send({"id": 100_002, "c": "rsd", "h": [["k3a9zq", "1-2"]]},
               {"id": 100_003, "c": "rsd", "h": [["zzzzzz", "1"]]})
        self.assertEqual(r.acks(), [])                       # heal ids: console only
        lines = r.lines()
        self.assertIn("OK id=100002 rsd: 1 heal(s) queued", lines[0])
        self.assertIn("REJECTED id=100003", lines[1])
        self.assertEqual([h["key"] for h in S.V9State(r.state_path).pending_heals], ["k3a9zq"])
        self.assertEqual([e["a"] for e in r.daemon.heal_events], ["requested", "refused"])

    def test_wap_fires_once(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "wap", "v": 1}, {"id": 1_000_001, "c": "wap", "v": 1},
               {"id": 1_000_002, "c": "wap", "v": 7})
        r.daemon.wap_action_fn.assert_called_once_with(1)
        self.assertEqual(r.acks()[-1]["e"], "val")

    def test_later_stage_verbs(self):
        r = Rig(self)
        r.send({"id": 1_000_002, "c": "cfm", "ref": 5}, {"id": 1_000_003, "c": "hld", "v": 30})
        self.assertEqual([a["e"] for a in r.acks()], ["ref", "cmd"])

    def test_unackable_and_out_of_range(self):
        r = Rig(self)
        r.send(b"not json", {"id": 0, "c": "ping"}, {"id": 150_000_000 * 2, "c": "ping"})
        self.assertEqual(r.acks(), [])
        lines = r.lines()
        self.assertIn("DROPPED", lines[0])
        self.assertIn("e=id", lines[1])


class Get(unittest.TestCase):
    def test_get_group_short_and_cf(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        r.acks()
        r.lines()
        r.send({"id": 1_000_002, "c": "get", "k": ["mode", "m"]})
        out = r.daemon._acks
        self.assertEqual(json.loads(out[0]), {"id": 1_000_002, "ok": 1, "h": r.hash()})
        cf = " ".join(out[1:])
        self.assertTrue(cf.startswith(f"<CF v=1 h={r.hash()}"))
        self.assertIn("mode.media=still", cf)
        self.assertIn("still.message_cap=150@c1000001", cf)
        lines = r.lines()
        self.assertIn("  still.message_cap = 150 (cmd 1000001)", lines)
        self.assertIn("  mode.media = still (yaml)", lines)

    def test_console_range_and_to_con_send_no_cf(self):
        r = Rig(self)
        r.send({"id": 7, "c": "get", "k": ["mode"]},
               {"id": 1_000_001, "c": "get", "k": ["mode"], "to": "con"})
        acks = r.daemon._acks
        self.assertEqual(len(acks), 1)                    # only the remote ack, no <CF>
        self.assertTrue(acks[0].startswith("{"))

    def test_big_and_unknown(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "get", "k": ["camera", "still", "video", "uplink"]},
               {"id": 1_000_002, "c": "get", "k": ["nope"]},
               {"id": 1_000_003, "c": "get", "k": ["camera", "still", "video", "uplink"],
                "to": "con"})
        self.assertEqual([a.get("e") for a in r.acks()], ["big", "key", None])

    def test_journal_is_console_only(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        r.acks()
        r.lines()
        r.send({"id": 1_000_002, "c": "get", "k": ["journal"]})
        self.assertEqual(len(r.daemon._acks), 1)
        self.assertTrue(any("still.message_cap: none -> 150" in line   # journal: overlay old
                            for line in r.lines()))

    def test_change_summary_after_set_and_reset(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        acks = list(r.daemon._acks)
        self.assertEqual(len(acks), 2)
        self.assertTrue(acks[1].startswith("<CF v=1 h=") and "still.message_cap=150@c1000001" in acks[1])
        r.daemon._acks.clear()
        r.send({"id": 1_000_002, "c": "reset", "all": 1})
        self.assertIn("still.message_cap=195", r.daemon._acks[1])
        r.daemon._acks.clear()
        r.send({"id": 8, "c": "set", "kv": {"m": 100}})          # console: no cellular at all
        self.assertEqual(r.daemon._acks, [])

    def test_duplicate_get_resends_cf_once(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "get", "k": ["mode.run"]})
        r.daemon._acks.clear()
        r.clock.t += V.DUP_CELLULAR_QUIET_S
        r.send({"id": 1_000_001, "c": "get", "k": ["mode.run"]})
        self.assertEqual(len(r.daemon._acks), 2)
        self.assertIn('"d":1', r.daemon._acks[0])
        self.assertTrue(r.daemon._acks[1].startswith("<CF"))


class Dedupe(unittest.TestCase):
    def test_duplicate_returns_the_original_answer(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        first = r.acks()[0]
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 999}})    # same id, other body
        self.assertEqual(r.acks(), [])                            # < 10 min: console only
        self.assertIn("duplicate", r.lines()[-1])
        self.assertEqual(S.V9State(r.state_path).overlay["still.message_cap"], 150)
        r.clock.t += V.DUP_CELLULAR_QUIET_S
        r.send({"id": 1_000_001, "c": "ping"})
        self.assertEqual(r.acks(), [dict(first, d=1)])
        r.send({"id": 1_000_001, "c": "ping"})
        self.assertEqual(r.acks(), [])                            # at most once per 10 min
        r.new_process()
        r.send({"id": 1_000_001, "c": "ping"})
        self.assertEqual(r.acks()[0]["d"], 1)                     # once in a new process
        r.send({"id": 1_000_001, "c": "ping"})
        self.assertEqual(r.acks(), [])

    def test_a_rejection_is_the_original_answer_too(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"no.such": 1}})
        r.acks()
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        self.assertIn("REJECTED", r.lines()[-1])
        self.assertEqual(S.V9State(r.state_path).overlay, {})

    def test_high_water(self):
        r = Rig(self)
        r.send({"id": 1_000_010, "c": "ping"}, {"id": 1_000_005, "c": "ping"})
        self.assertEqual([a.get("e") for a in r.acks()], [None, "old"])
        r.send({"id": 1_000_020, "c": "set", "kv": {"no.such": 1}},     # rejected: no advance
               {"id": 1_000_015, "c": "ping"})
        self.assertEqual([a.get("e") for a in r.acks()], ["key", None])
        r.send({"id": 1_000_010, "c": "ping"})                          # cached beats old
        self.assertIn("duplicate", r.lines()[-1])
        r.send({"id": 5, "c": "ping"}, {"id": 4, "c": "ping"})           # console: no HW
        self.assertEqual(sum("OK id=" in line for line in r.lines()), 2)


class Durability(unittest.TestCase):
    def test_persist_failure_changes_nothing(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"m": 150}})
        r.acks()
        before = open(r.state_path).read()
        with mock.patch.object(atomic_io, "write_text", side_effect=OSError("SD full")):
            r.send({"id": 1_000_002, "c": "set", "kv": {"m": 100}})
        ack = r.acks()[0]
        self.assertEqual(ack.get("e"), "err")
        self.assertEqual(open(r.state_path).read(), before)
        self.assertEqual(r.state.overlay["still.message_cap"], 150)
        self.assertIsNone(r.state.cached(1_000_002))
        r.send({"id": 1_000_002, "c": "set", "kv": {"m": 100}})      # a re-send applies now
        self.assertEqual(r.acks()[0]["ok"], 1)


if __name__ == "__main__":
    unittest.main()

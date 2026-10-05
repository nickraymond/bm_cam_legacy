#!/usr/bin/env python3
# filename: test_s4_guards.py
# description: Sprint26 S4 b.5 — guarded keys: revert by boots / transmitting actions / uptime, stage + cfm, service signatures, notes, D15.
"""
Sprint26 S4 commit b.5 (DESIGN §6.3; PLAN_S4.md G10a/b/e/f/g; consensus R10,
R11, NEW-1).

Pins:
  - guarded_revert: applies at once with a record; counters start when the
    value is in effect (next boot for next-boot keys); revert after 3 boots or
    2 TRANSMITTING actions; mode.output (save_local) after 3 boots or 2 h of
    uptime, and transmitting actions do not count for it;
  - a revert restores the pre-guard overlay value (or the YAML), journals
    `revert`, and leaves a <CF reverted=..> note that always goes cellular;
  - cfm confirms (record dropped, value kept); cfm of nothing = e:ref;
  - guarded_stage (halt true, bus_always_on true): stored, not applied, ack
    s:1; cfm applies it; reset drops it;
  - service keys: only a service-range id with a valid signature, then
    guarded_revert; commands off still self-reverts (boot counting needs no
    daemon); a bad UART value reverts at the 4th process start, before the
    wrapper's crash-loop cap (6th start);
  - a failed persist of the boot count changes nothing (D15).

Run (repo root):
  python3 -m unittest tests.test_s4_guards -v
"""

import json
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import atomic_io  # noqa: E402
import command_guards as G  # noqa: E402
import command_state_v9 as S  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig, signed  # noqa: E402


def reload(r):
    return S.V9State(r.state_path, log=lambda *_: None)


def boots(r, n):
    out = []
    for _ in range(n):
        out += quiet(G.count_boot, reload(r))
    return out


class Revert(unittest.TestCase):
    def test_save_local_reverts_after_3_boots(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"mode.output": "save_local"}})
        ack = r.acks()[0]
        self.assertEqual(ack["ok"], 1)
        self.assertNotIn("s", ack)
        self.assertIn("guarded: cfm 1000001 within 3 boots or 2 h", r.lines()[0])
        st = reload(r)
        self.assertEqual(st.overlay["mode.output"], "save_local")
        self.assertFalse(st.guarded["mode.output"]["in_effect"])        # next-boot key
        self.assertEqual(boots(r, 3), [])
        self.assertEqual(boots(r, 1), [("mode.output", "boot3")])
        st = reload(r)
        self.assertNotIn("mode.output", st.overlay)
        self.assertEqual(st.guarded, {})
        self.assertEqual(st.extra["notes"],
                         [{"reverted": "mode.output", "lim": "boot3", "ref": 1_000_001}])
        self.assertEqual(r.journal()[-1]["src"], "revert")

    def test_save_local_2h_uptime_and_sends_do_not_count(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"o": "save_local"}})
        boots(r, 1)
        st = reload(r)
        self.assertEqual(quiet(G.count_action, st, True, 1800.0), [])     # sends: no count
        self.assertEqual(quiet(G.count_action, st, True, 1800.0), [])
        self.assertEqual(quiet(G.count_action, st, False, 3599.0), [])
        self.assertEqual(quiet(G.count_action, st, False, 1.0), [("mode.output", "2h")])

    def test_topic_reverts_after_two_transmitting_actions(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"commands.topic": "bmcam/cmd2"}})
        st = reload(r)
        self.assertEqual(quiet(G.count_action, st, True), [])     # not in effect yet
        boots(r, 1)
        st = reload(r)
        self.assertEqual(quiet(G.count_action, st, False, 60.0), [])   # not transmitting
        self.assertEqual(quiet(G.count_action, st, True), [])
        self.assertEqual(quiet(G.count_action, st, True), [("commands.topic", "tx2")])
        self.assertNotIn("commands.topic", reload(r).overlay)

    def test_revert_restores_the_previous_overlay_value(self):
        r = Rig(self)
        r.send(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}))
        r.send(signed({"id": 100_000_002, "c": "set", "kv": {"uplink.chunk_chars": 256}}))
        rec = reload(r).guarded["uplink.chunk_chars"]
        self.assertEqual((rec["had"], rec["ref"]), (False, 100_000_002))   # original kept
        st = reload(r)
        quiet(G.count_action, st, True)
        quiet(G.count_action, st, True)
        self.assertNotIn("uplink.chunk_chars", reload(r).overlay)

    def test_commands_off_self_reverts_without_a_daemon(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"commands.enabled": False}})
        self.assertIs(reload(r).overlay["commands.enabled"], False)
        self.assertEqual(boots(r, 4), [("commands.enabled", "boot3")])
        self.assertNotIn("commands.enabled", reload(r).overlay)

    def test_bad_uart_reverts_before_the_crash_loop_cap(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"uplink.uart.port": "/dev/ttyWRONG"}})
        # every wrapper restart (exit 70) is a process start = a counted boot;
        # the wrapper's crash-loop fallback is the 6th start (5 restarts)
        for start in range(1, 7):
            if quiet(G.count_boot, reload(r)):
                break
        self.assertEqual(start, 4)
        self.assertLess(start, 6)


class Confirm(unittest.TestCase):
    def test_cfm_keeps_the_value(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"commands.topic": "bmcam/cmd2"}},
               {"id": 1_000_002, "c": "cfm", "ref": 1_000_001},
               {"id": 1_000_003, "c": "cfm", "ref": 1_000_001})
        acks = r.acks()
        self.assertEqual([a["ok"] for a in acks], [1, 1, 0])
        self.assertEqual(acks[2]["e"], "ref")
        st = reload(r)
        self.assertEqual((st.guarded, st.overlay["commands.topic"]), ({}, "bmcam/cmd2"))
        self.assertEqual(boots(r, 5), [])

    def test_stage_then_cfm(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": True,
                                                      "power.bus_always_on": True}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack.get("s")), (1, 1))
        self.assertIn("STAGED", r.lines()[0])
        st = reload(r)
        self.assertNotIn("power.halt.enabled", st.overlay)
        self.assertEqual(sorted(st.guarded), ["power.bus_always_on", "power.halt.enabled"])
        self.assertEqual(boots(r, 5), [])                         # staged never reverts
        r.lines()
        r.send({"id": 1_000_002, "c": "cfm", "ref": 1_000_001})
        self.assertEqual(r.acks()[0]["ok"], 1)
        # S5 F3: say when it governs (a hld in the same tail was still clamped)
        self.assertIn("power.bus_always_on applied (next action), "
                      "power.halt.enabled applied (next action)", r.lines()[0])
        st = reload(r)
        self.assertIs(st.overlay["power.halt.enabled"], True)
        self.assertIs(st.overlay["power.bus_always_on"], True)
        self.assertEqual(st.guarded, {})

    def test_reset_drops_a_staged_value(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": True}},
               {"id": 1_000_002, "c": "reset", "k": ["power.halt.enabled"]},
               {"id": 1_000_003, "c": "cfm", "ref": 1_000_001})
        self.assertEqual([a.get("e") for a in r.acks()], [None, None, "ref"])
        self.assertEqual(reload(r).guarded, {})

    def test_duplicate_set_and_duplicate_cfm(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": True}},
               {"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": True}})
        lines = r.lines()
        self.assertEqual(len(lines), 1)              # #126: the same-instant copy is log only
        self.assertIn("STAGED", lines[0])
        self.assertEqual(len(reload(r).guarded), 1)  # staged once


class Service(unittest.TestCase):
    def test_only_signed_service_range(self):
        r = Rig(self)
        cases = [
            ({"id": 1_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}, "auth"),
            (signed({"id": 1_000_002, "c": "set", "kv": {"uplink.chunk_chars": 320}}), "auth"),
            (dict(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}),
                  sig="0" * 16), "auth"),
            (signed({"id": 100_000_002, "c": "set", "kv": {"uplink.network_type": 1}}), None),
        ]
        for data, code in cases:
            r.send(data)
            self.assertEqual(r.acks()[0].get("e"), code, data)
        st = reload(r)
        self.assertEqual(st.overlay, {"uplink.network_type": 1})
        self.assertEqual(st.guarded["uplink.network_type"]["cls"], "revert")
        self.assertTrue(st.guarded["uplink.network_type"]["in_effect"])   # next-action key

    def test_no_key_file_refuses(self):
        r = Rig(self, service_key=None)
        r.send(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}))
        self.assertEqual(r.acks()[0]["e"], "auth")


class Notes(unittest.TestCase):
    def test_reverted_note_goes_cellular_and_clears(self):
        r = Rig(self)
        r.send({"id": 7, "c": "set", "kv": {"commands.topic": "bmcam/cmd2"}})   # console id
        boots(r, 4)
        r.new_process()
        r.daemon._acks.clear()
        self.assertEqual(r.daemon.v9_dispatch.flush_notes(), 1)
        cf = r.daemon._acks
        self.assertEqual(len(cf), 1)
        self.assertTrue(cf[0].startswith("<CF v=1 h="))
        self.assertIn("reverted=commands.topic lim=boot3 ref=7", cf[0])
        self.assertIn("REVERTED commands.topic", r.daemon._console[-1])
        self.assertEqual(reload(r).extra.get("notes"), None)
        self.assertEqual(r.daemon.v9_dispatch.flush_notes(), 0)


class Durability(unittest.TestCase):
    def test_failed_boot_count_changes_nothing(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"commands.topic": "bmcam/cmd2"}})
        st = reload(r)
        before = open(r.state_path).read()
        with mock.patch.object(atomic_io, "write_text", side_effect=OSError("SD")):
            with self.assertRaises(OSError):
                G.count_boot(st)
        self.assertEqual(st.boot_counter, 0)
        self.assertFalse(st.guarded["commands.topic"]["in_effect"])
        self.assertEqual(open(r.state_path).read(), before)


if __name__ == "__main__":
    unittest.main()

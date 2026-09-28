#!/usr/bin/env python3
# filename: test_s4b_review_fixes.py
# description: Sprint26 S4b independent review (command layer) — one regression test per finding.
"""
Sprint26 S4b review fixes (PLAN_S4.md §S4b review record).

  #1  a guarded comms key on a trigger-only stay_on unit reverts after 2 h of
      uptime; the stay_on loop notes idle time at most once a minute
  #2  the final ack flush waits for the lane guard only within the halt margin
  #3  an unsigned service-range id is refused e:auth and never moves the
      service high-water
  #4  the rsd-only drain takes heal-range rsd only (a remote-range rsd waits,
      so an older stashed set is not refused e:old)
  #5  a MISSING state file after remote activity re-seeds the high-water from
      the journal (each ok remote answer leaves an `hw` line)
  #6  an unguarded set drops a stale staged record (a later cfm of it is e:ref)
  #7  set resolves `m` with a `med` in the same command
  #8  a signed reset of a service key needs a service-range id
  #9  a duplicate hld from an earlier boot answers v:0, not active
  #10 heal events are rolled back when the rsd persist fails
  NIT the cellular d:1 copy goes once per process

Run (repo root):
  python3 -m unittest tests.test_s4b_review_fixes -v
"""

import json
import os
import sys
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import atomic_io  # noqa: E402
import command_daemon  # noqa: E402
import command_guards as G  # noqa: E402
import command_inbox as I  # noqa: E402
import command_state_v9 as S  # noqa: E402
import command_v9 as V  # noqa: E402
import rc_command_hooks as hooks  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig, signed  # noqa: E402
from tests.test_s4_lane import LANE, T0, Mono  # noqa: E402
from tests.test_s4_replies import FakeBm  # noqa: E402


def reload(r):
    return S.V9State(r.state_path, log=lambda *_: None)


class F1Backstop(unittest.TestCase):
    def test_topic_reverts_after_2h_with_no_sends(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"commands.topic": "bmcam/cmdX"}})
        quiet(G.count_boot, reload(r))                        # in effect after the restart
        st = reload(r)
        self.assertEqual(quiet(G.count_action, st, False, 3600.0), [])
        self.assertEqual(quiet(G.count_action, st, False, 3600.0), [("commands.topic", "2h")])

    def test_idle_loop_notes_guards_once_a_minute(self):
        sup.STOP.update(requested=False, signal=None)
        clock = [0.0]
        notes = []

        class D:
            v9_dispatch = None

            def process_pending(self):
                if clock[0] >= 200:
                    sup.STOP["requested"] = True
                return []

            def drain_acks(self, clock=None):
                return 0

            def drain_console(self, sleep_fn=None):
                return 0

            def reader_health(self):
                return True, ""

        boot = type("B", (), {"command_state": None, "save_local": False,
                              "note_guards": lambda self, s: notes.append(clock[0])})()
        quiet(sup._loop, boot, D(), lambda b, s: {}, lambda: {}, 0, 0, lambda s: None,
              lambda: clock[0], lambda s: clock.__setitem__(0, clock[0] + s))
        sup.STOP.update(requested=False, signal=None)
        self.assertEqual(len(notes), 3)                       # 60, 120, 180


class F2FlushRoom(unittest.TestCase):
    def flush(self, room):
        d = command_daemon.CommandDaemon(FakeBm(), None)
        mono = Mono()
        d.lane_cfg, d._last_utc, d._last_utc_mono = LANE, T0, mono.t   # phase 0: 30 s guard
        d.lane_room_fn = lambda: room
        d._acks.append('{"id":1000001,"ok":1}')
        clock = [0.0]

        def sleep(s):
            clock[0] += s
            mono.t += s
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            quiet(hooks.flush_acks, d, {"command_events": []}, clock=lambda: clock[0],
                  sleep_fn=sleep, budget_s=15.0)
        return d, clock[0]

    def test_no_room_leaves_the_acks(self):
        d, t = self.flush(room=5.0)
        self.assertEqual(d.bm.tx, [])
        self.assertLess(t, 16.0)

    def test_room_or_unlimited_waits(self):
        for room in (60.0, None):
            d, _t = self.flush(room=room)
            self.assertEqual(len(d.bm.tx), 1, room)


class F3UnsignedService(unittest.TestCase):
    def test_refused_and_no_high_water(self):
        r = Rig(self)
        r.send({"id": 199_999_999, "c": "ping"})
        self.assertEqual(r.acks()[0]["e"], "auth")
        self.assertNotIn("service", reload(r).high_water)
        r.send(signed({"id": 100_000_001, "c": "ping"}))
        self.assertEqual(r.acks()[0]["ok"], 1)
        self.assertEqual(reload(r).high_water["service"], 100_000_001)


class F4RsdOrder(unittest.TestCase):
    def test_remote_rsd_waits_for_the_decision_point(self):
        r = Rig(self)
        r.daemon.v9_inbox = I.Inbox(I.path_beside(r.state_path), log=lambda *_: None)
        for c in ({"id": 1_000_005, "c": "set", "kv": {"m": 150}},
                  {"id": 1_000_010, "c": "rsd", "h": [["k3a9zq", "1"]]},
                  {"id": 100_002, "c": "rsd", "h": [["k4a9zq", "1"]]}):
            r.daemon._inbound.put(json.dumps(c).encode())
        r.daemon.stash_pending()
        self.assertEqual([e["id"] for e in quiet(r.daemon.drain_rsd)], [100_002])
        events = quiet(r.daemon.process_pending)
        self.assertEqual([e["action"] for e in events], ["applied", "applied"])
        self.assertEqual(reload(r).overlay["still.message_cap"], 150)


class F5MissingFile(unittest.TestCase):
    def test_missing_after_activity_is_lost(self):
        r = Rig(self)
        r.send({"id": 1_000_050, "c": "ping"})                   # no setting: an hw line only
        self.assertIn("high_water.remote", [e["key"] for e in r.journal()])
        os.remove(r.state_path)
        st = reload(r)
        self.assertTrue(st.is_old("remote", 1_000_050))
        self.assertTrue(st.is_old("service", 100_000_001))


class F6StaleStage(unittest.TestCase):
    def test_unguarded_set_drops_the_stage(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"power.halt.enabled": True}},
               {"id": 1_000_002, "c": "set", "kv": {"power.halt.enabled": False}},
               {"id": 1_000_003, "c": "cfm", "ref": 1_000_001})
        self.assertEqual([a.get("e") for a in r.acks()], [None, None, "ref"])
        self.assertIs(reload(r).overlay["power.halt.enabled"], False)


class F7MedThenM(unittest.TestCase):
    def test_m_follows_med(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"med": "video", "m": 90}})
        ov = reload(r).overlay
        self.assertEqual((ov["mode.media"], ov["video.send.message_cap"]), ("video", 90))
        self.assertNotIn("still.message_cap", ov)


class F8ServiceReset(unittest.TestCase):
    def test_signed_reset_needs_the_service_range(self):
        r = Rig(self)
        r.send(signed({"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320}}))
        r.send(signed({"id": 5, "c": "reset", "k": ["uplink.chunk_chars"]}))
        self.assertIn("e=auth", r.lines()[-1])
        r.send(signed({"id": 100_000_002, "c": "reset", "k": ["uplink.chunk_chars"]}))
        self.assertNotIn("uplink.chunk_chars", reload(r).overlay)


class F9HoldDuplicate(unittest.TestCase):
    def test_a_later_boot_does_not_claim_the_hold(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "hld", "v": 30})
        self.assertEqual(r.acks()[0]["v"], 30)
        quiet(G.count_boot, reload(r))
        r.new_process()
        r.send({"id": 1_000_001, "c": "hld", "v": 30})
        ack = r.acks()[0]
        self.assertEqual((ack["d"], ack["v"]), (1, 0))
        self.assertIn("NOT active", r.lines()[-1])


class F10HealRollback(unittest.TestCase):
    def test_no_hl_for_an_unsaved_rsd(self):
        r = Rig(self)
        with mock.patch.object(atomic_io, "write_text", side_effect=OSError("SD")):
            r.send({"id": 100_002, "c": "rsd", "h": [["k3a9zq", "1"]]})
        self.assertEqual(r.daemon.heal_events, [])
        self.assertIn("e=err", r.lines()[-1])


class DupOncePerProcess(unittest.TestCase):
    def test_once(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "ping"})
        r.acks()
        for _ in range(3):
            r.clock.t += V.DUP_CELLULAR_QUIET_S
            r.send({"id": 1_000_001, "c": "ping"})
        self.assertEqual(len(r.acks()), 1)


class R2Runtime(unittest.TestCase):
    """Runtime-integration review (R2-1, -2, -3, -8, -9)."""

    def boot(self, r, media="still"):
        b = sup.Boot({"x": 1}, media=media, bm_commands_cfg={}, command_state=r.state,
                     transmit=True, bench_commands=False)
        b.output = "transmit"
        return b

    def test_r2_1_hl_wait_never_into_the_halt_margin(self):
        import rc_heal
        from rc_time_budget import CycleBudget
        d = command_daemon.CommandDaemon(FakeBm(), None)
        mono = Mono()
        d.lane_cfg, d._last_utc, d._last_utc_mono = LANE, T0, mono.t
        mono.t += 20.0                                      # 10 s of guard left
        d.state = type("St", (), {"pending_heals": []})()
        d.heal_events = []
        heals = quiet(rc_heal.WakeHeals, d, "/nonexistent", {})
        heals.outcomes = {"k3a9zq": {"a": "sent", "n": 1, "r": "ok", "id": 100_002}}
        slept = []
        with mock.patch.object(command_daemon.time, "monotonic", mono):
            quiet(heals.send_status_after_end, lambda b: None,
                  CycleBudget(30.0, 1.0, clock=lambda: 0.0), delay_seconds=1.0,
                  sleep_fn=slept.append)
        self.assertEqual(slept, [1.0])                     # 30 - 20 < 10 + 1: no wait

    def test_r2_2_unavailable_alt_cancels_the_trigger(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"med": "video"}})
        b = self.boot(r)

        def broken(boot, settings=None):
            raise sup.AltActionUnavailable("video block does not resolve")
        b.alt_actions = {"video": broken}
        ran = []
        quiet(b.pick_action(lambda boot: ran.append("default") or {}), b)
        self.assertEqual(ran, ["default"])
        self.assertIsNone(S.V9State(r.state_path).pending_trigger)
        self.assertEqual(b.media, "still")

    def test_r2_3_video_logger_is_deploy_only(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "set", "kv": {"med": "video_logger"}})
        ack = r.acks()[0]
        self.assertEqual((ack.get("e"), ack.get("k")), ("lock", "mode.media"))
        self.assertEqual(r.daemon.v9_dispatch.restart_requested, [])

    def test_r2_8_w10_sizes_a_longer_one_shot_clip(self):
        from rc_time_budget import CycleBudget
        r = Rig(self, base_over={"mode.media": "video"})
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"d": 30}})
        b = self.boot(r, media="video")
        b.min_action_s, b.video_duration_s = 67.0, 5.0
        b.budget = CycleBudget(20.0 + 67.0 + 10.0, 1.0, clock=lambda: 0.0)
        self.assertFalse(b.w10_trigger_fits())               # needs 67 + 25 s
        b.budget = CycleBudget(20.0 + 67.0 + 30.0, 1.0, clock=lambda: 0.0)
        self.assertTrue(b.w10_trigger_fits())

    def test_r2_9_action_log_shows_the_one_shot_output(self):
        r = Rig(self)
        b = self.boot(r)
        b.one_shot_output = "save_local"
        self.assertEqual(sup.action_record(b)["output"], "save_local")


if __name__ == "__main__":
    unittest.main()

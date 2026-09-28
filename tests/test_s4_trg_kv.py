#!/usr/bin/env python3
# filename: test_s4_trg_kv.py
# description: Sprint26 S4 b.6a — trg kv: validation (ONE_SHOT, action media, whole config), persisted only in the trigger, re-validated at action time, one action only.
"""
Sprint26 S4 commit b.6a (DESIGN §9; PLAN_S4.md G5, G6, G7).

Pins:
  - trg kv resolves short names with the ACTION's media (`m` = the cap of that
    media), refuses keys outside R.ONE_SHOT, `r` on a video action, bad types,
    and a per-action copy that fails the whole-config rules (e.g. a crop
    outside the frame);
  - the kv lives only in pending_trigger_v9: the overlay and the config hash
    never change;
  - Boot._apply_one_shot re-validates at action time: a kv that no longer
    validates is dropped loudly and the trigger runs without it; `o` and the
    save quality are one-action overrides, cleared at the next boot_drain.

Run (repo root):
  python3 -m unittest tests.test_s4_trg_kv -v
"""

import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_state_v9 as S  # noqa: E402
import command_v9 as V  # noqa: E402
import rc_supervisor as sup  # noqa: E402
from tests.test_config_v2 import quiet  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402


class Validate(unittest.TestCase):
    def test_accepted_and_persisted_in_the_trigger_only(self):
        r = Rig(self)
        h0 = r.hash()
        r.send({"id": 1_000_001, "c": "trg", "v": 2,
                "kv": {"r": [768, 432, 3072, 1728], "m": 100, "e": -1.0, "o": "save_local"}})
        ack = r.acks()[0]
        self.assertEqual((ack["ok"], ack["h"]), (1, h0))
        st = S.V9State(r.state_path)
        self.assertEqual(st.pending_trigger["kv"],
                         {"still.crop": [768, 432, 3072, 1728], "still.message_cap": 100,
                          "camera.exposure.ev": -1.0, "mode.output": "save_local"})
        self.assertEqual(st.overlay, {})
        self.assertEqual(st.guarded, {})
        self.assertIn("one action only", r.lines()[0])

    def test_m_is_the_video_cap_on_a_video_unit_and_d_is_flagged(self):
        r = Rig(self, base_over={"mode.media": "video"})
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"d": 8, "m": 90}})
        self.assertEqual(S.V9State(r.state_path).pending_trigger["kv"],
                         {"video.send.duration_s": 8, "video.send.message_cap": 90})
        self.assertIn("d=8!", r.lines()[0])

    def test_rejections(self):
        cases = [
            ({"mode.run": "stay_on"}, "key"),
            ({"power.halt.enabled": False}, "key"),
            ({"uplink.chunk_chars": 320}, "key"),
            ({"still.quality_ladder": [90, 80]}, "key"),
            ({"m": True}, "val"),
            ({"still.crop": [4000, 2000, 1000, 900]}, "xk"),
        ]
        r = Rig(self)
        cid = 1_000_100
        for kv, code in cases:
            cid += 1
            r.send({"id": cid, "c": "trg", "v": 2, "kv": kv})
            self.assertEqual(r.acks()[0].get("e"), code, kv)
        v = Rig(self, base_over={"mode.media": "video"})
        v.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"r": [0, 0, 100, 100]}})
        self.assertEqual(v.acks()[0].get("e"), "key")
        self.assertIsNone(S.V9State(r.state_path).pending_trigger)

    def test_video_cap_floor_applies_to_a_one_shot(self):
        r = Rig(self, base_over={"mode.media": "video"})
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"m": 40}})
        ack = r.acks()[0]
        self.assertEqual((ack.get("e"), ack.get("k")), ("xk", "video.send.message_cap"))

    def test_persisted_kv_load_check(self):
        self.assertIsNone(V.check_persisted_kv({"still.message_cap": 100}, 2))
        self.assertIsNotNone(V.check_persisted_kv({"mode.run": "stay_on"}, 2))
        self.assertIsNotNone(V.check_persisted_kv({"still.message_cap": "x"}, 2))


class ActionTime(unittest.TestCase):
    def boot(self, r, one_shot_fn):
        b = sup.Boot({"x": 1}, media="still", bm_commands_cfg={}, command_state=r.state,
                     transmit=True, bench_commands=False)
        b.one_shot_fn = one_shot_fn
        b.output = "transmit"
        return b

    def test_applied_once_then_cleared(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2,
                "kv": {"m": 100, "o": "save_local", "still.save.quality": 70}})
        seen = []
        b = self.boot(r, lambda kv: seen.append(kv) or {"fresh": True})
        b.save_quality = 85
        settings = {"trigger": {"id": 1_000_001, "value": 2,
                                "kv": S.V9State(r.state_path).pending_trigger["kv"]}}
        out = quiet(b._apply_one_shot, settings, r.daemon)
        self.assertEqual(seen, [{"still.message_cap": 100, "mode.output": "save_local",
                                 "still.save.quality": 70}])
        self.assertTrue(out["fresh"])
        self.assertEqual(out["trigger"]["id"], 1_000_001)
        self.assertTrue(b.save_local)
        self.assertEqual(b.save_quality, 70)
        quiet(r.state.consume_trigger)                    # the action consumed it
        b.owner = None
        quiet(b.boot_drain, {"x": 1}, {"command_events": []}, lambda s: None)
        self.assertFalse(b.save_local)                     # next action: back to normal
        self.assertEqual(b.save_quality, 85)

    def test_kv_that_no_longer_validates_is_dropped(self):
        r = Rig(self)
        called = []
        b = self.boot(r, lambda kv: called.append(kv) or {})
        settings = {"trigger": {"id": 7, "value": 2, "kv": {"still.crop": [4000, 2000, 1000, 900]}}}
        out = quiet(b._apply_one_shot, settings, r.daemon)
        self.assertIs(out, settings)
        self.assertEqual(called, [])
        self.assertIsNone(b.one_shot_output)


class MediaOverride(unittest.TestCase):
    """b.6b: pick_action + boot_drain leave a trg for the other media armed."""

    def boot(self, r, media="still"):
        b = sup.Boot({"x": 1}, media=media, bm_commands_cfg={}, command_state=r.state,
                     transmit=True, bench_commands=False)
        b.output = "transmit"
        return b

    def test_med_accepted_and_logger_refused(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"med": "video", "m": 90}},
               {"id": 1_000_002, "c": "trg", "v": 2, "kv": {"med": "video_logger"}})
        acks = r.acks()
        self.assertEqual((acks[0]["ok"], acks[1].get("e")), (1, "val"))
        self.assertEqual(S.V9State(r.state_path).pending_trigger["kv"],
                         {"mode.media": "video", "video.send.message_cap": 90})

    def test_pick_action_runs_the_other_media_once(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"med": "video"}})
        b = self.boot(r)
        b.min_action_s = 60.0
        seen = []
        b.alt_actions = {"video": lambda boot, s=None: seen.append((boot.media,
                                                                    boot.min_action_s)) or {}}
        b.alt_min_action_s = {"video": 67.0}
        default = lambda boot, s=None: seen.append("default")        # noqa: E731
        quiet(b.pick_action(default), b)
        self.assertEqual(seen, [("video", 67.0)])
        self.assertEqual((b.media, b.min_action_s), ("still", 60.0))  # restored
        quiet(r.state.consume_trigger)
        quiet(b.pick_action(default), b)
        self.assertEqual(seen[-1], "default")

    def test_boot_drain_leaves_an_other_media_trg_armed(self):
        r = Rig(self)
        r.send({"id": 1_000_001, "c": "trg", "v": 2, "kv": {"med": "video"}})
        b = self.boot(r)
        b.owner = None
        settings, flags = quiet(b.boot_drain, {"x": 1}, {"command_events": []}, lambda s: None)
        self.assertNotIn("trigger", settings)
        self.assertFalse(flags["skip_time_window"])
        self.assertEqual(S.V9State(r.state_path).pending_trigger["id"], 1_000_001)


if __name__ == "__main__":
    unittest.main()

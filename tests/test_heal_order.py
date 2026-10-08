#!/usr/bin/env python3
# filename: test_heal_order.py
# description: media_key.heal_order before|after — the rsd heal chunks go before START (today) or after the new media's END.
"""
`uplink.media_key.heal_order` (registry v12, Nick 2026-10-08, EPIC_transmission_reliability).

Measured on the bench 10/8 (REEF-RC 7 + 8 AM wakes): up to 40 heal chunks sent
BEFORE START fill the Spotter's 2-slot hand-off queue, the stall lands on START and
the NEW image loses START + its first 6-8 chunks. `after` puts START + the new
media's burst first and the heals in what is left.

Pins
  registry / loader / render   key exists (enum before|after, default before, group
                               uplink.media_key); the `media_key:` island reads
                               heal_order (absent = before, bad value = ValueError);
                               the v2 render writes the line ONLY for `after`
  wake rules (both orders)     before: today's sequence + reserve (unchanged);
                               after: chunks paced with the sleep BEFORE each, the
                               budget gives way keeping one <HL> slot per key, cap 40,
                               the pending list ages exactly as before; summary["heal"]
                               carries order only when it is not the default
  stills cycle (after)         START .. END, (ack flush), heal chunks, <HL>; the
                               `before` cycle is pinned by tests/test_s5_heal.py
  video cycle (after)          END, mid-burst ack, heal chunks, <HL>, close, halt
  zero heals                   nothing pending -> no extra line either way

Run (repo root):  python3 -m pytest tests/test_heal_order.py -q
"""

import contextlib
import io
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import rc_heal  # noqa: E402
import rc_media_key as mk  # noqa: E402

from command_state import CommandState  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from tests.test_command_integration import IntegrationHarness, make_cmd_frame  # noqa: E402
from tests.test_s3_video_tx_daemon import VideoTxHarness, settings as vtx_settings  # noqa: E402
from tests.test_s5_heal import (  # noqa: E402
    OLD_KEY, T_WAKE, WAKE_KEY, FakeDaemon, golden_chunk_line, write_old_record)

KEY = "uplink.media_key.heal_order"


# --- registry / loader / render ----------------------------------------------------------

class TestRegistryAndIsland(unittest.TestCase):
    def test_registry_key(self):
        key = next(k for k in R.KEYS if k.path == KEY)
        self.assertEqual((key.type, key.default, key.enum), (R.ENUM, "before", ("before", "after")))
        self.assertEqual(key.group, "uplink.media_key")
        self.assertEqual(key.v1_sources, ("media_key.heal_order",))
        self.assertEqual(key.guard, R.NONE, "a console `set` must be able to flip it for the A/B")
        self.assertGreaterEqual(R.REGISTRY_VERSION, 12)
        self.assertEqual(R.defaults()[KEY], "before")

    def _island(self, body):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        f.write("capture_mode: \"progressive_jpeg\"\nmedia_key:\n  enabled: true\n" + body)
        f.close()
        return f.name

    def test_island_absent_is_before(self):
        self.assertEqual(mk.load_media_key_config(self._island(""))["heal_order"], "before")
        self.assertEqual(mk.DEFAULT_CONFIG["heal_order"], "before")

    def test_island_reads_after_bare_or_quoted(self):
        self.assertEqual(mk.load_media_key_config(self._island("  heal_order: after\n"))["heal_order"],
                         "after")
        self.assertEqual(mk.load_media_key_config(self._island('  heal_order: "after"   # x\n'))["heal_order"],
                         "after")

    def test_island_rejects_unknown_value(self):
        with self.assertRaises(ValueError):
            mk.load_media_key_config(self._island("  heal_order: during\n"))

    def test_render_writes_the_line_only_for_after(self):
        v = R.defaults()
        base = config_v2.render_v1_text(v)
        self.assertNotIn("heal_order", base, "a default unit's render is byte-identical to v11")
        v[KEY] = "after"
        text = config_v2.render_v1_text(v)
        self.assertIn("media_key:\n  enabled: false\n  retain_days: 14.0\n  heal_order: \"after\"\n", text)
        # Round trip through the unit's own island parser.
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        f.write(text)
        f.close()
        self.assertEqual(mk.load_media_key_config(f.name)["heal_order"], "after")

    def test_heal_order_for_settings(self):
        self.assertEqual(rc_heal.heal_order_for({}), "before")
        self.assertEqual(rc_heal.heal_order_for({"media_key_cfg": {}}), "before")
        self.assertEqual(rc_heal.heal_order_for({"media_key_cfg": {"heal_order": "after"}}), "after")
        self.assertEqual(rc_heal.heal_order_for({"media_key_cfg": {"heal_order": "sideways"}}), "before")


# --- wake rules, both orders -------------------------------------------------------------

class TestWakeRulesByOrder(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.addCleanup(self.d.cleanup)
        self.sent_dir = os.path.join(self.d.name, "sent")
        write_old_record(self.sent_dir)
        self.state = CommandState(path=os.path.join(self.d.name, "state.json"))

    def wake(self, order, budget_s=500, reserve=0, **after_kw):
        """One wake: the trace is [("sleep", s) | ("tx", line)] in order."""
        clk, trace = [0.0], []

        def sleep(s):
            clk[0] += s
            trace.append(("sleep", s))

        budget = CycleBudget(budget_s, 1.0, clock=lambda: clk[0])
        summary = {}
        with contextlib.redirect_stdout(io.StringIO()):
            heals = rc_heal.WakeHeals(FakeDaemon(self.state), self.sent_dir, summary, order=order)
            if order == "before":
                sent = heals.send_before_start(lambda b: trace.append(("tx", b)), budget,
                                               reserve_msgs=reserve, delay_seconds=1.0, sleep_fn=sleep)
            else:
                sent = heals.send_after_end(lambda b: trace.append(("tx", b)), budget,
                                            delay_seconds=1.0, sleep_fn=sleep, **after_kw)
        return heals, trace, sent, summary

    def test_before_is_todays_sequence(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1, 2]]]})
        heals, trace, sent, summary = self.wake("before")
        self.assertEqual(sent, 2)
        self.assertEqual(trace, [("tx", golden_chunk_line(1)), ("sleep", 1.0),
                                 ("tx", golden_chunk_line(2)), ("sleep", 1.0)], "tx, pump, sleep")
        self.assertEqual(summary["heal"], {"planned": 2, "sent": 2}, "no order field on the default")
        self.assertEqual(self.state.pending_heals, [])

    def test_after_paces_with_the_sleep_before_each_chunk(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1, 2]]]})
        heals, trace, sent, summary = self.wake("after")
        self.assertEqual(sent, 2)
        self.assertEqual(trace, [("sleep", 1.0), ("tx", golden_chunk_line(1)),
                                 ("sleep", 1.0), ("tx", golden_chunk_line(2))],
                         "END is not followed by a sleep: the pacing sleep leads each chunk")
        self.assertEqual(summary["heal"], {"planned": 2, "sent": 2, "order": "after"})
        self.assertEqual(heals.outcomes[OLD_KEY]["a"], "sent")
        self.assertEqual(self.state.pending_heals, [])

    def test_after_gives_way_keeping_one_hl_slot_per_key(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1, 2, 3, 4, 5]]]})
        heals, trace, sent, _ = self.wake("after", budget_s=5)
        # 5 s at 1 s/msg: each chunk needs itself + the one <HL> -> 4 go, the 5th waits.
        self.assertEqual(sent, 4)
        self.assertEqual(self.state.pending_heals[0]["n"], [5])
        self.assertEqual(self.state.pending_heals[0]["wakes_left"], 2)
        self.assertEqual(heals.outcomes[OLD_KEY], {"a": "sent", "n": 4, "r": "partial", "id": 1})

    def test_after_honours_an_explicit_reserve(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1, 2, 3]]]})
        _, _, sent, _ = self.wake("after", budget_s=4, reserve_msgs=3)
        self.assertEqual(sent, 1, "4 s: one chunk + the 3 reserved slots, then stop")

    def test_after_no_budget_sends_nothing_and_ages(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, [1]]]})
        _, trace, sent, _ = self.wake("after", budget_s=1)
        self.assertEqual((sent, trace), (0, []))
        self.assertEqual(self.state.pending_heals[0]["wakes_left"], 2)

    def test_after_cap_40_then_rest_next_wake(self):
        self.state.record(1, "rsd", {"h": [[OLD_KEY, list(range(0, 60))]]})
        heals, trace, sent, _ = self.wake("after")
        self.assertEqual(sent, 40)
        self.assertEqual([t for t in trace if t[0] == "tx"][0][1], golden_chunk_line(0))
        self.assertEqual(self.state.pending_heals[0]["n"], list(range(40, 60)))
        heals, _, sent, _ = self.wake("after")
        self.assertEqual(sent, 20)
        self.assertEqual(self.state.pending_heals, [])
        self.assertEqual(heals.outcomes[OLD_KEY]["a"], "sent")

    def test_unknown_order_falls_back_to_before(self):
        with contextlib.redirect_stdout(io.StringIO()):
            heals = rc_heal.WakeHeals(FakeDaemon(self.state), self.sent_dir, {}, order="sideways")
        self.assertEqual(heals.order, "before")

    def test_begin_wake_reads_the_island(self):
        settings = {"media_key_cfg": {"sent_dir": self.sent_dir, "heal_order": "after"}}
        with contextlib.redirect_stdout(io.StringIO()):
            heals = rc_heal.begin_wake(FakeDaemon(self.state), settings, {})
        self.assertEqual(heals.order, "after")


# --- stills cycle --------------------------------------------------------------------------

class TestStillsCycleAfter(IntegrationHarness):
    def _set_after(self):
        with open(self.config_path, "a", encoding="utf-8") as fh:
            fh.write("media_key:\n  enabled: false\n  heal_order: after\n")

    def img(self):
        return [m for k, m in self.wire if k == "img"]

    def test_image_then_heals_then_hl(self):
        self._set_after()
        sent_dir = os.path.join(self.tmpdir.name, "sent")
        write_old_record(sent_dir)
        self.state.record(100004, "rsd", {"h": [[OLD_KEY, [0, 1]]]})
        with mock.patch.object(rc_heal, "_sent_dir", lambda s: sent_dir):
            r = self.run_rc(transmit=True)
        self.assertIsNone(r.summary.get("error"))
        img = self.img()
        self.assertTrue(img[0].startswith("<START IMG>"), "the new image goes first")
        end = next(i for i, m in enumerate(img) if m.startswith("<END IMG>"))
        self.assertFalse([m for m in img[:end] if m.startswith(f"<I{OLD_KEY}.")],
                         "no heal chunk before END")
        self.assertEqual([m.encode("ascii") for m in img[end + 1:end + 3]],
                         [golden_chunk_line(0), golden_chunk_line(1)])
        self.assertEqual(img[end + 3], f"<HL v=1 key={OLD_KEY} a=sent n=2 r=ok id=100004>\n")
        self.assertEqual(len(img), end + 4)
        self.assertEqual(CommandState(path=self.state_path).pending_heals, [])
        self.assertEqual(r.summary["heal"], {"planned": 2, "sent": 2, "order": "after",
                                             "hl": [img[end + 3].strip()]})

    def test_nothing_pending_adds_nothing(self):
        self._set_after()
        r = self.run_rc(transmit=True)
        self.assertIsNone(r.summary.get("error"))
        img = self.img()
        self.assertTrue(img[0].startswith("<START IMG>"))
        self.assertTrue(img[-1].startswith("<END IMG>"))
        self.assertEqual(r.summary["heal"], {"hl": []}, "as today: no chunks, no <HL>")


# --- video cycle ---------------------------------------------------------------------------

class TestVideoCycleAfter(VideoTxHarness):
    def setUp(self):
        super().setUp()
        self.sent_dir = os.path.join(self.tmp.name, "sent")
        write_old_record(self.sent_dir)
        self.daemon.heal_validate_fn = rc_heal.make_heal_validate_fn(self.sent_dir)
        self.mk_cfg = {"enabled": True, "retain_days": 3.0, "source": "test", "sent_dir": self.sent_dir,
                       "state_path": os.path.join(self.tmp.name, "last_key"), "heal_order": "after"}

    def run_after(self, **kw):
        import tests.test_s3_video_tx_daemon as s3

        def keyed_settings(tmp):
            s = vtx_settings(tmp)
            s["media_key_cfg"] = self.mk_cfg
            return s

        with mock.patch.object(s3, "settings", keyed_settings), \
             mock.patch.object(mk, "spotter_utc_for_wake",
                               lambda gate_info=None, daemon=None, timeout_s=20.0: (T_WAKE, "spotter")):
            return self.run_vtx(**kw)

    def test_clip_then_heals_then_hl(self):
        self.state.record(100003, "rsd", {"h": [[OLD_KEY, [63, 64]]]})
        summary, out = self.run_after()
        self.assertIsNone(summary["error"], summary["error"])
        start, end = self.idx("img", "<START IMG>")[0], self.idx("img", "<END IMG>")[0]
        heal = self.idx("img", f"<I{OLD_KEY}.")
        self.assertEqual(len(heal), 2)
        self.assertEqual(self.wire[start][1][:6], "<START", "the clip goes first")
        self.assertTrue(all(end < i for i in heal), "heals go AFTER the clip's END")
        self.assertEqual(self.wire[heal[0]][1].encode("ascii"), golden_chunk_line(63))
        hl = self.idx("img", "<HL ")
        self.assertEqual(len(hl), 1)
        self.assertGreater(hl[0], heal[-1], "<HL> follows the heal chunks")
        self.assertEqual(self.wire[hl[0]][1],
                         f"<HL v=1 key={OLD_KEY} a=sent n=2 r=ok id=100003 w={WAKE_KEY}>\n")
        close, halt = self.idx("close")[0], self.idx("halt")[0]
        self.assertTrue(hl[0] < close < halt)
        self.assertIn("[HEAL] sent 2 heal chunk(s) after END", out)
        self.assertEqual(CommandState(path=self.state_path).pending_heals, [])

    def test_mid_burst_ack_keeps_its_place_before_the_heals(self):
        self.state.record(100003, "rsd", {"h": [[OLD_KEY, [63]]]})
        frame = make_cmd_frame({"id": 100020, "c": "rsd", "h": [[OLD_KEY, "5"]]})
        summary, _ = self.run_after(inject_at_sleep_call=20, inject_frame=frame)
        self.assertIsNone(summary["error"], summary["error"])
        end = self.idx("img", "<END IMG>")[0]
        acks = self.idx("ack", '"id":100020')
        heal = self.idx("img", f"<I{OLD_KEY}.63>")
        self.assertTrue(acks and heal)
        self.assertTrue(end < acks[0] < heal[0], "END, deferred ack flush, then the heal chunks")
        hl = self.idx("img", "<HL ")
        self.assertEqual(len(hl), 1)
        # The mid-burst rsd for the SAME key replaced the planned heal before the heal
        # slot ran (v8 pump: persisted at once). The chunk still went out under the
        # planned command, so its <HL> says so (sent beats requested for one key).
        self.assertIn(f"a=sent n=1 r=ok id=100003 w={WAKE_KEY}", self.wire[hl[0]][1])
        pending = CommandState(path=self.state_path).pending_heals
        self.assertEqual(pending, [{"key": OLD_KEY, "n": [5], "id": 100020, "wakes_left": 3}],
                         "the rsd heard mid-burst waits for the next wake, whole")

    def test_nothing_pending_adds_nothing(self):
        summary, _ = self.run_after()
        self.assertIsNone(summary["error"])
        self.assertEqual(self.idx("img", "<HL "), [])
        imgs = self.idx("img")
        self.assertTrue(self.wire[imgs[0]][1].startswith("<START IMG>"))
        self.assertTrue(self.wire[imgs[-1]][1].startswith("<END IMG>"))


if __name__ == "__main__":
    unittest.main()

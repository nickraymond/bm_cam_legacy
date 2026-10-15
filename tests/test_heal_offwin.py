#!/usr/bin/env python3
# filename: test_heal_offwin.py
# description: d35 — capped rsd heals on stills wakes OUTSIDE the capture window (media_key.heal_offwin_max).
"""
`uplink.media_key.heal_offwin_max` (registry v14; board decision d35, Nick 2026-10-15,
NOAA release): the capture window restricts NEW images only. A per_boot stills wake
outside the window sends <WS a=skip_win>, then up to `heal_offwin_max` pending rsd heal
chunks (+ one <HL> per key) through the heal-only pass, then the listen tail. No
capture, no START. 0 = the registry v13 out-of-window wake.

Pins
  registry / loader / render   int 0..40, default 40, group uplink.media_key, guard none;
                               the island reads it (absent = 40; bad = ValueError); the v2
                               render writes the line ONLY when not 40; v1 reader; the
                               limit equals rc_heal.HEAL_CAP_PER_WAKE; offwin_cap_for
  catalog tier                 control (the one uplink.* exception); heal_order stays blocked
  off-window wake, cap 40      backlog 60 -> exactly 40 chunks, byte-identical to the
                               original send, then <HL a=sent r=partial>; the rest stays
                               pending (wakes_left 3 -> 2); order <WS>, gap, chunks, <HL>,
                               tail; no capture
  backlog 10 < cap             all 10, <HL r=ok>, nothing left pending
  empty backlog                no uplink beyond <WS> (no port open), no state write
  cap 0                        same wire / summary / state as a v13 wake (the step patched
                               out), with a backlog of 60 pending
  cap 20                       20 chunks
  stay_on / legacy             unchanged: no heal pass outside the window
  a failing heal pass          logged; the tail still runs; the wake does not fail
  the per-wake log line        cap, heal_chunks, hl, uplink_msgs, heal_s, uptime_s
  in-window                    unchanged: the in-window heal slot ignores the key (cap 0
                               still sends the pending heals); goldens pin the rest

The wire of a whole off-window wake on a migrated v9 unit is pinned by the goldens
vectors_v9/v9_offwin_heal and v9_offwin_heal_cap0.

Run (repo root):  python3 -m pytest tests/test_heal_offwin.py -q
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import rc_heal  # noqa: E402
import rc_media_key as mk  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402

from command_state import CommandState  # noqa: E402
from rc_time_budget import CycleBudget  # noqa: E402
from tests.test_command_integration import IntegrationHarness  # noqa: E402
from tests.test_s3a_supervisor import Base  # noqa: E402
from tests.test_s5_heal import OLD_KEY, golden_chunk_line, write_old_record  # noqa: E402

KEY = "uplink.media_key.heal_offwin_max"
DELAY = 1.0
STILL = {"budget_seconds": 600, "pacing_delay_seconds": DELAY, "pacing_chunk_b64_chars": 384,
         "config_path": "/nonexistent.yaml", "enforce_time_window": True,
         "timezone": "America/New_York", "window_start": "10:00", "window_end": "15:00",
         "crop_native_xywh": (1504, 846, 1600, 900), "output_width": 1000,
         "output_size": (1000, 562), "q_max": 90, "power_halt_enabled": False,
         "power_halt_dry_run": True, "power_halt_mode": "halt",
         "power_halt_script_path": "/nonexistent_halt.sh"}
OUT_OF_WINDOW = (False, {"source_time": "spotter", "local_time": "17:00",
                         "reason": "outside window 10:00-15:00 America/New_York"})


def _quiet(fn, *a, **k):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        out = fn(*a, **k)
    return out, buf.getvalue()


# --- registry / loader / render / catalog -------------------------------------------------

class TestRegistryAndIsland(unittest.TestCase):
    def test_registry_key(self):
        key = R.BY_PATH[KEY]
        self.assertEqual((key.type, key.default, key.range), (R.INT, 40, (0, 40)))
        self.assertEqual(key.group, "uplink.media_key")
        self.assertEqual(key.v1_sources, ("media_key.heal_offwin_max",))
        self.assertEqual(key.guard, R.NONE, "a console / backend `set` must reach it")
        self.assertEqual(key.apply, R.NEXT_ACTION)
        self.assertEqual(R.REGISTRY_VERSION, 14)
        self.assertEqual(R.defaults()[KEY], 40)
        self.assertIsNone(R.check_value(key, 0))
        self.assertIsNone(R.check_value(key, 40))
        self.assertIsNotNone(R.check_value(key, 41))
        self.assertIsNotNone(R.check_value(key, -1))

    def test_limits_agree(self):
        self.assertEqual(mk.HEAL_OFFWIN_MAX_LIMIT, rc_heal.HEAL_CAP_PER_WAKE,
                         "the off-window cap never exceeds the in-window heal size")
        self.assertEqual(R.BY_PATH[KEY].range, (0, mk.HEAL_OFFWIN_MAX_LIMIT))
        self.assertEqual(R.BY_PATH[KEY].default, mk.DEFAULT_HEAL_OFFWIN_MAX)
        self.assertEqual(mk.DEFAULT_CONFIG["heal_offwin_max"], mk.DEFAULT_HEAL_OFFWIN_MAX)

    def _island(self, body):
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        f.write("capture_mode: \"progressive_jpeg\"\nmedia_key:\n  enabled: true\n" + body)
        f.close()
        return f.name

    def test_island_absent_is_40(self):
        self.assertEqual(mk.load_media_key_config(self._island(""))["heal_offwin_max"], 40)

    def test_island_reads_bare_or_quoted(self):
        self.assertEqual(mk.load_media_key_config(self._island("  heal_offwin_max: 0\n"))
                         ["heal_offwin_max"], 0)
        self.assertEqual(mk.load_media_key_config(self._island('  heal_offwin_max: "20"  # x\n'))
                         ["heal_offwin_max"], 20)

    def test_island_rejects_bad_values(self):
        for bad in ("41", "-1", "2.5", "true", "lots"):
            with self.assertRaises(ValueError, msg=bad):
                mk.load_media_key_config(self._island(f"  heal_offwin_max: {bad}\n"))

    def test_settings_line_unchanged_on_the_default(self):
        cfg = mk.load_media_key_config(self._island(""))
        _, out = _quiet(mk.print_media_key_settings, cfg)
        self.assertNotIn("heal_offwin_max", out, "a default unit's [KEY] line is unchanged")
        _, out = _quiet(mk.print_media_key_settings, dict(cfg, heal_offwin_max=0))
        self.assertIn(" heal_offwin_max=0", out)

    def test_render_writes_the_line_only_when_not_40(self):
        v = R.defaults()
        self.assertNotIn("heal_offwin_max", config_v2.render_v1_text(v),
                         "a default unit's render is byte-identical to registry v13")
        v[KEY] = 0
        text = config_v2.render_v1_text(v)
        self.assertIn("media_key:\n  enabled: false\n  retain_days: 14.0\n  heal_offwin_max: 0\n",
                      text)
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False, encoding="utf-8")
        self.addCleanup(os.unlink, f.name)
        f.write(text)
        f.close()
        self.assertEqual(mk.load_media_key_config(f.name)["heal_offwin_max"], 0, "round trip")

    def test_render_with_heal_order_after(self):
        v = dict(R.defaults(), **{"uplink.media_key.heal_order": "after", KEY: 20})
        self.assertIn('  heal_order: "after"\n  heal_offwin_max: 20\n', config_v2.render_v1_text(v))

    def test_v1_reader(self):
        import config_v1_reader
        src = open(config_v1_reader.__file__, encoding="utf-8").read()
        self.assertIn('v["uplink.media_key.heal_offwin_max"] = int(mk.get("heal_offwin_max", 40))',
                      src)

    def test_offwin_cap_for(self):
        self.assertEqual(rc_heal.offwin_cap_for({}), 40)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {}}), 40)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {"heal_offwin_max": 0}}), 0)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {"heal_offwin_max": 99}}), 40)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {"heal_offwin_max": -3}}), 0)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {"heal_offwin_max": True}}), 40)
        self.assertEqual(rc_heal.offwin_cap_for({"media_key_cfg": {"heal_offwin_max": "7"}}), 40)


class TestCatalogTier(unittest.TestCase):
    def test_control_and_the_exception_is_narrow(self):
        import gen_config_catalog as G
        self.assertEqual(G._tier(R.BY_PATH[KEY]), ("control", None))
        self.assertEqual(G._tier(R.BY_PATH["uplink.media_key.heal_order"])[0], "blocked")
        self.assertEqual(G._tier(R.BY_PATH["uplink.media_key.retain_days"])[0], "blocked")
        self.assertEqual(set(G.CONTROL_EXCEPTIONS), {KEY})

    def test_committed_catalog_has_the_key(self):
        with open(os.path.join(REPO_ROOT, "docs", "bmcam_config_catalog.json"), encoding="utf-8") as fh:
            cat = json.load(fh)
        self.assertEqual(cat["registry_version"], 14)
        entry = next(k for k in cat["keys"] if k["path"] == KEY)
        self.assertEqual((entry["tier"], entry["default"], entry["range"]), ("control", 40, [0, 40]))


# --- one off-window stills wake ----------------------------------------------------------

class FakeDaemon:
    """What the heal pass touches: the persisted state and the per-command heal events."""

    def __init__(self, state):
        self.state = state
        self.heal_events = []
        self.pumps = 0

    def process_pending(self):          # the heal slot's pump (pump-only, no wire)
        self.pumps += 1
        return []


class OffWindowWake(Base):
    """still_action outside the window on a per_boot supervised boot: a real CommandState
    and sent record (the golden h264 clip, 126 chunks), a recording uplink."""

    def setUp(self):
        super().setUp()
        self.sent_dir = os.path.join(self.tmp.name, "sent")
        write_old_record(self.sent_dir)
        self.state_path = os.path.join(self.tmp.name, "state.json")
        self.state = CommandState(path=self.state_path)
        self.wire = []                       # ("ws", action) | ("tx", bytes) | ("sleep", s) | ("tail",)
        self.opened = 0

    def capture(self, *_a, **_k):
        raise AssertionError("no capture outside the window")

    def bm_open(self, _config_path):
        self.opened += 1
        return lambda line: self.wire.append(("tx", line))

    def sleep(self, s):
        self.clock.sleep(s)
        self.wire.append(("sleep", s))

    def backlog(self, n, command_id=100001):
        self.state.record(command_id, "rsd", {"h": [[OLD_KEY, list(range(n))]]})

    def run_wake(self, cap=None, run="per_boot", supervised=True, extra_patches=()):
        settings = dict(STILL, media_key_cfg={"sent_dir": self.sent_dir})
        if cap is not None:
            settings["media_key_cfg"]["heal_offwin_max"] = cap
        boot = None
        if supervised:
            boot = self.boot(settings=STILL)
            boot.run = run
        summary = {"command_events": []}
        budget = CycleBudget(STILL["budget_seconds"], DELAY, clock=self.clock)

        def tail(daemon, cfg, summ, bud, **kw):
            self.wire.append(("tail",))
            summ["listen_tail_s"] = 150.0
            return 150.0

        with contextlib.ExitStack() as stack:
            stack.enter_context(mock.patch.object(rc, "should_transmit_now_from_schedule",
                                                  lambda path, **kw: OUT_OF_WINDOW))
            stack.enter_context(mock.patch.object(rc.cmd_hooks, "post_transmit_listen", tail))
            for p in extra_patches:
                stack.enter_context(p)
            _, out = _quiet(rc.still_action, settings, summary, FakeDaemon(self.state), budget,
                            transmit=True, capture_only=False, native_path=None,
                            skip_time_window=False, output_dir=self.tmp.name,
                            capture_fn=self.capture, bm_open_fn=self.bm_open,
                            wake_fn=lambda **kw: self.wire.append(("ws", kw["action"])),
                            sleep_fn=self.sleep, clock=self.clock, bm_commands_cfg={},
                            bench_commands=False, grid_clock_fn=None, supervised=boot)
        return summary, out

    def tx(self):
        return [w[1] for w in self.wire if w[0] == "tx"]

    def chunks(self):
        return [t for t in self.tx() if t.startswith(f"<I{OLD_KEY}.".encode())]

    def hl(self):
        return [t for t in self.tx() if t.startswith(b"<HL ")]

    def persisted(self):
        return CommandState(path=self.state_path).pending_heals


class TestOffWindowCap40(OffWindowWake):
    def test_backlog_bigger_than_the_cap(self):
        self.backlog(60)
        summary, out = self.run_wake()
        self.assertIs(summary["schedule_allowed"], False)
        self.assertEqual(self.chunks(), [golden_chunk_line(n) for n in range(40)],
                         "40 chunks, newest heal first, byte-identical to the original send")
        self.assertEqual(self.hl(), [f"<HL v=1 key={OLD_KEY} a=sent n=40 r=partial id=100001>\n"
                                     .encode()])
        self.assertEqual(len(self.tx()), 41, "nothing else on the uplink: no START, no image")
        self.assertFalse([t for t in self.tx() if t.startswith(b"<START")])
        self.assertEqual(self.persisted(), [{"key": OLD_KEY, "n": list(range(40, 60)),
                                             "id": 100001, "wakes_left": 2}],
                         "the rest waits; an off-window heal wake ages it like any heal wake")
        self.assertEqual(summary["heal"]["planned"], 40)
        self.assertEqual(summary["heal"]["sent"], 40)
        self.assertEqual(self.opened, 1)
        self.assertIn("[HEAL] off-window wake: cap=40 heal_chunks=40 hl=1 uplink_msgs=42 ", out)

    def test_order_ws_gap_chunks_hl_tail(self):
        self.backlog(2)
        self.run_wake()
        kinds = [w[0] if w[0] != "sleep" else f"sleep{w[1]:g}" for w in self.wire]
        self.assertEqual(kinds, ["ws", "sleep1", "tx", "sleep1", "tx", "sleep1",
                                 "sleep1", "tx", "tail"],
                         "<WS>, one pacing gap, chunk+sleep x2, <HL> (sleep before), tail")
        self.assertEqual(self.tx()[-1], f"<HL v=1 key={OLD_KEY} a=sent n=2 r=ok id=100001>\n"
                         .encode())

    def test_backlog_smaller_than_the_cap(self):
        self.backlog(10)
        summary, out = self.run_wake()
        self.assertEqual(self.chunks(), [golden_chunk_line(n) for n in range(10)])
        self.assertEqual(self.hl(), [f"<HL v=1 key={OLD_KEY} a=sent n=10 r=ok id=100001>\n"
                                     .encode()])
        self.assertEqual(self.persisted(), [])
        self.assertIn("cap=40 heal_chunks=10 hl=1 uplink_msgs=12 ", out)

    def test_empty_backlog_opens_nothing(self):
        summary, out = self.run_wake()
        self.assertEqual(self.wire, [("ws", "skip_win"), ("tail",)], "today's wake exactly")
        self.assertEqual(self.opened, 0, "no uplink port use for heals")
        self.assertNotIn("heal", summary)
        self.assertFalse(os.path.exists(self.state_path), "no state write")
        self.assertIn("[HEAL] off-window wake: cap=40 heal_chunks=0 hl=0 uplink_msgs=1 ", out)

    def test_a_heal_event_alone_still_gets_its_hl(self):
        """An rsd refused at command time (no record) leaves only an event: its <HL>
        goes out, as on an in-window wake."""
        daemon_events = [{"key": "zzzzzz", "a": "refused", "n": 1, "r": "no_record", "id": 7}]
        orig = FakeDaemon.__init__

        def init(this, state):
            orig(this, state)
            this.heal_events = list(daemon_events)
        self.run_wake(extra_patches=[mock.patch.object(FakeDaemon, "__init__", init)])
        self.assertEqual(self.hl(), [b"<HL v=1 key=zzzzzz a=refused n=1 r=no_record id=7>\n"])
        self.assertEqual(self.chunks(), [])

    def test_cap_20(self):
        self.backlog(60)
        _, out = self.run_wake(cap=20)
        self.assertEqual(self.chunks(), [golden_chunk_line(n) for n in range(20)])
        self.assertEqual(self.persisted()[0]["n"], list(range(20, 60)))
        self.assertIn("cap=20 heal_chunks=20 hl=1 uplink_msgs=22 ", out)

    def test_a_failing_heal_pass_keeps_the_tail(self):
        self.backlog(5)

        def broken_open(_path):
            raise OSError("uart gone")
        self.bm_open = broken_open
        summary, out = self.run_wake()
        self.assertEqual(self.wire[0], ("ws", "skip_win"))
        self.assertEqual(self.wire[-1], ("tail",))
        self.assertIn("[HEAL][WARN] off-window heal pass failed (OSError: uart gone)", out)
        self.assertNotIn("error", summary)

    def test_the_wake_line_reads_uptime(self):
        with mock.patch.object(rc.cmd_hooks, "boot_mark", return_value=257.94):
            _, out = self.run_wake()
        self.assertRegex(out, r"\[HEAL\] off-window wake: cap=40 heal_chunks=0 hl=0 uplink_msgs=1 "
                              r"heal_s=\d+\.\d tail_s=150\.0 cycle_s=\d+\.\d uptime_s=257\.9\n")


class TestOffWindowCap0(OffWindowWake):
    def _snapshot(self, **kw):
        summary, out = self.run_wake(**kw)
        with open(self.state_path, "rb") as fh:
            state = fh.read()
        return self.wire, summary, state, out

    def test_cap_0_is_the_v13_wake(self):
        self.backlog(60)
        wire, summary, state, out = self._snapshot(cap=0)
        self.assertEqual(wire, [("ws", "skip_win"), ("tail",)])
        self.assertEqual(self.opened, 0)
        self.assertEqual(self.persisted(), [{"key": OLD_KEY, "n": list(range(60)),
                                             "id": 100001, "wakes_left": 3}], "nothing aged")
        self.assertIn("[HEAL] off-window wake: cap=0 heal_chunks=0 hl=0 uplink_msgs=1 ", out)
        self.assertNotIn("[HEAL] wake plan", out, "no heal planning at cap 0")

        # The same wake with the d35 step removed = the registry v13 code path.
        self.wire, self.opened = [], 0
        self.state = CommandState(path=self.state_path)
        wire13, summary13, state13, out13 = self._snapshot(
            cap=0, extra_patches=[mock.patch.object(rc, "_offwin_heals", lambda *a, **k: None)])
        self.assertEqual((wire, summary, state), (wire13, summary13, state13))
        self.assertNotIn("off-window wake", out13)


class TestOffWindowUnchangedPaths(OffWindowWake):
    def test_stay_on_first_skip_sends_no_heals(self):
        self.backlog(5)
        summary, out = self.run_wake(run="stay_on")
        self.assertEqual(self.wire, [("ws", "skip_win"), ("tail",)],
                         "stay_on heals go out through the O5 idle pass, not per skip")
        self.assertNotIn("off-window wake", out)
        self.assertEqual(self.persisted()[0]["wakes_left"], 3)

    def test_legacy_runtime_unchanged(self):
        self.backlog(5)
        summary, out = self.run_wake(supervised=False)
        self.assertEqual(self.wire, [("ws", "skip_win")], "legacy: no tail, no heals")
        self.assertNotIn("off-window wake", out)


# --- in-window: the key does not touch the in-window heal slot -----------------------------

class TestInWindowIgnoresTheKey(IntegrationHarness):
    def test_cap_0_still_heals_in_the_window(self):
        with open(self.config_path, "a", encoding="utf-8") as fh:
            fh.write("media_key:\n  enabled: false\n  heal_offwin_max: 0\n")
        sent_dir = os.path.join(self.tmpdir.name, "sent")
        write_old_record(sent_dir)
        self.state.record(100004, "rsd", {"h": [[OLD_KEY, [0, 1]]]})
        with mock.patch.object(rc_heal, "_sent_dir", lambda s: sent_dir):
            r = self.run_rc(transmit=True)
        self.assertIsNone(r.summary.get("error"))
        img = [m for k, m in self.wire if k == "img"]
        self.assertEqual([m.encode("ascii") for m in img[:2]],
                         [golden_chunk_line(0), golden_chunk_line(1)],
                         "in the window the heal slot uses HEAL_CAP_PER_WAKE, not the key")
        self.assertTrue(img[2].startswith("<START IMG>"))
        self.assertEqual(r.summary["heal"]["sent"], 2)


if __name__ == "__main__":
    unittest.main()

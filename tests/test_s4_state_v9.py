#!/usr/bin/env python3
# filename: test_s4_state_v9.py
# description: Sprint26 S4 a.4 — V9State: one writer (B1), D15 atomic commit, dedupe v2 cache, high-water, fold/re-fold (G2), trigger, heals.
"""
Sprint26 S4 commit a.4 (PLAN_S4.md a.4, G1, G2, G9; reviews B1, A13, A14/B5).

Pins:
  - B1: a v9 overlay write, then rc_heal's set_pending_heals, then a trigger
    consume, then a reload keeps ALL of them (one owner of the file);
  - D15: a failed persist leaves memory AND the file exactly as before;
  - journal: one line per changed key after the persist; a journal failure
    never fails the commit;
  - dedupe v2: the original answer comes back; 256 kept, oldest evicted;
    high-water per range; the cache survives a restart;
  - G2 fold: for every golden STATE_FIXTURES entry, base ⊕ folded overlay has
    the same hash as S2's base ⊕ (v8 then overlay); re-fold lets CHANGED v8
    keys win and leaves later overlay commits alone; an armed v8 trigger moves
    once and is cleared in v8;
  - a persisted trigger kv is re-validated on load (invalid -> dropped);
  - heals follow command_state's rules exactly (the v8 class is the oracle);
  - an unreadable file is kept aside before the first save.

Run (repo root):
  python3 -m unittest tests.test_s4_state_v9 -v
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
sys.path.insert(0, os.path.join(REPO_ROOT, "tests", "golden"))

import atomic_io  # noqa: E402
import command_state_v9 as S  # noqa: E402
import config_journal  # noqa: E402
import config_migrate  # noqa: E402
import config_v2 as C  # noqa: E402
from command_state import CommandState  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402
import scenarios  # noqa: E402

LOG = []


def st(path, **kw):
    return S.V9State(path, log=LOG.append, **kw)


class Base(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="v9state_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.path = os.path.join(self.dir, "bm_command_state_v2.json")

    def raw(self):
        with open(self.path, "rb") as fh:
            return fh.read()


class OneWriter(Base):
    def test_v9_write_survives_heal_and_trigger_updates(self):
        s = st(self.path)
        s.commit([("mode.run", "stay_on")], cid=1_000_001, source="remote")
        s.transaction(lambda x: (x.record_heals(100_002, {"h": [["k3a9zq", [1, 2]]]}),
                                 x.arm_trigger(1_000_002, 2, {"d": 8})))
        s.remember(1_000_001, {"ok": 1, "h": "a41c09e2"})
        s.advance_high_water("remote", 1_000_002)
        s.count_boot()
        s.set_pending_heals([{"key": "k3a9zq", "n": [2], "id": 100_002, "wakes_left": 2}])
        self.assertEqual(s.consume_trigger(), {"id": 1_000_002, "value": 2, "kv": {"d": 8}})
        again = st(self.path)
        self.assertEqual(again.overlay, {"mode.run": "stay_on"})
        self.assertEqual(again.overlay_ids, {"mode.run": 1_000_001})
        self.assertEqual(again.pending_heals,
                         [{"key": "k3a9zq", "n": [2], "id": 100_002, "wakes_left": 2}])
        self.assertIsNone(again.pending_trigger)
        self.assertEqual(again.cached(1_000_001)["h"], "a41c09e2")
        self.assertEqual(again.high_water, {"remote": 1_000_002})
        self.assertEqual(again.boot_counter, 1)

    def test_v8_section_and_unknown_keys_pass_through(self):
        v8 = {"settings": {"roi": 5}, "touched": ["roi"], "applied_ids": [1, 2],
              "pending_trigger": None, "pending_heals": []}
        with open(self.path, "w") as fh:
            json.dump({"schema": S.SCHEMA, "v8": v8, "migrated_from": "x", "future": [1]}, fh)
        s = st(self.path)
        s.commit([("mode.run", "stay_on")])
        data = json.loads(self.raw())
        self.assertEqual(data["v8"], v8)
        self.assertEqual((data["migrated_from"], data["future"]), ("x", [1]))


class Durability(Base):
    def test_failed_persist_changes_nothing(self):
        s = st(self.path)
        s.commit([("mode.run", "stay_on")], cid=1)
        before_raw, before_overlay = self.raw(), dict(s.overlay)
        with mock.patch.object(atomic_io, "write_text", side_effect=OSError("SD full")):
            with self.assertRaises(OSError):
                s.commit([("mode.run", "per_boot"), ("still.message_cap", 100)], cid=2)
            with self.assertRaises(OSError):
                s.count_boot()
            with self.assertRaises(OSError):
                s.set_pending_heals([{"key": "k3a9zq", "n": [1], "id": 5, "wakes_left": 1}])
        self.assertEqual(s.overlay, before_overlay)
        self.assertEqual(s.overlay_ids, {"mode.run": 1})
        self.assertEqual((s.boot_counter, s.pending_heals), (0, []))
        self.assertEqual(self.raw(), before_raw)

    def test_consume_trigger_failure_keeps_it_armed(self):
        s = st(self.path)
        s.transaction(lambda x: x.arm_trigger(7, 2))
        with mock.patch.object(atomic_io, "write_text", side_effect=OSError("x")):
            self.assertIsNone(s.consume_trigger())
        self.assertEqual(s.pending_trigger["id"], 7)
        self.assertEqual(st(self.path).pending_trigger["id"], 7)

    def test_journal_after_persist_and_never_fatal(self):
        s = st(self.path)
        done = s.commit([("mode.run", "stay_on"), ("still.message_cap", 150)], cid=9,
                        source="remote")
        self.assertEqual(done, [("mode.run", None, "stay_on"), ("still.message_cap", None, 150)])
        lines = config_journal.read(config_journal.path_beside(self.path))
        self.assertEqual([(e["src"], e["key"], e["new"], e["id"]) for e in lines],
                         [("remote", "mode.run", "stay_on", 9),
                          ("remote", "still.message_cap", 150, 9)])
        self.assertEqual(s.commit([("mode.run", "stay_on")], cid=10), [])   # no-op: no line
        with mock.patch.object(config_journal, "append", side_effect=RuntimeError("x")):
            s.commit([("mode.run", "per_boot")], cid=11)
        self.assertEqual(st(self.path).overlay["mode.run"], "per_boot")

    def test_remove(self):
        s = st(self.path)
        s.commit([("a.b", 1), ("c.d", 2)], cid=1)
        self.assertEqual(s.commit([("a.b", S.remove()), ("x.y", S.remove())], cid=2),
                         [("a.b", 1, None)])
        self.assertEqual(st(self.path).overlay, {"c.d": 2})

    def test_unreadable_file_kept_aside(self):
        with open(self.path, "w") as fh:
            fh.write("{torn")
        s = st(self.path)
        self.assertIsNotNone(s.load_info["error"])
        s.count_boot()
        with open(self.path + ".unreadable") as fh:
            self.assertEqual(fh.read(), "{torn")
        self.assertEqual(st(self.path).boot_counter, 1)

    def test_other_schema_not_overwritten_blindly(self):
        with open(self.path, "w") as fh:
            json.dump({"schema": "bm_command_state_v1", "settings": {}}, fh)
        s = st(self.path)
        s.count_boot()
        self.assertTrue(os.path.exists(self.path + ".unreadable"))


class Dedupe(Base):
    def test_original_answer_and_eviction(self):
        s = st(self.path)
        for cid in range(1, S.RESULT_CACHE_MAX + 2):
            s.remember(cid, {"ok": 1, "h": "0000000%d" % (cid % 10), "e": None})
        self.assertIsNone(s.cached(1))                   # 257th evicted the oldest
        self.assertEqual(s.cached(2), {"ok": 1, "h": "00000002", "b": 0})
        self.assertEqual(len(s.result_cache), S.RESULT_CACHE_MAX)
        s.save()
        self.assertEqual(st(self.path).cached(257)["h"], "00000007")

    def test_rejections_are_cached_too(self):
        s = st(self.path)
        s.remember(5, {"ok": 0, "h": "a41c09e2", "e": "xk", "k": "mode.output"})
        self.assertEqual(s.cached(5)["e"], "xk")

    def test_high_water_per_range(self):
        s = st(self.path)
        self.assertFalse(s.is_old("remote", 1_000_005))
        s.advance_high_water("remote", 1_000_005)
        s.advance_high_water("remote", 1_000_001)        # never moves back
        self.assertTrue(s.is_old("remote", 1_000_005))
        self.assertTrue(s.is_old("remote", 1_000_004))
        self.assertFalse(s.is_old("remote", 1_000_006))
        self.assertFalse(s.is_old("service", 100_000_001))


class Fold(unittest.TestCase):
    def effective_v9(self, cfg, state):
        eff = dict(cfg.base)
        eff.update(state.overlay)
        return eff

    def test_fold_keeps_the_hash_for_every_fixture(self):
        for name, records in scenarios.STATE_FIXTURES.items():
            u = Unit(self, state_records=records)
            path = u.values["commands.state_path"]
            s2 = C.read_state(path)
            want = C.load_config(u.v2, state=s2, strict=False)
            s = st(path)
            done = s.fold_v8(config_migrate.overlay_from_v8)
            self.assertTrue(done, name)
            self.assertEqual(C.config_hash(self.effective_v9(want, s)), want.hash, name)
            self.assertEqual(s.fold_v8(config_migrate.overlay_from_v8), [], name)  # idempotent
            self.assertEqual(json.loads(open(path).read())["v8"]["settings"],
                             s2["v8"]["settings"], name)

    def test_refold_changed_v8_keys_win(self):
        u = Unit(self, state_records=[(1, "exp", 4), (2, "hlt", 3)])
        path = u.values["commands.state_path"]
        s = st(path)
        s.fold_v8(config_migrate.overlay_from_v8)
        folded_ev = s.overlay["camera.exposure.ev"]
        s.commit([("camera.exposure.ev", -1.0), ("power.halt.dry_run", False)], cid=1_000_001)
        # a legacy rollback edits v8: exp changes, hlt does not
        data = json.loads(open(path).read())
        data["v8"]["settings"]["exp"] = 6          # +2 EV (exp 2 would be -1.0 = the commit)
        with open(path, "w") as fh:
            json.dump(data, fh)
        s = st(path)
        done = dict((p, n) for p, _o, n in s.fold_v8(config_migrate.overlay_from_v8))
        self.assertIn("camera.exposure.ev", done)
        self.assertNotEqual(s.overlay["camera.exposure.ev"], folded_ev)
        self.assertNotEqual(s.overlay["camera.exposure.ev"], -1.0)
        self.assertIs(s.overlay["power.halt.dry_run"], False)   # unchanged v8 key: overlay kept

    def test_first_fold_existing_overlay_wins(self):
        u = Unit(self, state_records=[(1, "hlt", 3)])
        path = u.values["commands.state_path"]
        s = st(path)
        s.commit([("power.halt.dry_run", False)], cid=5)
        s = st(path)
        s.v8_folded = None
        s.fold_v8(config_migrate.overlay_from_v8)
        self.assertIs(s.overlay["power.halt.dry_run"], False)

    def test_v8_trigger_moves_once(self):
        u = Unit(self, state_records=[(418, "trg", 2)])
        path = u.values["commands.state_path"]
        s = st(path)
        s.fold_v8(config_migrate.overlay_from_v8)
        self.assertEqual(s.pending_trigger, {"id": 418, "value": 2, "kv": {}})
        data = json.loads(open(path).read())
        self.assertIsNone(data["v8"]["pending_trigger"])
        self.assertEqual(st(path).fold_v8(config_migrate.overlay_from_v8), [])
        lines = config_journal.read(config_journal.path_beside(path))
        self.assertIn("pending_trigger", [e["key"] for e in lines if e["src"] == "migrate_v8"])


class Trigger(Base):
    def test_kv_revalidated_on_load(self):
        s = st(self.path)
        s.transaction(lambda x: x.arm_trigger(7, 2, {"d": 8}))
        self.assertEqual(st(self.path, trigger_validator=lambda kv, v: None).pending_trigger,
                         {"id": 7, "value": 2, "kv": {"d": 8}})
        dropped = st(self.path, trigger_validator=lambda kv, v: "d out of range")
        self.assertIsNone(dropped.pending_trigger)
        self.assertIn("pending_trigger_v9", dropped.load_info["dropped"])

    def test_malformed_trigger_dropped(self):
        for bad in ({"id": True, "v": 2}, {"id": 1, "v": 0}, {"id": 1, "v": 9}, [1],
                    {"id": 1, "v": 2, "kv": [1]}):
            with open(self.path, "w") as fh:
                json.dump({"schema": S.SCHEMA, "pending_trigger_v9": bad}, fh)
            self.assertIsNone(st(self.path).pending_trigger, bad)

    def test_cancel(self):
        s = st(self.path)
        s.transaction(lambda x: x.arm_trigger(7, 2))
        s.transaction(lambda x: x.arm_trigger(8, 0))
        self.assertIsNone(st(self.path).pending_trigger)


class Heals(Base):
    def test_same_rules_as_command_state(self):
        v1 = os.path.join(self.dir, "v1.json")
        oracle = quiet(CommandState, path=v1)
        s = st(self.path)
        seq = [(100_001, {"h": [["aaaaaa", [1, 2]], ["bbbbbb", [3]]]}),
               (100_002, {"h": [["cccccc", [4]], ["aaaaaa", [9]]]}),
               (100_003, {"h": [[f"k{i:05d}", [i]] for i in range(8)]}),
               (100_004, {"h": []}),
               (100_005, {"x": 1}),
               (100_006, {"h": [["dddddd", [5, 5, 1]]]})]
        for cid, value in seq:
            quiet(oracle._record_heals, cid, value)
            s.record_heals(cid, value)
            self.assertEqual(s.pending_heals, oracle.pending_heals, cid)


if __name__ == "__main__":
    unittest.main()

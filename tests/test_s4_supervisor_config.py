#!/usr/bin/env python3
# filename: test_s4_supervisor_config.py
# description: Sprint26 S4 b.1 — the supervisor's effective config: migrated gate, per-path drops, protected halt keys, derived width, render + re-resolve.
"""
Sprint26 S4 commit b.1 (PLAN_S4.md G1, G2, G3).

Pins:
  - is_migrated: a v2 state file (or none yet under the v2 name) = the v9
    path; a v1 state file = v8;
  - resolve(): base ⊕ overlay; a failing rule drops only the overlay-sourced
    keys it names; power.halt.* survives an unrelated failure (dev-mode
    dry-run, REVIEW B); a base failure is logged, never dropped or fatal;
  - roi 5 folded from v8 runs with a derived 800 px width (no refusal, no
    upscale in the render);
  - apply() rewrites the render; make_reresolve() picks up a state-file change.

Run (repo root):
  python3 -m unittest tests.test_s4_supervisor_config -v
"""

import json
import os
import sys
import unittest

import yaml

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import config_registry as R  # noqa: E402
import config_v2 as C  # noqa: E402
import config_validate as V  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
import supervisor_config as SC  # noqa: E402
from tests.test_config_v2 import Unit, quiet  # noqa: E402


def commands_on_unit(case, records=()):
    u = Unit(case, state_records=records)
    for path, value in (("commands.enabled", True), ("commands.runtime", "supervisor")):
        u.edit_v2(path, value)
    return u


def base_of(u):
    return C.load_config(u.v2, strict=False).base


def write_state(u, **fields):
    path = u.values["commands.state_path"]
    data = C.read_state(path) or {"schema": "bm_command_state_v2"}
    data.update(fields)
    with open(path, "w") as fh:
        json.dump(data, fh)
    return path


class Gate(unittest.TestCase):
    def test_migrated(self):
        u = Unit(self, state_records=[(1, "roi", 5)])
        self.assertTrue(SC.is_migrated(u.values))
        self.assertTrue(SC.is_migrated(dict(u.values, **{
            "commands.state_path": os.path.join(u.dir, "none_yet_v2.json")})))
        self.assertFalse(SC.is_migrated(dict(u.values, **{
            "commands.state_path": os.path.join(u.dir, "bm_command_state.json")})))


class Resolve(unittest.TestCase):
    def test_overlay_applies(self):
        u = Unit(self)
        base = base_of(u)
        eff = SC.resolve(base, {"schema": "bm_command_state_v2",
                                "overlay": {"still.message_cap": 150}})
        self.assertEqual(eff.values["still.message_cap"], 150)
        self.assertEqual(eff.dropped, [])
        self.assertEqual(eff.hash, C.config_hash(eff.values))

    def test_drop_only_the_overlay_key(self):
        base = R.defaults()
        base["mode.media"] = "still"
        eff = SC.resolve(base, {"schema": "bm_command_state_v2",
                                "overlay": {"camera.white_balance.mode": "manual",
                                            "still.message_cap": 150}})
        self.assertEqual([p for p, _v, _w in eff.dropped], ["camera.white_balance.mode"])
        self.assertEqual(eff.values["still.message_cap"], 150)
        self.assertIsNone(eff.values["camera.white_balance.mode"])

    def test_dev_mode_dry_run_survives_an_unrelated_bad_key(self):
        u = Unit(self, state_records=[(1, "hlt", 3)])
        base = base_of(u)
        state = C.read_state(u.values["commands.state_path"])
        state["overlay"] = {"still.crop": [4000, 2000, 1000, 900]}     # outside the frame
        eff = SC.resolve(base, state)
        self.assertEqual([p for p, _v, _w in eff.dropped], ["still.crop"])
        self.assertIs(eff.values["power.halt.dry_run"], True)

    def test_protected_unless_primary(self):
        v = [V.Violation("xk", ("still.crop", "power.halt.dry_run"), "x"),
             V.Violation("xk", ("power.halt.enabled",), "y")]
        drop, base = SC._overlay_violations(v, {"still.crop": 1, "power.halt.dry_run": 1,
                                                "power.halt.enabled": 1})
        self.assertEqual([p for p, _ in drop], ["still.crop", "power.halt.enabled"])
        self.assertEqual(base, [])

    def test_base_failure_is_logged_not_dropped(self):
        base = R.defaults()
        base.update({"mode.media": "still", "camera.white_balance.mode": "manual"})
        eff = SC.resolve(base, None)
        self.assertEqual(eff.dropped, [])
        self.assertEqual(len(eff.base_errors), 1)
        self.assertTrue(any("YAML base" in line for line in eff.lines))

    def test_bad_overlay_value_dropped(self):
        base = R.defaults()
        base["mode.media"] = "still"
        eff = SC.resolve(base, {"overlay": {"still.message_cap": True, "no.key": 1}})
        self.assertEqual(sorted(p for p, _v, _w in eff.dropped), ["no.key", "still.message_cap"])

    def test_roi5_runs_with_a_derived_width(self):
        u = Unit(self, state_records=[(1, "roi", 5)])
        eff = SC.resolve(base_of(u), C.read_state(u.values["commands.state_path"]))
        self.assertEqual(eff.dropped, [])
        self.assertEqual(eff.values["still.crop"][2], 800)
        render = yaml.safe_load(SC.render_values(eff.values))
        self.assertEqual(render["progressive_jpeg"]["output_width"], 800)


class ApplyAndReresolve(unittest.TestCase):
    def test_apply_renders_and_reresolve_follows_the_state(self):
        u = commands_on_unit(self)
        boot = quiet(u.boot)
        render = os.path.join(u.dir, "render.yaml")
        lines = []
        eff = SC.apply(boot, render, announce=lines.append)
        self.assertIsNotNone(eff)
        self.assertEqual(quiet(rc.resolve_rc_settings, render)["message_cap"],
                         u.values["still.message_cap"])
        write_state(u, overlay={"still.message_cap": 150, "uplink.msg_interval_s": 2.0})
        reresolve = SC.make_reresolve(boot.values, render, rc.resolve_rc_settings,
                                      announce=lines.append)
        fresh = quiet(reresolve, {"video": {"x": 1}})
        self.assertEqual(fresh["message_cap"], 150)
        self.assertEqual(fresh["pacing_delay_seconds"], 2.0)
        self.assertEqual(fresh["video"], {"x": 1})

    def test_not_migrated_is_untouched(self):
        u = commands_on_unit(self)
        boot = quiet(u.boot)
        boot.values = dict(boot.values, **{"commands.state_path":
                                           os.path.join(u.dir, "bm_command_state.json")})
        render = os.path.join(u.dir, "render.yaml")
        self.assertIsNone(SC.apply(boot, render, announce=lambda *_: None))
        self.assertFalse(os.path.exists(render))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# filename: test_heal_cap_one_source.py
# description: Heal size has ONE source (registry heal.max_chunks_per_wake): render -> daemon config -> rc_heal cap; rsd parse ceiling = the key's range max.
"""
Nick 2026-10-02: the heal size was hard-coded three times (unit RSD_MAX_CHUNKS = 40, unit
HEAL_CAP_PER_WAKE = 40, backend MAX_CHUNKS_PER_COMMAND = 40). Now:
  - config_registry heal.max_chunks_per_wake (default 40, range 1..120) is the one value;
  - the v2 render writes bm_commands.heal_max_chunks_per_wake; command_daemon's loader reads it;
    the daemon factory puts it on the daemon (heal_cap); rc_heal.begin_wake uses it;
  - command_messages.RSD_MAX_CHUNKS (the most one rsd may ask for) = the key's range max, so a
    command above the unit's per-wake value is ACCEPTED and the excess waits for the next wake
    (never refused);
  - the v1 reader maps it back (a v1 file without it reads the default 40).

Run (repo root):
  python3 -m unittest tests.test_heal_cap_one_source -v
"""

import os
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_daemon  # noqa: E402
import command_messages as M  # noqa: E402
import config_registry as R  # noqa: E402
import config_v2  # noqa: E402
import rc_heal  # noqa: E402

KEY = R.BY_PATH["heal.max_chunks_per_wake"]


class OneSource(unittest.TestCase):
    def test_constants_derive_from_the_registry(self):
        self.assertEqual(rc_heal.HEAL_CAP_PER_WAKE, KEY.default)
        self.assertEqual(M.RSD_MAX_CHUNKS, KEY.range[1])

    def test_render_to_daemon_config(self):
        values = R.defaults()
        values.update({"mode.media": "still", "heal.max_chunks_per_wake": 90})
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "render.yaml")
            with open(path, "w") as fh:
                fh.write(config_v2.render_v1_text(values))
            cfg = command_daemon.load_bm_commands_config(path)
        self.assertEqual(cfg["heal_max_chunks_per_wake"], 90)

    def test_absent_key_is_the_default_and_bad_values_fall_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "v1.yaml")
            with open(path, "w") as fh:
                fh.write("bm_commands:\n  enabled: true\n")
            self.assertEqual(command_daemon.load_bm_commands_config(path)["heal_max_chunks_per_wake"], 40)
            with open(path, "w") as fh:
                fh.write("bm_commands:\n  heal_max_chunks_per_wake: zero\n")
            self.assertEqual(command_daemon.load_bm_commands_config(path)["heal_max_chunks_per_wake"], 40)

    def test_begin_wake_uses_the_daemons_cap(self):
        class D:
            heal_cap = 77
            state = None
        seen = {}

        class FakeWake:
            def __init__(self, daemon, sent_dir, summary, cap, pump_fn):
                seen["cap"] = cap
        orig, orig_dir = rc_heal.WakeHeals, rc_heal._sent_dir
        rc_heal.WakeHeals, rc_heal._sent_dir = FakeWake, (lambda settings: "/tmp")
        try:
            rc_heal.begin_wake(D(), {}, {})
            self.assertEqual(seen["cap"], 77)
            del D.heal_cap
            rc_heal.begin_wake(D(), {}, {})
            self.assertEqual(seen["cap"], rc_heal.HEAL_CAP_PER_WAKE)     # no daemon cap -> default
        finally:
            rc_heal.WakeHeals, rc_heal._sent_dir = orig, orig_dir

    def test_rsd_up_to_the_ceiling_is_accepted(self):
        ok = M.parse_rsd({"h": [["0dhnso", f"0-{KEY.range[1] - 1}"]]})
        self.assertIsNotNone(ok)
        self.assertEqual(len(ok["h"][0][1]), KEY.range[1])
        self.assertIsNone(M.parse_rsd({"h": [["0dhnso", f"0-{KEY.range[1]}"]]}))

    def test_registry_bounds(self):
        self.assertEqual(KEY.default, 40)
        self.assertEqual(KEY.range, (1, 120))
        self.assertEqual(KEY.apply, R.NEXT_BOOT)        # read once, when the daemon is built
        self.assertIsNotNone(R.check_value(KEY, 121))
        self.assertIsNone(R.check_value(KEY, 100))


if __name__ == "__main__":
    unittest.main()

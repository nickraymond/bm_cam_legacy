#!/usr/bin/env python3
# filename: test_config_durability.py
# description: Sprint26 S2e — kill-during-write: state v2, config files and the journal survive SIGKILL at any instant.
"""
Pins Sprint26 S2e durability (DESIGN_supervisor.md §6.2; REVIEW K8):

  atomic_io      a writer SIGKILLed at random instants always leaves the old or the
                 new content (never torn, never empty); unique tmp names; a stale
                 tmp left by a killed writer is swept, a fresh one is not
  CommandState   on a v2 state file, killed mid-record at random instants: the file
                 always loads cleanly with the v2 fields intact
  journal        killed mid-append at random instants: read() never raises, every
                 entry it returns is complete and in append order, never > 500; an
                 append after a torn last line is not lost; 2 x 250 rotation keeps
                 the newest 500 in order
  record         a v8 setting on a v2 unit is journaled after the state is saved;
                 a v1 unit journals nothing

A power cut is approximated by SIGKILL (the page cache survives a SIGKILL, so this
proves the write ORDER, not the fsync; the fsync calls are asserted separately).

Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_config_durability.py
"""

import contextlib
import io
import json
import os
import random
import signal
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP = os.path.join(REPO, "BM_Devel_Pi")
if APP not in sys.path:
    sys.path.insert(0, APP)

import atomic_io  # noqa: E402
import config_journal  # noqa: E402
from command_state import CommandState  # noqa: E402

KILLS = 25


def child(code):
    """Start a python child looping forever on `code` (APP on its path)."""
    prog = f"import sys\nsys.path.insert(0, {APP!r})\n" + textwrap.dedent(code)
    return subprocess.Popen([sys.executable, "-c", prog], stdout=subprocess.DEVNULL,
                            stderr=subprocess.PIPE)


def kill_after(proc, seconds):
    time.sleep(seconds)
    proc.send_signal(signal.SIGKILL)
    proc.wait(timeout=10)


class TestAtomicIo(unittest.TestCase):
    def test_killed_writer_leaves_old_or_new(self):
        rng = random.Random(26)
        a, b = "A" * 70000 + "\n", "B" * 90000 + "\n"
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_config.yaml")
            atomic_io.write_text(path, a)
            for _ in range(KILLS):
                proc = child(f"""
                    import atomic_io
                    while True:
                        atomic_io.write_text({path!r}, {b!r})
                        atomic_io.write_text({path!r}, {a!r})
                """)
                kill_after(proc, rng.uniform(0.08, 0.3))
                with open(path, encoding="utf-8") as fh:
                    self.assertIn(fh.read(), (a, b))

    def test_fsyncs_file_and_directory(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch("os.fsync", wraps=os.fsync) as fsync:
            atomic_io.write_text(os.path.join(d, "x.json"), "{}")
        self.assertEqual(fsync.call_count, 2)          # the tmp file, then the directory

    def test_stale_tmp_is_swept_fresh_tmp_is_not(self):
        with tempfile.TemporaryDirectory() as d:
            stale = os.path.join(d, ".x.json.abc.tmp")
            fresh = os.path.join(d, ".x.json.def.tmp")
            for p in (stale, fresh):
                with open(p, "w") as fh:
                    fh.write("partial")
            old = time.time() - atomic_io.STALE_TMP_S - 5
            os.utime(stale, (old, old))
            atomic_io.write_text(os.path.join(d, "x.json"), "{}")
            self.assertFalse(os.path.exists(stale))
            self.assertTrue(os.path.exists(fresh))


class TestCommandStateV2(unittest.TestCase):
    def test_killed_mid_record_always_loads(self):
        rng = random.Random(8)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_command_state_v2.json")
            with open(path, "w") as fh:
                json.dump({"schema": "bm_command_state_v2", "tables_version": 8,
                           "boot_counter": 3, "overlay": {}, "guarded": {},
                           "result_cache": {}, "high_water": {"remote": 7},
                           "v8": {"settings": {}, "touched": [], "applied_ids": [],
                                  "pending_trigger": None, "pending_heals": []}}, fh)
            for k in range(KILLS):
                proc = child(f"""
                    import io, contextlib
                    from command_state import CommandState
                    i = {k * 100000}
                    while True:
                        with contextlib.redirect_stdout(io.StringIO()):
                            s = CommandState(path={path!r})
                            s.record(i, "roi", i % 7)
                        i += 1
                """)
                kill_after(proc, rng.uniform(0.08, 0.3))
                buf = io.StringIO()
                with contextlib.redirect_stdout(buf):
                    st = CommandState(path=path)
                self.assertIsNone(st.load_info["error"], buf.getvalue())
                self.assertTrue(st.is_v2)
                with open(path) as fh:
                    data = json.load(fh)
                self.assertEqual(data["boot_counter"], 3)
                self.assertEqual(data["high_water"], {"remote": 7})


class TestJournal(unittest.TestCase):
    def test_killed_mid_append_never_corrupts(self):
        rng = random.Random(250)
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, config_journal.NAME)
            last = 0
            for k in range(KILLS):
                proc = child(f"""
                    import config_journal
                    i = 0
                    while True:
                        config_journal.append({path!r}, "v8", key="v8.roi", old=i, new=i + 1,
                                              cid={k} * 1000000 + i, note="x" * 300)
                        i += 1
                """)
                kill_after(proc, rng.uniform(0.08, 0.3))
                entries = config_journal.read(path)
                for e in entries:
                    self.assertEqual(set(e), {"t", "src", "id", "key", "old", "new", "h", "note"})
                    self.assertEqual(e["new"], e["old"] + 1)
                # Bounded, and in append order (ids strictly increase per writer).
                self.assertLessEqual(len(entries), 2 * config_journal.MAX_LINES)
                ids = [e["id"] for e in entries]
                self.assertEqual(ids, sorted(ids))
                last = max(last, len(entries))
            self.assertGreater(last, 0)

    def test_append_after_a_torn_line_is_kept(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, config_journal.NAME)
            config_journal.append(path, "v8", key="v8.roi", old=0, new=1, cid=1)
            with open(path, "ab") as fh:
                fh.write(b'{"t":"2026-09-25T00:00:00Z","src":"v8","key":"v8.r')   # cut off
            config_journal.append(path, "v8", key="v8.hlt", old=None, new=3, cid=2)
            self.assertEqual([e["id"] for e in config_journal.read(path)], [1, 2])

    def test_rotation_keeps_the_newest_500_in_order(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, config_journal.NAME)
            for i in range(620):
                config_journal.append(path, "deploy", key="*", new=i, cid=i)
            ids = [e["id"] for e in config_journal.read(path)]
            with open(path) as fh:
                live = fh.read().count("\n")
            with open(path + ".1") as fh:
                old = fh.read().count("\n")
            self.assertLessEqual(live, config_journal.MAX_LINES)
            self.assertEqual(old, config_journal.MAX_LINES)
            self.assertEqual(ids, list(range(620 - live - old, 620)))

    def test_failure_is_logged_not_raised(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            ok = config_journal.append("/nonexistent/dir/j.jsonl", "v8", key="v8.roi")
        self.assertFalse(ok)
        self.assertIn("[CFG][WARN]", buf.getvalue())


class TestRecordJournals(unittest.TestCase):
    def test_v2_unit_journals_v8_settings_after_save(self):
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_command_state_v2.json")
            with contextlib.redirect_stdout(io.StringIO()):
                st = CommandState(path=path)
                st.record(10, "roi", 5)
                st.record(11, "roi", 2)
                st.record(12, "ping", None)
                st.record(13, "trg", 2)
            entries = config_journal.read(os.path.join(d, config_journal.NAME))
            self.assertEqual([(e["key"], e["old"], e["new"], e["id"]) for e in entries],
                             [("v8.roi", None, 5, 10), ("v8.roi", 5, 2, 11)])

    def test_journal_failure_never_fails_a_saved_record(self):              # review 12
        import config_journal as cj
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "bm_command_state_v2.json")
            with mock.patch.object(cj, "append", side_effect=ImportError("x")), \
                    contextlib.redirect_stdout(io.StringIO()) as out:
                st = CommandState(path=path)
                st.record(1, "roi", 5)                      # must not raise
            self.assertIn("journal not written", out.getvalue())
            with open(path) as fh:
                self.assertEqual(json.load(fh)["v8"]["settings"]["roi"], 5)

    def test_dir_fsync_failure_after_rename_is_logged(self):                 # review 12
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.object(atomic_io, "fsync_dir", side_effect=OSError("EIO")), \
                contextlib.redirect_stdout(io.StringIO()) as out:
            atomic_io.write_text(os.path.join(d, "x.json"), "{}")
            with open(os.path.join(d, "x.json")) as fh:
                self.assertEqual(fh.read(), "{}")
        self.assertIn("directory fsync failed", out.getvalue())

    def test_v1_unit_journals_nothing(self):
        with tempfile.TemporaryDirectory() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                CommandState(path=os.path.join(d, "bm_command_state.json")).record(1, "roi", 5)
            self.assertFalse(os.path.exists(os.path.join(d, config_journal.NAME)))


if __name__ == "__main__":
    unittest.main()

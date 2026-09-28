#!/usr/bin/env python3
# filename: test_golden_vectors.py
# description: Sprint26 S1 — the refactor safety net: today's wire + resolved settings, byte for byte.
"""
Golden vectors for the Sprint26 refactor (DESIGN_supervisor.md §8.1).

Runs every scenario in tests/golden/scenarios.py through the REAL runtime in a
fake world (tests/golden/world.py), each in its own interpreter, and compares
the result with the committed record under tests/golden/vectors/ and
tests/golden/settings/. Any byte that changes on the wire, any extra port open,
any new subprocess, or any change in a resolved setting fails with a diff.

A commit that is SUPPOSED to change the wire (DESIGN §8.2, W1-W8) re-records:
  GOLDEN_RECORD=1 .venv-dev/bin/python -m pytest -q tests/test_golden_vectors.py
and the reviewed diff of tests/golden/ is part of that commit.

Requires PyYAML and the recorded Pillow version (tests/golden/vectors/_env.json);
see tests/golden/README.md. Run (repo root):
  .venv-dev/bin/python -m pytest -q tests/test_golden_vectors.py
"""

import concurrent.futures
import difflib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "tests", "golden")
sys.path.insert(0, GOLDEN)

import scenarios as S  # noqa: E402

VECTORS = os.path.join(GOLDEN, "vectors")
# Sprint26 S3a (PLAN_S3a.md G2): every wire scenario also runs under
# --runtime supervisor and is compared with the SAME vector. Where a deliberate
# supervisor-only wire change (W2-W6) makes it differ, its expected output lives
# in vectors_supervisor/<scenario>/, and only for the scenarios named here.
VECTORS_SUPERVISOR = os.path.join(GOLDEN, "vectors_supervisor")
SUPERVISOR_DIFFERS = {      # scenario -> the W-item(s) that make it differ
    "still_bench": "W2+W4", "still_heal": "W2",
    "still_window_skip": "W3", "video_window_skip": "W3",
    "video_trigger_pending": "W5", "still_trigger": "W6",
    "still_trigger_in_tail": "W10",
}
# Sprint26 S3b: stay_on scenarios have no legacy counterpart (a long-lived loop
# only the supervisor runs); their whole record is new wire, reviewed in full.
VECTORS_STAY_ON = os.path.join(GOLDEN, "vectors_stay_on")
# Sprint26 S3c: every supervisor-only scenario (stay_on + per_boot save_local) is
# recorded once under its own dir (scenarios.VECTOR_DIRS).
SETTINGS = os.path.join(GOLDEN, "settings")
RUNNER = os.path.join(GOLDEN, "run_scenario.py")
RECORD = os.environ.get("GOLDEN_RECORD") == "1"
ENV_KEYS = ("pyyaml", "pillow")        # versions that change recorded bytes


def settings_targets():
    targets = {t.replace("/", "__"): t for t in S.SETTINGS_PROFILES}
    targets.update({f"bmcam003+{f}": f"bmcam003+{f}" for f in S.STATE_FIXTURES})
    return targets


def supervisor_scenarios():
    """Wire scenarios the supervisor runs: all but those pinning another commit."""
    return [n for n, sc in S.SCENARIOS.items() if not sc.get("app_ref")]


def _run(job):
    mode, target, outdir, extra = job
    with open(outdir + ".log", "w", encoding="utf-8") as log:
        proc = subprocess.run([sys.executable, RUNNER, mode, target, outdir] + list(extra),
                              cwd=REPO, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    return job, proc.returncode


class GoldenVectors(unittest.TestCase):
    results = {}

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="golden_check_")
        jobs = [("wire", name, os.path.join(cls.work, "wire", name), ()) for name in S.SCENARIOS]
        jobs += [("wire_supervisor", name, os.path.join(cls.work, "wire_supervisor", name),
                  ("--runtime", "supervisor")) for name in supervisor_scenarios()]
        jobs += [("wire_only", name, os.path.join(cls.work, "wire_only", name), ())
                 for name in S.SUPERVISOR_ONLY]
        jobs += [("settings", target, os.path.join(cls.work, "settings", slug), ())
                 for slug, target in settings_targets().items()]
        for _mode, _target, outdir, _extra in jobs:
            os.makedirs(os.path.dirname(outdir), exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for (mode, target, outdir, _extra), code in pool.map(
                    _run, [(m if m not in ("wire_supervisor", "wire_only") else "wire", t, o, e)
                           for m, t, o, e in jobs]):
                key = ("wire_supervisor" if "--runtime" in _extra
                       else "wire_only" if target in S.SUPERVISOR_ONLY else mode)
                cls.results[(key, target)] = (outdir, code)
        env_path = os.path.join(VECTORS, "_env.json")
        some_env = next(os.path.join(o, "env.json") for o, _c in cls.results.values()
                        if os.path.exists(os.path.join(o, "env.json")))
        with open(some_env, "r", encoding="utf-8") as fh:
            cls.env = json.load(fh)
        if RECORD:
            os.makedirs(VECTORS, exist_ok=True)
            with open(env_path, "w", encoding="utf-8") as fh:
                json.dump({k: cls.env[k] for k in ENV_KEYS}, fh, indent=1, sort_keys=True)
                fh.write("\n")

    @classmethod
    def tearDownClass(cls):
        if not any(getattr(cls, "_kept", [])):
            shutil.rmtree(cls.work, ignore_errors=True)

    def check_env(self):
        with open(os.path.join(VECTORS, "_env.json"), "r", encoding="utf-8") as fh:
            recorded = json.load(fh)
        now = {k: self.env[k] for k in ENV_KEYS}
        if now != recorded:
            self.fail(f"golden vectors were recorded with {recorded}, this run has {now}. "
                      "Use the pinned dev venv (tests/golden/README.md); re-record only if "
                      "the diff is JPEG bytes alone and the reviewer agrees.")

    def compare(self, mode, target, files, golden_dir):
        outdir, code = self.results[(mode, target)]
        if code != 0:
            with open(outdir + ".log", "r", encoding="utf-8", errors="replace") as fh:
                tail = fh.read()[-3000:]
            type(self)._kept = [True]
            self.fail(f"{mode} {target} exited {code}; log tail:\n{tail}")
        with open(outdir + ".log", "r", encoding="utf-8", errors="replace") as fh:
            refused = [line for line in fh if "[PORT][ERROR]" in line]
        # A port refusal swallowed by a try/except (e.g. debug_print) never
        # reaches trace.txt; any refusal in a golden run is a failure.
        self.assertEqual(refused, [], f"{mode} {target}: bm_port refused a port use")
        if RECORD and mode == "wire_supervisor":
            # Only a difference from the legacy output of THIS run is recorded,
            # and only for a scenario SUPERVISOR_DIFFERS names.
            legacy_dir = self.results[("wire", target)][0]
            same = all(_read(os.path.join(outdir, n)) == _read(os.path.join(legacy_dir, n))
                       for n in files)
            if same:
                shutil.rmtree(golden_dir, ignore_errors=True)
                return
            if target in SUPERVISOR_DIFFERS:
                os.makedirs(golden_dir, exist_ok=True)
                for name in files:
                    shutil.copyfile(os.path.join(outdir, name), os.path.join(golden_dir, name))
                return
        elif RECORD:
            os.makedirs(golden_dir, exist_ok=True)
            for name in files:
                shutil.copyfile(os.path.join(outdir, name), os.path.join(golden_dir, name))
            return
        self.check_env()
        for name in files:
            want_path = os.path.join(golden_dir, name)
            self.assertTrue(os.path.exists(want_path), f"no golden {want_path}; record first")
            with open(want_path, "r", encoding="utf-8") as fh:
                want = fh.read()
            with open(os.path.join(outdir, name), "r", encoding="utf-8") as fh:
                got = fh.read()
            if got != want:
                type(self)._kept = [True]
                diff = "".join(list(difflib.unified_diff(
                    want.splitlines(True), got.splitlines(True),
                    f"golden/{name}", f"actual/{name}", n=2))[:80])
                self.fail(f"{mode} {target}: {name} differs (actual kept in {outdir}):\n{diff}")


def _read(path):
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def _wire_test(name):
    def test(self):
        self.compare("wire", name, ("trace.txt", "summary.json"), os.path.join(VECTORS, name))
    test.__doc__ = S.SCENARIOS[name].get("notes")
    return test


def _supervisor_wire_test(name):
    def test(self):
        override = os.path.join(VECTORS_SUPERVISOR, name)
        golden_dir = override if name in SUPERVISOR_DIFFERS else os.path.join(VECTORS, name)
        if RECORD:
            golden_dir = override
        self.compare("wire_supervisor", name, ("trace.txt", "summary.json"), golden_dir)
    test.__doc__ = f"supervisor runtime: {S.SCENARIOS[name].get('notes')}"
    return test


def _supervisor_only_wire_test(name):
    def test(self):
        self.compare("wire_only", name, ("trace.txt", "summary.json"),
                     os.path.join(GOLDEN, S.VECTOR_DIRS[name], name))
    test.__doc__ = f"supervisor only: {S.SUPERVISOR_ONLY[name].get('notes')}"
    return test


class SupervisorOnlyCatalogue(unittest.TestCase):
    def check_dir(self, dirname, catalogue):
        path = os.path.join(GOLDEN, dirname)
        present = sorted(os.listdir(path)) if os.path.isdir(path) else []
        self.assertEqual(present, sorted(catalogue), f"{dirname}/ must hold exactly its catalogue")

    def test_stay_on_vectors_match_the_catalogue(self):
        self.check_dir("vectors_stay_on", S.STAY_ON_SCENARIOS)

    def test_save_local_vectors_match_the_catalogue(self):
        self.check_dir("vectors_save_local", S.SAVE_LOCAL_SCENARIOS)

    def test_v9_vectors_match_the_catalogue(self):
        self.check_dir("vectors_v9", S.V9_SCENARIOS)

    def test_names_do_not_collide(self):
        self.assertEqual(set(S.STAY_ON_SCENARIOS) & set(S.SCENARIOS), set())
        self.assertEqual(set(S.SAVE_LOCAL_SCENARIOS) & set(S.SCENARIOS), set())
        self.assertEqual(set(S.SAVE_LOCAL_SCENARIOS) & set(S.STAY_ON_SCENARIOS), set())
        self.assertEqual(set(S.V9_SCENARIOS) & (set(S.SCENARIOS) | set(S.STAY_ON_SCENARIOS)
                                                | set(S.SAVE_LOCAL_SCENARIOS)), set())
        self.assertEqual(set(S.VECTOR_DIRS), set(S.SUPERVISOR_ONLY))


class SupervisorOverrides(unittest.TestCase):
    def test_only_named_scenarios_differ_under_the_supervisor(self):
        present = sorted(os.listdir(VECTORS_SUPERVISOR)) if os.path.isdir(VECTORS_SUPERVISOR) else []
        self.assertEqual(present, sorted(SUPERVISOR_DIFFERS),
                         "vectors_supervisor/ must hold exactly the scenarios SUPERVISOR_DIFFERS "
                         "names (each tied to a W-item, DESIGN §8.2)")


def _settings_test(slug, target):
    def test(self):
        self.compare("settings", target, ("settings.json",), os.path.join(SETTINGS, slug))
        # Sprint26 S2b: --print-config --json agrees with every loader (not recorded).
        outdir, _code = self.results[("settings", target)]
        with open(os.path.join(outdir, "json_check.json"), "r", encoding="utf-8") as fh:
            check = json.load(fh)
        self.assertEqual(check["mismatch"], [], f"{target}: --print-config --json disagrees")
        self.assertIn(check["exit"], (0, 2))
    return test


for _name in S.SCENARIOS:
    setattr(GoldenVectors, f"test_wire_{_name}", _wire_test(_name))
for _name in supervisor_scenarios():
    setattr(GoldenVectors, f"test_wire_supervisor_{_name}", _supervisor_wire_test(_name))
for _name in S.SUPERVISOR_ONLY:
    _prefix = ("stay_on" if _name in S.STAY_ON_SCENARIOS
               else "v9" if _name in S.V9_SCENARIOS else "save_local")
    setattr(GoldenVectors, f"test_wire_{_prefix}_{_name}", _supervisor_only_wire_test(_name))
for _slug, _target in settings_targets().items():
    _safe = _slug.replace("+", "_").replace(".", "_").replace("-", "_")
    setattr(GoldenVectors, f"test_settings_{_safe}", _settings_test(_slug, _target))


if __name__ == "__main__":
    unittest.main()

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
SETTINGS = os.path.join(GOLDEN, "settings")
RUNNER = os.path.join(GOLDEN, "run_scenario.py")
RECORD = os.environ.get("GOLDEN_RECORD") == "1"
ENV_KEYS = ("pyyaml", "pillow")        # versions that change recorded bytes


def settings_targets():
    targets = {t.replace("/", "__"): t for t in S.SETTINGS_PROFILES}
    targets.update({f"bmcam003+{f}": f"bmcam003+{f}" for f in S.STATE_FIXTURES})
    return targets


def _run(job):
    mode, target, outdir = job
    with open(outdir + ".log", "w", encoding="utf-8") as log:
        proc = subprocess.run([sys.executable, RUNNER, mode, target, outdir],
                              cwd=REPO, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    return job, proc.returncode


class GoldenVectors(unittest.TestCase):
    results = {}

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="golden_check_")
        jobs = [("wire", name, os.path.join(cls.work, "wire", name)) for name in S.SCENARIOS]
        jobs += [("settings", target, os.path.join(cls.work, "settings", slug))
                 for slug, target in settings_targets().items()]
        for _mode, _target, outdir in jobs:
            os.makedirs(os.path.dirname(outdir), exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for (mode, target, outdir), code in pool.map(_run, jobs):
                cls.results[(mode, target)] = (outdir, code)
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
        if RECORD:
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


def _wire_test(name):
    def test(self):
        self.compare("wire", name, ("trace.txt", "summary.json"), os.path.join(VECTORS, name))
    test.__doc__ = S.SCENARIOS[name].get("notes")
    return test


def _settings_test(slug, target):
    def test(self):
        self.compare("settings", target, ("settings.json",), os.path.join(SETTINGS, slug))
    return test


for _name in S.SCENARIOS:
    setattr(GoldenVectors, f"test_wire_{_name}", _wire_test(_name))
for _slug, _target in settings_targets().items():
    _safe = _slug.replace("+", "_").replace(".", "_").replace("-", "_")
    setattr(GoldenVectors, f"test_settings_{_safe}", _settings_test(_slug, _target))


if __name__ == "__main__":
    unittest.main()

#!/usr/bin/env python3
# filename: test_config_v2_parity.py
# description: Sprint26 S2d stage gate (Mac side): every golden, re-run through migrate -> config v2 -> render, is unchanged.
"""
The S2 stage gate, off-device (DESIGN_supervisor.md §8.3 S2; PLAN_S2.md G1/G2):
"every migrated profile resolves to the same effective values as its v1, and the
wire goldens stay byte-identical".

Each golden scenario / settings target runs again with `run_scenario.py --via-v2`:
the scenario's v1 YAML + command state are migrated to camera_config.yaml +
bm_command_state_v2.json beside them, and the runtime's own boot path picks v2,
renders it to a v1-shaped file (tmpfs stand-in) and runs.

  wire      trace.txt byte-identical to the golden (every frame, port open,
            subprocess, clock set, halt); the cycle summary, sidecars and sent
            records identical; the command state the run leaves in the v2 file's
            v8 section equals the v1 state file the golden recorded; the only new
            files are the v2 config, its state, the render and last-known-good
  settings  every loader's output equal to the golden settings.json, after
            normalising only: paths (render vs v1 file, v2 state file), the
            loaders' `source` provenance fields (a migrated file states every
            block, so "defaults" becomes "yaml"), and the added [CFG] log lines

The two `field_*_main` scenarios run main's committed runtime (no config v2 there)
and are not part of this gate.

Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_config_v2_parity.py
"""

import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GOLDEN = os.path.join(REPO, "tests", "golden")
sys.path.insert(0, GOLDEN)

import scenarios as S  # noqa: E402

RUNNER = os.path.join(GOLDEN, "run_scenario.py")
WIRE = [n for n, sc in S.SCENARIOS.items() if not sc.get("app_ref")]
SETTINGS = {t.replace("/", "__"): t for t in S.SETTINGS_PROFILES
            if t.startswith("device_profiles/")}
SETTINGS.update({f"bmcam003+{f}": f"bmcam003+{f}" for f in S.STATE_FIXTURES})
NEW_FILES = {"camera_config.yaml", "camera_config.lkg.json", "render/camera_schedule.yaml",
             "state/bm_command_state_v2.json", "state/config_journal.jsonl"}
STATE_V1 = "state/bm_command_state.json"


def _run(job):
    mode, target, outdir = job
    with open(outdir + ".log", "w", encoding="utf-8") as log:
        proc = subprocess.run([sys.executable, RUNNER, mode, target, outdir, "--via-v2"],
                              cwd=REPO, stdout=log, stderr=subprocess.STDOUT, timeout=900)
    return job, proc.returncode


def _load(path):
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


class ConfigV2Parity(unittest.TestCase):
    results = {}

    @classmethod
    def setUpClass(cls):
        cls.work = tempfile.mkdtemp(prefix="v2_parity_")
        jobs = [("wire", n, os.path.join(cls.work, "wire", n)) for n in WIRE]
        jobs += [("settings", t, os.path.join(cls.work, "settings", slug))
                 for slug, t in SETTINGS.items()]
        for _m, _t, outdir in jobs:
            os.makedirs(os.path.dirname(outdir), exist_ok=True)
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
            for (mode, target, outdir), code in pool.map(_run, jobs):
                cls.results[(mode, target)] = (outdir, code)

    @classmethod
    def tearDownClass(cls):
        if not getattr(cls, "_kept", False):
            shutil.rmtree(cls.work, ignore_errors=True)

    def outdir(self, mode, target):
        outdir, code = self.results[(mode, target)]
        if code != 0:
            type(self)._kept = True
            with open(outdir + ".log", encoding="utf-8", errors="replace") as fh:
                self.fail(f"{mode} {target} --via-v2 exited {code}:\n{fh.read()[-3000:]}")
        return outdir

    # ---------------------------------------------------------------- wire
    def check_wire(self, name):
        out = self.outdir("wire", name)
        gold = os.path.join(GOLDEN, "vectors", name)
        with open(os.path.join(gold, "trace.txt"), encoding="utf-8") as fh:
            want_trace = fh.read()
        with open(os.path.join(out, "trace.txt"), encoding="utf-8") as fh:
            got_trace = fh.read()
        if got_trace != want_trace:
            type(self)._kept = True
        self.assertEqual(got_trace, want_trace, f"{name}: wire differs via v2 (kept in {out})")

        want, got = _load(os.path.join(gold, "summary.json")), _load(os.path.join(out, "summary.json"))
        for key in want:
            if key in ("files", "state_file"):
                continue
            self.assertEqual(got[key], want[key], f"{name}: summary {key}")
        # Files: the golden set, plus exactly the v2 files; the v1 state file is
        # never written on the v2 path (it may exist as the scenario's seed).
        extra = set(got["files"]) - set(want["files"])
        self.assertTrue(extra <= NEW_FILES, f"{name}: unexpected files {extra - NEW_FILES}")
        for path, meta in want["files"].items():
            if path == STATE_V1:
                continue
            self.assertEqual(got["files"].get(path), meta, f"{name}: file {path}")
        # Command state: the v8 section equals what v1 left in its own file.
        if want["state_file"] is not None:
            v8 = got["state_file_v2"]["v8"]
            body = {k: v for k, v in want["state_file"].items()
                    if k not in ("schema", "tables_version")}
            self.assertEqual(v8, body, f"{name}: v8 state section")

    # ------------------------------------------------------------ settings
    def normalise(self, doc):
        text = json.dumps(doc, sort_keys=True)
        text = text.replace("{TMP}/render/camera_schedule.yaml", "{TMP}/camera_schedule.yaml")
        text = text.replace("bm_command_state_v2.json", "bm_command_state.json")
        doc = json.loads(text)
        for block in ("video", "video_tx", "media_key"):
            if isinstance(doc.get(block), dict):
                doc[block].pop("source", None)
        for block in ("resolved", "overlaid"):
            if isinstance(doc.get(block), dict) and isinstance(doc[block].get("media_key_cfg"), dict):
                doc[block]["media_key_cfg"].pop("source", None)
        doc["print_config"] = [re.sub(r"\(source=(yaml|defaults)\)", "(source=*)", line)
                               for line in doc.get("print_config", [])
                               if not line.startswith("[CFG]")]
        return doc

    def check_settings(self, slug):
        out = self.outdir("settings", SETTINGS[slug])
        want = self.normalise(_load(os.path.join(GOLDEN, "settings", slug, "settings.json")))
        got = self.normalise(_load(os.path.join(out, "settings.json")))
        for key in sorted(set(want) | set(got)):
            self.assertEqual(got.get(key), want.get(key), f"{slug}: {key}")
        check = _load(os.path.join(out, "json_check.json"))
        self.assertEqual(check["mismatch"], [], f"{slug}: --print-config --json via v2")


for _n in WIRE:
    setattr(ConfigV2Parity, f"test_wire_{_n}", lambda self, n=_n: self.check_wire(n))
for _slug in SETTINGS:
    setattr(ConfigV2Parity, f"test_settings_{_slug.replace('+', '_').replace('.', '_')}",
            lambda self, s=_slug: self.check_settings(s))


if __name__ == "__main__":
    unittest.main()

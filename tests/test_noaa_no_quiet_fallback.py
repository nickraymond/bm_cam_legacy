#!/usr/bin/env python3
# filename: test_noaa_no_quiet_fallback.py
# description: SPEC_e4 rev 13 M13-3 — an nrjxl unit missing cjxl or numpy sends no JPEG: deploy fatal, boot ERROR, trg refused, wake skip_err.
"""
SPEC_e4_noaa_release rev 13 M13-3 + rev 13.1 (a). Nick, Lead 005 chat Mon 10/26 ~8:30 PM:
"Falling back to the old JPEG is not an acceptale solution, I want JPEG-XL" and "flags if
something is missing not a silent or quiet fall back".

Pins, with cjxl and numpy each hidden from the runtime (shutil.which / find_spec patched;
the shell tests put a numpy that fails to import first on PYTHONPATH):
  (a) deploy_rc_runtime.sh refuses (exit 1, runtime untouched) when the config the unit
      will run is nrjxl: the v1 YAML, the unit's v2 camera_config.yaml, or --target-config;
      a pjpg unit only gets a line
  (b) the still action: no capture, no image on the wire, one <WS a=skip_err
      r=nodep_cjxl|nodep_numpy|nodep_cjxl_numpy> (the backend stores r= as wake_reason),
      and the console trg is refused with e=xk naming the missing tool
  (c) the boot: an overlay still.format=nrjxl is no longer dropped to the YAML's pjpg; the
      base or overlay nrjxl stays, and resolve() logs a [CFG][ERR] naming the tool
  and with both present nothing changes (the rest of the suite covers that path).

What "dropped at boot" resolved to before this change (config_validate.py:169-176 +
supervisor_config.resolve on 56dd7ca3): an OVERLAY still.format=nrjxl (a `set`) was
dropped, so the boot ran the YAML's still.format (pjpg on every shipped profile) and sent
a JPEG; a BASE nrjxl (bmcam002's target profile) was kept with a "[CFG][WARN] ... running
it anyway" line, and the wake then sent today's JPEG with rfb=enc (cjxl) or rfb=err (numpy)
(rc_progressive_jpeg.py:478-486). Both are pinned as gone below.

Run (repo root):  python3 -m pytest -q tests/test_noaa_no_quiet_fallback.py
"""

import contextlib
import importlib.util
import io
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, REPO_ROOT)

import config_registry as R  # noqa: E402
import config_validate as V  # noqa: E402
import supervisor_config as SC  # noqa: E402
from tests.test_rc_orchestrator import OrchestratorHarness, rc_yaml  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402
from tests import test_deploy_rc_runtime as TD  # noqa: E402

ENV_OK = {"ffmpeg": True, "timezones_ok": None, "cjxl": True, "numpy": True}
NRJXL_ISLAND = 'still_raw:\n  format: "nrjxl"\n  layout: "rgb"\n'
REAL_WHICH = shutil.which
REAL_FIND_SPEC = importlib.util.find_spec


@contextlib.contextmanager
def hidden(*tools):
    """Hide cjxl (PATH lookup) and/or numpy (import spec) from the runtime's probes."""
    which = lambda name, *a, **k: None if name in tools else REAL_WHICH(name, *a, **k)  # noqa: E731
    spec = lambda name, *a, **k: None if name in tools else REAL_FIND_SPEC(name, *a, **k)  # noqa: E731
    with mock.patch.object(shutil, "which", side_effect=which), \
            mock.patch.object(importlib.util, "find_spec", side_effect=spec):
        yield


class Rules(unittest.TestCase):
    def test_missing_still_deps(self):
        v = dict(R.defaults(), **{"still.format": "nrjxl"})
        self.assertEqual(V.missing_still_deps(v, ENV_OK), [])
        self.assertEqual(V.missing_still_deps(v, dict(ENV_OK, cjxl=False)), ["cjxl"])
        self.assertEqual(V.missing_still_deps(v, dict(ENV_OK, cjxl=False, numpy=False)),
                         ["cjxl", "numpy"])
        self.assertEqual(V.missing_still_deps(v, None), [])            # not probed
        self.assertEqual(V.missing_still_deps(dict(v, **{"still.format": "pjpg"}),
                                              dict(ENV_OK, cjxl=False)), [])
        self.assertEqual(V.dep_reason(["cjxl", "numpy"]), "nodep_cjxl_numpy")

    def test_set_still_refuses_and_names_the_tool(self):
        v = dict(R.defaults(), **{"still.format": "nrjxl"})
        all_errs = V.validate(v, "effective", env=dict(ENV_OK, numpy=False))
        errs = [x for x in all_errs if V.is_dep_violation(x)]
        self.assertEqual(len(errs), 1)
        self.assertIn("numpy", errs[0].message)
        self.assertIn("apt-get install -y python3-numpy", errs[0].message)
        # the s28 media-key rule also starts "nrjxl needs ": it is NOT a dependency line,
        # so the boot still drops an overlay that breaks it
        mk = [x for x in all_errs if "media_key" in x.message]
        self.assertTrue(mk and not any(V.is_dep_violation(x) for x in mk), all_errs)


class Boot(unittest.TestCase):
    """(c) supervisor_config.resolve, the boot's effective config."""

    def base(self, fmt):
        # a valid nrjxl config otherwise (keyed media, as bmcam002's target profile)
        return dict(R.defaults(), **{"mode.media": "still", "still.format": fmt,
                                     "uplink.media_key.enabled": True})

    def test_overlay_nrjxl_is_kept_not_dropped_to_pjpg(self):
        state = {"schema": "bm_command_state_v2", "overlay": {"still.format": "nrjxl"}}
        with mock.patch.object(SC.config_v2, "state_overlay", return_value={"still.format": "nrjxl"}):
            eff = SC.resolve(self.base("pjpg"), state, env=dict(ENV_OK, cjxl=False))
        self.assertEqual(eff.values["still.format"], "nrjxl")          # was: dropped -> pjpg
        self.assertEqual(eff.dropped, [])
        self.assertEqual(eff.missing_deps, ["cjxl"])
        err = [ln for ln in eff.lines if ln.startswith("[CFG][ERR]")]
        self.assertEqual(len(err), 1, eff.lines)
        self.assertIn("cjxl missing", err[0])
        self.assertIn("NO image", err[0])
        self.assertIn("r=nodep_cjxl", err[0])

    def test_base_nrjxl_logs_an_error_not_a_warning(self):
        eff = SC.resolve(self.base("nrjxl"), None, env=dict(ENV_OK, numpy=False))
        self.assertEqual(eff.values["still.format"], "nrjxl")
        self.assertEqual(eff.missing_deps, ["numpy"])
        self.assertFalse([ln for ln in eff.lines if "running it anyway" in ln], eff.lines)
        self.assertTrue([ln for ln in eff.lines
                         if ln.startswith("[CFG][ERR]") and "numpy missing" in ln], eff.lines)

    def test_deps_present_no_line_and_hash_unchanged(self):
        eff = SC.resolve(self.base("nrjxl"), None, env=ENV_OK)
        self.assertEqual((eff.missing_deps, eff.lines), ([], []))
        # the config hash depends on values only, never on the probe
        self.assertEqual(eff.hash, SC.resolve(self.base("nrjxl"), None,
                                              env=dict(ENV_OK, cjxl=False)).hash)


class Trg(unittest.TestCase):
    """(b) the console answer: trg 1-4 refused, naming the tool; trg 0 still cancels."""

    def rig(self, env, **base):
        r = Rig(self, base_over=dict({"still.format": "nrjxl"}, **base))
        r.daemon.v9_dispatch.env = env
        return r

    def test_trg_refused_when_cjxl_missing(self):
        r = self.rig(dict(ENV_OK, cjxl=False))
        r.send({"id": 1_000_101, "c": "trg", "v": 2})
        ack = r.acks()[-1]
        self.assertEqual((ack["ok"], ack["e"], ack.get("k")), (0, "xk", "still.format"))
        text = " ".join(r.lines())
        self.assertIn("trg 2 refused: nrjxl needs cjxl", text)
        self.assertIn("no image would be sent", text)
        self.assertIsNone(r.daemon.v9_dispatch.state.pending_trigger)

    def test_trg_refused_when_numpy_missing_and_cancel_still_works(self):
        r = self.rig(dict(ENV_OK, numpy=False))
        r.send({"id": 1_000_102, "c": "trg", "v": 1})
        self.assertEqual(r.acks()[-1]["e"], "xk")
        self.assertIn("numpy", " ".join(r.lines()))
        r.send({"id": 1_000_103, "c": "trg", "v": 0})
        self.assertEqual(r.acks()[-1]["ok"], 1)

    def test_trg_armed_when_present_or_for_a_video_action(self):
        r = self.rig(ENV_OK)
        r.send({"id": 1_000_104, "c": "trg", "v": 2})
        self.assertEqual(r.acks()[-1]["ok"], 1)
        v = self.rig(dict(ENV_OK, cjxl=False), **{"mode.media": "video"})
        v.send({"id": 1_000_105, "c": "trg", "v": 2})
        self.assertEqual(v.acks()[-1]["ok"], 1)                         # a clip needs no cjxl


class Wake(OrchestratorHarness):
    """(b) the still action, end to end on the committed coral native."""

    def run_nrjxl(self, *tools, transmit=True):
        with hidden(*tools):
            return self.run_rc(yaml_extra=NRJXL_ISLAND, transmit=transmit)

    def assert_no_image(self, r, reason):
        self.assertEqual(r.tx, [], "an image (or anything else) went on the BM bus")
        self.assertEqual(len(r.wake.calls), 1)
        kw = r.wake.calls[0][1]
        self.assertEqual((kw["action"], kw["reason"]), ("skip_err", reason))
        self.assertEqual(r.summary["error"], reason)
        self.assertIsNone(r.summary.get("final_path"))
        self.assertEqual([f for f in os.listdir(r.out_dir) if "compressed" in f], [])
        self.assertIn("[RAW][ERROR] still.format=nrjxl but", r.stdout)
        self.assertIn("no JPEG fallback", r.stdout)
        self.assertNotIn("rfb=enc", r.stdout)

    def test_cjxl_missing(self):
        self.assert_no_image(self.run_nrjxl("cjxl"), "nodep_cjxl")

    def test_numpy_missing(self):
        self.assert_no_image(self.run_nrjxl("numpy"), "nodep_numpy")

    def test_both_missing(self):
        self.assert_no_image(self.run_nrjxl("cjxl", "numpy"), "nodep_cjxl_numpy")

    def test_bench_run_no_transmit_says_so_and_makes_no_image(self):
        r = self.run_nrjxl("cjxl", transmit=False)
        self.assertEqual((r.tx, r.wake.calls), ([], []))
        self.assertEqual(r.summary["error"], "nodep_cjxl")
        self.assertNotIn("send plan (NO transmit)", r.stdout)

    def test_pjpg_unit_without_the_tools_is_unchanged(self):
        with hidden("cjxl", "numpy"):
            r = self.run_rc(transmit=True)
        self.assertEqual(r.wake.calls[0][1]["action"], "cap")
        self.assertTrue(r.tx)

    def test_raw_begin_never_falls_back_to_pjpg(self):
        import rc_progressive_jpeg as rc
        with tempfile.TemporaryDirectory() as d:
            path = os.path.join(d, "camera_schedule.yaml")
            with open(path, "w") as fh:
                fh.write(rc_yaml(NRJXL_ISLAND))
            with hidden("cjxl"), self.assertRaisesRegex(RuntimeError, "no JPEG fallback"):
                rc._raw_begin({"config_path": path, "crop_native_xywh": (1504, 846, 1600, 900)},
                              None, d)


class Deploy(unittest.TestCase):
    """(a) the REAL deploy_rc_runtime.sh, on test_deploy_rc_runtime's temp unit."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="nodep_")
        self.addCleanup(shutil.rmtree, self.tmp, True)
        # PATH without cjxl: only this venv's bin + the system dirs
        self.path_no_cjxl = ":".join([os.path.dirname(sys.executable), "/usr/bin", "/bin"])
        self.assertIsNone(shutil.which("cjxl", path=self.path_no_cjxl), "cjxl in /usr/bin")
        self.fake = os.path.join(self.tmp, "fakebin")
        os.makedirs(self.fake)
        with open(os.path.join(self.fake, "cjxl"), "w") as fh:
            fh.write("#!/bin/sh\necho 'cjxl v0.11.2 fake'\n")
        os.chmod(os.path.join(self.fake, "cjxl"), 0o755)
        # numpy hidden: a numpy that fails to import, first on PYTHONPATH
        self.no_numpy = os.path.join(self.tmp, "no_numpy")
        os.makedirs(os.path.join(self.no_numpy, "numpy"))
        with open(os.path.join(self.no_numpy, "numpy", "__init__.py"), "w") as fh:
            fh.write("raise ImportError('numpy hidden by test_noaa_no_quiet_fallback')\n")

    def deploy(self, u, *args, cjxl=True, numpy=True):
        env = dict(os.environ, BMCAM_PYTHON=sys.executable, BMCAM_CRONTAB_FILE=u.cron,
                   BMCAM_SERVICE_KEY_DIR=u.keys, BMCAM_RENDER_DIR=os.path.join(u.root, "shm"),
                   PATH=(self.fake + ":" if cjxl else "") + self.path_no_cjxl)
        if not numpy:
            env["PYTHONPATH"] = self.no_numpy
        return subprocess.run(["bash", TD.DEPLOY, "--repo", TD.REPO, "--dst", u.dst,
                               "--backup-dir", os.path.join(u.root, "backups"), *args],
                              capture_output=True, text=True, env=env, timeout=600)

    def nrjxl_unit(self):
        u = TD.Unit(self)
        with open(u.yaml, "a", encoding="utf-8") as fh:
            fh.write("\n" + NRJXL_ISLAND)
        return u

    def assert_refused(self, u, r, *needles):
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        for n in needles:
            self.assertIn(n, r.stdout + r.stderr)
        self.assertTrue(u.old_runtime_intact(), "a refused deploy touched the runtime")
        self.assertFalse(os.path.exists(u.dst + ".next"))

    def test_nrjxl_unit_cjxl_missing_is_fatal(self):
        u = self.nrjxl_unit()
        self.assert_refused(u, self.deploy(u, cjxl=False), "still.format is nrjxl but missing",
                            "cjxl (sudo apt-get install -y libjxl-tools)", "sends NO image")

    def test_nrjxl_unit_numpy_hidden_is_fatal(self):
        u = self.nrjxl_unit()
        r = self.deploy(u, numpy=False)
        self.assert_refused(u, r, "numpy (sudo apt-get install -y python3-numpy)")
        self.assertNotIn("cjxl (sudo", r.stdout + r.stderr)

    def test_target_config_nrjxl_is_fatal_on_a_pjpg_unit(self):
        u = TD.Unit(self)                                  # pjpg v1 unit (bmcam002 today)
        target = os.path.join(REPO_ROOT, "device_profiles", "bmcam002", "noaa_20261027",
                              "camera_config.yaml")
        self.assert_refused(u, self.deploy(u, "--target-config", target, cjxl=False),
                            "still.format=nrjxl (v2 " + target)

    def test_pjpg_unit_without_them_deploys(self):
        u = TD.Unit(self)
        r = self.deploy(u, "--accept-print-config-diff", cjxl=False, numpy=False)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("fine for still.format=pjpg", r.stdout)

    def test_nrjxl_unit_with_both_passes_the_preflight(self):
        u = self.nrjxl_unit()
        r = self.deploy(u, "--dry-run")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("preflight: still.format=nrjxl", r.stdout)


if __name__ == "__main__":
    unittest.main()

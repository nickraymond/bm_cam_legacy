#!/usr/bin/env python3
# filename: test_deploy_rc_runtime.py
# description: Sprint26 S2f — deploy_rc_runtime.sh stages, checks, then installs (refusals leave the runtime untouched).
"""
Runs the REAL tools/deploy_rc_runtime.sh against a temp runtime dir (a unit
with bmcam003's live config) and pins (DESIGN_supervisor.md §8.3 S2f):

  refusals   armed boot cycle; media_gid in the YAML; print-config text that
             differs between the old and the staged runtime (unless
             --accept-print-config-diff); a camera_config.yaml that does not
             resolve like its v1 file — each leaves the live runtime untouched
  v1 unit    parity OK, files installed, staging dir removed, sha recorded
  v2 unit    strict load + migration parity OK; deploy_history carries the
             config hash; a `deploy` line lands in config_journal.jsonl
  key        --create-service-key: 64 hex chars, mode 600, never replaced

Test hooks used: BMCAM_PYTHON (this venv: PyYAML + pyserial), BMCAM_CRONTAB_FILE,
BMCAM_SERVICE_KEY_DIR. Nothing outside the temp dir is touched.

Run (repo root):  .venv-dev/bin/python -m pytest -q tests/test_deploy_rc_runtime.py
"""

import json
import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEPLOY = os.path.join(REPO, "tools", "deploy_rc_runtime.sh")
MIGRATE = os.path.join(REPO, "tools", "config_migrate_v1_v2.py")
LIVE = os.path.join(REPO, "device_profiles", "bmcam003", "live_20260925", "camera_schedule.yaml")
SENTINEL = "# SENTINEL: the old runtime, untouched\n"


def manifest_files():
    out = []
    with open(os.path.join(REPO, "tools", "rc_runtime_manifest.txt")) as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            src = line.split(" -> ")[0].strip()
            dest = line.split(" -> ")[1].strip() if " -> " in line else os.path.basename(src)
            out.append((src, dest))
    return out


class Unit:
    def __init__(self, case):
        self.root = tempfile.mkdtemp(prefix="deploy_")
        case.addCleanup(shutil.rmtree, self.root, True)
        self.dst = os.path.join(self.root, "BM_Devel_Pi")
        os.makedirs(self.dst)
        for src, dest in manifest_files():            # the "old" runtime = this tree
            path = os.path.join(self.dst, dest)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            shutil.copy2(os.path.join(REPO, src), path)
        with open(os.path.join(self.dst, "rc_progressive_jpeg.py"), "a") as fh:
            fh.write(SENTINEL)
        with open(LIVE, encoding="utf-8") as fh:
            text = fh.read().replace("state_path: /home/pi/BM_Devel_Pi/bm_command_state.json",
                                     f'state_path: "{self.dst}/bm_command_state.json"')
        self.yaml = os.path.join(self.dst, "camera_schedule.yaml")
        with open(self.yaml, "w", encoding="utf-8") as fh:
            fh.write(text)
        self.cron = os.path.join(self.root, "crontab.txt")
        with open(self.cron, "w") as fh:
            fh.write("# DISABLED field_update: @reboot /usr/bin/flock -n /tmp/x "
                     f"{self.dst}/rc_run_capture_cycle.sh\n")
        self.keys = os.path.join(self.root, "keys")

    def deploy(self, *args):
        env = dict(os.environ, BMCAM_PYTHON=sys.executable, BMCAM_CRONTAB_FILE=self.cron,
                   BMCAM_SERVICE_KEY_DIR=self.keys, BMCAM_RENDER_DIR=os.path.join(self.root, "shm"))
        return subprocess.run(["bash", DEPLOY, "--repo", REPO, "--dst", self.dst,
                               "--backup-dir", os.path.join(self.root, "backups"), *args],
                              capture_output=True, text=True, env=env, timeout=600)

    def migrate(self):
        r = subprocess.run([sys.executable, MIGRATE, "--config", self.yaml, "--app", self.dst,
                            "--write"], capture_output=True, text=True, timeout=120)
        assert r.returncode == 0, r.stdout + r.stderr

    def old_runtime_intact(self):
        with open(os.path.join(self.dst, "rc_progressive_jpeg.py")) as fh:
            return fh.read().endswith(SENTINEL)

    def history(self):
        with open(os.path.join(self.dst, "deploy_history.log")) as fh:
            return fh.read().splitlines()


class TestDeploy(unittest.TestCase):
    def assert_refused(self, u, r, needle):
        self.assertNotEqual(r.returncode, 0, r.stdout)
        self.assertIn(needle, r.stdout + r.stderr)
        self.assertTrue(u.old_runtime_intact(), "a refused deploy touched the runtime")
        self.assertFalse(os.path.exists(os.path.join(u.dst, "deploy_history.log")))

    def test_armed_cron_is_refused(self):
        u = Unit(self)
        with open(u.cron, "w") as fh:
            fh.write(f"@reboot /usr/bin/flock -n /tmp/x {u.dst}/rc_run_capture_cycle.sh\n")
        self.assert_refused(u, u.deploy(), "ARMED")
        self.assertFalse(os.path.exists(u.dst + ".next"))

    def test_v1_unit_installs_after_parity(self):
        u = Unit(self)
        r = u.deploy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("print-config parity OK", r.stdout)
        self.assertFalse(u.old_runtime_intact(), "the new runtime was not installed")
        self.assertFalse(os.path.exists(u.dst + ".next"))
        self.assertNotIn("cfg=", u.history()[-1])
        self.assertFalse(os.path.exists(os.path.join(u.dst, "config_journal.jsonl")))

    def test_v2_unit_checks_parity_and_journals(self):
        u = Unit(self)
        u.migrate()
        r = u.deploy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("strict load OK", r.stdout)
        self.assertIn("config v2 parity OK", r.stdout)
        self.assertRegex(u.history()[-1], r" cfg=[0-9a-f]{8}$")
        sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))
        import config_journal
        entries = config_journal.read(os.path.join(u.dst, "config_journal.jsonl"))
        self.assertEqual([e["src"] for e in entries], ["migrate", "deploy"])
        self.assertEqual(entries[1]["h"], u.history()[-1].rsplit("cfg=", 1)[1])

    def test_v2_unit_with_commands_since_migration_still_deploys(self):     # review 1
        u = Unit(self)
        u.migrate()
        sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))
        import contextlib, io
        from command_state import CommandState
        with contextlib.redirect_stdout(io.StringIO()):
            st = CommandState(path=os.path.join(u.dst, "bm_command_state_v2.json"))
            st.record(4242, "hlt", 3)                        # e.g. dev_mode on
            st.record(4243, "txd", 2)
        r = u.deploy()
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("config v2 parity OK", r.stdout)

    def test_armed_line_with_a_redirect_is_seen(self):                        # review 10
        u = Unit(self)
        with open(u.cron, "w") as fh:
            fh.write(f"@reboot /usr/bin/flock -n /tmp/x {u.dst}/rc_run_capture_cycle.sh "
                     "> /tmp/l 2>&1\n")
        self.assert_refused(u, u.deploy(), "ARMED")

    def test_v2_file_that_resolves_differently_is_refused(self):
        u = Unit(self)
        u.migrate()
        path = os.path.join(u.dst, "camera_config.yaml")
        with open(path) as fh:
            text = fh.read()
        with open(path, "w") as fh:
            fh.write(text.replace("  message_cap: 190  #", "  message_cap: 191  #"))
        self.assert_refused(u, u.deploy(), "does not resolve like camera_schedule.yaml")

    def test_media_gid_is_refused(self):
        u = Unit(self)
        with open(u.yaml, "a") as fh:
            fh.write("\nmedia_gid:\n  enabled: true\n")
        self.assert_refused(u, u.deploy(), "media_gid")

    def test_print_config_diff_is_refused_unless_accepted(self):
        u = Unit(self)
        path = os.path.join(u.dst, "rc_progressive_jpeg.py")
        with open(path) as fh:
            text = fh.read()
        with open(path, "w") as fh:            # an "old" runtime that prints differently
            fh.write(text.replace("message cap: {", "message cap (old): {"))
        self.assert_refused(u, u.deploy(), "print-config differs")
        r = u.deploy("--accept-print-config-diff")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("ACCEPTED", r.stdout)

    @unittest.skipUnless(subprocess.run(["git", "-C", REPO, "cat-file", "-e", "a71b6c7^{commit}"],
                                        capture_output=True).returncode == 0,
                         "a71b6c7 (bmcam003's runtime on 2026-09-25) not in this clone")
    def test_bench_rehearsal_over_bmcam003s_runtime(self):
        """The S2 bench sequence, off-device, on bmcam003's real runtime + config:
        S2 deploy over a71b6c7 (parity), migrate, S2 reads v2 == a71b6c7 read v1,
        and the rolled-back a71b6c7 still reads the untouched v1 files."""
        import tarfile, io as _io
        u = Unit(self)
        shutil.rmtree(u.dst)
        data = subprocess.run(["git", "-C", REPO, "archive", "a71b6c7", "BM_Devel_Pi"],
                              capture_output=True, check=True).stdout
        with tarfile.open(fileobj=_io.BytesIO(data)) as tar:
            tar.extractall(u.root, filter="data")
        with open(LIVE, encoding="utf-8") as fh:
            text = fh.read().replace("state_path: /home/pi/BM_Devel_Pi/bm_command_state.json",
                                     f'state_path: "{u.dst}/bm_command_state.json"')
        with open(u.yaml, "w", encoding="utf-8") as fh:
            fh.write(text)

        def print_config(app):
            env = dict(os.environ, BMCAM_RENDER_DIR=os.path.join(u.root, "shm"))
            return subprocess.run([sys.executable, "rc_progressive_jpeg.py", "--print-config",
                                   "--config-path", u.yaml], cwd=app, env=env,
                                  capture_output=True, text=True, timeout=120).stdout

        old_v1 = print_config(u.dst)
        old_runtime = os.path.join(u.root, "a71b6c7")
        shutil.copytree(u.dst, old_runtime)                   # kept for the rollback
        r = u.deploy()                                        # S2 over a71b6c7, v1 unit
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("print-config parity OK", r.stdout)
        u.migrate()
        r = u.deploy()                                        # S2 again, now a v2 unit
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("config v2 parity OK", r.stdout)
        s2_v2 = print_config(u.dst)
        self.assertIn("[CFG] config v2", s2_v2)
        tmp = tempfile.mkdtemp(dir=u.root)
        paths = []
        for name, text in (("old", old_v1), ("new", s2_v2)):
            paths.append(os.path.join(tmp, name))
            with open(paths[-1], "w") as fh:
                fh.write(text)
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import config_parity
        self.assertEqual(config_parity.text_lines(paths[0]), config_parity.text_lines(paths[1]))
        # Rollback = the old runtime back in place: it reads the untouched v1 files.
        self.assertEqual(print_config(old_runtime), old_v1)

    def test_field_update_requires_an_explicit_profile(self):
        # Sprint26 S2f: no hostname default (a repo profile can be stale). The
        # refusal comes before stage 0, so nothing (crontab, repo) is touched.
        r = subprocess.run(["bash", os.path.join(REPO, "tools", "rc_field_update.sh"),
                            "--dry-run"], capture_output=True, text=True, timeout=60)
        self.assertEqual(r.returncode, 2)
        self.assertIn("pass --profile NAME explicitly", r.stderr)
        self.assertNotIn("stage 0", r.stdout)

    def test_service_key(self):
        u = Unit(self)
        r = u.deploy("--create-service-key")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        key = os.path.join(u.keys, "service.key")
        with open(key) as fh:
            first = fh.read().strip()
        self.assertRegex(first, r"^[0-9a-f]{64}$")
        self.assertEqual(stat.S_IMODE(os.stat(key).st_mode), 0o600)
        r = u.deploy("--create-service-key")
        self.assertIn("not replaced", r.stdout)
        with open(key) as fh:
            self.assertEqual(fh.read().strip(), first)


if __name__ == "__main__":
    unittest.main()

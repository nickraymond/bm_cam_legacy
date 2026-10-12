#!/usr/bin/env python3
# filename: test_pi_wifi_profile.py
# description: Pool day 2026-10-13 — tools/pi_wifi_profile.sh: keyfile written 0600, password never in an ssh argv.
"""
tools/pi_wifi_profile.sh gate, no hardware. A fake `ssh` logs its FULL argv to a file and runs the
remote command locally with bash; a fake `nmcli` lists the keyfiles in NM_DIR. Checks:

  1. two hosts -> two OK lines, keyfile mode 0600 with the right ssid/psk/priority, exit 0;
  2. the password appears in NO ssh argv (the EM rev-2 rule: no plaintext in argv/history);
  3. re-running backs the old keyfile up (0600) before replacing it;
  4. refusals: short password, backslash, bad con-name -> exit 2, nothing written.

Run (repo root):  python3 -m pytest -q tests/test_pi_wifi_profile.py
"""
import os
import stat
import subprocess
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(REPO_ROOT, "tools", "pi_wifi_profile.sh")
PSK = "s3cret-Pool-pass"

FAKE_SSH = """#!/bin/bash
printf '%s\\n' "ARGV: $*" >> "$SSH_ARGV_LOG"
while [ $# -gt 0 ]; do case "$1" in -o|-l|-p|-i) shift 2;; -*) shift;; *) break;; esac; done
H="$1"; shift
[ "$H" = "pi@deadhost" ] && { echo "ssh: connect to host deadhost: Connection refused" >&2; exit 255; }
exec bash -c "$*"
"""
FAKE_NMCLI = """#!/bin/bash
case "$*" in
  "connection reload") exit 0;;
  *"connection show"*) for f in "$NM_DIR"/*.nmconnection; do [ -f "$f" ] && echo "$(sed -n 's/^id=//p' "$f"):yes"; done;;
esac
"""


class TestWifiProfile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        t = self.tmp.name
        self.bin = os.path.join(t, "bin")
        os.makedirs(self.bin)
        for name, body in (("ssh", FAKE_SSH), ("nmcli", FAKE_NMCLI)):
            p = os.path.join(self.bin, name)
            with open(p, "w") as f:
                f.write(body)
            os.chmod(p, 0o755)
        self.nm = os.path.join(t, "nm")
        self.bk = os.path.join(t, "bk")
        self.argv_log = os.path.join(t, "argv.log")
        self.env = dict(os.environ, PATH=self.bin + os.pathsep + os.environ["PATH"], PI_WIFI_SUDO="",
                        NM_DIR=self.nm, NM_BACKUP_DIR=self.bk, NMCLI=os.path.join(self.bin, "nmcli"),
                        SSH_ARGV_LOG=self.argv_log)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, args, stdin):
        return subprocess.run(["bash", SCRIPT] + args, input=stdin, capture_output=True, text=True,
                              env=self.env, timeout=60)

    def keyfile(self, con):
        return os.path.join(self.nm, f"{con}.nmconnection")

    def test_writes_0600_keyfile_and_no_password_in_argv(self):
        r = self.run_script(["phone-hotspot", "pi@bmcam003", "pi@bmcam004"], f"Nick iPhone\n{PSK}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(r.stdout.count("OK pi@"), 2, r.stdout)
        kf = self.keyfile("phone-hotspot")
        self.assertEqual(stat.S_IMODE(os.stat(kf).st_mode), 0o600)
        text = open(kf).read()
        for want in ("id=phone-hotspot", "ssid=Nick iPhone", f"psk={PSK}", "key-mgmt=wpa-psk",
                     "autoconnect=true", "autoconnect-priority=0", "interface-name=wlan0", "uuid="):
            self.assertIn(want, text)
        argv = open(self.argv_log).read()
        self.assertEqual(argv.count("ARGV:"), 2)
        self.assertNotIn(PSK, argv)                     # the rule this script exists for
        self.assertNotIn(PSK, r.stdout + r.stderr)      # not echoed either

    def test_rerun_backs_up_and_priority(self):
        self.run_script(["pool-wifi", "pi@bmcam003"], f"PoolNet\n{PSK}\n")
        r = self.run_script(["--priority", "-10", "pool-wifi", "pi@bmcam003"], "PoolNet\nother-pass-123\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("priority=-10", r.stdout)
        self.assertIn("autoconnect-priority=-10", open(self.keyfile("pool-wifi")).read())
        backups = [f for f in os.listdir(self.bk) if f.startswith("nm-pool-wifi.")]
        self.assertEqual(len(backups), 1, os.listdir(self.bk))
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.bk, backups[0])).st_mode), 0o600)
        self.assertIn(f"psk={PSK}", open(os.path.join(self.bk, backups[0])).read())

    def test_unreachable_host_fails_loud(self):
        r = self.run_script(["phone-hotspot", "pi@bmcam003", "pi@deadhost"], f"Nick iPhone\n{PSK}\n")
        self.assertEqual(r.returncode, 1)
        self.assertIn("OK pi@bmcam003", r.stdout)
        self.assertIn("FAILED pi@deadhost", r.stdout)

    def test_refusals_write_nothing(self):
        for args, stdin in ((["phone-hotspot", "pi@bmcam003"], "Net\nshort\n"),
                            (["phone-hotspot", "pi@bmcam003"], "Net\nback\\slash-pass\n"),
                            (["bad name!", "pi@bmcam003"], f"Net\n{PSK}\n"),
                            (["phone-hotspot"], f"Net\n{PSK}\n")):
            r = self.run_script(args, stdin)
            self.assertEqual(r.returncode, 2, (args, r.stdout, r.stderr))
        self.assertFalse(os.path.exists(self.nm))
        self.assertFalse(os.path.exists(self.argv_log))


if __name__ == "__main__":
    unittest.main()

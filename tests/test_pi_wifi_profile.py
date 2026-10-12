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
# profiles = keyfiles in NM_DIR + any in FAKE_EXTRA (e.g. a netplan profile under /run)
files() { for f in "$NM_DIR"/*.nmconnection ${FAKE_EXTRA:-}; do [ -f "$f" ] && echo "$f"; done; }
case "$*" in
  "connection reload") exit 0;;
  "-g NAME,UUID,FILENAME connection show") files | while IFS= read -r f; do
      echo "$(sed -n 's/^id=//p' "$f"):$(sed -n 's/^uuid=//p' "$f"):$f"; done;;
  *"connection show"*) files | while IFS= read -r f; do echo "$(sed -n 's/^id=//p' "$f"):yes"; done;;
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
                     "autoconnect=true", "autoconnect-priority=0", "interface-name=wlan0", "uuid=",
                     "powersave=2"):                  # Nick's rule: Wi-Fi power save always off
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

    def uuid_of(self, path):
        return [l.split("=", 1)[1] for l in open(path).read().splitlines() if l.startswith("uuid=")][0]

    def test_rerun_keeps_uuid(self):
        """QE #154: a re-run must update the profile in place (same UUID), not replace it."""
        self.run_script(["pool-wifi", "pi@bmcam003"], f"PoolNet\n{PSK}\n")
        u1 = self.uuid_of(self.keyfile("pool-wifi"))
        r = self.run_script(["pool-wifi", "pi@bmcam003"], "PoolNet\nnew-pass-456\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertEqual(self.uuid_of(self.keyfile("pool-wifi")), u1)
        self.assertIn(f"uuid={u1}", r.stdout)
        self.assertEqual([f for f in os.listdir(self.nm) if f.endswith(".nmconnection")], ["pool-wifi.nmconnection"])

    def test_existing_profile_other_file_updated_in_place(self):
        os.makedirs(self.nm)
        other = os.path.join(self.nm, "Home WiFi.nmconnection")
        with open(other, "w") as f:
            f.write("[connection]\nid=home-wifi\nuuid=11111111-2222-3333-4444-555555555555\ntype=wifi\n")
        r = self.run_script(["home-wifi", "pi@nereus000"], f"HomeNet\n{PSK}\n")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertFalse(os.path.exists(self.keyfile("home-wifi")))          # no second file
        text = open(other).read()
        self.assertIn("uuid=11111111-2222-3333-4444-555555555555", text)
        self.assertIn("ssid=HomeNet", text)
        self.assertEqual(stat.S_IMODE(os.stat(other).st_mode), 0o600)

    def test_profile_outside_keyfile_dir_refused(self):
        run_dir = os.path.join(self.tmp.name, "run")
        os.makedirs(run_dir)
        netplan = os.path.join(run_dir, "netplan-wlan0-HomeNet.nmconnection")
        with open(netplan, "w") as f:
            f.write("[connection]\nid=home-wifi\nuuid=aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee\n")
        self.env["FAKE_EXTRA"] = netplan
        r = self.run_script(["home-wifi", "pi@nereus000"], f"HomeNet\n{PSK}\n")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("FAILED pi@nereus000", r.stdout)
        self.assertIn("REFUSED", r.stdout)
        self.assertFalse(os.path.exists(self.keyfile("home-wifi")))

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

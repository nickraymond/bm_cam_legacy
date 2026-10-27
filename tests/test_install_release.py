#!/usr/bin/env python3
# filename: test_install_release.py
# description: SPEC_e4 rev 13 M13-2 — tools/install_release.sh end to end on a temp unit: each hard fail, check-or-install, idempotent re-run.
"""
Runs the REAL tools/install_release.sh + tools/release_manifest.py against a temp "unit":
  repo   a temp git repo whose tag holds releases/<tag>/release_manifest.json and the
         target profile (the staged manifest must equal the tag's copy)
  dst    a runtime dir with bmcam002's v1 YAML and software_sha 0d03a62cf565
  PATH   fake dpkg / dpkg-query / apt-get / cjxl / rpicam-still / pgrep (fakebin/), logging
         every apt-get call; "installed" packages live in fake/installed.txt
  numpy  hidden by a numpy that fails to import, first on PYTHONPATH (bmcam004 keeps numpy:
         SPEC_e4 rev 13.1, so this path is covered here, not by the rehearsal)
rc_field_update.sh is replaced (BMCAM_FIELD_UPDATE) by a fake that copies the runtime files
and writes software_sha.txt, as deploy_rc_runtime.sh does; config_migrate_v1_v2.py is the
tag's (a fake that writes a camera_config.yaml). The post-checks run the REAL runtime
(--print-config, config_v2) on the REAL target profile, so hash fe87abab is checked for real.

Pins:
  PASS from a no-JXL start (cjxl + numpy missing): only the missing packages are installed,
    offline (apt --no-download, no source lists), simulation first; final PASS line
  re-run after a PASS: no apt-get call, step 4 skipped, PASS again (idempotent)
  every dependency present: nothing installed
  hard fails, each with the runtime untouched (software_sha still 0d03a62cf565):
    OS not in the manifest (bookworm) -> exit 3 STOP; a deb's sha256 differs (F12) ->
    exit 1; apt simulation fails ("apt blocked") -> exit 1; the simulation would remove a
    package -> exit 1; numpy still missing after the install -> exit 1 listing it;
    rpicam-still missing -> exit 1; a dirty tree (non-0-byte edit) -> exit 3;
    the staged manifest differs from the tag's -> exit 1
  a tree of only 0-byte tracked files (power cut) is repaired, then PASS

Run (repo root):  python3 -m pytest -q tests/test_install_release.py
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INSTALL = os.path.join(REPO, "tools", "install_release.sh")
MTOOL = os.path.join(REPO, "tools", "release_manifest.py")
PROFILE = "device_profiles/bmcam002/noaa_20261027/camera_config.yaml"
V1 = os.path.join(REPO, "device_profiles", "bmcam002", "camera_schedule.yaml")
TAG = "bmcam-rel-test"
PKGS = {"libjxl-tools": "0.11.2-0.1~deb13u3", "python3-numpy": "1:2.2.4+ds-1",
        "python3-yaml": "6.0.2-1+b2", "python3-pil": "11.1.0-5+deb13u4",
        "python3-serial": "3.5-2", "libjxl0.11": "0.11.2-0.1~deb13u3",
        "python3": "%d.%d.5-1" % sys.version_info[:2]}

FAKE_DPKG = r'''#!/bin/sh
case "$1" in
  --print-architecture) echo "${FAKE_ARCH:-arm64}" ;;
  --compare-versions) [ "$2" = "$4" ] && exit 1; [ "$(printf '%s\n%s\n' "$2" "$4" | sort -V | tail -1)" = "$2" ] ;;
  *) exit 2 ;;
esac
'''
FAKE_DPKG_QUERY = r'''#!/bin/sh
for pkg in "$@"; do :; done          # the package is the last argument
ver="$(awk -v p="$pkg" '$1==p {print $2}' "$FAKE/installed.txt")"
[ -n "$ver" ] || exit 1
echo "installed $ver"
'''
FAKE_APT = r'''#!/bin/sh
echo "apt-get $*" >> "$FAKE/apt.log"
sim=0; for a in "$@"; do [ "$a" = "-s" ] && sim=1; done
[ -n "$FAKE_APT_FAIL" ] && { echo "E: offline install blocked (test)"; exit 100; }
for a in "$@"; do
  case "$a" in */*.deb)
    pkg="$(basename "$a" | cut -d_ -f1)"
    if [ $sim = 1 ]; then echo "Inst $pkg"; [ -n "$FAKE_APT_REMV" ] && echo "Remv python3-picamera2"
    else
      echo "$pkg installed-by-test" >> "$FAKE/installed.txt"
      [ "$pkg" = libjxl-tools ] && cp "$FAKE/cjxl.real" "$FAKE/bin/cjxl"
      [ "$pkg" = python3-numpy ] && [ -z "$FAKE_NUMPY_STAYS_BROKEN" ] && rm -rf "$FAKE/no_numpy/numpy"
    fi ;;
  esac
done
exit 0
'''
FAKE_CJXL = r'''#!/bin/sh
if [ "$1" = "--version" ]; then echo "cjxl v${FAKE_CJXL_V:-0.11.2} ${FAKE_CJXL_V:-0.11.2} [NEON]"; exit 0; fi
printf 'JXL-test' > "$2"
'''
FAKE_FIELD_UPDATE = r'''#!/bin/sh
# test stand-in for rc_field_update.sh: deploy the runtime files + software_sha
echo "$*" > "$FAKE/field_update.args"
"$BMCAM_PYTHON" - "$REAL_REPO" "$DST" <<'EOF'
import os, shutil, sys
repo, dst = sys.argv[1:]
for raw in open(os.path.join(repo, "tools", "rc_runtime_manifest.txt")):
    line = raw.split("#", 1)[0].strip()
    if not line:
        continue
    src = line.split(" -> ")[0].strip()
    dest = line.split(" -> ")[1].strip() if " -> " in line else os.path.basename(src)
    os.makedirs(os.path.dirname(os.path.join(dst, dest)), exist_ok=True)
    shutil.copy2(os.path.join(repo, src), os.path.join(dst, dest))
EOF
git -C "$UNIT_REPO" rev-parse --short=12 HEAD > "$DST/software_sha.txt"
'''
FAKE_MIGRATE = '''#!/usr/bin/env python3
import os, sys
app = sys.argv[sys.argv.index("--app") + 1]
if "--write" in sys.argv:
    open(os.path.join(app, "camera_config.yaml"), "w").write("# migrated (test)\\n")
print("[MIGRATE] dry-run OK" if "--write" not in sys.argv else "wrote camera_config.yaml OK")
'''
OS_RELEASE = 'ID=debian\nVERSION_ID="13"\nVERSION_CODENAME={codename}\n'


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)
    if mode:
        os.chmod(path, mode)


def git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True, text=True,
                   env=dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                            GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t"))


class Unit:
    def __init__(self, case):
        self.root = tempfile.mkdtemp(prefix="install_")
        case.addCleanup(shutil.rmtree, self.root, True)
        self.fake = os.path.join(self.root, "fake")
        self.bin = os.path.join(self.fake, "bin")
        for name, text in (("dpkg", FAKE_DPKG), ("dpkg-query", FAKE_DPKG_QUERY),
                           ("apt-get", FAKE_APT), ("rpicam-still", "#!/bin/sh\nexit 0\n"),
                           ("pgrep", "#!/bin/sh\nexit 1\n")):
            write(os.path.join(self.bin, name), text, 0o755)
        write(os.path.join(self.fake, "cjxl.real"), FAKE_CJXL, 0o755)
        write(os.path.join(self.fake, "no_numpy", "numpy", "__init__.py"),
              "raise ImportError('numpy hidden by test_install_release')\n")
        write(os.path.join(self.fake, "installed.txt"),
              "".join(f"{p} {v}\n" for p, v in PKGS.items()
                      if p in ("python3", "python3-yaml", "python3-pil", "python3-serial",
                               "libjxl0.11")))
        write(os.path.join(self.fake, "os-release"), OS_RELEASE.format(codename="trixie"))
        write(os.path.join(self.fake, "field_update.sh"), FAKE_FIELD_UPDATE, 0o755)
        self._target_dir()
        self.manifest = os.path.join(self.root, "stage", "release_manifest.json")
        subprocess.run([sys.executable, MTOOL, "build", "--tag", TAG, "--profile", PROFILE,
                        "--target", self.tdir, "--out", self.manifest], check=True,
                       capture_output=True)
        shutil.copy(MTOOL, os.path.join(self.root, "stage", "release_manifest.py"))
        shutil.copy(INSTALL, os.path.join(self.root, "stage", "install_release.sh"))
        self.debs = os.path.join(self.tdir, "debs")
        self._repo()
        self.dst = os.path.join(self.root, "BM_Devel_Pi")
        os.makedirs(self.dst)
        shutil.copy(V1, os.path.join(self.dst, "camera_schedule.yaml"))
        write(os.path.join(self.dst, "software_sha.txt"), "0d03a62cf565\n")

    def _target_dir(self):
        self.tdir = os.path.join(self.root, "target_trixie_arm64")
        rows = []
        for pkg, ver in sorted(PKGS.items()):
            name = f"{pkg}_{ver.replace(':', '%3a')}_arm64.deb"
            write(os.path.join(self.tdir, "debs", name), f"fake deb {pkg} {ver}\n")
            rows.append(f"{pkg}\t{ver}\tarm64\t{name}\n")
        write(os.path.join(self.tdir, "debs_index.tsv"), "".join(rows))
        write(os.path.join(self.tdir, "os-release.txt"), OS_RELEASE.format(codename="trixie"))
        write(os.path.join(self.tdir, "arch.txt"), "arm64\n")
        write(os.path.join(self.tdir, "built_utc.txt"), "2026-10-27T04:00:00Z\n")

    def _repo(self):
        self.repo = os.path.join(self.root, "repo")
        os.makedirs(self.repo)
        git(self.repo, "init", "-q")
        write(os.path.join(self.repo, "README"), "old release (0d03a62 stand-in)\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "old")
        self.old_sha = subprocess.check_output(["git", "-C", self.repo, "rev-parse", "HEAD"],
                                               text=True).strip()
        tag_manifest = os.path.join(self.repo, "releases", TAG, "release_manifest.json")
        os.makedirs(os.path.dirname(tag_manifest))
        shutil.copy(self.manifest, tag_manifest)
        os.makedirs(os.path.dirname(os.path.join(self.repo, PROFILE)), exist_ok=True)
        shutil.copy(os.path.join(REPO, PROFILE), os.path.join(self.repo, PROFILE))
        write(os.path.join(self.repo, "tools", "config_migrate_v1_v2.py"), FAKE_MIGRATE, 0o755)
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "release")
        git(self.repo, "tag", "-a", TAG, "-m", TAG)
        self.tag_sha = subprocess.check_output(["git", "-C", self.repo, "rev-parse",
                                                f"{TAG}^{{commit}}"], text=True).strip()
        git(self.repo, "checkout", "-q", "--detach", self.old_sha)

    def run(self, *extra, numpy=True, **fake_env):
        env = dict(os.environ, BMCAM_PYTHON=sys.executable, BMCAM_SUDO="",
                   BMCAM_OS_RELEASE=os.path.join(self.fake, "os-release"),
                   BMCAM_FIELD_UPDATE=os.path.join(self.fake, "field_update.sh"),
                   FAKE=self.fake, REAL_REPO=REPO, UNIT_REPO=self.repo, DST=self.dst,
                   PATH=self.bin + ":" + os.path.dirname(sys.executable) + ":/usr/bin:/bin",
                   **fake_env)
        if not numpy:
            env["PYTHONPATH"] = os.path.join(self.fake, "no_numpy")
        stage = os.path.join(self.root, "stage")
        r = subprocess.run(["bash", os.path.join(stage, "install_release.sh"), "--tag", TAG,
                            "--manifest", self.manifest, "--debs", self.debs,
                            "--repo", self.repo, "--dst", self.dst,
                            "--log-dir", os.path.join(self.root, "logs"), *extra],
                           capture_output=True, text=True, env=env, timeout=600)
        r.out = r.stdout + r.stderr
        return r

    def has_cjxl(self):
        shutil.copy(os.path.join(self.fake, "cjxl.real"), os.path.join(self.bin, "cjxl"))
        with open(os.path.join(self.fake, "installed.txt"), "a") as fh:
            fh.write(f"libjxl-tools {PKGS['libjxl-tools']}\npython3-numpy {PKGS['python3-numpy']}\n")

    def apt_calls(self):
        path = os.path.join(self.fake, "apt.log")
        return open(path).read().splitlines() if os.path.exists(path) else []

    def software_sha(self):
        return open(os.path.join(self.dst, "software_sha.txt")).read().strip()


class Install(unittest.TestCase):
    def assert_untouched(self, u, r, code, *needles):
        self.assertEqual(r.returncode, code, r.out)
        for n in needles:
            self.assertIn(n, r.out)
        self.assertEqual(u.software_sha(), "0d03a62cf565", "the runtime changed")
        self.assertFalse(os.path.exists(os.path.join(u.dst, "camera_config.yaml")))
        self.assertFalse(os.path.exists(os.path.join(u.fake, "field_update.args")))
        head = subprocess.check_output(["git", "-C", u.repo, "rev-parse", "HEAD"], text=True)
        self.assertEqual(head.strip(), u.old_sha, "the checkout moved")

    def assert_pass(self, u, r):
        self.assertEqual(r.returncode, 0, r.out)
        last = [ln for ln in r.out.splitlines() if ln.startswith("[INSTALL] PASS")]
        self.assertEqual(len(last), 1, r.out)
        self.assertIn(f"sha={u.tag_sha[:12]} cfg=fe87abab runtime=supervisor target=trixie_arm64",
                      last[0])
        self.assertEqual(u.software_sha(), u.tag_sha[:12])

    def test_no_jxl_start_installs_only_whats_missing_offline_then_passes(self):
        u = Unit(self)
        r = u.run(numpy=False)
        self.assert_pass(u, r)
        calls = u.apt_calls()
        self.assertEqual(len(calls), 2, calls)                     # simulate, then install
        self.assertIn(" -s ", calls[0])
        for c in calls:
            self.assertIn("--no-download", c)
            self.assertIn("Dir::Etc::SourceList=/dev/null", c)
            self.assertNotIn("update", c.split())
        installed = [os.path.basename(a).split("_")[0] for a in calls[1].split() if a.endswith(".deb")]
        self.assertEqual(sorted(installed), ["libjxl-tools", "python3-numpy"])
        self.assertIn("python import numpy: MISSING", r.out)
        self.assertIn("cjxl self-test encode: OK", r.out)
        self.assertIn("--target-config " + os.path.join(u.repo, PROFILE),
                      open(os.path.join(u.fake, "field_update.args")).read())
        self.assertIn("--leave-disarmed", open(os.path.join(u.fake, "field_update.args")).read())
        self.assertTrue([f for f in os.listdir(u.dst)
                         if f.startswith(f"camera_config.yaml.before_{TAG}_")], os.listdir(u.dst))
        self.assertIn("unit left DISARMED", r.out)
        logs = os.listdir(os.path.join(u.root, "logs"))
        self.assertEqual(len(logs), 1)

        again = u.run()                                            # idempotent re-run
        self.assert_pass(u, again)
        self.assertEqual(len(u.apt_calls()), 2, "a re-run called apt-get")
        self.assertIn("nothing installed", again.out)
        self.assertIn("step 4 skipped", again.out)

    def test_everything_present_installs_nothing(self):
        u = Unit(self)
        u.has_cjxl()
        r = u.run()
        self.assert_pass(u, r)
        self.assertEqual(u.apt_calls(), [])

    def test_os_not_in_the_manifest_stops_before_any_change(self):
        u = Unit(self)
        write(os.path.join(u.fake, "os-release"), OS_RELEASE.format(codename="bookworm"))
        self.assert_untouched(u, u.run(), 3, "STOP step 1", "F13", "bookworm")
        self.assertEqual(u.apt_calls(), [])

    def test_wrong_arch_stops(self):
        u = Unit(self)
        self.assert_untouched(u, u.run(FAKE_ARCH="armhf"), 3, "arch=armhf")

    def test_deb_sha256_mismatch_fails_before_apt(self):
        u = Unit(self)
        with open(os.path.join(u.debs, sorted(os.listdir(u.debs))[0]), "a") as fh:
            fh.write("tampered\n")
        self.assert_untouched(u, u.run(), 1, "FAIL step 2", "F12", "sha256")
        self.assertEqual(u.apt_calls(), [])

    def test_apt_blocked_fails_runtime_untouched(self):
        u = Unit(self)
        self.assert_untouched(u, u.run(FAKE_APT_FAIL="1"), 1, "FAIL step 2",
                              "apt simulation failed")

    def test_a_removal_in_the_plan_fails(self):
        u = Unit(self)
        self.assert_untouched(u, u.run(FAKE_APT_REMV="1"), 1, "would REMOVE packages")
        self.assertEqual(len(u.apt_calls()), 1)                    # the simulation only

    def test_numpy_still_missing_after_install_fails_listing_it(self):
        u = Unit(self)
        r = u.run(numpy=False, FAKE_NUMPY_STAYS_BROKEN="1")
        self.assert_untouched(u, r, 1, "FAIL step 3", "[INSTALL][MISSING] python import numpy")

    def test_cjxl_below_minimum_is_a_miss(self):
        u = Unit(self)
        u.has_cjxl()
        r = u.run(FAKE_CJXL_V="0.7.0")
        self.assertNotEqual(r.returncode, 0, r.out)
        self.assertIn("cjxl 0.7.0: BELOW 0.11", r.out)
        self.assertEqual(u.software_sha(), "0d03a62cf565")

    def test_rpicam_missing_fails(self):
        u = Unit(self)
        u.has_cjxl()
        os.remove(os.path.join(u.bin, "rpicam-still"))
        self.assert_untouched(u, u.run(), 1, "FAIL step 3", "rpicam-still (rpicam-apps)")

    def test_dirty_tree_stops(self):
        u = Unit(self)
        u.has_cjxl()
        write(os.path.join(u.repo, "README"), "a real local edit\n")
        self.assert_untouched(u, u.run(), 3, "STOP step 3", "not power-cut damage")

    def test_zero_byte_tree_is_repaired_then_passes(self):
        u = Unit(self)
        u.has_cjxl()
        open(os.path.join(u.repo, "README"), "w").close()          # 0 bytes, tracked
        r = u.run()
        self.assertIn("0-byte tracked file", r.out)
        self.assert_pass(u, r)

    def test_staged_manifest_must_equal_the_tags(self):
        u = Unit(self)
        u.has_cjxl()
        with open(u.manifest) as fh:
            m = json.load(fh)
        m["spec"] = "edited after the tag"
        with open(u.manifest, "w") as fh:
            json.dump(m, fh, indent=1, sort_keys=True)
        self.assert_untouched(u, u.run(), 1, "FAIL step 4", "differs from the tag's copy")


class Manifest(unittest.TestCase):
    def test_committed_manifest_matches_the_repo(self):
        """The committed release manifest names this tree's runtime files and profile; a
        runtime change after it fails here until the manifest is rebuilt."""
        path = os.path.join(REPO, "releases", "bmcam-rel-20261027", "release_manifest.json")
        r = subprocess.run([sys.executable, MTOOL, "check-repo", "--manifest", path],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        with open(path) as fh:
            m = json.load(fh)
        self.assertEqual(m["profile"]["config_hash"], "fe87abab")
        self.assertEqual(m["profile"]["runtime"], "supervisor")
        self.assertEqual([t["id"] for t in m["targets"]], ["trixie_arm64"])
        t = m["targets"][0]
        self.assertEqual((t["os_id"], t["python"]), ("debian", "3.13"))
        for pkg in ("libjxl-tools", "python3-numpy", "python3-yaml", "python3-pil",
                    "python3-serial"):
            self.assertIn(pkg, t["packages"])
        self.assertTrue(t["packages"]["libjxl-tools"].startswith("0.11."))
        self.assertTrue(all(len(d["sha256"]) == 64 for d in t["debs"]))

    def test_cjxl_version_rule(self):
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import release_manifest as RM
        self.assertEqual(RM.cjxl_version_ok("cjxl v0.11.2 0.11.2 [NEON]"), (True, "0.11.2"))
        # trixie arm64's own line (debian:trixie container, libjxl-tools 0.11.2-0.1~deb13u3)
        self.assertEqual(RM.cjxl_version_ok("cjxl v0.11.2 [NEON,NEON_WITHOUT_AES]"), (True, "0.11.2"))
        self.assertEqual(RM.cjxl_version_ok("cjxl v0.7.0 0.7.0"), (False, "0.7.0"))
        self.assertEqual(RM.cjxl_version_ok("JPEG XL encoder v0.12.0"), (True, "0.12.0"))
        self.assertEqual(RM.cjxl_version_ok("garbage"), (False, None))


if __name__ == "__main__":
    unittest.main()

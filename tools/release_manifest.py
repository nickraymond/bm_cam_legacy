#!/usr/bin/env python3
# filename: release_manifest.py
# description: SPEC_e4 rev 13 M13-1 — build and check a bm release manifest (runtime hashes, offline .deb set, OS, profile hash).
"""
The release manifest pins what a release is, so tools/install_release.sh can check a
unit against it before and after it changes anything (SPEC_e4_noaa_release rev 13 M13-1,
rev 13.1 c, rev 13.3 N1-N2).

What it records (releases/<tag>/release_manifest.json, committed with the release):
  - the tag; the release sha is the commit the tag points to (a committed file cannot
    name its own commit), so the installer checks HEAD == `git rev-parse <tag>^{commit}`
    and that the checkout's copy of this file is byte-identical to the one it was given;
  - runtime_files: sha256 of every file tools/rc_runtime_manifest.txt installs, keyed by
    its name under /home/pi/BM_Devel_Pi (what deploy_rc_runtime.sh copies);
  - profile: the target camera_config.yaml, its sha256 and its config hash
    (config_v2.config_hash of the base: the `hash=` --print-config prints with no overlay);
  - targets: one per supported OS (ID, VERSION_CODENAME, dpkg architecture, Python
    major.minor). Each lists the offline .deb set (package, version, file, sha256) that
    install_release.sh installs with no network, and the checks: cjxl >= the minimum
    version from the OS's own suite (never one exact string, rev 13.3 N1), the Python
    imports the runtime needs, the tools it calls.
The .deb files themselves are outputs, never in git: they live in the data root
(~/NereusData/bm_cam_legacy/<date>_release_debs/<codename>_<arch>/debs/) and are copied to
the unit by the runbook's scp step.

Usage (repo root, Mac):
  python3 tools/release_manifest.py build --tag bmcam-rel-20261027 \
      --profile device_profiles/bmcam002/noaa_20261027/camera_config.yaml \
      --target ~/NereusData/bm_cam_legacy/2026-10-26_release_debs/trixie_arm64 \
      --out releases/bmcam-rel-20261027/release_manifest.json
  python3 tools/release_manifest.py check-repo --manifest releases/<tag>/release_manifest.json
On the unit (stdlib only: runs before PyYAML is installed):
  python3 release_manifest.py target   --manifest M --os-release /etc/os-release --arch arm64 --python 3.13
  python3 release_manifest.py debs     --manifest M --target-id trixie_arm64 --dir DEBS   (verify sha256s)
  python3 release_manifest.py runtime  --manifest M --dst /home/pi/BM_Devel_Pi
  python3 release_manifest.py get      --manifest M KEY[.KEY...]
Exit codes: 0 OK; 1 a check failed (each miss printed); 2 bad usage or manifest.

A target directory (built in a debian:<codename> container, see the PR) holds debs/,
debs_index.tsv (package, version, arch, file from dpkg-deb), os-release.txt, arch.txt,
built_utc.txt and sources.txt; build reads those, never guesses a version from a name.
"""

import argparse
import hashlib
import json
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "bmcam_release_manifest_v1"
# What the release runtime needs on the unit (tools/rc_runtime_manifest.txt files, read
# 10/26 at 56dd7ca3: third-party imports PIL, numpy, serial, yaml; tools cjxl for nrjxl and
# rpicam-still for the profile's camera.backend rpicam). numpy + cjxl: config_validate.NRJXL_DEPS.
PYTHON_IMPORTS = {"numpy": "python3-numpy", "yaml": "python3-yaml", "PIL": "python3-pil",
                  "serial": "python3-serial"}
TOOLS = {"cjxl": "libjxl-tools", "rpicam-still": None}     # None = not in the offline set
CJXL_MIN = "0.11"                                          # rev 13.3 N1


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def runtime_entries(repo):
    """(src_rel, dest_name) per tools/rc_runtime_manifest.txt line (deploy's own parse)."""
    out = []
    with open(os.path.join(repo, "tools", "rc_runtime_manifest.txt"), encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            src = line.split(" -> ")[0].strip()
            dest = line.split(" -> ")[1].strip() if " -> " in line else os.path.basename(src)
            out.append((src, dest))
    return out


def read_os_release(path):
    out = {}
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            if "=" in line:
                k, v = line.rstrip("\n").split("=", 1)
                out[k] = v.strip().strip('"')
    return out


def version_tuple(text):
    parts = []
    for p in str(text).split("."):
        digits = "".join(ch for ch in p if ch.isdigit())
        if not digits:
            break
        parts.append(int(digits))
    return tuple(parts)


def cjxl_version_ok(version_line, minimum=CJXL_MIN):
    """`cjxl v0.11.2 0.11.2 [NEON]` -> (ok, "0.11.2")."""
    for word in str(version_line).split():
        if word.startswith("v") and version_tuple(word[1:]):
            v = word[1:]
            return version_tuple(v) >= version_tuple(minimum), v
    return False, None


def profile_entry(profile_rel):
    sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))
    import config_v2                                   # PyYAML: Mac side only
    path = os.path.join(REPO, profile_rel)
    cfg = config_v2.load_config(path, strict=True)
    return {"file": profile_rel, "sha256": sha256(path),
            "config_hash": config_v2.config_hash(cfg.base),
            "still_format": cfg.base.get("still.format"),
            "runtime": cfg.base.get("commands.runtime")}


def target_entry(tdir):
    osr = read_os_release(os.path.join(tdir, "os-release.txt"))
    with open(os.path.join(tdir, "arch.txt")) as fh:
        arch = fh.read().strip()
    debs = []
    with open(os.path.join(tdir, "debs_index.tsv"), encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            pkg, ver, deb_arch, name = line.rstrip("\n").split("\t")
            debs.append({"package": pkg, "version": ver, "arch": deb_arch, "file": name,
                         "sha256": sha256(os.path.join(tdir, "debs", name))})
    by_pkg = {d["package"]: d for d in debs}
    python3 = by_pkg.get("python3")
    py_minor = ".".join(python3["version"].split(".")[:2]) if python3 else None
    missing = [p for p in list(PYTHON_IMPORTS.values()) + ["libjxl-tools"] if p not in by_pkg]
    if missing:
        raise SystemExit(f"[MANIFEST][ERROR] {tdir}: no .deb for {missing}")
    cjxl_ok, _ = cjxl_version_ok("v" + by_pkg["libjxl-tools"]["version"].split("-")[0])
    if not cjxl_ok:
        raise SystemExit(f"[MANIFEST][ERROR] {tdir}: libjxl-tools "
                         f"{by_pkg['libjxl-tools']['version']} is below cjxl {CJXL_MIN} "
                         "(rev 13.3 N1: that OS ends the update day, F13)")
    built = open(os.path.join(tdir, "built_utc.txt")).read().strip() \
        if os.path.exists(os.path.join(tdir, "built_utc.txt")) else None
    sources = open(os.path.join(tdir, "sources.txt")).read().strip() \
        if os.path.exists(os.path.join(tdir, "sources.txt")) else None
    return {"id": f"{osr.get('VERSION_CODENAME')}_{arch}", "os_id": osr.get("ID"),
            "version_codename": osr.get("VERSION_CODENAME"),
            "version_id": osr.get("VERSION_ID"), "arch": arch, "python": py_minor,
            "built_utc": built, "apt_sources": sources,
            "packages": {p: by_pkg[p]["version"] for p in
                         sorted(set(PYTHON_IMPORTS.values()) | {"libjxl-tools"})},
            "debs": sorted(debs, key=lambda d: d["file"])}


def cmd_build(a):
    manifest = {
        "schema": SCHEMA, "tag": a.tag,
        "sha": "the commit the tag points to: git rev-parse <tag>^{commit}",
        "spec": a.spec,
        "runtime_files": {dest: sha256(os.path.join(REPO, src))
                          for src, dest in runtime_entries(REPO)},
        "profile": profile_entry(a.profile),
        "checks": {"python_imports": PYTHON_IMPORTS, "tools": TOOLS, "cjxl_min": CJXL_MIN},
        "targets": [target_entry(os.path.expanduser(t)) for t in a.target],
    }
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"[MANIFEST] wrote {a.out}: {len(manifest['runtime_files'])} runtime files, "
          f"profile hash {manifest['profile']['config_hash']}, targets "
          + ", ".join(f"{t['id']} ({len(t['debs'])} debs)" for t in manifest["targets"]))
    return 0


def load(path):
    try:
        with open(path, encoding="utf-8") as fh:
            m = json.load(fh)
    except (OSError, ValueError) as exc:
        print(f"[MANIFEST][ERROR] {path}: {exc}", file=sys.stderr)
        raise SystemExit(2)
    if m.get("schema") != SCHEMA:
        print(f"[MANIFEST][ERROR] {path}: schema {m.get('schema')!r} != {SCHEMA}", file=sys.stderr)
        raise SystemExit(2)
    return m


def cmd_check_repo(a):
    m = load(a.manifest)
    repo = a.repo or REPO
    misses = []
    want = {dest: sha256(os.path.join(repo, src)) for src, dest in runtime_entries(repo)}
    if set(want) != set(m["runtime_files"]):
        misses.append(f"runtime file set differs: +{sorted(set(want) - set(m['runtime_files']))} "
                      f"-{sorted(set(m['runtime_files']) - set(want))}")
    misses += [f"{d}: repo {want[d][:12]} != manifest {m['runtime_files'][d][:12]}"
               for d in sorted(set(want) & set(m["runtime_files"]))
               if want[d] != m["runtime_files"][d]]
    prof = os.path.join(repo, m["profile"]["file"])
    if not os.path.exists(prof) or sha256(prof) != m["profile"]["sha256"]:
        misses.append(f"profile {m['profile']['file']}: sha256 differs from the manifest")
    for miss in misses:
        print(f"[MANIFEST][FAIL] {miss}")
    print(f"[MANIFEST] check-repo {'OK' if not misses else 'FAIL'} ({len(want)} runtime files)")
    return 1 if misses else 0


def cmd_target(a):
    """Step 1 (F13): the unit's OS / arch / Python -> the matching target id, else exit 1."""
    m = load(a.manifest)
    osr = read_os_release(a.os_release)
    have = (osr.get("ID"), osr.get("VERSION_CODENAME"), a.arch, a.python)
    for t in m["targets"]:
        if (t["os_id"], t["version_codename"], t["arch"], t["python"]) == have:
            print(t["id"])
            return 0
    print(f"[MANIFEST][FAIL] this unit is ID={have[0]} VERSION_CODENAME={have[1]} arch={have[2]} "
          f"python={have[3]}; the release supports only "
          + "; ".join(f"{t['os_id']} {t['version_codename']} {t['arch']} python {t['python']}"
                      for t in m["targets"]), file=sys.stderr)
    return 1


def _target(m, tid):
    for t in m["targets"]:
        if t["id"] == tid:
            return t
    print(f"[MANIFEST][ERROR] no target {tid!r}", file=sys.stderr)
    raise SystemExit(2)


def cmd_debs(a):
    """Every .deb of the target present with its sha256 (F12 per rev 13.3 N2). Prints
    `package<TAB>version<TAB>path` per deb on OK."""
    t = _target(load(a.manifest), a.target_id)
    misses, rows = [], []
    for d in t["debs"]:
        path = os.path.join(a.dir, d["file"])
        if not os.path.exists(path):
            misses.append(f"{d['file']}: missing")
        elif sha256(path) != d["sha256"]:
            misses.append(f"{d['file']}: sha256 {sha256(path)[:12]} != {d['sha256'][:12]}")
        else:
            rows.append(f"{d['package']}\t{d['version']}\t{path}")
    for miss in misses:
        print(f"[MANIFEST][FAIL] deb {miss}", file=sys.stderr)
    if misses:
        return 1
    print("\n".join(rows))
    return 0


def cmd_runtime(a):
    """Step 5: the deployed runtime files equal the manifest's."""
    m = load(a.manifest)
    misses = []
    for dest, want in sorted(m["runtime_files"].items()):
        path = os.path.join(a.dst, dest)
        if not os.path.exists(path):
            misses.append(f"{dest}: missing")
        elif sha256(path) != want:
            misses.append(f"{dest}: sha256 {sha256(path)[:12]} != {want[:12]}")
    for miss in misses:
        print(f"[MANIFEST][FAIL] runtime {miss}", file=sys.stderr)
    print(f"[MANIFEST] runtime {'OK' if not misses else 'FAIL'}: "
          f"{len(m['runtime_files']) - len(misses)}/{len(m['runtime_files'])} files match")
    return 1 if misses else 0


def cmd_get(a):
    value = load(a.manifest)
    for part in a.key.split("."):
        value = value[part] if isinstance(value, dict) else value[int(part)]
    print(json.dumps(value) if isinstance(value, (dict, list)) else value)
    return 0


def cmd_cjxl_ok(a):
    ok, v = cjxl_version_ok(a.version_line, load(a.manifest)["checks"]["cjxl_min"])
    print(v or "unknown")
    return 0 if ok else 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("build")
    b.add_argument("--tag", required=True)
    b.add_argument("--profile", required=True, help="repo-relative v2 camera_config.yaml")
    b.add_argument("--target", action="append", required=True, help="target dir (repeatable)")
    b.add_argument("--spec", default="SPEC_e4_noaa_release rev 13.8 (sha256 31edce5380c5)")
    b.add_argument("--out", required=True)
    c = sub.add_parser("check-repo")
    c.add_argument("--manifest", required=True)
    c.add_argument("--repo")
    t = sub.add_parser("target")
    t.add_argument("--manifest", required=True)
    t.add_argument("--os-release", default="/etc/os-release")
    t.add_argument("--arch", required=True)
    t.add_argument("--python", required=True)
    d = sub.add_parser("debs")
    d.add_argument("--manifest", required=True)
    d.add_argument("--target-id", required=True)
    d.add_argument("--dir", required=True)
    r = sub.add_parser("runtime")
    r.add_argument("--manifest", required=True)
    r.add_argument("--dst", required=True)
    g = sub.add_parser("get")
    g.add_argument("--manifest", required=True)
    g.add_argument("key")
    v = sub.add_parser("cjxl-ok")
    v.add_argument("--manifest", required=True)
    v.add_argument("version_line")
    a = p.parse_args(argv)
    return {"build": cmd_build, "check-repo": cmd_check_repo, "target": cmd_target,
            "debs": cmd_debs, "runtime": cmd_runtime, "get": cmd_get,
            "cjxl-ok": cmd_cjxl_ok}[a.cmd](a)


if __name__ == "__main__":
    raise SystemExit(main())

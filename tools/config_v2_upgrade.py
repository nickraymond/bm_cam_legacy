#!/usr/bin/env python3
# filename: config_v2_upgrade.py
# description: Sprint26 S4 — rewrite a v2 camera_config.yaml in today's registry spelling (old key names, new keys) without changing any value.
"""
Rewrite camera_config.yaml (config v2) with today's registry: old key names
(registry ALIASES, e.g. video.storage.* -> storage.*) become the new ones and
keys a newer registry added are spelled out with their defaults. Every VALUE
stays the same, so the config hash stays the same (checked before writing).

Why a separate tool (PLAN_S4.md G11, review S4a #5): re-migrating from the v1
file would drop v2-only settings (mode.run stay_on, mode.output save_local,
commands.runtime); this keeps them. The loader already accepts old names with
one warning per boot, so running this is housekeeping, not a fix.

Inputs:  path to camera_config.yaml (default /home/pi/BM_Devel_Pi/camera_config.yaml)
Outputs: dry-run (default): what would change, on stdout; nothing written.
         --write: the old file kept as <name>.before_upgrade_<UTC>, the new one
         written atomically, one `deploy` line in config_journal.jsonl.
Exit:    0 OK (or nothing to do) · 2 the file does not load strictly · 3 the
         rewrite would change the hash (refused; nothing written).

Example (on the unit, cron disarmed):
  python3 ~/repos/bm_cam_legacy/tools/config_v2_upgrade.py /home/pi/BM_Devel_Pi/camera_config.yaml --write

Known limitations: hand-written comments in the file are replaced by the
registry's help comments (migrated files carry only those anyway).
"""

import argparse
import datetime as dt
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import config_migrate  # noqa: E402
import config_registry as R  # noqa: E402
import config_v2 as C  # noqa: E402


def upgraded_text(path):
    """-> (config, new_text). Raises config_v2.ConfigError when not strict-clean."""
    cfg = C.load_config(path, strict=True)
    text = config_migrate.render_config_text(cfg.base, header_lines=(
        f"camera_config.yaml — config v2 (schema {R.SCHEMA_VERSION}, registry "
        f"v{R.REGISTRY_VERSION}). Rewritten by tools/config_v2_upgrade.py; values unchanged",
        "Every setting is spelled out. Remote changes live in the command state "
        "overlay, never here;",
        "this file changes only by deploy or migrate (DESIGN_supervisor.md §5).",
    ))
    return cfg, text


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("path", nargs="?", default="/home/pi/BM_Devel_Pi/camera_config.yaml")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args(argv)
    try:
        cfg, text = upgraded_text(args.path)
    except C.ConfigError as exc:
        print(f"[UPGRADE][ERROR] {args.path} does not load strictly: {exc}")
        return 2
    with open(args.path, encoding="utf-8") as fh:
        old_text = fh.read()
    print(f"[UPGRADE] {args.path}: registry v{R.REGISTRY_VERSION}, hash {cfg.hash}")
    for warning in cfg.warnings:
        print(f"[UPGRADE] {warning}")
    if old_text == text:
        print("[UPGRADE] already in today's spelling; nothing to do")
        return 0
    tmp_check = args.path + ".upgrade_check"
    with open(tmp_check, "w", encoding="utf-8") as fh:
        fh.write(text)
    try:
        after = C.load_config(tmp_check, strict=True)
    finally:
        os.remove(tmp_check)
    if after.hash != cfg.hash or after.base != cfg.base:
        print(f"[UPGRADE][ERROR] the rewrite would change the config ({cfg.hash} -> "
              f"{after.hash}); refused, nothing written")
        return 3
    if not args.write:
        print("[UPGRADE] dry-run: would rewrite (same values, same hash); pass --write")
        return 0
    stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = f"{args.path}.before_upgrade_{stamp}"
    shutil.copy2(args.path, backup)
    import atomic_io
    atomic_io.write_text(args.path, text)
    try:
        import config_journal
        config_journal.append(config_journal.path_beside(cfg.base["commands.state_path"]),
                              "deploy", key="camera_config.yaml", old=None,
                              new=f"registry v{R.REGISTRY_VERSION} spelling", h=cfg.hash,
                              note="tools/config_v2_upgrade.py")
    except Exception as exc:
        print(f"[UPGRADE][WARN] journal line not written: {exc}")
    print(f"[UPGRADE] rewritten (hash {cfg.hash} unchanged); old file kept as {backup}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

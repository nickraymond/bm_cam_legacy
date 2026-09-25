#!/usr/bin/env python3
# filename: config_migrate_v1_v2.py
# description: Sprint26 S2c — migrate a unit's v1 config + command state to config v2 (dry-run by default).
"""
Migrate camera_schedule.yaml (v1) + bm_command_state.json (v1) to
camera_config.yaml + bm_command_state_v2.json (DESIGN_supervisor.md §5,
PLAN_S2.md). The v1 files are only READ; they stay in place for rollback.

Inputs:  --config  v1 YAML          (default /home/pi/BM_Devel_Pi/camera_schedule.yaml)
         --state   v1 state JSON    (default: the path the unit's own v1 loader resolves)
         --app     runtime dir whose v1 loaders are used (default: the repo's BM_Devel_Pi)
Outputs: dry-run (default): the diff report on stdout, nothing written.
         --write: <out-dir>/camera_config.yaml, <out-dir>/bm_command_state_v2.json
         (commands on only) and <out-dir>/config_migration_report.md, each written
         atomically. Refuses to replace an existing v2 file unless --force (the
         old one is then kept as <name>.before_migrate_<UTC>).
Exit:    0 OK · 3 refused (a human must decide; nothing written) · 2 usage / IO error.

Stops for a human (REVIEW R1/R2/X3): missing or heic capture_mode; network_type
0x01 because the key was absent or PyYAML is missing; media_gid enabled;
unknown v1 keys; ambiguous values; v1 state not tables v8.

Examples (on the unit):
  python3 ~/repos/bm_cam_legacy/tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi
  python3 ~/repos/bm_cam_legacy/tools/config_migrate_v1_v2.py --app /home/pi/BM_Devel_Pi --write
Known limitations: run where the v1 runtime runs (PyYAML installed).
"""

import argparse
import datetime as dt
import os
import shutil
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
V2_CONFIG = "camera_config.yaml"
V2_STATE = "bm_command_state_v2.json"
REPORT = "config_migration_report.md"


def main(argv=None):
    ap = argparse.ArgumentParser(description="config v1 -> v2 migration (dry-run by default)")
    ap.add_argument("--config", default="/home/pi/BM_Devel_Pi/camera_schedule.yaml")
    ap.add_argument("--state", default=None)
    ap.add_argument("--app", default=os.path.join(REPO, "BM_Devel_Pi"))
    ap.add_argument("--out-dir", default=None, help="default: the v1 YAML's directory")
    ap.add_argument("--write", action="store_true", help="write the v2 files (else dry-run)")
    ap.add_argument("--force", action="store_true",
                    help="replace existing v2 files (kept as *.before_migrate_<UTC>)")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.abspath(args.app))
    import atomic_io
    import config_migrate

    if not os.path.exists(args.config):
        print(f"[MIGRATE][ERROR] v1 YAML not found: {args.config}", file=sys.stderr)
        return 2
    out_dir = args.out_dir or os.path.dirname(os.path.abspath(args.config))
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    print(f"[MIGRATE] app={os.path.abspath(args.app)} config={args.config} "
          f"state={args.state or '(v1 loader default)'} out_dir={out_dir} "
          f"mode={'WRITE' if args.write else 'dry-run'}")

    m = config_migrate.migrate(args.config, args.state,
                               generated_by=f"tools/config_migrate_v1_v2.py at {ts}")
    report = config_migrate.report_text(m)
    print(report)
    if m.problems:
        print(f"[MIGRATE] REFUSED: {len(m.problems)} problem(s); nothing written", file=sys.stderr)
        return 3
    if not args.write:
        print("[MIGRATE] dry-run OK: re-run with --write to create the v2 files")
        return 0

    targets = [(os.path.join(out_dir, V2_CONFIG), m.config_text)]
    if m.state is not None:
        targets.append((os.path.join(out_dir, V2_STATE), None))
    existing = [p for p, _ in targets if os.path.exists(p)]
    if existing and not args.force:
        print(f"[MIGRATE][ERROR] refusing to replace {existing} (use --force)", file=sys.stderr)
        return 2
    try:
        for path in existing:
            keep = f"{path}.before_migrate_{ts}"
            shutil.copy2(path, keep)
            print(f"[MIGRATE] kept {path} -> {keep}")
        atomic_io.write_text(os.path.join(out_dir, V2_CONFIG), m.config_text)
        print(f"[MIGRATE] wrote {os.path.join(out_dir, V2_CONFIG)}")
        if m.state is not None:
            atomic_io.write_json(os.path.join(out_dir, V2_STATE), m.state)
            print(f"[MIGRATE] wrote {os.path.join(out_dir, V2_STATE)}")
        atomic_io.write_text(os.path.join(out_dir, REPORT), report)
        print(f"[MIGRATE] wrote {os.path.join(out_dir, REPORT)}")
    except OSError as exc:
        print(f"[MIGRATE][ERROR] write failed: {exc}", file=sys.stderr)
        return 2
    print("[MIGRATE] OK. v1 files untouched: rollback = redeploy the old SHA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

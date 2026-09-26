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
         (commands on and a v1 state to carry) and <out-dir>/config_migration_report.md,
         each written atomically, plus one `migrate` line in config_journal.jsonl.
         Refuses to replace an existing v2 file unless --force (the old one is then
         kept as <name>.before_migrate_<UTC>), and refuses an --out-dir that is not
         where the migrated commands.state_path points.
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
                    help="replace an existing camera_config.yaml (kept as *.before_migrate_<UTC>)")
    ap.add_argument("--reset-state", action="store_true",
                    help="with --force: ALSO rebuild bm_command_state_v2.json from the v1 "
                         "state (reverts every command applied since the first migration)")
    args = ap.parse_args(argv)

    sys.path.insert(0, os.path.abspath(args.app))
    import atomic_io
    import config_journal
    import config_migrate
    import config_v2

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

    # One truth for where the v2 state lives: the migrated config's own
    # commands.state_path. It must be the file this tool writes.
    state_target = os.path.join(out_dir, V2_STATE)
    if os.path.normpath(m.values["commands.state_path"]) != os.path.normpath(
            os.path.abspath(state_target)):
        print(f"[MIGRATE][ERROR] the migrated config says commands.state_path="
              f"{m.values['commands.state_path']}, but this run would write the state to "
              f"{state_target}; run with --out-dir {os.path.dirname(m.values['commands.state_path'])}",
              file=sys.stderr)
        return 2
    # A re-migration (--force) keeps the unit's live v2 command state: the v1
    # state file has been frozen since the first migration, so rebuilding from
    # it would revert every command since (and forget their ids, so replays
    # would apply again). --reset-state is the explicit opt-in.
    write_state = m.state is not None
    if write_state and os.path.exists(state_target) and not args.reset_state:
        write_state = False
        print(f"[MIGRATE] keeping the existing {state_target} (live command state; "
              "--reset-state to rebuild it from the v1 state)")
    targets = [(os.path.join(out_dir, V2_CONFIG), m.config_text)]
    if write_state:
        targets.append((state_target, None))
    existing = [p for p, _ in targets if os.path.exists(p)]
    if existing and not args.force:
        print(f"[MIGRATE][ERROR] refusing to replace {existing} (use --force)", file=sys.stderr)
        return 2
    try:
        for path in existing:
            keep = f"{path}.before_migrate_{ts}"
            shutil.copy2(path, keep)
            print(f"[MIGRATE] kept {path} -> {keep}")
        # State FIRST, config LAST: camera_config.yaml's presence is what
        # switches the unit to v2, so it must never exist without its state.
        if write_state:
            atomic_io.write_json(state_target, m.state)
            print(f"[MIGRATE] wrote {state_target}")
        atomic_io.write_text(os.path.join(out_dir, V2_CONFIG), m.config_text)
        print(f"[MIGRATE] wrote {os.path.join(out_dir, V2_CONFIG)}")
        atomic_io.write_text(os.path.join(out_dir, REPORT), report)
        print(f"[MIGRATE] wrote {os.path.join(out_dir, REPORT)}")
        journal = config_journal.path_beside(m.values["commands.state_path"])
        effective = dict(m.values)
        effective.update(config_migrate.overlay_from_v8(m.state["v8"]) if m.state else {})
        config_journal.append(journal, "migrate", key="*", new="config v2",
                              h=config_v2.config_hash(effective),
                              note=f"from {os.path.abspath(args.config)} sha256 "
                                   f"{m.report['source']['yaml_sha256'][:16]}")
        print(f"[MIGRATE] journaled in {journal} (hash {config_v2.config_hash(effective)})")
    except OSError as exc:
        print(f"[MIGRATE][ERROR] write failed: {exc}", file=sys.stderr)
        return 2
    print("[MIGRATE] OK. v1 files untouched: rollback = redeploy the old SHA.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

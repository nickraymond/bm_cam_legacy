# Kickoff — self-healing media, continue at S5 (for a NEW session)

Written 2026-09-24 ~00:50Z by the "Self-healing media S0 kickoff" session (context full). Owner and
approval gate: Nick. Everything below was measured, read from code, or decided by Nick unless marked
ASSUMPTION. One stage at a time; report each gate as What happened / What I learned / What's next.

## Required reading, in order
1. `CLAUDE.md`. Branching: never commit to `main`/`development`; PRs to `development`.
   Backend repo `nereus-vision-dev`: branch from `origin/staging`, PR into `staging`; staging IS BM
   production; Render runs `alembic upgrade head` on every deploy.
2. `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` v3: §0c holds every stage record and decision
   made since approval (S0–S2b, the 0014 incident). Then `RESEND_DEVICE.md` §4–§5 (rsd, pending list,
   heal slot, `<HL>`).
3. `docs/bm_media_wire_contract.md` §14 (rev 5, signed).
4. `runs/s3_bench_20260923T2325Z/RESULTS.md` and `run_manifest.json` (hardware state + restore lines).

## Where the stages stand

| Stage | What | State |
|---|---|---|
| S0 | contract rev 5, backend M0 | live (nvd #49, bm #61) |
| S1 | backend chunk map + keyed identity, migration 0012 | live (nvd #50) |
| S2a | keyed grouping `BM_KEYED_GROUPING` (default off), capture-time fix, seal gap 600 s | live (nvd #52) |
| S2b | heal endpoints, `heal_commands`, `<HL>` ingest, `media.fps`, migration 0014 | live (nvd #56 + fix #57) |
| S3 | daemon in video_tx wakes, `[BOOT]` marks | merged (bm #66); bench: video subscribes at ~21.3 s |
| S4 | keyed chunks behind `media_key` island, sent record + prune | merged (bm #68; #67 landed on the S3 branch by mistake) |
| **S5** | **rsd + pending list + heal send + `<HL>`** | **WIP: branch `feature/s5-rsd-heal`, 1 commit (tables v8)** |
| ladder 2 | live heal on bmcam004 (cellular) | after S5 |

## Nick's actions still open (backend) — needed before the live heal
- Set `BM_KEYED_GROUPING=on` in the Render env group used by the web service AND the
  `nereus-sofar-ingest-staging` cron. Without it, bare heal chunks (no START) are dropped. Legacy output is
  proven identical with it on (replay, 50,105 real rows). Rollback: unset.
- fps backfill (only 6/270 video rows have fps on 2026-09-24): from the `nereus-vision-dev` root,
  `DATABASE_URL=<staging, postgresql+psycopg://> backend/.venv/bin/python -m backend.app.scripts.backfill_media_fps --apply`
  (dry run 2026-09-23: 264/264, all 10.0, 2.3 s, by id, row locks only).
- captured_at backfill (S2a): `backend.app.scripts.backfill_captured_at_from_filename` — dry run first,
  then `psql -f` the SQL it writes (263 rows; the reference clip media 52691 is excluded by design).

## S5 — what to build (Nick said go 2026-09-24, both decisions below approved)
Done: `command_tables` v8 (`rsd` in COMMANDS, `HEAL_COMMANDS`, doc row, help fits 72 cols).
Remaining, in this order:
1. `command_messages.parse_rsd` (called from `parse_command` when `c == "rsd"`): either `"x":1` (cancel)
   or `"h"`: 1..8 items `[key, ranges]`; key `^[0-9a-z]{6}$`; ranges `^\d+(-\d+)?(,\d+(-\d+)?)*$`,
   expand -> reject reversed/duplicate; total <= 40 chunks per command. Error code `val`.
2. `CommandState.pending_heals` (persisted, tolerant load): items `{key, n:[...], id, wakes_left:3}`,
   dedupe by key (newest id wins), <= 8, newest first. `record()` for rsd applies accepted heals or the
   cancel and ALWAYS records the id (a re-sent command must be acked-duplicate, not re-refused).
3. Daemon: rsd dispatch with an injected `heal_validate_fn(key, ns) -> (ok, reason)` (sent record exists
   via `rc_media_key.find_sent_record`, payload file exists, every n < msgs). Wire the default in
   `default_daemon_factory` from `settings["media_key_cfg"]["sent_dir"]`. Ack ok=1 if >= 1 heal accepted
   (or cancel), else ok=0 `e="rsd"`. Keep per-wake heal events (`requested`/`refused`) for `<HL>`.
4. `rc_heal.py`: before START in BOTH cycles (after `prepare_keyed_send`, when a daemon exists and
   `pending_heals` is non-empty) re-send `<I{key}.{n}>` from the sent record using ITS `chunk_b64_chars`,
   paced at the unit's delay (1.3 s on video units), pump-only, <= 40 chunks per wake, and never eat
   the room the new capture needs (reserve its message count + envelope + keyframe repeat). Verify the
   payload sha256 against the record (mismatch -> refused `payload_changed`). Decrement `wakes_left` only
   for heals that existed at wake start; 0 -> `dropped`. MVP simplification (state it in the PR): no
   post-END heals (the spec's +240 s rule); leftovers wait for the next wake.
5. `<HL v=1 key=<key> a=<requested|sent|refused|dropped> n=<n> r=<token> id=<cmd id> w=<wake key>>` —
   after END, one per key per wake (priority sent > dropped > refused > requested), via the cycle's tx,
   paced; omit `w=` when the wake has no key. The backend parser already accepts this (nvd #56,
   `heal_status_ingest.parse_heal_status`).
6. Bench-only flag (Nick approved): `--bench-drop-chunks 63` (video_tx only) — skip tx of those indices
   but keep pacing and count them as sent. Off unless passed on the CLI.
7. Tests: ladder 1 (validation, byte-identical re-send vs the sent record, ordering stop->close->halt,
   island off = unchanged wire). Existing harnesses: `tests/test_s3_video_tx_daemon.py` (real
   CommandDaemon on FakeUart, ordered wire log), `tests/test_s4_media_key.py`.

## Ladder 2 — the live heal (Nick approved; ~2–3 clips of cellular)
bmcam004 (on SPOT-31593C, node `0xe6fe83ea6b4a2b7f`): deploy S5 with `tools/rc_field_update.sh --ref
feature/s5-rsd-heal --profile bmcam003 --leave-disarmed` (bare branch name, NOT `origin/...`; the profile
is the only video profile; it sets 1.3 s), then `patch_camera_schedule.py --ensure media_key.enabled=true`.
Run one cycle with `--bench-drop-chunks 63` -> backend shows a keyed partial -> `POST
/admin/ingest/devices/BMCAM_004/heal-commands` -> Nick runs `send_with` -> next wake: ack, chunk 63
re-sent before START, `<HL a=sent>` after END -> backend completes the SAME row byte-exact, partial
objects deleted. Commands reach the unit only if the Spotter's report minute falls inside the listening
span (spec §6): re-send until acked.

## Hardware state RIGHT NOW (this session owned it; Nick: "you can now own the hardware")
| Item | State | Restore |
|---|---|---|
| SPOT-33507C (bridge `c3c564b91856226c`) | bus ALWAYS ON (`bridgePowerControllerEnabled 0`) | `bridge cfg set c3c564b91856226c s u bridgePowerControllerEnabled 1` + `bridge cfg commit c3c564b91856226c s` |
| SPOT-31593C (bridge `0e582dd12c1e1480`) | bus ALWAYS ON | same with `0e582dd12c1e1480` |
| bmcam003 (192.168.1.230) | awake, DISARMED (`#S3BENCH @reboot`), Sprint24 code, 1.3 s | `crontab ~/backups/crontab.before_s3_bench_20260923T232747Z` |
| bmcam004 (192.168.1.143) | awake, DISARMED, **S3 code** (1ff1ba7 -> 3acc55e, runtime tar in `~/backups`), video, 1.3 s, `bm_commands` on | `crontab ~/backups/crontab.before_s3_bench_20260923T232748Z`; YAML: `camera_schedule.yaml.bak_20260923T234018Z` |
| Spotter consoles | monitor PID 26536 (Sprint24 session) owns both USB ports; send via `~/spotter_logs/<SPOT>/cmd.txt`, never open a port twice | — |

Gotchas learned here: never `pgrep -f`/`pkill -f` a pattern that appears in your own ssh command line
(it kills your remote shell — use `pgrep -x` or an anchored `^/usr/bin/python3 -u rc_progressive_jpeg.py`);
a cold boot on a bus-always-on Spotter = let the Pi self-halt, THEN `bridge cfg commit`; a migration on
`media` must be DDL only (the 0014 incident, SPEC §0c).

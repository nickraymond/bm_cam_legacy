# Sprint25 kickoff — when to transmit over the BM bus, and a backend that heals itself

Written 2026-09-22 by the Sprint24 session for a NEW session. Everything below was
measured or read from code by that session unless marked ASSUMPTION. Owner and approval
gate: Nick. Give him your plan before any work and wait for his go.

Two parts, in order:

1. **Transmit timing (do first, test indoors today):** from the two Spotter SD cards that
   ran overnight indoors, determine the optimal time to send the camera's messages over the
   BM bus relative to the Spotter's own traffic; settle the `alignmentInterval5Min`
   question (Nick: keep it ALIGNED if that makes transmissions more robust); spin up an
   indoor test that changes one variable and proves it.
2. **Self-healing backend (design, stub, discuss — no device code before Nick's sign-off):**
   a backend tool that identifies which messages of a recent transmission were missed,
   issues a command through the Sofar API, and has the camera RESEND those messages.

A live-fire test is already running: Nick swapped the two cameras onto each other's
Spotters this morning, on purpose, to exercise the node-identity feature shipped last night.
Read "Backend state" — it is not yet ingesting the swapped cameras, and it found a gap.

## REQUIRED READING, in order

1. `CLAUDE.md` (manifesto; branching — never commit to `main`/`development`).
2. `sprints/Sprint24_outdoor_hil_baseline/PLAN.md` (banner first) and the Sprint24 session's
   findings summarised below; `sprints/Sprint23_remote_msg_latency/RESULTS.md` findings 6,
   8, 9, 10, 11 (command delivery only ~72 s after the hourly `MS_Q_LEGACY` report; held
   commands lost; 2-slot cellular queue); `sprints/Sprint22_video_over_spotter/TRACKER.md`.
3. Skill `nereus-spotter-sd-analysis` §10 (the SD `log/` files, the duplicate-`log/` trap,
   the phase recipe), `spotter-usb-console-capture`, `spotter-health-check`,
   `spotter-usb-power-cycle`.
4. `docs/bm_media_wire_contract.md` (rev 3, FROZEN — a resend envelope is rev 4 and needs
   Nick's explicit approval), `docs/sofar_command_api_reference.md`,
   `docs/bmcam_command_reference.md`, `BM_Devel_Pi/command_tables.py` (TABLES_VERSION 7),
   `BM_Devel_Pi/rc_video_tx.py` (note `WORK_DIR = "/tmp/rc_video_tx"`: the fitted payload
   that was SENT is not persisted — a resend needs it byte-identical).
5. Backend: `nereus-vision-dev/backend/docs/SPEC_node_identity.md` (§2, §4, §5, §7, §10, §11)
   — merged as PR #43, live on staging since 2026-09-22 05:41Z.
6. Tools: `tools/bm_video_soak_report.py` (per-cycle join; its Spotter-side columns
   `report_utc`, `rx_check_utc`, `queue_full_count_in_cycle` sourced from SD `log/` are
   Sprint24 step 7 and are NOT built yet — build them), `tools/sofar_send_command.py`,
   `tools/sofar_poll_acks.py`, `tools/spotter_serial_monitor.py` (`--only` always),
   `tools/remote_msg_tester/`.

## HARDWARE IN SCOPE — nothing else

| Rig | Spotter | Bridge node | Notecard | Camera (BM node) | Backend device |
|---|---|---|---|---|---|
| A | SPOT-33507C | `c3c564b91856226c` | NOTE-WBGLW fw 6.2.5 | see "swap" | `BMCAM_004` / `BMCAM_003` |
| B | SPOT-31593C | `0e582dd12c1e1480` | NOTE-WBNA-500 fw 4.2.1 | see "swap" | |

Cameras: **bmcam003** = node `0x53171fa3d81a8e6f` = `BMCAM_003`; **bmcam004** = node
`0xe6fe83ea6b4a2b7f` = `BMCAM_004`. **Swap (Nick, morning of 2026-09-22): the cameras were
moved onto each other's ebox** — verify which node is on which Spotter FIRST (registry
`external_nodes.last_external_system_id` via `GET /admin/ingest/external-nodes`, or the
Sofar rows) and do not assume the direction.

SPOT-33507C resets itself ~60 min after every boot (`rebootctl reset 2. Source: 7` on its
first hourly health check; seen 19:01:11Z after an 18:01Z boot; known since July, still on
v2.16.8). Every Spotter reset cuts bus power and moves the hourly report minute. Its red LED
is `baroErrorState INIT_ERR`, harmless.

## STATE YOU INHERIT (verify, do not assume)

Cameras (both identical, verified byte-for-byte 2026-09-21):
- Build `development` `1ff1ba7` + `rc_video_tx.py`/`rc_video_clip.py` copied by hand (the
  deploy manifest lacked them — fix in `bm_cam_legacy` PR #59, OPEN; until merged,
  `deploy_rc_runtime.sh` still misses them). `software_sha.txt` = `1ff1ba719011`.
- YAML: `capture_mode: video`, `video_tx.enabled: true` (5 s clip + 2 s lead-in, 480x270 @
  10 fps, `message_cap 126`, `keyframe_repeat_max 30`, preset medium), `transmit_phase.enabled:
  false`, `power_halt.enabled: true / dry_run: false` (real self-halt PROVEN on both:
  `halt=halt_initiated`, bus current → 0 A), `NO_HALT` REMOVED, transmit window ALL DAY
  (`00:00`–`00:00`, gate ON so Spotter UTC is read every boot — with the gate off the Pi clock
  is never set and outdoor filenames would repeat), `allow_system_clock_fallback: true`,
  `progressive_jpeg.max_run_time_min: 8`. `bm_command_state.json` moved aside on both (the
  `hlt=3`/`twn=2` overlay is gone). No `bm_commands` island → **no command daemon runs in
  `video_tx` mode** (Sprint24 dropped D2). Backups on each Pi: `camera_schedule.yaml.before_s24_*`,
  `camera_schedule.yaml.s24v1_*`; crontab in `~/backups/`.
- Cycle shape: bus-on → Pi runtime at ~18–19 s uptime → Spotter UTC → record 7 s → fit
  (2-pass, ~9 s) → burst 122–125 chunks + 30 keyframe repeat at 1 msg/s (~157 s UART) →
  halt. Whole cycle ~195 s of the 600 s window.

Ebox schedule on BOTH bridges (committed 20:06:57Z, read back): `sampleIntervalMs 960000`,
`sampleDurationMs 600000`, `bridgePowerControllerEnabled 1`, `alignmentInterval5Min 1`.
**Measured effect of the alignment flag:** the bridge starts a window only on the next
5-minute wall-clock boundary at/after the interval elapses (20:08:57 off → `power off for
671000` → on 20:20:00.3), so the 16-minute schedule runs as a **20-minute period on :00/:20/:40**;
captures land at :03/:23/:43. Every burst therefore sits at the same phase, ~+75 s to ~+235 s
after a boundary — a free lane guard; only the hourly report minute can hit it. Original
bridge values, for restore: SPOT-33507C `1800000 / 900000 / controller 0`; SPOT-31593C
`3600000 / 900000 / controller 0`. Any `bridge cfg commit` forces the bus ON 120 s and triggers
a report + mailbox check; never mid-run.

Spotters: SD cards were formatted FROM the Spotters before the run and **pulled this morning**
(SPOT-33507C had index sets `0000_*` and `0001_*` because of its 19:01Z self-reset; SPOT-31593C
`0000_*`). Both Spotters now run WITHOUT cards (`ERR SD card is not mounted`, `sd err`
climbing — expected). Re-inserting a card RESETS the Spotter ("SD card insertion reset") →
report minute moves. `werr` also climbs a little during every burst (17→47 on B in one
evening) and one `MS.log` line was truncated mid-line: parse the SD logs by pattern, never by
line start. USB consoles have been unplugged since 2026-09-21 23:53Z; the Mac monitor
(`spotter_serial_monitor.py --only SPOT-33507C --only SPOT-31593C`, PID 26536 under
`caffeinate`) may still be alive and WILL re-grab the ports when they reappear — check
`pgrep -fl spotter_serial_monitor` and reuse or kill it before starting another; never open a
port twice. Logs: `~/spotter_logs/<SPOT-ID>/console_YYYYMMDD.log`. No monitoring Pi; the SD
cards are the record; consoles are the live view during an indoor test.

Overnight result to beat (bmcam003 via SPOT-31593C, 24 h to 04:53Z, from the recovery poll):
39 complete captures, 28 partial, 59 wake statuses; the video cycles from 22:23Z on were
mostly 100 %, with 23:23Z and 00:23Z at 99 % (one chunk each). Separate the pre-18:49Z stills
cycles from the video cycles before drawing conclusions.

## Backend state (nereus-vision-dev; staging IS production for BM — `main` has no BM stack)

- PR #43 merged; migration `20260922_0010` live; `GET /admin/ingest/gateways` → SPOT-31593C
  and SPOT-33507C on `SOFAR_API_TOKEN_BM_REEF`, SPOT-33361C (AOML customer) on
  `SOFAR_API_TOKEN_AOML`, all active, first cron tick 05:44Z clean. `DeviceOut` now carries
  `name` and `node_id`. Flag **`BM_AUTO_PROVISION` is OFF** (shadow mode) on the cron job
  `nereus-sofar-ingest-staging` (Render env var; Nick changes Render, not you).
- Binding rows (`external_data_sources`) now: SPOT-31593C → BMCAM_000 (active), BMCAM_001/002
  (paused legacy), BMCAM_003 (active, created 04:53Z); SPOT-33507C → BMCAM_003 (**PAUSED** —
  the interim move's leftover), BMCAM_004 (active); SPOT-33361C → BMCAM_001/002 (active).
  `devices.system_id`: BMCAM_003 = SPOT-31593C, BMCAM_004 = SPOT-33507C.
- **Consequence of the swap with the flag off — both swapped cameras are being DROPPED by
  the backend right now:** bmcam004's node on SPOT-31593C has no binding there → `would_move`
  shadow line + `skipped_unmapped`; bmcam003's node on SPOT-33507C resolves to its PAUSED
  binding → `skipped_paused`. Nothing is lost at Sofar, but the cron's 6-hour lookback means a
  manual `sofar-poll-once … hours=24&auto_provision=true` is needed to recover once fixed.
- **Gap found while writing this (fix it first, small PR):** for a node with two binding
  rows (BMCAM_001, 002, 003), the resolver's tier-1 pick is "the row whose Spotter is the one
  being polled", so `detect_move` compares the registry against a binding that always matches
  the polled gateway → `devices.system_id` never updates even with the flag ON, and a paused
  row on the new Spotter blocks ingest. Intended semantics (spec §4 W3): the device's current
  gateway follows the registry's advance-only `last_external_system_id`; the resolver should
  prefer pollable rows; migration 0011 (Gate 3) removes the duplicates for good. Also
  un-pause the (SPOT-33507C, `0x53171fa3d81a8e6f`) row — that pause was an interim artefact,
  not admin intent (SQL; the upsert endpoint 409s while `devices.system_id` differs).
- Gate 2 = observe one cron tick's shadow lines (they prove detection), then Nick sets
  `BM_AUTO_PROVISION=1` on the cron job and redeploys; verify `devices.system_id`,
  `device_gateway_history`, media labelled by the gateway they arrived through.
- Credentials exist in the Mac shell BY NAME ONLY — never print them: `NEREUS_ADMIN_TOKEN`
  (`Authorization: Bearer`), `STAGING_DATABASE_URL` (psql, Postgres 18.3; local `pg_dump` is
  16.x and cannot dump it), `SOFAR_API_TOKEN_BM_REEF`, `SOFAR_API_TOKEN_AOML`. Backup: Render
  export taken 2026-09-22 before the merge; PITR 3 days; git tag `staging-backup-20260921-212118`.
- What the auto-mode classifier allowed / blocked for the last session: allowed — read-only
  GETs, psql (reads AND writes), `git commit`/`push`, state-changing SSH to the Pis (Nick's
  permission stands: the cameras are yours over Tailscale); blocked — `curl -X POST` to the
  staging admin API and `gh pr create`. When blocked, hand Nick the exact command; do not
  route around it. Worktree of the merged branch: `nereus-vision-dev/.claude/worktrees/node-identity`.

## FIRST TASKS

1. **Unblock the live-fire test (backend, ~1 h):** verify the swap direction; read one cron
   tick's shadow lines (ask Nick for the cron log, or infer from `GET /admin/ingest/gateways`
   + `skipped_*` counters); fix the resolver gap above with tests (assert-script style beside
   `tests/test_gateway_poll_integration.py`); un-pause the leftover row; Nick flips
   `BM_AUTO_PROVISION=1`; recover the morning's clips with one manual poll-once per Spotter;
   verify `devices.system_id` moved, history rows opened/closed, media labels. Report it as
   the Gate 2 record in the spec (§7).
2. **SD analysis (both cards):** the card contents are on the Mac already:
   `~/Downloads/SPOT-31593C_indoors.zip` (35 files; `log/0000_*`: MS 3.6 MB, NCD 11.9 MB,
   HDR 21 KB, BM_TX) and `~/Downloads/SPOT-33507C_indoor.zip` (63 files; `log/0000_*` up to the
   19:01Z self-reset, then `log/0001_*`: MS 4.0 MB, NCD 13.1 MB). Unzip into a timestamped
   run folder under `runs/` (git-ignored: `*.log`, `*.csv`; never commit card contents — the
   BM_TX hex dumps contain bench footage). Confirm `log/index` matches the sets present. Per Spotter, per
   cycle: bus-on (`BRIDGE_SYS`), the camera burst window (`BM_TX.log` submissions), every
   `MS_Q_CELLULAR_ONLY is full` timestamp → phase on the 5-minute grid AND offset from the
   hourly `MS_Q_LEGACY` report, `HDR.log` push times (+~2 s after boundaries), `NCD.log` sync
   cadence and `Checking for Rx`, reboots. Extend `tools/bm_video_soak_report.py` with the
   Sprint24 step-7 columns sourced from SD, join with Sofar (`SOFAR_API_TOKEN_BM_REEF`) and
   staging, and produce: per-cycle CSV + a timeline cut sheet per rig + one table answering
   "where does loss fall". Compare the two rigs (Notecard models differ).
3. **Timing recommendation:** with numbers. Where should a ~160 s burst start relative to
   the 5-minute boundary, and how to avoid the hourly report minute (Sprint23 hypothesis:
   report minute = first post-boot LEGACY report rounded up to the next 5/10-minute boundary,
   then every 60 min — test it on both cards). Then `alignmentInterval5Min`: keep 1 if the
   data says aligned bursts are more robust (Nick's preference); otherwise make the case.
   The camera-side knobs that already exist: `transmit_phase` (Sprint11 C2, 5-minute grid
   guards) — do not add a new mechanism if tuning that one is enough.
4. **Indoor test today (one variable):** cards back in (Spotter resets — record the new
   report minute), consoles plugged in, monitor with `--only`; run ≥ 6 cycles per rig with the
   chosen setting; measure completeness per cycle at Sofar and staging; compare to the
   overnight numbers. Cellular only, no `note sync`, one commit per bridge BEFORE the run.
5. **Part 2 — self-healing resend, design only:** write
   `nereus-vision-dev/backend/docs/SPEC_resend_heal.md` (+ a device-side section in a
   bm_cam_legacy sprint doc) covering: how the backend already knows the missing chunk indices
   per media (parser + `percent_received`) and an endpoint to expose them; the command
   (`rsd <media> <index ranges>`, TABLES_VERSION 8) sent via `tools/sofar_send_command.py`;
   delivery constraints (a command lands only ~72 s after the hourly report AND the camera
   must be awake with a listener — there is NO daemon in `video_tx` today, so porting it back
   (Sprint24's dropped D2) is a prerequisite; held commands are lost, TODO-BM-017); the camera
   persisting the exact sent payload per filename on SD; the wire (rev 4: chunks re-sent for a
   CLOSED group must be accepted by filename — today a chunk belongs to the open group or is
   dropped; the backend dedupes by index keeping the longest payload); a test ladder
   (loopback → console → cellular). Stub the backend endpoint and the command table entry
   behind flags only if Nick signs the design off.

## STANDING RULES

- One variable at a time; cellular only (every send `cellular_only`); `note sync` OFF.
- `--only <SPOT-ID>` on every monitor; never open a port twice; one writer per `cmd.txt`.
- No Spotter reboots and no `bridge cfg commit` during a run. `log flush` before pulling a
  card; never fsck/First Aid a Spotter card; format cards FROM the Spotter.
- Backup before editing anything on a Pi; write the restore command next to the change.
- Repo is PUBLIC: no bench footage, no tokens, env-var names only.
- Report to Nick at each milestone: What happened / What I learned / What's next, two
  bullets each, comparisons in tables. Trust artifacts, not exit codes.

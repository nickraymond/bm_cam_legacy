# Sprint26 S3c — save_local: implementation plan

Written 2026-09-27. Status: **DONE 2026-09-27** — bench gate PASS on bmcam003 (runs/s3c_bench_20260927/RESULTS.md: 1 h stay_on × save_local, SD bounded, no media on the uplink, W11 seen; video bound = cap + 2 clips, F3). Was: **APPROVED 2026-09-27** (Nick: "move forward with the improvement and proposals"): the §5
consensus supersedes §0–§2 where they differ. R1 = defer J8 (recorder) to a follow-up; R2 = the
shared default stays 10 GiB (changing it would change every video unit's resolved config); card
sizes are recorded at the gate and the default is revisited then; R3 = save_local is in no RC
until S4; R4 = measured at the gate.
Branch `feature/sprint26-s3c-save-local` from `origin/development` 9491f1f (S3b / PR #80 merged).
Spec: `DESIGN_supervisor.md` §4 "Actions" (still/video × save_local), §6.3 (`mode.output` →
`save_local` is guarded_revert), §8.3 S3c row, §10–11; `PLAN_S3a.md` G4; `PLAN_S3b.md` H1–H12;
`runs/s3b_bench_20260927/RESULTS.md` §3, §5.

Baseline on this Mac (.venv-dev, PyYAML 6.0.2, Pillow 12.3.0): suite **1204 passed** at S3b close
(`tests/test_reference_card_color_utils.py` excluded). Every commit re-runs the goldens (legacy,
per_boot supervisor, stay_on), `tests/test_config_v2_parity.py` and the full suite BEFORE it is
committed. Legacy vectors never change (G2). Existing supervisor and stay_on vectors never change.

Facts this plan rests on (inspection at 9491f1f):
- `mode.output` is `ENUM transmit|save_local`, `guard=GUARDED_REVERT, guard_when=("save_local",)`,
  `runnable=("transmit",)` (`config_registry.py:124-126`). `config_v2._cross_key_errors`
  (`:108-112`) refuses a non-runnable value: strict load (deploy) refuses; at boot the v1 file is
  used. Nothing at runtime reads `mode.output`. The guard classes are data only until S4.
- The v1 render (`config_v2.render_v1_text`) has no `output` concept: the legacy runtime reading a
  save_local file would **transmit**.
- **Stills files per cycle** (`images/`, `<stem>` = `YYYY-MM-DDTHH:MM:SSZ_image`): the native
  `<stem>_native_full.jpg` (4608×2592 at `camera.native.jpeg_quality`) + its `.stdout.log`,
  `.stderr.log`, `.metadata.json`; `<stem>_compressed.jpg` (the final ladder encode) + its
  `.capture_metadata.json` sidecar; on transmit a `camera_log.csv` row and, with media_key,
  `sent/<stem>_compressed.sent.json`. **Nothing ever deletes any of them** except partial natives
  between failed capture attempts (`rc_capture.py:183-190,636-642`). The "~1.4 MB/cycle" in DESIGN
  §4 is not backed by code or a measurement — the bench measures it.
- **The compressed JPEG is the heal payload for images** (`rc_media_key.py:24`: "the compressed
  JPEG on disk IS the wire bytes"; the sent record points at it). A guard that deletes it breaks
  heals for that key. `prune_sent` ages sent records out after `retain_days` (hard cap 30 d).
- **Prepare/encode**: `prepare_source(native, crop, output_width)` (fixed crop + LANCZOS resize,
  in memory) and `encode_progressive(img, quality, chunk_chars)` (one quality, no file write)
  (`rc_jpeg_encoder.py:59-122`). The ladder (`rc_quality_selector.select_quality`) only exists to
  fit the uplink; save_local needs one encode.
- **`--capture-only`**: `still_action` stops after prepare (`rc_progressive_jpeg.py:608-617`), the
  prepared image is never saved; the native is. Legacy halts after it (PortOwner.finish). Under the
  supervisor, main forces `runtime = "legacy"` (`:1318-1320`). A trg 1 already runs capture-only
  under the supervisor (boot drain → `capture_only`).
- **Video × transmit already records at recorder quality to SD**: `video_action` records
  `duration_s + lead_in_s` with the recorder's `record_one_clip` and `video.record.*` geometry into
  `video.dir`, behind `video_ring.ensure_room` (a paused ring raises), then fits and sends. trg 1 on
  a video unit (W5) stops right after the record ("clip kept on SD, not sent"). No `.json` sidecar
  or `manifest.json` is written on that path (only the recorder writes them).
- **The recorder** (`mode.media: video_logger` → v1 `capture_mode: video`, `video_tx.enabled:
  false`) is `video_recorder.run_video_mode`: its own `while True` clip loop, the ring before every
  clip (pause + 60 s recheck), sidecar + manifest + status line per clip, the gallery UI thread;
  ends on `session_minutes` (halt) or power loss. It is the third port owner: it starts its own
  daemon (`daemon_factory` → `bm_port.open_shared`), closes the port in its `finally` only if a
  daemon ran, and with `--transmit` but commands off it opens the port lazily via
  `send_compact_text_message` and never closes it. No `PortOwner`, no `new_session`. No golden
  covers it (every video scenario has `video_tx.enabled: true`).
- `heic`: main prints "the heic path was retired … Nothing to do" and returns 0 before any port or
  halt, in both runtimes. There is nothing left to move.
- The ring (`video_ring.ensure_room`) reads `shutil.disk_usage` of the whole filesystem, prunes the
  oldest completed `.mp4`/thumb/`.json` triples, and is injectable (`disk_usage_fn`, `remove_fn`).
  On this Mac the real disk may already be over 75 %, so any new guard in a golden path must use a
  faked disk usage.

## 0. Gaps in the spec — for Nick's ruling

**J1 — Registry (REGISTRY_VERSION 3 → 4, as S3a.4 / S3b.1).**
- `mode.output`: `runnable=` dropped; save_local becomes runnable.
- Cross-key: `save_local` needs `commands.runtime: supervisor` (strict load refuses; boot falls
  back to v1 = transmit legacy, loud line, as stay_on does).
- `--runtime legacy` overriding a save_local file: **nothing to do this boot** (one loud line,
  exit 0, no port, halt as configured). The operator said "keep it local"; running legacy would
  transmit it. (Alternative: run legacy transmit anyway, as stay_on → per_boot does.)
- New keys (J2, J4): `still.save.quality`, `still.storage.max_used_pct`,
  `still.storage.min_free_gb`, `still.storage.dry_run`. `config_v1_reader` sets the defaults.
- No guard machinery in S3c (no `set` until S4): save_local is set in YAML only; the
  guarded_revert class stays declared in the registry for S4.
- Every v2 hash changes again; bmcam003/004 get a re-verified hash at deploy.

**J2 — What a still × save_local action saves.**
- Proposal: capture → prepare (same `still.crop` / `still.output_width` as transmit, so saved and
  sent images are comparable) → ONE encode at `still.save.quality` (new key, INT 1–95, **default
  85**; the ladder's 15/13/11/9 exist only to fit the uplink) → `<stem>_compressed.jpg` + its
  `.capture_metadata.json` sidecar (with `"output": "save_local"`). The native is kept too (it is
  the full-resolution record), under the J4 guard.
- No START/chunks/END, no `camera_log.csv` row (that log is "transmit results"), no sent record.
- Alternative: keep the native only and skip prepare/encode.

**J3 — What a video × save_local action saves.**
- Proposal: the W5 trg-1 clip, promoted: time read → ring → record `video.send.duration_s +
  lead_in_s` at recorder quality (`video.record.*` geometry, fps, bitrate) → `.mp4` + thumb, **plus
  the recorder's `.json` sidecar and `manifest.json`** (so the gallery UI and tools list it the same
  way as recorder clips). No fit, no send. No new clip-length key.
- Continuous recording stays `mode.media: video_logger` (J8).
- Alternative: a new `video.save.duration_s` so saved clips can be longer than sent ones.

**J4 — The stills storage guard.**
- Where: every supervisor stills action, **both outputs** (transmit units grow the same natives),
  before the capture. The legacy runtime is untouched (G2).
- Limits: `still.storage.max_used_pct` (default 75) and `still.storage.min_free_gb` (default 2.0)
  on the filesystem holding `images/`, read once and applied arithmetically like the ring
  (`disk_usage_fn`/`remove_fn` injectable). `still.storage.dry_run` logs only. (Video keeps
  `video.storage.*`; one SD serves both, and each guard sees the whole disk.)
- Prune order, oldest first by `<stem>`:
  1. natives: `<stem>_native_full.jpg` + its `.stdout.log`/`.stderr.log`/`.metadata.json`;
  2. whole stems (compressed + sidecar) **that have no sent record** in `sent/` (save_local images,
     or media_key off, or records past `retain_days`).
  A compressed JPEG with a live sent record is **never** deleted (it is the heal payload).
- Still over a limit after pruning: the action does not capture; summary `error = "storage_full"`,
  action log line, one `[STORE][FULL]` log line. No new wire (see J5 for the per_boot status line).
- Every prune is logged (`[STORE] pruned N native(s), M stem(s), freed X MB; used a→b %`).

**J5 — What a save_local unit puts on the wire.**
- stay_on: nothing per action. Heartbeats (`<WS a=idle>`), acks and the first-skip `<WS>` as today.
- per_boot: proposal **W11**, one `<WS a=saved …>` per save_local action (today's
  `send_wake_status` fields; 1 message/boot), so the backend still sees an hourly unit alive.
  Staging maps an unknown `a=` to `wake_action` and advances `last_seen` (PLAN_S3b facts), so it
  ingests today. Alternative: send nothing (the unit is dark to the backend until a heartbeat-less
  per_boot unit is next transmitting).
- The listen tail still runs per_boot (commands stay reachable). No heals ride a save_local action
  (nothing is transmitted); O5 idle heals keep running in stay_on for media sent earlier.

**J6 — `trg` on a save_local unit.**
- Proposal: trg follows its own meaning (`command_tables.py:322-328`): trg 1 = capture only (the
  save_local action); **trg 2/3/4 transmit that one action** (the operator asked for a picture or a
  clip); the next action is save_local again. This matches the "one-shot" rule (§9).
- Alternative: every trg runs the configured output (trg 2 on a save_local unit saves only).

**J7 — Time and window for save_local.**
- With `--transmit` (the cron line), the action does the Spotter time read and window gate exactly
  as a transmitting action (filenames are capture time; a Pi has no RTC). Scheduled save_local
  actions obey the window; trg bypasses it.
- Without `--transmit` (bench), no daemon and no gate: Pi clock, as `--capture-only` today.

**J8 — The recorder (`video_logger`) under the supervisor (G4).**
- Port ownership only: the supervisor's process scope (PortOwner + port session + daemon) comes up
  once; `run_video_mode` gets the owner's daemon (not its own `daemon_factory`), and shutdown →
  close → halt move to `owner.finish(halt=session_expired)`. The lazy commands-off open goes through
  the owner (one OPEN, one CLOSE). Loop, ring, sidecars, UI, session halt are unchanged.
- `mode.run` / `mode.output` do not apply to `video_logger` (it is its own loop): cross-key rule
  `video_logger` needs `mode.run: per_boot` (stay_on refused), and `mode.output` is ignored with
  one line. Legacy recorder path unchanged.
- Not in S3c: folding the recorder into video × save_local × stay_on (DESIGN "folds in later", N5).

**J9 — `--capture-only` under the supervisor.**
- The forced fall-back to legacy goes. `--capture-only` runs the still action with `capture_only`
  through a per_boot `Boot` (the same path trg 1 already takes): the J4 guard, then capture +
  prepare, no encode, halt from the owner (legacy halts too). With J2 there is a real alternative
  for "save a picture" (`mode.output: save_local`), so `--capture-only` keeps its bench meaning.

**J10 — Stage gate shape** (details in §2). bmcam003 is a video unit. Proposal: one hour of
stay_on × save_local split in two halves, **30 min video then 30 min still**, `interval_s: 60`,
with the guard limits set just above the disk's current use so pruning MUST fire within the run
(proves "SD bounded", not just "didn't fill in an hour"). Alternative: 1 h video only, stills
covered by a shorter 10 min run.

## 1. Commits (each small, each green)

| # | commit | gate / tests |
|---|---|---|
| S3c.1 | **Registry** (J1): save_local runnable, cross-keys (supervisor; `video_logger` per_boot only), `still.save.quality`, `still.storage.*`, `REGISTRY_VERSION` 4, `config_v1_reader` defaults, config_v2 docstring; the `test_config_v2` strict case that used save_local as "not runnable" moves to the cross-key rule; main: `resolve_output()` + `[OUTPUT]` line, legacy + save_local → nothing to do | goldens + settings goldens byte-identical; `tests/test_s3c_registry.py` (keys, ranges, cross-keys, S3b-era v2 file still loads, version 4) |
| S3c.2 | **Stills storage guard** `rc_still_storage.py` (J4): pure, injectable disk usage/remove, two-tier prune, sent-record protection, dry run, full → refuse. Wired into supervisor stills actions (both outputs); the golden world fakes the disk under the limits | all vectors byte-identical; unit tests: tier order, heal payload never deleted, dry run, full, arithmetic after each delete, never raises on a vanished file |
| S3c.3 | **still × save_local action** (J2, J6, J7) | new supervisor-only scenarios `still_save_local` (per_boot) and `stay_on_still_save_local`; unit tests: one encode at `still.save.quality`, sidecar `output`, no START, trg 2 transmits once then save_local again |
| S3c.4 | **video × save_local action** (J3): record → sidecar + manifest, ring; no fit/send | scenarios `video_save_local`, `stay_on_video_save_local`; unit tests: ring paused → error, sidecar/manifest written, no fit called |
| S3c.5 | **W11** `<WS a=saved>` per per_boot save_local action (J5), its own W-commit (drop it if J5 rules "nothing") | reviewed diff of the two per_boot save_local vectors; README W table row |
| S3c.6 | **`--capture-only` under the supervisor** (J9) | new supervisor scenario `still_capture_only` (legacy counterpart = same vector, as S3a: capture-only sends nothing on the wire either way); unit test: owner halts |
| S3c.7 | **Recorder under the supervisor** (J8): `run_video_mode(…, supervised=boot)` takes the owner's daemon and leaves close/halt to the owner; legacy call unchanged | test_video_recorder green unchanged; new tests: one OPEN/CLOSE under the supervisor, commands-off lazy open owned, session halt via owner, legacy `finally` unchanged; a golden scenario `recorder_session` (2 clips, fake clock, session_minutes) under both runtimes if the harness can fake `record_one_clip` there (else unit tests only, said in the PR) |
| S3c.8 | **Soak**: `tests/test_s3b_soak.py` gains a save_local variant (50 actions, still + video, fake disk that grows per action): used % stays ≤ the limit, image/video file counts bounded, one port OPEN | runtime ≤ 60 s |

Supervisor-only per_boot save_local scenarios get their own vectors dir `vectors_save_local/`
(like `vectors_stay_on/`: no legacy counterpart, recorded once, reviewed in full); stay_on ones go
in `vectors_stay_on/`. Docs in the same commits: golden README ("save_local" section, W11 row), the
`bmcam-field-update` skill (save_local units send no media), runtime manifest for the new module.

Before the bench: an independent reviewer (subagent, fresh context) checks the whole diff against
DESIGN §4/§6.3, this plan and PLAN_S3a/S3b. Every finding fixed or answered in writing.

## 2. Stage gate (bmcam003 stay_on × save_local; bmcam004 = control on S3b per_boot)

Needs SPOT-33507C's bus **held on** for ~75 min (`bridgePowerControllerEnabled 0` + commit +
read-back; Nick's OK in chat at the time), restored with the Pi disarmed + halted and re-armed in
the stub window (`runs/s3b_bench_20260927/rearm_s3b.sh`). Everything in `runs/s3c_bench_<date>/`.

1. Catch bmcam003 awake, disarm (backup), deploy S3c by SHA, record the new hash.
2. Record `df /`, `du -s images videos`, file counts; measure one native's size (the "1.4 MB").
3. v2 file: `mode.run: stay_on`, `mode.output: save_local`, `interval_s: 60`, `media: video`,
   `video.storage.max_used_pct` = current used % + ~0.1 %. Start the wrapper by hand (cron line).
   A sampler logs `df` + counts every 60 s.
4. 30 min: ~30 clips; the ring must prune (used % flat after it first reaches the limit).
5. SIGTERM, switch to `media: still`, `still.storage.max_used_pct` likewise; 30 min, ~30 stills;
   natives pruned first, then non-sent stems; no sent-record JPEG touched (list before/after).
6. One `trg 2` over the console during the still half (J6): it transmits once (complete at Sofar),
   the next action saves only. Heartbeats seen on the console.
7. Restore: v2 file back to the backup (per_boot transmit video), cron re-armed, bus schedule
   restored, state in RESULTS.md.

PASS = both halves ran with no reboot and one port OPEN, SD used % bounded by the configured limit
(sampler plot), no media sent except the trg 2 action and the heartbeats, legacy + per_boot +
stay_on goldens unchanged. Cellular cost: ~12 heartbeats/h + one still transmit (~30–190 msgs).

## 3. Not in S3c

- guarded_revert machinery for `mode.output` (S4, with `cfm`), `set` of any key.
- Folding the recorder into video × save_local × stay_on; recorder goldens beyond one scenario.
- Pruning `camera_log.csv`, per_boot `rc_cycle_*.log` files (future hardening).
- Deleting the legacy runtime (after S5).

## 4. S3c step 0 (bmcam004 follows S3b; `runs/s3c_bench_20260927/`)

bmcam004 moves from development 1636c8b (S3a) to development 9491f1f (S3b) at the 16:00Z bus window
(`move_bmcam004.sh` = `watcher_s3b.sh bmcam004` → `follow_bmcam004.sh`: field update
`--ref development --profile bmcam004/live_20260925 --leave-disarmed`, check v2 + supervisor +
per_boot, re-arm from `/home/pi/s3bbench/backup/crontab_ARMED.txt`, halt). That hour's clip is lost.
Result (2026-09-27): **PASS.** Caught at 16:00:39Z (up 0 min), cycle SIGTERMed, cron disarmed,
backups in `/home/pi/s3bbench/backup/`. Field update SUMMARY PASS (print-config + config json + v2
parity OK), sha 1636c8bf17bf → **9491f1f54217**, hash 5e679ef9 → **72a12186** (registry v3; same as
bmcam003). Verified level v2, runtime supervisor (config), run per_boot. Re-armed (`@reboot` flock
line), halt issued 16:01:17Z, dark 16:01:46Z. Logs: `move_bmcam004.out`, `bmcam004_deploy_s3b.log`.

## 5. Review 2026-09-27 — consensus (supersedes §0–§2 where they differ)

Two independent reviewers (A: code and hidden coupling, B: field ops and scope) found 2 blockers,
about 20 should-fixes and several nits. After one consensus round, both agree on every item
below. Their inputs: `REVIEW_S3c_consensus_r1.md`, plus each reviewer's counters, all folded in here.

**Blockers (both fixed in the plan):**
- **C1 — stay_on save_local went dark.** `_loop` resets `last_uplink`/`last_send` after every
  non-skipped action (`rc_supervisor.py:622-625`). At `interval_s` 60 no heartbeat or O5 heal
  would ever run. Fix: the action summary carries `uplinked`, and only that moves the two timers.
  Unit test and stay_on golden: interval 60 < heartbeat 300.
- **C2 — one command would flip save_local back to transmit.** The re-resolve rebuilds
  `settings` from the YAML base and carries over only `video` (main `_reresolve`). So the output,
  the save quality and the storage limits are read from the v2 values held on `Boot`, never from
  `settings`. Test: a command drained at boot, and one applied while idle; the action still saves.

**Changes to the J items:**
- **J1:** only one new key, `still.save.quality` (default 85). The stills guard uses the SAME
  limit pair as the ring, `video.storage.{max_used_pct,min_free_gb,ring_dry_run}`, so the two
  guards can't fight (renamed `storage.*` in S4). `--runtime legacy` on a save_local file means
  legacy **transmits**, with a loud line. That is the only automatic revert until S4 and matches
  stay_on.
- **J2:** save both. The native is the record; the q85 crop is the quick look. Writes are atomic
  (tmp + fsync + rename) on the save_local path; the transmit path is untouched.
- **J4 guard:**
  - Transmit units: prune, never refuse (WARN).
  - save_local: refuse the capture when still over the limit after pruning; `reason=storage_full`
    on the next `<WS>`/heartbeat. The heartbeat's `reason` becomes a callable.
  - Tiers, oldest first:
    - tier 0: `.tmp` debris;
    - tier 1: natives of any stem that is not save_local, pre-S3c history and capture-only
      natives included;
    - tier 2: whole save_local stems;
    - tier 3: transmitted compressed + sidecar pairs whose JPEG no live sent record's `payload`
      names.
  - "Live" means the record's mtime is within `retain_days` (cap 30 d), computed by the guard;
    `sent_dir` comes from the media_key config. A heal payload is never deleted.
- **J5 / W11:**
  - Stills keep today's per-action `<WS a=cap>` under per_boot, and it is suppressed in stay_on
    save_local.
  - W11 applies to video only: a per_boot video save_local sends one `<WS a=saved>`.
  - per_boot save_local runs the heal slot when heals are pending. It is factored out of
    `heal_pass`, runs on the action's own budget (G1 kept), and sets `uplinked`.
- **J6 reversed (DESIGN §6.1 is the spec):** trg 2 = "capture + output per mode", so a save_local
  unit SAVES on trg 2. trg 1 = capture only (native). The console label for trg 2 changes from
  "capture + send" to "capture + output per mode". A one-shot "send this one" (`o` in `trg kv`)
  is an S4 item.
- **J7:** save_local with `--transmit` always reads Spotter time, even with the window off. If
  the read fails it saves anyway on the Pi clock (sidecar `time_source: system`, loud line); the
  window is enforced only on a Spotter time.
- **J9:** `--capture-only` forces per_boot. Parity is proved by running the scenario under both
  runtimes (the harness passes `--transmit`, so `a=cap` and the time read are on the wire).
- **Goldens:** keep `STAY_ON_SCENARIOS`, add `SAVE_LOCAL_SCENARIOS`; `SUPERVISOR_ONLY` = their
  union, with a name → dir map (`vectors_stay_on/`, `vectors_save_local/`) and one catalogue test
  per dir. The under-limit guard path adds no summary key.
- **Action log:** `output` = the resolved output.

**Rulings needed from Nick:**
- **R1 (scope):** defer J8, the recorder under the supervisor, to its own small follow-up, which
  needs a legacy recorder golden first plus its own driver (`finish(halt=session_expired)`, no
  double shutdown, close the lazy port, an action line). Both reviewers recommend it. It is in
  the S3c deliverable as written, so this is Nick's call. `--capture-only` (J9) stays in S3c.
- **R2 (SD default):** the shared `min_free_gb` default is 10 GiB. On a 16 GB stills card that
  caps use near 30 %, and transmit units would start pruning natives much earlier than today
  (never). Confirm the stills cards' sizes, or lower the default. Note: once natives are pruned,
  the 1000 px crop is the only record of a transmitted image.
- **R3 (guardrail):** `mode.output: save_local` goes in no release candidate until S4's
  `cfm`/guarded_revert lands (added to DESIGN §11). `rc_field_update` prints a banner for a
  save_local unit.
- **R4 (energy expectation):** a per_boot save_local wake still pays the 150 s listen tail, so
  the saving against transmit is modest. The battery lever is `commands.listen_tail_s`. The gate
  measures it.

**Revised commits:** S3c.1 registry + `resolve_output` on Boot (C2) · S3c.2 `uplinked` timers
(C1) · S3c.3 stills guard module (tiers above) · S3c.4 still × save_local · S3c.5 video ×
save_local · S3c.6 W11 · S3c.7 `--capture-only` · S3c.8 save_local soak · (S3c.9 recorder only
if R1 = keep). Every commit keeps the full suite, legacy, per_boot and stay_on goldens green.

**Revised gate (bmcam003):**
- Before: stop `bm-heal-driver` for bmcam003 (back up `state.json`), and archive the
  images/videos history that the lowered limit would prune to the Mac first.
- Limits come from a measured per-action size, so pruning starts after about 10 actions.
- Run: 30 min video + 30 min still, stay_on save_local, then one per_boot save_local wake (halt
  dry-run, bus held): W11/`a=cap`, the tail, and the wake duration against transmit.
- Artifacts:
  - `run_manifest.json`;
  - a sampler CSV every 15 s, plus a PNG of used % against the limit;
  - before/after file lists with the sha256 of every JPEG that has a sent record;
  - the action log, the console log, RSS, boot time, and directory-walk timings.
- PASS = used ≤ limit + one action's bytes, no heal payload touched, heartbeats every 300 s, no
  media on the wire.
- Restore checklist with read-back: controller = 1, crontab diffed against ARMED, config hash,
  heal driver active with its `state.json` diffed.

## 6. Pre-bench independent review (2026-09-27, whole diff 974f931..f843bbe)

1 blocker, 3 should-fix, 6 nits; suite green before and after.
- **#1 BLOCKER, fixed:** a stay_on save_local action that RAISED had no `uplinked` key and
  counted as an uplink (a failing unit never beat: probe 17 actions, 0 heartbeats). `_loop` now
  defaults `uplinked` to `not boot.save_local`; unit test.
- **#2 fixed:** a save_local unit with commands off had no Spotter time source (window off) and
  no road back to transmit (S4 cfm): cross-key `save_local needs commands.enabled: true`. The
  time read also honours `set_system_clock_from_spotter`.
- **#3 deferred to S4 (stated):** the trg 2/3/4 console labels ("capture + send", "send stored
  …") are on the LEGACY wire (help reply, `still_bench`), so renaming them would change legacy
  vectors (G2). S4 regenerates the command reference; the labels change there.
- **#4 fixed (ruled per C14 wording):** the heal slot runs per_boot only; stay_on leaves heals to
  the O5 idle pass (a save_local action is not an uplink, so the idle timer runs).
- **#5 fixed:** save_local_time_read keeps its own W6 counter (a window-off gate reads nothing).
- **#6 fixed / intended:** a guard exception clears `storage_reason` (never stale). Transmit
  stay_on heartbeats may carry `r=storage_full` too: intended (the SD is the unit's, whatever the
  output).
- **#7 fixed:** `sent` only after the `<WS>` call returns; a heal slot that planned 0 is not an
  uplink.
- **#8 fixed:** `stay_on_save_local_video` now runs interval 60 < heartbeat 300 (13 clips, beats
  at 5 and 10 min), pinning C1 on the wire.
- **#9/#10 noted, no change:** video per_boot storage_full exits 1 like any video error (stills
  0; the wrapper only logs it); the golden disk seam is `rc_still_storage.DISK_USAGE_FN`.

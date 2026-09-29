# Sprint26 S6 — remote: implementation plan

Written 2026-09-28. Status: **APPROVED as proposed (Nick, 2026-09-28)** — H1–H6 ruled as written. Independent review folded in (§7).
Spec: DESIGN_supervisor.md §6.1–6.5, §8.2 W9, §8.3 S6 row, §9, §10 O8/O11, §11; PLAN_S4.md
G4, G8, G9, G13; KICKOFF.md R3–R5; runs/s5_console_20260928/RESULTS.md (F1, F5).

S6 splits in two:

- **S6a — backend only, no hardware (this session).** nereus-vision-dev (nvd) PRs into
  `staging`, opened but NOT merged or deployed: a 24 h S5 conductor loop polls staging every
  minute until ~2026-09-29 22:30Z, and every staging deploy restarts the backend and runs
  migrations. Merge and deploy wait for the loop to end AND Nick.
- **S6b — needs hardware (after the loop, with Nick).** Planned here (§5), not started.

## 0. Facts this plan rests on (nvd `origin/staging` 03272be; bm_cam_legacy `origin/development` 0f67784)

- **Ingest routing.** `<WS>` and `<HL>` are collected per node from the Sofar payload and
  ingested after images, in both the cron worker (`app/scripts/sofar_poll_worker.py:382`) and
  poll-once (`app/admin_ingest_poll_once.py:231`). `<HL>` is the model for new line types:
  parse → `external_ingest_events` dedupe by payload hash → rows; errors never fail the poll.
- **Acks and `<CF>` are not parsed anywhere.** They fall through the image parser (counted as
  nothing) and are lost. O8 ("get answers only in raw Sofar data") is still true.
- **`<WS>`**: `parse_compact_ws_message` (`bm_sofar_telemetry.py:105`) already keeps every
  token in `short` and passes unknown `a=` codes through as `wake_action` (`:125-134`), so
  `a=idle|saved|inc|crashloop` already land as their code. `cfg=` and `up=` are parsed but
  dropped from `metrics_json`.
- **START** fields go through the loose `parse_key_values` into `start_header_fields`, then
  `build_image_capture_metrics_from_probe` picks named keys. `st` = `sd_total_mib` (`:347`).
  The v9 START keys `cfg tg r m d` are parsed but dropped. None of them collides with a key
  that is read today (checked: `st su sf sp im bf lg zh`, `fps dur res crop br crf`, `fmt`,
  `key`, `filename`, `length`).
- **Chunk regexes** that reject a W9 chunk `<I{key}.{i}/{M}>` (G4): `RE_CHUNK_MARKER`
  (`bm_image_parser.py:147`, M0), `RE_KEYED_CHUNK` (`:623`, keyed grouping), the sample
  preview (`:939`), and the probe's `CHUNK_RE` (`admin_ingest_message_probe.py:29`).
  A chunk-born keyed row has no length → `length_unknown` → never healed
  (`heal_commands.py:158`). A stored row whose length differs from an incoming one is a
  `media_key_collision` (`poll_once_ingest.py:184`).
- **Heal ids**: `next_command_id` = max(`heal_commands.command_id`) + 1 from 100 000
  (`heal_commands.py:328-333`), no upper bound. The backend never sends a command
  (`heal_commands.py:4`); the operator or the conductor sends the returned line.
- **Heal line limit**: `MAX_CONSOLE_BYTES = 270` (`heal_commands.py:40`). S5 F5: the Spotter
  USB console takes ≤ 256 B per line. Heal lines are sent over the console today (bench
  conductor / heal driver), so a 257–270 B heal line would be dropped (`RX overflow`).
- **The v9 uplink shapes** (ground truth `BM_Devel_Pi/command_wire.py`, recorded in S5
  `ladder.log` and the goldens `tests/golden/vectors_v9/*/trace.txt`):
  - ack `{"id":N,"ok":0|1,"h":"<hash8>"[,"e":code][,"k":key][,"s":1][,"d":1][,"v":N]}`;
    a duplicate (`d:1`) carries the ORIGINAL result and hash (§6.2 `result_cache`). v8 acks
    carried `st` as an object.
  - `<CF v=1 h=<hash8> [n=i/N] [head] key=value[@src] …>`; head = `reverted= lim= ref=` or
    `err= k=`; values `%XX`-escaped outside `[A-Za-z0-9_:./+,-]`; lists comma-joined; bool
    `1/0`; `null`; source `@c<id>` (command), `@d` (default), `@v8`, any `@<token>`, none
    (YAML); an item cut to fit ends in `~` (and may have lost its `=` or `@src`); every
    registry key is dotted (102/102), head names are not. `<CF err=…>` is re-sent every boot
    while the problem lasts (`command_v9.py:350-360`). The bench poller already parses both
    (`tools/sofar_poll_acks.py` `extract_ack`, `extract_cf`).
  - The `<CF … reverted=mode.output lim=boot3 ref=61111>` sample is the unit's own log line
    (`ladder_bmcam004.out`), not a decoded cellular payload; the shape is the builder's.
  - `<WS v=1 a=… cfg=<hash8> up=<s> …>`; START `… key=…, cfg=<hash8>[, tg=<id>, r=WxH+X+Y|na,
    m=<n>, d=<s>] …`.
- **Remote-range ids are picked by the senders themselves**: the bench GUI picks the next
  id ≥ 1e6 from its JSONL plus the CLI send log (`tools/bm_command_gui/lifecycle.py:154`);
  `sofar_send_command.py --id` is given by the operator. The unit keeps ONE high-water per range, so
  two allocators in one range can refuse each other `e:"old"`.
- **Main-wire goldens exist**: `tests/golden/vectors/field_bmcam00{1,2}_main/trace.txt` (the
  bmcam001/002 wire, S1 harness against `main`), the §11 regression input.
- nvd tests are module-level assert scripts (`backend/tests/README.md`); baseline on this
  Mac: **36/36 pass** (with `PYTHONPATH=.:backend`). Migration head `20260924_0015`; no open
  nvd PRs.

## 1. S6a — what gets built (three PRs into `staging`, none stacked)

| PR | branch | contents | migration |
|---|---|---|---|
| **A** | `feature/s6a-chunk-total` | W9 `/M` parser (G4) + wire contract §14.6 + main-wire regression test | none |
| **B** | `feature/s6a-uplink-v9-fields` | `<WS>` `cfg`/`up`; START `cfg`/`tg`/`r`/`m`/`d` into telemetry | none |
| **C** | `feature/s6a-command-log` | ack + `<CF>` parsers, shared id allocator (heal guard), command log, hash → snapshot, desired vs reported | `20260928_0016`, new tables only (0011 stays reserved) |

A and B are independent and small. C is one PR with one commit per concern (each green), so
it never stacks on an unmerged branch (options in §3 H1).

### A — the `/M` chunk parser (unblocks S4w / W9)

- `/M` is accepted ONLY after a key: keyed `<I([0-9a-z]{6})\.(\d+)(?:/(\d+))?>`; the legacy
  `<I(\d+)>` is unchanged and an unkeyed `<I{n}/{M}>` stays rejected (so `<I5>` after a keyed
  START is still `legacy_after_keyed`). The same pair is used by the four places above.
  M0 (keyed grouping off): the index is used as today, M is ignored (legacy grouping takes
  its length from START).
- Keyed grouping: a group records the M values it saw. Rules:
  - START length present → it is the length (unchanged). A chunk whose M ≠ that length is
    **excluded from the group, never merged** (O11/G4: a collision), and counted
    `total_mismatch` (keyed flag + `keyed_stats` + one WARNING line).
  - no START (chunk-born) → length = the M carried by the group's chunks; when they
    disagree, length stays unknown (as today) and the group is flagged `total_mismatch`
    (never guessed).
  - a stored row's length (from an earlier window) plays START's role for later chunks.
  - chunks with index ≥ length are dropped as today (`index_ge_length`).
- Ingest: a chunk-born keyed row now gets `expected_chunks` from M, so it is no longer
  `length_unknown` and the heal loop picks it up. A later START with a different length is
  still a `media_key_collision` (today's rule, conservative: never merge).
- Old wire still parses identically: `<I{n}>`, `<I{key}.{n}>` (asserted by the regression
  test below).
- **Known limit (stated, not fixed in A):** a START-lost VIDEO healed to complete from `/M`
  has no `fps`, so the mp4 transcode fails (`video_derivatives.py:106`) and a complete row
  never merges a late START (`poll_once_ingest.py:228`). The H.264 original is stored. Fix
  proposed for S6b/S4w: take `video.send.fps` from the unit's config snapshot (C) — ruling H6.
- Tests: W9 vectors built from `vectors_v9` traces with `/M` added (START lost / kept /
  M ≠ length / chunks disagree / heal chunk alone), both grouping modes; the probe;
  `missing_for_media` on a chunk-born row with M.
- **§11 regression test** (lands in A, used by every later PR). Two fixtures from the
  goldens' `W tx.02` lines (Sofar-shaped payloads, hex values, 1.3 s apart):
  - **main wire (FROZEN):** `field_bmcam00{1,2}_main`. The rows ingest writes (media fields,
    chunk counts, capture + wake telemetry `metrics_json`) recorded at `staging` 03272be;
    must stay byte-identical in every commit of every PR. Rows via the ingest functions with
    a fake session / FakeS3 (the `test_gateway_poll_integration.py` pattern), else a scratch
    Postgres.
  - **v9 wire (REVIEWED):** `vectors_v9/*`. Recorded at 03272be too; a PR that changes it (B's
    new metrics keys, A's `keyed_stats` counter) re-records it and the diff is in the PR.

### B — `<WS>` and START v9 fields (additive telemetry keys)

- `<WS>` metrics gain `config_hash` (`cfg`), `uptime_s` (`up`) and `wake_reason` (`r`, e.g.
  `window`, `restarts`, `storage_full`). `wake_action` is left as
  it is (`idle`, `saved`, `inc` already pass through as their codes; renaming them would split
  history). Tests: `a=idle|saved|skip_win|inc|crashloop` samples from S5 and the goldens.
- Media capture metrics gain `config_hash` (`cfg`), and on a triggered action `trigger_id`
  (`tg`), `resolved_crop` (`r`, text, `na` kept), `resolved_message_cap` (`m`),
  `resolved_duration_s` (`d`) (DESIGN §9: "the backend records r/m/d per media").
- Nothing else changes: no column, no endpoint. An image without these keys yields exactly
  the metrics it yields today (regression test from A). The message probe (logs.html) needs
  keys of 2+ characters (`admin_ingest_message_probe.py:34`), so START `r m d` stay hidden
  there; logs.html is S6b step 7.

### C — command replies, one allocator, the command log (§6.2, §6.5)

Commits (each with its own tests, full suite green before each):

1. **Pure parsers** `app/services/command_replies.py` (no DB):
   - `parse_ack(text)`: strict JSON object (no NaN/Infinity, duplicate keys refused), `id`
     int 0..2³²−1, `ok` 0/1, optional `h` 8-hex, `e` (code; unknown codes kept + flagged),
     `k`, `s`, `d`, `v`. A v8 ack (`st` object) is parsed and marked `v8` (bench history;
     field units have commands off). Anything else → None (counted invalid, never guessed).
     Ported from `tools/sofar_poll_acks.py` `extract_ack`/`extract_cf` with a parity test
     over the same samples.
   - `parse_cf(text)`: `v`, `h`, `n=i/N`, head (`reverted lim ref err k`), items
     `(key, value_text, source, truncated)` with `%XX` decoded; any `@<token>` source kept;
     a `~` item is kept as truncated (never compared) even without `=`/`@`; unknown
     non-dotted tokens kept in `extra`. Values are stored as wire text; comparison is
     **typed**: numbers (and comma lists of numbers) compare numerically (`8` = `8.0`),
     `1/0` vs bool, `null` vs None, else text.
   - `range_of(id)`: the §6.2 table (console / heal / remote / service / conductor).
   - Tests against every recorded sample (S5 ladder: slim acks, `e=auth k=sig`, `e=old`,
     `<CF … @c100000010>`, `<CF … reverted=mode.output lim=boot3 ref=61111>`; goldens:
     `still.crop=768,432,3072,1728@c1000501`), plus synthetic multi-part `n=1/2`, `err=`,
     `%20`, `~`, `null`, bool, and hostile input (NaN, dup keys, 9-hex hash, `n=3/2`).
2. **Migration 0016** (DDL only, three NEW tables, `lock_timeout` first, no backfill, offline
   test in the 0015 style + a scratch-Postgres upgrade/downgrade/upgrade round trip):
   - `device_commands` — the command log, one row per (device_id, command_id), **remote and
     service ranges only** (console, heal and conductor ids have no high-water and repeat;
     heal commands stay in `heal_commands`, their `<HL>` answers as today): range, verb,
     `command_json`, `kv` (JSONB, what we asked for), lane, sender (`admin` / `observed`),
     `external_system_id`, `created_at`, `sent_at`/`sent_status` (NULL until S6b's sender),
     `base_hash` (last reported hash at allocation, or `b`), ack fields (`ack_ok`, `ack_e`,
     `ack_k`, `ack_s`, `ack_d`, `ack_v`, `ack_h`, `ack_at`, `ack_raw`), `reverted_at`,
     `reverted_limit`, `superseded_by`. UNIQUE(device_id, command_id). BIGINT ids.
   - `config_snapshots` — hash (PK, 8 hex) → `kv` JSONB {key: value text},
     `first_seen_at`, `last_seen_at`, `first_device_id`. Fleet-wide: the hash is over the
     effective config only, so two units with the same settings share a hash (S5: both rigs
     580ce986). Merged: every `<CF>` item seen under hash H is a fact about H (a change
     summary lists only the changed keys; later `get`s fill in the rest).
   - `device_config_sightings` — append-only (device_id, hash, via = ack|cf|ws|start,
     seen_at = Sofar row time, ref = command id / media key / '', `sources` JSONB for a `<CF>`
     — sources are per unit, not per hash). UNIQUE(device_id, hash, via, seen_at, ref) —
     re-polls insert nothing. **No sighting from a `d:1` ack** (its `h` is the original hash
     and would roll "reported" back).
   - DDL: `CREATE TABLE IF NOT EXISTS` matching the ORM exactly (`db.py` also runs
     `create_all`); the FK to `devices` takes a brief lock on it, so `lock_timeout` first.
3. **Shared id allocator** `app/services/command_ids.py`: one range table (mirrors
   `command_wire.RANGES`); `allocate(db, device_id, range)` under the device-row lock (the
   heal pattern). `heal`: max(heal_commands) + 1 from 100 000, **refuses ≥ 1 000 000** (409
   with a loud log line). `remote`: 1 + the highest remote id known for the unit from ANY
   evidence — `device_commands` (ours and observed), acks, START `tg=`, `<CF @c<id>>` — and
   never below an optional `min_id` in the request (the operator's floor above ids the
   backend has not heard yet: GUI sends are invisible for 11–45 min, `"to":"con"` gets never
   ack over cellular). An explicit `id` is also accepted if it is in range and above every
   known id. Refuses past 99 999 999; a UNIQUE conflict with the worker's observed row →
   re-allocate once. The backend never allocates console, service (signing key is Nick's
   only) or conductor ids. `heal_commands.next_command_id` calls it (same ids as today: the
   allocator is a move, then the guard). Residual risk until H2: an id another tool sends
   in the same minutes can still collide; the unit then answers the first and refuses or
   duplicates the second, both visible in the log.
   Heal line cap → **256 B** (F5, the tighter lane) — ruling H3.
4. **Reply ingest** `app/services/command_reply_ingest.py`, wired next to `<HL>` in the worker
   and poll-once (try/except, never fails the poll): acks → `device_commands` (the row
   is created as `sender=observed` when the backend did not allocate the id — GUI, CLI,
   console-typed remote ids — so the log stays fleet-wide); `<CF>` → `config_snapshots`
   merge, `reverted=`/`err=` onto the command / a sighting; ack `h` (not `d:1`), `<CF> h`,
   `<WS> cfg`, START `cfg` → `device_config_sightings`. An ack updates a backend-allocated
   row as the answer. Dedupe: `external_ingest_events` payload hash
   `bm-ack:{node}:{row timestamp}:{text}` / `bm-cf:{node}:{row timestamp}:{text}` (the Sofar
   row time is stable across re-polls; a repeat from a later boot is a new fact, like `<HL>`'s
   wake key). Commit mode only writes; preview mode reports `would_create`.
5. **Desired vs reported** (pure `command_status.py` + admin endpoints, `require_admin`):
   - `POST /admin/devices/{device_id}/commands` `{"c": verb, …}` (+ optional `id` /
     `min_id`): shape check (the `command_wire` rules for an outbound command: verbs,
     charset, lists ≤ 4, **dotted key paths only** — short names are refused so the desired
     key matches the `<CF>` key; JSON ≤ **234 B** = a 256 B console line, until S6b measures
     the Sofar lane), allocate a remote id, record, return `command_json`, the console line
     and a paste-ready `sofar_send_command.py --id N --json '…'` line. **Records; never
     sends** (sending = S6b). Keys are not checked against the registry (the unit validates
     the whole config).
   - `POST /admin/devices/{device_id}/commands/{command_id}/sent` `{"http_status": 202}` —
     the operator (S6a) or the sender (S6b) marks it sent; `waiting`/`late` start here.
   - `GET /admin/devices/{device_id}/commands` — the log with a derived §6.5 status per
     command: `allocated` (not yet marked sent) · `waiting` (+ `expected_by`) · `late` ·
     `saved` (ack ok) · `awaiting_cfm` (ok on a guarded_revert key — list copied from the
     registry at 0f67784 with its source line; the unit reverts without a `cfm`) ·
     `in_effect` (a `<WS>`/START sighting of the ack's `h` after the ack) · `rejected`
     (+ `e`/`k`; `e:old` adds "re-issue with a new id") · `staged` (`s:1`, no ok `cfm` for it
     yet) · `reverted` (`<CF reverted ref=id>`) · `superseded` (unanswered, and a later
     command set the same key) · `answered` (get/ping/hld ok) · `triggered` (a `trg` whose id
     shows up as START `tg=`). A `d:1` ack keeps the original status.
   - Ordering uses the Sofar row timestamps (the Spotter's time, one clock per unit), never
     the backend receive time (exposure lag 11–30 min, batches).
   - `GET /admin/devices/{device_id}/config` — reported hash (latest sighting, time, via),
     its known kv, desired kv (our last unanswered-or-saved set/reset per key), pending keys,
     and `changed_elsewhere` (the hash moved to one no acked command of ours produced).
   - `GET /admin/config-snapshots/{hash}`.
   - `expected_by` = sent (or allocated) + `COMMAND_EXPECTED_S` (env, default 5400 s: hourly
     report + 30 min Sofar exposure lag). **Assumption**, calibrated in S6b from measured hops.

**S6a demo gate.** nvd CLAUDE.md accepts a Swagger demo. S6a's demo is **local**: uvicorn on
this Mac against a scratch Postgres (never staging), the recorded fixture payloads ingested,
and the Swagger calls with their real responses written into each PR. PRs open as drafts
because the staging demo cannot run before the loop ends; it runs in S6b step 1 without
pushing the PR heads (cherry-picked copies), so no open PR gets auto-marked merged.

## 2. Assumptions (labelled)

- **A1** `BM_KEYED_GROUPING` is ON on staging (heals complete there, S5 L10). Tests cover both
  modes either way.
- **A2** No acks or `<CF>` from bmcam001/002 (commands off, O1). Their wire is only START /
  END / chunks / `<WS>`; the regression test pins it.
- **A3** The Sofar API lane's line limit is unknown (docs say 270 B; F5 measured 256 B on the
  USB console). S6b measures it.
- **A4** `<CF>` value text is compared as wire text, so `1.0` vs `1` would read as a change;
  the unit always encodes with `cf_value` (`repr(float)`), and the backend encodes desired
  values with the same rule. A mismatch shows as "pending", never as a false "in effect".

## 3. Rulings needed

**H1 — PR shape.**

| option | pros | cons |
|---|---|---|
| **3 PRs (A, B, C) — recommended** | none stacked; C's commits review one concern each; A can merge first to unblock W9 | C is the big one (parsers + migration + ingest + endpoints) |
| 5 PRs (parsers, allocator, log separate) | smaller PRs | the log needs both parsers and allocator → two stacked PRs, can't merge before the loop ends (memory "Stacked PR merge order") |
| 1 PR | one review | W9 waits for the whole log; one revert undoes everything |

**H2 — who allocates remote-range ids.** The unit keeps one high-water per range, so every
sender must share one counter. Recommended: **the backend is the one allocator from S6b on**;
the GUI and `sofar_send_command.py` ask it (`POST …/commands`) instead of picking. Until then
(S6a) the backend allocates above every remote id it has evidence of, with an operator floor,
and tools keep their own ids. Alternative: split the remote range per sender (needs a unit
change: one high-water per sub-range — not proposed).

**H3 — heal line cap 270 → 256 B.** Heal lines go over the console today (F5). Recommended:
256 now in C; revisit in S6b when the Sofar lane is measured.

**H4 — table vs telemetry rows for replies.** Recommended: the three tables (status needs
joins by command id and hash; telemetry JSONB would need a scan). Acks/`<CF>` are NOT also
written as telemetry rows in S6a; logs.html showing commands and replies moves to **S6b step
7**, because the R5 gate says "every command visible on logs.html" (KICKOFF:64).

**H6 — fps for a START-lost video healed from `/M`.** Recommended: S6b follow-up in nvd that
takes `video.send.fps` from the unit's latest config snapshot (C), labelled on the row;
alternative: `rsd` asks for the header (the O11 alternative, a unit change).

**H5 — `in_effect` evidence.** Recommended: a `<WS>` or START `cfg` equal to the ack `h`,
seen after the ack. A `get` reply alone does not count (it is still the command channel).

## 4. Not in S6a

Sending anything (S6b); heal auto-send (S6b/R4); the web UI (S7); changing `wake_action`
names; bench-tool changes (they move to the allocator in S6b, H2); service-range allocation
or signing in the backend; a `/M` unit change (S4w, after A is merged, deployed and
verified on staging).

## 5. S6b — needs hardware (after the loop, with Nick; plan only)

Order: R3 → R4 → R5 (KICKOFF §3), with the S6a PRs merged and demoed first.

1. **Demo + merge S6a** (after 2026-09-29 22:30Z, Nick's go). The PRs are already open, so
   the staging test-push must NOT contain their head commits (a fast-forward marks an open PR
   merged, memory "Staging test-push vs PR order", #59): demo from cherry-picked copies (new
   SHAs) on a backup-tagged staging, then reset; Nick merges A, then B, then C (migration
   0016 runs on deploy; DDL only). Verify on the live bmcam003/004 rows; CAM_0003 regression
   check each time.
2. **S4w (W9) on the device**, after A is live: one device commit + reviewed golden diff;
   bmcam003 first, then bmcam004. Proof: a lost START is healed from `/M` alone.
3. **Sofar lane from nereus000 (R3)**: token in `~/.config/nereus/`, `sofar_send_command.py`
   there, send log; `tools/remote_latency_report.py` joins send log × console × backend
   → CSV `t_post, t_console, t_ack, t_media_complete`; the conductor's heal step switched to
   "send via Sofar" as the interim. Gate (R3): **10 heals via Sofar over ~10 h**, delivery %,
   p50/p95 per hop. Plus a few v9 `set`/`get` to time the ack lane. Calibrates
   `COMMAND_EXPECTED_S`.
4. **Nested `kv` over Sofar**: one `set` with two dotted keys and one `trg` with `kv`, sent
   through the Sofar API; the console shows the JSON byte-identical to what was posted.
5. **Sofar lane size limit (F5 question)**: send 256, 257, 270 B lines via the API; watch the
   console for `RX overflow`. Outcome sets the heal cap and `MAX_JSON_BYTES` guidance.
6. **One Sofar sender + heal auto-send (R4)**: backend Sofar command client with the
   gateway's `token_env_var`, 1 req/min/Spotter guard shared by heals and commands,
   `BM_HEAL_AUTOSEND=1`, `BM_HEAL_AUTOSEND_MAX_PER_DAY` (bench 96, production default 24),
   the candidate walk in the cron tick, a `heal_requested` event with `sent_via=sofar`; sets
   `sent_at`/`sent_status`; conductor's heal step off. Tools switch to the backend allocator
   (H2). Gate (R4): a real loss (queue stall) healed end-to-end by the backend alone, the
   `[heal]` line shows `received_age_s ≥ 600`, no double-issue.
7. **logs.html**: commands, acks, `<CF>` and their §6.5 status in the device timeline (R5's
   "every command visible on logs.html"); START `r m d` in the probe (1-char keys).
8. **24 h remote loop (R5)** on both rigs: conductor + backend auto-send; 0 clips lost, 0
   redundant heals, every command visible on logs.html with its §6.5 status; RESULTS.md
   (cycles, delivery %, latency per hop, heals issued/needed/redundant, stills vs clips).

Hands-off during the S5 loop applies to all of the above until it ends.

## 6. Test plan (S6a)

Every commit: the whole nvd assert suite green (36 today + the new files), the main-wire
§11 fixture unchanged, and any v9-fixture change re-recorded with its diff in the PR. C adds an offline migration test and a scratch-Postgres round trip
(local `pg_ctl`, never staging). Fixtures copied into `backend/tests/fixtures/s6a/` with their
source path and commit.

## 7. Review record

Independent reviewer (fresh context, read-only, 2026-09-28): **7 MAJOR, 10 MINOR, 3 NIT, no
BLOCKER**; "implementable after fixing 1–5 in S6a and 7–8 in the S6b plan". All folded in:

| # | finding | where fixed |
|---|---|---|
| 1 MAJOR | draft PRs + later staging push = the #59 auto-merge trap | §1 demo gate (local), §5 step 1 (cherry-picked copies) |
| 2 MAJOR | remote allocator blind to GUI / `to:con` / `tg` / `@c` ids | C3: all evidence + `min_id` floor + explicit id + retry; residual risk stated; H2 |
| 3 MAJOR | `d:1` ack carries the original hash | C2/C4: no sighting from `d:1` |
| 4 MAJOR | text compare (`8` vs `8.0`), short names never match `<CF>` keys | C1 typed compare; C5 dotted paths only |
| 5 MAJOR | M ≠ length must be a collision, not merged | A: excluded + counted |
| 6 MAJOR | START-lost video healed from `/M` has no fps | A known limit + H6 |
| 7 MAJOR | R5 needs commands on logs.html | H4 → S6b step 7 |
| 8 MAJOR | R3/R4 detail and gates dropped | S6b steps 3, 6 |
| 9 | `<CF err>` repeats per boot; text-only dedupe loses facts | C4: Sofar row time in the hash |
| 10 | console/heal/v8 ids repeat → UNIQUE breaks | C2: log holds remote + service only |
| 11 | sightings key lacks ref; sources are per unit | C2 |
| 12 | `@v8`/any source, cut items; port the poller's parsers | C1 + parity test |
| 13 | unkeyed `<I{n}/{M}>` must stay rejected | A |
| 14 | one regression JSON can't stay unchanged across B | A: frozen main-wire rows + reviewed v9 fixture |
| 15 | trg via `tg`, `e:old` hint, awaiting cfm, mark-sent | C5 |
| 16 | 248 B JSON vs 256 B line | C5: 234 B |
| 17 | `IF NOT EXISTS` (create_all), FK lock | C2 |
| 18–20 NIT | probe 1-char keys; 0011 reserved; `<WS> r=` dropped | B, C table, B |

Facts corrected: v8 `st` is an object; the GUI also reads the CLI send log; the `reverted`
sample is a unit log line. Not verifiable by the reviewer (no network): no open nvd PRs, the
36/36 baseline, the live loop — all checked in this session (`gh pr list`, local run,
conductor README).

## 8. S6a build record (2026-09-28)

Built as approved. Three draft PRs into nvd `staging`, none merged or deployed. No hardware,
Sofar or staging was touched during the S5 loop.

| PR | branch head | contents | suite |
|---|---|---|---|
| nickraymond/nereus-vision-dev#65 **A** | `feature/s6a-chunk-total` fc7da16 | §11 wire regression (parse + rows, main FROZEN / v9 REVIEWED, recorded at 03272be); `/M` parser; collision rows never healed | 40/40 |
| nickraymond/nereus-vision-dev#66 **B** | `feature/s6a-uplink-v9-fields` fecce87 | `<WS>` config_hash / uptime_s / wake_reason; START config_hash, trigger_id, resolved_crop / message_cap / duration_s | 37/37 |
| nickraymond/nereus-vision-dev#67 **C** | `feature/s6a-command-log` 7218fd2 | reply parsers (parity with sofar_poll_acks), migration 0016 (3 new tables), allocator (heal < 1e6, heal cap 256 B), reply ingest (per-message savepoint), §6.5 status + desired vs reported + admin endpoints | 43/43 |

- **Tests.** The DB tests run on a local scratch Postgres (`TEST_DATABASE_URL`, Homebrew on
  port 54329). They SKIP without it.
- **Local integration.** A local merge of A+B+C (never pushed) merges clean. There, the main
  wire is unchanged. The v9 parse gains 16 keys and the rows 86, only B's additive keys.
- **Merge order A → B → C.** After A merges, B gets one commit that re-records the v9 JSON.
  C's copies of `wire.py` / `s6a_db.py` are byte-identical to A's.
- **Independent code review** (fresh context): no BLOCKER.
  - A: 1 MAJOR + 1 MINOR, fixed.
  - B: clean.
  - C: 1 MAJOR + 6 MINOR + NITs, fixed except #6.
- **Deviation (review A #1).** A total-mismatch group keeps its own M rather than an unknown
  length. The collision row it produces is never healed (`key_collision`). Wire contract §14.6.
- **Residual C #6.** A `cfm` sent by another tool is not linked to its `set` until H2 moves
  the tools to the backend allocator (S6b).
- **Demo.** Local, recorded in each PR body. The staging demo is S6b step 1, done with
  cherry-picked copies of the commits.

## 9. PLAN_S6b_backend — steps 6 (code) and 7

Written 2026-09-29. Status: **DRAFT r2 (independent review folded in, §9.9), awaiting Nick.**
Scope: backend code only, nvd `origin/staging` a2721d3 (S6a #65/#66/#67 + heal fix #68 merged;
migration head `20260928_0016`; suite baseline **48/48** on this Mac with a scratch Postgres).
Built while the bench test runs, so:

- **No sending, no hardware.** No ssh to or command for bmcam003/004/nereus000, no Spotter
  `cmd.txt`, no Sofar request of any kind. Tests and the demo use a fake HTTP layer.
- **No merge into staging.** PRs open; Nick merges when the bench says so.
- **Everything new is OFF by default:** `BM_HEAL_AUTOSEND` and `BM_COMMAND_SEND` default `0`,
  with empty device lists. A merge changes no behaviour. It runs one DDL migration (a new table
  only), the tick summary gains a `heal_autosend: {"enabled": false}` key, and logs.html gains
  read-only rows.

### 9.0 Facts this rests on (checked in this session)

- **Sofar command API** (`docs/sofar_command_api_reference.md`, `tools/sofar_send_command.py`):
  - `POST https://api.sofarocean.com/user-rest/devices/<spotter>/command?token=…` with body
    `{"telemetry":"cellular","message":"<console line>"}`; `202` = queued in the mailbox, the only
    delivery signal.
  - The cellular mailbox never expires and runs commands in order on the next cellular transmit.
  - 1 successful request/min/Spotter; after a success, ALL requests are rejected until the
    cooldown ends. The documented line limit is 270 B incl. the final newline.
  - `runs/sofar_command_sends.jsonl` (56 records, 2026-07-27 → 2026-09-25): `?token=` auth
    works (51 × 202 on SPOT-33507C / SPOT-31593C). A cooldown rejection is **HTTP 400**
    `{"status":"bad request","message":"Too many requests, next send allowed at <date>"}` (3 ×).
    2 network errors (TLS). The tool labels every network error "nothing enqueued"
    (`sofar_send_command.py:357`); a timeout after Sofar enqueued is NOT excluded, so the
    backend treats it as `unknown`.
- **Tokens.** `external_gateways.token_env_var` names the env var per Spotter, resolved by
  `gateway_poll.resolve_gateway_token`. Per memory (not checked in the repo): SPOT-33361C
  (bmcam001/002, field) is on `SOFAR_API_TOKEN_AOML`; the bench Spotters are on
  `SOFAR_API_TOKEN_BM_REEF`.
- **`sofar_client._make_session` retries GET only.** A command POST must not use it: a retried
  POST after an enqueue is a second command.
- **Heals today.** `POST /admin/ingest/devices/{d}/heal-commands` allocates, packs and records.
  The conductor (heal_step every cycle since F10) and bm-heal-driver take their ids there and
  publish over the **console**; nothing sends via Sofar. `heal_commands.last_hl_action` is per
  command, not per key.
  - The per-key answers are `<HL>` telemetry rows. To read them, use the heal-events predicate
    (`message_type = heal_status` OR raw `LIKE '<HL %'`, because of the B20 rows, `main.py:2112`).
- **`<HL>` semantics** (`BM_Devel_Pi/rc_heal.py`):
  - `a=sent r=ok`: every asked chunk was sent.
  - `a=sent r=partial`: some were sent; the heal stays pending.
  - `a=dropped r=expired`: the unit gave up.
  - `a=refused` with `no_record | no_payload | range`: terminal for that key. A sha mismatch at
    send time is also terminal.
  - One `<HL>` per key per wake. The priority is `sent > dropped > refused > requested`, so a
    partial send in the same wake as the expiry reads as `sent r=partial`.
- **Command log (S6a C).**
  - `device_commands.sent_at` / `sent_status` are set only by `POST …/sent`.
  - `command_status` goes to waiting/late on any `sent_at` and ignores `sent_status`
    (`command_status.py:170`).
  - `POST …/commands` hard-codes `lane="sofar"` (`admin_commands.py:131`).
  - `<CF>` heads live in the cf sighting's `sources` as `_err _k _reverted _lim _ref _n`.
- **Two Render services** (cron `*/5` and web) with their own env, per the S6a deploy practice
  (not in the repo).
- **logs.html** merges `/systems/{id}/heal-events` (kinds `heal_request`, `heal_status`), also in
  non-BM mode. Commands, acks and `<CF>` are not shown. The probe's `KV_RE` needs keys of 2+
  characters.
- **`app/settings.py` runs `load_dotenv(backend/.env)`.** That file (main checkout) holds a
  production-like `DATABASE_URL` and a Sofar token. The worktree has no `.env`.

### 9.1 What gets built (two nvd PRs into `staging`)

| PR | branch | contents | migration | new behaviour on merge |
|---|---|---|---|---|
| **D** | `feature/s6b-sofar-sender` | commits: (1) send log + Sofar client + guard; (2) remote-command `…/send` + `lane` + `…/sent` logging; (3) heal auto-send walk + no-double-issue; (4) heal-events sent fields | `20260929_0017` (new table only) | none (flags off) |
| **L** | `feature/s6b-logs-commands` (from `staging`, independent) | `/systems/{id}/command-events`; logs.html commands / acks / `<CF>` + §6.5 status (incl. `send_failed`); START `r m d` in the probe | none | read-only UI rows |

D's commits depend on each other, so D is one PR with one commit per concern, each green (the
S6a C style, ruling S5). L is independent. R4's "logs.html shows it" means L merges before the
R4 gate.

### 9.2 D(1) — the send log, the Sofar client, the one guard

- **Migration `20260929_0017`** creates the new table `sofar_command_sends`. Nothing is ALTERed
  (ruling S1). The migration is DDL only:
  - `SET LOCAL lock_timeout = '5s'`, `CREATE TABLE IF NOT EXISTS` matching the ORM, no backfill;
  - offline test + scratch-Postgres upgrade/downgrade/upgrade.
  
  The table has one row per send attempt:
  - who and what: `spotter_id`, `device_id`, `kind` (`heal` | `command`), `command_id` BIGINT,
    `requested_by` (`autosend` | `admin` | `tool`), `token_env_var` (the name only), `message`,
    `message_bytes`;
  - timing: `created_at`, `completed_at`;
  - result: `http_status` (NULL until done, and NULL when unknown), `outcome`, `response`
    (≤ 2000 chars, token-scrubbed).
  
  Indexes: `(spotter_id, completed_at)` and `(device_id, kind, command_id)`. This is the
  backend's copy of the tool's `sofar_command_sends.jsonl`.
  - Heal "requested_by / sent via / sent at / status" are derived by joining on
    (device_id, command_id). A heal with no send row was requested by the API (console /
    operator).
  - So no ALTER on `heal_commands`: no lock on the table `<HL>` ingest updates, and no
    deploy-order risk for the cron ORM.
- **`app/services/sofar_commands.py`** (new, small):
  - `validate_message(line)`: printable ASCII, no tab, ≤ 270 B incl. newline (Sofar's rule). The
    tighter 256 B caps stay upstream in `heal_commands` / `command_outbound` (H3) until §5 step 5.
  - `post_command(spotter_id, token, message, *, transport)` makes **one** POST:
    - telemetry is hard-locked to `cellular`, and `clear_command_queue` is never set;
    - timeouts (5 s connect, 20 s read), **no retry adapter**;
    - the token is scrubbed from every exception text and response before anything is logged
      or stored (a `requests` error string carries the full URL).
    - `transport` comes from a FastAPI dependency (web) or a parameter (cron). Tests override
      it, and production code has no env switch to a fake.
  - **Three-phase send** (the POST never runs inside an open transaction):
    1. **txn 1** (commit):
       - `pg_advisory_xact_lock(hashtext('sofar-send:' || spotter))`;
       - the **guard**: the Spotter is busy when it has a send row within the last 65 s with
         outcome `sent`, `pending` or `unknown`, anchored on `completed_at` (`created_at` while
         pending), since Sofar's cooldown starts when it accepts;
       - the caller's re-checks (§9.3);
       - allocate the id and insert the command / heal row;
       - insert a send row `outcome=pending`.
       
       If the Spotter is busy: `rate_limited_local`, no row, no request, one log line.
    2. **POST**, with no transaction open. The heal / command row is already committed, so its
       id is never reused even if what follows fails.
    3. **txn 2**: update the send row: `completed_at`, `http_status`, `outcome`.
    
    A crash between 2 and 3 leaves `pending`. A `pending` older than 10 min reads as `unknown`.
  - Outcomes:
    - `sent`: 202;
    - `rate_limited`: 400 whose message starts "Too many requests", i.e. a sender we cannot see;
    - `rejected`: other 4xx;
    - `auth_failed`: 401/403;
    - `unknown`: network error, timeout or 5xx. The command may be in the mailbox.
  - **Retries:** never automatic. For heals, see §9.3. For remote commands, the operator calls
    `…/send` again with the same id (§9.2b).
- **Gateway checks for every send:**
  - the gateway must be `poll_enabled` and not `paused` (`current_gateway` can return a disabled
    row);
  - the device must be on its send allowlist;
  - a command is sent only to the Spotter it was recorded for.

### 9.2b D(2) — remote commands (H2, backend side)

- `POST /admin/devices/{d}/commands/{id}/send` sends the recorded `command_json` line. It
  refuses with **409** when:

  | reason | condition |
  |---|---|
  | `sending_disabled` | `BM_COMMAND_SEND != 1` |
  | `device_not_allowed` | `d` is not in `BM_COMMAND_SEND_DEVICES` (empty default) |
  | `gateway_changed` | the current gateway ≠ `row.external_system_id` |
  | `console_lane` | the row's lane is `console` |
  | `already_answered` | the row has an ack |
  | `newer_sent` | a higher remote id of the device already has `sent_at`: re-sending N earns `e:"old"`, so re-issue with a new id (DESIGN §6.5 "Late") |
  | `rate_limited` | the local guard is busy (with `retry_after_s`) |

  It uses only `sender=admin` rows. It sets `sent_at` / `sent_status` **only on 202**, or on
  `unknown` with `sent_status` NULL and a note. Failures live only in the send log, so a failed
  send never reads as "waiting".
- `command_status` gains **`send_failed`**: no `sent_at` and the latest send row for the id
  failed. It is used by `GET …/commands` and by L.
- `POST …/commands` accepts `lane` (`sofar` default | `console`).
- `POST …/sent`:
  - `http_status` may be null for the console lane;
  - a Sofar-lane 202 marked this way also writes a send row (`requested_by=tool`,
    `completed_at` = the given `sent_at`), so tool sends become visible to the guard.

### 9.3 D(3) — heal auto-send in the cron tick (R4)

- **Where:** `sofar_poll_worker.main`, **after** the processing phase, whose deadline is
  anchored at the tick start.
  - `run_heal_autosend(...)` has its own `SessionLocal` and a walk deadline (the tick budget
    left, at least 60 s), and commits per device.
  - It needs `--commit`; preview mode reports `would_send`.
  - It never raises and never fails the tick. The result goes in `summary["heal_autosend"]`.
- **Env (cron service only; the web side needs none, see "no double-issue"):**

  | var | default | meaning |
  |---|---|---|
  | `BM_HEAL_AUTOSEND` | `0` | `1` = the walk runs; anything else returns `{"enabled": false}` before any query |
  | `BM_HEAL_AUTOSEND_DEVICES` | empty | **required** comma list (e.g. `BMCAM_003,BMCAM_004`); empty = nothing is sent. Keeps field units out |
  | `BM_HEAL_AUTOSEND_MAX_PER_DAY` | `24` | send attempts (`sent` / `unknown` / `rejected` / `rate_limited` rows, not local guard hits) per device per rolling 24 h; bench 96. Spacing = 86400 / max (96 → 15 min) |
  | `BM_HEAL_AUTOSEND_REASK_S` | `5400` | how long a key stays in flight (= `COMMAND_EXPECTED_S`; an assumption until §5 step 3 gives p95 of send → `<HL>` at the backend) |

- **Per device, in order.** The first rule that stops wins. Each decision logs one
  `[heal_autosend]` line: device, decision, command id, keys, chunks, outcome, attempts in 24 h.
  1. No current gateway, or it is disabled or paused → `no_gateway`.
  2. **Other sender active:** an API-requested heal command (no send row) created within
     `REASK_S` → `other_sender_active` (loud).
  3. Attempts in 24 h ≥ max → `daily_cap`; the last attempt is younger than the spacing →
     `spacing`.
  4. `heal_candidates(db, d)` runs, with the S6a/#68 rules unchanged:
     - F9 truncation, `key_collision` never, still-arriving < 600 s never;
     - the `[heal]` lines log `received_age_s`.
  5. **Key filter** (per media key, from its latest heal command of any sender):
     - **in flight**: the command is younger than `REASK_S`, it was API-requested or its send
       was `sent` / `unknown` / `pending`, and no release came back for (that id, that key);
     - **release**: `<HL a=sent r=ok>` or `<HL a=dropped>`. `a=sent r=partial` does not release
       (the heal is still pending, and it can hide a drop);
     - **excluded**: any `<HL a=refused>` for the key. It is terminal, logged once per tick, and
       never auto-asked again (a person can still heal it by hand once autosend is off).
     
     Nothing left → `nothing_new`.
  6. Three-phase send (§9.2). txn 1 re-checks rules 2, 3 and 5 under the advisory lock, so a
     conductor POST or an overlapping tick in between cannot double-issue. Then it allocates
     (heal < 1e6, 256 B cap), packs and records.
  7. Outcome:
     - `sent` / `unknown`: the keys are in flight;
     - `rejected` / `rate_limited`: not in flight, re-asked at the next due slot (the heal row
       stays, its id is spent);
     - `auth_failed`: stops that Spotter for the tick.
- **No double-issue against a conductor** (KICKOFF §6). It is data-driven, so it holds even if
  only the cron env is set:
  - **web:** `POST …/heal-commands` answers **409 `autosend_active`** when the device has an
    autosend send row within `REASK_S`. The conductor's heal step then logs `backend_error`
    every cycle: visible, never silent.
  - **cron:** rule 2 (re-checked in txn 1).
  - Whichever sender is active holds the other off for `REASK_S`. Every heal sender takes its id
    from the backend, so two cannot be live.
- **"heal_requested, sent_via=sofar".** The `heal_request` rows of `/systems/{id}/heal-events`
  gain `requested_by`, `sent_via`, `sent_at`, `send_outcome` and `sent_status` (from the send
  log; null for API heals). The kind is not renamed: that would split the logs.html filter.
- **Why not F10's "fresh command every cycle".** F10 fixed a conductor that held ONE command
  for 3 cycles and starved new losses. The key filter is per key, so new losses go out at the
  next slot. Over Sofar, a re-ask can reach the unit after it already re-sent the chunks
  (exposure lag 11–30 min), which is a redundant heal (the R5 gate is 0).

### 9.4 L — logs.html (step 7)

- `GET /systems/{system_id}/command-events?device_id&start&end&limit&sort` (admin, the
  heal-events window rules):
  - kind `command`: `device_commands` rows whose created, sent or ack time falls in the window.
    Each carries:
    - its §6.5 status (incl. `send_failed`) from `command_status` over the device's whole log;
    - lane, sender, JSON, sends (from the send log), the ack (`ack_raw` + fields + time) and
      `expected_by`.
  - kind `config`: cf sightings in the window: hash, ref, head (`_err _k _reverted _lim _n`),
    and the snapshot kv of that hash.
- **logs.html:**
  - new type filters `command` and `config`, each fetched with its own try/catch like
    heal-events. They are fetched in every mode, as heal-events already is (stated on purpose;
    CAM_0003 gets empty lists, regression-checked);
  - badges by status:
    - green: saved, in_effect, answered, triggered;
    - amber: allocated, waiting, staged, awaiting_cfm;
    - red: late, rejected, reverted, send_failed;
    - grey: superseded, observed;
  - the detail panel shows the JSON / console line, sends, ack, hint and expected-by;
  - `heal_request` rows show "sent via sofar 202 HH:MM", "send unknown", "send failed <status>"
    or "console / operator".
- **What "every command" can cover:**
  - Shown: remote/service ids (backend-sent, tool-marked or observed).
  - Heals: shown via `heal_commands` + `<HL>`.
  - Conductor (2e9) and console ids: never reach the backend (console-only replies). They stay
    in the conductor's `events.jsonl`, and R5's RESULTS says so.
- **Probe:** a START-only pass that surfaces exactly `r`, `m`, `d`.

### 9.5 H2 — tools move to the backend allocator (bm_cam_legacy, a later tools PR)

The backend side is D(2), §9.2b. The tool changes are not in this session. Their shape:

- **`sofar_send_command.py --device BMCAM_00x --backend <url>`**:
  1. `POST …/commands` (allocate + record);
  2. `POST …/send` (the backend sends through the one guard and send log; the token stays on
     the server).
  
  The standalone `--id` mode stays, with a warning.
- **GUI** (`lifecycle.py:154`):
  - allocates via `POST …/commands` for both lanes instead of `max+1`;
  - the console lane then calls `POST …/sent` (`lane=console`, `http_status=null`);
  - the Sofar lane calls `…/send`.
- **Conductor:** its 2e9 ids are unchanged. It gets a `--no-heal` switch; the 409 above stops it
  meanwhile.
- **Until the tools switch,** the S6a rule stands (every piece of evidence + `min_id`).

### 9.6 Tests (no real Sofar; scratch Postgres per `backend/tests/README.md` §3)

- **Pure checks:**
  - `validate_message`, token scrubbing (no token in any row, log or exception text);
  - outcome classification: 202, the recorded 400 "Too many requests" body, other 400, 401,
    500, timeout, TLS error;
  - flag and allowlist parsing.
- **DB:**
  - migration 0017, offline test and round trip;
  - the guard: across kinds, `pending` / `unknown` count as busy, anchored on `completed_at`;
  - a crash between the POST and txn 2 (the id is not reused; `pending` → `unknown` after
    10 min);
  - the walk:
    - flag off → zero queries and zero writes;
    - empty allowlist, disabled gateway;
    - daily cap and spacing;
    - in flight / `r=ok` / `r=partial` / `dropped` / `refused` release rules;
    - B20-shaped `<HL>` rows;
    - `other_sender_active` and the in-lock re-check;
    - F9 truncation, `key_collision`, the heal id guard at 999 999;
  - `…/send`: every 409, including `newer_sent` and `device_not_allowed`;
  - `send_failed` status; `…/sent` send-log row; 409 `autosend_active`; the heal-events and
    command-events fields.
- **Whole suite:** every existing assert script passes (baseline 48/48), and any failure is
  compared against `origin/staging`. The §11 main-wire fixture is unchanged.
- **Demo:** FastAPI `TestClient` against the scratch Postgres, with the transport dependency
  overridden by the fake. The real responses go in each PR body.
  - The demo never runs uvicorn with a send flag set.
  - It refuses a DB URL that is not local `*test*` (the S6a guard) and never reads
    `backend/.env`.
  - The first real send is §5 step 6 on the bench.

### 9.7 Before anyone sets `BM_HEAL_AUTOSEND=1` (bench owner)

1. **§5 step 3**: 10 heals via Sofar over ~10 h. This gives delivery % and p50/p95 per hop.
   p95(POST → `<HL>` at the backend) sets `BM_HEAL_AUTOSEND_REASK_S` and `COMMAND_EXPECTED_S`.
2. **§5 step 5**: the Sofar lane's line limit (256 / 257 / 270 B). This keeps or raises the
   256 B heal cap and the 234 B command JSON cap.
3. **The token on the cron service.** The env var named by each bench gateway's
   `token_env_var` must be set on the **Render cron service** and accepted for command POSTs.
   All 202s so far came from Nick's shell.
4. **Cron env:**
   - `BM_HEAL_AUTOSEND=1`;
   - `BM_HEAL_AUTOSEND_DEVICES=BMCAM_003,BMCAM_004`;
   - `BM_HEAL_AUTOSEND_MAX_PER_DAY=96`;
   - `BM_HEAL_AUTOSEND_REASK_S` from step 1.
   
   The web service needs nothing for heals. `BM_COMMAND_SEND*` stays off until §5 step 4.
5. **Before the flip:**
   - the conductor heal step is off and bm-heal-driver is stopped (else the walk logs
     `other_sender_active` for `REASK_S` after their last heal);
   - step 3's interim "conductor heals via Sofar" is off;
   - no Mac CLI sends to those Spotters.
6. **Gate R4:**
   - a real loss is healed end-to-end by the backend alone;
   - `[heal]` shows `received_age_s ≥ 600`;
   - no `other_sender_active` after the flip;
   - 0 re-asked keys inside `REASK_S`;
   - L is merged (logs.html shows it).

### 9.8 Rulings needed

| # | question | recommended | alternatives |
|---|---|---|---|
| **S1** | where sends are recorded | send-log table only; heal sent fields joined (no ALTER on `heal_commands`) | + 4 `heal_commands` columns: simpler reads, but ACCESS EXCLUSIVE on a table `<HL>` ingest updates and cron/web deploy-order risk |
| **S2** | re-ask rule | per key: in flight until `<HL a=sent r=ok>` / `dropped` or `REASK_S`; `refused` excluded | F10 fresh-every-slot: faster, but redundant re-sends over Sofar lag |
| **S3** | which devices | required allowlists (`BM_HEAL_AUTOSEND_DEVICES`, `BM_COMMAND_SEND_DEVICES`) | every device with a gateway: would reach SPOT-33361C field units |
| **S4** | who sends remote commands | the backend `…/send` (off + allowlisted), plus tool sends marked via `…/sent` into the send log | allocate-only (H2 minimum): tools send and mark; no backend command sender until later |
| **S5** | PR shape | D (one PR, a commit per concern) + L independent | D1/D2 separate (D2 carries D1's commits anyway), or everything in one PR |

Not in scope: H6 (fps from the snapshot), the tool changes (§9.5), any send, any merge.

### 9.9 Review record (r1 → r2)

Independent reviewer (fresh context, read-only, 2026-09-29): **7 MAJOR, 8 MINOR, 1 NIT, no
BLOCKER** ("fix 1–7 before building"). All folded in:

| # | finding | where fixed |
|---|---|---|
| 1 MAJOR | POST inside an open txn: a rolled-back 202 reuses the id (unit dedupes → heal lost); device lock held 30 s | §9.2 three-phase send, per-device commit |
| 2 MAJOR | `unknown` treated as not in flight | §9.3 rule 5/7: in flight |
| 3 MAJOR | `<HL>` misread (partial, sent>dropped, terminal refused, per-command column, B20 rows) | §9.0 semantics, §9.3 rule 5 |
| 4 MAJOR | `…/send` could reach SPOT-33361C | §9.2 allowlist, gateway match, lane, disabled gateway |
| 5 MAJOR | failed send reads "waiting" | `sent_at` only on 202/unknown; `send_failed` |
| 6 MAJOR | re-send after a newer id earns `e:old` | 409 `newer_sent` |
| 7 MAJOR | local demo could hit real Sofar via `backend/.env` | §9.6 TestClient + dependency override |
| 8 | TOCTOU between checks and lock | re-check in txn 1 |
| 9 | token in `requests` error text | scrubbed + test |
| 10 | guard anchor; cap counting | `completed_at`; local hits not counted |
| 11 | web 409 needs web env | data-driven 409 |
| 12 | walk eats the processing deadline | after processing, own deadline, (5, 20) s |
| 13 | S1 missing send-log-only option; ALTER risk | S1 recommendation changed |
| 14 | S5/S4 options | S5 now D + L; S4 alternative restated |
| 15 | send-log date range; unverifiable facts | §9.0 corrected / labelled |
| 16 NIT | heal-events fetched in non-BM mode; summary key; R4 needs L | §9.4, header, §9.7 |

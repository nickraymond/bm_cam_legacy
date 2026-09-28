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

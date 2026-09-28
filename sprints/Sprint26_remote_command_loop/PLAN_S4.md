# Sprint26 S4 — new verbs (commands v9): implementation plan

Written 2026-09-27. Status: **APPROVED as proposed (Nick, 2026-09-27)** — G1–G17 ruled as written; S4w (W9) waits for the nvd parser.
Reviewed by two reviewers + one consensus round (§5); every finding is folded in below.
Branch `feature/sprint26-s4-verbs` from `origin/development` 43d4d03 (S3c PR #81 + follow-ups
PR #82 merged). Baseline on this Mac (.venv-dev, PyYAML 6.0.2, Pillow 12.3.0): **1270 passed**
(`tests/test_reference_card_color_utils.py` excluded).
Spec: DESIGN §4 (hold, keep-alive), §6.1–6.3, §8.2 W8/W9, §8.3 S4 row, §9, §10 O6/O7/O11, §11;
REVIEW_20260925 K1, K7, K8, K10, X1–X7, R4–R6; PLAN_S2 G1; PLAN_S3b H1, H5, H6, H12;
PLAN_S3c C2, C3/J1, C8, C16/R3, §6 #2–#3.

Every commit: legacy + per_boot supervisor + stay_on + save_local goldens,
`tests/test_config_v2_parity.py` and the full suite green BEFORE the commit. A commit that
changes any vector byte is a named W-item (wire) or V-item (vector bytes only, no wire change)
with the reviewed `tests/golden/` diff in the same commit. No hardware in S4 (gate = unit tests
+ tools smoke test; hardware proof is S5).

Facts this plan rests on (inspection at 43d4d03; file:line):
- **The command layer is shared by both runtimes.** One `CommandDaemon`, one `build_ack`
  (`command_messages.py:192`, full 11-index `st`), one v8 dispatch (`command_tables.py`).
  Every ack goes cellular (`command_daemon.py:535`); console output is only help/cfg text.
  `main()` builds one v8 `CommandState` for both runtimes (`rc_progressive_jpeg.py:1378`); its
  `save()` writes back the v2 fields it loaded at start (`command_state.py:160, 336`).
- **The runtime never sees the v2 overlay.** Boot runs on `cfg.base` (`config_v2.py:439`);
  v8 settings reach the runtime through `command_bindings` (`rc_progressive_jpeg.py:377`).
  `state["overlay"]` only feeds the hash, which is only logged. Cross-key rules run on BASE
  only (`parse_values`:161).
- **State v2** placeholders (`boot_counter / overlay / guarded / result_cache / high_water`)
  are read or written by nothing. `pending_trigger` is rebuilt as `{id, value}` on load
  (:194-206).
- **Burst path** runs the full `process_pending` in every pacing slot through ONE factory,
  `make_pending_pump_fn` (`rc_command_hooks.py:268-285`; used by rc_transmit, rc_video_tx,
  rc_heal, `send_pending_heals`). Only the ack send is deferred. No inbox exists.
- **Dedupe** = 32 `applied_ids`; a duplicate acks `ok:1` with the CURRENT `st`. No id ranges on
  the device; GUI floor 1000; `dev_mode.sh` writes `hlt` with epoch-second ids.
- **Registry** v4: guard classes are data only; short names `m`=mode.media, `r`=mode.run,
  `c`=still.crop (against O7); no keep-alive/hold/`bus_always_on` keys;
  `video.send.message_cap` 8–1000, no floor.
- **The supervisor per_boot goldens run on v1 config** (`run_scenario.py:449` adds
  `--runtime supervisor` to a v1 profile); only stay_on/save_local scenarios are v2-migrated,
  and their `summary.json` pins the migrated YAML and LKG bytes.
- **Restart wrapper** restarts only on exit 70/71 and counts every restart toward the 5-in-10-min
  crash-loop cap (`rc_run_capture_cycle.sh:16-24`); exit 0 = done.
- **W9 precondition NOT met.** nvd `origin/staging` (03272be): no `/M` parser (every chunk regex
  rejects `<I{key}.{i}/{M}>`), no branch or PR. The backend does not parse acks (staging grep).
  nvd reads START `st` as `sd_total_mib` — new START keys must avoid it.
- **Legacy bug found (not fixed, see §0 G17):** v8 `roi 5/6` clamps `output_size` in the
  bindings (`command_bindings.py:82`), but `prepare_source` uses `settings["output_width"]`
  (`rc_progressive_jpeg.py:752`) = 1000 > crop 800/640 → "no upsampling" raise.
- `docs/bmcam_command_reference.md` is hand-written, stale (tables v5); nothing generates it.
  `tools/bm_service_sign.py` does not exist.

## 0. Gaps needing a ruling (proposals; consensus of both reviewers)

**G1 — v9 vs "legacy byte-identical until S5".** Legacy shares the daemon and ack builder and
its goldens pin v8 acks, the help tail and v8 verbs, so item (11) and W8 can't also leave
legacy identical. **Proposal:** v9 is active only on the **supervisor action path of a migrated
unit** (state file `bm_command_state_v2`; NOT keyed on the config fallback level, so a unit
that fell back to lkg/v1_migrated still speaks v9 and can be repaired remotely). Legacy, the
video_logger path, and the supervisor on an unmigrated unit keep v8 verbatim, with one loud
line naming why. Item (11) becomes: v8 leaves the supervisor's dispatch (test: no
`command_bindings`/`command_help` import and no v8 dispatch on that path; `command_tables` is
still imported by the fold), the command reference is regenerated for v9, and the v8 files are
deleted with the legacy runtime after S5. Consequence: the 5 per_boot v1 command scenarios stay
legacy ≡ supervisor on v8 and keep the parity net; v9 is pinned by new v2 scenarios.

**G2 — the v8 state section.** **Proposal: copy, don't move.** The supervisor folds
`overlay_from_v8()` into `overlay` (journal source `migrate_v8`), records `v8_folded: <hash>`,
and then ignores `v8` (so `reset` works). If the `v8` hash later differs (a legacy rollback
edited it), it re-folds and the CHANGED v8 keys win over the overlay. Legacy reads `v8`
untouched. The fold leaves the effective config and hash unchanged for every `STATE_FIXTURES`
entry (test).

**G3 — the supervisor runs on base ⊕ overlay**, validated as a whole. Derived stills output
width = min(`still.output_width`, crop w) (DESIGN §5.1 derived values; fixes roi 5/6 on the
supervisor path). On boot-validation failure, only the overlay-sourced keys among the paths a
failing rule names are dropped (each `<CF err k=…>`); `power.halt.*` and guarded records are
kept unless they are themselves the failing keys. A new-rule failure on a BASE key is logged +
`<CF err>` only (base never bricks). LKG stays BASE.

**G4 — W9 is out of S4.** It becomes **S4w** (one device commit + reviewed golden diff) after an
nvd PR into `staging` (additive `/M`; M as length when START is missing; M ≠ length flagged) is
merged, deployed and verified. I can open that nvd PR in a separate session when you say so.

**G5 — short names (O7, value-typed per N8).** A command-side map next to the registry (help,
GUI and reference generated from it): `r`=`still.crop`, `m`=message cap of the media in play
(effective `mode.media` for `set`, the action's media for `trg kv`), `d`=`video.send.duration_s`,
`f`=`camera.focus.mode`, `b`=`camera.white_balance.mode`, `e`=`camera.exposure.ev`,
`o`=`mode.output` (S3c C8 one-shot; the KICKOFF output-width `o` was never built),
`med`=`mode.media`. `mode.run` and `c` lose their letters. `r` on a video action → `e:"key"`.

**G6 — `trg kv` allow-list:** `camera.*` controls (focus, white_balance, exposure,
image_processing, controls_enabled; not `camera.backend` / `camera.native.*`, which change the
sensor path — as built in a.1), `still.{crop,output_width,message_cap,
save.quality}`, `video.send.{duration_s,message_cap,size,fps,x264_preset}`,
`video.record.{fps,bitrate_mbps}`, `mode.media`, `mode.output`; `v:3/4` = one-shot
reference-image source (internal action key). Validated as a whole-config overlay on a
per-action copy; persisted only in `pending_trigger_v9` (top level, never the v8 section),
re-validated on load and at action time. `still.quality_ladder` is not remotely settable
while it has > 4 entries (strict-JSON list cap).

**G7 — one-shot echo.** The ack stays slim; resolved one-shot values + the `d≠5` flag go on the
console line; START carries `tg/r/m/d` (W8b). `hld` acks add `"v":<granted min>` (K10).

**G8 — rejections.** Ack `e` + `k` (first offending key); full sentence on the console; no
`<CF>` for a rejection (settles DESIGN §6.1's two statements; errata line after approval).
Codes: `id`, `cmd`, `key`, `val`, `xk` (cross-key/env), `lock` (locked key; `commands.runtime`),
`auth` (missing or bad `sig`), `old`, `cas`, `big`, `ref` (cfm of nothing pending), `err`
(state write failed, D15). Bad JSON / no id: no ack, console line only.

**G9 — duplicates.** Check order: range → result cache (original + `d:1`) → high-water
(`old`) → validate; a rejected id never advances high-water. A duplicate always gets the
console line; its cellular copy goes at most once per id per boot and once per 10 min
(monotonic) inside a stay_on process — so the mote's 60 s replay costs nothing. No high-water
reset verb in S4 (it would reopen signed-command replay): a stuck remote mark is passed with a
higher id, a stuck service mark by a field update.

**G10 — guards.**
- (a) Counters start when a value is IN EFFECT (first boot after, for NEXT_BOOT keys).
- (b) `mode.output: save_local`: **3 boots or 2 h of uptime since in effect** (uptime summed
  across processes and persisted in the guarded record; monotonic, not wall clock), because a
  save_local stay_on unit neither transmits nor reboots and a remote `cfm` takes 15–45 min.
  **Deviation from DESIGN §6.3's "2 transmitting actions" for this one key.**
- (c) `commands.runtime` is not command-settable (`e:"lock"`) until the legacy runtime is gone.
- (d) `mode.run` unguarded (a stay_on unit keeps listening).
- (e) new `power.bus_always_on: true` is guarded_stage (it lifts the hold clamp; wrong on a
  scheduled bus = hard power cut mid-hold).
- (f) `<CF reverted=>` always goes cellular (no remote id to key a lane on).
- (g) stay_on + a NEXT_BOOT key: the supervisor exits with a new code **72** ("config
  restart") at the next decision point, several sets coalesced; the wrapper restarts after 5 s
  and does NOT count it toward the crash-loop cap. Each process start is a "boot" for guard
  counting (a bad UART value reverts at boot 3, before the cap at 5 — tested).

**G11 — registry v5.** New keys `commands.keepalive_s` (300), `commands.keepalive_max_s`
(1800), `commands.hold_max_min` (120), `power.bus_always_on` (false), and the S3c C3/J1 rename
`video.storage.*` → `storage.*` in the same bump (old names accepted as aliases with a loud
line; `tools/config_v2_upgrade.py` rewrites them with every value and the hash unchanged —
re-migrating from v1 would drop v2-only edits; review S4a #5). Every v2 hash changes once (journal `deploy`). The inbox
path is derived next to the state file (not a key; locked by construction).

**G12 — `video.send.message_cap` floor 80** (the measured working value; 40 failed, nothing
between measured), in the whole-config validator and for a one-shot `m` on video, so it can be
lowered after a measurement without a registry bump.

**G13 — reply lanes.** Heal-range acks go console only (`<HL>` stays the cellular answer);
console-range acks go console only. The backend does not parse acks. New v9 scenarios use
remote-range ids so the cellular path stays pinned.

**G14 — pure validator.** `validate(effective, env) -> errors`; env facts (ffmpeg on PATH,
service key present, the configured + requested time zones resolvable) are probed by the
supervisor outside it. A test runs the validator with `subprocess`, `shutil.which`, `open` and
`zoneinfo` patched to raise.

**G15 — `help`, `get journal`, big answers.** Console only; `help` generated from the registry
+ short-name map, trg labels "2 = capture + output per mode" (closes S3c §6 #3). Cellular
`get` ≤ 3 `<CF>` parts, else `e:"big"`. `<CF>` source encoded `y` / `d` / `c<id>`.

**G16 — three PRs:** S4a pure foundations · S4b the supervisor speaks v9 · S4c W8b, tools,
reference. Plus S4w (W9) after the nvd parser.

**G17 — legacy `roi 5/6` bug** (above). Proposal: not fixed in legacy (byte-identical rule;
no field unit runs commands); the supervisor path is correct via G3. Say if you want the
one-line legacy fix instead (it would change no current vector).

## 1. Commits

### S4a — pure foundations (PR 1)

| # | commit | tests |
|---|---|---|
| a.1 | **V1 registry v5** (G11): new keys, `storage.*` rename + aliases, guard data (G10c/e), ONE_SHOT list (G6), short-name map (G5). | registry/coverage/migrate; S3c-era YAML loads to the same values; settings goldens identical; reviewed vector diff (migrated YAML + LKG bytes in stay_on/save_local summaries) |
| a.2 | **`command_wire.py`** (pure): strict parse (JSON ≤ 248 B **including** `sig`; finite numbers; no bool-as-int; duplicate keys rejected; strings `[A-Za-z0-9_:./+-]{1,48}`; lists ≤ 4 numbers; depth ≤ 2), verb shapes, id ranges, short-name resolve, canonical JSON, `verify_sig` (stdlib hmac, constant-time), slim ack, `<CF>` builder (charset-restricted), ASCII console line. | hostile table: NaN/Inf/-0/1e999, bool ints, dup keys, unicode digits, trailing `\n`, 248/249 B signed and unsigned, depth, 5-list, spaces, every range edge, sig flip/short/upper; fuzz round-trip |
| a.3 | **`config_validate.py`** (pure, G14): per-key + today's cross-key rules (moved; `parse_values` calls it, unchanged) + new rules (manual WB needs gains, derived output width, video block valid for video, cap ≥ 80, env facts) applied at set / effective boot / deploy-migrate strict. Rules return every path involved. | purity test; one test per rule; every profile valid; v2 parity unchanged |
| a.4 | **`command_state_v9.py`** (G1, B1): one owner of the whole file under the supervisor — overlay ops, `result_cache` 256, per-range `high_water`, `boot_counter`, `guarded` records (+ in-effect uptime), `pending_trigger_v9`, `pending_heals` + `applied_ids` API used by rc_heal, `v8` section passed through verbatim, fold/re-fold (G2). Atomic write + journal after persist. | v9 write → heal update → trigger consume → reload keeps all; kill-during-write; torn journal; dup returns original; 257th evicts; old/range isolation; fold hash-unchanged per fixture; re-fold precedence |
| a.5 | **`command_inbox.py`**: append raw (≤ 1 KB, 64 / 16 KB, drop-oldest + loud log, byte-identical payload dropped, fsync), torn-line tolerant read, per-entry clear after persist, file deleted when empty. | torn line, eviction, dedupe, bounds, delete-when-empty |
| a.6 | **`tools/bm_service_sign.py`**: signed console line; refuses JSON > 248 B. | round-trip with a.2 on a fixture key |

### S4b — the supervisor speaks v9 (PR 2)

| # | commit | tests / golden |
|---|---|---|
| b.1 | **Supervisor on effective config** (G2, G3), v9 state owner wired in (v8 CommandState only on the G1 v8 paths); v8 dispatch unchanged. `<CF err>` logged here, sent in c.1. | goldens identical (or V-item if a fixture moves); invalid-overlay boot keeps dry-run halt |
| b.2a | **Harness**: new v2 per_boot `v9_*` scenarios (still, video, heal, trigger-in-tail, mid-burst rsd), recorded on v8 first; stay_on/save_local v9 inputs prepared. | no vector change except the new dirs |
| b.2b | **W8a slim ack + lanes + console lines** on shared verbs (ping, help, trg, rsd). | reviewed diff: ack shape and lanes only, v2 dirs only |
| b.2c | **W8a' verb switch**: v9 dispatch, dedupe v2, high-water, `b` CAS; stay_on/save_local inputs switched to v9. | reviewed diff; legacy vectors untouched |
| b.3 | **`get`** (+ groups, `get journal`, `"to":"con"`, 3-part cap), `<CF>` through the one pacer + lane guard. | unit tests; a remote `get` in a v9 scenario |
| b.4 | **`set` / `reset`**: whole-config validation, all-or-none, persist then ack (D15), journal, `<CF>` summary of changed keys (remote/service ranges), applies at the next decision point; G10g exit 72 for NEXT_BOOT keys on stay_on; wrapper handles 72. | every rejection leaves hash + state bytes unchanged; persist failure → `e:"err"`, nothing changes in memory; set→reset returns the original hash; wrapper: 72 restarts, not counted |
| b.5 | **V2 guards + `cfm` + boot counter**: counter + revert after `resolve_runtime`, before the daemon/port; state loaded even with commands off; skipped for `--print-config`/non-`--transmit`; effective re-render to the same tmpfs path. Service keys: service-range id + valid `sig`, then guarded_revert. | per class: apply/confirm/revert by actions, boots, uptime (G10b), across restarts; stage then cfm; bad/missing sig; no key file; uart revert at boot 3 < cap 5; reviewed vector diff (counter in state files) |
| b.6 | **`trg kv`, `hld`, keep-alive + clamp**; `rsd`/`wap` regression under v9. | hash unchanged after `trg kv`; kv only in `pending_trigger_v9`; clamp on scheduled bus; `hld v:0`; wap fires once, not on dup |
| b.7 | **Durable inbox on the burst path**: `make_pending_pump_fn` only appends; the decision point drains all; `rsd` entries alone are also drained before heal planning and between END and `<HL>` (so a mid-burst rsd still rides this wake's `<HL>`). `dev_mode.sh` moves to v9 (takes the cycle flock or refuses; writes v8 `hlt` + overlay `power.halt.*` while legacy exists). | 70-command burst: 64 kept + logged; kill mid-burst → replays next boot; D15; mid-burst rsd golden unchanged vs b.6 |

### S4c — W8b, tools, reference (PR 3)

| # | commit | tests / golden |
|---|---|---|
| c.1 | **W8b**: `<WS>` `cfg=` + `up=`; START `cfg=` (core, never dropped) + `tg/r/m/d` on a triggered action (no `st`); `<CF err=>` (K7) and `<CF reverted=>` sent. Worst-case START ≤ 285 B test. | reviewed diff (supervisor v2 dirs only); field_*_main untouched |
| c.2 | **v8 out of the supervisor dispatch** (G1 test). | goldens identical |
| c.3 | **Tools**: GUI (floor 1e6, `verify_ack` on `h`, forms from the registry), `sofar_send_command` (v9, `--id` range-checked, 248/270 B), `sofar_poll_acks` (`h e k s d v`), `soak_reconcile` (`<CF>`/`<HL>`, slim ack), `soak_command_scheduler`, `bm_cmd_bench_listener`, hotspot skill text. | tools smoke test: every CLI `--help`, dry-run build of every verb, GUI server against a fake daemon |
| c.4 | **Generated command reference** (`tools/gen_command_reference.py` → `docs/bmcam_command_reference.md`) + freshness test. | doc-freshness test |

Before each PR: an independent reviewer (fresh context) on that PR's diff; every finding fixed
or answered in writing (PLAN_S3c §6 format).

## 2. Stage gate

Unit tests for every verb and rejection path + the tools smoke test; goldens green every
commit, legacy byte-identical throughout. No bench in S4; S5 is the console ladder.

### S4b additions ruled 2026-09-28 (Nick)

- **b.8 lane guard (in S4):** DESIGN §6.1's "every cellular send shares the lane guard" was
  not true before S4 (only the media burst was phase-planned). On the v9 path with
  `uplink.lane.enabled`, acks and `<CF>` (drain_acks), stay_on heartbeats and the `<HL>` lines
  now wait out the post-boundary guard (30 s) or a boundary within 2 s; the final ack flush may
  wait one guard. Phase = the last fresh Spotter UTC read, extrapolated; no read = no wait (D1).
- b.6 split: b.6a trg kv (same media), b.6b one-shot media override, b.6c hld + keep-alive (W12).

## 3. Not in S4

W9 (S4w, G4). R1 recorder under the supervisor. F1 residual (manifest O(clips)). The camera
GUI / local inbox writer (S7). Backend `<CF>`/ack parsers (S6). Deleting the legacy runtime
and v8 files (after S5, G1). Safe-minimal "commands on, halt dry-run" and a 5-key LKG
(DESIGN §5.1; today safe-minimal = "nothing to do", LKG = full base) — flag for S5 hardening.

## 4. DESIGN errata to apply after approval

§6.1: rejection reason rides the ack (`e`, `k`) + console, not `<CF>` (G8). §6.2: journal
source `migrate_v8`; START avoids `st`. §6.3: `mode.output` revert limit (G10b); exit 72
(G10g). §8.3 S4 row: (9) W9 → S4w; (11) v8 files deleted after S5 (G1).

## 5. Review record (2026-09-27)

Reviewer A (spec conformance): 18 findings (8 MAJOR). Reviewer B (code feasibility): 12 (2
BLOCKER: two state writers — fixed by a.4 single owner; supervisor goldens have no v2 — fixed
by G1's migrated-unit gate + new v2 scenarios). Consensus round 1: 27 resolutions, all agreed
with amendments (re-fold precedence, `<CF reverted>` always cellular, persisted uptime,
rsd-only drain at END→`<HL>`, per-path drop, zone probe scope, flock for dev_mode). Two new
MAJORs from the round, both folded: exit 70/71-only wrapper → code 72 outside the cap (G10g);
a high-water reset verb would reopen signed replay → dropped (G9).

### S4a independent review (2026-09-27, fresh-context reviewer on ca79437..8142d32)

0 BLOCKER, 1 MAJOR, 6 MINOR, NITs. Suite green at review (1347). Fixed in the S4a review commit:
1. MAJOR re-fold kept a key the v8 fold stopped producing (`hlt 3` → `hlt 0` left dev mode on):
   now removed unless a v9 command changed it; test.
2. D15 depended on callers: the in-memory mutators refuse to run outside `transaction()`.
3. A lost state file reset high-water (replay): re-seeded from the journal; service range closed
   until a field update.
5. G11 "deploy rewrites": no — `tools/config_v2_upgrade.py` (dry-run default, backup, hash
   check, journal `deploy`).
6. stale `overlay_ids` on a local/revert commit: cleared.
7. an inbox holding only damaged lines was never deleted: deleted.
NITs fixed: G6 text vs ONE_SHOT (text corrected), v8 trigger dropped loudly, `<CF>` UTF-8
escapes + no split `%XX` + 3-digit part counts, v1 reader defaults from the registry,
`Rejected` hashable with args, stable `pending_trigger` object (the supervisor compares by
identity). #4 fixed too: one pure `plan_fold()` is used by V9State and by
`config_v2.state_overlay`, so a reset of a folded key sticks and the logged hash matches.

### S4b independent review (2026-09-28, two fresh-context reviewers on daac449..88bdd3f)

Command layer: 1 BLOCKER, 5 MAJOR, 4 MINOR, NITs. Runtime integration: 0 BLOCKER, 4 MAJOR,
3 MINOR, 3 NIT (one overlapping). All fixed in the S4b review commit, each with a regression
test (tests/test_s4b_review_fixes.py) or a golden:
- BLOCKER: a guarded comms key on a trigger-only stay_on unit never reverted (no sends, no
  reboots) -> every guarded_revert key also reverts after 2 h of uptime in effect; the stay_on
  loop accrues idle uptime once a minute. (DESIGN §6.3 errata: tx2 / boot3 / 2 h backstop.)
- the final ack flush and the <HL> lines waited for the lane guard past the per_boot halt
  margin -> extended only within budget - TAIL_SAFETY_S (or bus_always_on / stay_on).
- an unsigned service-range id moved the service high-water (closing the range) -> any
  service-range id needs a valid sig (sig allowed on every verb); a signed reset of a service key
  needs a service-range id.
- the rsd-only drain could refuse an older stashed remote command e:old -> heal-range rsd only.
- a MISSING state file after remote activity reopened replay -> every ok remote answer journals
  an `hw` line; a missing file with such lines is treated as lost (re-seeded, service closed).
- a later unguarded set left a staged record a cfm could apply -> dropped; cfm re-validates.
- a failed media-override render re-picked every boot (no capture, no daemon) -> the trg is
  cancelled loudly and the normal action runs; `set mode.media video_logger` refused (it would
  leave the v9 path).
- next-action keys outside `settings` (clip config, recording block, save quality, storage,
  keep-alive limits) were frozen at process start -> re-read at every decision point
  (golden stay_on_video_set_d: a `set d 8` while idle shapes the next clip).
Minors/NITs: `m` resolves with a `med` in the same set; a duplicate hld from an earlier boot
answers v:0 "not active"; heal events rolled back when the rsd persist fails; the cellular d:1
copy goes once per process; a failure after an ok never becomes a second (err) answer; a revert
restores the value's command id; W10 sizes a longer one-shot clip; the action log shows a one-shot
output; guard uptime counted after the hold and before an exit; revert re-render falls back to
the YAML base; dev_mode.sh `off` writes hlt 0 before re-arming.
Verification round (both reviewers re-checked c0027e8): 17 of 21 fixed, 4 partial, 4 new
minors — all closed in the follow-up commit: the mid-cycle flush extends only if wait + its own
15 s fit before the halt margin; a duplicate hld is active only if answered in this process; no
second answer after any verb's ok; an unsigned service-range rejection is not cached (a forged
id cannot block the real signed command); `get journal` skips `hw` lines; the v9 re-resolve loads
the video config first (nothing half-updated on failure) and refreshes the W10 video minimum; a
save-quality set drained at boot applies to this action.

### S4c independent review (2026-09-28, fresh-context reviewer on 88bdd3f..8af6c60)

0 BLOCKER, 0 MAJOR, 4 MINOR, 7 NIT — all fixed in the S4c review commit: the `val` error text
(bad JSON / no id gets no ack at all); bm_service_sign points at `sofar_send_command --json`,
never `--raw-message`; the GUI retires (never re-sends) a pre-v9 log entry; the v8 reference for
legacy/field units is named (`git show 50e4586:docs/bmcam_command_reference.md`) with a
`--raw-message` example, in the doc and the hotspot skill; `--kv` refuses a key given twice; the
scheduler refuses at plan load what the GUI would drop; START `r=na` for a stored reference and
`m=` always on a triggered still; W8b fields also on a migrated unit with commands off; every part
of a multi-part `<CF>` is printed by the poller; the V9State marker is an attribute
(`is_v9`); a staged ack's cfm hint is a `note`, not a mismatch. Open (DESIGN l.90 shows
`up=.. cfg=..`; the unit sends `cfg=.. up=..` — order is irrelevant to the parser).

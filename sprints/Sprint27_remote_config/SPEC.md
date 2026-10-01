# Sprint27 — remote camera configuration from the backend (SPEC, r2)

Status: **r2** (2026-10-01): r1 reviewed by three independent reviewers; consensus fixes applied
(`REVIEW_r1.md`). Author: Claude session "remote config". Implementation: bm `claude/sprint27-remote-config`,
nvd `feature/s27-remote-config`.
Repos: bm_cam_legacy `development` 04a5b92 · nereus-vision-dev (nvd) `origin/staging` 715d3ef.
Constraint during writing: S6b HIL 24 h test running until ~2026-10-02 06:00Z → desk work only,
no staging merge, no PR into nvd `staging`, nothing sent to SPOT-33507C / SPOT-31593C.

Goal (Nick): **controls for every exposed still and video command**, from the backend, so the
frontend can plug in next and the controls can join a later overnight soak.

Labels: **MVP now** · **Next sprint** · **Future**.

---

## 0. Facts this spec rests on (verified in code)

Camera (bm_cam_legacy `development` 04a5b92):

| fact | source |
|---|---|
| Registry v5: 102 keys; guard classes none / guarded_revert / guarded_stage / locked / service; apply next_action / next_boot | `BM_Devel_Pi/config_registry.py:38-55, 141-400` |
| `set` is all-or-none, resolves short names, refuses LOCKED (`e:lock`), SERVICE without sig (`e:auth`), `mode.media=video_logger` (`e:lock`), validates the WHOLE effective config (`e:xk` only if a rule names a key being set) | `command_v9.py:424-517` |
| next_boot keys changed → stay_on restarts itself (exit 72) | `command_v9.py:568-576` records it; the exit is `rc_supervisor.py:1044-1051`, the restart `rc_run_capture_cycle.sh` (G10g) |
| Wire: JSON ≤ 248 B (270 B line), ASCII, strings `[A-Za-z0-9_:./+-]{1,48}`, lists 1..4 all-numbers or all-strings, kv ≤ 16 keys | `command_wire.py:50-60, 222-245, 304-308` |
| ⇒ the **empty string `""` cannot be sent** (default of `video.record.encoder.profile/level/denoise`); only a `reset` restores it | `command_wire.py:61`, `config_registry.py:282-290` |
| Ack `{"id","ok","h"[,e,k,s,d,v]}`; `<CF v=1 h= …>` change summary after a cellular-range ok set; config hash = sha256(canonical effective values)[:8] | `command_wire.py:391-478`, `command_v9.py:558-576`, `config_v2.py:203` |
| Video geometry no-upscale rule lives in `video_geometry.resolve_geometry`, run when the video config is LOADED at every start — **not** in `config_validate` (before Sprint27). A `set` that breaks geometry or the clip size is acked ok and the **next start exits 2 before the command daemon runs** (unreachable until SSH). Fixed by §3.4 | `rc_progressive_jpeg.py:1607-1625`, `video_geometry.py:304-401`, REVIEW_r1 A1 |
| Sent clip size must be even, ≥ 16 px, and not larger than the recording output (`refusing to UPSCALE`); registry `WXH` accepts odd sizes | `rc_video_tx.py:146-152`, `rc_video_clip.py:154-155`, `config_registry.py:626-629` |
| Exposure / WB / focus / image-processing flags are built only if `camera.controls_enabled` AND the group's `enabled` are true; WB gains present ⇒ `--awb custom --awbgains` (gains override the mode); shutter/gain ≤ 0 ignored | `rc_capture.py:387-474` |
| Video uses the same controls builder as stills | `video_recorder.py:44, 404` |
| bmcam001/002 (field) run `main` = legacy runtime, v8 verbs only: v9 `set` does not exist there | `docs/bmcam_command_reference.md` "Retired v8 verbs" |
| stay_on re-resolve FileNotFoundError: fix open as bm PR #97 (`fix/settings-reresolve-tmp`, 9336720), not merged | `gh pr list` 2026-10-01 |

Backend (nvd `origin/staging` 715d3ef):

| fact | source |
|---|---|
| Auth = shared bearer tokens only: `ADMIN_TOKEN` (`require_admin`), `VIEW_TOKEN` (`require_view_or_admin`); no users / orgs / per-device permissions | `backend/app/auth.py:42-85`, `models.py` |
| Dashboard = static vanilla HTML in `backend/dashboard/`, token from `?token=` | `index.html:1133-1138` |
| `POST /admin/devices/{d}/commands` checks wire shape (`command_outbound.build`: dotted paths only, service keys refused, JSON ≤ 234 B), allocates a remote id (per device, 1e6..99,999,999, above every known id), records a `device_commands` row; never sends, never splits | `admin_commands.py:126-182`, `command_outbound.py:26-152`, `command_ids.py:37-98` |
| `POST …/{cid}/send`: `BM_COMMAND_SEND=1` + `BM_COMMAND_SEND_DEVICES`; refuses `newer_sent` (sending id N after a higher id went out ⇒ `e:old`); shared 65 s/Spotter guard over `sofar_command_sends` (heals + commands + tool sends) | `admin_commands.py:220-304`, `sofar_commands.py:175-186` |
| Status per command (rejected, reverted, staged, awaiting_cfm, in_effect, saved, …, late, waiting, allocated); desired vs reported per key (`match / differs / unknown`) | `command_status.py:7-34, 251-277` |
| Backend holds **no registry** (only hard-coded GUARDED_REVERT / SERVICE_KEYS lists) | `command_outbound.py:6-7`, `command_status.py:47-52` |
| Old config paths `GET/POST /device/config`, `/system/config`, `/system/config/history` serve the **HTTP-camera pipeline** (legacy `device/system_agent`, dashboard `index.html` power/interval/features). BM units never call them | `main.py:1109-1615`, `device/system_agent/…/agent_loop.py:60-67`, `config_manager.py:72-97` |
| Highest migration 0017; Render runs `alembic upgrade head` on every deploy | `alembic/versions/20260929_0017_*` |

Delivery (kickoff, S6b HIL 2026-10-01; Sprint23): Sofar → Spotter 94 s … 20+ min indoors; the
camera dispatches between actions, measured send → ack 352 s when it landed mid-clip.

---

## 1. Shape of the solution (r2)

```text
config_registry.py ──gen──▶ docs/bmcam_config_catalog.json ──vendored──▶ nvd backend/app/vendor/
   (camera truth)            (generated, freshness test)                 (sha + selftest parity tests)
                                                                               │ validate (pure)
 frontend ──▶ GET  /remote-config/catalog                                      │
          ──▶ GET  /devices/{d}/remote-config          (eligibility, desired vs reported, statuses)
          ──▶ POST /devices/{d}/remote-config/plan     (dry run: refusals, warnings, the command)
          ──▶ POST /devices/{d}/remote-config/changes  (record ONE set or reset command)
          ──▶ POST /admin/devices/{d}/commands/{cid}/send   (EXISTING S6b sender, unchanged)
```

**One change = one command** (REVIEW_r1 row 4): one `set` (kv) or one `reset` (k ≤ 4 dotted
keys), JSON ≤ 234 B. Bigger → refused `too_big` with each key's byte cost so the UI can split it
into separate changes, each acked before the next is recorded. Multi-part changes are Next sprint
(ack-gated).

nvd files (all new; the S6b-owned files are untouched):

| file | role |
|---|---|
| `backend/app/vendor/bmcam_config_catalog.json` | byte-for-byte copy of bm `docs/bmcam_config_catalog.json` |
| `backend/app/services/config_catalog.py` | lazy load + sha check, `check_value` port, geometry port, typed `<CF>` text parsing (pure) |
| `backend/app/services/remote_config.py` | plan rules, eligibility, dead / pending / in-flight, ui_status (pure) |
| `backend/app/remote_config.py` | the router (DB reads, one `device_commands` insert) |

Imports from existing modules (read-only use): `command_ids.allocate_remote_id`,
`command_ids.REMOTE_LAST`, `command_outbound.build`, `command_status` (`Cmd`, `Sighting`,
`command_status`, `config_view`, `reported`), `sofar_commands` (`env_flag`, `env_devices`,
`sendable_gateway`, `sends_for`, `effective_outcome` — public names), `heal_commands.current_gateway`,
`auth.require_admin` / `require_view_or_admin`. The S6a `_load`/`_cmd` helpers are copied, not
imported. **No migration.**

## 2. Frontend-facing API (MVP now)

| endpoint | auth (Q1) | writes |
|---|---|---|
| `GET /remote-config/catalog` | view or admin | no |
| `GET /devices/{d}/remote-config` | view or admin | no |
| `POST /devices/{d}/remote-config/plan` | admin | no |
| `POST /devices/{d}/remote-config/changes` | admin + `BM_REMOTE_CONFIG=1` + `d ∈ BM_REMOTE_CONFIG_DEVICES` | one `device_commands` row |
| send: `POST /admin/devices/{d}/commands/{cid}/send` (existing) | admin + `BM_COMMAND_SEND=1` + `BM_COMMAND_SEND_DEVICES` | send log |

No path collides with existing routes (`/devices/{id}`, `/devices/{id}/media` are the only
`/devices/{id}…` routes; REVIEW_r1 B10). The catalog is loaded lazily: a missing or corrupt
vendored file answers 503 `catalog_unavailable` on these routes only and never stops the app
(B8). View-token responses carry no `ack_raw` and no Sofar `response` text (B10).

### 2.1 Catalog

The vendored JSON minus the selftest vectors. Per key: `path, group, section, type, default,
nullable, enum, range, choices` (framing / sensor-mode names), `unit, help, presets, apply`
(next_action / next_boot), `guard, guard_when, media, one_shot, tier, tier_reason,
blocked_values, requires` (enable switches that gate it), `limits` (backend clamps / warnings).

**Tiers (explicit allowlist, REVIEW_r1 row 2; new registry keys start blocked):**

| tier | keys | count |
|---|---|---|
| `control` (writable) | `mode.media/run/interval_s/heartbeat_s/output` · `schedule.timezone`, `schedule.window.*` · `camera.native.jpeg_quality`, `camera.controls_enabled` · `camera.focus.*` · `camera.white_balance.*` · `camera.exposure.{enabled,ev,shutter_us,analogue_gain}` · `still.{crop,output_width,quality_ladder,message_cap,budget_min}` · `video.record.{framing,crop,output,sensor_mode,fps,bitrate_mbps,encoder.*}` · `video.send.*` | 47 |
| `engineering` (shown, read-only in the MVP) | `camera.image_processing.*` (free strings / unranged into argv, video has no fallback), `camera.exposure.mode` (builds no flag), `still.save.quality` (save_local only) | 9 |
| `blocked` | LOCKED, SERVICE, guarded keys, `time.* power.* uplink.* commands.* network.* storage.* video.ui.* video.logger.*`, `camera.backend`, `camera.native.width/height` | 46 |

Blocked **values** of control keys: `mode.media=video_logger`, `mode.output=save_local` (cfm
flow = Next sprint), `""` for the encoder enums (cannot cross the wire: use `reset`).

**Backend clamps** (decided by Nick 2026-10-01, Q7):

| key | refuse above | warn above | note |
|---|---|---|---|
| `still.message_cap`, `video.send.message_cap` | 500 | 300 | Nick Q7 (2026-10-01) |
| `still.budget_min`, `video.send.budget_min` | 30 | 18 | Nick Q7 |
| `still.quality_ladder` | 4 rungs | — | wire lists ≤ 4 |
| `camera.white_balance.gains` | 8.0 each | — | Nick Q7; no registry upper bound; into rpicam argv |

Warnings: `mode.interval_s` / `heartbeat_s` below 600 s; `video.send.duration_s` ≠ 5 (only 5 s
ladder-validated).

### 2.2 Device view `GET /devices/{d}/remote-config`

```json
{"device_id":"BMCAM_003","gateway":"SPOT-33507C",
 "eligible":true,"why_not":[],"writable":false,"send_enabled":false,
 "reported":{"hash":"580ce986","seen_at":"…","via":"ws"},
 "keys":[{"path":"camera.exposure.ev","tier":"control","reported":-1.0,"reported_text":"-1.0",
          "source":"c1000501","desired":-1.0,"state":"match","command_id":1000501}],
 "commands":[{"command_id":1000501,"verb":"set","kv":{…},"status":"in_effect",
              "ui_status":"in_effect","dead":false,"sent_at":"…","ack":{…}}],
 "pending_unsent":[],"in_flight_keys":[]}
```

- **eligible** (REVIEW_r1 row 8 as amended by B): an ack or `<CF>` sighting at any age (only the
  v9 daemon emits those), AND a `<WS>`/START sighting within 30 days (alive), AND
  `commands.enabled` not reported false, AND `mode.media` not reported `video_logger`. A
  never-commanded v9 unit becomes eligible after one `ping` through the existing admin endpoint.
  bmcam001/002 on `main` have no v9 sightings → not eligible.
- **writable** = eligible AND `BM_REMOTE_CONFIG=1` AND the device is allowlisted.
- **send_enabled** = `BM_COMMAND_SEND=1` AND allowlisted AND `sofar_commands.sendable_gateway`
  (not `current_gateway`, which also returns paused gateways: B7).
- `keys`: every non-blocked catalog key with its reported value (typed from the `<CF>` text by the
  catalog type; `null` = never reported) and the existing `config_view` comparison, computed over
  the commands minus **dead** rows.
- **dead** (REVIEW_r1 row 5): `sent_at IS NULL` and a higher remote id of the device was sent,
  acked or observed — exactly the send endpoint's `newer_sent` refusal. Dead rows can never be
  sent; they are shown `expired` and ignored for desired / pending. No time-based expiry.

**ui_status** (from the existing §6.5 status):

| ui_status | status |
|---|---|
| `queued` | allocated (not dead) |
| `expired` | any unsent dead row |
| `sending` / `send_failed` | same |
| `sent` (+`late:true`) | waiting, late; also a `send_unknown` send (it has `sent_at`) |
| `saved` | saved (ack ok: v9 persists before acking) |
| `in_effect` | in_effect (a later `<WS>`/START carries the ack's hash) |
| `needs_cfm` | staged, awaiting_cfm (cannot come from this API: guarded values are blocked) |
| `rejected` | rejected (+ `e`, `k`, the catalog's error text) |
| `reverted` / `superseded` | same |
| `other` | triggered, answered, observed (raw status kept) |

### 2.3 Plan / change

Request: `{"set": {path: value, …}}` **or** `{"reset": [path, …]}`, plus optional
`"supersede": false`, `"lane": "sofar" | "console"` (console = the bench ladder, per the
console-first rule), `"min_id"` (above bench-GUI ids the backend has not heard yet: B4).

Response: `{"ok", "refusals":[{key, reason, why}], "warnings":[{key, why}], "command":{json,
json_bytes, kv}, "key_bytes":{path: bytes}, "command_id", "send": "POST /admin/…/send"}`. `plan`
returns the same with no id and writes nothing.

Refusals (422 unless noted): `empty`, `mixed` (set and reset), `unknown_key`, `not_writable` (tier),
`blocked_value`, `bad_value` (type / range / charset, INT must be a JSON int: A8), `over_limit`,
`cross_key`, `too_big`, `not_eligible` (+ `why_not`), `in_flight` (a key has a sent, unanswered,
not-dead command), `pending_unsent` (the device has any queued, not-dead admin row — any key, either
lane: B2; both refusals lifted by `supersede:true`, and the older rows go dead once the new one is
sent), 409 `no_current_gateway`, 403 `remote_config_disabled` / `device_not_allowed`, 503
`catalog_unavailable`. One transaction: `allocate_remote_id` under the device-row lock, flush,
commit; one retry on an `IntegrityError` with a worker-observed row (the S6a pattern).

## 3. Validation and safety

### 3.1 Wire

- Dotted paths only; JSON ≤ 234 B (`command_outbound.build`, unchanged, also enforces the wire
  shape). Measured: empty `set` 33 B (8-digit id); a key costs 20–42 B (+1 comma), a 48-char
  `schedule.timezone` ~70 B. ⇒ 5–6 keys per change. Exposure (5 keys incl. `controls_enabled`) =
  206 B fits; exposure + WB = 322 B → two changes. Video framing+crop+output+mode+fps+send.size =
  236 B → too_big (use `framing`, or two changes).
- `reset` takes catalog dotted keys only (no group prefixes), ≤ 4 per command.

### 3.2 The registry is the single source of truth

- bm `tools/gen_config_catalog.py` → `docs/bmcam_config_catalog.json` (registry v5, wire limits,
  video geometry tables, tiers, clamps, `sha256` of the body). bm tests: freshness, sha, every
  registry key with its registry fields, LOCKED/SERVICE blocked, still/video keys are controls,
  allowlist explicit, per-key `selftest` vectors == `check_value`, `geometry_selftest` (388
  cases) == the unit's own geometry rule.
- nvd vendors it byte-for-byte. nvd tests: sha, the `check_value` port over every `selftest`
  vector, the geometry port over every `geometry_selftest` vector, and that the catalog's
  guarded / service keys equal the hard-coded `command_status.GUARDED_REVERT(_WHEN)` and
  `command_outbound.SERVICE_KEYS` (B11). Updating = copy the file, run the tests.
- The unit stays the authority (it re-validates the whole config and answers `xk`).

### 3.3 Backend cross-key rules (refuse only when every input is known: REVIEW_r1 row 7)

"Known" = in this change, or reported (`<CF>` snapshot of the current hash). Otherwise → a warning
naming the unknown inputs; the unit decides.

| rule | refuse / warn |
|---|---|
| still.crop / video.record.crop inside the native frame (native size known) | refuse |
| WB `manual` needs gains | refuse |
| interval / heartbeat 0 or ≥ 60 | refuse (single key) |
| mode.media video ⇒ video.send.message_cap ≥ 80 | refuse |
| `mode.run=stay_on` needs `power.bus_always_on` **reported true** and `commands.enabled` true (fail-safe: unknown = refuse) | refuse |
| video geometry resolves (port of `resolve_geometry`; framing-less crop xor output refused) | refuse |
| video.send.size even, ≥ 16 px (always), ≤ resolved record output (geometry known) | refuse |
| clamps (§2.1) | refuse / warn |
| a gated control whose `requires` switch is false or unknown | warn |
| WB mode set while gains are set (gains win: `--awb custom`) | warn |
| video.send.fps > resolved record fps | warn |

### 3.4 Camera-side change (MVP now; mandatory before any remote video control)

DONE in bm `b7729af`: `config_validate._rule_video_geometry` and `_rule_video_send_size`
(strict + effective scope), pure, in memory, any exception caught (TypeError for crop-xor-output
included), stdout swallowed; violations name `mode.media` and the keys involved, and apply only
when `mode.media` is video (geometry: also video_logger).

- A bad remote `set` (or `trg kv`, which uses the same validator) now gets `e:xk`; nothing stored.
- A unit already holding a bad overlay recovers at its next start: `supervisor_config.resolve`
  drops overlay keys named by an effective-scope violation (G3) — once this code is on the unit.
- Pinned by `tests/test_s27_video_rules.py`: the rules refuse **exactly** what the real v1 video
  loaders (and the clip fit's upscale refusal) refuse, over 602 effective configs (bench bmcam003/004
  configs + defaults × every framing, sensor mode, fps edge, crop/output combination, odd / tiny /
  uppercase WxH, every video key at its range edges); dispatcher answers `e:xk`; the bench configs
  pass unchanged; the boot drops a stored bad overlay.
- Strict deploy load now also refuses such a YAML (it would exit 2 today on a video unit).
- Known limitation (code review r3 #1): the G3 boot drop removes every overlay key a violation
  names, so a stored bad `video.send.size` + `mode.media=video` also drops a valid stored
  `video.record.fps`; the unit stays on stills, reachable, and reports it (`<CF err>`). Fail-safe;
  narrowing it is a `supervisor_config` change (Next sprint if wanted).

### 3.5 Safety summary

- Only allowlisted control keys; blocked values; clamps; eligibility; two allowlists (config
  write, send). Field units (AOML token) stay off both lists and are not eligible anyway.
- Rate limit: every send goes through the existing 65 s/Spotter guard shared with heals; a config
  send delays a heal by at most one cron tick (it is not counted as a heal attempt: B9). When the
  guard refuses, the existing endpoint answers 409 `rate_limited` + `retry_after_s`; the operator
  retries.
- Ordering / conflicts: one command per change; `pending_unsent` and `in_flight` refusals; dead
  rows shown `expired`. A forgotten queued row blocks new changes until `supersede:true` (stated).
- Revert / last-known-good: `reset` (back to the unit's deploy YAML). "Undo to the previous
  values" = Next sprint (needs a change table).
- Mid-clip: dispatch happens between actions (measured 352 s send → ack mid-clip). next_action
  keys apply at the next capture; next_boot keys (`mode.media/run/interval_s/heartbeat_s/output`)
  restart a stay_on unit (exit 72) or apply at the next per_boot wake. The catalog's `apply` field
  drives the UI text.
- stay_on: bm PR #97 (re-resolve FileNotFoundError) must be on the unit, else a set may not take
  effect.

## 4. Per-key hardware risks to verify on the bench (ladder, after R5)

| key(s) | risk | check |
|---|---|---|
| `video.record.*` | Sprint15 FOV / upscale defect; now refused at set time, never verified via a remote set | `[VID]` describe lines + a clip frame vs a still at the same crop |
| `video.record.fps` | clamped to the sensor mode max (4608x2592: 14 fps) | `[VID]` fps note |
| `video.send.size/fps/duration_s` | message-budget fit | START `r=` / `d=`, clip plays |
| `camera.exposure.*` | no effect unless `controls_enabled` + `exposure.enabled`; rpicam-vid flag parity assumed | capture metadata `requested_*`; visible change |
| `camera.white_balance.*` | gains override the mode | metadata + colour |
| `camera.focus.*` | lens_position only in manual | metadata `LensPosition` |
| `still.crop` / `output_width` | width derived ≤ crop w (G3) | START `r=`, image size |
| `still.quality_ladder`, `camera.native.jpeg_quality` | size / time budget | message count, cycle time |
| `mode.media` still↔video | next_boot: stay_on restarts (exit 72) | `<WS>` media, next START type |
| `mode.run` / `interval_s` | needs a held bus | supervisor log |
| two changes queued in one wake | delivery order at the unit (`e:old` if reversed) | both acked ok |

## 5. Test plan

1. **Desk (done in the PRs):** bm: `tests/test_s27_*` + the full suite. nvd: assert-script tests,
   no-DB (catalog parity, plan rules) and `*_db.py` on a local scratch Postgres
   (`TEST_DATABASE_URL`, `fixtures/s6a_wire/s6a_db.py` harness; never staging; never bare pytest),
   incl. refusals, flags, allowlists, eligibility, dead / pending / in-flight, and that the existing
   admin send accepts the recorded row with a fake transport.
2. **Daytime command ladder (after R5; Nick frees ONE bench unit):** gate = bm #97 AND `b7729af`
   deployed on the unit, else the ladder does not start. Per key: change (console lane first) →
   ack → `<CF>` → one capture checked by eye → reset. Then one Sofar-lane change per group. Record
   in `runs/s27_ladder_<date>/`.
3. **Overnight soak (Next sprint, after the ladder):** scheduled config changes on one unit, kept
   apart from loss measurements.

## 6. Scope labels

| item | label |
|---|---|
| camera rules (video geometry, send size) | MVP now — DONE, deploy after R5 |
| catalog export + vendoring + parity tests | MVP now |
| nvd router: catalog, device view, plan, changes (one command) | MVP now |
| sending via the existing admin send endpoint | MVP now (exists) |
| frontend controls page | Next sprint |
| multi-part (ack-gated) changes; cfm flow for guarded keys; change table + undo | Next sprint |
| backend send queue in the cron (with the S6b owner) | Next sprint |
| engineering keys writable (per key, with ranges) | Next sprint |
| "capture now with these settings" (`trg` + one-shot kv) | Q6 |
| per-user auth; capability field in `<WS>` | Future |
| v8 field units on `main` | out of scope (they get v9 by a release) |

## 7. Open questions for Nick

- **Q1 auth:** reads with VIEW_TOKEN, writes with ADMIN_TOKEN + `BM_REMOTE_CONFIG_DEVICES`. OK?
- **Q2 tiers:** the 47 / 9 / 46 split above (`docs/bmcam_config_catalog.json` has the per-key
  reason). Anything to move?
- **Q3 guarded keys:** `mode.output=save_local` waits for the cfm flow (Next sprint). OK?
- **Q4 camera rule:** done and tested; it changes `set` answers (`e:xk`) and the strict deploy load.
  OK to deploy with the ladder?
- **Q5 nvd PR:** branch pushed; PR opened only after R5 closes. OK?
- **Q6:** a "capture now with these settings" button (`trg` + one-shot kv) in scope next?
- **Q7 clamps:** video message cap ≤ 200, budgets ≤ 30 min and WB gains ≤ 8.0 each are proposals. Values?

---

## 8. Build record (2026-10-01, desk only; bench untouched)

| repo | branch | commit | what |
|---|---|---|---|
| bm_cam_legacy | `claude/sprint27-remote-config` (from development 04a5b92) | see PR | camera rules, catalog generator + JSON, tests, this spec |
| nvd | `feature/s27-remote-config` (from staging 715d3ef) | see `git log` (cbf2920 + review r3 fixes) | router, catalog / planner services, vendored catalog, tests. **Pushed, NO PR** (R5 rule): open after Nick closes R5 — body in `PR_nvd_body.md` |

Tests run:
- bm: `tests/test_s27_video_rules.py` (7 tests, 602 parity cases), `tests/test_s27_config_catalog.py`
  (8), `tests/test_s4_command_reference.py`; full suite 1601 passed. Pre-existing on development
  (not this change): `tests/test_remote_latency_report.py` 4 fail (its `.log` fixture is gitignored,
  never committed); `tests/test_reference_card_color_utils.py` needs numpy (not in requirements-dev).
- nvd (scratch Postgres 127.0.0.1:54339 `nereus_s27_test`, local `pg_ctl`): `test_s27_config_catalog`
  (3480 check_value + 388 geometry vectors, guard parity), `test_s27_remote_config_plan`,
  `test_s27_remote_config_db` (end to end incl. the existing admin send with a fake transport,
  401 without a token, 503 on a bad catalog); regression: every `test_s6a_*`, `test_s6b_*`,
  `test_migration_0016/0017_*` OK; `tests/processing/test_no_cv2_at_import.py` +
  `test_api_no_db.py` 25 passed.
- Local demo (TestClient on the scratch DB, fake Sofar): `demo_local_20261001/*.json` + the script.

Not tested: anything on hardware; the real Sofar lane (S6b H5 still pending); the Render deploy.

Deploy notes: no migration; new env flags default OFF (`BM_REMOTE_CONFIG`,
`BM_REMOTE_CONFIG_DEVICES`); the vendored catalog ships in the repo. With the flags off, merging
changes nothing for existing routes (GETs only read; POST /changes answers 403).

---

## 9. Nick's rulings (2026-10-01, relayed by the EM session) and the image-control scope change

| Q | ruling | status |
|---|---|---|
| Q1 | approved; keep two roles (viewer = no admin token, admin = admin token, may send) with the check in ONE place for future user roles | DONE: nvd `backend/app/remote_roles.py` (cfabf1a) |
| Q4 | ship the camera video safety rule in R1 | in bm #98 |
| Q5 | nvd PR after G1 | waiting for the EM's word |
| Q2 | **scope change:** all 7 `camera.image_processing.*` keys → control, with validated ranges / enums taken from rpicam ON THE UNIT; refused outside them on the camera AND the backend; a bad value must never lose clips. `camera.exposure.mode`, `still.save.quality` stay read-only | planned below; blocked on a bench probe |
| Q3, Q6, Q7 | to follow | — |

### 9.1 Image-processing controls: plan (R1)

Today (`rc_capture.py:484-527`): with `camera.controls_enabled` + `camera.image_processing.enabled`,
`sharpness/contrast/saturation/brightness` go to `--sharpness/--contrast/--saturation/--brightness`
as floats (unranged in the registry), `denoise` to `--denoise <text>`, `hdr` true → `--hdr auto`,
text → `--hdr <text>`. Stills retry WITHOUT camera controls on a failure (`rc_capture.py:681-690`);
**video has no such fallback** (`video_recorder.py`), so a value rpicam-vid rejects loses every clip.

Hazards to settle on the bench before any range is written down (no values from memory):

1. **Ranges / enums** of each control as THIS unit's rpicam-apps + libcamera accept them.
2. **Duplicate flags on video:** `video.record.encoder.denoise/sharpness` (`video_recorder.py:426-429`)
   and `camera.image_processing.denoise/sharpness` both emit `--denoise` / `--sharpness` on the
   same `rpicam-vid` command. Whether a repeated option is an error is unverified.
3. **HDR on the IMX708** may change the available sensor modes; the video geometry pins an explicit
   `--mode` (`video_geometry.mode_argument`). Whether `--hdr` + each `--mode` starts is unverified.

Work (after the probe P0 in `LADDER.md`):

| step | repo | est. |
|---|---|---|
| encode the measured ranges / enums in `config_registry` (`range=` on the 4 floats, `enum=` on denoise / hdr; REGISTRY_VERSION 6), regenerate reference + catalog | bm | 1.5 h |
| camera cross-key rules for hazards 2 and 3 as the probe dictates (e.g. refuse both denoise flags on a video unit; refuse hdr with a mode it cannot run) + parity tests | bm | 1.5 h |
| **video fallback:** retry `rpicam-vid` once WITHOUT camera-control args when it exits non-zero at start (the stills behaviour), logged `[VID][WARN]`; tests with a fake runner | bm | 3 h |
| catalog tiers: the 7 keys → control; re-vendor; backend tests | bm + nvd | 1 h |
| ladder steps (IP1–IP5) + handoff docs | bm | 0.5 h (done) |

Desk total ≈ 7.5 h after the probe; bench: P0 probe ≈ 1 h (Test Engineer), IP ladder ≈ 1.5 h.
Recommendation: include the video fallback — it is the only change that makes "never lose clips"
hold for values rpicam accepts at parse time but fails at run time.

### 9.2 Remaining rulings (2026-10-01, second relay)

| item | ruling | status |
|---|---|---|
| video retry without camera controls | YES | DONE (bm): `video_recorder.record_one_clip` retries an encode that failed WITH camera-control flags once without them; `requested_controls.controls_dropped`; tests `TestControlsRetry`. Cost: one more clip duration on that action. Ladder IP6 |
| Q3 `mode.output=save_local` | next sprint | unchanged (blocked value) |
| Q6 capture-now button | next sprint | — |
| Q7 limits | message caps warn > 300 / refuse > 500 (still + video); budgets warn > 18 / refuse > 30 min; WB gains ≤ 8.0 | DONE: catalog + backend (nvd bd54bac) |
| rollout | per-Spotter settings on `external_gateways` (self_heal / remote_commands / link / heal cap) replace the `*_DEVICES` env lists incl. `BM_REMOTE_CONFIG_DEVICES`; built by the S6b backend session; the Fri staging demo still uses the env | eligibility interface agreed with that session (below) |
| P0 probe | Test Engineer after G1; stand-alone | `LADDER.md` PART 1 |

### 9.3 Per-Spotter rollout: agreed with the S6b backend session (2026-10-01; its plan pending Nick's OK)

- The S6b backend session owns `backend/app/services/rollout.py`:
  `allows(db, device_id, capability: "remote_commands" | "self_heal") -> (bool, reason | None)`.
  - It reads the device's current gateway.
  - It answers False on no gateway, a disabled or paused gateway, the flag off, or link=iridium
    (the sender is cellular-only).
  - It reads no env.
- Order:
  1. This nvd PR merges first, after G1, still gated by env. The Friday demo runs on the env.
  2. Their PR rebases onto it and replaces the two env-list checks here (`_writable` →
     `BM_REMOTE_CONFIG_DEVICES`, `send_enabled` → `BM_COMMAND_SEND_DEVICES`) with
     `allows(db, device_id, "remote_commands")`. The diff will be in their PR body for this
     session to review.
- **This session does not edit those lines.**
- `BM_REMOTE_CONFIG` stays as the global kill switch, as Nick and the EM asked.

# Sprint27 — remote camera configuration from the backend (SPEC, r1)

Status: **r1 draft for review** (2026-10-01). Author: Claude session "remote config".
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
| next_boot keys changed → stay_on restarts itself (exit 72) | `command_v9.py:568-576` (G10g) |
| Wire: JSON ≤ 248 B (270 B line), ASCII, strings `[A-Za-z0-9_:./+-]{1,48}`, lists 1..4 all-numbers or all-strings, kv ≤ 16 keys | `command_wire.py:50-60, 222-245, 304-308` |
| ⇒ the **empty string `""` cannot be sent** (default of `video.record.encoder.profile/level/denoise`); only a `reset` restores it | `command_wire.py:64`, `config_registry.py:282-290` |
| Ack `{"id","ok","h"[,e,k,s,d,v]}`; `<CF v=1 h= …>` change summary after a cellular-range ok set; config hash = sha256(canonical effective values)[:8] | `command_wire.py:391-478`, `command_v9.py:558-576`, `config_v2.py:203` |
| Video geometry no-upscale rule (crop × sensor mode vs output) lives in `video_geometry.resolve_geometry`, run when the video config is LOADED for an action — **not** in `config_validate`, so a `set` that makes geometry invalid is acked ok and fails at the next video action | `video_geometry.py:304-401`, `config_validate.py:197-212` |
| Sent clip size must be even and not larger than the recording output (`refusing to UPSCALE`); registry `WXH` accepts odd sizes | `rc_video_tx.py:146-152`, `rc_video_clip.py:154-155`, `config_registry.py:578-581` |
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

## 1. Shape of the solution

```text
config_registry.py ──gen──▶ docs/bmcam_config_catalog.json ──vendored──▶ nvd backend/app/vendor/
   (camera truth)            (generated, staleness test)                  (parity test: same sha256)
                                                                               │
 frontend ──▶ GET  /remote-config/catalog                                      │ validate + pack
          ──▶ GET  /devices/{d}/remote-config          (desired vs reported, command status)
          ──▶ POST /devices/{d}/remote-config/plan     (dry run: checks + packed commands)
          ──▶ POST /devices/{d}/remote-config/changes  (allocate + record N set/reset commands)
          ──▶ POST /admin/devices/{d}/commands/{cid}/send   (EXISTING S6b sender, unchanged)
```

Nothing in the S6b-owned files changes (`sofar_commands.py`, `admin_commands.py`,
`heal_autosend.py`, `command_events.py`, logs.html). The new router only **imports**
`command_ids`, `command_outbound`, `command_status`, `heal_commands.current_gateway`.
**No migration** in the MVP (rows go into the existing `device_commands`).

---

## 2. Frontend-facing API (MVP now)

Router `backend/app/remote_config.py`, new paths (no `/admin` prefix; nothing collides with the
old singular `/device/config`).

| endpoint | auth (proposal) | writes |
|---|---|---|
| `GET /remote-config/catalog` | view or admin | no |
| `GET /devices/{d}/remote-config` | view or admin | no |
| `POST /devices/{d}/remote-config/plan` | admin | no |
| `POST /devices/{d}/remote-config/changes` | admin + `BM_REMOTE_CONFIG=1` + `d ∈ BM_REMOTE_CONFIG_DEVICES` | `device_commands` rows |
| send: existing `POST /admin/devices/{d}/commands/{cid}/send` | admin + `BM_COMMAND_SEND` + allowlist | send log |

**Auth (Nick decides, Q1).** There are no users today. Proposal: reads = view token, writes =
admin token, plus a **separate per-device allowlist** for writing config
(`BM_REMOTE_CONFIG_DEVICES`), independent of the send allowlist. Real per-user / per-device
permission = Future (needs a users table).

### 2.1 Catalog `GET /remote-config/catalog`

The vendored JSON (§4) plus `backend_rules` (the cross-key checks the backend runs). Per key:

```json
{"path":"camera.exposure.ev","group":"camera.exposure","section":"camera","type":"float",
 "default":null,"nullable":true,"range":[-8.0,8.0],"enum":[],"unit":"EV",
 "help":"Exposure bias (EV).","presets":[["-1 EV",-1.0]],"apply":"next_action",
 "guard":"none","guard_when":null,"media":null,"one_shot":true,
 "tier":"control","requires":["camera.controls_enabled=true","camera.exposure.enabled=true"],
 "wire":{"sendable_values":"all but \"\"","bytes":26}}
```

- `tier`: `control` (still / video / camera / mode / schedule: what the UI shows),
  `engineering` (shown behind an "advanced" switch), `blocked` (never accepted; reason given).
- `blocked` = LOCKED, SERVICE, guarded_revert keys, guarded values (`power.halt.enabled=true`,
  `power.bus_always_on=true`, `mode.output=save_local`) and `mode.media=video_logger` — the cfm
  flow is Next sprint (Q3).
- `unit` is a small table in the generator (s, min, us, px, EV, fps, Mbps, %, GB); never invented
  beyond what the key's help says.
- `requires` lists the enable switches that gate a key (from `rc_capture.py:387-474`).

### 2.2 Device view `GET /devices/{d}/remote-config`

```json
{"device_id":"BMCAM_003","writable":false,"why_not":["BM_REMOTE_CONFIG off"],
 "speaks_v9":true,"gateway":"SPOT-33507C","send_enabled":false,
 "reported":{"hash":"580ce986","seen_at":"…","via":"ws"},
 "keys":[{"path":"camera.exposure.ev","reported":"-1.0","source":"c1000501",
          "desired":-1.0,"state":"match","command_id":1000501,"ui_status":"in_effect"}],
 "commands":[{"command_id":1000501,"verb":"set","kv":{…},"status":"in_effect",
              "ui_status":"in_effect","reason":null,"sent_at":"…","ack_at":"…"}]}
```

- `speaks_v9` = the device has at least one config sighting (`<WS cfg=>`, START `cfg=`, ack
  `h`, `<CF>`): only the v9 supervisor emits those. A device with none (bmcam001/002 on `main`)
  is **not writable** (`not_v9`). Assumption: this evidence is enough; a deploy back to legacy
  would leave stale sightings (Future: a capability field in `<WS>`).
- `keys` = existing `command_status.config_view` + catalog values, for every catalog key (a key
  the backend never heard is `reported: null, state: unknown`).
- `ui_status` (the per-command lifecycle the UI shows) maps the existing §6.5 status:

| ui_status | from command_status | meaning |
|---|---|---|
| `queued` | allocated | recorded, not sent |
| `sending` / `send_failed` / `send_unknown` | same | backend send in progress / refused / no answer |
| `sent` | waiting, late (+`late:true`) | Sofar 202, no ack yet |
| `saved` | saved, staged, awaiting_cfm | ack ok: persisted on the unit (v9 persists before acking) |
| `in_effect` | in_effect | a later `<WS>`/START carries the ack's hash |
| `rejected` | rejected (+ `e`,`k`, human text from the catalog's error table) | |
| `reverted` / `superseded` | same | |
| `expired` | allocated/late and a newer id was sent (can never be sent: `e:old`), or no ack after `REMOTE_CONFIG_EXPIRE_S` (default 86 400 s, **assumption**) | |

Per key, "reported" = the `<CF>` value matches desired (`state: match`); per command,
"in effect" = heartbeat hash after the ack. Both are shown; neither is invented.

### 2.3 Plan / change `POST /devices/{d}/remote-config/{plan|changes}`

Request:

```json
{"set":{"camera.exposure.enabled":true,"camera.exposure.ev":-1.0},
 "reset":["still.crop"], "supersede":false, "cas":false}
```

Response (plan = same shape, ids are placeholders and nothing is written):

```json
{"ok":true,"warnings":[{"key":"camera.exposure.ev","why":"camera.controls_enabled is false on the unit: saved but not applied"}],
 "parts":[{"part":1,"command_id":1000502,"verb":"set","kv":{…},"json_bytes":171,
           "send":"POST /admin/devices/BMCAM_003/commands/1000502/send"}],
 "send_order":"in command_id order, one per ≥65 s (shared Spotter guard)"}
```

Refusals (422, `reason` + per-key list): `unknown_key`, `blocked_key`, `bad_value` (type/range/
charset), `cross_key` (§3.3), `not_v9`, `in_flight` (a key already has a queued/sent unanswered
command; pass `supersede:true` to accept that the older one will expire), `too_big` (a single
group does not fit 234 B: cannot happen with the current registry — tested), `no_current_gateway`
(409). 403 `remote_config_disabled` / `device_not_allowed` for `changes`.

---

## 3. Packing, validation, safety

### 3.1 Packing (MVP now)

- Limit: the backend's existing **234 B JSON** (= 256 B console line, S5 F5;
  `command_outbound.MAX_JSON_BYTES`) — tighter than the unit's 248 B. Dotted paths only (short
  names would not match `<CF>` keys). kv ≤ 16 keys.
- Measured (id 8 digits): an empty `set` is 33 B, 48 B with `b`; a key costs 20–42 B (+1 comma)
  (`video.send.fps:10` 20 B … `video.record.framing:"stills_roi_1000p"` 42 B). ⇒ **5–6 keys per
  command.** Exposure group (5 keys incl. `camera.controls_enabled`) = 206 B; exposure + WB
  (8 keys) = 322 B → 2 commands.
- **Atomic groups** never split (all-or-none on the unit must cover keys a cross-key rule ties):
  1. `still.crop` + `still.output_width`
  2. `video.record.{framing,crop,output,sensor_mode,fps}` (+ `mode.media` when set together)
  3. `video.send.{size}` with group 2 when both are in the change
  4. `camera.white_balance.{enabled,mode,gains}`
  5. `camera.focus.{enabled,mode,lens_position}`
  6. `mode.media` + `video.send.message_cap`
  7. `mode.run` + `mode.interval_s` + `mode.heartbeat_s`
  8. `schedule.window.{enabled,start,end}`
  Every other key is its own group. `camera.controls_enabled` and each `*.enabled` switch go in
  the **last** part, so values land before the switch that activates them (a half-applied change
  is then "values saved, not active", never "active with the old values").
- First-fit in a fixed order (registry order of each group's first key); `reset` keys go in a
  separate `reset` command (`"k"` ≤ 4 names per command).
- Ids: one `allocate_remote_id` per part, consecutive, in one request; each part a normal
  `device_commands` row (`sender=admin`, `lane=sofar`). Parts are independent all-or-none
  commands on the unit; a rejected part does not undo an earlier saved one (the UI shows it per
  part). Between two parts the unit may run an action with only part 1 applied — accepted for the
  MVP, labelled in the response.
- `b` (compare-and-set): only on part 1 and only with `cas:true` (default off): the backend cannot
  predict the post-part-1 hash (the unit hashes the full effective config, `config_v2.py:203`, and
  snapshots are partial), so parts 2..N never carry `b`.

### 3.2 Validation: the registry is the single source of truth (MVP now)

- `tools/gen_config_catalog.py` exports `config_registry` (+ the wire limits from `command_wire`,
  `video_geometry.SENSOR_MODES` / `PRESETS` / `MAX_ENCODE_*` / `UPSCALE_SLACK_PX` /
  `FPS30_BLOCK_ABOVE_PIXELS`, `config_validate.VIDEO_CAP_FLOOR`, the error-code table, the tier
  table) to `docs/bmcam_config_catalog.json` with `registry_version` and a content `sha256`, and is checked by
  `tests/test_s27_config_catalog.py` (stale ⇒ fail, like the command reference).
- nvd vendors the file byte-for-byte at `backend/app/vendor/bmcam_config_catalog.json`; a test
  checks its embedded sha256 and that the backend checker agrees with the catalog's own
  `selftest` vectors (value, expected ok/reason) generated from the camera's `check_value`.
  Updating = copy the new file + run the test (one command in the README section).
- Backend per-key check = a port of `config_registry.check_value` (types, ranges, enums,
  charset) + wire charset (`""` refused with "use reset").
- The unit stays the authority: it re-validates the whole config and can still answer `xk`.

### 3.3 Cross-key checks on the backend (MVP now)

Evaluated on `effective = reported known values ⊕ this change` (reported values come from
merged `<CF>` snapshots; missing ones fall back to the registry default **only for the rule
input**, flagged `assumed_default` in the warning):

| rule | camera source | backend |
|---|---|---|
| still.crop inside native frame | `config_validate._rule_crop` | refuse |
| video.record.crop inside native frame | `_rule_video_crop` | refuse |
| WB manual needs gains | `_rule_manual_wb` | refuse |
| interval / heartbeat 0 or ≥ 60 | `_rule_intervals` | refuse |
| mode.media video ⇒ message_cap ≥ 80 | `_rule_video_cap_floor` | refuse |
| stay_on needs commands.enabled + supervisor | `_rule_supervisor_modes` | refuse if known false |
| **video geometry no-upscale** (crop × sensor mode ≥ output; output ≤ encoder max; 30 fps blocked above 1280×720) | `video_geometry.resolve_geometry` | refuse (port of the arithmetic, data from the catalog) |
| **video.send.size even and ≤ resolved record output** | `rc_video_tx.py:146-152`, `rc_video_clip.py:154` | refuse |
| video.send.fps > resolved record fps | (frames duplicated by ffmpeg `fps=`) | warn |
| fps above the sensor mode max | `clamp_fps` (camera clamps, loud) | warn |
| a camera.* value whose `requires` switches are false/unknown | `rc_capture.py:387-474` | warn |
| WB mode set while gains are set (gains win: `--awb custom`) | `rc_capture.py:410-421` | warn |
| analogue_gain / shutter_us = 0 (ignored by the builder) | `rc_capture.py:449, 461` | warn |

### 3.4 Camera-side change (MVP now, small, bm_cam_legacy PR)

Add `_rule_video_geometry` and `_rule_video_send_size` to `config_validate.s4_rules` (strict +
effective scopes) so the unit itself refuses a `set` that would break the next video action
(today: ack ok, then the action fails). Rules run only when `mode.media` is `video` or
`video_logger` (send size: `video` only); they call `video_geometry.resolve_geometry` with the
same key mapping `config_v2` renders (`framing→preset, crop→crop_native_xywh, output, sensor_mode,
fps`). Effects to check (regression list in the PR):
- `supervisor_config.py:110` drops an overlay value that now fails (G3 boot behaviour) — the
  intended outcome for a bad remote set.
- `config_v2.py:152` strict load (deploy/migrate) refuses a YAML whose geometry is invalid —
  that YAML already fails at every video action today.
- Bench units' current configs must pass (test with the S6b HIL configs in `runs/`).
Deploying this to a bench unit waits for the bench (after R5).

### 3.5 Safety (MVP now unless labelled)

- No LOCKED / SERVICE / guarded keys or guarded values (tier `blocked`).
- Writes need `BM_REMOTE_CONFIG=1` + device allowlist; sending needs the existing
  `BM_COMMAND_SEND=1` + `BM_COMMAND_SEND_DEVICES`. Field units (AOML token) stay out of both lists
  until Nick adds them; `not_v9` blocks them anyway.
- **Rate limit:** every send goes through the existing `SC.send` guard (65 s/Spotter, shared with
  heal auto-send and tool sends). A multi-part change takes ≥ 65 s per part. Heals and config
  compete for the same minute: the guard is first come first served (Next sprint: priority).
- **Ordering:** parts must be sent in id order (`newer_sent` refuses an older id). The UI sends
  part N+1 after part N's 202; it never sends a later change before an earlier one's parts.
- **Conflicting pending sets:** a change touching a key with a queued/sent unanswered command is
  refused `in_flight` unless `supersede:true`; superseded unsent rows then show `expired`.
- **Revert / last-known-good:** MVP = `reset` (back to the unit's deploy YAML: the
  last-known-good the unit itself holds) per key or "reset all I changed". Next sprint: "undo"
  = re-set the values reported before the change (needs a change table + migration).
- **Mid-clip:** the unit dispatches between actions (measured 352 s send→ack mid-clip).
  next_action keys apply to the NEXT capture; next_boot keys (`mode.media/run/interval_s/
  heartbeat_s/output`, `camera.backend`, `time.source`, `network.default`) restart a stay_on unit
  (exit 72) or apply at the next per_boot wake. The catalog's `apply` field drives the UI text.
- **stay_on caveat:** until bm PR #97 is deployed, a `set` in stay_on may not take effect (the
  re-resolve FileNotFoundError). The command ladder needs #97 on the unit first.

---

## 4. Per-key hardware risks to verify on the bench (ladder, after R5)

| key(s) | risk | check |
|---|---|---|
| `video.record.crop/output/sensor_mode/framing` | Sprint15 defect: rpicam-vid picks the sensor mode from --width/--height; video_geometry now passes `--mode` explicitly; never verified via a remote set | `[VID]` describe lines in the log + frame of the clip vs a still at the same crop |
| `video.record.fps` vs sensor mode max | clamped loudly (4608x2592 caps 14.3 fps) | log `[VID]` fps note |
| `video.send.size/fps/duration_s` | message budget fit; odd sizes refused (backend + new camera rule) | START `r=` / `d=`, clip plays |
| `camera.exposure.*` | no effect unless `camera.controls_enabled` + `camera.exposure.enabled`; rpicam-vid flag parity with rpicam-still assumed | `requested_*` fields in the capture metadata; visible exposure change |
| `camera.white_balance.*` | gains override mode | metadata + colour |
| `camera.focus.*` | lens_position only in manual | metadata `LensPosition` |
| `still.crop` / `still.output_width` | output width derived ≤ crop w (G3) | START `r=`, image size |
| `still.quality_ladder` / `camera.native.jpeg_quality` | size/time budget | message count, cycle time |
| `mode.media` still↔video | next_boot: stay_on restarts (exit 72); per_boot next wake | `<WS>` media, next START type |
| `mode.run` / `mode.interval_s` | stay_on needs commands.enabled; restart | supervisor log |

---

## 5. Test plan

1. **Unit (MVP now, desk):** bm: catalog generator + staleness + selftest vectors; new camera
   rules (geometry, send size) incl. the S6b HIL configs; full `tests/` green. nvd: catalog
   parity; per-key checker vs selftest vectors; cross-key rules; packing (every group fits;
   worst-case change; order; switch-last); router tests with `TestClient` against a **scratch
   Postgres** (`TEST_DATABASE_URL`, local `pg_ctl`; never staging) incl. refusals, allowlists,
   `not_v9`, `in_flight`, and that the existing admin send accepts the recorded rows.
2. **Local demo (desk):** uvicorn + scratch Postgres + recorded fixtures; plan → changes → (fake
   transport) send; screenshots/JSON into the PR.
3. **Daytime command ladder (after R5, Nick frees the bench, ONE unit, e.g. bmcam003):** bm #97 +
   this PR's camera rule deployed; per key: set → ack → `<CF>` → one capture visually checked →
   reset. Console lane first (per the console-bm-pub rule), then one Sofar send per step. Record
   in `runs/s27_ladder_<date>/`.
4. **Overnight soak (Next sprint, only after the ladder):** scheduled config changes on one unit,
   kept separate from loss measurements (one variable at a time).

---

## 6. Scope labels

| item | label |
|---|---|
| catalog export + vendoring + parity tests | MVP now |
| camera rules: video geometry + send size at `set` time | MVP now (deploy after R5) |
| nvd router: catalog, device view, plan, changes (no migration) | MVP now |
| sending via the existing admin send endpoint, UI drives parts | MVP now |
| frontend controls page | Next sprint (API is ready for it) |
| cfm flow for guarded keys (mode.output save_local, halt) | Next sprint |
| backend send queue in the cron (parts, priority vs heals) — touches heal_autosend | Next sprint (with the S6b owner) |
| change table + "undo to previous values" | Next sprint |
| per-user auth / per-device permission | Future |
| capability field (v9, registry version) in `<WS>` | Future |
| v8 (field units on `main`) config | out of scope: field units get v9 by a release |

---

## 7. Open questions for Nick

- **Q1 auth:** reads with VIEW_TOKEN, writes with ADMIN_TOKEN + `BM_REMOTE_CONFIG_DEVICES`. OK?
- **Q2 tiers:** `control` = still / video / camera / mode / schedule; everything else
  `engineering` behind an advanced switch. Should engineering keys be writable at all in the MVP?
- **Q3 guarded keys:** blocked until the cfm flow (Next sprint). OK to defer `mode.output=save_local`?
- **Q4 camera rule:** ship the video-geometry / send-size refusal on the unit (changes `set`
  answers and strict deploy load), or backend-only for now?
- **Q5 nvd PR:** opened only after R5 closes (branch pushed tonight). OK?

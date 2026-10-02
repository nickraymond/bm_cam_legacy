# Sprint27 → config UI (Release R1 gate G2): the API contract

For the nvd UI session. Source of truth: `SPEC.md` r2 §2. Code: nvd `feature/s27-remote-config`
(`backend/app/remote_config.py`). Real responses: `demo_local_20261001/*.json`.

**Catalog change coming (Nick Q2, 2026-10-01):** the 7 `camera.image_processing.*` keys move
from `engineering` to `control`, with `range` on sharpness / contrast / saturation / brightness
and `enum` on denoise / hdr, once the limits are measured on a unit. **Build every form from the
catalog's `tier`, `type`, `range` and `enum`, and never hard-code a key list**, so the UI picks up
the change by re-reading the catalog (check `sha256` / `registry_version`). `camera.exposure.mode`
and `still.save.quality` stay read-only.

**Roles (Nick Q1):** viewer (view token) reads; admin (admin token) plans, records and sends. The
check lives in nvd `backend/app/remote_roles.py`. In the UI, hide the write controls without the
admin token.

**Contract freeze (R1):** the paths, request fields and the response fields listed below are stable
through R1. Additions only; anything else is announced to the EM first.

## Run it locally before staging has it

```bash
cd ~/Documents/GitHub/nereus-vision-dev && git fetch && git worktree add ../nvd-ui-s27 origin/feature/s27-remote-config
```

Then a scratch Postgres (local, `*test*` name) and `backend/tests/test_s27_remote_config_db.py`
(it builds a device with sightings) or `demo_local_20261001/s27_demo.py` (prints / writes every
response). Never point a local run at staging.

## Endpoints

| call | auth | use in the UI |
|---|---|---|
| `GET /remote-config/catalog` | view or admin token | build the form: one control per key with `tier == "control"`; `engineering` keys shown read-only; `blocked` keys hidden |
| `GET /devices/{id}/remote-config` | view or admin | current state + command list; poll every 30–60 s (delivery takes minutes) |
| `POST /devices/{id}/remote-config/plan` | admin | on every edit: show `refusals` (block Send) and `warnings` (show, allow) |
| `POST /devices/{id}/remote-config/changes` | admin | record the change → returns `command_id` |
| `POST /admin/devices/{id}/commands/{command_id}/send` | admin | send it (existing S6b endpoint) |

Request body for plan / changes: `{"set": {path: value}}` **or** `{"reset": [path, ...]}`
(not both), optional `"supersede": true`, `"lane": "sofar"` (default), `"min_id"`.

## Catalog key fields (per key)

`path, section, group, type, default, nullable, enum, range, choices, unit, help, presets,
apply (next_action | next_boot), tier, tier_reason, blocked_values[{value, why}],
requires[switch paths], limits{max, max_each, max_items, warn_above, warn_below, warn_not, why, proposal}, level (basic | advanced)`.

`level` (added 2026-10-01, Nick): show `basic` keys up front and fold `advanced` ones away. It is
display only; `tier` still decides what is writable.

Widget by `type`: `bool` toggle · `int`/`float` number in `range` (+ `presets`) · `enum` select
(`""` cannot be sent: offer "reset to default" instead) · `crop` 4 ints `[x, y, w, h]` native px ·
`wxh` text `WxH` · `ladder` up to 4 descending ints · `gains` 2 floats · `hhmm` time · `tz` select
(presets) · `str` with `choices` = select (framing, sensor mode). `nullable` → an "auto / unset"
option = JSON `null`. INT keys must be sent as JSON integers.

Show `apply`: next_action = "next capture"; next_boot = "next restart (a stay_on unit restarts
itself)". Show `requires`: e.g. exposure values do nothing until `camera.controls_enabled` and
`camera.exposure.enabled` are on.

## Device view fields

`eligible, writable, why_not[], send_enabled, gateway, reported{hash, seen_at, via},
keys[{path, tier, reported, reported_text, source, desired, reset, state (match|differs|unknown|null),
command_id, in_flight}], pending_unsent[], in_flight_keys{}, commands[{command_id, verb, kv,
status, ui_status, late?, dead, sent_at, ack{ok,h,e,k,at}, e, e_text, hint}]`.

`ui_status` lifecycle (show per command): `queued → sending → sent (late) → saved → in_effect`;
side exits `send_failed`, `rejected` (+ `e_text`), `reverted`, `superseded`, `expired` (can never
be sent), `needs_cfm`, `other`. `reported == null` = the unit never reported that key (not "off").

## Refusal reasons (422 `detail.reason`, every refusal in `detail.refusals[]`)

`empty, mixed, unknown_key, not_writable, blocked_value, bad_value, over_limit, cross_key, too_big
(see key_bytes: split into two changes), not_eligible, pending_unsent, in_flight`; 403
`remote_config_disabled` / `device_not_allowed`; 409 `no_current_gateway`; 503 `catalog_unavailable`.
Send endpoint: 409 `rate_limited` + `retry_after_s` (shared with heals, retry after it),
`newer_sent`, `sending_disabled`, `device_not_allowed`.

## UI flow (one change at a time)

1. edit → `plan` → show refusals / warnings / `json_bytes` of 234.
2. Send → `changes` → `send` (if 409 `rate_limited`, wait `retry_after_s`, retry the SAME id).
3. Poll the device view until the command is `in_effect` or `rejected`; only then start the next
   change for the same keys (else `in_flight` / `pending_unsent`).

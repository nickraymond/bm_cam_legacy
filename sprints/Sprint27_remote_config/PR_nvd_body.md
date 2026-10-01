## Sprint27: remote camera config API for the frontend

**Open only after Nick closes R5 (S6b HIL).** Branch `feature/s27-remote-config` → `staging`.
Spec + three-reviewer consensus: bm_cam_legacy `sprints/Sprint27_remote_config/` (SPEC.md r2, REVIEW_r1.md).

### What
New router `backend/app/remote_config.py` (no `/admin` prefix):

| endpoint | auth | writes |
|---|---|---|
| `GET /remote-config/catalog` | view or admin | no |
| `GET /devices/{id}/remote-config` | view or admin | no |
| `POST /devices/{id}/remote-config/plan` | admin | no |
| `POST /devices/{id}/remote-config/changes` | admin + `BM_REMOTE_CONFIG=1` + `BM_REMOTE_CONFIG_DEVICES` | one `device_commands` row |

- One change = one `set` or `reset` command (≤ 234 B). Sending = the EXISTING
  `POST /admin/devices/{id}/commands/{cid}/send` (unchanged; its own flag + allowlist + 65 s guard).
- Validation comes from the camera's own registry: `backend/app/vendor/bmcam_config_catalog.json`
  (generated in bm_cam_legacy). The `check_value` and video-geometry ports are pinned by
  camera-generated vectors. 47 control keys are writable (explicit allowlist); everything else is
  read-only or blocked.
- Cross-key rules refuse only when every input is known (else warn; the unit answers `e:xk`).
  `stay_on` is fail-safe: refused unless `power.bus_always_on` is reported true.
- Queue: rows that can never be sent (`newer_sent`) show `expired` and are ignored.
  `pending_unsent` / `in_flight` refusals apply unless `supersede`. Eligibility = the v9 daemon has
  answered once AND a `<WS>` in the last 30 days.

### Safety
- S6b-owned files untouched (`sofar_commands`, `admin_commands`, `heal_autosend`, `command_events`, logs.html): imports only.
- No migration. New flags default OFF. The catalog loads lazily: a bad file answers 503 on these routes only, and the app still starts.
- The view token never sees `ack_raw` or Sofar response text.

### Tests (scratch Postgres, fake Sofar)
`test_s27_config_catalog.py`, `test_s27_remote_config_plan.py`, `test_s27_remote_config_db.py`; all
`test_s6a_*` / `test_s6b_*` / migration 0016-0017 tests and `tests/processing/test_no_cv2_at_import.py`,
`test_api_no_db.py` green. Demo JSON: bm_cam_legacy `sprints/Sprint27_remote_config/demo_local_20261001/`.

### Not tested
Hardware; the real Sofar lane; the Render deploy. The bench ladder (SPEC r2 §5.2) needs bm #97 and
the bm Sprint27 camera rule on the unit first.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

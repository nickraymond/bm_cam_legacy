# Sprint26 S2 — live config pull (read-only), 2026-09-25

Approved by Nick 2026-09-25 (bmcam004 read-only pull explicitly OK'd).
Tool: `pull.sh` (polls SSH, then `cat`/`ls`/`crontab -l`/`sha256sum` only —
nothing on the units was changed; no disarm, no Spotter change). Log: `pull.log`.

| unit | caught | runtime sha | repo checkout | cron | camera proc at pull |
|---|---|---|---|---|---|
| bmcam004 | 23:00:43Z, up 0 min (scheduled bus window) | f8a1bbf56929 | f8a1bbf feature/s5-rsd-heal | armed | own boot cycle running (`rc_progressive_jpeg.py --transmit`) |
| bmcam003 | 23:00:59Z, up 0 min (scheduled bus window) | a71b6c774b32 | a71b6c7 feature/s5-rsd-heal | armed | own boot cycle running |

bmcam000 was not reachable before the 23:20Z deadline (not required).

## Files and hashes

Copied to `device_profiles/<unit>/live_20260925/` (the migration's source of truth;
the stale repo profiles stay until the S2 gate, because every wire golden starts
from `device_profiles/bmcam003/camera_schedule.yaml`).

| file | sha256 (pulled copy) | on-unit sha256 (survey, seconds later) |
|---|---|---|
| bmcam003 camera_schedule.yaml | c5376fa8…dbdb | c5376fa8…dbdb (same) |
| bmcam003 bm_command_state.json | 8acf7615…37cd3 | 0fd4835e…0c4 (**changed in between**) |
| bmcam004 camera_schedule.yaml | 18d6dbe2…5954 | 18d6dbe2…5954 (same) |
| bmcam004 bm_command_state.json | 56714784…2ff5 | 56714784…2ff5 (same) |

bmcam003's state file was rewritten by its own running boot cycle between the copy
and the survey's hash (the daemon records every command id). The pulled copy is a
valid earlier snapshot (touched = [], no pending heals/trigger). The bench step
migrates whatever state the unit holds at that time, on the unit.

## Findings

- The two live YAMLs are identical except the key ORDER inside `bm_commands`.
- Both differ heavily from the repo's bmcam003 profile (2026-07-31): capture_mode
  video with `video_tx` on (wide_1080p_lean 6 Mbps, 190-msg cap), all-day window
  (00:00-00:00), `allow_system_clock_fallback: true`, auto focus, `media_key` on,
  `network.default: nereus_hq`, 8-min budget, 384/1.3 pacing. The header comment
  "bm_commands island absent = disabled" is stale: the island is present and on.
- `video_tx` and `media_key` exist only on the units (DESIGN §5 predicted this).
- **No `media_gid`** on either unit.
- Neither state has a v8 overlay (touched = []); no pending heals or trigger.
- `config_migrate_v1_v2.py` dry-run on both: **OK**, every key mapped, no problems.
- Both units: Python 3.13.5, PyYAML 6.0.2.

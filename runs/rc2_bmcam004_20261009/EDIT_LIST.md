# bmcam004 → RC2 (21Z / 2 PM PDT window): exact changes, for Nick's OK

Inspection (read-only, 16Z): bmcam004 = runtime 0a6e411 (104ee3c + #143), cjxl 0.11.2 present, cron ARMED,
`still.raw.layout rgb` + exposure auto already in the base. The base YAML differs from bmcam003's RC2 base
(`runs/jxl_rc_20261008/BUILD_RECORD.md`) in only 3 lines; the overlay carries 3 remote settings.

## Edits (backup first; strict-load verify; revert all on failure; every change in gate.log)

| # | file | change | why |
|---|---|---|---|
| 1 | camera_config.yaml (v2 base) | `mode.media` "video" → "still" | the flashed unit has no overlay; today media=still comes from overlay cid 1000072 |
| 2 | camera_config.yaml | insert `still.format: "nrjxl"` under `still:` | today nrjxl comes from overlay cid 1000061 |
| 3 | camera_config.yaml | `uplink.msg_interval_s` 1.3 → 1.0 | RC2 pacing (R3-PACE: 1.0 s final) |
| 4 | camera_schedule.yaml (v1, parity source only) | `bm_serial.image_transmit_delay_seconds` → 1.0, `capture_mode` → "progressive_jpeg", `video_tx.enabled` → false | the deploy's v1-vs-v2 parity guard (as on bmcam003) |
| 5 | bm_command_state_v2.json | clear the overlay {mode.media still, video.send.message_cap 126, still.format nrjxl} | after edits 1–2 it is redundant; the build record must hold the base only |
| 6 | runtime | deploy development **9ec4cb7** (= RC2, the EM holds merges; tip checked before arming) with `hil_deploy_window.sh` | RC2 build; preserves sent/ (pre-check a) |

Order in the window:
1. Edits 1–4, then strict load.
2. Deploy 6 (its parity guard validates edits 1–4).
3. POST: edit 5 + verify + BUILD_RECORD inputs.

The 21Z capture is skipped (deploy wake). The first RC2 wake on bmcam004 is 22Z (3 PM).

Unchanged: rgb layout, exposure auto (Nick decides later by command), crop 1504,846,1600,900 → 1000, cap 195, 384 chars, 0x02,
lane off, window all day, real halt, bridge utc, crontab, sent/.
Restore: the backups under `/home/pi/hil_backup/rc2_004_<TS>/` + the deploy's runtime tgz.

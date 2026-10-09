# RC2 build record (bmcam004 / SPOT-31593C), the same build as bmcam003's (runs/jxl_rc_20261008/BUILD_RECORD.md)

Taken 2026-10-09 21:02:17Z (2:02 PM PDT) in the deploy POST. Nick's OK: TE2 chat 16:03Z (re-confirmed 19:26Z), `EDIT_LIST.md`.

| item | value |
|---|---|
| runtime sha | **9ec4cb7ae7c1** (development tip; = 786b100 + #145 docs-only), deployed 21:02:12Z; previous 0a6e411 (104ee3c + #143) |
| deploy checks | print-config parity OK (14 lines); **config json OK (13 loader outputs equal)**; **config v2 parity OK**; validation PASS; re-armed. This is the on-hardware confirmation of Friday pre-check (b): nrjxl + rgb in the base pass the guard |
| encoder | cjxl v0.11.2 [NEON_WITHOUT_AES] (installed 2026-10-05) |
| base hash | **8fded2ea**, identical to bmcam003's RC2 base. File sha256 camera_config.yaml 29d8f099…; it differs from bmcam003's ee69a9b1… only in comments (dates / the R3 note) |
| camera_schedule.yaml (v1) | e8f06056… (parity edits: delay 1.0, capture_mode progressive_jpeg, video_tx.enabled false) |
| bm_command_state_v2.json | 791e89f6… (overlay {}) |
| effective | still, per_boot, nrjxl, rgb, exposure **auto** (base), 1.0 s, 384, 0x02, lane off, cap 195, crop 1504,846,1600,900 → 1000, all day, real halt |

## Edits (backups on the unit)

| when (Z) | change | backup |
|---|---|---|
| 21:00:48 | v1: delay 1.3 → 1.0, capture_mode video → progressive_jpeg, video_tx.enabled true → false; v2: media video → still, msg_interval_s 1.3 → 1.0, insert still.format nrjxl | `/home/pi/hil_backup/rc2_004_20261009T210048Z/` |
| 21:02:17 | overlay cleared: {mode.media still (1000072), video.send.message_cap 126 (1000049), still.format nrjxl (1000061)} | `/home/pi/hil_backup/bm_command_state_v2.json.rc2_004_20261009T210217Z` |
| — | crontab ARMED backup | `~/hil_backup/20261009T210049Z/crontab_ARMED.txt` |

## Weekend exposure (Nick 1:30 PM)

low_gain / max_shutter_us 60000 / max_gain 1.0 as a **command overlay**, sent by the EM after this deploy. The base stays auto.
See the same note in bmcam003's record.

## Rollback

`tar xzf /home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam004_20261009T21*.tgz -C /home/pi`, plus the YAML/state backups above.

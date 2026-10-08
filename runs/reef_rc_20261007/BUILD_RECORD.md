# REEF-RC build record (bmcam003 / SPOT-33507C): what the NOAA unit must reproduce

Gate: `hil/gates/REEF_RC.md`. Taken 2026-10-08 06:01:47Z (11:01 PM PDT 10/7), right after the deploy, before wake 1 (07Z).
To be re-taken after wake 12: the two copies may differ only by the commands' overlay.

## Build

| item | value | source |
|---|---|---|
| tested runtime sha | **c8bea4de50a8** (bm PR #143 head, branch `fix/sent-prune-spotter-time` = development 12575ba + #143) | `software_sha.txt` read back after the deploy (`pulled/post_05z.txt`) |
| shipped sha | development's merge commit that contains #143 (same content as c8bea4de). Record that sha here when #143 merges | EM decision 2026-10-08 |
| deploy | `hil_deploy_window.sh bmcam003 fix/sent-prune-spotter-time c8bea4d bmcam003/live_20260925 2026-10-08T06:00:00Z reefrc2` (ACCEPT_DIFF=1): print-config parity OK (14 lines, no diff), config json OK (13 loader outputs equal), config v2 parity OK, validation ladder PASS | `pulled/bmcam003_field_update_reefrc2_20261008T060055Z.log` |
| runtime rollback | `tar xzf /home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam003_20261008T060111Z.tgz -C /home/pi` (= a50636e + R2 patch) | deploy log |

## Config (unit-local files after the deploy)

| file | sha256 | copy |
|---|---|---|
| `camera_config.yaml` (v2 base, registry v8, base hash 4977d70a) | c626bf014654e7bcfdbc460724b727a913827adb04eb5d0b79d22047e6bc36bd | `pulled/build_camera_config.yaml` |
| `camera_schedule.yaml` (v1, the parity source) | 6f419ebb131d1eba98aaca5ef5a0c8cf92ed74d3d9f71b0ab2ea71925e6d621d | to pull at wake 1 (read-only) |
| `bm_command_state_v2.json` (overlay {} after the clear) | 860dd662814f9be3ac311ba191b9f5d04ece5e959945b13f07387e3745214083 | — |

Effective values (strict load + resolve with the state, `pulled/post_05z.txt`):
- `mode.media` still, `mode.run` per_boot
- `uplink.msg_interval_s` 1.0, `uplink.chunk_chars` 384, `uplink.network_type` 2 (0x02 cellular-only), `uplink.lane.enabled` false
- `still.message_cap` 195, `still.crop` [1504, 846, 1600, 900], `still.output_width` 1000 (→ 1000×562)
- `camera.image_processing.enabled` false, `camera.image_processing.contrast` None
- `schedule.window.enabled` true with start 00:00 / end 00:00 (= all day), tz America/Los_Angeles
- `power.halt.enabled` true, `power.halt.dry_run` false
- `dropped []`

Full `--print-config`: `pulled/post_05z.txt` (`== print-config`). Key lines: `media=still hash=4977d70a registry=v8 overlay=0 key(s)`,
`capture_mode=progressive_jpeg`, ladder 90…9, budget 480 s, cap 195, `delay_s=1.0`, `transmit_phase (C2): OFF`, crop → 1000x562 rpicam.

## Edits made to reach this config (Nick's OK, TE2 chat 2026-10-08 02:25Z; every one backed up on the unit)

| when (Z) | file | change | backup |
|---|---|---|---|
| 05:00:51 | camera_schedule.yaml (v1) | `bm_serial.image_transmit_delay_seconds` 1.3 → 1.0; `capture_mode` "video" → "progressive_jpeg" | `/home/pi/hil_backup/reefrc_20261008T050051Z/` |
| 05:00:51 | camera_config.yaml (v2) | `mode.media` "video" → "still" (the unit took stills only through overlay cid 1000159) | same |
| 05:00:51 | r2_start_delay_s | removed (was 0) | same |
| 06:00:50 | camera_schedule.yaml (v1) | `video_tx.enabled` true → false (v1/v2 parity) | `/home/pi/hil_backup/reefrc_20261008T060050Z/` |
| 06:01:47 | bm_command_state_v2.json | overlay {mode.media: still, video.send.message_cap: 126} → {} (ids 1000159, 1000020) | `/home/pi/hil_backup/bm_command_state_v2.json.reefrc_20261008T060147Z` |
| (R3, 10/7) | camera_config.yaml (v2) | `uplink.msg_interval_s` 1.0 (R3-PACE final; comment `# R3-PACE arm A` left on the line) | r3 run |

## Provisioning notes (for the NOAA unit)

- The repo profile `device_profiles/bmcam003/live_20260925/camera_schedule.yaml` is NOT this config. It has pacing 1.3
  (stage 4 flagged `msg_interval_s: unit=1.0 profile=1.3 <-- DIFFERS`), `capture_mode: "video"` and `video_tx.enabled: true`.
  The NOAA profile needs: `capture_mode: "progressive_jpeg"`, `bm_serial.image_transmit_delay_seconds: 1.0`,
  `video_tx.enabled: false`, cap 195, crop 1504,846,1600,900 → 1000, 384 chars, 0x02, real halt. Then migrate to v2 and
  check that the effective values match the list above.
- **Not tested by REEF-RC:** the transmit window and time source of the shipped unit (Nick: set at provisioning). The bench
  runs all day (00:00–00:00) on spotter_utc.
- Bridge (SPOT-33507C): production hourly bus, 10 min on from :00 (seen on the console every wake: bus HH:59:58 → HH+1:09:58).
  Not re-read with `bridge cfg get` (that needs console writes).
- Backend: self_heal ON for SPOT-33507C at 24/day, flipped by the EM before wake 1. Record the time here.

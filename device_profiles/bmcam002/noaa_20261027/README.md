# bmcam002 NOAA target profile (10/27 on-land update)

`camera_config.yaml` here is bmcam002's **config v2 target** for the Tue 10/27 update (SPEC_e4_noaa_release rev 12.2,
must do 2). It is installed whole on the unit after the shipped v1 file is migrated (runbook
`runs/noaa_update_20261027/RUNBOOK.md`). It is not a v1 profile: `rc_field_update.sh --profile` can't use it, and a v1
file can't carry `commands.runtime: supervisor` (`BM_Devel_Pi/config_v1_reader.py:300`), the runtime the soak ran.

**Pinned hash: `fe87abab`** (`--print-config`, build 613fb2c1 = Nick's S3 GO, Mon 10/26 10:00 AM). With no command
overlay on the unit, the effective hash is the same.

## Where the values come from
The production NOAA soak profile, cfg **d95aa41d**, as bmcam003 ran it for the RC soak (10/22–10/26):
- the base `camera_config.yaml` and overlay in bmcam003's pre-soak backup:
  `~/NereusData/bm_cam_legacy/2026-10-23_rc_soak_s3/backup_pre/bmcam003/` (sha256 ee69a9b1… and 2a53a202…);
- window 08:00–18:00 (soak card step 3).
Base + overlay + that window hash to d95aa41d, the hash the unit acked. The generator checks this and stops if it differs.
It is not bmcam004's temporary auto AE/AF config.

Then six keys change (must do 2 and rev 12.2):

| Key | Soak d95aa41d | bmcam002 target |
|---|---|---|
| `schedule.timezone` | America/Los_Angeles | **America/New_York** (in BASE, envelope trap 2) |
| `schedule.window.start` / `.end` | 08:00 / 18:00 | **10:00 / 15:00** |
| `schedule.window.enabled` | true | true |
| `time.allow_system_fallback` | true | **false** (Nick, Sat 10/17 9:01 AM: "Yes I agree, off") |
| `network.default` | nereus_hq | **none** (leave Wi-Fi as is; the bench value changes Wi-Fi on every non-`--print-config` run and could drop Nick's hotspot SSH) |

Every other key is the soak's, including:
- nrjxl B3a (`still.raw.layout: rgb`), progressive, crop [1504, 846, 1600, 900];
- focus manual 0.8, exposure `low_gain` (60000 µs, gain 1.0), contrast 1.0;
- pacing 1.0 s, 384 chars, cellular 0x02;
- media key on, `heal_order: after`, `heal_offwin_max: 40`;
- commands on, runtime supervisor, budget 8 min, real halt.

## Reproduce
Built with the registry at 613fb2c1 (`config_migrate.render_config_text`; strict-load round trip). Then
`rc_progressive_jpeg.py --print-config` ran on the Mac with the shipped v1 bmcam002 YAML (aoml-production 6cb38dd,
sha256 117876ae0928…) beside this file. It printed `config v2 … hash=fe87abab … overlay=0 key(s)`,
`schedule: window=10:00-15:00 tz=America/New_York` and `geometry … crop_xywh=(1504, 846, 1600, 900) output=1000x562`.

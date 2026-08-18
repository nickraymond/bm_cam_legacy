# Shipped artifacts — AOML reef, 2026-08-18

These two files are **byte-for-byte copies of `/home/pi/BM_Devel_Pi/camera_schedule.yaml`
as it ran on each unit** during the 48-hour reef test that started 2026-08-18 ~18:50 UTC.
They are the record of what was actually deployed, not a profile template — no
provenance header, no editing. Do not "tidy" them.

| Unit | md5 | Verified |
|---|---|---|
| bmcam001 | `3e08b8ebc47d5973d8f573239dcd0d2e` | md5 printed by the unit itself at 18:50:11Z, immediately before its forced capture |
| bmcam002 | `37fd4ffb905d1735270e26eef8f15b2f` | md5 printed by the unit at 18:02Z; unchanged through its 18:49 boot cycle |

Runtime on both units: `main` `0d03a62cf565c761795d0380a412029f66866ef7`
(`software_sha.txt` = `0d03a62cf565`). No code was rolled forward — this deployment
was config-only.

The matching entries in `device_profiles/bmcam00{1,2}/camera_schedule.yaml` on this
branch are identical to these files below their provenance headers; that equivalence
was verified by diff before this branch was cut.

To restore a unit to exactly this state:

```bash
scp bmcam001_camera_schedule.yaml pi@bmcam001:/home/pi/BM_Devel_Pi/camera_schedule.yaml
ssh pi@bmcam001 'cd /home/pi/BM_Devel_Pi && python3 rc_progressive_jpeg.py --print-config'
```

Remember the unit must be caught awake and disarmed first — see
`docs/DEPLOYMENT_AOML_REEF_2026-08-18.md`, "Operational lessons".

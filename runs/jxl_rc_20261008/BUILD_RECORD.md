# JXL-RC (RC2) build record (bmcam003 / SPOT-33507C): the candidate NOAA build with JPEG XL stills

Gate: `hil/gates/JXL_RC.md`. Taken 2026-10-08 21:01:42Z (2:01 PM PDT), before wake 1 (22Z). Re-take after the soak.
Builds on `runs/reef_rc_20261007/BUILD_RECORD.md` (reef config); differences below.

## Build

| item | value | source |
|---|---|---|
| runtime sha | **9ec4cb7ae7c1** (development tip; = 786b100 "Sprint28 B3a … #134" + #145 docs-only; `git diff 786b100 9ec4cb7 -- BM_Devel_Pi tools` empty). The EM recorded RC2 = 9ec4cb7 | `pulled/build_after_rc2.txt`, deploy log |
| deploy | `hil_deploy_window.sh bmcam003 development 786b100 bmcam003/live_20260925 2026-10-08T20:00:00Z rc2` (ACCEPT_DIFF=1): rc_field_update rc=0, print-config parity OK (14 lines), validation PASS; tool flagged the sha ≠ 786b100 → POST skipped, re-armed, dark 20:02:59Z | `pulled/bmcam003_field_update_rc2_20261008T200054Z.log`, gate.log |
| encoder | cjxl v0.11.2 [NEON_WITHOUT_AES] = libjxl-tools 0.11.2-0.1~deb13u2 (+ libgif7, libjpeg-turbo-progs, libtcmalloc-minimal4t64), apt 19:01:34–19:02:00Z, dpkg audit clean | `pulled/install_cjxl_19z.txt` |
| runtime rollback | `/home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam003_*20261008T20*.tgz` (= c8bea4de REEF-RC build); cjxl: `sudo apt-get remove -y libjxl-tools` | deploy log |

## Config after the POST (21:01Z)

| file | sha256 |
|---|---|
| camera_config.yaml (base hash 8fded2ea) | ee69a9b1a5d2dc378cd66f4d6663e699d950d2826ac381ed745fc7945cd4b621 (copy `pulled/build_camera_config.yaml`) |
| camera_schedule.yaml (v1, unchanged since REEF-RC) | 6f419ebb131d1eba98aaca5ef5a0c8cf92ed74d3d9f71b0ab2ea71925e6d621d |
| bm_command_state_v2.json | b15a153d149a36bcf974dbb4c76c31889c4b2eb459c8d2171f5fba9399f40ad2 |

Effective values: same as REEF-RC (still, per_boot, 1.0 s, 384, 0x02, lane off, cap 195, crop 1504,846,1600,900 → 1000, all-day window, real halt), plus:
- **`still.format` nrjxl**: base edit, Nick's OK 18:21Z; inserted as line 71 under `still:`; backup `/home/pi/hil_backup/rc2_20261008T210120Z/camera_config.yaml`.
- **`still.raw.layout` rgb**: `hil_b3a_layout.sh bmcam003 rgb`; still path reads `nrjxl rgb 45`.
- **`camera.exposure.profile` auto**: registry default, not in the YAML.
- **Overlay**: `camera.image_processing.contrast` 1.0, the REEF-RC cmd 4 leftover. The flashed unit has no overlay.

## Backend

- self_heal ON, 24/day (since 2026-10-08T06:03:51Z).
- 2-week reach-back ON for SPOT-33507C at 20:25Z (heal_since 2026-10-08T20:00Z clean start; read-back True/True).

## Under test tonight (NOT in the base)

- Low-gain lock command (overlay): `camera.exposure.profile low_gain`, `max_shutter_us 60000`, `max_gain 1.0`, sent by the EM at
  21:50Z → reached the console at the 23:10Z sync → applied at the 00Z boot → **in effect from the 01Z wake**:
  - cfg hash 9c980489;
  - sidecar `exposure_profile=low_gain`, `exposure_profile_applied=true`, tuning `imx708_wide_lowgain_s60000_g1.json` used;
  - first locked frame: AnalogueGain 1.12, ExposureTime 26.8 ms at Lux 825.
  It goes into the BASE of the flashed build only if the soak is clean (Nick decides Fri AM).
- **Reading AnalogueGain on this sensor:** `max_gain 1.0` means "stay at the IMX708 floor". The sensor's minimum analogue gain is
  **1.122807**, so locked frames report AnalogueGain 1.12. That is the floor, not a leak above the cap. Under the lock, exposure
  grows by shutter (up to 60 ms) instead of gain.

## Provisioning notes

- Same as the REEF-RC record (the repo profile `device_profiles/bmcam003/live_20260925` is not this config). In addition, the NOAA
  unit needs `libjxl-tools` (cjxl), `still.format: "nrjxl"` and `still.raw.layout: rgb` in the base.
- v1/v2 parity with nrjxl + rgb in the base: passes (offline, 786b100 runtime, "13 loader outputs equal"); these keys are not in
  the parity set.

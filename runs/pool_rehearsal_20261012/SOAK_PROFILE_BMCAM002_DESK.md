# Weekend soak vs bmcam002's NOAA profile: desk answer (TE2, Thu 10/15 ~9:40 AM PDT; read-only, no unit changes)

## 1. bmcam002 config as the backend shows it

Source: `GET /devices/BMCAM_002/remote-config`, run read-only on nereus000 with its own token. Saved to the scratchpad as remote_config_view.json.

- **Reported hash:** NONE. `reported: null`, `reported_known: 0`, `writable: false`, gateway SPOT-33361C.
- **Backend `why_not`:**
  - "no v9 command reply ever seen (a legacy/v8 unit, or never commanded)";
  - "no <WS> or START config hash in the last 30 days".
- **Target / desired:** NONE. 0 of 67 keys have a desired value, and 0 commands are queued.
- So the backend has no current or target config for bmcam002. The only copy in the repo is `device_profiles/bmcam002/camera_schedule.yaml` (v1, last changed bb38612e "AOML reef test config", 8/18). It is a HEIC-era profile and is almost certainly not what bmcam002 will run after the 10/22 update.

## 2. Delta from 0cb7ad9e (soak units) vs that repo profile: 71 keys differ (whole product generation)

Main ones (repo bmcam002 → 0cb7ad9e):

| Area | bmcam002 repo profile | 0cb7ad9e |
|---|---|---|
| Still pipeline | HEIC q20, 720p, crop 3072×1728@768,432 → 2688×1512 lanczos | nrjxl B3a rgb, crop 1600×900, progressive JXL |
| Focus | continuous AF, lens 1.82, range/speed normal | manual, lens 0.8 |
| Exposure / WB | AE auto, AWB auto | exposure locked, low_gain profile (gain 1.0, max shutter 60 ms); AWB off |
| Schedule | transmit window 10:00–15:00, timezone America/New_York | 24 h window, timezone America/Los_Angeles |
| Cycle | max run 12 min, pjpg q 9–15 step 2 | 8 min (480 s), ladder per nrjxl |
| Self-heal | no media_key | media_key on, retain 14 d, heal_order after |
| Misc | system-clock fallback off, v1 command state | fallback on, v2 state; video / video_tx blocks present (disabled) |

The real delta needs the **NOAA target profile defined**: which keys bmcam002 will run after its 10/22 update. That is Nick's / the NOAA owner's call. With that list, TE2 produces the key-by-key delta against 0cb7ad9e in minutes.

## 3. Can the soak units run that profile without touching bmcam002?

Yes, for the remotely settable keys.
- The registry's 67 remote keys include:
  - `schedule.timezone`, `schedule.window.*`;
  - `camera.focus.*`, `camera.exposure.*`, `camera.white_balance.*`, `camera.image_processing.*`;
  - `still.*` (format, crop, quality ladder, message cap, raw.*), `video.*`, `mode.*`.
  These are console `set`s on bmcam003/004 (overlay, reversible with `reset`), in a listen window or a held-bus boot.
- The Spotter wake cadence (interval / window) is `hil_restore_schedule.sh <SPOT> <interval_ms> <window_ms>`.
- NOT in the remote set: e.g. uplink pacing (`uplink.msg_interval_s`; the units run 1.0, profile 1.3). Matching that needs a base-config edit (camera_config.yaml), which is a deploy-class change.
- All of it needs Nick's own line in the TE2 chat after the Friday envelope expires. bmcam002 is never touched.

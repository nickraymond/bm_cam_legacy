# Sprint27 bench ladder — remote config (for the Test Engineer, after G1)

Owner: the Test Engineer session (sole bench owner after G1). Author: the Sprint27 session.
Daytime, ONE unit (bmcam003 suggested), one change at a time. Record everything in
`runs/s27_ladder_<YYYYMMDD>/` (console log, commands sent, answers, images/clips, `RESULTS.md`).
Feeds R1 G3 (hard mode). SPEC r2 §4–§5.

## Gate before step 1 (do not start otherwise)

- [ ] G1 closed and the bench handed over (RELEASE_PLAN).
- [ ] The unit runs `development` + bm **#97** (re-resolve fix) + bm **#98** (video set safety
      rule). Check: `grep -n "_rule_video_geometry" ~/BM_Devel_Pi/config_validate.py` on the Pi.
      Deploy per the bmcam-field-update skill (back up cron + config first, restore after).
- [ ] Unit awake and reachable for the whole ladder: stay_on with the bus held
      (`power.bus_always_on` true in its YAML), or per_boot + `hld`.
- [ ] Console monitor running on nereus000; **console `cmd.txt` sends need Nick's OK**.
- [ ] Baseline: `{"id":N,"c":"get","k":["mode","camera","still","video.record","video.send"],"to":"con"}`
      saved; note the config hash `h` (the final step must return to it). One baseline still +
      one baseline clip saved.

## How each step is sent

- **Console lane (default first):** record with the backend when nvd is on staging
  (`POST /devices/{id}/remote-config/changes` with `"lane":"console"`), publish the returned
  `console_line` on the Spotter console, then `POST /admin/devices/{id}/commands/{cid}/sent` with
  `http_status: null`. Before nvd is deployed: type `bm pub bmcam/cmd {"id":<remote id>,"c":"set","kv":{…}} 1 1`
  with a remote-range id above the unit's last one.
- **Capture:** `{"id":N,"c":"trg","v":2}` (capture + output per mode) after the ack.
- One command per minute per Spotter (shared with heals). Wait for the ack before the next step.

Pass criteria for every `set` step unless the step says otherwise:
**(a)** ack `ok:1`, **(b)** `<CF … key=value@c<id>>` shows the new value, **(c)** the next capture
shows the effect named in the step, **(d)** `reset` of the same keys → ack ok and `<CF>` back to the
YAML value. Any SSH needed = FAIL.

## Steps

| # | change (`kv`) | check (c) |
|---|---|---|
| L1 | `ping` | ack; device view `eligible: true` |
| L2 | `camera.controls_enabled: true, camera.exposure.enabled: true, camera.exposure.ev: -1.0` | still visibly darker than baseline; capture sidecar `requested_ev` −1 (field names: `rc_capture.py:387-474`) |
| L3 | `camera.exposure.shutter_us: 10000, camera.exposure.analogue_gain: 2.0` (switches from L2 still on) | metadata ExposureTime ≈ 10000, AnalogueGain ≈ 2 |
| L4 | `camera.white_balance.enabled: true, camera.white_balance.mode: daylight` | colour change; metadata AWB mode |
| L4b | `camera.white_balance.mode: manual, camera.white_balance.gains: [1.8, 1.6]` | metadata ColourGains ≈ [1.8, 1.6] |
| L5 | `camera.focus.mode: manual, camera.focus.lens_position: 0.5` | sidecar `requested_lens_position` 0.5 and libcamera LensPosition |
| L6 | `still.crop: [1904, 1071, 800, 450]` | START `r=` and the image are 800 px wide, framing = that box |
| L7 | `still.quality_ladder: [20, 15, 11, 9]` | message count / quality in START change |
| L8 | `video.record.framing: wide_720p` (video unit) | log `[VID]` mode 2304x1296, output 1280x720; clip frame = full sensor FOV (compare with a still at `still.crop` full frame) |
| L9 | `video.send.fps: 8, video.send.size: 640x360` | START `r=640x360`; clip plays at 8 fps |
| L10 | `video.send.duration_s: 8.0` | START `d=8`; clip ~8 s (backend warns: only 5 s ladder-validated) |
| L11 | `mode.media: still` then back to `video` (next_boot) | stay_on: exit 72, the wrapper restarts it after 5 s (`rc_run_capture_cycle.sh`); next START type changes |
| L12 | two different changes queued back to back (one Sofar send each, ≥ 65 s apart) before the unit's next wake | both acked ok, in id order (no `e:old`) |
| L13 | a change sent while a clip is recording | ack after the clip ends; value applies at the NEXT action |
| L14 | one change end to end over the Sofar lane via the backend (`/changes` + existing `/send`; needs `BM_COMMAND_SEND` on Render, Nick only) | device view `ui_status`: sent → saved → in_effect |

## P0 — rpicam limits probe (Nick Q2, BEFORE the image-processing steps; read-only on the unit)

Camera idle first (no capture running: stop the runtime per the deploy skill, back up cron). Save
every output to `runs/s27_ladder_<date>/p0_rpicam_limits/`. The Sprint27 session encodes the ranges
from these files, never from memory.

```bash
rpicam-still --version; rpicam-vid --version
```

```bash
for app in rpicam-still rpicam-vid; do $app --help 2>&1 | grep -A3 -E -- '--(sharpness|contrast|saturation|brightness|denoise|hdr)'; done
```

```bash
python3 -c "from picamera2 import Picamera2; c=Picamera2(); print({k: v for k, v in c.camera_controls.items() if k in ('Sharpness','Contrast','Saturation','Brightness','NoiseReductionMode','HdrMode')}); c.close()"
```

Run-time probes (each a 2 s clip to /tmp, record exit code + last 5 lines; delete the clips):

| probe | command (add `-t 2000 -n -o /tmp/p.h264 --mode 2304:1296:10:P --width 1280 --height 720`) |
|---|---|
| duplicate denoise | `rpicam-vid … --denoise cdn_off --denoise cdn_fast` |
| duplicate sharpness | `rpicam-vid … --sharpness 1.0 --sharpness 2.0` |
| each float at min / max / beyond | `rpicam-vid … --contrast <v>` (same for sharpness, saturation, brightness) |
| each denoise value | `rpicam-vid … --denoise <v>` |
| hdr × mode | `rpicam-vid … --hdr <v>` with `--mode 2304:1296:10:P` and `--mode 4608:2592:10:P` (output 1000x562) |
| stills | `rpicam-still -n -o /tmp/p.jpg --hdr <v>` / `--denoise <v>` |

Pass: every file saved; restore cron. Hand the folder to the Sprint27 session.

## Image-processing steps (after P0 and the Sprint27 IP change are on the unit)

| # | change (`kv`; with `camera.controls_enabled` and `camera.image_processing.enabled` true) | check |
|---|---|---|
| IP1 | `sharpness`, `contrast`, `saturation` at their measured min, then max (one change each) | still AND clip produced; sidecar `requested_*`; visible effect |
| IP2 | `brightness` min / max | as IP1 |
| IP3 | each `denoise` enum value | still + clip produced |
| IP4 | `hdr` each allowed value, on a still unit and on a video unit | still + clip produced; refused combinations get `e:xk` |
| IP5 (negative) | a value just outside each measured range (console range id) | `e:xk`, nothing stored, next clip produced |

## Negative steps (the unit must refuse, and stay reachable)

Send these on the console lane with **console-range ids (1–99,999)**. The backend refuses them
before sending, so they cannot go through `/changes`.

| # | `kv` (on a video unit, framing wide_1080p_lean) | pass |
|---|---|---|
| N1 | `video.record.fps: 30` | ack `ok:0, e:xk`; `get` shows fps unchanged |
| N2 | `video.record.sensor_mode: 1536x864` | `e:xk` |
| N3 | `video.send.size: 481x271` | `e:xk` |
| N4 | `video.record.framing: stills_roi_1000p, video.send.size: 1280x720` | `e:xk` (send larger than recording) |
| N5 | after N1–N4: restart the runtime once (or wait for the next wake) | starts normally, no exit 2, no SSH |

## Restore and overall pass

R1: `reset` every key touched (≤ 4 per command), then `get` → config hash == the baseline hash.
Restore cron / config per the deploy skill.

**Overall PASS:** every step meets its criteria, N1–N5 refused with the unit reachable, 0 units
needing SSH, and the final hash equals the baseline. Report per step in `RESULTS.md` (sent at,
ack at, `<CF>`, evidence file), then one line to the EM.

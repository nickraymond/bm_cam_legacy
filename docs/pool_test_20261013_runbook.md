# Pool test 2026-10-13: Pi-side runbook (pool_sequence.py)

Units: bmcam003 + bmcam004. Pool session: Tue 10/13, 11 AM PT.
Code: `BM_Devel_Pi/pool_sequence.py`, `BM_Devel_Pi/rc_preview.py`, `tools/pool_mode.sh`.
Tests: `tests/test_pool_sequence.py`.

## What runs

| Phase | Trigger | Does | Default |
|---|---|---|---|
| calibration | `cal` | native 4608×2592 JPEG + DNG of the same exposure, every `interval_s`, auto-stop | 10 s × 1200 s = 120 frames (~3.8 GB) |
| loop | `loop` (or auto-start 30 min after cal ends) | 1 production still → pjpg + nrjxl (B3a, same capture), plus a 30 s 1280×720 10 fps clip at 2.0 Mbit/s | 18 × 600 s |
| idle | after the loop | Pi stays up for the Wi-Fi download; **never halts** | none |

Settings come from the unit's effective config: camera_config.yaml ⊕ bm_command_state_v2.json, merged the way the supervisor does it. The pool reads them only and never writes either file. Set focus (manual, 1.82) and the exposure overlay by command **before** `pool_mode.sh on`, then check them with `--print-config`.

Wi-Fi: pool mode never calls `network_ap.sh` or the network boot apply (that call lives in `rc_progressive_jpeg.main()`, which pool mode never runs). NetworkManager autoconnect owns wlan0. `status` reports `ssid=` and `ip=`.

## Commands (nereus000 console, console id range)

Every command gets one answer line: `[bmcamNNN] OK id=N pool <verb> ...` or `ERR id=N ...`. A replayed id re-prints its first answer with ` dup` and does not run again.

```text
bm pub bmcam/cmd {"id":90101,"c":"pool","v":"status"} 1 1
bm pub bmcam/cmd {"id":90102,"c":"pool","v":"preview"} 1 1
bm pub bmcam/cmd {"id":90103,"c":"pool","v":"cal","duration_s":120,"interval_s":10} 1 1
bm pub bmcam/cmd {"id":90104,"c":"pool","v":"mark","note":"card_1m"} 1 1
bm pub bmcam/cmd {"id":90105,"c":"pool","v":"loop","iterations":3,"period_s":120} 1 1
bm pub bmcam/cmd {"id":90106,"c":"pool","v":"preview","lines":"2-4,9"} 1 1
bm pub bmcam/cmd {"id":90107,"c":"pool","v":"stop"} 1 1
```

| Verb | Fields | Notes |
|---|---|---|
| status | (none) | phase, cal n/N, loop n/N, `run=<folder name>`, free MB, marks, last error, ip, ssid, uptime, autostart countdown |
| cal | duration_s 10..7200, interval_s 2..600 | refuses with ERR when busy, when the phase won't fit free space + `--min-free-mb`, or when this run's `calibration/` already has frames (see below) |
| loop | iterations 1..100, period_s 30..7200 | same guards (`loop/` already has frames → refused; the auto-start too) |
| mark | note `[A-Za-z0-9_:./+,-]{1,40}` | use `_` for spaces; the marker goes into run_manifest.json `marks` |
| stop | (none) | ends the phase after the current step. Sent while idle with an auto-start pending, it cancels the auto-start |
| preview | lines `a-b,c` (optional) | no lines = a new capture. With lines = resend from the LAST preview |

Unrecognised `c` values (any production verb) get `ERR ... pool_mode: only c=pool runs`.

**One run folder = one session (EM 10/12).** A second `cal` or `loop` in the same process is refused, so in-air rehearsal frames never mix with the pool's:

```text
[bmcamNNN] ERR id=<N> pool cal refused: run already has calibration frames, restart pool mode (pool_mode.sh stop/start) for a new run
[bmcamNNN] ERR id=<N> pool loop refused: run already has loop frames, restart pool mode (pool_mode.sh stop/start) for a new run
```

For a new run: `bash /home/pi/pool_mode.sh stop`, then `start` (or reboot). `status` then shows `cal=0/0 loop=0/0` and a new `run=`.

## Preview wire (frozen with the nereus000 reassembler, PR #151)

```text
[host] PVH <unit> <img_id> n=<lines> bytes=<B> sha256=<hex> w=640 h=360 q=<q>
[host] PV <unit> <img_id> <seq>/<n> <crc32 of the b64 chunk, hex8> <b64, 160 chars>
[host] PVE <unit> <img_id>
```

- The image is the production 1600×900 crop, downscaled to 640×360 and saved as a progressive JPEG. Quality starts at q60 and steps down by 10 until the file is ≤ 30 KB.
- Lines are paced 0.031 s apart (Sprint09 DEV_LOG.md:179). Acks go out ahead of queued PV lines.
- Cross-checked against `tools/pool_preview.py` from #151: OK 85/85, sha256 matches, resend included.
- Expected time per preview (**assumed**, measure on the bench):
  - capture ~3.6 s (measured median with `--raw`, runs/s28_ladder_20261004/RESULTS.md:15)
  - crop + encode ~2–3 s (assumed)
  - console: (bytes·4/3)/160 lines × 0.031 s, i.e. ~4–8 s for 15–30 KB
  - total ≈ 10–15 s
- If the camera is busy with a 30 s loop clip, the preview waits and prints a `waiting for camera` line.

## Timing budget

- Calibration frame: 3.62 s median per `--raw` capture on bmcam004 (**measured**, RESULTS.md:15). The s28 pair loop ran captures 5 s start-to-start (gate.log:27-28). Fits 10 s with ~6 s spare. Each frame logs `frame_s` and `slot_late_s`. A slot is skipped (and logged) only when a frame overruns a whole period.
- Loop iteration, **assumed** ≈ 70 s out of 600 s:
  - still ~3.6 s
  - clip 30 s + ~2 s start + mux
  - pjpg ~3 s
  - nrjxl search median 28.0 s, cjxl VmPeak 126 MiB (**measured**, RESULTS.md:145)
  - each iteration logs `iteration_s`
- Video bitrate 2.0 Mbit/s: V1 720p look rehearsal `--bitrate 2000000` (hil/gates/V1_720p_look_rehearsal.md:24, branch claude/underwater-video-feasibility-df934d @333ca7a1). The France cam1 30 s 720p source measured 1.9 Mbit/s = 7.17 MB. Expect ~7.5 MB per clip.

## Bench dry run (TE): bmcam004 first, compressed

From the Mac (repo at this branch). The units run 9ec4cb7, and BM_Devel_Pi is identical between 9ec4cb7 and this branch base, so copy only the new files:

```bash
scp BM_Devel_Pi/pool_sequence.py BM_Devel_Pi/rc_preview.py pi@bmcam004:/home/pi/BM_Devel_Pi/
```

```bash
scp tools/pool_mode.sh pi@bmcam004:/home/pi/pool_mode.sh
```

On the Pi:

```bash
cd /home/pi/BM_Devel_Pi && python3 pool_sequence.py --print-config
```

Check the output: `capture_settings.camera_controls.focus` shows mode manual and lens 1.82, `exposure_profile` shows low_gain, and `POOL_CONFIG_HASH` is printed.

```bash
bash /home/pi/pool_mode.sh on
```

```bash
sudo reboot
```

Instead of the reboot you can run `bash /home/pi/pool_mode.sh start`, but only when no RC cycle is running. Then, from the nereus000 console, send:

- status
- preview
- cal 120 s @ 10 s (expect 12 frames)
- mark
- loop 3 @ 120 s
- preview resend
- status

After the dry run, either restore production or leave the unit in pool mode for Tuesday. To restore:

```bash
bash /home/pi/pool_mode.sh stop
```

```bash
bash /home/pi/pool_mode.sh off
```

```bash
sudo reboot
```

`off` restores the exact timestamped crontab backup and verifies it byte for byte. It also checks that camera_config.yaml and the state files are bit-identical to the moment pool mode went on.

### Pass checks

- An `OK id=` line for every id. Replayed ids show ` dup`.
- `/home/pi/pool_runs/<UTC>_<host>/`, inside the newest run folder:
  - `calibration/`: 12 × (.jpg + .dng ≈ 24.0 MB)
  - `loop/`: 3 × (still .jpg + .dng, `_compressed.jpg`, `_compressed.nrjxl`, `_video.mp4` ≈ 7.5 MB)
  - `preview/`
- `run_manifest.json`, per frame `applied`:
  - LensPosition ≈ 1.82
  - AnalogueGain ≤ 1.12
  - ExposureTime ≤ 60000
  - ScalerCrop full sensor for stills
- `run_manifest.json`, other fields:
  - cal `frame_s` < 10
  - loop `iteration_s` well under the period
  - nrjxl `msgs` ≤ 195
  - `free_mb` logged everywhere
- `pool.log` and `console.log` present. `launch.log` under /home/pi/pool_runs/ for cron starts.

## Known limits

- Filenames and manifest times use the Pi clock (no Spotter time read). nereus000 timestamps the console lines independently.
- The 0.031 s/line pacing over ~250 lines has not been measured: Sprint13 measured 0 drops at 0.05 s over 123 lines. Use the resend if lines are lost, or raise `--preview-line-s`.
- Storage estimates use the measured DNG size (24,029,068 B) and an **assumed** 8 MB native JPEG. A full session ≈ 4.5 GB including the 1 GB margin, against ~24 GB free (EM 10/12).
- Production video does not apply the low_gain tuning file. The pool clip does, so the clip and the stills share exposure. If rpicam-vid refuses it, the clip is retried once without controls and that is logged.
- Post-pool: wire `rc_preview` into the supervisor / v9 daemon as a product command, and add it to tools/rc_runtime_manifest.txt.

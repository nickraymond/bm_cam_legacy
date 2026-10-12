# T-DRY (bmcam004 / SPOT-31593C), Mon 10/12, 12:03–12:28 PM PDT: PASS (one known defect, fixed upstream)

Card: docs/em/POOL_PLAN_2026-10-13.md §3 (bm #150).
Code: bm #152 @ **36a4e67**: pool_sequence.py 88bf3e70, rc_preview.py 027fd06e, pool_mode.sh 87d9e549. This is NOT the merged a23f4d6.
Host tools: bm #151 @ 037bb4ae on nereus000.
Evidence (this folder):
- step outputs: tdry_a/b/d/e_*.txt
- tdry_c.log: pool_cmd answers
- tdry_rss.log: /proc samples, 1 s
- pulled/run_manifest.json, pool.log, console.log
- preview_tdry_1.jpg
- gate.log

Approvals: Nick 10:59 AM (b, c, d) + 11:03 AM (console / always-on), APPROVALS.md.

## Sequence (PDT)

| Time | Step |
|---|---|
| 12:03 PM | Pre-pool snapshot (armed production) + deploy + `--print-config` → focus manual 1.82, low_gain, POOL_CONFIG_HASH bbbd992a |
| 12:10:43 | `hil_bus_always_on.sh SPOT-31593C`: controller 0, Pi caught at up=37 s, @reboot disarmed (backup ~/hil_backup/20261012T191046Z), no cycle |
| 12:11:52 | `pool_mode.sh on` + `start` → pid 1081, run 20261012_191155_bmcam004 |
| 12:12–12:24 | 10 commands via `pool_cmd.py` on nereus000 (ids 90001–90010) |
| 12:24:48 | `pool_mode.sh stop` + `off` |
| 12:25 | EM media cleanup (images/videos, 80 % → 6 %) |
| 12:25–12:27 | Re-arm + halt + `hil_restore_schedule.sh` (T-RESTORE) |

## Bars

| Bar | Result | Verdict |
|---|---|---|
| 12 ± 1 calibration DNG+JPEG pairs | 12/12 pairs: DNG 24.0 MB, JPEG 3.06–3.14 MB | PASS |
| 3 × (pjpg + nrjxl + mp4), plausible sizes | nrjxl 53.8–54.0 KB (187–188 msgs ≤ 195), pjpg 47.3–48.7 KB, mp4 7.32–7.33 MB, no video fallback | PASS |
| Every command `OK id=` | 10/10 OK. Every id was also replayed once by the mote and answered ` dup`, not re-run (21 entries in the manifest) | PASS |
| Preview byte-identical on nereus000 ≤ 30 s | 232/232 lines, sha256 identical, 27,823 B q40 640×360. Command 12:12:28 → image complete 12:12:42 ≈ 14 s (Pi ready 5.96 s + console ~7.3 s). The resend of lines 2-4,9 merged into the same image, still OK (4 dup lines, pass 2) | PASS |
| Calibration time per frame ≤ 10 s | frame_s 4.19–5.74 s (median 4.94), slot_late 0 | PASS |
| Loop iteration ≤ 5 min | 64.7 / 73.2 / 67.9 s (nrjxl encode 20–21 s), slot_late 0 | PASS |
| Peak RSS < 150 MB | pool_sequence.py VmHWM **135.5 MiB**; rpicam-still 66.1, ffmpeg 55.7, rpicam-vid 19.6 MiB. Sum of sampled processes at the worst second: 216 MiB (12:16:16), no OOM, no errors in pool.log | PASS (see note) |
| Pi never halted | uptime continuous: status up=27 s → 772 s | PASS |
| Applied settings | 12/12 cal + 3/3 loop stills: LensPosition 1.82, AG 1.1228, ET 3.1–3.3 ms (≤ 60 ms), ScalerCrop full 4608×2592 | PASS |
| After `pool_mode off`: crontab + camera_config hash identical to before | crontab = the pre-`on` state (# DISARMED_BUS_ON line); camera_config.yaml 29d8f099, schedule e8f06056, state_v2 a07c37b1 bit-identical to pool-mode entry | PASS |

**Defect (QE, already fixed in a23f4d6):** `pool_mode.sh off` printed "PRODUCTION MODE: the RC cycle runs at the next boot" while the restored crontab was DISARMED. On 36a4e67 that's misleading. a23f4d6 exits 4 instead. The TE restore re-arms from the hil_bus_always_on backup and checks the result byte for byte against the pre-pool snapshot, so this was caught.

**Note (peak RSS):** the 150 MB bar is met per process. The python process and rpicam-still overlap during a capture (216 MiB combined). On a 512 MB Pi Zero 2W with CMA 256 MB that is worth watching at the pool, but nothing failed here.

## T-RESTORE (bmcam004)

| Bar | Result | Verdict |
|---|---|---|
| Crontab identical to the pre-pool snapshot | re-armed from ~/hil_backup/20261012T191046Z; `crontab -l` byte-identical to the 12:03 PM snapshot (the single active @reboot rc_run_capture_cycle.sh line); `hil_restore_schedule` re-confirmed ARMED in the stub boot | PASS |
| Config hash identical (+ focus manual 1.82) | camera_config.yaml 29d8f099 = pre-pool; effective cfg bbbd992a (focus manual 1.82). state_v2 changed c392942e → a07c37b1 during the 12 PM production wake itself, before pool mode, which is expected (acks / boot counter) | PASS |
| Bus schedule restored | read-back controller 1 / 3600000 / 600000, stub boot halted at 12:27:16 | PASS (read-back); bus-off at 1:10 PM to be observed |
| Next hourly wake delivers a JPEG XL with the expected cfg hash | 1 PM wake | PENDING |

## Spotter battery (SPOT-31593C, Sofar via rig health)

| When | Reading |
|---|---|
| Before | 4.07 V, +1.25 W (11:15 AM); 4.06 V, +0.53 W, in_v 4.5 V (12:04 PM) |
| After | the first row after 12:28 PM is pending (Sofar lag) |

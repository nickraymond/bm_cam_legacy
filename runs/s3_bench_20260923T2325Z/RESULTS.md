# S3 lean benchmark — bmcam004 on SPOT-31593C (2026-09-23T23:41Z – 2026-09-24T00:25Z)

Code: `feature/s3-daemon-every-wake` @ 3acc55e, deployed with `tools/rc_field_update.sh`
(`--profile bmcam003 --leave-disarmed`). Unit config: `capture_mode: video`, `video_tx.enabled: true`,
`bm_serial.image_transmit_delay_seconds: 1.3` (PR #65 decision), `bm_commands.enabled: true`
(tail 150 s), `power_halt` real halt. Spotter bus power held ON (`bridgePowerControllerEnabled 0`);
**cold boot = the Pi halts itself, THEN `bridge cfg commit` cycles bus power** (no hard cut on a running Pi).

All values are `/proc/uptime` seconds from the `[BOOT]` marks. `cron` = `[RC-CRON] uptime_s`.

| Boot | Mode | cron | main() | **[CMD] subscribed** | Spotter UTC | transmit start | halt | sent | tail |
|---|---|---|---|---|---|---|---|---|---|
| 1 | video | 17.88 | 20.79 | **21.44** | 27.52 | 52.18 | 409.0 | 126/126 | 150 s |
| 2 | video | 17.72 | 20.61 | **21.25** | 26.43 | 51.72 | 406.0 | 124/124 | 150 s |
| 3 | video | 17.82 | 20.69 | **21.34** | 26.31 | 51.89 | 407.5 | 125/125 | 150 s |
| 4 | stills | 17.73 | 20.61 | **21.24** | 22.92 | 29.58 | 407.5 | 172/172 | 150 s |
| 5 | stills | 17.92 | 20.84 | **21.46** | 24.89 | 30.64 | 407.2 | 171/171 | 150 s |

Two more logs (20260924T000526Z, 20260924T002337Z) are boots stopped on purpose (mode switch, final
disarm) before transmit; they show subscribed at 21.29 / 21.43 s too. Errors in all 7 logs: 0.

**Result (S3 gate, spec §8):** video_tx wakes now subscribe at ~21.3 s uptime (before S3: never);
stills subscribe at the same ~21.3 s (the daemon start did not move — no regression). The ~18 s before
`main()` is boot + `rc_run_capture_cycle.sh` (`py_compile` of 20 modules) — the optional optimisation
the design lists separately. At 1.3 s pacing a video cycle halts at ~407 s uptime: 206 s on the UART
+ the 150 s listen tail, inside the 480 s cycle budget and the 600 s bus window.

Not measured here: the Spotter-clock anchor (`Bridge bus power: 1` -> decoded UTC) and 10 boots per
mode (the full benchmark; Nick chose the lean run 2026-09-23). Cellular: 3 clips + 2 stills sent.

Incident during the run (mine, no harm): the first script's cleanup used `pgrep -f "rpicam-vid|ffmpeg"`
over ssh, which matched its own remote shell and killed the ssh session (exit 255) after the cycle
had already been stopped; the resume script uses `pgrep -x` and an anchored cycle pattern.

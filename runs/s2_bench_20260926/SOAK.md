# Sprint26 S2 soak — started 2026-09-26 01:00Z (runs without the camera Mac)

| rig | runtime | config | schedule |
|---|---|---|---|
| bmcam003 / SPOT-33507C | S2 4c4fe8f (feature/sprint26-s2-settings) | **config v2** (camera_config.yaml, hash 81e05dee) | every 30 min, 10 min on, aligned (:00/:30) |
| bmcam004 / SPOT-31593C | development 9771d53 (S1) — control | v1 camera_schedule.yaml | same |

Both armed (production crontab), real halt. Changed from the 2026-09-25 state: Spotter
`sampleIntervalMs` 3600000 → **1800000** (Nick 2026-09-26: two transmissions per hour).

Controller = nereus000 (nothing depends on the Mac):
- `bm-heal-driver.service` enabled (console-path rsd heals, both rigs).
- cron `20,50 * * * * /home/pi/s2soak/soak_controller.sh`: sends due queued console
  commands, then `note sync` on both Spotters (Notecard reached 34% on 2026-09-26 00:35
  with extra bursts). Log `/home/pi/s2soak/controller.log`.
- Queued mote-cache test (sent while the bus is OFF, must reach the camera at the next
  wake): 01:20 `twn 2` (id 26101, = the window both units already use: no behaviour
  change) and 01:50 `twn 0` (id 26102, back to no override), to both Spotters.

Evidence for the morning: nereus000 `/home/pi/spotter_logs/<SPOT>/console_*.log`,
`/home/pi/spotter_logs/heal_driver/events.jsonl`, `/home/pi/s2soak/controller.log`;
units `cron_logs/`, bmcam003 `config_journal.jsonl` (expect v8.twn entries from 01:30/02:00),
`camera_config.lkg.json`; backend media rows per unit.

## Stop / restore
- nereus000: `crontab /home/pi/s2soak/crontab_before_s2soak.txt` (was empty: `crontab -r`);
  heal driver may stay.
- Spotters: `bridge cfg set <bridge> s u sampleIntervalMs 3600000` + commit + read back,
  with the Pis disarmed; re-arm in the ~2 min stub window (`rearm_both.sh`).
- bmcam003 back to pre-bench: `/home/pi/s2bench/backup/` (code tar a71b6c7, v1 files,
  crontab_ARMED.txt); `mv camera_config.yaml camera_config.yaml.off_<TS>`.

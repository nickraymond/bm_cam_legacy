# S5 gate — 24 h conductor loop, both rigs (started 2026-09-28 20:32Z)

Gate (DESIGN §8.3 S5): both rigs pass the ladder (done, `runs/s5_console_20260928/`) and
**24 h with 0 lost clips**. Tool: `tools/bm_bench_conductor.py` (ac31802), deployed as
`/home/pi/bm_bench_conductor.py` on nereus000, run as the transient unit `bm-bench-conductor`
(`systemd-run`; gone after a nereus000 reboot, nothing persistent installed).

## Setup (gate.log)

| when (Z) | what |
|---|---|
| 20:21 | both buses held ON (`bridgePowerControllerEnabled 0` + commit; Pis halted, armed) |
| 20:26–20:28 | each unit's armed production wake; in its tail `set mode.run stay_on` (52001 / 62001, cfg f7c9194f) |
| 20:29 | both halted; bm-heal-driver STOPPED (`state.json` → `state.json.conductor`): one heal sender |
| 20:30–20:31 | bus-cycle wake → both run stay_on, trigger-only (interval 0), heartbeat 300, video transmit, runtime 7d30fea |
| 20:32:07 | conductor started: `--hours 24 --min-interval 30 --drain-min 120`, run dir `/home/pi/spotter_logs/conductor/20260928T203207Z` |

Each cycle per rig: heal step (backend candidates → `rsd` on the console before the trigger)
→ `trg 2` (id 2e9 + seconds) confirmed by the console "OK id=" → the backend media row
(matched by capture time) → next trigger ≥ 30 min after the last. Triggering ends ~20:32Z
2026-09-29; the drain (heals only) ends ~22:32Z; then `summary.json`, exit 1 if any clip is lost.

## Watch

```bash
ssh pi@192.168.1.45 'journalctl -u bm-bench-conductor -n 30 -o cat'
```

```bash
ssh pi@192.168.1.45 'python3 /home/pi/bm_bench_conductor.py --report /home/pi/spotter_logs/conductor/20260928T203207Z'
```

## Stop early / restore (after the run)

1. `sudo systemctl stop bm-bench-conductor` (nereus000).
2. Each unit: console `reset` of `mode.run` (e.g. `{"id":52010,"c":"reset","k":["mode.run"]}`)
   → exit 72 → per_boot baseline wake, real halt (the unit stays armed).
3. With the Pi halted: `restore_schedule.sh <host> <bridge> <SPOT>` (bmcam003
   c3c564b91856226c SPOT-33507C; bmcam004 0e582dd12c1e1480 SPOT-31593C). Read back
   1 / 3600000 / 600000.
4. bm-heal-driver: back up `state.json`, start it again.

Cellular cost: ~185 msgs per clip, at most 48 clips per rig in 24 h (about 2× the hourly schedule).

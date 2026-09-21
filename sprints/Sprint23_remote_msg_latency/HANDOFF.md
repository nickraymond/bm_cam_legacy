# Sprint23 — Handoff (to Sprint24 outdoor HIL, and to the dashboard session)

Written 2026-09-21 ~17:40Z. Evidence and numbers: `RESULTS.md`. This file is
what to DO with them.

## 1. What is proven, what is not

| Claim | Status | Evidence |
|---|---|---|
| Sofar API → Ebox console delivers on v2.16.8 | PROVEN, 13/13 | `runs/remote_msg_latency/latency_summary.csv` |
| A wedged cloud mailbox is cleared by `clear_command_queue` | PROVEN once (SPOT-31593C, 0/16 ever → delivered in 46 s) | RESULTS "Deliveries" #1 |
| Mailbox check happens only after a standard `MS_Q_LEGACY` report, hourly | PROVEN on this unit, 8 hourly reports | RESULTS findings 2, 6, 8 |
| `bm …` line arriving bus-OFF is held (`Queuing serial command`) | PROVEN, USB and remote | RESULTS Step B run 2 |
| Held commands replay ~8 s after `Bridge bus power: 1`, silently | PROVEN once, 2 commands | RESULTS Step B run 2 |
| Pi listens 38.0 ± 1.1 s after bus power → held commands are lost | PROVEN, 2/2 lost, `frames=0` | RESULTS Step B runs 1–2 |
| Command arriving LIVE with a listener is acked | PROVEN 7/7, ack in 0.48 ± 0.36 s, all 7 acks at the Sofar API | RESULTS Step C |
| Report minute = post-boot report rounded up to a 5/10-min boundary | HYPOTHESIS. Prediction: 18:20:00Z on 2026-09-21 | RESULTS finding 10 |
| Works with halt ON / a real settings command / a field schedule / video_tx | NOT TESTED | — |

## 2. State of the bench when this was written

Processes on Nick's Mac (mine — stop them before taking the port):

| What | PID / address | Stop |
|---|---|---|
| Serial monitor, `--only SPOT-31593C`, log root `~/spotter_logs` | PID 52489 | `kill 52489` |
| Tester server | `127.0.0.1:8771` | `pkill -f remote_msg_tester/server.py` |

Spotter SPOT-31593C, bridge `0e582dd12c1e1480` — I changed three keys (system
partition). Current → original:

```text
bridgePowerControllerEnabled  1        -> 0   (bus always on)
sampleIntervalMs              600000   -> 3600000
sampleDurationMs              360000   -> 900000
```

Restore (USB console, via `cmd.txt`; the commit forces the bus ON 120 s and
triggers an uplink + mailbox check):

```text
bridge cfg set 0e582dd12c1e1480 s u bridgePowerControllerEnabled 0
bridge cfg set 0e582dd12c1e1480 s u sampleIntervalMs 3600000
bridge cfg set 0e582dd12c1e1480 s u sampleDurationMs 900000
bridge cfg commit 0e582dd12c1e1480 s
```

Unverified since Nick pulled the SD card and the unit rebooted 3× (16:29,
16:52, 17:09Z): re-read with `bridge cfg status 0e582dd12c1e1480 s`.

bmcam003 (node `53171fa3d81a8e6f`) — changed from how I found it:

| Setting | Found | Now | How it was changed | Restore |
|---|---|---|---|---|
| `capture_mode` | `"video"` | `"progressive_jpeg"` | camera settings page :8080 | on the Pi: `cp ~/BM_Devel_Pi/camera_schedule.yaml.before_gui_20260921T062630Z ~/BM_Devel_Pi/camera_schedule.yaml` (the page only runs in video mode) |
| `twn` (command state) | 0 (10:00–15:00 ET) | 2 (all day) | `bm pub bmcam/cmd {"id":2302,"c":"twn","v":2} 1 1` | same with `"v":0` and a new id |
| `hlt` | 3 (halt OFF) | 3 — untouched | — | — |

Command ids used: 2301–2312. 2312 has no observed outcome. Start Sprint24 ids
above 2400.

## 3. Running the 1-ping/hour overlay (Sprint24 decision 5)

```bash
python3 tools/spotter_serial_monitor.py --log-root ~/spotter_logs --only SPOT-31593C
python3 tools/remote_msg_tester/server.py --log-root ~/spotter_logs
sprints/Sprint23_remote_msg_latency/overnight_loop.sh 2401 2412
```

- `overnight_loop.sh` sends `ping` only, one in flight, next send only after
  the previous watcher returns. Output: `runs/remote_msg_latency/overnight_<date>.log`.
- `watch_delivery.sh <id> [SPOT-ID] [pi-host]` is read-only. Its ack detector
  matches a `transmit-data … Len: 11x` line — the ping-ack size. On a rig
  that is also sending video chunks, confirm the ack from the Pi log or
  `tools/sofar_poll_acks.py` instead.
- Two Spotters: the loop is hard-wired to SPOT-31593C (`SPOT=` at the top).
  For the second rig copy the variable, do not glob.
- The tester blocks a send when the console log is > 60 s stale. A sleeping
  laptop ends the run (lost run 8). Log from the monitoring Pi.

## 4. Things that will bite

1. Any `bridge cfg commit` = bus forced ON 120 s + LEGACY uplink + mailbox
   check. It invalidated 3 tests. Do not commit with a command in flight.
2. The hourly report minute is per-unit and moves on reboot. It is the minute
   commands need and the minute bursts lose chunks (`MS_Q_CELLULAR_ONLY is
   full`: 301 lines overnight on this rig).
3. With a 16-min bus period the phase walks, so most commands will arrive
   bus-OFF → held → replayed at ~8 s → lost unless the listener is up by then.
   Expect a LOW ack rate in the baseline. That is the finding, not a bug in
   the overlay. Log `held|live` per command.
4. A stills unit outside its transmit window exits in 3.1 s and is deaf
   (finding 7).
5. `note sync` does not cause a mailbox check.
6. `cmd.txt` is one slot per Spotter. Two sessions writing it race.
7. Claude sessions may be blocked from state-changing SSH on the Pis; the
   camera settings page and `bm pub` commands are the paths that worked.

## 5. For the dashboard session (original sprint goal)

- Endpoint + body + limits: `docs/sofar_command_api_reference.md`; sender:
  `tools/sofar_send_command.py::post_command`. Token by env name only.
- State machine to port: `tools/remote_msg_tester/server.py` (`sent →
  received [→ released]`), plus the missing piece: **ack**, read from
  `GET /api/sensor-data` via `tools/sofar_poll_acks.py::fetch_acks`
  (≤ 32 min backend lag seen).
- Without a USB console in the field, the dashboard cannot see Ebox arrival.
  The only field-visible signals are HTTP 202 and the ack. Design the UI
  around those two, with retry-until-ack on the same id (the daemon dedupes).
- Tell the operator the expected wait: up to 60 min for the next hourly
  report, +72 s, and only if the unit is awake and listening at that moment.
- Open TODOs: TODO-BM-016 (cloud-init, ~6 s of boot).

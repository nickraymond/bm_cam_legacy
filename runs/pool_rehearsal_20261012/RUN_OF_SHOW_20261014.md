# Pool run of show: Wed 10/14 (bmcam003 + bmcam004)

**Pool:** 1 PM. Runbook: `docs/em/POOL_DAY_RUNBOOK_2026-10-13.md` (its times were written for Tue 10/13; this sheet re-times them).
**Code:** development fdeb01a, keys ON. Focus 0.8 via `~/pool_args`. Nick's approval: APPROVALS.md, 10/14 ~9:15 AM.

## How we work together

| Who | Does |
|---|---|
| **Nick** (pool deck, phone in this chat) | Everything physical: hotspot, power, Spotters, board, card, diving. Eyes on the viewer. Sends one-liners: `board ready`, `card at 1m`, `card OK`, `card at 2m`, `out of water`, or anything odd |
| **TE2** (this session, on the Mac at the pool) | Runs every command. Checks each answer against its pass bar. Posts ONE status line per step (`PASS …, next: …`). Keeps the clock and the stop rules. Asks before anything outside the approved line |
| **EM** | Milestone updates, backend/dashboard checks |

- **The Mac comes to the pool:** on power, lid open, on the hotspot. This session runs on it, and the download lands on it.
- `pc` = `ssh pi@<nereus000> python3 /home/pi/pool_tools/pool_cmd.py`.

## Timeline

| Time (PDT) | Who | Step / command | Test | Takes | Pass |
|---|---|---|---|---|---|
| by 10:45 AM | LTE session / Nick | Spotters back on nereus000's hub, consoles live (ttyACM0/1) | gate | — | both consoles logging |
| 11:00–11:08 | units | normal 11 AM production wake | — | 8 min | halts |
| 11:10 | TE2 | `pool_day_0b.sh hold` (bus held on both) | — | 2 min | `bus held ON … up + disarmed` ×2 |
| 11:13 | TE2 | `pool_day_0b.sh sha` | deployed = development | 1 min | 9 MATCH, ALL MATCH |
| 11:15 | TE2 | `pool_day_0b.sh on` (args --lens-position 0.8, on, start) | — | 2 min | `wrote … 0.8`, `started pid` ×2 |
| 11:18 | TE2 | `pool_day_0b.sh status` | — | 1 min | `phase=idle cal=0/0 loop=0/0 lp=0.8` ×2 |
| 11:20 | TE2 | `pool_day_0b.sh memloop` (1 loop iteration on bmcam003, then a fresh run folder) | **B1: -p JPEG XL memory in pool mode** (never run before: loops encode with -p now) | 5 min | nrjxl made, no ERR/FALLBACK, cjxl ≤ 150 MiB, MemAvailable ≥ 50 MiB |
| 11:27 | TE2 | `pool_day_0b.sh preview bmcam003` / `bmcam004` | preview path | 4 min | both OK in viewer :8090 |
| 11:32 | TE2 | final status | — | 1 min | idle, cal=0/0, lp=0.8 |
| 11:35–12:30 | Nick | unplug the Spotters from the hub, pack (Mac + charger, nereus000 + 3 A supply, Spotters, battery pack, ChArUco board, v3 card), transport. Bus stays held | — | ~55 min | — |
| 12:30 PM | Nick | hotspot ON (Maximize Compatibility), power nereus000 + hub, plug in the Spotters, Mac on power | — | 10 min | — |
| 12:40 | TE2 | find IPs, `pc both status`, Spotter BATT_V | setup | 5 min | lp=0.8, cal=0/0, consoles live, BATT_V ≥ 3.80 V |
| **1:00** | Nick `board ready` → TE2 `pc both cal` | | **T1 calibration** (board, 15–20 poses) | **20 min**, auto-stop ~1:20 | 120 ± 2 frames each, frame_s ≤ 10 |
| 1:25 | Nick `card at 1m` → TE2 `pc both sweep` | | **T2 focus sweep @ 1 m** | ≤ 3 min | `OK … pool sweep … n=` ×2 |
| 1:28 | TE2 `pc both preview` → Nick `card OK` | | **T3 framing preview** | 2 min | card in frame, both OK |
| **1:30** | TE2 `pc both mark card_1m` + `pc both loop` | | **T4 card loop** (still pjpg + JPEG XL + 30 s video per iteration, every 10 min) | **3 h** (18 iterations) | 18/18 each, no ERR |
| 1:31, then every 30 min | TE2 | status + memory read on iteration 1 | B1 again, under water | 1 min | as B1 |
| **3:00 PM** (loop 9/18) | Nick moves the card → `card at 2m` → TE2 `pc both mark card_2m` | | T4, 2 m half | — | mark OK |
| 4:30 | TE2 | loop 18/18 → `pc both sweep` | **T5 focus sweep @ 2 m** | 3 min | OK ×2 |
| 4:35 | Nick `out of water` → TE2 status (new IPs) | | — | 2 min | — |
| 4:40 | TE2 | `pool_pull.py … --dry-run`, then the real pull | **T6 download** | ~15 min (measured 12) | `[pool_pull] OK` ×2, sha verified; DNGs stay on the units |
| 5:00 | TE2 | §9 restore, swapped pairs (stop → off → ARMED check → halt → `hil_restore_schedule`) | **T7 back to production** | 15 min | `PRODUCTION schedule confirmed` ×2, cfg 0cb7ad9e |
| 6:00 PM | units | first production wake | T7 | — | new JPEG XL from each unit on the dashboard |

- **Shorter session (Nick's call):** 1 h at 1 m + 1 h at 2 m = 12 iterations.
  - Move the card at 2:30.
  - End with `pc both stop` at 3:30.
  - Everything after shifts 1 h earlier.

## Stop rules (TE2 calls them)

- `lp` ≠ 0.8 at any status → STOP. The pool would run at the config focus 1.82; restart it with the args.
- B1 fails on the bench (cjxl killed/over the bar, MemAvailable < 50 MiB):
  - tell Nick before transport;
  - option = pool without -p (needs `pool_mode` off → a production wake → `set still.raw.progressive false` → back to pool mode, ~1 h).
- Spotter BATT_V < 3.80 V → `pc both stop`, go to download + restore.
- Any `[ERR]`/OOM, or a unit missing 2 statuses in a row → pause, runbook §10.
- A Spotter reboot or replug restarts the pool process: a new run folder, and noted in the log.

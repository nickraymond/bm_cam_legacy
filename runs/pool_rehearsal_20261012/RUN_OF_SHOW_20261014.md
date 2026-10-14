# Pool run of show: Wed 10/14, rev 2 (Nick's order; leave the pool at 3 PM; overnight loop in the pool)

**Units:** bmcam003 (SPOT-31593C) and bmcam004 (SPOT-33507C).
**Code:** fdeb01a, keys ON; focus 0.8 via `~/pool_args`. Approval: APPROVALS.md, 10/14 ~9:15 AM.
**Runbook:** `docs/em/POOL_DAY_RUNBOOK_2026-10-13.md` (Tue times; this sheet re-times them).

## How we work together

| Who | Does |
|---|---|
| **Nick** (pool deck; phone in this chat) | Everything physical: hotspot, power, Spotters, card, units in/out of the water. Eyes on the viewer. One-liners: `card at 1m`, `card OK`, `card at 2m`, `units out`, `units in`, or anything odd |
| **TE2** (this session, on the Mac at the pool) | Runs every command. Checks each answer against its pass bar. One status line per step. Keeps the clock and the stop rules. Asks before anything outside the approved line |

- `pc` = `ssh pi@<nereus000> python3 /home/pi/pool_tools/pool_cmd.py`.
- **A loop can run once per run folder.** A second `loop` is refused ("run already has loop frames"). Each new loop therefore needs a pool restart (`pool_mode.sh stop` + `start`, over ssh, so the unit must be on Wi-Fi) or a Spotter reboot.

## Camera IPs (step d)

Nothing to set:
- The units, nereus000 and the Mac all auto-join the hotspot. It uses the home SSID; the units' profiles were verified 9 AM today.
- Local IPs: `pc both status` prints `ip=` for each unit.
- Fallback that works on any network with internet: Tailscale **bmcam003 = 100.108.182.50**, **bmcam004 = 100.79.38.9**. The Mac is on the tailnet; `ssh pi@bmcam003` just works.
- nereus000 is on the tailnet once the LTE session finishes.

## Timeline

| Time (PDT) | Who | Step | Test | Takes | Pass |
|---|---|---|---|---|---|
| by 10:45 AM | LTE session / Nick | Spotters back on nereus000's hub, consoles live | gate | — | ttyACM0/1 logging |
| 11:10 | TE2 | `pool_day_0b.sh hold` → `sha` → `on` → `status` | bench §0B | 10 min | bus held ×2, ALL MATCH, `lp=0.8 cal=0/0 loop=0/0` |
| 11:20 | TE2 | `pool_day_0b.sh memloop` (1 loop iteration on bmcam003, then a fresh run folder) | **B1: -p JPEG XL memory in pool mode** (new combination) | 5 min | nrjxl made, no ERR/FALLBACK, cjxl ≤ 150 MiB, MemAvailable ≥ 50 MiB |
| 11:27 | TE2 | one preview each, final status | bench preview | 5 min | both OK in viewer :8090 |
| 11:35–12:30 | Nick | unplug the Spotters from the hub, pack (Mac + charger, nereus000 + 3 A supply, Spotters, battery pack, card), transport. Bus stays held | — | ~55 min | — |
| 12:30 PM | Nick | hotspot ON (Maximize Compatibility), power nereus000 + hub, plug in the Spotters, Mac on power | setup | 10 min | — |
| 12:40 | TE2 | IPs, `pc both status`, Spotter BATT_V | setup | 5 min | lp=0.8, consoles live, BATT_V ≥ 3.80 V |
| (1:00 | Nick + TE2 | **T1 calibration** `pc both cal` (board, 20 min) | optional, not on Nick's list | 20 min | 120 ± 2 frames) |
| **1:00** | Nick `card at 1m` → TE2 `pc both preview` → Nick `card OK` → TE2 `pc both sweep` | | **(a) framing check + focus sweep @ 1 m** | ~6 min | preview OK ×2; `OK … pool sweep … n=` ×2 |
| 1:10 | Nick `card at 2m` → preview → `card OK` → sweep | | **(b) framing check + focus sweep @ 2 m** | ~6 min | same |
| 1:20 | TE2 `pc both mark card_2m` + `pc both loop --iterations 3 --period-s 120` | | **(c) 3 × (still pjpg + JPEG XL + 30 s video) @ 2 m** | ~7 min | `loop=3/3` ×2, no ERR |
| 1:30 | Nick `units out` → TE2 status (IPs), `pool_pull.py … --dry-run`, then the pull | | **(d) download to the Mac** (DNGs stay on the units) | ~10–15 min | `[pool_pull] OK` ×2, sha verified |
| 1:45 | TE2 + Nick | quick look at the 1 m / 2 m stills and sweeps on the Mac; TE2 reads B1 memory from pool.log | review | 10 min | card readable, focus OK |
| 1:55 | TE2 | **restart pool on both** over ssh (fresh run folder for the overnight loop), status | prep (e) | 3 min | `lp=0.8 cal=0/0 loop=0/0` ×2 |
| 2:00 | Nick `units in`, card at 2 m → TE2 `pc both mark overnight_2m` + `pc both loop --iterations 18 --period-s 3600` | | **(e) overnight loop, 1 h cadence** (18 h → ~8 AM Thu) | starts now | `start iterations=18 every=3600s` ×2 |
| 2:05–2:45 | TE2 | watch iteration 1 (status, viewer), Spotter BATT_V, nereus000 VIN/VBAT | (e) health | — | loop=1/18 ×2, no ERR |
| **3:00 PM** | Nick | leaves; hotspot leaves with him | — | — | — |
| evening | TE2 (remote: nereus000 over LTE + Tailscale) | hourly `pc both status` + power readings; Nick decides 30 min | (e) | — | — |
| Thu AM | Nick + TE2 | units out → pull the overnight run → §9 restore to production | download + T7 | ~45 min | production confirmed, cfg 0cb7ad9e |

## Overnight: what to know now

- **No ssh to the units tonight.** They're underwater, and the hotspot leaves at 3 PM. Only console verbs work, over nereus000 (LTE + Tailscale): status / mark / stop / preview / loop.
- **Switching to 30 min in the evening:** a running loop can't change period, and a new loop needs a new run folder. Two ways:
  1. **Spotter reset (recommended).** `pc both stop` → console `reset` on both Spotters. The Pi reboots in pool mode and comes up with a new run folder (runbook §2.3). Then `pc both loop --iterations 30 --period-s 1800`.
     - ~5 min, done remotely.
     - Cost: an idle Pi gets a power cut (small SD risk) and one 2-min reboot.
     - **Needs Nick's OK for the Spotter console `reset`.** It's not in today's line.
  2. Decide 30 min before 2 PM and start the overnight loop at `--period-s 1800`. Downgrading later = `pc both stop` (no images after that).
- **Disk:** ~35 MB per iteration (DNG + JPEG + JXL + video). 18–30 iterations ≈ 0.6–1 GB per unit, against 100 GB free.
- **Memory:** T-ENDURE 6.9 h showed a flat VmHWM. 18 h is longer than tested: TE2 watches status, and B1 covers -p.
- **Power:** the bus is held all night. The Spotters charge from nereus000's hub; nereus000 is on the 3 A supply plus the LTE modem. TE2 reads Spotter BATT_V and nereus000 VIN/VBAT hourly.

## Stop rules (TE2 calls them)

- `lp` ≠ 0.8 → STOP. The pool would run at focus 1.82; restart with the args.
- B1 fails on the bench → tell Nick before transport. Option: pool without -p, which takes ~1 h via a production wake + `set still.raw.progressive false`.
- Spotter BATT_V < 3.80 V, or nereus000 VBAT falling with VIN < 4.9 V → `pc both stop` (remote), report.
- Any `[ERR]`/OOM, or a unit missing 2 statuses in a row → pause, runbook §10, tell Nick.

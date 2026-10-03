# G4 — outdoor tethered 12 h (Release R1)

When: **Fri 10/2 21:00 PDT → Sat 10/3 09:00 PDT (T0 = the 04:00Z wake, end 16:00Z)** (Nick 2026-10-02 evening: start tonight; 21:00 because the alternator needed a dry run), 12 h of capture + a 3 h completion tail. Owner: Test Engineer + Nick (puts
the box outside). Units: bmcam003 (SPOT-33507C), bmcam004 (SPOT-31593C), both on **RC1** (the
development tip frozen Mon 10/5 EOD). Mains power, nereus000 on both USB consoles.
Plan: RELEASE_PLAN §2 (G4), D1, D3, D4, D5. Evidence: `runs/g4_outdoor12h_<YYYYMMDD>/`.

## Pass (RELEASE_PLAN)

D1, D3, D4 hold for 12 h; no bus drops.

## Criteria

| id | criterion | PASS when | evidence |
|---|---|---|---|
| G4.1 | production config (D5) | both bridges read back `bridgePowerControllerEnabled 1`, `sampleIntervalMs 3600000`, `sampleDurationMs 600000`; heal cap 24/day per Spotter in the backend settings; units per_boot, cron armed, real halt; runtime sha == RC1 on both | `snapshots/*_g4_start_*`, `console/bridge_readback.txt`, `api/gateway_settings.json` |
| G4.2 | every wake happened (no bus drops) | each unit: one bus-on window per hour, 12/12 (console `power on for` / bus lines), and a `<WS>` or START per window; 0 unscheduled bus-off events | `analysis/windows.csv` |
| G4.3 | D1 complete within 3 h | 100 % of media from COMPLETE cycles (not cut by a power event, G4.10) captured in the 12 h are complete at the backend ≤ 3 h after capture (`captured_at` → complete time) | `analysis/media.csv` |
| G4.4 | D1 0 redundant heals | 0 heal commands asking for chunks the backend already held at send time | `analysis/heals.csv` |
| G4.5 | heal cap respected | heal commands per Spotter ≤ the cap in any 24 h window | `analysis/heals.csv` |
| G4.6 | D3 on-demand capture | ≥ 3 `trg` per unit (console lane, inside a bus window), each → one media row, complete ≤ 3 h | `steps.log`, `analysis/media.csv` |
| G4.7 | D4 visible | every trg, ack and heal in G4.4–G4.6 is on logs.html (full list, not a sample) | `analysis/logs_check.csv` |
| G4.8 | 0 SSH needed | no write over ssh to a unit during the 12 h + tail | `gate.log` |
| G4.9 | thermal / power sanity | no Pi undervoltage / thermal throttle in the cycle logs; Spotter `post` clean at start and end (spotter-health-check skill) | `console/post_*.txt` |
| G4.10 | Spotter power-cycle stub cuts (Nick 2026-10-02) | each event logged as an OBSERVED EVENT (time, unit, cut or protectively halted, next wake normal?). FAIL only if it loses data beyond that one cycle or damages the SD/filesystem (`fsck`/journal errors at the next boot, missing/corrupt state or config files). No protective SSH halt of a stub-booted Pi (Nick 2026-10-02: the Pi tolerates mid-cycle cuts; the stub guard is dropped, not R1.1) unless something is actually going wrong | `gate.log` "stub event" lines, next-boot log |

## Preconditions

- [ ] G3 PASS; freeze done (RC1 sha recorded; release notes exist).
- [ ] Production switch done at freeze (Mon EOD): bus schedule restored (`runs/s5_console_20260928/restore_schedule.sh`
      pattern: unit HALTED + DISARMED, commit, re-arm in the ~2 min stub window); heal cap 24/day set in the
      per-Spotter settings (backend, S6b backend session's admin API); `BM_HEAL_AUTOSEND` live; bm-heal-driver
      and the conductor STOPPED (backend is the only heal sender).
- [ ] Nick has the box outside, mains connected, console cables to nereus000 checked (`hil_console.sh <SPOT> post`).


## Plan as amended by Nick (2026-10-02 evening, via the EM) — supersedes the D3/console lines below

1. **Mixed media:** still ↔ video alternate every hour, driven by BACKEND remote-config commands
   (`set mode.media`, `/devices/{d}/remote-config/changes` + `/admin/.../send`, Sofar lane) sent while the
   units are off (:20), so the command waits for the next wake. Tool: `hil/tools/hil_g4_alternator.py` on
   nereus000 as `hil-g4-alternator.timer` (hourly :20 UTC, until 15:00Z; target = still on even UTC
   hours, video on odd; allow-list BMCAM_003/004; 409 rate_limited → retry the same id).
2. **D3:** `trg` via the backend (Sofar lane), 3 per unit, sent by the same timer at 05:20, 09:20, 13:20Z
   (for the 06:00, 10:00, 14:00 wakes), ≥ 70 s after that hour's media change (65 s Spotter guard).
3. **Self-heal:** backend auto-send as configured (cap 24/day per Spotter).
4. **Console: OBSERVE ONLY** (bus windows, queue-full, resets, health checks). No console sends during G4.
   `post` (G4.9) is therefore N/A; Spotter health = passive console log (errors, rebootctl, charge mode).

Added criteria:

| id | criterion | PASS when | evidence |
|---|---|---|---|
| G4.11 | commanded media = captured media | for every wake, the START type (still `IMG …jpg` / video `…h264`) equals the media commanded for it, allowing the measured command→effect lag (reported in wakes, per unit); a wrong media beyond that lag = FAIL | `alternator.jsonl` + console START lines |
| G4.12 | every backend command acked or confirmed | each media / trg command: ack at the backend, or (ack lost at the Spotter, F-G3-10) its effect confirmed by a later `<WS>`/START hash | backend command log, `analysis/commands.csv` |

Also measured: a still cycle's wake→halt vs the 10-min window (budget `still.budget_min` 8), and any 409
collisions between the timer's sends and heal sends.

## Nick's physical steps (done 2026-10-02 15:38–15:55 PDT)

The units run the production schedule: the bus is ON only :00–:10 each hour, and each Pi halts itself
before :10. **Only move hardware between :12 and :55 past the hour** (bus off, Pis halted = safe to
unplug). Never unplug during :00–:10 (an SD hard cut mid-write).

1. Between 07:12 and 07:55 PDT: move both Spotters + bmcam003/004 (still connected by their BM bus
   cables) into the outdoor box. Keep each Spotter's mains/tether power connected if possible; if a Spotter
   must be unplugged, do it inside the off window (its bridge config is on flash and survives).
2. Antennas: both Spotters with open sky (cellular + GPS).
3. nereus000: powered, with BOTH USB console cables (SPOT-33507C → hub port 1.2, SPOT-31593C → 1.3, as
   now) and on the LAN/Wi-Fi (the Test Engineer reaches it at 192.168.1.45). If the box location has no
   LAN Wi-Fi, tell the EM before moving: G4 then runs without consoles (G5-style evidence only).
4. Tell the EM "moved" with the time. The Test Engineer checks `post` on both consoles and watches the
   08:00 window; first G4 capture = 08:00.
5. Do not touch the units during 08:00–20:00 unless the Test Engineer asks via the EM.
6. **Never turn a Spotter's power switch off** (2026-10-02 shakedown: the move left both Spotters in
   CHARGE MODE, off, for 25 min; a wake was lost). After any Spotter power cycle the bridge opens a
   120 s bus stub: an armed Pi boots in it and is cut at its end; that is logged as an observed event (G4.10).

## Fallback: nereus000 unreachable at the box (decided 2026-10-02 evening, EM)

nereus000 lost its link at the outdoor box (Wi-Fi 55–59 there vs 76 indoors; down 17:22 PDT). If it is
not fixed by Sat 07:30, G4 runs **G5-style**:
- Evidence = backend media/heal/command rows, pulled by the EM (the admin token lives only on
  nereus000, so the Test Engineer cannot read the backend without it). Bus windows / wake→halt come
  from the unit's own cycle logs, read-only over the tailnet during a wake.
- D3 triggers (G4.6) go over the Sofar lane, sent by the EM's backend access (same reason).
- G4.2 (every wake), G4.9 (`post`) become "from backend + unit logs" (no console); queue-full counts
  (F1) are not measurable without the console: G4 reports them as N/A.

## Steps

1. T−30 min: `hil_unit_snapshot.sh` can't be taken on a halted unit: take it inside the first window
   (read-only, < 1 min) or rely on the freeze snapshot + START `cfg=` hash. Read back both bridges'
   bus config over the console. `post` on both Spotters.
2. T0 = the first aligned window (hh:00). Log start in `gate.log`.
3. Every window: no action unless a D3 trigger is scheduled. D3 triggers at T0+2 h, +6 h, +10 h:
   `hil_cmd.sh G4.trg.<n> '{"id":<id>,"c":"trg","v":2}' 8 <SPOT>` right after `[CMD] subscribed`
   (queue only while the bus is OFF is lost: bench-gotchas).
4. Watch (every ~2 h): console queue-full counts, bus windows, backend media completeness. No
   intervention unless a unit stops waking for 2 windows (then: BLOCKED + console `post`, and tell the EM).
5. T0+12 h: stop counting captures. Tail: +3 h for completeness. Then pull the analysis.

## Watch item: heal backlog vs the 40-chunk/wake cap

Shakedown 2026-10-02 outdoors: normal clips arrived 15–27 % short (28–50 of 182–187 chunks). If G4 shows
the heal backlog growing against `max_chunks` 40/wake, report the numbers for raising it (design ceiling
60) against the 10-min window: each +20 chunks ≈ +26 s on a ~8.5-min wake→halt. A Nick decision before
G5, not a G4 change.

## Analysis

- `media.csv`: device, key, captured_at, first chunk at, complete at, chunks, healed (y/n), latency.
- `heals.csv`: command id, sent at, keys/chunks asked, chunks already held at send (redundant if > 0), ack.
- `windows.csv`: Spotter, window start, bus on/off lines, Pi WS/START seen.
Reuse: `tools/bm_bench_conductor.py --report` patterns and the staging admin heal-candidates API
(token on nereus000 only); copy any script used into `hil/tools/` first.

## Restore

The units stay in production config for G5 (no change between G4 and G5 except the power source and
removing nereus000). If G4 FAILs: Wed 10/7 is the slack day: fix, re-run G4 (12 h) only if the fix
touched the units.

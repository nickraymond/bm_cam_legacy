# S6b HIL — backend heal auto-send, R4 + R5 24 h loop (G1) — RESULTS

Run: 2026-10-01 05:30:33Z → **2026-10-02 05:30:01Z** (22:30 PDT, cut with **no drain**, Nick's call
via the EM at 22:12 PDT). Indoors, both bench rigs. Plan: PLAN_S6.md §9.14 (H3 → H4) and §5 step 8
(R5); Release R1 gate G1 (bm PR #99).

| | bmcam003 | bmcam004 |
|---|---|---|
| Spotter | SPOT-33507C | SPOT-31593C |
| runtime / config | development 3c1801d / 580ce986 + `{mode.run: stay_on}` (f7c9194f) | same |
| mode | stay_on, trigger-only, held bus, cron armed | same |

Triggers: conductor `--no-heal`, 30 min, console `bm pub` (nereus000). Heals: **only the backend**
(`BM_HEAL_AUTOSEND=1`, devices BMCAM_003,BMCAM_004, MAX_PER_DAY 96, REASK_S 5400; web REASK_S 5400),
sent through the Sofar API. bm-heal-driver stopped; no Mac/GUI Sofar sends; no API heals.

## Verdict

| Gate | bmcam003 | bmcam004 |
|---|---|---|
| **R4** (PLAN_S6 §9.14 H4, 6 criteria) | **PASS** | **PASS** (#1, #3–#6) · #2 needs Nick's cron line for 100073 |
| **R5** 0 clips lost | **PASS** (0 lost; 1 partial + 1 in flight = stopped early) | **FAIL**: 4 clips never reached Sofar, **all Spotter-side** (below) |
| **R5** 0 redundant heals | **PASS** (S6b judged, 8 re-asks all by the rules) | **PASS** |
| **R5** every command on logs.html | **PASS** at the data level (sent_via=sofar, 202, last_hl_action) | **PASS** at the data level |

**The backend half of G1 passes on both rigs.** Every clip the backend ever saw was healed by
the backend alone, or was still in progress at the cut. The R5 loss on bmcam004 is a Spotter
firmware failure: the backend never saw those clips, so no heal path could have recovered them.

## 1. Triggers and clips (backend truth at the cut, `r5_snapshot_cut.json`)

| | bmcam003 | bmcam004 |
|---|---|---|
| triggers sent / acked first try | 48 / 48 | 46 / 46 |
| media row at the backend | 47 | 41 |
| complete on the first send | 8 (17 %) | 25 (61 %) |
| **complete at the cut** | **46** | **41** |
| partial at the cut, being healed: "stopped early (no drain, Nick)" | 1 (56302, 04:57Z clip, 18 missing; heal 100121 sent 05:20Z) | 0 |
| in flight at the cut: "stopped early" | 1 (trigger 05:28:05Z) | 1 (trigger 05:27:56Z) |
| **lost: never reached Sofar** | **0** | **4** (01:23, 02:09, 02:55, 03:41Z) |
| trigger → row, p50 | 797 s | 796 s |

The conductor's state.json holds 47 / 45 triggers. The two 05:28Z triggers were published and
acked (events.jsonl) and were cut mid-cycle.

## 2. Backend heals (auto-send)

| | bmcam003 | bmcam004 | all |
|---|---|---|---|
| heals issued (autosend) | 43 | 17 | **60** |
| Sofar 202 | 43 | 17 | **60 (100 %)** |
| arrived at the Spotter (`Remote message received`) | 43 | 17 | **60 (100 %)**; the console shows exactly 43 / 17 remote messages, no duplicates |
| accepted by the camera (`OK id=… rsd`) | 43 | 17 | **60 (100 %)** |
| `<HL a=sent r=ok>` reached the backend | 40 | 15 | 55: the other 5 `<HL>` uplinks were lost at the Spotter (queue full) |
| media complete afterwards | 42 | 17 | 59 (+1 in progress at the cut) |
| chunks asked | 609 | 244 | 853 |
| non-202 / rate-limited / auth-failed / 409 / other_sender_active | 0 | 0 | 0 |

Re-asks (S6b, from heal-events): 8.
- 5 were released early by `<HL a=sent r=ok>` and re-asked only for chunks still missing:
  0e26dt, 0e297h, 0e2lxq (F9 second part), 0e2vuw, 0e3a13, all on bmcam003.
- 3 waited ≥ REASK_S with no release (5402 / 5698 / 5462 s): 0e2ncu (bmcam003), 0e2evc and
  0e3a1d (bmcam004). The first heal was sent r=ok, but its `<HL>` and part of its chunks were
  lost in the Spotter queue.
- Each re-ask was a strict subset of the first ask, and each clip completed only after the
  re-ask reached the camera. **Redundant heals: 0.**

R4 #2 evidence (Nick, cron log):
`[heal_autosend] device=BMCAM_003 decision=sent id=100079 http=202 chunks=38 attempts_24h=0 received_age_s={"0e1yjd": 879.1, "0e1zb5": 877.9}`.
BMCAM_004's first send (100073, 07:00:39Z): **its received_age_s line is still to be read from
the cron log**. By design the walk never asks a key that is still arriving.

## 3. Latency per hop (Nick's numbers for Matt K)

From the backend's Sofar 202 (`sent_at`). n = heals with that hop observed. Mode = whole minutes.

| hop | n | min | mean | mode | stdev | median | p95 | max |
|---|---|---|---|---|---|---|---|---|
| 202 → rsd at the Spotter (console) | 60 | 0.7 min | 3.8 min | 4 min | 2.9 min | 3.2 min | 8.5 min | 16.0 min |
| 202 → camera accepts (ebox, `OK id=… rsd`) | 60 | 0.9 min | 5.2 min | 5 min | 3.0 min | 4.9 min | 9.6 min | 16.0 min |
| 202 → camera re-sent (`<HL a=sent>`, Spotter row time) | 55 | 1.1 min | 9.5 min | 4 min | 6.6 min | 6.1 min | 18.3 min | 19.7 min |
| 202 → media complete at the backend | 59 | 5.0 min | 29.0 min | 25 min | 23.1 min | 23.9 min | 69.5 min | 119.7 min |

Per rig:
- **bmcam003**
  - 202 → Spotter: mean 3.8 / mode 4 / stdev 2.6 min (n 43)
  - 202 → camera: mean 5.3 / mode 5 / stdev 2.8 min
  - 202 → complete: mean 27.8 / median 24.6 min
- **bmcam004**
  - 202 → Spotter: mean 3.8 / mode 3 / stdev 3.5 min (n 17)
  - 202 → camera: mean 5.0 / mode 2 / stdev 3.5 min
  - 202 → complete: mean 32.1 / median 20.2 min

Raw: `h4/heal_ledger_auto.csv` and `h4/heal_summary.json`.

How to read it:
- **The downlink was 100 % reliable**: 60/60 heals reached the Spotter and the camera. It took
  1–16 min indoors, median 3.2 min.
- **The camera adds 0–5 min.** It dispatches commands between actions, so an rsd that lands
  mid-clip waits for the clip to finish.
- **Most of send → complete is by design.** In stay_on the camera re-sends after 600 s idle (O5)
  or at the next trigger. Sofar then exposes the rows 10–30 min late.
- **The 3 completions past 90 min** are the lost-`<HL>` chains above (REASK_S + one more cycle).

Clocks: Render (t_alloc, t_202, t_complete), the Spotter's own console stamps (t_spotter_rx,
t_camera_ok), and Sofar row time on the Spotter clock (`<HL>`). nereus000 and both Pis were
NTP-synced (checked 2026-10-01 05:32Z). The Spotter clock is GPS-disciplined; SPOT-31593C ran without a GPS fix 01:27–04:25Z.

## 4. REASK_S recommendation: **keep 5400 s for R1**

Data:
- The longest 202 → camera re-sent was 1183 s (19.7 min).
- Sofar exposes rows ≤ ~30 min late (memory: exposure lag 11–30 min).
- So a heal's chunks and `<HL>` are visible at the backend within ~50 min (~3000 s) worst
  case, indoors.

REASK_S only matters when the `<HL>` release is lost (3/60 here).
- **5400 s** leaves ~1.8× margin over that worst case. It produced 0 redundant heals.
- **3600 s** would shorten those 3 cases by 30 min, but leaves only ~1.2× margin. Indoor
  (signal-constrained) is our worst case, but field exposure lag has not been measured.

Revisit after the outdoor gates (G4/G5) with the same ledger.

## 5. The bmcam004 losses: Spotter firmware (Sofar ticket)

1. **2026-10-02 01:27:20Z: SPOT-31593C rebooted itself.**
   - Console: `Reset Reason: mem_fault_reboot reset`, FW v2.16.8 (GIT 47FF21A4).
   - The bus reset hard-cut bmcam004 mid-burst. The Pi came back by itself (cron @reboot →
     stay_on, up since 01:27:31).
   - Clip 01:23Z: 137 chunks submitted 01:23:30–01:27:20 were lost with the unsynced Notecard
     queue. No row, no key, not healable.
2. **01:27 → 04:25Z: no GPS on SPOT-31593C** (`GPS failed to initialize!` 02:27:20Z,
   `GpsErrorState … OK` 04:25:03Z).
   - The camera sent three full clips: 0e3knk 02:09 (177/177), 0e3mrx 02:55 (183/183), 0e3owl
     03:41 (182/182).
   - The console shows ~200 `Submitted … cell-only queue` per clip, only 8–14 queue-full, and
     `All messages sent successfully` lines.
   - **The Sofar API has 0 rows for SPOT-31593C between 02:00 and 04:27Z**, under any
     timestamp (S6b, read-only query; `sofar/sofar_SPOT-31593C_20261002.json` + `.QUERY.txt`).
     The clip from 04:26:59Z, right after the GPS fix, arrived normally.
   - **So messages queued while the Spotter has no GPS after a mem-fault reboot are silently not
     delivered.** This is a Sofar ticket candidate (Matt K).
3. This is the only Spotter reset in the run. The `Neighbor … added` lines every ~71.5 min on both
   Spotters are periodic, not resets; bmcam003 stayed up 24 h.

## 6. Other findings

- **F-SUP (device bug):** both units log `[SUP][ERR] settings re-resolve failed (FileNotFoundError
  … /dev/shm/bmcam/.camera_schedule.yaml.*.tmp); keeping the last good settings`, many times per
  day. It is harmless for this test, but a remote `set` in stay_on may not take effect. Owner:
  session "Fix supervisor settings re-resolve tmp-file error". Relevant to G3.
- **Spotter queue (F1):** `MS_Q_CELLULAR_ONLY is full` 1176× on SPOT-33507C and 441× on
  SPOT-31593C over the 24 h. It accounts for the low first-send rate on bmcam003 (17 %) and for
  the 5 lost `<HL>` uplinks.
- **Deploy during R5:** nvd #74 (migration 0018, DDL only, new columns default OFF and not read
  yet) merged to staging 2026-10-02T02:23:35Z, so Render redeployed mid-run. There was no
  behaviour change. The next auto-send went out at 02:46:08Z (100116), and they continued normally after that (ledger).
- **Credential note:** `~/.config/nereus/sofar.env` (SOFAR_API_TOKEN_BM_REEF) appeared on
  nereus000 at 2026-10-01 05:10Z, against the HANDOFF ("no Sofar token"). Nothing on the bench
  used it. Flagged to Nick.
- **Mirror gaps:** the Mac-side 10-min mirror had ~1.5 h gaps (23:28 / 00:54 / 02:20Z, Mac
  sleep). No evidence was lost: the backend and nereus000 keep full history. But the
  mem_fault reboot was only noticed ~1 h later.
- Heal ids are per device (100079 exists on both). Join on (device, id).

## 7. State at the cut (held for G3, no restore, per Nick)

- Both units: stay_on, trigger-only, cron ARMED, buses HELD, development 3c1801d.
- bm-heal-driver stopped; conductor stopped 05:30:01Z.
- Auto-send stays **ON** (EM: Nick's decision; drop to 24/day at code freeze, Mon 10/5).
- The bench was handed to "Test Engineer: own the bench and run R1 gates" at 05:3xZ.

## Files

| file | what |
|---|---|
| `gate.log` | timeline, decisions, every check |
| `h4/` | mirror: heal-events, command-events, console evidence, queue-full counts, Pi evidence, media_status, ledgers, summary |
| `h4_mirror.sh`, `h4_media_status.py`, `h4_ledger.py` | the mirror + ledger tools |
| `r5_snapshot.py`, `r5_snapshot_cut.json` | per-trigger backend status at the cut |
| `pulled/20261001T053033Z/` | conductor events.jsonl + state.json |
| `sofar/` | the read-only Sofar API pull proving the 3 undelivered clips |

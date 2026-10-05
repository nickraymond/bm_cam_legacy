# R1F-CMD — RESULTS (`runs/r1fix_cmdres_20261003`)

**R1F-CMD: PENDING** — one sentence: what was proven or what failed.

Spec: `hil/gates/R1F-CMD_*.md` · Manifest: `run_manifest.json` · Timeline: `gate.log` ·
Commands: `commands.log` / `steps.log`. Times PDT (UTC in the logs).

## Setup

| unit | Spotter | runtime sha | config hash | bus | cron | snapshot |
|---|---|---|---|---|---|---|
| | | | | | | `snapshots/…` |

Bench owner: Test Engineer session. Other sessions on the hardware during the run: none / …

## Criteria

| id | criterion | PASS when | measured | verdict | evidence |
|---|---|---|---|---|---|
| | | | | PENDING | |

## Steps

| step | sent (PDT) | command / action | answer / ack (PDT) | result | evidence |
|---|---|---|---|---|---|

## Findings

| id | seen | effect | owner | link |
|---|---|---|---|---|

## Restore

| unit | restored | read-back | matches baseline |
|---|---|---|---|

## Not tested

- …

# R1G — R1 re-gate, COMMANDS FIRST (bmcam003 / SPOT-33507C): **PASS**

Window: 24 wakes, 2026-10-04 06:00Z → 2026-10-05 05:00Z, + 6 h completion tail (to ~11:10Z). Code: development
a50636e (= bm #121, merged 5ea193a), unchanged through the window. Load: `hil-r1-cmdres.timer` (set mode.media hourly,
trg every 2 h, Sofar lane, product path) + backend heal auto-send. Spec: `hil/gates/R1G_commands_first.md`.
Exclusions (decided before scoring): 1000043 = the #121 deploy event (sent before the window, its cycle was SIGTERMed);
the 3 deliberate ack-loss stress slots count only for the recovery check, not against R1G.1.

| id | criterion | result | evidence |
|---|---|---|---|
| R1G.1 | every command confirmed at the backend | **PASS** — 38/38 | `analysis/cmd_ledger_R1G_final.csv`: 24 sets `in_effect`, 11 trg (10 `triggered`, 1 `answered`), 3 get `answered`; unit OK on the console for 38/38; 0 `late` / `superseded` for a command that ran. Lost originals recovered by d:1 with the original h (stress slot 00:20Z, 1000076/77, backend upgraded at 03:00:34Z) |
| R1G.2 | every trg accounted for | **PASS** — 11/11 delivered a clip, 0 silent | 10 `triggered` (backend linked the media); 1000069 `answered` though its clip (media 57270, 22:04:52Z) exists and completed — a backend trg→media LINKAGE gap, not a silent trg. 0 `clip NOT sent` (the #121 budget fix) |
| R1G.3 | no SSH needed | **PASS** | only reads over ssh after the 05:01Z 10/4 deploy (cycle-log pulls, cjxl/numpy readout); all changes via the backend |
| R1G.4 | every clip completes, 0 redundant heals | **PASS** — 35/35 complete; 0 redundant | `analysis/media_R1G_final.csv`; 28 heal requests / 49 entries, none for a media already complete at request time (`pulled/heal_events_R1G.json` vs completion times) |
| R1G.5 | no regression | **PASS** | 24/24 wakes, wake→halt 496–519 s (≤ 540), START every wake; 1 END line not printed on the console (23:00Z, capture gap; media complete); heals sent and served |

Known limits (documented, not criteria):
- SPOT-33507C commands take effect 2 wakes after sending (its hub.sync at :10 is after the :08 halt). Fix to be chosen
  after C1 (zero-energy option first); "listen until :13" not applied for R1 (Nick).
- Lost SET ack → `in_effect` via the heartbeat hash: covered by nvd #84 DB tests + the live d:1 path; not exercised on hardware.
- 3 h D1 moved to C1 (R1G.4 only requires completion).
Out of R1 scope, found on bmcam004 in stay_on (Sprint28 bench): stay_on never re-sends a lost ack (d:1 runs at boot
only), and the stay_on heal burst collides with the hub.sync (issue #126).

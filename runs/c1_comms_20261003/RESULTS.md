# C1 phase 1 — RESULTS (`runs/c1_comms_20261003`)

**C1 phase 1: NO SIGNIFICANT EFFECT** — moving bmcam004's burst off SPOT-31593C's hourly report (:30 vs :00) did not
change queue-full or first-send completion on 5 + 5 alternating wakes.

Spec: `hil/gates/C1_comms_reliability.md` (§ Arms — as run, § Significance bar, both fixed before the first window).
Times UTC (PDT = UTC − 7).

## Setup

| unit | Spotter | runtime | config | bus | cron |
|---|---|---|---|---|---|
| bmcam004 | SPOT-31593C | development 34a6222 | base + video, cap 126, chunk 384; no test commands | 90-min UTC interval (5400000 / 600000) from 14:53:50Z 10/4 → windows alternate :00 / :30 | armed, per_boot, halt |

Arm A = :00 (burst ≈ :01–:05 overlaps the Spotter's hourly report at :05:00 and health check ~:03).
Arm B = :30 (burst clear of both). One bridge commit (stub boot halted 14:54:33Z), no camera change, no re-commits.
Owner: Test Engineer; Nick's OK in the TE chat (2026-10-04 14:5x); EM/Nick asked for alternation + a significance bar.

## Per wake

| arm | window (Z) | queue-full | sent / START | wake→halt (s) | first-send complete | complete after (min) |
|---|---|---|---|---|---|---|
| A1 | 10/4 15:00 | 36 | 123/123 | 430 | no (healed) | 100 |
| B1 | 16:30 | 0 | 121/121 | 450 | yes | 10 |
| A2 | 18:00 | 0 | 121/121 | 420 | yes | 10 |
| B2 | 19:30 | 8 | 126/126 | 430 | no (healed) | 190 |
| A3 | 21:00 | 0 | 124/124 | 501 | yes | 10 |
| B3 | 22:30 | 0 | 125/125 | 501 | yes | 10 |
| A4 | 10/5 00:00 | 24 | 125/125 | 440 | no (healed) | 190 |
| B4 | 01:30 | 21 | 122 (END not on console) | 500 | no (healed) | 190 |
| A5 | 03:00 | 0 | 119/119 | 501 | yes | 10 |
| B5 | 04:30 | 0 | 119/119 | 501 | yes | 10 |

Sources: `gate.log` (hil_wake_report.sh per window, console/), `analysis/media.csv` (hil_media_table.py; completion =
backend `timestamp_utc`). All 10 windows opened on the 90-min UTC grid; 0 Spotter resets; 0 charger faults
(hil_rig_health); no external events.

## Verdict against the bar

| bar | A (:00) | B (:30) | met? |
|---|---|---|---|
| median queue-full per wake drops ≥ 50 % | median 0 (36, 0, 0, 24, 0) | median 0 (0, 8, 0, 21, 0) | **no** (0 → 0) |
| first-send-complete improves by ≥ 2 of 5 | 3/5 | 3/5 | **no** (equal) |
| (or) median first-send loss halves | 2 of 5 needed a heal | 2 of 5 needed a heal | **no** |

→ **No significant effect.** Queue-full events happen in both arms (A 2 of 5 wakes, B 2 of 5) and every wake with
queue-full ≥ 8 needed a heal; the burst's overlap with the hourly report is not what drives them on this Spotter.

## Notes

- The 90-min spacing is not production (60 min); both arms share it, so it does not bias A vs B, but the healed
  clips' completion (100–190 min) reflects 90-min heal cycles.
- B4's END line was not printed on the console (START 122; the backend shows 122/122 complete): a console-capture gap,
  not a delivery gap.
- Implication for the R1 known limit (SPOT-33507C's 2-wake command delay): moving the window after the report remains
  the zero-energy fix for COMMAND LAG (commands arrive at the report sync), but C1 shows no queue-loss benefit from it.

## Restore

The bus did not return to hourly: per Nick's JPEG-XL plan SPOT-31593C goes straight to bus-always-on for Sprint28 R0
(`runs/s28_ladder_*`), whose restore is `hil/tools/hil_restore_schedule.sh SPOT-31593C` (1 / 3600000 / 600000).

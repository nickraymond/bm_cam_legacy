# S0 gate — M0 no-regression (Nick 2026-09-23: option A, bmcam003/004)

Replaces the kickoff's bmcam001/002 control (BMCAM_002 last media row 2026-08-20; BMCAM_001 1 row in
8 days). Read-only query `gate_query.sql` against staging Postgres (`psql "$STAGING_DATABASE_URL" -f`).

- Baseline: `baseline_A_<UTC stamp>.txt`, taken before PR nickraymond/nereus-vision-dev#49 deployed.
- After: same query at deploy + 48 h → `after_A_<UTC stamp>.txt`.
- PASS: video rows/day per unit and % complete within the baseline's day-to-day spread (~74 video/day,
  ~50 % complete on full days 2026-09-22); no day with zero rows while the unit was up.
- Caveats: baseline is short (outdoor test began 2026-09-21); BMCAM_003's 51 images on 2026-09-22 all
  carry timestamps within 23 s (04:53:40–04:54:03) — treat image counts as noise, gate on video.
  bmcam003/004 are rev 3 units, so this proves "no regression", not keyed reception (that is ladder 2).

## Option B — replay (Nick 2026-09-23: run now instead of waiting 48 h)

`s0_replay.py` fetched 7 days of raw Sofar sensor-data (read-only, 2026-09-16T04Z..09-23T04Z, 6 h
windows = the production poll window, 84 requests) for all three gateways in `external_gateways`, and
parsed every window and each whole 7-day span with the staging parser before (`dbcb812`) and after
(`563022f`) PR #49. Every summary and every parsed media field (bytes by sha256) was compared.

| Spotter | Rows | Media (complete) | `<I{n}>` msgs | keyed msgs | other `<I{x}.{n}>` | old == new |
|---|---|---|---|---|---|---|
| SPOT-31593C (bmcam004) | 22,549 | 137 (76) | 22,205 | 0 | 0 | yes, 28/28 windows + whole span |
| SPOT-33361C (bmcam001/002) | 743 | 1 (0) | 67 | 0 | 0 | yes |
| SPOT-33507C (bmcam003) | 14,945 | 99 (48) | 14,592 | 0 | 0 | yes |

Negative control: one keyed clip injected into a real window → old 16 media, new 17 (the comparison
detects a change). Raw payloads (39 MB) kept outside the repo; results in `replay_results_B.json`.

**RESULT (B): PASS** — M0 output is identical on all real rev 3 traffic from the last 7 days.

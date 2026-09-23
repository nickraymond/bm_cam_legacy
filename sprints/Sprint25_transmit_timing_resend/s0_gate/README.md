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

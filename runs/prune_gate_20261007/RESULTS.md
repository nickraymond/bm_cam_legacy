# PRUNE gate: RESULTS (bm #143 on bmcam004 / SPOT-31593C, runtime 0a6e411 = 104ee3c + #143)

Gate: `hil/gates/PRUNE_GATE.md`. Deployed 2026-10-08 04:01:56Z, after the pre-lane YAML restore (Nick's OK). Halt band 452–552 s (baseline 502 s).

| wake (Z) | PDT | key | prune line (after the key line?) | sent/ before → after | deleted / expected | deleted ≤ 14 d | wake→halt | START on console | verdict |
|---|---|---|---|---|---|---|---|---|---|
| 05:00 | 10 PM | 0eewky | none (oldest 13.998 d) | 450 → 451 | 0 / 0 | 0 | 499 s | yes (194) | PASS |
| 06:00 | 11 PM | 0eezcy | `pruned 10 sent file(s) older than 14 d (by key time)` (yes) | 451 → 447 | 5 / 5 | 0 | 499 s | yes (190) | PASS |
| 07:00 | 12 AM | 0ef24x | `pruned 8 sent file(s) older than 14 d (by key time)` (yes) | 447 → 444 | 4 / 4 | 0 | 499 s | **no**: sent by the unit (190/190 complete), START + chunk .0 rejected by the Spotter `Queue MS_Q_CELLULAR_ONLY is full` (07:01:58–02:05) | PASS on #143 criteria, flagged |

**Verdict: PASS (3/3 on every #143 criterion).**
- One flag for the EM: wake 3's START was lost at the Spotter queue in transport. It was not missing from the unit, so not a #143 regression.
- Second bench proof on the reef config: REEF-RC wake 1 on bmcam003 (#143 head c8bea4de) logged the same order at 07Z
  (`[KEY] media key` → `pruned 8 sent file(s) … (by key time)` → `[KEY] sent record`).
- Artifacts:
  - sent/ listings `sent_lists/`
  - cycle logs `pulled/cycle_*Z.log`
  - console excerpts `console/`
  - scorer `prune_score.py`

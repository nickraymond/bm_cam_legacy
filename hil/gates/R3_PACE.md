# R3-PACE — does 1.3 s/msg cut first-send loss vs 1.0 s on bmcam003 (reef config, no delay)?

Card from the EM (Nick 2026-10-07 08:05 PDT, after R2-DELAY was stopped as inconclusive). Owner: Test Engineer.
Evidence: `runs/r3_pace_20261007/`. Times UTC. **Rules fixed before the first counted wake (16:00Z).**

**Question:** on bmcam003 / SPOT-33507C with the reef config and no first-send hold, does 1.3 s per message cut
first-send loss compared with 1.0 s? One variable: pacing.

## Fixed
`r2_start_delay_s` = 0 (R2 patch installed as a no-op); reef pjpg config (still every wake, crop 1504,846,1600,900 →
1000 px, still.message_cap 195); 528 B keyed chunks (384 chars, R1 framing); cellular-only; per_boot + halt; lane OFF;
backend self_heal OFF for SPOT-33507C (since 05:15:04Z; Nick restores it after the card:
`PATCH /admin/gateways/SPOT-33507C/rollout {"self_heal": true}`); no commands (cmdres timer stopped).

## Arms, switching, n
A = 1.0 s, B = 1.3 s per message (base YAML `uplink:` → `msg_interval_s:`; backup of the YAML before R2:
`~/hil_backup/r2_20261007T050052Z/camera_config.yaml`). ABBA from 16Z (A 16, B 17, B 18, A 19, …).
Switched by `hil/tools/hil_r3_switch.py` (state `runs/r3_pace_20261007/schedule.json`) via
`runs/r3_pace_20261007/scripts/r3_switch_at.sh HH` at HH:01:30Z: reads the wake's `[RC] pacing … delay_s=` line,
counts the wake for the arm it actually ran (off-sequence wakes count without advancing the sequence), writes the next
wake's pacing (the YAML is read at boot). Interim look at **6 per arm**, decision at **12 per arm** (24 counted wakes).

## Expected effect (stated up front, EM)
Stall loss ≈ D / txd − 2 rejects per stall of duration D, so ~25–30 % fewer rejects in the same stalls at 1.3 s, e.g.
11.5 % → ~8 %. With the observed spread (A in R2: 0–12.75 %) that needs ~12+ per arm.

## Measures per wake (as R2; `hil/tools/hil_r2_score.py` + `hil_wake_report.sh`)
first-send loss %, gaps, max gap, queue_full times, hand-off stalls (n / longest / rejects in), HDR marks crossed and
rejects HDR-stall vs other, START / END, wake→halt, **message count (START length) and END time**.

## Rules
- **PASS:** B median < A median AND one-sided Mann-Whitney (B < A) p < 0.10 at 12 per arm.
- **STOP early:** at the 6-per-arm look, if B median ≥ A median → stop (no benefit).
- **Safety / flag:** B bursts should END before :04:55 (at 1.3 s × 195 a large daytime image reaches the :05 HDR). If a
  B burst crosses :05:00, flag the wake "crossed HDR" — it counts, and is reported separately.
- Any wake with wake→halt > 570 s → report to the EM.

## Restore (after the card)
Pacing back to the pre-R2 value (1.3 s) or per the EM; then the R2 restore (original module + YAML from
`~/hil_backup/r2_20261007T050052Z`, rm r2_start_delay_s); self_heal ON (Nick); cmdres timer per the EM.

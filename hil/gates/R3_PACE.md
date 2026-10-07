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
A = 1.0 s, **B = 1.5 s** per message (Nick 2026-10-07 08:20 PDT, before the first B write) (base YAML `uplink:` → `msg_interval_s:`; backup of the YAML before R2:
`~/hil_backup/r2_20261007T050052Z/camera_config.yaml`). ABBA from 16Z (A 16, B 17, B 18, A 19, …).
Switched by `hil/tools/hil_r3_switch.py` (state `runs/r3_pace_20261007/schedule.json`) via
`runs/r3_pace_20261007/scripts/r3_switch_at.sh HH` at HH:01:30Z: reads the wake's `[RC] pacing … delay_s=` line,
counts the wake for the arm it actually ran (off-sequence wakes count without advancing the sequence), writes the next
wake's pacing (the YAML is read at boot). Interim look at **6 per arm**, decision at **15 per arm** (EM amendment 2026-10-07 before the first B wake: simulation
on the A-arm spread gives ≈ 83 % power at 12/arm, ≈ 90 % at 15; sequence ABBA × 7 + AB = 30 counted wakes).
**B = 1.5 s** (Nick 08:20 PDT; state `b_value` = 1.5 set 15:2xZ, before the 16:01:30Z write → the 17Z wake runs 1.5 and
counts as B).

## Expected effect (stated up front, EM)
Stall loss ≈ D / txd − 2 rejects per stall of duration D. At 1.5 s: **expected B median ~6 % vs A ~11.5 %** (EM).

## Measures per wake (as R2; `hil/tools/hil_r2_score.py` + `hil_wake_report.sh`)
first-send loss %, gaps, max gap, queue_full times, hand-off stalls (n / longest / rejects in), HDR marks crossed and
rejects HDR-stall vs other, START / END, wake→halt, **message count (START length) and END time**.

## Rules
- **PASS:** B median < A median AND one-sided Mann-Whitney (B < A) p < 0.10 at 15 per arm.
- **STOP early:** at the 6-per-arm look, if B median ≥ A median → stop (no benefit).
- **Safety / flag:** at 1.5 s a burst over ~170 messages reaches the :05 HDR (START ~:00:40 + n × 1.5 s). **Flag every B
  wake whose burst crosses :05:00** ("crossed HDR") — it counts, and those wakes are also reported separately. The
  message count (START length) is logged for every wake.
- Any wake with wake→halt > 570 s → report to the EM.

## Restore (after the card)
Pacing back to the pre-R2 value (1.3 s) or per the EM; then the R2 restore (original module + YAML from
`~/hil_backup/r2_20261007T050052Z`, rm r2_start_delay_s); self_heal ON (Nick); cmdres timer per the EM.

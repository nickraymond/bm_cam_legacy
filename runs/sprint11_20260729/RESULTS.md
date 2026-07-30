# Sprint11 validation run — ABORTED, with real findings (2026-07-29/30)

**Status: the 6 h A/B did not complete.** Two setup incidents ate the night
(YAML corruption from our own config patcher; a hard power cut that took
bmcam003 down), and the run was stopped at Nick's call at ~01:50Z. This
file records what WAS measured, because several of the sprint's questions
got answered anyway — including the central one.

## The central result: phase-aware transmit works

In the clean window (Unit A fixed 22:00Z, both units clean 00:00–01:50Z),
Unit A (bmcam003, 1.0 s, C1–C4 on) placed every burst exactly where the
model wanted it:

- START at grid phase **+33 s** every cycle (just past the 30 s
  post-boundary guard), burst end ~+210 s — 70 s clear of the boundary.
- **Zero periodic-blackout losses.** No 7-chunk mid-image cuts in any
  clean-window cycle. The 65 %-in first-gap signature from Sprint10 is gone.
- C1 timing confirmed: capture at ~35 s after power-on (was ~3 min 10 s).
- C3 confirmed on the wire: acks arrived ~3 min after power-up, after END.
- Commands: Unit A acked **5/5** roi sweep values (2,3,0,4,1), each applied
  the following cycle. Unit B acked its single clean-window command on
  try 1. All four of Unit B's earlier no-acks were the YAML bug (daemon
  disabled), not command-path failures.

## What remains broken: the sporadic population (D10, as predicted)

Backend delivery in the clean window:

| | complete | chunk % | first gaps |
|---|---|---|---|
| Unit A (1.0 s) | **0/3** | 98.65 % | chunk 18, chunk 18, chunk 138 |
| Unit B (5.0 s) | **3/4** | 99.71 % | one image lost 2 chunks @135 |

Unit A lost only 2–3 chunks per image, but twice at **chunk ~18** — ~50 s
after power-on — which beheads a progressive JPEG (usable prefix 10 %).
This is the sporadic sync-session population D10 explicitly deferred, and
it is NOT random: the repeat at the same early offset suggests a
fixed-schedule Spotter/Notecard event colliding with the burst in the
2-slot queue. **Head-chunk duplication (D10 mitigation #2) would have made
all three Unit A images complete.** That is the next sprint's first task.

Manual single-cycle test (02:09Z, bmcam000): console 192/192 + START/END,
**Sofar API complete with zero gaps** ~11 min after transmit end — the
full chain works end-to-end.

## Energy (interim, console-derived; SD cards not pulled)

Unit A at 15 on/15 off: **0.1643 Wh/cycle** median over 5 clean windows —
already below the 0.1797 baseline; predicted 0.1443 remains plausible once
a full run exists. Unit B console coverage was too gappy to quote.

## The three hard lessons (do not repeat)

1. **A read-back through a different parser than the runtime is not a
   read-back.** Our config patcher glued values to inline comments
   (`enabled: true# ... 2026-07-26: halt`), making the file invalid to
   PyYAML while every grep/sed check — and the hand-rolled core parsers —
   read it fine. Result: bm_commands silently disabled (camera captures
   instead of the reef reference, no acks) and BOTH arms silently at 5.0 s
   defaults, discovered only because Nick asked to verify the reference
   image on the wire. Fixed in tools/patch_camera_schedule.py (+ test);
   repair script in this dir. RULE: verify with `yaml.safe_load` on-device
   or the runtime's own --print-config override lines, ungrepped.
2. **Never cut bus power on an un-halted Pi.** SPOT-33507C's leftover
   schedule cut power mid-cycle at 01:59:33Z; bmcam003 never came back
   (boot surge then 0.66 W flatline, no network, ~6 power cycles). Root
   cause not concluded (SD damage vs WiFi/boot drift — Nick investigating;
   reflash likely). RULE: before ANY bus-power change, confirm no cycle is
   in flight; the disarm-before-schedule-change order in the run scripts
   exists for this.
3. **The infrastructure failure modes cost more than the code.** This
   run lost hours to: a Tailscale SSH check-mode gate whose console
   message our own log filter swallowed; `ssh -o ConnectTimeout` not
   bounding stalled handshakes (one hung probe starved the other unit's
   whole wake window); host processes dying with the session that spawned
   them (monitor 2×, caffeinate); the runner's HH:MM deadline being unable
   to cross midnight; and zsh not word-splitting `set -- $var`. All fixed
   in the run scripts (sshto.sh hard timeouts, filter allowlists, epoch
   deadlines) — carry those forward, not just the lesson.

## Where the units ended tonight

- **bmcam000**: healthy, Sprint11 runtime, reef reference, 5.0 s control
  config, bus always-on, halt OFF, cron @reboot ARMED → Spotter `reset` is
  the manual trigger (Nick testing). Verified end-to-end.
- **bmcam003**: unreachable since the 01:59:33Z hard cut. Bus always-on,
  Spotter healthy (`post`: baro INIT_ERR + indoor GPS/solar only). Needs
  physical recovery — likely fresh SD + `bmcam-provision`.
- Both Spotters: power controller DISABLED (bus continuously on) — NOT
  field-normal. Restore schedules before any unattended soak.

## Artifacts

timeline.jsonl (runner events, acks), backend_check_0155.json (per-image
backend verdicts), energy interim via tools/bridge_energy_per_cycle.py,
disarm/configure/arm/fieldupdate logs per unit, yaml_repair logs,
INCIDENT_tailscale_ssh_check.md, poll_manual_cycle.sh (the API verifier).

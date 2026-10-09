# Weekend run-book §3: Test Engineer section (TE2, 2026-10-09 ~7 AM PDT, from the JXL-RC overnight data)

Replaces the draft §3 in docs/em/WEEKEND_RUNBOOK_2026-10-10.md. Changes vs the draft are marked **(new)**.

## Stop rules (applied unattended, per unit; the other unit keeps running)

- **SSH needed to recover a unit → stop that unit only.** Never touch field units (SPOT-33361C, bmcam001/002).
- **2 commands lost → stop sending commands to that unit; keep imaging.**
  - **(new) Definition of "lost":** no ack, `<CF>` or matching cfg hash at the backend within **3 Spotter syncs of the send**.
  - Overnight we saw:
    - a command miss its first sync because the Sofar mailbox was empty (1000164, 22:10Z);
    - acks rejected at a queue stall and recovered by the unit's `d:1` re-send one wake later (REEF cmd 2).
  - Neither is a loss.
- **(new) Unit misbehaviour → stop commands and flag; keep imaging unless SSH is needed:**
  - a hard cut (node current still above the halted ~0.018 A at bus-off) twice in a row;
  - `skipped_no_budget` or `[PHASE][WARN] skipping` in 2 consecutive wakes;
  - `[CFG][ERR]` / an LKG fallback at boot.
- **(new) NOT a stop reason** (measured, not failures): Spotter-side first-send loss. This covers health-check syncs, HDR messages, queue_full rejects, and lost START/END messages. Overnight it ranged 0.5–39 % per wake.
- Rig CRIT (charger fault, nereus000 battery) → push-notify Nick; no remote action.
- Spotter reset / bus hard cut → log it as an event; the wake restarts on its own; no action.
  - **(new)** After any Spotter reboot, the boot-anchored health check re-phases (gotcha 8).
  - **(new)** A cloud reset lands ~80 s after bus-on, so it hard-cuts a running Pi (gotcha 13). Don't send one unattended.

## Command schedule

- EM sends through the backend product path, one key: contrast 1.0 ↔ 1.1, only when the previous command is answered (plan dry-run first; never supersede).
- **(new) Timing note:** a send at :33 reaches the console at the next :10 sync (37 min later), is applied after the transmit at the following boot (~:04), and is in effect at the wake after that. So in effect ≈ 2.5 h after the send. With an answered-only gate, 2:33 AM / 10:33 AM / 6:33 PM on both units fits: ≥ 8 h between sends.
- **(new)** SPOT-31593C (bmcam004) receives its mailbox at ~:16 (console 2026-10-09: rsd at 01:16:58Z, 02:16:11Z), not :10. A :33 send still lands at the next sync.

## Left alone over the weekend

- Base YAML, overlays (other than the EM's contrast commands), crontab, sent/, /dev/shm, heal_since, caps (from the draft).
- **(new)** Bridge config on both Spotters; Spotter cfg; no console `cmd.txt` writes.
- **(new)** Runtime + packages: no deploys, no development merges aimed at the units, `libjxl-tools` stays.
- **(new)** nereus000 services (spotter-monitor, rig health, dashboard). The rig-health bus_v WARN at :10:30 (bus-off edge) is benign until the probe fix lands.

## Mac / sessions

- `caffeinate -i` held through Sun night, **lid open + on AC**: a closed lid sleeps anyway (gotcha 14).
- EM + TE2 sessions open.
- **(new)** TE2 per-wake scoring is chained in-session (each job re-arms the next), so it stops if the session dies. Imaging and heals don't depend on it. For a weekend that must leave evidence even if the session dies: a detached `nohup` collector (as the R3 driver) that saves the console excerpt + cycle log per wake. I can install that Fri afternoon if wanted.

## Open item for Nick / EM (not a TE call)

- **SPOT-33507C's health check now fires at ~:02:45 every hour, mid-burst.** Overnight it caused 31–39 % loss on 4 of 17 wakes.
- JXL-RC backlog at 6 AM: 179 chunks over 6 open images.
- A Spotter reset before the weekend would re-phase it, but only to a random minute. Decide with the overnight numbers.

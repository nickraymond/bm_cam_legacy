# Sprint10 — 24-hour RC soak & bug-hunt plan (2026-07-27 → 07-28)

Nick-approved constraints: cellular messages are FREE — send as many
images/commands as needed. Goal: soak time + prove the release
candidate + find and squash bugs. Two units, both on development
@ 9330779 (PR #16 merge).

## Roles

| Unit | Config | Role in the soak |
|------|--------|------------------|
| **bmcam003** (SPOT-33507C, node 53171fa3d81a8e6f) | Bench mule: bus always on, outdoors on WiFi, cron off, halt off | High-rate pipeline soak: continuous capture+**transmit** cycles with the daemon concurrent (the §5 gap — acks in real pacing slots), all Phase D permutations, negative tests |
| **bmcam000** (SPOT-31593C, node TBD from first ack) | Field-normal: armed cron, real halt, Spotter 20/40 power cycling | Customer-condition soak: ~20+ real wake→capture→transmit→halt cycles; queue-while-off command delivery; state persistence across real power cuts |

## Hour-by-hour plan

| When (Z) | bmcam003 | bmcam000 |
|----------|----------|----------|
| T0 (~20:00) | Switch loop to `--transmit --skip-time-window` (image every cycle). First cycle = **Phase D perm #1**: backend image must show the already-applied roi=2 + exp=4 | Untouched: armed, ping 900 queued (queue-while-off test in flight) |
| T0+1h | **Perm #2 via GUI**: awb=3 + foc=2 batch; verify GUI lifecycle draft→acked, ack node-id match | First wake on new code: verify cycle log + image at backend + whether 900 delivered during listen window |
| T0+2h | **Perm #3**: win=1 + ping; verify next-cycle budget 720 s | Queue 1 command during its OFF window each odd hour (alternate: roi/exp/foc values, ping) — measures queued-delivery success rate over ~8 tries |
| T0+3h | **Perm #4 (negative)**: duplicate id re-send via cloud (expect ack, no re-apply) + invalid value awb=42 (expect ok=0 e=val ack) | — |
| T0+4h | **Perm #5**: cloud factory reset (5-command chain roi/foc/awb/exp/win=0); verify capture returns to stock crop/flags | — |
| Overnight | Continuous transmit cycles (~15-20/night at ~10-15 min each incl. images); hourly reconciliation sweeps | Armed duty cycle continues unattended (~15 wakes); logs accumulate on SD |
| T0+20-24h | Stop loop; final reconciliation; leave state factory-zero | Final wake check; leave armed field-normal (or per Nick) |

## Metrics (the report's headline table)

Per unit, from send logs + Pi cycle logs + `api/sensor-data` sweeps:

1. **Commands: sent → delivered → acked (device) → ack at backend**
   (with per-command latency: enqueue → device-apply, apply → backend)
2. **Images: cycles run → transmits attempted → chunks sent → chunks
   received at backend → complete images (START + END + all `<I{i}>`)**
   — Sprint09 count_phase_b.py reconciliation pattern
3. **Integrity**: dedupe correctness (dup test), reject correctness
   (invalid test), settings-persistence across bmcam000's real power
   cuts (state file diff per wake), zero unexplained daemon counters
   (crc_scan_fail / bad_start / read_err)
4. **Incidents**: every anomaly (Spotter reboots, missed drains, WiFi
   drops, tracebacks) with timestamp + log pointer

Report lands at `runs/sprint10_soak_20260727/REPORT.md`: headline
metrics table, PASS/FAIL per metric vs. targets below, incident list,
bug list (found/fixed/open), and raw-artifact index.

**Targets (RC gate):** ≥95 % command ack rate (cloud re-send covers the
rest — documented Spotter 2-slot drop mode exists); 100 % of *delivered*
commands correctly applied/deduped/rejected; ≥90 % complete image
delivery; zero daemon crashes; zero unexplained state resets.

## Bug protocol

Reproduce → capture logs into `runs/sprint10_soak_20260727/bugs/` →
fix on the branch → unit-test pinning the bug → redeploy affected unit
→ note in DEV_LOG. Field-critical bugs (daemon crash, capture
regression, state corruption) stop the soak and get raised to Nick
immediately.

## Automation to build at T0 (all committed to the branch)

- `tools/soak_command_scheduler.py` — timed command batches from a plan
  file; reuses sofar_send_command; logs to the shared send log.
- `tools/soak_reconcile.py` — sweeps api/sensor-data, matches chunks +
  acks to send/cycle logs, emits cumulative CSV + the report table.
- bmcam003 loop switch to `--transmit`; bmcam000 needs **no changes**
  (that's the point).

## Handoff note

Everything a fresh session needs: this file, DEV_LOG (Q1-Q11 answers +
today's findings), TRACKER, tools/ CLIs above, GUI runbook
(tools/bm_command_gui/README.md), send log + gui log in runs/. Bench
loop control: `/tmp/phaseC_stop` on bmcam003; bmcam000 crontab backups
are timestamped in /home/pi/.

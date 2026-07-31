# Sprint10 Phase E — Cellular Queue Drain Characterization (RUNBOOK)

Self-contained runbook. A session starting from zero should be able to
execute Phase E with only this file, the repo, and the bench. Approved by
Nick 2026-07-28.

---

## 1. Why this phase exists

Sprint09 locked `384 chars / 1.0 s` by measuring UART **throughput** with
small bursts (30 messages). The 2026-07-27/28 RC soak then found that at
**image scale** (~190 messages) every lossy cycle loses **one consecutive
run** of chunks, and converting index → time shows the runs all start
~140–150 s into the transmit and last ~6–7 s:

| Delay | First missing index | ≈ time into burst | Chunks lost |
|---|---|---|---|
| 1.00 s | 144 | 144 s | 7 |
| 1.25 s | 117 | 146 s | 5 |
| 1.50 s | 92 | 138 s | 4 |

Working hypothesis: a **Notecard sync session** (~6–7 s) blacks out the
Spotter's 2-slot cellular queue mid-transmit; everything offered during
the window is dropped silently (`[MS] [ERROR] Queue MS_Q_CELLULAR_ONLY is
full` on the console, invisible to the Pi — the Pi logs
`sent=N/N complete=True` every time). Slower pacing shrinks how many
messages land inside the fixed window but never reached zero at 1.0/1.25/
1.5 s. Nick's historical 100 % era ran 300 chars / **5.0 s**.

Phase E measures the mechanism instead of guessing config values.

**Model to test:** `lost ≈ max(0, blackout_s / delay_s − queue_slots)`,
queue_slots = 2 → predicts zero loss near **3.5–4.0 s**. At 4.0 s a
190-message image takes ~12.7 min, which still fits the 16-min budget in
a 20-min window. If confirmed, that is the Wednesday ship value; if the
curve says otherwise, the data says what to ship instead.

---

## 2. Bench topology (as of 2026-07-28)

| Unit | Pi (tailnet) | Spotter | Notes |
|---|---|---|---|
| bmcam003 | `pi@100.103.35.24` (host `bmcam003`) | SPOT-33507C | bench mule; camera node id `53171fa3d81a8e6f` |
| bmcam000 | `pi@100.119.14.92` (host `bmcam000`) | SPOT-31593C | customer-class test unit |

- Both Pis: repo runtime in `/home/pi/BM_Devel_Pi`, code =
  `development@9330779` (PR #16). SSH is Tailscale SSH (may print an auth
  banner line — filter with `grep -viE "tailscale|authenticate"`).
- Both Spotters on the Mac (or a monitoring Pi) via USB; device paths look
  like `/dev/cu.usbmodemSPOT_33507C1` (Mac) or `/dev/ttyACM*` (Pi),
  115200 8N1.
- Spotter power schedule is currently **20 min on / 40 off** (production);
  Phase E needs the units **continuously powered** — see §3.3.

---

## 3. Pre-flight (do all of this before sending a single test message)

### 3.1 Disarm the RC cron (both units)

The `@reboot` cron runs a capture cycle on every power-up and the cycle
**halts the box** in its `finally` block. Phase E drives the UART itself;
a cron cycle would fight it for the port and then power the unit off.

```bash
TS=$(date -u +%Y%m%dT%H%M%SZ)
ssh pi@100.103.35.24 "crontab -l > /home/pi/crontab_backup_phaseE_$TS.txt && \
  crontab -l | sed 's|^@reboot |# DISABLED phaseE '"$TS"': @reboot |' | crontab - && crontab -l"
```
Repeat for `pi@100.119.14.92`. **Keep the backup filename** — it is the
re-arm source in §6.

### 3.2 Kill any in-flight cycle WITHOUT triggering its halt

```bash
ssh pi@100.103.35.24 "pkill -TERM -f 'rc_run_capture_cycle.sh|rc_progressive_jpeg.py'; \
  sleep 2; pgrep -af 'rc_progressive|rc_run_capture' | grep -v pgrep || echo 'no cycle running'"
```
SIGTERM (not SIGKILL, not `sudo halt`) — Python dies without running the
`finally` halt.

### 3.3 Turn power_halt OFF in the YAML (belt and braces)

```bash
ssh pi@100.103.35.24 "python3 - <<'EOF'
import re
p='/home/pi/BM_Devel_Pi/camera_schedule.yaml'
s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) true', r'\1 false', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) false', r'\1 true', s, count=1)
open(p,'w').write(s)
print([l for l in s.splitlines() if 'enabled:' in l or 'dry_run:' in l][:2])
EOF"
```
Back it up first if you prefer: `cp camera_schedule.yaml camera_schedule.yaml.phaseE_$TS`.

### 3.4 Keep the units powered for the whole run

Phase E takes ~3 h. Ask Nick to either disable the Spotter power
controller or set a long on-window.

> **CORRECTED 2026-07-29 (bench-verified).** The `bm cfg … 0 …` form
> originally written here **does not work and fails SILENTLY**: the
> console answers `[BRIDGE] [INFO] Queuing serial command: …` and then
> nothing ever happens, because `bm cfg` forwards onto the BM bus and
> node `0` is not the bridge. Worse, when the bus is unpowered there is
> nothing to receive it at all. A read-back proved the value was still
> `1` after `bm cfg set … 0` + `bm cfg commit … 0` both "succeeded".
> Use `bridge cfg`, addressed to the **bridge node id**:

```text
bridge cfg set <bridge_node_id> s u bridgePowerControllerEnabled 0
bridge cfg commit <bridge_node_id> s
bridge cfg get <bridge_node_id> s bridgePowerControllerEnabled   # MUST read 0
```
Argument grammar (deduced from the working commands): `<node_id>` then
`s` = **partition** (system; `u`=user, `h`=hardware) then, for `set`
only, `u` = **value type** (uint). `bridge cfg status <node_id> s` dumps
all 18 system keys with values — the fastest way to confirm the whole
power chain at once.

Bridge node ids (from each Spotter's own `power |` publish lines):

| Spotter | bridge node id |
|---|---|
| SPOT-33507C (bmcam003) | `c3c564b91856226c` |
| SPOT-31593C (bmcam000) | `0e582dd12c1e1480` |

…or leave cycling enabled and set `sampleDurationMs` long. **Always
read the value back** — a committed `bridge cfg` prints the new network
config JSON, and success looks like `bridgePowerControllerEnabled: 0`
plus `[BRIDGE] handle_power_states, power on for: 4294967295` (0xFFFFFFFF
= indefinitely) and `Bridge bus power: 1`.

`bridge cfg commit` re-inits the BRIDGE (`Bridge State Init`, a reboot
notice from the bridge node) and cycles bus power — the Spotter console
itself stays up, so the earlier "reboots the Spotter" wording was
imprecise, but the bus blip is real and hard power-cycles the Pi. Do
this BEFORE starting bursts, never during. Confirm the unit stays
reachable for >25 min before trusting a long run.

### 3.5 Start console capture on both Spotters

The console is the only place the drop mechanism is visible.

```bash
python3 tools/spotter_serial_monitor.py --log-root ~/spotter_logs
```
(Multi-port, auto-discovers both, survives re-enumeration, writes
`events.log` with queue-full + Notecard fill lines, and lets you inject
commands via `echo "note sync" > ~/spotter_logs/<SPOT-ID>/cmd.txt`.)
Sanity-check that BOTH ports appear before starting.

### 3.6 Stage the harness on each Pi

```bash
scp sprints/Sprint10_bm_commands/test_queue_drain.py pi@100.103.35.24:/home/pi/BM_Devel_Pi/
scp sprints/Sprint10_bm_commands/test_queue_drain.py pi@100.119.14.92:/home/pi/BM_Devel_Pi/
```
It must sit next to `bm_serial.py` (it imports it). Verify with a dry run:

```bash
ssh pi@100.103.35.24 "cd /home/pi/BM_Devel_Pi && python3 test_queue_drain.py \
  --run TEST --matrix '200@1500' --dry-run"
```

---

## 4. Execution

### Step 1 — mechanism discriminator (~35 min, do this first)

Same count, three very different delays. Run on **one** unit
(bmcam003) so the result is unambiguous.

```bash
ssh pi@100.103.35.24 "cd /home/pi/BM_Devel_Pi && nohup python3 -u test_queue_drain.py \
  --run DISC --matrix '200@1000,200@3000,200@4000' --drain-s 300 \
  --out-dir /home/pi/phaseE > /home/pi/phaseE_disc.log 2>&1 &"
```

**Prediction to falsify:** if the blackout is *time*-triggered, the first
gap appears near seq 144 (1.0 s), seq ~47 (3.0 s), seq ~36 (4.0 s) — i.e.
same ~140 s wall-clock. If it is *count*-triggered, the first gap sits
near the same seq in all three. Anything else (no gaps at 3/4 s) is the
happy answer: the queue absorbs it and we have our ship value.

Wait ≥30 min after the last burst (backend lag), then analyze on the Mac:

```bash
python3 sprints/Sprint10_bm_commands/analyze_queue_drain.py \
  --spotter-id SPOT-33507C --manifest runs/sprint10_phaseE/manifest_DISC.json \
  --sendlog-dir runs/sprint10_phaseE --out-dir runs/sprint10_phaseE
```
(Pull the Pi's `/home/pi/phaseE/` into `runs/sprint10_phaseE/` first with
`scp -r`.) Re-run the analyzer later to catch stragglers — it is
idempotent.

### Step 2 — full matrix (~2.5–3 h)

Split across the two units to halve wall-clock. Suggested split:

```bash
# bmcam003 / SPOT-33507C — the fast half
--run FULLA --matrix '100@1000,200@1000,300@1000,100@1500,200@1500,300@1500,100@2000'
# bmcam000 / SPOT-31593C — the slow half (incl. the 5.0 s historical control)
--run FULLB --matrix '200@2000,300@2000,100@3000,200@3000,300@3000,300@4000,300@5000'
```
Same `nohup … &` pattern, `--drain-s 300`. **Do not trim the drain
pause below 180 s** without noting it — it is what makes bursts
comparable.

### Step 3 — analysis + deliverables

Run the analyzer per Spotter, then write up:

- `bursts_<SPOT>.csv` — one row per burst: delivered/sent, loss %,
  delivered-per-minute, gap count, first-gap seq + time
- `gaps_<SPOT>.csv` — one row per blackout: start seq, start second,
  length, span
- **loss-vs-delay curve** per burst size (100/200/300)
- **blackout statistics**: onset (mean/σ), duration, how often it fires
  per burst, whether a second one appears in 300-message bursts
- correlation with `events.log` (does each gap coincide with a
  queue-full burst and a Notecard fill drop?)
- **recommended (delay, message_cap) pair** with margin, and the
  predicted awake-time cost per image at that setting
- Sofar question sheet (see §7)

Results land in `runs/sprint10_phaseE_<date>/RESULTS.md` + DEV_LOG entry.

---

## 5. Safety rails / gotchas (all learned the hard way, 07-27/28)

- **Any `rc_progressive_jpeg.py` run halts the box** if power_halt is
  armed — even `--capture-only`. Disarm first (§3.3).
- **Halt without power cycling is a one-way trip**: a halted Pi on
  continuously-powered bus never comes back. If Nick disables the power
  controller for Phase E, do NOT let a halt fire.
- **`cfg save` / `bm cfg commit` reboots the Spotter** → bus power blip →
  the Pi hard power-cycles. Never mid-burst.
- **Console output lies about success**: after a remote mailbox command
  executes, the Spotter prints "Command not recognised" twice (echo
  artifact). Ignore it.
- **Backend lag is 13–30 min.** "Not seen yet" ≠ "not delivered". Always
  re-run the analyzer before concluding loss.
- **Notecard buffers, it does not discard**: a stalled sync makes data
  appear lost for hours, then all of it arrives at once. Check fill % on
  the console before blaming the link. Force a drain with `note sync`
  (console or via the cloud mailbox) — but never during a burst (that
  *causes* a blackout, finding 007).
- **Sofar Command API rate limit**: 1 successful request per minute per
  Spotter; all requests are rejected during the cooldown.
- **Don't run two things on the UART at once** — the command daemon
  (`bm_commands` island) opens the port during cycles. With cron
  disarmed nothing else touches it, but do not start a manual cycle
  while Phase E runs.

---

## 6. Restore to field-normal (mandatory at the end)

```bash
# 1. re-arm cron from the PRE-disarm backup
ssh pi@100.103.35.24 "crontab /home/pi/crontab_backup_phaseE_<TS>.txt && crontab -l"
# 2. power_halt back on
ssh pi@100.103.35.24 "python3 - <<'EOF'
import re
p='/home/pi/BM_Devel_Pi/camera_schedule.yaml'
s=open(p).read()
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*enabled:) false', r'\1 true', s, count=1)
s=re.sub(r'(power_halt:\n(?:.*\n)*?\s*dry_run:) true', r'\1 false', s, count=1)
open(p,'w').write(s)
EOF"
# 3. remove the harness from the runtime dir (keeps deploys clean)
ssh pi@100.103.35.24 "rm -f /home/pi/BM_Devel_Pi/test_queue_drain.py"
# 4. restore the Spotter power schedule
#    CORRECTED 2026-07-29: `bm cfg … 0 …` SILENTLY NO-OPS (see §3.4).
#    Using it here would leave both units NOT restored while every
#    command appeared to succeed. Use bridge cfg + the bridge node id,
#    and READ BACK before believing it.
#      bridge cfg set <node_id> s u bridgePowerControllerEnabled 1
#      bridge cfg set <node_id> s u sampleIntervalMs 3600000
#      bridge cfg set <node_id> s u sampleDurationMs 1200000
#      bridge cfg set <node_id> s u samplesPerReport 1
#      bridge cfg commit <node_id> s
#      bridge cfg status <node_id> s     # verify all four, in key order
#    node ids: SPOT-33507C c3c564b91856226c / SPOT-31593C 0e582dd12c1e1480
#    Key order in the status dump: 0 sampleIntervalMs, 1 sampleDurationMs,
#    5 bridgePowerControllerEnabled, 8 samplesPerReport.
```
Repeat for bmcam000. Also outstanding from the soak: **restore
`cfg vle 1`** (visibility LED, disabled on SPOT-33507C for bench comfort)
and set bmcam003's `message_cap` back to 195 if the A/B value is still in
place.

---

## 7. Sofar questions this phase should answer or ask

1. Can Notecard sync scheduling be pinned/deferred (e.g. `hub.set`
   sync interval, or sync-on-demand only) so a sync never lands inside a
   transmit? — **DEV_LOG Q12**, blocks the structural fix.
2. Is the cellular queue depth (2 slots) configurable?
3. Why do queue-full submits fail silently to the BM sender — is there a
   flow-control/backpressure signal the Pi could read?
4. Sustained-rate ceiling: what msgs/min can the Spotter forward
   indefinitely at 384 B, and does it vary with signal strength?

---

## 8. Definition of done

- [ ] Discriminator run answers time- vs count-triggered
- [ ] Full matrix executed (14 bursts, both units), all sendlogs +
      manifests archived under `runs/sprint10_phaseE_<date>/`
- [ ] Analysis artifacts: two CSVs, loss-vs-delay curve, blackout stats
- [ ] Recommended `(image_transmit_delay_seconds, message_cap)` with
      margin, awake-time cost, and the evidence behind it
- [ ] Values written into `camera_schedule.yaml` + device profiles and
      deployed via `tools/deploy_rc_runtime.sh`
- [ ] Both units restored to field-normal (§6) and verified imaging
- [ ] DEV_LOG entry + RESULTS.md + Sofar question sheet

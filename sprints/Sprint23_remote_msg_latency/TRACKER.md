# Sprint23 — Tracker

Spec: `SPEC.md` · Plan: `PLAN.md`. Opened 2026-09-20.
Units: **SPOT-31593C** + **bmcam003** (Nick handed over ownership 2026-09-21
~06:00Z: "clean and not being used by the other session"). Originally
**SPOT-31593C only** (USB console on Nick's Mac). SPOT-33507C belongs
to Sprint22 — never open its port, never send it a command.

## Done before the sprint

- [x] Repo survey: sender, serial monitor, GUI pattern, July diagnosis
- [x] Token path confirmed: `SOFAR_API_TOKEN_BM_REEF` present in env (name
      checked only, value never read)
- [x] Decisions D-S23-1..7 (Nick answered 2026-09-20)
- [x] Read Sofar v2.16.8 install guide — install steps only, no feature notes

## Phase 0 — prep + baseline (v2.16.6)

- [x] `--only <SPOT-ID>` flag on `tools/spotter_serial_monitor.py` (235→246
      lines; default unchanged; filter checked via discover_ports(), port not
      opened — flasher had it)
- [x] Monitor running for SPOT-31593C only (`lsof`: one port held)
- [x] FW banner + `post` / `sensors` recorded (RESULTS.md)
- [x] `clear_command_queue` + `uptime` sent (202) 04:50:09Z — done on v2.16.8
- [~] Baseline on v2.16.6 SKIPPED — Nick started the v2.16.8 flash
      2026-09-20 before Phase 0 ran. "Before" reference = July record:
      21–66 min E2E on SPOT-33507C, 0/16 on SPOT-31593C
      (`runs/remote_cmd_diagnosis_20260731/REPORT.md`). Queue clear + first
      `uptime` move to Phase 2a.

## Phase 1 — firmware update (Nick)

- [x] **GATE (Nick):** update started by Nick 2026-09-20 (flasher PID seen
      holding `/dev/cu.usbmodemSPOT_31593C1`)
- [x] Monitor stopped, port free (it was not running)
- [ ] bmcam000 safe — NOT CONFIRMED. Bus shows 0.000 A after the flash;
      ask Nick whether the Pi is connected, then check it boots
- [x] Flash done (Nick); console banner v2.16.8 at 04:49:25Z
- [x] Monitor restarted (`--only`), banner + health recorded

## Phase 2 — signature discovery (v2.16.8)

- [x] 2a `uptime` delivered in 46 s; receive signature unchanged
- [x] 2b held: `Queuing serial command` (USB + remote). Release is SILENT;
      timed with held `bm info`: ~8 s after bus-on
- [x] 2c `help`, bridge keys, `cfg`: no replay-delay setting on v2.16.8
- [x] Signatures + raw excerpts in `RESULTS.md` (`released_regex` stays null:
      there is no release line to match)

## Phase 3 — MVP tool

- [x] `tools/remote_msg_tester/` server + page + README
- [x] Offline replay test passes (decoy rejected, 5 s latency, id + FW
      captured, typed key never written)
- [x] Live sends end to end: 13/13 caught by the tester
- [x] Guards verified offline: reboot-class, bad id, stale log, in-flight,
      foreign origin, token leak grep. Rate limit seen live (UI chip).

## Phase 4 — characterize + hand off

- [x] 13 deliveries logged; 7/7 camera acks (Step C), all 7 at the Sofar API
- [x] `RESULTS.md` (no v2.16.6 baseline — flash came first; July numbers cited)
- [x] `HANDOFF.md` — for Sprint24 (joint outdoor HIL, PR #56) and the dashboard
- [ ] **GATE (Nick):** demo — Nick sends a command from the UI
- [ ] PR to `development` — branch merged with development locally
      (0 behind), NOT pushed: waiting for Nick's go
- [ ] Finding 10 prediction (report at 18:20:00Z 2026-09-21) — unchecked

## Hazards

- The serial monitor globs every Spotter. Until `--only` lands, do not start
  it while Sprint22 is using SPOT-33507C.
- The monitor holds the USB port; the firmware flasher needs it. Stop the
  monitor before Phase 1.
- Flashing/rebooting the Spotter cuts BM bus power — hard cut for bmcam000.
- Sofar rate limit: 1 successful request/min/Spotter, blanket cooldown.
- Many pending commands is a risk state (July wedge). One in flight at a time.
- Never mailbox-send reboot-class commands.

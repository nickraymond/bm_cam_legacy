# Sprint23 — Tracker

Spec: `SPEC.md` · Plan: `PLAN.md`. Opened 2026-09-20.
Unit: **SPOT-31593C only** (USB console on Nick's Mac). SPOT-33507C belongs
to Sprint22 — never open its port, never send it a command.

## Done before the sprint

- [x] Repo survey: sender, serial monitor, GUI pattern, July diagnosis
- [x] Token path confirmed: `SOFAR_API_TOKEN_BM_REEF` present in env (name
      checked only, value never read)
- [x] Decisions D-S23-1..7 (Nick answered 2026-09-20)
- [x] Read Sofar v2.16.8 install guide — install steps only, no feature notes

## Phase 0 — prep + baseline (v2.16.6)

- [ ] `--only <SPOT-ID>` flag on `tools/spotter_serial_monitor.py`
- [ ] Monitor running for SPOT-31593C only; 33507C port untouched (`lsof`)
- [ ] FW banner + `post` / `sensors` recorded
- [ ] `clear_command_queue` + `uptime` sent (202)
- [ ] Baseline latency recorded (or no-delivery after 2 syncs)

## Phase 1 — firmware update (Nick)

- [ ] **GATE (Nick):** plan approved, go for update
- [ ] Monitor stopped, port free
- [ ] bmcam000 safe (bus off / halted / unplugged)
- [ ] Flash: `Successfully updated spotter to: 2.16.8`
- [ ] Monitor restarted, new banner + health recorded

## Phase 2 — signature discovery (v2.16.8)

- [ ] 2a `uptime` delivered; receive signature confirmed
- [ ] 2b `bm pub` delivered with bus OFF; queued/released lines captured
- [ ] 2c `help` checked for new queue/config commands
- [ ] Regexes + raw excerpts in `RESULTS.md`

## Phase 3 — MVP tool

- [ ] `tools/remote_msg_tester/` server + page + README
- [ ] Offline replay test passes (match + latency math)
- [ ] One live send end to end
- [ ] Guards verified: rate limit, reboot-class block, token never in log

## Phase 4 — characterize + hand off

- [ ] ≥5 live deliveries logged
- [ ] `RESULTS.md` with before/after table
- [ ] `HANDOFF.md` for the dashboard session
- [ ] **GATE (Nick):** demo — Nick sends a command from the UI
- [ ] PR to `development`

## Hazards

- The serial monitor globs every Spotter. Until `--only` lands, do not start
  it while Sprint22 is using SPOT-33507C.
- The monitor holds the USB port; the firmware flasher needs it. Stop the
  monitor before Phase 1.
- Flashing/rebooting the Spotter cuts BM bus power — hard cut for bmcam000.
- Sofar rate limit: 1 successful request/min/Spotter, blanket cooldown.
- Many pending commands is a risk state (July wedge). One in flight at a time.
- Never mailbox-send reboot-class commands.

# Sprint23 — Remote message latency (Sofar API → Ebox console)

Side quest, opened 2026-09-20. Branch `feature/sprint23-remote-msg-latency`
(off `development`). PR gate targets `development`.
Plan: `PLAN.md` · Tracker: `TRACKER.md`.

## Why

Remote commands sent through the Sofar Command API have never reliably
reached a camera. Two separate causes were isolated on 2026-07-31
(`runs/remote_cmd_diagnosis_20260731/REPORT.md`):

1. Wedged cloud mailbox (head-of-line blocking). Fixed per-Spotter with
   `clear_command_queue`. SPOT-31593C was never cleared: 0/16 delivered, ever.
2. On firmware v2.16.6 the mailbox drains at the Spotter's ~hourly `[MS]`
   sync, not at the camera's wake. Every `bm pub` landed on an unpowered bus
   and was consumed unheard.

Sofar firmware v2.16.8 is reported (by Nick, from Sofar) to hold remote
messages for Bristlemouth devices in the Ebox and send them down the bus only
once the bus is on and active. **Source caveat:** the only Sofar document
read so far is the v2.16.8 install guide, which does not describe this
feature. Its console strings, config keys, and limits are UNKNOWN until
Phase 2 observes them.

## Question this sprint answers

Can we send a command via the Sofar API to SPOT-31593C, see it arrive on the
Ebox USB console, see it released to the BM bus when the bus powers on — and
how long does each step take?

## Units

- **SPOT-31593C** (pairs with bmcam000; USB console on Nick's Mac,
  `/dev/cu.usbmodemSPOT_31593C1`). Firmware v2.16.6 / bridge v0.13.11 as of
  the last console log (2026-07-29).
- **Never touch SPOT-33507C** — Sprint22 (video) owns it. Address ports by
  full name, never a `*SPOT*` glob.
- bmcam000 Pi: not modified. Its presence on the bus is only needed as a bus
  load/listener in Phase 2b; its daemon ack is out of scope.

## Deliverables

1. `tools/spotter_serial_monitor.py --only <SPOT-ID>` (additive flag; default
   behavior unchanged). Also unblocks Sprint22's Phase 3 prerequisite.
2. `tools/remote_msg_tester/` — stdlib local server + one HTML page:
   - inputs: Spotter ID, API key (blank = server reads env var), command
   - Send → POST to Sofar → live elapsed timer → RECEIVED + latency
   - second timer/event for bus release (signature from Phase 2)
   - history table from the log
3. `runs/remote_msg_latency/latency_log.jsonl` (+ CSV export): one record per
   send — `test_id, spotter_id, message, sent_utc, http_status, api_response,
   received_utc, latency_s, released_utc, release_latency_s, fw_version,
   matched_console_line, status`.
4. `RESULTS.md` — before/after latency table, PASS/FAIL, console signatures.
5. `HANDOFF.md` — contract for the Nereus Vision dashboard session.

## Decisions

- **D-S23-1 Token handling.** Tools read `SOFAR_API_TOKEN_BM_REEF` from the
  environment by name (already in Nick's `~/.zshenv`; it produced 202s for
  SPOT-31593C in July). Claude never sees, prints, or logs the value. The UI
  key field is an optional override held in server memory for that request
  only: never written to the log, never echoed back, never in a URL the
  browser can see. Server binds `127.0.0.1` only.
- **D-S23-2 Architecture.** Local stdlib server + single HTML page (Nick,
  2026-09-20). A `file://` page cannot tail the console log and would likely
  hit CORS at the Sofar API. Reuse `tools/sofar_send_command.py` send logic
  and the `tools/bm_command_gui` pattern; do not modify either.
- **D-S23-3 "Received" means two events** (Nick, 2026-09-20): (a) Ebox
  console echo `[SYS] [INFO] Remote message received(N)! "<msg>`, and (b) the
  new firmware releasing the queued message onto the bus. Camera ack is out
  of scope.
- **D-S23-4 Clear the wedged queue first** (Nick approved 2026-09-20):
  `clear_command_queue:true` together with the first benign `uptime`.
- **D-S23-5 Matching.** A send is matched to the first
  `Remote message received` line after `sent_utc` whose quoted text starts
  with the sent message. Sofar appends an `id:NNNNN` line; record it. One
  in-flight test per Spotter, so there is no ambiguity.
- **D-S23-6 Guards.** 60 s client-side lockout (Sofar: 1 successful
  request/min/Spotter). Refuse reboot-class messages: `reset`, `debug reset`,
  `cfg save`, `bm cfg commit`, `bridge cfg commit` — override only with an
  explicit checkbox. 270-byte message cap (from `sofar_send_command.py`).
- **D-S23-7 Long waits.** Pending state lives server-side and in the log, so
  an hour-long wait survives a page reload or server restart (on restart the
  server re-scans the console log from `sent_utc`).

## Definition of done

- [ ] ≥1 baseline delivery timed on v2.16.6 (or a documented no-delivery
      after 2 sync cycles)
- [ ] ≥5 deliveries timed on v2.16.8, each with a log record and the matched
      console line
- [ ] Bus-release event observed and timed at least twice with the bus OFF at
      delivery time — or documented as "feature not observed", with console
      evidence, for a Sofar ticket
- [ ] UI demo: Nick sends a command himself and sees RECEIVED + latency
- [ ] `RESULTS.md`, `HANDOFF.md`, PR to `development`

## Out of scope

Camera-side ack, new camera commands, satellite telemetry, dashboard
integration (next session), any change to bmcam000's runtime.

# Sprint23 — Plan

Spec: `SPEC.md`. One variable at a time: baseline on old firmware, then
firmware, then the tool.

## Phase 0 — Prep + baseline on v2.16.6 (Claude, before the FW update)

1. Add `--only <SPOT-ID>` to `tools/spotter_serial_monitor.py`. Default
   (no flag) behavior unchanged. Smoke test: `--only SPOT-31593C` opens one
   port; `lsof /dev/cu.usbmodemSPOT_33507C1` stays empty.
2. Start the monitor for SPOT-31593C only, log root `~/spotter_logs`.
3. Record the firmware banner + `post` / `sensors` (skill
   `spotter-health-check`). Confirm cellular is healthy — without it nothing
   drains.
4. `clear_command_queue:true` + `uptime` in one call
   (`tools/sofar_send_command.py --clear-queue --raw-message uptime`).
5. Wait up to 2 sync cycles (~2 h) for `Remote message received`. Record the
   latency by hand in `RESULTS.md`. This is the "before" number.

Exit: baseline number, or documented no-delivery.

## Phase 1 — Firmware update (Nick)

Guide: Sofar "Firmware v2.16.8 Installation Instructions". v2.16.x → v2.16.8
is a supported direct path. Package `Spotter_FW_v2.16.8_BM_v0.13.11.zip`
(bridge FW version unchanged).

1. Claude stops the monitor — the flasher needs the USB port. Verify with
   `lsof /dev/cu.usbmodemSPOT_31593C1`.
2. Decide bmcam000's state first: a Spotter reflash cuts bus power. If
   bmcam000 is on the bus and mid-cycle, that is a hard cut (SD-corruption
   risk, see skill `spotter-usb-power-cycle`). Flash while the bus is off or
   the Pi is halted/disconnected.
3. Nick runs `spotter-flash-upload`; success line is
   `Successfully updated spotter to: 2.16.8` then `Update finished!`.
4. Claude restarts the monitor (`--only`), records the new banner, re-runs
   `post` / `sensors`, and diffs against Phase 0.

Exit: v2.16.8 banner on console, health unchanged.

## Phase 2 — Signature discovery on v2.16.8 (Claude)

Nothing about the new feature is assumed. Learn it from the console.

- 2a. `uptime` via API. Confirm the `Remote message received` signature is
  unchanged. Note whether the drain cadence changed from ~hourly.
- 2b. `bm pub bmcam/s23test {"id":N} 1 1` sent so that it is delivered while
  the bus is OFF. Capture every console line from delivery through the next
  bus-on window. Identify: the "queued" line, the "released to bus" line, and
  what happens to a non-`bm` command (`uptime`) — does it run immediately?
- 2c. Also check `help` output for any new queue/config commands. Read-only.

Exit: documented regexes for received / queued / released, with the raw
console excerpts pasted in `RESULTS.md`. If no queue behavior is visible,
stop and report — that is a Sofar question, not something to engineer around.

## Phase 3 — MVP tool (Claude)

`tools/remote_msg_tester/`: `server.py` (stdlib, `127.0.0.1:8771`),
`index.html`, `README.md`.

- `POST /api/send` → validate (guards D-S23-6) → Sofar POST → append log
  record → start watcher.
- Watcher: tail `~/spotter_logs/<SPOT-ID>/console_YYYYMMDD.log` (handles the
  UTC day rollover) for the Phase 2 regexes; update the record on match.
- `GET /api/status` polled at 1 Hz by the page: state, elapsed, matched line.
- Page: three inputs + Send, big state badge
  (SENDING → QUEUED AT SOFAR → RECEIVED BY EBOX → RELEASED TO BUS / FAILED),
  live timer, monitor-health indicator (console log mtime — if the log is
  stale the page says so instead of waiting forever), history table.
- Startup checks, loud: monitor running? log fresh? token env var set?
- Test without cellular first: replay a saved console excerpt into a temp
  log and confirm match + latency math, then one live send.

## Phase 4 — Characterize + hand off

- ≥5 live sends, spaced across bus-on and bus-off moments.
- `RESULTS.md`: table (sent, received, released, latencies), min/median/max,
  before/after firmware, PASS/FAIL against the DoD.
- `HANDOFF.md` for the dashboard session: endpoint + body, token handling,
  rate limit, console regexes, state machine, log schema, known failure
  modes (wedged queue → clear; stale monitor; hourly drain).
- Nick demo, then PR to `development`.

## Risks

| Risk | Mitigation |
|---|---|
| Latency is ~1 h per test (hourly `[MS]` drain) — Phase 4 may take a day of wall clock | Tool survives reloads/restarts; sends can be queued one per drain; start Phase 0 early |
| The monitor grabs both Spotters and collides with Sprint22 | `--only` flag first; each session runs its own instance; never glob |
| Both sessions add `--only` → merge conflict | This sprint owns the flag; tell the Sprint22 session to consume it |
| Queue wedges again under rapid sends | One in-flight test per Spotter; `clear_command_queue` button |
| Bench cellular is weak → no drain at all | Phase 0 `post` check; note signal in each log record if visible |
| FW flash hard-cuts bmcam000 | Phase 1 step 2 |
| The feature needs a config key we don't know | Phase 2c; ask Sofar rather than guess |

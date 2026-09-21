# Remote Message Tester (Sprint23)

Send one console line to a Spotter through the Sofar Command API and time how
long it takes to show up on the Ebox USB console.

```text
browser page ──► server.py (127.0.0.1:8771) ──► Sofar Command API ──► cellular
                      │                                                  │
                      └── tails ~/spotter_logs/<SPOT-ID>/console_*.log ◄─┘
                          (written by tools/spotter_serial_monitor.py)
```

The tester never opens a serial port. The serial monitor owns the port; the
tester only reads the monitor's log files.

## Run it

1. Start the serial monitor for the Spotter under test (one Spotter only, so
   it never grabs another session's console):

```bash
python3 tools/spotter_serial_monitor.py --log-root ~/spotter_logs --only SPOT-31593C
```

2. Start the tester from a shell that has `SOFAR_API_TOKEN_BM_REEF` set:

```bash
python3 tools/remote_msg_tester/server.py --log-root ~/spotter_logs
```

3. Open <http://127.0.0.1:8771>, fill in Spotter ID and command, press Send.

The API key box is optional. Blank means the server uses the environment
token. A typed key is used for that one request, held in memory only, and is
never written to a log, the CSV, or the terminal.

## What you see

| Badge | Meaning |
|---|---|
| QUEUED AT SOFAR — WAITING FOR EBOX | Sofar returned 202; timer counts up |
| RECEIVED BY EBOX | console printed `Remote message received(N)! "<command>`; timer freezes at the latency |
| RELEASED TO BUS | only once `released_regex` is set in `signatures.json` (see below) |
| SEND FAILED | Sofar did not return 202; its reply is shown |

The wait can be an hour. State lives in the server and the log, so reloading
the page or restarting the server picks the test back up (the server resumes
scanning the console log from the byte offset recorded at send time).

## Outputs — `runs/remote_msg_latency/`

- `latency_log.jsonl` — append-only events (`sent`, `received`, `sofar_id`,
  `released`, `abandoned`), keyed by `test_id`
- `latency_summary.csv` — one row per test, rewritten on every change
- `excerpts/<test_id>.log` — the receive line plus the next 60 console lines
- the send is also appended to `runs/sofar_command_sends.jsonl`, so the CLI
  sender (`tools/sofar_send_command.py`) and this tool share one rate-limit
  view and one audit trail

## Guards

- 1 send per 60 s per Spotter (Sofar's limit; a blanket cooldown on their side)
- one test in flight per Spotter
- stale console log (> 60 s) blocks the send — nobody would see it arrive
- reboot/commit-class lines (`reset`, `cfg save`, `… commit`, …) are blocked
  unless the override box is ticked. A Spotter reboot is a hard power cut for
  the camera Pi.
- 270-byte message cap and ASCII check, reused from `sofar_send_command.py`
- POSTs from any other web origin are refused; server binds `127.0.0.1` only

## Signatures — `signatures.json`

`received_regex` was observed on firmware v2.16.6 and v2.16.8. Sofar appends
an `id:NNNNN` line to every delivered message; the tester records it as
`sofar_msg_id`.

`released_regex` is `null`. It is meant for the v2.16.8 "hold BM messages
until the bus is on" release line, which has not been observed yet. Fill it in
from a real console capture, never from a guess. While it is `null`, every
test ends at RECEIVED BY EBOX.

## Offline check (no network, no Spotter)

```bash
python3 tools/remote_msg_tester/server.py --log-root /tmp/fake_logs --fake-send --out-dir /tmp/fake_out --port 8772
```

Append lines shaped like `2026-09-21T04:50:55Z [SYS] [INFO] Remote message
received(16)! "uptime` to `/tmp/fake_logs/SPOT-XXXXX/console_<UTCDATE>.log`
and the page flips to RECEIVED. `--fake-send` refuses to run against the real
output folder.

## Known limitations

- Latency resolution is 1 s (the monitor's host timestamp).
- "Abandon" is local only. The command may still be queued at Sofar; send the
  next one with "clear queue" ticked to drop it.
- Matching is by exact first line of the command, first hit after the send.
  Sending the same command twice in a row is fine because only one test can
  be in flight.

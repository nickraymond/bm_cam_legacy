---
name: spotter-usb-console-capture
description: Use a Spotter's USB serial console as a zero-cellular test receiver — find the port on the Mac (or monitoring Pi), log it with spotter_serial_monitor.py, have a bmcam Pi push payload lines to it with bm_serial.spotter_print, then parse the console log and diff/reassemble what arrived against what was sent. Use when you need to prove the Pi -> UART -> BM bus -> Spotter path is byte-exact, test a new wire format or payload type (image, video, anything chunked) before spending a cellular cycle, measure real pacing on the Spotter clock, or split "our framing is wrong" from "the transport dropped it". For Spotter health triage use spotter-health-check; to reboot the Spotter use spotter-usb-power-cycle.
---

# Spotter USB console as a bench test receiver

The Spotter ebox has a USB console. A bmcam Pi can print a line on that
console over the BM bus with **zero cellular cost**. Log the console on the
Mac, and you have an independent record of exactly what the Spotter received
— which you can diff byte-for-byte against what the Pi sent.

One cellular cycle takes 5+ minutes and fails in four different places
(UART, transmit queue, Notecard/cellular, backend). This loop takes ~3
minutes for 172 messages and can only fail in one.

Proven: 2026-09-20T05:50Z, bmcam004 -> SPOT-33507C (fw v2.16.6), 172 x
388-char base64 lines at 1 msg/s: **172/172 byte-exact**, clip rebuilt from
the console log alone, sha256 match.
Run folder: `runs/video_tx_bench_20260920T053933Z_bmcam004/`.

## 1. Find the console port

macOS (ebox USB plugged into the Mac):

```bash
ls /dev/cu.usbmodem*SPOT*
```

Expect `/dev/cu.usbmodemSPOT_33507C1` (macOS appends the USB interface
index). 115200 baud. Confirm nothing else has it open — **never open the
port twice**:

```bash
lsof /dev/cu.usbmodemSPOT_*
```

Linux monitoring Pi: `/dev/serial/by-id/*SPOT*`.

## 2. Log it with the repo monitor (do not hand-roll a reader)

`tools/spotter_serial_monitor.py` auto-discovers the port on macOS and
Linux, survives re-enumeration, timestamps every line, and gives you a
command FIFO. Point `--log-root` INSIDE the run folder so the evidence is
self-contained. Run it in the background; leave `--sync-min` off (a
`note sync` is a cellular action).

```bash
python3 -u tools/spotter_serial_monitor.py --only SPOT-33507C --log-root runs/<run>/spotter_logs
```

**Always pass `--only <SPOT-ID>`.** Without it the monitor opens EVERY
Spotter on the host. The bench Mac often has a second Spotter plugged in
for unrelated work (2026-09-20: SPOT-31593C, held by Nick's own process) —
never open, command, or log a Spotter you were not given. Confirm afterwards
with `lsof /dev/cu.usbmodemSPOT_*`: your monitor's PID on your port only.

- Log: `runs/<run>/spotter_logs/SPOT-33507C/console_YYYYMMDD.log`
- Send a console command: `printf 'post\n' > .../SPOT-33507C/cmd.txt`
  (file is emptied after send)

**Pre-flight every session** with `post` + `sensors` (skill
`spotter-health-check`) and keep the output in the run folder. If
`cellularErrorState: OK`, remember the Spotter WILL forward anything sent
on `spotter/transmit-data` — only the printf path below is cellular-free.

## 3. Free the Pi's UART

The cron runtime (`rc_progressive_jpeg.py --transmit`) holds
`/dev/ttyAMA0` for its whole life — in video mode that is forever. Two
writers on one UART corrupt both. Check:

```bash
ssh pi@bmcamNNN 'ls -l /proc/$(pgrep -f "rc_progressive_jpeg.py --transmit" | head -1)/fd | grep ttyAMA0'
```

Pausing it (needs Nick's OK — it is field-style hardware):

- Back up the crontab (`crontab -l > ~/backups/crontab.before_<why>_<ts>`).
  It is `@reboot` only, so do NOT edit it unless you will reboot.
- **Kill by PID, never `pkill -f <pattern>` over ssh** — the pattern is in
  your own remote shell's command line and pkill kills your shell mid-script
  (happened 2026-09-20; left rpicam-vid orphaned). `pgrep` first, then
  `kill -TERM <pid>`, then the `rpicam-vid` child by PID.
- In video mode the in-progress clip is lost: the next start's boot sweep
  deletes the `.h264.part`. Stop just after a clip boundary (rpicam-vid
  process age < 25 s) to lose seconds, not minutes.
- Restore with the exact cron command, then VERIFY (recording + UI :8080):

```bash
ssh pi@bmcamNNN 'nohup /usr/bin/flock -n /tmp/bmcam_rc_capture.lock /home/pi/BM_Devel_Pi/rc_run_capture_cycle.sh >/dev/null 2>&1 < /dev/null & disown'
```

Only ONE unit may talk to a shared Spotter at a time (bmcam003 and
bmcam004 share SPOT-33507C).

## 4. Send from the Pi — `spotter_print`, not `spotter_tx`

`bm_serial.BristlemouthSerial.spotter_print(line)` publishes on topic
`spotter/printf`: one console line, never enters the transmit queue, zero
cellular quota. `spotter_tx` (`spotter/transmit-data`) is the production
path and DOES go out over cellular.

`tools/bm_video_tx_bench_send.py` is the reference sender: reads a wire
file (one message per line), prints `<TAG> BEGIN n=..`, paces the lines
(`--delay 1.0` = production), prints `<TAG> END`, writes a send-log CSV,
and **refuses to run while the runtime owns the UART**. Run it from
`~/BM_Devel_Pi` so it imports the unit's own `bm_serial.py`.

Always `--limit 5` first: it shows the console line format and whether
your line length survives before you spend 3 minutes. Measured: 388-char
lines pass intact; the printf length ceiling is NOT documented here —
probe before assuming more.

## 5. Parse the console — by pattern, never by column

Console line as logged (Spotter v2.16.6):

```text
2026-09-20T05:49:59Z 1789883398.726 e6fe83ea6b4a2b7f, <I0>AAAAAQYF...
^ monitor UTC        ^ Spotter epoch ^ sender node id  ^ your payload
```

Gotchas that cost time:

- **The Spotter's own log output interleaves MID-LINE.** A payload line
  arrived as `Message: 1789883399.773 e6fe…, <I1>IGNx…`. Anchor on the
  payload pattern (`<I(\d+)>([A-Za-z0-9+/=]*)`), not on line start.
- `power | tick` and `BRIDGE_CFG` chatter every few seconds — filter with
  `grep -vE ", power \| tick|BRIDGE_CFG"` when reading by eye.
- Bracket every burst with unique BEGIN/END tag lines and parse only that
  window (use the LAST occurrence — a day's log holds many bursts).
- Use the **Spotter epoch** column for pacing/gap stats; the monitor
  timestamp has 1 s resolution.
- `*.log` is gitignored and the log contains your payload. If the payload
  is imagery of a person/place, that is a feature — this repo is PUBLIC.

`tools/bm_video_tx_bench_verify.py` is the reference parser: extracts the
tagged window, matches every received message against the sent wire file,
rebuilds the payload from the console ONLY, compares sha256, reports
missing / corrupted / duplicate indices and gap statistics, exits nonzero
unless byte-exact.

```bash
python3 tools/bm_video_tx_bench_verify.py \
  --wire <wire.txt> --payload <payload> --tag VTXFULL --fps 10 \
  --console runs/<run>/spotter_logs/SPOT-33507C/console_YYYYMMDD.log \
  --out-dir runs/<run>/verify_full
```

## 6. What a PASS does and does not prove

Proves: framing is byte-exact, line length fits, UART + BM bus + Spotter
ingest are clean at that pacing, Pi-side timing is what you think.

Does NOT prove: the `spotter/transmit-data` queue (MS_Q), Notecard fill,
the 5-min cellular blackout lanes, the ~145-message delivery wall
(TODO-SPOT-001), Sofar API delivery, or backend reassembly. Those need a
real cellular cycle — and Nick's go-ahead, since it spends quota and
publishes to Sofar.

## 7. Leave it clean

- Restore + verify the unit's runtime (section 3).
- Remove your `/tmp` files from the Pi.
- Stop the monitor when done — it holds the USB port and blocks anyone
  else (including Nick's own terminal) from the console.
- Write `run_manifest.json` with every hardware state change and its UTC.

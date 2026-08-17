# Sprint15 — Design

Decisions numbered D-S15-N. Where a decision copies an existing
doctrine, the source is named — this sprint invents as little as
possible (SPEC constraint 3).

## D-S15-1 — Mode dispatch, not a new entry point

`capture_mode: video` is a third value of the EXISTING key (audit calls
it the runtime-path switch). `rc_progressive_jpeg.py` main() dispatches
to `video_recorder.run_video_mode(cfg)` right after config load +
command-overlay resolution. The cron @reboot line, deploy manifest
path, and flock lock are UNCHANGED — a video unit and a stills unit
differ by one YAML value (constraint 10). Overlay doctrine applies
untouched: commands recorded during a session apply from the NEXT boot
(Sprint11 D2).

## D-S15-2 — Clip pipeline: one encoder process per clip

Per clip: `libcamera-vid --codec h264 -t <clip_minutes*60s>` writes
`<name>.h264.part` → `ffmpeg -c copy` muxes to `<name>.mp4.tmp` →
poster JPEG extracted (`ffmpeg -frames:v 1`) → fsync → atomic rename to
final names → sidecar JSON written → status message queued → next clip.

Why per-clip processes instead of `--segment`: a wedged/crashed encoder
self-heals at the next clip boundary; the .part/.tmp suffixes make
crash debris unambiguous (never served, never counted, swept at boot);
the gap is ~2–4 s per 5 min (<1.5% dead time). Boring wins.

Crash contract (SPEC gate 3): a hard power cut loses AT MOST the
in-flight clip. Completed clips are fsynced before rename; rename is
atomic on ext4. Boot-time sweep deletes orphaned .part/.tmp.

## D-S15-3 — Geometry: derived from the stills keys, never restated

ROI comes from the SAME `crop_xywh` native-coordinate box stills use,
converted to libcamera-vid's normalized `--roi x,y,w,h` (fractions of
the 4608x2592 sensor — coordinate systems labeled per the manifesto).
Output `--width/--height` are the stills output values (1000x562 today;
both even, H.264-legal). Focus/AWB/exposure args reuse the SAME
camera-controls builder the stills path uses. There are NO video
geometry keys (constraint 4) — if the YAML crop changes, both paths
follow.

## D-S15-4 — Naming + sidecars

`<UTC>_video_<WxH>_<fps>fps.mp4` (e.g.
`2026-08-17T23-40-00Z_video_1000x562_15fps.mp4`), timestamp from the
existing Spotter-time chain (bench falls back per existing config —
constraint 6). Alongside: `<same>_thumb.jpg` poster and `<same>.json`
sidecar (the status-message fields, plus duration and sha256 prefix).
Lexicographic order == chronological order, which is what the UI sorts
by (constraint 8).

## D-S15-5 — Ring buffer (video_ring.py; TODO-BM-008 made real)

Before each clip starts, read statvfs and prune if EITHER trigger
fires (stricter wins): filesystem used > `max_used_pct` (default 75 —
the primary, card-size-portable knob, Nick 2026-08-17) OR free <
`min_free_gb` (default 10, absolute backstop). Prune = delete the
OLDEST completed clip triple (mp4 + thumb + sidecar), oldest-first by
timestamp filename, repeating until under both limits, then log +
include the `rd` count in the next status message. At 75% on the
116 GB card this is a rolling window of ~3.8 days of newest footage. Rules (all from TODO-BM-008): only completed video triples in
the video directory are candidates — never .part/.tmp, never stills
artifacts, never logs, never anything outside the directory;
`ring_dry_run: true` reports what WOULD be deleted; every deletion is
telemetered. If the floor cannot be met even after the ring empties
(disk eaten by something else), recording PAUSES with a loud periodic
log + status message rather than writing the disk to 0 — the
unbrickable guarantee (constraint 5).

## D-S15-6 — Status message (constraint 9, parity with stills style)

One compact JSON line per completed clip, on the existing cellular tx
path (the same bm_serial doctrine stills telemetry uses — reaches the
Sofar sensor-data API):

`{"t":"vid","fn":"<name>.mp4","sz":78643200,"res":"1000x562","fps":15,
"br":2.0,"dur":300,"tmp":52.1,"du":21.4,"dt":104.0,"rd":0}`

tmp = CPU temp C, du/dt = disk used/total GB, rd = ring deletions this
clip. Kept well under the Spotter message size cap; fields mirror the
still-image telemetry vocabulary. Send failure never blocks recording —
queue and retry at the next boundary, drop-oldest beyond a small cap.

## D-S15-7 — Command daemon during recording

Video mode opens the SAME shared-UART daemon the bench cycle uses
(single port owner). Frames are read continuously; settings commands
record to state (apply next boot, unchanged doctrine); queries and
acks drain at clip boundaries — the encoder owns the camera, the
daemon owns the UART, they never contend. This keeps deployed video
units remotely reachable (hlt/twn/help/cfg) and is the delivery path
for `wap` (D-S15-10).

## D-S15-8 — Power model

`session_minutes: 0` (ship default) = record until power loss; duty
cycling is the SPOTTER's job (bus schedule), and D-S15-2's crash
contract makes arbitrary cuts safe. `session_minutes > 0` = record N
minutes then fall through to the NORMAL cycle-end halt path
(power_halt YAML/override untouched) — the optional Pi-side duty-cycle
lever. Prune candidate #3.

## D-S15-9 — UI (videoui_server.py): stdlib only, static-first

`http.server`-based, serving: `/` gallery page (single static HTML file
with inline JS), `/manifest.json` (regenerated at each clip boundary by
video_manifest.py), `/videos/<file>` with Range support (iPhone Safari
scrubbing), thumbs inline. Gallery = newest-first grid of posters with
name/size/duration; tap to play (native MP4 playback); Download link
per file (MVP; multi-select zip is prune candidate #2). No framework,
no pip installs, no database — the manifest IS the state. Runs as a
thread of the video runtime when `ui.enabled` (port 8080); serves on
whatever network the Pi has (LAN today, AP later).

## D-S15-10 — `wap` AP toggle (tables v6) — LAST, PRUNE-FIRST

`wap`: 0 = client WiFi (normal), 1 = AP mode. Applied IMMEDIATELY on
command (unlike settings commands — documented exception, like trg),
via `network_ap.sh`: bring up hostapd + dnsmasq (SSID `bmcam000-video`,
WPA2), static 192.168.50.1, UI reachable at
`http://192.168.50.1:8080`. A systemd one-shot timer ARMED BEFORE the
flip auto-reverts to client WiFi after `ap_timeout_min` (default 60) —
revert-first design: the unit can only ever be temporarily off the
tailnet, even on a garbled command or a wedged AP stack. `wap 0` or
the timer both restore. Rehearse the full flip on the bench BEFORE any
remote use. Everything here stays out of the critical path until HIL
is running (Nick's sequencing).

## Test strategy (today)

- Unit tests, no hardware: config parsing/validation of the video
  island; geometry conversion (crop_xywh → --roi string, exact
  values); naming; ring-buffer policy on a temp dir (floor met, floor
  unmeetable, dry-run, never-touch rules); manifest generation; status
  message construction + size bound; mode dispatch (video YAML never
  enters the stills path and vice versa); daemon boundary servicing
  with the existing mock UART.
- Encoder calls are subprocess-wrapped and injected — tests fake them;
  the bench proves the real ones (SPEC gates 2–3).
- Existing suites must stay green untouched (gate 1).

# Sprint15 — Video recording, local storage, customer download UI

Status: SPECCED 2026-08-17 (live spec session with Nick; supersedes the
2026-08-17 pivot stub). SHIP TARGET: 48 h — development complete today,
HIL overnight on bmcam000, flash bmcam003 tomorrow (bmcam004 follows).
Branch: feature/sprint15-video-recording, off development (post PR #36).

## Nick's constraints (2026-08-17, binding)

1. Works inside the EXISTING framework (RC runtime, YAML, bm_serial,
   command daemon) — no new architecture.
2. 48 h to ship: dev today, HIL tonight + tomorrow.
3. Simple, boring, reliable — minimal soak time means no cleverness.
4. Video uses the SAME ROI and resolution settings as stills, from the
   same YAML keys. No video-vs-still divergence in optics/geometry.
5. Power + memory constrained. Near-continuous 24 h logging, possibly
   light duty cycling. 128 GB card total: storage management must make
   it impossible to brick the unit by leaving it out for days.
6. Production timestamps inherit the Spotter clock (existing time-sync
   chain). Bench fallback (indoors, no GPS) must not block development.
7. Per-video size limit as a YAML variable — start at 5 minutes.
8. End-user UI: list all videos (filenames prepended with timestamp),
   GoPro-style preview, then download one or many at full res — likely
   in the field via the Pi becoming a WiFi access point that an iPhone
   joins. AP switching is BM-command-driven, is the RISKIEST piece,
   is scheduled LAST (tonight, after HIL is running), and is the FIRST
   feature pruned if time runs out.
9. A BM message after every successful video save, for remote
   monitoring via the Sofar API: file name, size, resolution, frame
   rate, quality, Pi CPU temp, storage used vs total. Parity with the
   still-image telemetry style.
10. One simple YAML switch between stills and video production. This is
    an EXTENSION of the stack (AOML coral cams etc. are future users),
    not a customer-specific fork.

## Locked decisions (defaults accepted by Nick 2026-08-17)

- **Video knobs**: `fps: 15`, `bitrate_mbps: 2.0` (hardware H.264 via
  /dev/video11) — the only two video-only settings; everything else is
  inherited. `clip_minutes: 5`.
- **Status cadence**: one BM message per completed clip (~288/day).
- **Command daemon runs DURING recording** — UART is idle in video mode
  (no image transmit), so hlt/twn/help/cfg and the future `wap` command
  stay reachable on a deployed video unit. Commands are serviced at
  clip boundaries (safe points).
- **Thumbnails**: one poster-frame JPEG per clip. NO low-res proxy
  encode (Pi Zero 2 W cannot dual-encode within the power budget; at
  1000x562 / 2 Mbps the full MP4 already streams in iPhone Safari).
  "Preview" = tap poster, play the real file.
- **AP safety doctrine**: `wap 1` (tables v6) flips to hostapd AP with
  a HARD auto-revert timer (default 60 min) back to client WiFi — a
  mis-send can never strand the unit off the tailnet. Prune-first.
- **HIL**: tonight on bmcam000 (bench, SPOT-33507C). bmcam003 gets
  flashed/updated tomorrow after HIL passes.
- **trg 2 one-shot still during video mode: DEFERRED** (next sprint).
- **Crash-safety is a requirement, not a nice-to-have**: record →
  atomic mux → fsync → rename. A hard power cut loses at most the
  in-flight clip. This is what makes "light duty cycling" free — the
  Spotter bus schedule can cut power at any time.

## YAML (the switch + the island)

```yaml
capture_mode: video        # existing key; new value alongside
                           # progressive_jpeg (D-S15-1)
video:
  clip_minutes: 5          # per-video size limit (constraint 7)
  fps: 15
  bitrate_mbps: 2.0
  session_minutes: 0       # 0 = record until power loss (continuous);
                           # >0 = record N min then the NORMAL halt path
                           # (the light-duty-cycle lever, reuses
                           # power_halt machinery unchanged)
  storage:
    min_free_gb: 10        # ring buffer: keep at least this free
    ring_dry_run: false    # TODO-BM-008 doctrine: dry-run capable
  ui:
    enabled: true
    port: 8080
```

ROI, output resolution, focus, AWB, exposure: read from the SAME
existing YAML keys stills use (constraint 4). Time source: the existing
spotter_time_sync chain with its existing fallback (constraint 6).

## Storage math (defaults, measured free space 104 GB on bmcam000)

2 Mbps ≈ 75 MB per 5-min clip ≈ 21 GB/day continuous ≈ **5 days** of
24 h logging before the ring buffer deletes its first clip. Ring floor
of 10 GB free keeps the OS safe regardless.

## Architecture: see DESIGN.md (D-S15-1 … D-S15-10)

New modules (BM_Devel_Pi/): `video_recorder.py` (clip loop),
`video_ring.py` (storage guard), `video_manifest.py` (sidecars +
manifest + UI data), `videoui_server.py` (stdlib HTTP file/gallery
server). Entry stays `rc_progressive_jpeg.py` (mode dispatch at top —
cron line unchanged). `wap`/AP lives in `network_ap.sh` + tables v6
(last, prunable).

## Acceptance gates (PR → development)

1. Full unit suite green, INCLUDING all existing stills tests untouched
   (extension, not regression).
2. Bench (bmcam000, short clips): 3 consecutive clips → 3 playable
   MP4s + 3 poster JPEGs + sidecars + manifest entries; per-clip status
   line visible on the Spotter console path; ring dry-run reports
   correctly against an artificially low floor.
3. Crash test: hard power cut mid-clip → reboot → recorder resumes on
   its own (cron), filesystem clean, only the in-flight clip lost.
4. HIL overnight (bmcam000): continuous clips through the night,
   per-clip status messages queued to cellular. KNOWN ISSUE: verify
   SPOT-33507C cellular (no Sofar rows since 2026-07-31T19:00Z —
   antenna/queue check) — console-side evidence acceptable for the
   gate if cellular is still stalled.
5. UI: iPhone on the same LAN loads the gallery, plays a clip from its
   poster, downloads a clip at full res.
6. (Prunable) `wap 1` → AP up, iPhone joins, gallery loads → auto
   revert restores client WiFi + tailnet within the timer.
7. PR → development with TRACKER artifacts.

## Prune order (time pressure)

1. `wap` AP mode (Nick pre-approved as first cut)
2. Multi-select / zip download (MVP = per-file download links)
3. `session_minutes` duty-cycle lever (0/continuous is the ship mode)
4. (already deferred) `trg 2` one-shot still during video

## Not in scope

Audio. Video over BM/cellular. Live streaming. H.265. Retention
policies beyond the ring floor. AOML camera ports (this sprint only
keeps the door open by staying an extension).

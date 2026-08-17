# Sprint15 build session — kickoff prompt

Paste the block below into a fresh Claude Code session in this repo.

---

You are building Sprint15: video recording + local storage + customer
download UI for the bmcam underwater cameras. This is a 48-hour ship:
development completes TODAY, HIL runs overnight on bmcam000, bmcam003
gets flashed tomorrow.

REQUIRED READING, in order, before writing any code:
1. CLAUDE.md (the manifesto — especially "simple, boring, reliable")
2. sprints/Sprint15_video_recording/SPEC.md (constraints + locked
   defaults + acceptance gates + prune order)
3. sprints/Sprint15_video_recording/DESIGN.md (D-S15-1..10 — these
   decisions are made; implement them, don't relitigate them)
4. sprints/Sprint15_video_recording/TRACKER.md (chunk order; tick
   boxes only with artifacts)

Work on branch feature/sprint15-video-recording (exists, tracks
origin; spec docs are its first commit). Never commit to development
or main directly.

BUILD ORDER (tracker chunks — verify each chunk with its unit tests
before starting the next):
1. Config + geometry: `video:` YAML island, capture_mode dispatch,
   crop_xywh -> --roi conversion, reuse the stills camera-controls
   builder.
2. Recorder + ring: video_recorder.py clip loop (subprocess-injected,
   .part/.tmp -> atomic rename, boot debris sweep), video_ring.py
   (max_used_pct 75 primary + min_free_gb 10 backstop, prune oldest
   triples only, pause-not-brick, dry-run mode).
3. Status + manifest + UI: per-clip status JSON on the existing
   cellular tx path, sidecars + manifest.json, videoui_server.py
   (stdlib http.server, Range support, poster-thumb gallery), daemon
   serviced at clip boundaries.
4. Bench smoke on bmcam000 (short clips), then arm HIL for overnight.
5. wap AP mode (tables v6, auto-revert) ONLY after HIL is running —
   first thing pruned if time runs out.

Test command: `python3 -m unittest discover -s tests` — the FULL suite
(560+ tests) must stay green, including every existing stills test,
untouched. Encoder/muxer calls are injected so tests need no hardware.

HARDWARE (already staged):
- bmcam000 = the dev unit: LAN 192.168.86.23 / tailnet `bmcam000`,
  key-auth SSH as pi, developer state (cron disarmed, hlt=3 overlay),
  on the development tip, hosted/bus-powered by Spotter SPOT-33507C
  (console `reset` at the Spotter = power-cycle the Pi). libcamera-vid,
  ffmpeg, /dev/video11 H.264 verified present; 104 GB free.
- The Pi's clock drifts when no cycles run (no GPS sync; fake-hwclock)
  — if TLS/git breaks on the unit, fix the date first.
- bmcam003/004 are potted (no SD access), bmcam003 off — do not use.

HAZARDS:
- bmcam000 still has the DEFAULT pi password — change before ship
  prep, and never print/handle it yourself (human does passwords).
- SPOT-33507C has pushed no Sofar cloud rows since 2026-07-31 — HIL's
  remote-monitoring evidence may be console-only; check antenna/queue.
- AP mode kills client WiFi (and SSH/tailnet) while active — the
  auto-revert timer is the only un-brick; never flip without it armed.
- The command daemon only listens during cycles today; in video mode
  you are ADDING the always-listening behavior (D-S15-7).

Start with chunk 1. Keep functions small, print progress loudly, and
trust artifacts, not exit codes.

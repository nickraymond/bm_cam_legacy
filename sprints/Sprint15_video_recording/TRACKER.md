# Sprint15 — Tracker

Tick a box only when an artifact proves it. IN PROGRESS since
2026-08-17 (branch feature/sprint15-video-recording, off development
post-#36). 48 h ship clock started at the 2026-08-17 spec session.
Chunked: 1 config/geometry → 2 recorder+ring → 3 status+manifest+UI →
4 bench smoke → 5 HIL overnight (bmcam000) → 6 wap AP (prunable) →
7 PR + flash bmcam003.

## 0. Setup
- [x] Spec session with Nick, constraints + defaults locked (SPEC.md)
- [x] DESIGN.md D-S15-1..10
- [x] Tooling verified on bmcam000: libcamera-vid, ffmpeg,
      /dev/video11 H.264, IMX708, 104 GB free
- [x] bmcam000 in developer state (dev_mode.sh on, dev tip, tailnet OK)

## 1. Config + geometry (chunk 1)
- [x] `video:` island parsing + validation (defaults per SPEC; loud
      failures on nonsense values) — video_recorder.load_video_config,
      tests/test_video_config.py (island in camera_schedule.yaml; added
      optional `dir` key, default /home/pi/BM_Devel_Pi/videos)
- [x] capture_mode: video dispatch in rc_progressive_jpeg.py (stills
      path byte-identical when mode != video; video branch is an
      added-only `if` + lazy import) — dispatch tests in
      test_video_config.py TestModeDispatch
- [x] crop_xywh → libcamera-vid --roi conversion (exact-value tests;
      coordinate systems labeled) — crop (1504,846,1600,900) ->
      "0.326389,0.326389,0.347222,0.347222"
- [x] Camera-controls args reused from the stills builder (no video
      keys) — build_encoder_command wraps
      process_image_v2._camera_controls_from_settings
- [x] Unit tests green — full suite 584 tests OK (was 555 baseline;
      +29 in tests/test_video_config.py), 2026-08-17

## 2. Recorder + ring (chunk 2)
- [ ] video_recorder.py clip loop (subprocess-injected encoder/muxer;
      .part/.tmp → atomic rename; boot-time debris sweep)
- [ ] video_ring.py floor guard (TODO-BM-008 rules; pause-not-brick
      when floor unmeetable; dry-run mode)
- [ ] Unit tests green (crash-contract simulations included)

## 3. Status + manifest + UI (chunk 3)
- [ ] Per-clip status JSON on the existing cellular tx path (size
      bound tested; queue/retry never blocks recording)
- [ ] Sidecars + manifest.json regeneration
- [ ] videoui_server.py gallery (stdlib, Range support) + static page
- [ ] Command daemon serviced at clip boundaries (mock-UART test)
- [ ] Full suite green incl. all existing stills tests

## 4. Bench smoke (bmcam000, short clips)
- [ ] 3 consecutive clips → 3 playable MP4s + thumbs + sidecars +
      manifest (artifacts in runs/sprint15_bench_<date>/)
- [ ] Status lines observed on the Spotter console path
- [ ] Ring dry-run against an artificial floor reports correctly
- [ ] Crash test: power cut mid-clip → reboot → auto-resume, only
      in-flight clip lost
- [ ] UI from a phone/laptop on the LAN: gallery, play, download

## 5. HIL overnight (bmcam000)
- [ ] Production-like YAML (clip_minutes 5, session 0) armed via cron
- [ ] Runs overnight; morning: clip count matches wall clock, no
      gaps beyond clip-boundary seconds, temps sane, ring behavior
      as expected
- [ ] Per-clip status messages queued to cellular (Sofar rows if
      SPOT-33507C's stall is cleared; console evidence otherwise —
      check antenna/queue first)

## 6. wap AP mode (tables v6) — PRUNE-FIRST, only after §5 is running
- [ ] network_ap.sh + auto-revert timer (revert-first design)
- [ ] `wap` in tables v6 + help/cfg rows + daemon immediate-apply
- [ ] Bench rehearsal: wap 1 → iPhone joins AP → gallery/download →
      auto-revert restores client WiFi + tailnet
- [ ] Suite green

## 7. Wrap
- [ ] PR → development, gates green, Nick review
- [ ] bmcam003: wake, field-update to the merged build, video YAML,
      re-verify (tomorrow)
- [ ] docs: bmcam_command_reference.md (wap, if it survives),
      fleet/skill notes

## Known hazards carried in
- SPOT-33507C cellular stall (no Sofar rows since 2026-07-31T19:00Z)
  — HIL's remote-monitoring evidence may be console-only.
- bmcam000 still has the default pi password — change BEFORE bmcam003
  flash/ship prep.
- bmcam003 is potted + off + on the 2026-08-01 build + dev-mode state;
  budget wake + field-update + state reset time tomorrow.
- AP mode kills client WiFi (and with it Tailscale/SSH) while active —
  the auto-revert timer is the only un-brick; never skip arming it.

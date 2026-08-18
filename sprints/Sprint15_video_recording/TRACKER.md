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
- [x] video_recorder.py clip loop (subprocess-injected encoder/muxer;
      .part/.tmp → atomic rename; boot-time debris sweep) —
      record_one_clip + run_video_mode; poster failure non-fatal;
      max_clips bounds ATTEMPTS (bench bail); 10 s failed-clip backoff
- [x] video_ring.py floor guard (TODO-BM-008 rules; pause-not-brick
      when floor unmeetable; dry-run mode; dry-run still pauses at the
      REAL floor — unbrickable beats convenient)
- [x] Unit tests green (crash-contract simulations included:
      failure-stage debris cleanup + boot sweep) — full suite 612 OK
      (+28: tests/test_video_ring.py, tests/test_video_recorder.py),
      2026-08-17

## 3. Status + manifest + UI (chunk 3)
- [x] Per-clip status JSON on the existing cellular tx path (size
      bound tested vs 280 B; StatusQueue drop-oldest cap 12; send
      failure retries at next boundary, never blocks recording; plain
      no-transmit runs PRINT lines instead — bus doctrine)
- [x] Sidecars + manifest.json regeneration (atomic writes; manifest
      degrades gracefully on missing sidecar/thumb; sha256_16 in
      sidecar) — video_manifest.py
- [x] videoui_server.py gallery (stdlib, single-range 206s for Safari
      scrubbing, basename+suffix+realpath hardening) + inline static
      page; UI failure never kills recording
- [x] Command daemon serviced at clip boundaries (fake-daemon test;
      gating identical to stills: island enabled + transmit/bench);
      Spotter clock sync at video start (drift hazard)
- [x] Full suite green incl. all existing stills tests — 644 OK
      (+32), 2026-08-17. New modules added to rc_runtime_manifest.txt

## 4. Bench smoke (bmcam000, short clips)
- [x] 3 consecutive clips → 3 playable MP4s + thumbs + sidecars +
      manifest — runs 2+3 gave 8 clean 15 s triples (h264 1000x562
      @15.0fps by ffprobe, ~2.0 s boundary gap); artifacts in
      runs/sprint15_bench_20260818/. Found+fixed on hardware: explicit
      ffmpeg -f mp4/-f image2 (.tmp suffix hides extension); poster
      -ss 1 (frame-0 AGC ramp washed out tiles)
- [x] Status lines on the Spotter tx path — --transmit run: 4x
      "[VID] status sent" (160 B JSON) via shared-UART spotter_tx;
      Spotter clock sync worked (D-S15-7). Spotter USB console NOT on
      the Mac → console capture pending; cloud check confirms the
      SPOT-33507C stall persists (latest Sofar row still
      2026-07-31T19:03:49Z, 18-day lookback) — antenna/queue needs
      physical attention (Nick)
- [x] Ring dry-run against an artificial floor (min_free_gb 200):
      listed all 8 triples oldest-first, deleted nothing (25 files
      intact), paused — exact TODO-BM-008 contract
- [x] Crash test x2 (sysrq-b hard reset = no-sync power-cut
      equivalent): (a) mid-boundary — swept manifest.json.tmp, zero
      clips lost; (b) mid-encode with confirmed 1 MB in-flight .part —
      part swept (0 B, unsynced), cron auto-resumed ~48 s, ext4 clean.
      Bonus finding: fake-hwclock stamps a stale RUN_TS at crash boots
      (two boots shared one cron log name) — clip names stayed correct
      because the Spotter clock sync runs before the first clip
- [x] UI from a phone/laptop on the LAN: gallery, play, download —
      Nick verified from laptop 2026-08-17 evening (PASS); Mac-side
      curl: gallery 200, manifest OK, Range 206. iPhone check via LAN
      http://192.168.86.23:8080 still worthwhile for gate-5 wording
      (no tailnet needed)

## 5. HIL overnight (bmcam000)
- [x] Production-like YAML (clip_minutes 5, session 0) armed via cron
      — armed 2026-08-18T00:28Z; @reboot flock line restored (backup
      ~/crontab_before_sprint15_arm_*), first 300 s clip in flight,
      .part growing. Unit YAML backups:
      camera_schedule.yaml.before_sprint15_bench_20260818T000331Z,
      .bak_sess, .bak_hil. Restarted 2026-08-18T00:43Z on the exact
      PR tip (a2991f9) after the wap deploy — overnight evidence is
      one build
- [ ] DEFERRED to next sprint (Nick, 2026-08-18): the bmcam000 morning
      review was never performed — Nick reformatted bmcam000 to Trixie
      the same morning (fleet OS split, see hazards), so the overnight
      evidence was not analyzed and this box stays UNFINISHED. Context
      preserved for the record: config changed MID-HIL per Nick
      (2026-08-18T02:14Z, via the settings GUI): clips before 02:14Z
      are 1000x562@2Mbps manual-focus; after are 1600x900@4Mbps
      autofocus. Reboots at ~01:26/01:40/02:0x/02:14Z were the GUI
      work + Nick's settings sessions, not failures. GUI save-poison
      bug (float 2.0 vs "2") found by Nick, fixed + regression-tested;
      full save→UI-restart→persist loop verified through HTTP
      2026-08-18T02:14Z. HIL evidence duty moves to bmcam003/004
      (see §7).
- [ ] DEFERRED with the box above: per-clip status messages to
      cellular (Sofar rows if SPOT-33507C's stall is cleared; console
      evidence otherwise — antenna/queue still an open physical item)

## 6. wap AP mode (tables v6) — PRUNE-FIRST, only after §5 is running
- [x] network_ap.sh + auto-revert timer (revert-first design: timer
      armed + VERIFIED before any flip; refuses to flip otherwise;
      never persisted — reboot = second un-brick). Deployed to
      bmcam000, sudo -n + py_compile verified, NOT flipped
- [x] `wap` in tables v6 + help/cfg rows + daemon immediate-apply
      (IMMEDIATE_COMMANDS; fires once, duplicates never re-fire) +
      quick action; cfg WiFi row reads live marker
- [ ] Bench rehearsal: wap 1 → iPhone joins AP → gallery/download →
      auto-revert restores client WiFi + tailnet — WITH NICK (kills
      WiFi while active; needs his iPhone on SSID bmcam000-video,
      password bristlemouth, gallery http://192.168.50.1:8080).
      BLOCKED 2026-08-18 by the fleet OS split: network_ap.sh as
      merged speaks Bullseye (hostapd/dnsmasq/dhcpcd) and matches NO
      unit once bmcam000 is on Trixie — a NetworkManager (nmcli
      hotspot) variant is required first. Command-path layers
      (tables v6, daemon immediate-apply, help/cfg) are stack-agnostic
      and stay as merged. NEXT SPRINT SCOPE locked by Nick 2026-08-18
      (full spec in TODO-BM-012): nmcli rewrite with three behaviors —
      (1) join Nereus HQ WiFi (stored creds), (2) OPEN AP with
      SSID = hostname (no password, field-tech friendly), (3)
      ephemeral customer SSID/password (this power cycle only, entered
      via settings GUI or BM) — plus a `network:` YAML island + GUI
      control for the boot default (nereus_hq for bench, ap for
      customer-ship units)
- [x] Suite green — 655 OK (+11 tests/test_wap_command.py;
      version-guard tests updated with the v5→v6 bump), 2026-08-18

## 7. Wrap (REPLANNED 2026-08-18 — fleet OS split found)
Probe (2026-08-18T04:50Z, all three over SSH): bmcam000 = Bullseye
(dhcpcd/wpa_supplicant, hostapd present); bmcam003/004 = Trixie
(NetworkManager, no hostapd/dnsmasq, rpicam-* only). Nick's calls:
(a) bmcam000 reformat to Trixie — NICK owns; (b) bmcam000 re-point +
HIL morning review — deferred (§5); (c) Claude owns bmcam003 AND
bmcam004: both to the merged dev tip + HIL.
- [x] bmcam003: merged tip (4132c31) + ffmpeg 7.1.5, video YAML
      (capture_mode video; clip 5 / session 0 / fps 15 / 2.0 Mbps;
      1000x562 inherited; power_halt stays disabled — bench-safe),
      4 verified 15 s bench triples (ffprobe h264, boundary 2.2-3.8 s,
      zero debris), HIL armed via --install-cron + reboot, first 300 s
      clip in flight 05:00:03Z; gallery 200 / manifest 200 / Range 206.
      Artifacts: runs/sprint15_hil_20260818_bmcam003_004/ (bench logs,
      rollup CSV, run_manifest.json); YAML backup
      camera_schedule.yaml.before_sprint15_video_20260818T045535Z,
      crontab backup crontab_before_rc_deploy_bmcam003_20260818T045851Z
- [x] bmcam004: same evidence set, HIL first clip 04:59:42Z (was
      untouched; Nick handed it over 2026-08-18). Note: unit runs
      camera_controls_enabled=false (full-auto optics) vs bmcam003's
      manual focus 1.82 — pre-existing stills-key drift, by design
- [x] docs: bmcam_command_reference.md (wap documented NOT-FIELD-READY
      with the Trixie/nmcli caveat), field-update + provision skills
      updated (video mode, ffmpeg dep, settings GUI), TODO-BM-012
      filed (customer-WiFi join over BM)
- [x] Full suite green on the closeout branch: 673 OK (skipped=1),
      2026-08-18 (no code changes this closeout; docs/artifacts only)
- [ ] morning read of the bmcam003/004 HIL (clip count vs wall clock,
      gaps, temps, ring) — owns the §5 evidence duty
- [ ] bmcam000 re-provision on Trixie (Nick reformatted 2026-08-18;
      bringup at 192.168.1.241 in progress — bmcam-provision skill)
- [ ] PR → development, Nick review

## Known hazards carried in
- SPOT-33507C cellular stall (no Sofar rows since 2026-07-31T19:00Z)
  — HIL's remote-monitoring evidence may be console-only.
- bmcam000 still has the default pi password — change BEFORE bmcam003
  flash/ship prep.
- bmcam003 is potted + off + on the 2026-08-01 build + dev-mode state;
  budget wake + field-update + state reset time tomorrow.
- AP mode kills client WiFi (and with it Tailscale/SSH) while active —
  the auto-revert timer is the only un-brick; never skip arming it.
- FLEET OS SPLIT (found 2026-08-18): bmcam000 Bullseye vs bmcam003/004
  Trixie/NetworkManager. network_ap.sh needs an nmcli variant; stills
  and video runtimes are already dual-stack (rpicam-*/libcamera-*
  fallback in code). bmcam000 being reflashed to Trixie by Nick to
  unify the fleet; ffmpeg is NOT preinstalled on Trixie (installed by
  hand on 003/004 2026-08-18 — add to provision docs).

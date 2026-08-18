# Sprint17 kickoff — GoPro-class video quality (bmcam000)

Paste the block below into a fresh Claude Code session in this repo.
PREREQUISITE: the Sprint16 PR (feature/sprint16-network-wifi) must be
MERGED into development first — this work builds on its settings GUI
and deploy fixes. Runs in PARALLEL with Sprint18 (UI, bmcam003): stay
off bmcam003/004 entirely.

---

You are building Sprint17: GoPro-class video quality for the bmcam
underwater cameras (TODO-BM-013 in TODO.md is the seed — read it).
Goal: crisp, high-definition underwater footage. This sprint is
ENGINEERING variables only — true resolution, crop in native pixels,
fps, denoise, encoder knobs. Nick will down-select to 3-4 simple
customer tiers ("high/medium/low") in a LATER sprint; do not build the
customer simplification now.

REQUIRED READING, in order, before touching anything:
1. CLAUDE.md (manifesto — especially coordinate-system labeling, one
   variable at a time, cut sheets as deliverables)
2. TODO.md § TODO-BM-013 (the scope seed + hidden-knob inventory)
3. sprints/Sprint15_video_recording/DESIGN.md D-S15-2/3 (clip
   pipeline + the geometry link you are about to break — Nick
   APPROVED breaking constraint 4: video gets its own geometry)
4. runs/sprint16_overnight_20260818/SUMMARY.md (three findings that
   constrain this design: bitrate-is-a-ceiling, mux dead time vs file
   size, storage burn at 8 Mbps)
5. sprints/Sprint16_network_wifi/TRACKER.md §4-5 (current fleet state)

Branch: pull the CURRENT development tip (post-Sprint16 merge), create
feature/sprint17-video-quality. Never commit to development or main.
PR back into development with tracker artifacts. Nick reviews the SPEC
before any code (spec session first — bring a preset table proposal).

SCOPE (spec session locks the details):
1. Break the stills/video geometry link: `video.crop_native_xywh` +
   `video.output` (WxH) — video-only keys, stills path byte-identical.
   Label every coordinate system. Upscaling past the crop stays
   FORBIDDEN. Wider-ROI options up to the full 4608x2592 sensor.
2. True 1080p+: needs a >=1920x1080 native crop. Watch the Zero 2W
   H.264 encoder envelope (~1080p30) and CMA; verify encode wall time
   stays < clip wall time (sidecar encode_s is the meter).
3. Expose hidden encoder knobs as YAML + GUI engineering fields:
   --qp (constant quality — the JPEG-quality analog), --profile/
   --level, --intra, --denoise (cdn_off/fast/hq — likely the big
   underwater lever), --sharpness; sensor-mode selection if useful.
4. A/B methodology (the actual deliverable Nick judges): same-scene
   paired clips varying ONE variable at a time; a comparison artifact
   (cut-sheet or gallery grouping) + CSV per clip: settings, size,
   encode_s, boundary_s, CPU temp. GoPro reference: Hero8 1080p ran
   ~30-45 Mbps; ~0.3 bits/pixel/frame is the quality-class target
   (≈8-12 Mbps at 1080p15).
5. Keep visible: storage math per setting (GB/day + ring window on a
   116 GB card) and mux dead-time cost — both go in the comparison
   CSV. These decide what is field-viable, not just what looks best.

Test command: `python3 -m unittest discover -s tests` — full suite
(692+) stays green; stills tests untouched; every code fix ships with
a test. Encoder calls are injected — hardware proves the real ones.

HARDWARE — bmcam000 ONLY (tailnet `bmcam000`; LAN roams between the
two office routers, use the tailnet name):
- Currently: video mode 5-min clips 15fps/2Mbps, cron @reboot ARMED,
  recording continuously, gallery+settings :8080, client WiFi
  (nereus-hq), no Spotter attached (status lines print-only).
- Dev pattern: rsync your branch to ~/repos/bm_cam_legacy_sprint17 on
  the unit + tools/deploy_rc_runtime.sh (field update). YAML backups
  before every config change; the GUI's .before_gui_* backups are the
  customer's undo — leave them.
- HANDS OFF bmcam003 (Sprint18's unit) and bmcam004 (untouched
  control at 15fps/4Mbps).

HAZARDS: default pi password (Nick's task, do not touch); crop
changes touch CMA/memory — check /proc/meminfo at new geometries;
30 fps halves per-frame bits vs 15 — compare at matched bits/pixel;
a dark bench scene undershoots any bitrate cap, so A/B clips need a
LIT scene (or daylight window) to be meaningful.

Trust artifacts, not exit codes. Loud progress, small diffs, tick
tracker boxes only with evidence.

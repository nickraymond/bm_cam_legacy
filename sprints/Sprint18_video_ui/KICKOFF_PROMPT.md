# Sprint18 kickoff — video UI tuning (bmcam003)

Paste the block below into a fresh Claude Code session in this repo.
PREREQUISITE: the Sprint16 PR (feature/sprint16-network-wifi) must be
MERGED into development first — this work builds on its GUI code (PRG
routes, network section, banner). Runs in PARALLEL with Sprint17
(video quality, bmcam000): stay off bmcam000/004 entirely.

---

You are building Sprint18: tuning the customer-facing video UI
(gallery + settings pages served on :8080) for the bmcam underwater
cameras. The UI is how customers browse, play, and download footage —
and how they change settings. It must feel simple and trustworthy on
an iPhone over the camera's open AP.

REQUIRED READING, in order, before touching anything:
1. CLAUDE.md (manifesto — simple, boring, reliable; customer-facing
   naming is VERBOSE, e.g. "help" never "hlp")
2. sprints/Sprint15_video_recording/DESIGN.md D-S15-9 (UI doctrine:
   stdlib http.server ONLY, no frameworks, no pip installs, manifest
   IS the state, Range support for Safari scrubbing)
3. BM_Devel_Pi/videoui_server.py + video_settings.py (current
   implementation: PRG on every POST, edits-not-authors YAML patcher,
   timestamped .before_gui_* backups = the customer's undo)
4. sprints/Sprint16_network_wifi/TRACKER.md §4 (rehearsal findings:
   the Safari POST-replay bug that PRG fixed, the open-AP banner)
5. runs/sprint16_overnight_20260818/SUMMARY.md (fleet state)

Branch: pull the CURRENT development tip (post-Sprint16 merge), create
feature/sprint18-video-ui. Never commit to development or main. PR
back into development with tracker artifacts. Nick reviews the SPEC
before any code — FIRST STEP IS A SPEC SESSION with him: he asked for
"UI adjustments" without locking scope, so bring a candidate list and
let him pick. Known backlog to seed it:
- Multi-select + zip download (Sprint15 prune #2 — never built)
- Gallery: date grouping / newest-first paging for 100s of clips,
  human sizes/durations, poster quality at higher resolutions
- Storage visibility: disk gauge, ring-window estimate ("~2.1 days of
  footage kept at current settings"), oldest-clip date
- Settings page: customer-friendly labels + grouping (engineering
  fields from Sprint17 may need an "advanced" section), clearer
  restart-to-apply flow
- Open-AP banner styling; download UX on iPhone Safari
- Whatever Nick adds in the session

Test command: `python3 -m unittest discover -s tests` — full suite
(692+) stays green; tests/test_videoui_server.py +
tests/test_video_settings.py are your surface; every route change
keeps the PRG (303) regression tests passing; every code fix ships
with a test.

HARDWARE — bmcam003 ONLY (tailnet `bmcam003`):
- Currently: video mode 5-min clips 30fps/8Mbps (Nick's quality
  experiment), cron @reboot ARMED, recording continuously, ~100+
  clips on disk — a REALISTIC gallery corpus, use it. At 8 Mbps
  daylight the ring buffer starts pruning within ~a day: you will
  likely see live ring deletions — good UI test material, do not
  fight it.
- Dev pattern: rsync your branch to ~/repos/bm_cam_legacy_sprint18 on
  the unit + tools/deploy_rc_runtime.sh (field update). The unit's
  camera_schedule.yaml and its .before_gui_* backups are sacred —
  back up before any config change, leave the GUI backups in place.
- A UI restart is cheap (server thread restarts with the runtime;
  reboot = ~2 min gap, crash-safe). Verify with curl + a phone/laptop
  on the LAN; the tailnet name always works.
- HANDS OFF bmcam000 (Sprint17's unit) and bmcam004 (untouched
  control).

HAZARDS: default pi password (Nick's task, do not touch); UI failure
must NEVER kill recording (existing doctrine — keep the thread
isolation); no framework/CDN/external assets — the page must work on
an offline AP; anything destructive in the UI (delete buttons etc.)
needs Nick's explicit sign-off in the spec session first.

Trust artifacts, not exit codes. Loud progress, small diffs, tick
tracker boxes only with evidence.

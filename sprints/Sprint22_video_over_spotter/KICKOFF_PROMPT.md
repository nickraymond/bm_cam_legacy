# Sprint22 kickoff — short video over Spotter (Phase 0 start)

Paste the block below into a fresh Claude Code session in this repo.
PREREQUISITE: PR #54 (`feature/video-over-spotter-ladder`) should be merged
into `development` first; if it is not, branch from that feature branch
instead so the ladder tools are present.

---

You are starting Sprint22: get a 3–5 s H.264 clip from a bmcam through the
Spotter cellular path to the dashboard, WITHOUT changing how a JPEG travels.
The design is already decided — do not re-litigate it. Your job today is
Phase 0 (wire contract + golden vectors), then stop at the gate.

REQUIRED READING, in order, before touching anything:
1. CLAUDE.md (manifesto; branching model — never commit to main/development)
2. sprints/Sprint22_video_over_spotter/DECISION.md (locked decisions D-S22-1..6,
   open questions Q1–Q3)
3. sprints/Sprint22_video_over_spotter/PLAN.md and TRACKER.md
4. docs/video_over_spotter_test_ladder_20260920.md (what was already proven)
5. .claude/skills/spotter-usb-console-capture/SKILL.md (bench method)
6. BM_Devel_Pi/rc_uplink_messages.py (`build_rc_start_message`,
   `build_rc_end_message`) and BM_Devel_Pi/rc_transmit.py
7. Backend, READ-ONLY for now: ~/Documents/GitHub/nereus-vision-dev — inspect
   ONLY via `git -C <path> show origin/staging:<file>`; read backend/CLAUDE.md
   first. NEVER touch `main` in either repo.

PHASE 0 TASKS:
- Q1: is ffmpeg available to the Render backend? Read the deploy config on
  origin/staging (render.yaml / Dockerfile / build scripts). Report, with the
  fallback if not (add to build, or PyAV remux).
- Q2: confirm the send must run inside the UART-owning runtime process in
  video mode, and propose the BM-command trigger against command_tables.py.
- Write docs/bm_media_wire_contract.md per PLAN.md Phase 0.
- Extend tools/bm_video_tx_loopback.py: strip the x264 SEI, repeat chunk 0 at
  the tail, emit real START/END via the production builders.
- Generate SYNTHETIC golden vectors (ffmpeg testsrc2; this repo is PUBLIC —
  never commit bench footage or anything embedding it).
- Update TRACKER.md as you go.

STOP at the Phase 0 gate: present the contract to Nick for sign-off. Do not
start backend or device code, and do not transmit over cellular, before that.
Standing approvals on record: Claude owns bmcam004 for this testing; cellular
sends up to 300 messages are approved for Phase 3 only.

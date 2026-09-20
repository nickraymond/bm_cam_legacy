# Sprint22 — Tracker

Plan: `PLAN.md` · Decisions: `DECISION.md` (locked 2026-09-19).
Units: bmcam004 (transmit; Nick granted ownership for this testing
2026-09-19), bmcam003 (clip source only — do not transmit), SPOT-33507C
(shared by both; USB console on Nick's Mac).

## Done before the sprint (test ladder, PR #54)

- [x] Step 1 size sweep — 5 s 480x270 10 fps: CRF 34 = 172 msgs, CRF 40 = 88
- [x] Step 2 loopback/loss — framing byte-exact; raw Annex-B chosen
- [x] Step 3 bench UART — bmcam004 → SPOT-33507C 172/172 byte-exact
- [x] Backend read-only review (`origin/staging` @ 516b813)
- [x] Skill `spotter-usb-console-capture`
- [ ] PR #54 reviewed + merged to `development` (Nick)

## Phase 0 — contract + vectors

- [ ] Q1: ffmpeg on Render? (read backend deploy config)
- [ ] Q2: sender lives in the UART-owning process; trigger = BM command?
- [ ] `docs/bm_media_wire_contract.md`
- [ ] loopback tool: SEI strip, chunk-0 repeat, real START/END builders
- [ ] synthetic golden vectors (complete, tail-cut, chunk-0-lost, mid-loss)
- [ ] **GATE: Nick signs off the contract**

## Phase 1 — backend (feature branch off `staging`)

- [ ] parser: `.h264` allowed, `fmt` dispatch, Annex-B sniff, agreement flag
- [ ] ingest: type/format/content-type/extension, skip Pillow for video
- [ ] display variant: mp4 + poster, partial-clip handling
- [ ] Alembic: `media_format` += `h264` (idempotent)
- [ ] dashboard: `<video>` in detail view, poster in grid, badge
- [ ] tests incl. byte-identical JPEG regression + non-BM regression
- [ ] **GATE: golden clip plays on staging; legacy JPEG unchanged**

## Phase 2 — device (feature branch off `development`)

- [ ] `rc_video_clip.py` (cut → encode → Annex-B, SEI stripped)
- [ ] video START builder + `fmt` in END
- [ ] transmit via existing `rc_transmit` loop + chunk-0 repeat
- [ ] `video_tx:` island, disabled by default; BM-command trigger
- [ ] tests; stills path byte-identical
- [ ] **GATE: golden round trip + stills regression**

## Phase 3 — prove

- [ ] bench console capture with real START/END (zero cellular)
- [ ] cellular cycle #1: 88 msgs (approval: ≤ 300 msgs)
- [ ] Sofar pull: delivered vs sent, cut position
- [ ] staging dashboard: plays, typed video, poster, right Content-Type
- [ ] next JPEG cycle unaffected
- [ ] (if clean) CRF 34 / 172-msg quality run

## Hazards

- In video mode the recorder owns `/dev/ttyAMA0`; stopping it loses the
  in-progress clip (boot sweep deletes `.part`). Kill by PID, never
  `pkill -f` over ssh.
- The Mac serial monitor holds the Spotter USB port while running.
- bmcam003 YAML has `power_halt.enabled: true / dry_run: false` — matters
  if it is ever flipped back to stills.

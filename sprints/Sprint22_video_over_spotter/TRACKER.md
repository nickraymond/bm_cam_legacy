# Sprint22 — Tracker

Plan: `PLAN.md` · Decisions: `DECISION.md` (locked 2026-09-19).
Units: bmcam004 (transmit; Nick granted ownership for this testing
2026-09-19), bmcam003 (clip source only — do not transmit), SPOT-33507C
(shared by both; USB console on Nick's Mac).

> **Hardware scope (Nick, 2026-09-20): this sprint touches bmcam004 and
> SPOT-33507C ONLY.** A SECOND Spotter is plugged into Nick's laptop for
> other work — never open its port, never send it a command, never log it.
> Address the console by its full name (`/dev/cu.usbmodemSPOT_33507C*`),
> never by a `*SPOT*` glob. See Hazards: the serial monitor auto-discovers
> every Spotter and must be fixed before Phase 3.

## Done before the sprint (test ladder, PR #54)

- [x] Step 1 size sweep — 5 s 480x270 10 fps: CRF 34 = 172 msgs, CRF 40 = 88
- [x] Step 2 loopback/loss — framing byte-exact; raw Annex-B chosen
- [x] Step 3 bench UART — bmcam004 → SPOT-33507C 172/172 byte-exact
- [x] Backend read-only review (`origin/staging` @ 516b813)
- [x] Skill `spotter-usb-console-capture`
- [ ] PR #54 reviewed + merged to `development` (Nick)

## Phase 0 — contract + vectors

Branch `feature/sprint22-phase0-contract` (off `d0359d1`, PR #54's head).

- [x] Q1: ffmpeg on Render? — not provable from the repo (no render.yaml /
      Dockerfile; native Python runtime, no apt). Recommend PyAV wheel:
      measured decode + libx264 re-encode of the golden clip incl. partials
      (contract §10). Unverified: the Linux wheel + RSS on Render itself.
- [x] Q2: confirmed — the in-process command daemon owns the UART for the
      whole video session. Proposal: send at the clip boundary, trigger
      `trg 5`, `TABLES_VERSION` 8 (contract §11). Not built.
- [x] `docs/bm_media_wire_contract.md` (DRAFT until the gate)
- [x] loopback tool `--golden`: SEI strip (== ffmpeg `filter_units=
      remove_types=6`, byte-identical), chunk-0 repeat, START from the
      production helpers + END from the production END builder. Video START
      builder lives in the TOOL until Phase 2 (no device code pre-gate).
- [x] synthetic golden vectors `tests/vectors/bm_media_h264/` — testsrc2,
      36,019 B = 126 msgs (busier than the bench scene's 88; not tuned):
      complete, tail-cut, chunk-0-lost, mid-loss, fmt-disagree.
- [x] vectors verified against the REAL staging parser (`516b813`, run from a
      scratch copy): all 5 reassemble byte-exact to the manifest; chunk-0
      repeat rescues with zero backend change; the `.h264` → `.jpg` filename
      rewrite and `sniff=None` landmines reproduced.
- [x] `tests/test_bm_media_golden_vectors.py` (8 tests)
- [x] Q1 SETTLED (Nick's Render shell, 2026-09-20): ffmpeg 5.1.9 + libx264
      present on `nereus-vision-staging`. Contract §10 now recommends system
      ffmpeg + guard, PyAV as fallback. Unverified: the ingest cron's image.
- [x] Contract rev 2 (Nick's product direction): `crop=` (field of view,
      native px) split from `res=` (output px); `br=` (kbps) alongside `crf=`;
      triggers redesigned as one pipeline with a pluggable source —
      `trg 5` record+send (product), `trg 6` stored reference, `trg 7` newest
      clip. Vectors regenerated (payload sha unchanged; START gains `crop=na`).
- [x] **GATE: Nick signed off the contract 2026-09-20** (rev 2; record in contract §13)

### Carried into Phase 2 (device) — decided 2026-09-20, not started

- [ ] FIRST BITE: re-run the quality ladder BY BYTE BUDGET (88 / 126 / 172
      msgs), not by CRF. Compare 1-pass ABR, 2-pass, CRF+VBV cap, and the
      Pi's hardware encoder recording directly at target crop/res/bitrate
      (no re-encode). Score: bytes vs target, SSIM, encode seconds + energy
      on the Pi Zero 2 W. Backend work does not wait on this.
- [ ] dynamic message budget -> `br` (reuse `rc_time_budget`)
- [ ] stored reference video on the unit = the committed golden payload

## Phase 1 — backend (feature branch off `staging`)

Pre-flight (Nick, 2026-09-20) — BEFORE any backend code: prove the plain JPEG
path for THIS pair, or a later "video does not show up" is undiagnosable.

- [ ] bmcam004 registered to SPOT-33507C in the backend (find the tool URL)
- [ ] admin dashboard links to the registration tool
- [ ] bmcam004 sends one JPEG -> visible on the staging front end

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
- **`tools/spotter_serial_monitor.py` opens EVERY Spotter it finds**
  (`/dev/cu.usbmodem*SPOT*` glob, no port/serial filter). With Nick's second
  Spotter on the laptop it would grab that console too. **Phase 3
  prerequisite:** add an `--only SPOT-33507C` filter (and make the skill's
  `ls`/`lsof` lines name the serial) BEFORE the monitor is run again.
- bmcam003 YAML has `power_halt.enabled: true / dry_run: false` — matters
  if it is ever flipped back to stills.

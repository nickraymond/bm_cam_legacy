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

- [x] bmcam004 registered to SPOT-33507C (Nick, 2026-09-20): tool =
      `https://nereus-vision-staging.onrender.com/register.html` (+
      `/dashboard/external-nodes.html`). Before: staging had NO `BMCAM_004`.
      Now `BMCAM_004` <- node `0xe6fe83ea6b4a2b7f`, active. Second cause of
      "no images": bmcam004 runs `capture_mode: "video"`, which never sends a
      JPEG (last SPOT-33507C media on staging was 2026-07-31).
- [x] admin gear on the dashboard -> registration tools: backend branch
      `feature/dashboard-admin-gear` (`4832552`), admin-token only, verified
      in a browser with/without token. NOT deployed: needs Nick's PR review.
- [x] bmcam004 sent one JPEG -> visible on the staging front end (2026-09-21).
      One-shot stills cycle from a temp YAML (`--transmit --skip-time-window`),
      unit config untouched, video mode restored + verified recording.
      Sent 180/180 at 05:34-05:37Z; Sofar has START + END + **173/180**;
      staging `BMCAM_004` media 52682 renders "96% received".
      - Delivery needed a `note sync`: 17 min after the burst Sofar had
        nothing, Spotter `post` all-OK, "Notecard is 19 % full". ~80 s after
        `note sync` everything queued since 05:28Z arrived.
      - **The 7 lost chunks are consecutive (37-43), sent 05:35:01-05:35:08Z —
        the 5-minute blackout-lane signature** (`transmit_phase (C2)` was OFF).
        For video this gap would land in or near the keyframe. The golden
        clip burst (129 msgs ~ 2 m 15 s) fits INSIDE one 5-minute lane: start
        it ~15 s after a :00/:05 boundary.

Backend branch `feature/sprint22-video-ingest` (nereus-vision-dev), NOT deployed.

- [x] 1a parser (`2cabd6f`): `.h264` allowed, `fmt` dispatch, Annex-B sniff,
      agreement flag. 5/5 golden vectors match the manifest; legacy JPEG/HEIC
      baseline recorded from the UNMODIFIED parser — every pre-existing field
      identical. Pre-existing failure, not ours: `test_w1_partial_ingest`
      (fake lacks `display_key`) fails the same on unmodified staging.
- [x] 1b part 1 (`7b4f006`): `video_derivatives.py` — system ffmpeg, decode +
      re-encode, mp4 (faststart) + poster; complete 50 f/5.0 s, tail_cut
      33 f/3.3 s, mid_lost 50 f; no-ffmpeg / bad fps / headerless fail as
      results, never exceptions.
- [x] 1b part 2 (`0e19a49`): ingest wiring + Alembic `20260921_0009`. Nick's
      calls: explicit nullable `media.video_key` (mp4; also the home for non-BM
      video later), poster in `display_key`, wire metadata + disagreement flag
      in the capture-telemetry JSON, playable seconds in `duration_seconds`.
      Staging DB is separate from production; Render applies migrations
      automatically on merge. Complete vector stored byte-exact (sha = golden);
      tail cut -> 3.3 s playable; no-ffmpeg -> only the original stored.
      NOT verified: the migration against a real Postgres (offline SQL only).
- [x] 1b follow-up (`963959b`): exact `video_playable_s` in capture telemetry
      (`duration_seconds` is an integer column: 3.3 s -> 3).
- [x] 1c dashboard: API adds `type` / `video_url` / `video_key` (additive);
      grid keeps `<img>` on the poster + "▶ video · N s" badge; detail view
      `<video controls poster>`; mp4 download; a video with no poster is a
      placeholder, never a raw `.h264` in an `<img>`. Verified in a real
      browser against a mock API with the golden clip's real mp4 (loads 5 s
      480x270, seek decodes, images/legacy rows unaffected, no console errors).
- [ ] 1d staging proof — needs Nick: review + merge the backend PR, then the
      golden-wire cellular send from bmcam004 (129 msgs).
- FINDING: PLAN's "admin poll against recorded rows" does not exist —
  `sofar-poll-once` only re-polls live Sofar. Nick chose option A: local
  end-to-end for 1b/1c, then send the golden wire file from bmcam004 over
  cellular as the 1d staging proof (AFTER 1b/1c deploy, per D-S22-6).
  Procedure: start ~15 s after a :00/:05 boundary; `note sync` after END.
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
- **A bare `BristlemouthSerial()` defaults to network type 0x01 = cellular WITH
  IRIDIUM FALLBACK.** The production runtime is safe only because it applies
  `cellular_only` from the YAML. Any ad-hoc sender MUST pass
  `network_type="cellular_only"` on every `spotter_tx` — a test burst falling
  back to satellite indoors would be expensive. `bm_video_tx_bench_send.py
  --cellular` forces it (2026-09-21).
- `tools/spotter_serial_monitor.py` opens EVERY Spotter it finds unless given
  `--only SPOT-33507C` (added 2026-09-21; verified with `lsof`: the second
  Spotter's port stayed with Nick's process). ALWAYS pass `--only`.
- bmcam003 YAML has `power_halt.enabled: true / dry_run: false` — matters
  if it is ever flipped back to stills.

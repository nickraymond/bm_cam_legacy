# Sprint22 — Tracker

Plan: `PLAN.md` · Decisions: `DECISION.md` (locked 2026-09-19).
Units: bmcam004 (transmit; Nick granted ownership for this testing
2026-09-19), bmcam003 (clip source only — do not transmit), SPOT-33507C
(shared by both; USB console on Nick's Mac).

> **Approvals on record (Nick, 2026-09-21):** cellular messages are
> UNLIMITED for this sprint **as long as they stay cellular** — every send
> must be `cellular_only` (0x02); Iridium fallback is never acceptable.
> This supersedes the 300-message cap. **bmcam003 has been physically
> removed from the rig** and is no longer in scope or under agent control;
> bmcam004 is the only node on SPOT-33507C's bus, so ebox power-cycling
> affects bmcam004 alone.
>
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
- [x] **1d STAGING PROOF PASSED 2026-09-21** (backend PR #41 merged; Alembic
      `20260921_0009` applied by Render; PostgreSQL 18).
      Golden wire sent from bmcam004 via `bm_video_tx_bench_send.py --cellular`
      (cellular-only forced): 129/129, 06:40:16 -> 06:42:30Z, inside one
      5-minute lane; Spotter console counted 129 on `MS_Q_CELLULAR_ONLY`, no
      queue-full, no satellite path; `note sync` 06:42:50Z.
      - Sofar by 06:44:18Z: START + END + **126/126 unique chunks** (127 chunk
        msgs: the chunk-0 repeat arrived too). Zero loss.
      - Staging media **52691**: `type=video`, `format=h264`, complete, 5 s.
        Stored original downloaded back: 36,019 B, `video/h264`, sha256
        `5fd3ce55...ea30fefe` == golden payload, BYTE-EXACT end to end.
      - mp4 made by the Render ingest cron (so ffmpeg IS on the cron):
        h264/yuv420p 480x270, 50 frames, 5.0 s. Poster JPEG. Telemetry carries
        video_fps/duration/playable/resolution/crop/crf.
      - Live dashboard: grid shows "▶ video · 5 s" on the poster, detail view
        plays it (seek to 2.5 s shows the burned-in 00:00:02.500 counter), the
        96 % JPEG beside it unchanged, no console errors.
      - CAM_0003 / BMCAM_001 / BMCAM_004 image rows unchanged after deploy.
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
- [x] **GATE PASSED 2026-09-21: golden clip plays on staging; legacy JPEG unchanged**

## Phase 2 — device (feature branch off `development`)

- [x] Bite 1 — ladder BY BYTE BUDGET (`tools/bm_video_tx_budget_ladder.py`,
      same reference scene as the CRF ladder, Mac, 2026-09-21; run folders
      local-only). Budgets 66/88/126/172/195 msgs x {veryslow, veryfast}:
      | mode | budget used | fits | encodes | verdict |
      |---|---|---|---|---|
      | 1-pass ABR | 72-75 % | always | 1 | wastes ~1/4 of the paid-for messages: rate control cannot converge in 5 s |
      | 1-pass ABR + VBV | 87-90 % | always | 1 | better, still leaves ~10 % |
      | 2-pass ABR | 99-104 % | ~half | 2 | on target but either side of it |
      | **2-pass at 96 %** | 95-100 % | 9 of 10 | 2 | **pick**; one miss (+3 msgs) at the smallest budget |
      | CRF + VBV cap | 121-127 % | never | 1 | unusable: the cap does not bind on a 5 s clip |
      | CRF bisect (old way) | 92-100 % | always | 5-6 | same quality as 2-pass, 3x the encodes |
      Quality at equal fit is the same (SSIM within 0.006) for 2-pass and CRF
      bisect. NOT measured: Pi Zero 2 W encode seconds/energy, the hardware
      encoder, a calm scene.
- [x] Bite 2 — 2-pass ON THE Pi Zero 2 W (bmcam004, ffmpeg 7.1.5, recorder
      still running at nice 19, real 5 s clip from its own SD, 2026-09-21).
      **Decision (Nick): rate control = 2-pass bitrate.**
      - Cost: 1080p -> 480x270 raw intermediate 8.2 s (decode ONCE, both passes
        read it); pass 1 ~1.3 s; each pass 2 ~1.2 s. Peak 49.9 C, never throttled.
      - 2-pass UNDERSHOOTS on a calm scene: 67-83 % of target at 44-126 msgs
        (the busy Mac scene hit 95-100 %). Scene was not saturated (CRF 18 =
        242 msgs). Fix = reuse the pass-1 stats, re-run ONLY pass 2 with a
        proportional correction: lands 94-100 % in <= 3 tries, always fits,
        ~4.5-5.3 s total.
      - Preset at equal bytes (88 msgs): ultrafast SSIM 0.970, superfast 0.981,
        veryfast 0.984, faster 0.985, **medium 0.987** — all ~4.5-5 s, so
        `medium`.
      - Scene dependence is large: this calm scene gets SSIM 0.987 at 88 msgs;
        the hand-waving scene got 0.89 at the same budget.
      - NOT measured: recorder stopped (boot-cycle case; can only be faster),
        the hardware encoder (dropped: 2-pass costs ~5 s), energy in joules.
- [x] Bite 3 — on-camera cycle, branch `feature/sprint22-video-tx`:
      `rc_video_clip.py` (budget fit), production video START/END builders,
      `transmit_video_clip` (reproduces the golden wire byte-for-byte),
      `rc_video_tx.py` (`video_tx:` island, DISABLED by default; cycle = Spotter
      time -> record 5 s + 2 s lead-in, 1080p kept on SD -> fit -> lane wait ->
      send cellular-only -> halt). 830 tests, only the pre-existing numpy error.
- [x] **FIRST REAL CAMERA VIDEO ON THE FRONT END — 2026-09-21** (bmcam004, code
      run from /tmp on the Pi; the unit's deployed software untouched).
      | run | result |
      |---|---|
      | A, no transmit | record 7 s, fit 125/126 msgs (98.9 %), cycle 25 s |
      | B1, transmit, 30 s post-boundary guard | sent 87/87 but **Spotter rejected 16: "Queue MS_Q_CELLULAR_ONLY is full" 07:30:31-07:30:47Z** (its own scheduled transmission held the 2-slot queue, +31..+47 s after the boundary). Sofar 71/87, chunks 1-9 + 11-17 (the keyframe) lost -> staging media 52700, 81 %, badly damaged |
      | B2, transmit, 60 s guard | sent 122/122, Spotter queued 125/125, **0 queue-full**, Sofar 122/122 + repeat -> staging media **52705**, complete, plays 4.9 s 480x270 |
      - Encoder bug found + fixed from B1: proportional correction bounced
        77 % -> 131 % -> 69 % and shipped the 69 % try. Now brackets +
        interpolates (B2: 77 -> 133 -> 97 %) and keeps the best fit.
      - The Pi gets NO signal when the Spotter rejects a message: a queue-full
        loss is invisible on the device and only shows on the USB console.
      - Minor: clip is 49 frames / 4.9 s, not 50 (`-sseof -5` + fps filter).
- [ ] SOAK PREREQS: deploy the branch to `~/BM_Devel_Pi` (real deployment, not
      /tmp), YAML `video_tx.enabled: true` + `transmit_phase.enabled: true`
      with `post_boundary_guard_s: 60` + `power_halt` enabled + window not
      enforced, ebox duty cycle (10 min on / 5 off), periodic `note sync`.
- [ ] OPEN: keyframe protection beyond the chunk-0 repeat (a 16 s queue-full
      window at the START of a burst destroys the clip).
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

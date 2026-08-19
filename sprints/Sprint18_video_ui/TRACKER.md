# Sprint18 — Tracker

Tick a box only when an artifact proves it. Ran 2026-08-18 → 2026-08-19.
Branch `feature/sprint18-video-ui`, PR #45 → development.

## 0. Setup

- [x] Prerequisite confirmed: Sprint16 (PR #42) merged into
      `development`; branch cut from that tip (`a38b3fd`)
- [x] Corpus survey before any design: bmcam003 read-only — 134 clips /
      13.7 GiB at the time, real posters, real sidecars, real config
      values pulled to the Mac so every mock rendered TRUE data.
      No invented numbers at any point in the sprint.
- [ ] SPEC.md / DESIGN.md — **not written**. This sprint was specced by
      mock instead (§1); the decisions log below is the record. If that
      is not acceptable as the sprint artifact, it needs writing
      retrospectively.

## 1. Spec-by-mock — seven rounds with Nick

No code was written until the design was agreed. Each round was a
self-contained HTML mock rendering bmcam003's real corpus, reviewed on
Nick's phone and laptop.

- [x] R1 gallery + watch view: layout, filters, nav, pills, metrics
- [x] R2 decisions locked: 50/page, AP banner, 2-up, both bitrates with
      units, hour dropdowns, lazy detail route
- [x] R3 HEIC dropped, focus distance greyed under autofocus, settings
      table made mode-aware
- [x] R4 26/page, date RANGE to match the hour range, Video/Image labels
- [x] R5 save/restart flow: persistent "saved, not yet running" +
      "Save and restart now" (auto-reboot on save REJECTED, §6)
- [x] R6 day grouping promoted to a real feature; slow-AP toggle dropped
- [x] R7 Nereus branding + Images gallery + Sprint17 realignment
- [x] Decisions carried into code exactly as agreed; where reality
      differed (bitrate is a ceiling, not a setting worth showing alone)
      the mock showed the evidence rather than arguing it

## 2. Mid-sprint realignment — Sprint17 (PR #44) merged

- [x] Branch fast-forwarded to `dbbe177`; all work re-based on the new
      module
- [x] GUI surface grew 12 → 18 fields; new `video.preset` +
      `video.encoder.*` absorbed into the Advanced disclosure the mock
      had already reserved for them
- [x] `progressive_jpeg.output_width` re-scoped to stills-only (Sprint17
      relabelled it) — moved out of video mode
- [x] Sidecar v2 (`preset`, `sensor_mode`, `avail_px`, `scale`) surfaced
      in the clip detail; `scale > 1` flags as upscaled, making the
      Sprint15 defect visible rather than inferred
- [x] Preset labels stripped of their static day estimates so the
      measured Storage panel owns that claim (Nick's call, §4)

## 3. Gallery + stills (videoui_server.py, stdlib only — D-S15-9 intact)

- [x] Videos | Images tabs; `/images.json` lists stills from the
      existing `images/` dir (one stat + one small sidecar read each)
- [x] `/images/<name>` serves stills with the same basename-only
      hardening as `/videos/` — traversal, dotfiles, debris, unknown
      extensions all 404 (tested)
- [x] Lazy per-item detail: `/clip/<stem>.json`, `/photo/<stem>.json`
      fetched only on open, so the LIST stays cheap as counts grow
- [x] `manifest.json` entries carry `br`/`preset`/`scale` for the cards
- [x] Live `disk` block injected at request time, not taken from the
      manifest file (which is only rewritten at a clip boundary)
- [x] 26/page, UTC date-range + hour-window filters (hours apply within
      each day of the range), day grouping with per-day counts
- [x] Achieved vs set bitrate on every card
- [x] Downloads via `Content-Disposition` (`?dl=1`); plain views
      deliberately unaffected

## 4. Settings

- [x] Grouped and mode-aware: `capture_mode` drives which fields render
- [x] Engineering knobs behind Advanced
- [x] Focus distance inert under autofocus, with the reason said out loud
- [x] Storage panel MEASURES retention from recent clip cadence
- [x] Storage panel PREDICTS: recomputes from the selected bitrate and
      cap before saving, anchored on the observed encoder efficiency
      (7.3 d @2 Mbps → 1.2 d @12 Mbps on bmcam003). Measured figures
      never move with the selector; a drift line says why
- [x] Photo mode gets its own measured fill estimate
- [x] "Save settings" + "Save and restart now"; save happens FIRST so a
      failed save never costs a reboot (tested)
- [x] "Saved, not yet running" derived from config mtime vs process
      start — no state file, self-clearing, catches SSH hand-edits
- [x] HEIC retired from the dropdown; modes read Video / Image

## 5. Branding + responsive

- [x] Palette, brand band, buttons, cards, pills taken from
      `nereus-vision-dev/backend/dashboard/gallery.html`
- [x] No webfont LINKED — the camera serves this over an offline
      hotspot; the dashboard falls back identically
- [x] Responsive by additive `min-width` queries only: 3/4/5 columns at
      700/1000/1320px, two-column detail at ≥900px, two-column settings
      at ≥1000px. Phone layout verified unchanged at 375px

## 6. Bugs found and fixed (with the reasoning that mattered)

- [x] **Save-poison hazard, caught before it shipped.** Retiring HEIC
      would have failed EVERY save on a unit still set to it, because
      the form echoes every field back and `patch_yaml` validated before
      comparing — the bmcam000 float-echo shape again. An echoed value
      matching the file is now a no-op; a genuinely off-menu value is
      still refused.
- [x] **Auto-reboot on save REJECTED** (Nick asked whether saving should
      force a restart): it breaks batching, drops the customer's AP
      session mid-request so a success looks like a crash, and re-arms
      the Safari POST replay PRG was added to fix. Persistent pending
      state chosen instead.
- [x] Stills metadata blank: detail stem derived by stripping `.jpg`
      left a `_compressed` tail; every `/photo/` 404'd
- [x] Stills download dead: bare `<a download>` is ignored by iOS Safari
      and defeated by colons in stills filenames
- [x] Photo letterboxed in a dark box: grid items stretch by default
- [x] Focus box stayed greyed when focus mode changed — server-only
      rule; now re-evaluated in the browser
- [x] Storage note contradicted the mode selector
- [x] **UI drew the cap at the hardcoded 75% default** instead of the
      unit's configured limit (found while setting up the fleet run at
      60%). The ring always read the YAML correctly, so only the display
      lied. Regression test added.
- [x] Own-goals caught by verifying rather than assuming: CSS
      specificity (`.flag` losing to `.mgroup td.v`), a float squeezing
      the banner, a stale-DOM read mid-rebuild, a per-day count that
      grew as you paged

## 7. Tests

- [x] **775 OK (skipped=1)**, up from 760 at the Sprint17 merge — 38 new
      test methods
- [x] PRG (303) regressions still green on every POST route
- [x] New coverage: stills routes + traversal 404s, both detail routes,
      live disk block, `Content-Disposition` both directions,
      save-and-restart ordering, retired-value guard, mode-aware
      visibility, storage panel following the selector, cap read from
      config

## 8. Hardware validation — bmcam003

- [x] Deployed with timestamped backups and an import check BEFORE each
      runtime swap; recording resumed on its own every time (boot sweep
      cleared the in-flight `.part`). **No clips lost across six
      deploys.**
- [x] All routes 200 over the tailnet; startup line names both dirs
- [x] Manifest regenerated at the next boundary carrying `br=9.3
      preset=wide_1080p scale=0.833`
- [x] Detail filled from real sidecars (45.6 °C, 9.2 s mux gap,
      autofocus note); stills detail from real capture metadata
- [x] Gallery page costs 1.9 MB of lazy-loaded posters
- [x] Verified at 375 / 1024 / 1440px, no horizontal scroll either page
- [ ] **Open-AP banner NOT verified on hardware** — bmcam003 sat on
      `nereus_hq` all sprint, so the banner path has only ever rendered
      in the mock. Carry to the next AP session.

## 9. Fleet HIL — ring buffer under three video settings (IN FLIGHT)

Started 2026-08-19 06:00Z. See `runs/sprint18_fleet_ring_20260819/`.

- [x] Sprint18 build on all three units (bmcam000/003/004)
- [x] One variable, three points: 720p lean @4 / 1080p lean @6 /
      1080p @9.3 Mbps; fps, clip length, session, limits held equal
- [x] Cap lowered to 60% + `fallocate` ballast so the ring is actually
      REACHABLE overnight — at 75% none of these units would have
      reached it, and a ring test that never fires proves nothing
- [x] Pre-flight safety check: if every clip were pruned, used% lands at
      47.4 / 26.9 / 34.6% — far below the cap, so the pause path cannot
      trigger spuriously
- [x] All 869 sidecars archived first (on-unit + Mac) because the test
      destroys the clips whose metadata the analysis needs
- [x] **Ring engagement PROVEN, not assumed** (bmcam004, 06:11:40Z): 19
      clips deleted oldest-first, 3 files each, used% settled at exactly
      60.0%, recording uninterrupted
- [ ] Overnight results + AM evaluation
- [ ] **CLEANUP OWED:** remove `ringtest_ballast.bin` /
      `ringtest_nudge.bin` and restore `max_used_pct` to 75 on all three

## 10. Wrap

- [x] TODO-BM-014 filed: the ring is video-only, so a photo deployment
      has no unbrickable-storage guarantee
- [x] Sprint19 backlog written (`sprints/Sprint19_live_controls/`)
- [x] Branch pushed, PR #45 opened into development
- [x] This tracker
- [ ] Nick review + merge
- [ ] Post-merge: re-point all three units at blessed git bits (they
      currently run rsync'd scratch copies from
      `~/repos/bm_cam_legacy_sprint18`)

## Hazards carried in

- Settings GUI **edits, does not author**: a unit's YAML must contain
  every GUI key. All three units carry the full 18 post-Sprint17 keys.
- The camera is a **single-owner device** — this bit Sprint15/16 and is
  the central problem for Sprint19's live view.
- Reboot is the only apply path; it costs the in-flight clip and ~2 min.

## Hazards carried out

- Ballast + 60% cap are live on all three units until cleaned up.
- Units run scratch copies, not blessed bits, until post-merge redeploy.
- Open-AP banner unproven on hardware.
- Stills have no ring buffer (TODO-BM-014).

# HIL review kickoff — fleet ring-buffer overnight (2026-08-19)

Paste the block below into a fresh Claude Code session in this repo.

---

You are reviewing an overnight hardware-in-the-loop run on the bmcam
fleet. It started 2026-08-19 06:00Z on all three units. Your job is to
say what actually happened, find what went wrong, and produce the
artifacts — not to keep building features.

REQUIRED READING, in order, before touching anything:
1. CLAUDE.md — especially "trust the artifacts, not the exit code" and
   the rules on protecting field operations
2. runs/sprint18_fleet_ring_20260819/RUN_NOTES.md — what was set up,
   why the cap is 60%, why there is ballast, and the cleanup owed
3. sprints/Sprint18_video_ui/TRACKER.md §9 — the HIL section
4. runs/sprint16_overnight_20260818/SUMMARY.md — the format your
   summary should match (table per unit, verdict, findings, files)

WHAT WAS RUNNING. All three units on the Sprint18 build (PR #45),
video mode, 15 fps, 5-minute clips, continuous. ONE deliberate variable:

  bmcam000  wide_720p_lean    4 Mbps   1280x720
  bmcam004  wide_1080p_lean   6 Mbps   1920x1080
  bmcam003  wide_1080p        9.3 Mbps 1920x1080

Held equal: max_used_pct 60, min_free_gb 10, ring_dry_run false.
Each unit carries a fallocate ballast file so the 60% cap is actually
reachable overnight; bmcam004 also has a 2.5 GB nudge file that pushed
it over the cap immediately. Ring engagement was already proven on
bmcam004 at 06:11:40Z (19 clips pruned oldest-first, settled at exactly
60.0%). The question tonight is whether it stayed correct for hours
under three different fill rates.

THE PRIMARY QUESTION: did the ring buffer hold the unbrickable-storage
guarantee all night, on all three units, without disturbing recording?

Check, with evidence for each:
- Deletions strictly OLDEST-FIRST, exactly 3 files per clip (mp4 +
  _thumb.jpg + .json). Never a .part or .tmp. Nothing outside the video
  directory — the ballast files MUST still be intact and untouched.
- manifest.json count == mp4 count on every unit, at all times you can
  observe. A mismatch means the manifest and the ring disagree.
- used% held just under the cap rather than sawtoothing wildly.
- `paused=True` must NEVER appear: there was ample prunable footage.
  If it did, that is the headline finding, not a footnote.
- Zero ERROR/Traceback lines, zero "clip failed", exactly one in-flight
  .part per unit.
- Cadence continuous: no missing intervals beyond mux/boundary work.

Then the quality-ladder questions:
- Prune rate per unit vs its bitrate. Does it scale the way the storage
  panel predicted? The panel estimated retention from observed encoder
  efficiency — compare its estimate against what actually happened. If
  the panel was wrong, that is a real finding: customers will use that
  number to choose settings.
- Clip size vs DAYLIGHT, not just the bitrate ceiling. The 2026-08-18
  run found the dark office undershoots every cap and sizes jump around
  13:20Z. Expect the three units to diverge once the lights come up.
- boundary_s (mux dead time) vs file size at each quality point — the
  TODO-BM-013 cost. 1080p @9.3 will be the worst.
- CPU temperature per unit; 1080p @9.3 is the hardest worked.

Also check the UI, which is new and was reading a manifest that was
being actively pruned all night:
- Does the gallery still load on each unit, and do day grouping, paging
  and per-day counts stay consistent?
- Does /clip/<stem>.json 404 CLEANLY for a clip deleted between listing
  and tapping, rather than erroring?
- Does the storage panel's measured figure track the real burn rate?

TOOLING. tools/sprint17_ab_rollup.py already pulls per-clip sidecars and
joins them with the Sprint17 geometry fields (preset, sensor mode,
available px, scale) and flags UNDERSHOOT clips — read its header before
writing anything new. Extend it or write a small sibling; do not
reinvent it. Pre-run sidecars for every clip that existed before the
test are archived on each unit at /home/pi/sidecar_archive/ and on the
Mac under the previous session's scratchpad — the ring deletes sidecars,
so clips pruned overnight only exist in that archive.

ARTIFACTS to produce in runs/sprint18_fleet_ring_20260819/:
- clips_<unit>.csv — per-clip rollup (match the Sprint16 column shape:
  utc, fn, sz, res, fps, dur, tmp, rd, encode_s, boundary_s; add preset
  and scale now that sidecar v2 carries them)
- ring_<unit>.log — the [RING] lines, with deletion counts and bytes
- SUMMARY.md — table per unit, explicit VERDICT, findings separated
  into "explained anomalies" vs "real failures", and what it means for
  the shipping default

HARDWARE RULES. The units are recording; keep your analysis READ-ONLY
unless something is actually broken. Do not reboot to "fix" anything
before you have captured the evidence. If a unit is wedged, capture
logs first, then act.

CLEANUP OWED once the evaluation is done — the units are currently
running with an artificially small footage window:

    ssh pi@bmcamNNN 'rm -f /home/pi/ringtest_ballast.bin /home/pi/ringtest_nudge.bin'

and restore max_used_pct to 75 on all three (settings GUI, or
video_settings.patch_yaml which backs up and validates). Confirm the
free space returns and the storage panel's retention figure jumps back
up. Do NOT do this before the evidence is captured.

Also note for the record: PR #45 may have merged overnight. If it has,
the units should be re-pointed at blessed git bits — they currently run
rsync'd scratch copies from ~/repos/bm_cam_legacy_sprint18. Flag it;
don't do it mid-evaluation.

Trust artifacts, not exit codes. Loud progress. If the ring misbehaved,
say so plainly with the log lines that prove it.

# Sprint28 camera side: HANDOFF (2026-10-06 ~20:30 PDT; wrap-up 2026-10-07)

From the camera build session ("Build Sprint28 camera side: RAW → JPEG-XL stills", context ~90 %)
to a fresh camera session, for any NEW build work. **Update 2026-10-07:** Nick delayed the RC to fix
dropped messages first; #133/#134 stay DRAFT; the old session is rotated out (no merges pending from
it). Next likely camera work: a start-delay key, if the bench prototype works. The EM (new: "Bristlemouth camera program EM handover") decides;
Nick approves merges and the RC config.

## 1. Branches and PRs

| PR | branch @ head | base | state | content |
|---|---|---|---|---|
| bm #120 | feature/sprint28-camera | development | **MERGED** (bbda9bf) | nrjxl v1 (4 Bayer planes, container profile 1), byte search, rfb fallbacks |
| bm **#133** | feature/sprint28-low-gain @ **f05077a** | development | DRAFT / HOLD | `camera.exposure.profile` auto\|low_gain (registry v9, default auto, `max_shutter_us` 30000, `max_gain` 16); sunrise tools |
| bm **#134** | feature/sprint28-b3a @ **2272f87** | #133's branch | DRAFT / HOLD | B3a = `still.raw.layout` rgb (container v2, method 20) + `still.raw.rgb_encode_max_s` 45 (registry **v11**); search calibration; e4 last attempt; fixtures + tools |
| bm #127 | fix/sync-settle-126 @ 7aeb8d7 | development | DRAFT | #126 sync settle 45 s + console de-dup (Nick decides R1 vs R1.1) |
| bm #135 | docs/backlog-long-shutter-dim | development | MERGED | backlog spec: long shutter, gain locked at 1.12 |

**Merge order** (when the EM sends one line): un-draft, then merge **#133, then #134**. Both
merge cleanly into development af059d7 (checked 2026-10-06 ~20:00 with `git merge-tree`). The
RC defaults: exposure **auto**, `still.raw.layout` **bayer4**, unless Nick says rgb.

## 2. Test status

- **Full suite at the #134 tip 2272f87** (= development up to #120 + #133 + #134; development's later
  commits since then are docs only): **1834 passed, 2 skipped, 0 failed** (2026-10-06 ~20:40 PDT).
  `hil/tools/test_hil_guards.sh`: 0 failures. Golden masked diff: only the rgb traces changed
  beyond config hashes.
- To rerun:
  - `.venv-dev/bin/python -m pytest -q tests/` (~3 min);
  - `tools/golden_hash_masked_diff.py --ref <parent>` after any re-record.

## 3. Hardware findings (bmcam004 unless noted)

- **Low gain:**
  - smoke PASS 2/2 (LG1/2/4): low_gain 29997 µs at gain 16 with the patched tuning file loaded;
  - daylight both members at the gain floor (LG3).
  - RC = **auto** (Nick 10/6); low_gain stays an off option.
- **B3a B0** (TE, 1d0ff4f):
  - VmPeak **126 MiB** under the 250 MiB guard, 0 kills;
  - ~9.5 s per e5 encode;
  - every run took 3 encodes (search 28 s of 30). Fixed in **4584436**: median-curve prior +
    fill-dependent slope + accept ≥ 0.90, giving ≤ 2 encodes on 29/31 replayed frames; rgb cap 45 s;
    e4 last attempt. Needs the TE's B0 re-run to confirm on the unit.
- **Desk Linux arm64** (Docker trixie, cjxl 0.11.2, real 57521 frame): e5 VmPeak 124–125 MiB,
  VmHWM ~99 MiB; the guard kills at 80/110 MiB with "Allocation failed" → `rfb=mem`
  (`runs/s28_b3a_vmpeak_20261005/`).
- **B1 PASS:**
  - stills 57901 / 57914 (19:00 / 20:00Z, layout rgb, 186 / 187 msgs, d 4.35 / 4.02, 2 attempts)
    decode with the backend's v2 decoder (nvd 23a4324);
  - sha = sent, bit-identical to djxl; render OK; LinearRaw DNG opens in LibRaw
    (`runs/s28_b3a_b1_decode_20261006/`).
- **Quality evidence:**
  - B3a wins big in low light: 30 TG-7 frames, +20.6 / +11.8 SSIMULACRA2 (P50 / P90) vs today's JPEG
    at 195 msgs, 0.59× / 0.71× the bytes at equal quality.
  - It is ≈ the JPEG on one lossless above-water daylight frame (`runs/s28_b3a_daylight_20261006/`).
  - e5 beats e4 by 4 s2 at equal bytes (`runs/s28_b3a_effort_20261005/`).

## 4. Open items

1. **Catalog v11 re-vendor (backend):** `docs/bmcam_config_catalog.json` sha **1ee3f33956c6**
   (registry v11: still.raw.layout, still.raw.rgb_encode_max_s, camera.exposure.*). `refresh_gets`
   now splits `still` into sub-groups (it no longer fits 2 `<CF>` parts). Until the backend
   vendors it, v9–v11 keys can be set only locally (the TE's `hil_b3a_layout.sh`), and the backend
   cannot confirm v11 config hashes.
2. **nrjxl peak memory above the R0.3 120 MB line:** R0.3 judged cjxl's own peak RSS ≤ 120 MB for the
   v1 planes (~31–40 MB). B3a's single RGB VarDCT encode is **VmHWM ~99–103 MiB, VmPeak ~124–126 MiB**
   (desk + B0), so any peak figure above 120 MB is the VIRTUAL peak, not RSS. It is inside the
   250 MiB guard with 2× headroom, but LADDER R0.3's 120 MB criterion is v1-only. **To do:** make
   the criterion layout-aware (rgb: VmPeak < 250 MiB guard with ≥ 1.5× margin, VmHWM recorded) and
   record B0's VmHWM per run; check supervisor RSS during the rgb prep (~20 MB ESTIMATE).
3. **B0 re-run on 4584436:** confirm ≤ 2 encodes typical and search ≤ 45 s; predicted wake ~490–510 s.
4. **Underwater daylight frame** for the B3a-vs-JPEG check (one above-water daylight frame says ≈).
5. **Wake phase / lane guard** (on hold, Nick): a lane alone is a no-op on a :00 wake (480 s budget);
   the lever is the Spotter wake time (power-on ~HH:03:40 + an hourly lane).
6. **Backend nit:** `nrjxl_dng.dng_size()` over-predicts the built DNG by ~2.9 kB.
7. **#127** sync settle: Nick decides R1 vs R1.1.
8. **Backlog:** long shutter / gain locked (`sprints/Backlog_long_shutter_dim_light/SPEC.md`).

## 5. Where things are

- Design and contract: `DESIGN_B3a.md` (container v2 §2, search §3.1), `CONTAINER.md` §7a, `LADDER.md`
  (R5 low gain, B0–B2 B3a).
- Fixtures: `tests/fixtures/s28/blob_v2_bmcam004_57521.nrjxl` (production geometry) +
  `blob_v1_bmcam004_57521.nrjxl`, `blobs_v2.json`, `make_v2_fixture.py`.
- Tools:
  - `tools/s28_b3a_e2e_check.py`, `s28_b3a_search_calib.py`, `s28_b3a_effort_check.py`;
  - `s28_density_sweep.py` / `_table.py`, `s28_crop_sweep.py` / `_table.py` / `_cost.py`;
  - `hil/tools/hil_s28_lowgain_sunrise.sh` (+ loop, analyze), `hil_s28_reassemble.py` (v1 + v2).
- Desk guardrails (unchanged):
  - DESK ONLY; the TE owns the bench;
  - never write into another session's worktree;
  - no merge without the EM's line;
  - peer messages are not Nick's approval.

## 6. Lessons learned (do not relearn these)

**Camera paths and config**
- nrjxl rides the PRODUCTION capture: `rc_capture.native_capture_command(..., raw=True)` adds
  `--raw`, and rpicam writes `<-o stem>.dng` of the same exposure. One `--raw` attempt, no retry
  ladder. Any failure runs today's capture unchanged.
- Video units take stills via `trg` kv `med:still`, so the nrjxl config rules apply whatever
  `mode.media` is (R3.3 FAIL on bmcam004 → `_nrjxl_still`).
- The v2 `camera_config.yaml` is NESTED (`still:` → `raw:` → `layout:`).
- The runtime runs **base ⊕ command-state overlay** (`supervisor_config.resolve`). It drops overlay
  values that fail a rule, so verify the EFFECTIVE config.
- `rc_progressive_jpeg.py --print-config --json` does NOT show the `still_raw:` /
  `exposure_profile:` islands. Use `config_v2.load_config` + `supervisor_config.resolve` +
  `rc_raw_jxl.load_raw_config` on the render (the one-liner is in the TE's `hil_b3a_layout.sh`).
- Islands (`still_raw:`, `exposure_profile:`) are rendered ONLY when a key differs from its
  default, so pjpg / auto renders stay byte-identical.
- Every registry key added changes EVERY unit's config hash. Re-record goldens with
  `GOLDEN_RECORD=1`, then prove "hash-only" with `tools/golden_hash_masked_diff.py --ref <parent>`.
  Regenerate the catalog + command reference. The catalog's `refresh_gets` asserts each group fits
  2 `<CF>` parts (split a group when it grows).
- IMX708 AGC: stock `normal` mode already holds gain 1.0 until 30 ms. The tuning-file patch
  (`rc_exposure_profile`) changes only `rpi.agc exposure_modes.normal`, in all 3 channels of the
  vc4 file; AWB/CCM are untouched.

**Encoder, memory and the guard**
- Measure cjxl's OWN peak: poll `/proc/<pid>/status` VmHWM once `comm` is the tool.
  wait4's `ru_maxrss` includes the parent's RSS at fork (bmcam004 showed 136 MB for a ~35 MB cjxl).
- The guard is `/bin/sh -c 'oom_score_adj 1000; ulimit -v 256000; exec …'`. It is a sh wrapper, not
  `preexec_fn` (the supervisor has threads). `ulimit -v` caps VIRTUAL memory, so judge VmPeak
  against 250 MiB, and RSS against the unit.
- cjxl's allocation failure is **rc 1 + "JXL_FAILURE: Allocation failed"** (no signal).
  `run_capped` classifies it as `mem` by its stderr words. Keep `alloc` in `_MEM_WORDS`.
- The Docker linux/arm64 Debian trixie container is a good memory proxy: the v1 plane's VmHWM
  there matched bmcam004. It is NOT a CPU-time proxy. bmcam004 e5 VarDCT 1600×900 ≈ 9.5 s; Mac
  single-thread ≈ 0.5–0.8 s; Pi/Mac ≈ 12×.
- VarDCT e4 is ~6× faster but −4 SSIMULACRA2 at equal bytes. Use it only as the last-attempt time
  lever.

**Quality work**
- **Always look at the cut sheet.** SSIMULACRA2 / butteraugli REWARDED camera-space RGB VarDCT that
  had red/magenta blotches. The cause: dim underwater red, which VarDCT quantised as dark and the
  WB gain then amplified.
  - Code WB'd values.
  - Never clip: WB-then-clip broke overexposed scenes (55–73 % clipped).
  - Hence B3a's headroom scale, which is an exact bound from the mosaic for a bilinear demosaic.
- Bayer-plane downscaling is structurally lossy (a lossless resampled mosaic scores 55 at 1000 px).
  Shrink RGB, not the mosaic.
- B3a's win is scene-dependent: large in low light, ≈ JPEG on bright high-detail daylight.
- The 4-plane nrjxl is ≈ the JPEG in low light and far worse in daylight.
- bytes(d) curves differ by scene complexity (smooth scenes are steeper), so a constant-slope prior
  misses. `tools/s28_b3a_search_calib.py` replays the real search on measured curves. Use it before
  touching the search constants.
- djxl → PPM keeps the stream's 12-bit depth (maxval 4095) even with `--bits_per_sample=16`; PNG
  is scaled. Always honour maxval (the backend refuses > 4095).
- Build v2 fixtures with the REAL crop origin / native size (`make_v2_fixture.py` takes them from
  the v1 header). A crop-sized DNG gives 0,0 / 1600×900, which no unit sends.
- Comparisons must use the same pixels: today's pjpg path on the neutral render, upsampled, against
  the lossless neutral reference. The ISP JPEG differs in look, not quality; show it, don't score it.

**Wire and timing**
- A lane wait spends the same 480 s cycle budget. The code skips a wait that does not fit
  (`skipped_no_budget`). On a :00 wake a lane cannot clear the :05 report; only the wake phase can.
- The health-check sync is boot-anchored, not on the UTC grid: neither the lane nor #127's settle
  can express it.

**Desk practice**
- Never write into another session's worktree (a hook blocks it). Read-only copies only.
- `git stash` is shared across worktrees. Never stash while a background test run uses the tree.
- The repo venv `.venv-dev` has no OpenCV or rawpy (use the rig venv). The backend decoder needs its
  pinned `imagecodecs` (a scratch venv).
- macOS `pgrep -a` differs from Linux; tests stub `busy()`.
- Real-time command-integration tests make the full suite ~3 min; that is normal, not a hang.

# Sprint28: RAW → JPEG XL stills from the IMX708 bm cameras to the backend (SPEC, r3)

Status: **r3** (2026-10-02). r1 was reviewed by an independent fresh-context reviewer (§12), and all
findings are applied. r2 merged as bm #114. r3 records Nick's rulings on Q1–Q3 (§10).
Written desk-only during the R1 gates G4/G5. The build starts
Mon 2026-10-05, after the R1 ship decision (Sun 10/4). Nick decided on 2026-10-01 (option A) that this is
its own sprint after R1.
Author: Claude session "Sprint28 RAW→JXL spec". Coordinator: the EM session "Engineering Manager
coordination". Decisions and blockers go to the EM, not to Nick.

Repos at writing: bm_cam_legacy `origin/development` 18ed066; nereus-vision-dev (nvd) `origin/staging`
108d1e7; nereus-camera-test-rig (rig) `origin/main` 372d6f2 (the compression study, PRs #84/#85).
Labels: **MVP now** · **MVP stretch** · **Next sprint** · **Future**. Values carry a source. Anything
without one is marked **ASSUMPTION** or **ESTIMATE**.

---

> **r4 RULING (Nick, 2026-10-02 ~21:30 PDT): spatial DENSITY over field of view. Supersedes the r3 Q1 "go bigger".**
> Nick's goal is more detail per coral, not a wider scene. Today's still = the 1600×900 native crop **downsampled to
> 1000×562** (`still.output_width` 1000; density 0.625) and heavy JPEG. nrjxl keeps **every native pixel** (density 1.0),
> so the **default nrjxl crop = today's `still.crop` 1600×900 at native resolution** (same field of view, 1.6× linear /
> 2.56× pixel density, ΔE00 0.11 vs 0.50 at 50 kB). 2000×1124 and 2400×1350 stay as **optional presets only**, never the
> default. R0.3 measures 1600×900 FIRST (it gates the feature), then the larger presets for information. Wherever this
> SPEC says "default 2400×1350" or "step down", read: default 1600×900 native; larger crops are opt-in presets.

## 0. Facts this spec rests on

### 0.1 The compression study (rig `compression_study/REPORT.md`, `results/results.csv`)

| fact | source |
|---|---|
| Method **D2**: Bayer mosaic → 4 planes (R, G1, G2, B) → integer sqrt LUT to 12-bit codes → each plane coded as a grey JPEG XL (`cjxl`, modular `-m 1`) → one byte string = `NR` header + 4 payloads | `methods/raw_planes.py` (table, `code_planes`, `assemble`), `common.py` (`sqrt_lut`, `Header`) |
| IMX708 frame: 4608×2592, 10-bit Bayer in the DNG, read with tifffile; DNG ~24 MB | `common.py` `Raw.bits` comment; `docs/SPEC_pool_codec_test.md` §5.5 |
| Field crop 1600×900 at ~50 kB on a Pi Zero 2 W: **D2 modular e5 = 6.4 s, 31 MB**; e7 = 8.3 s, same quality; VarDCT e3 = 1.2 s, 25 MB, 41 kB, worse colour | REPORT "D2 on the IMX708 field crop" |
| Colour at that point (stress ΔE00 / block ΔE00): **D2 0.11 / 0.38** vs today's deployed resized JPEG **0.50 / 1.04** | same table |
| Per frame set (results.csv, `*_field`, 50 kB): D2 stress 0.11–0.23, block 0.38–0.51; hydrium (H) stress 0.13–0.21, block 0.11–0.17; today's JPEG (M1 `field-1000x562`) stress 0.50–0.75, block 0.94–1.04 | `results/results.csv` |
| The 6.4 s is **4 `cjxl` runs (one per 800×450 plane), single-threaded, at a distance picked on the Mac**. No on-device rate search. The distance that hit 50 kB ranged **3.62–4.85** over the 4 frame sets | `phase2/pi_bench.py` `run_planes`; results.csv `knob` |
| Crop sweep at a fixed 50 kB (D2 = modular, **effort 7, encoded on the Mac**): stress ΔE worse-lamp air / water: 1600×900 0.14/0.17 · 2400×1350 0.25/0.30 · 3600×2024 **0.45/0.55** · full frame 0.56*/0.81. AprilTags 4/4 at every size | REPORT "How big a crop fits 50 kB?", `crop_sweep.py` |
| Pi time and memory for crops **above** 1600×900: **not measured**. The full 12 MP in modular e7 does not fit in a 250 MB cap; VarDCT e7 takes 127–142 s | REPORT Phase 2 table |
| hydrium (H, standard JPEG XL VarDCT, our patched BSD-2 C build, linear-light input): **1.3 s** for the IMX708 field crop at 50 kB on the Pi Zero 2 W. Its Pi peak RSS was **not measured**. Memory figures in the REPORT: desk study "~2.2 MB, flat"; OpenMV board heap ~1.3 MB | REPORT "hydrium vs wl53" (cost table, last paragraph), desk table (`REPORT.md:84`) |
| wl53 (our 5/3 wavelet + Rice coder, W): Pi Zero 2 W **0.36 s, 2.7 MB** for the field crop at 50 kB; stress ΔE 0.10 vs D2 0.11 there. Not standard JPEG XL: only our own decoder reads it. Breaks down above 2400×1350 at 50 kB | REPORT wl53 tables (`REPORT.md:111, 122`), crop sweep |
| Raw capture `rpicam-still -n --immediate --raw` (DNG + JPEG) = 5.9 s on nereus002 (a Pi Zero 2 W) | REPORT Phase 2 table; `pi_bench.py --capture` |
| **No real-water data.** "Underwater" = red ×0.14 / blue ×0.8 thinning at capture under LEDs. The pool test is specified (`docs/SPEC_pool_codec_test.md`) but has not been run | REPORT "Not tested / limits" |
| Toolchain of the study: cjxl/djxl libjxl **v0.11.1** on the Mac | `results/versions.json` |

### 0.2 Camera (bm `development` 18ed066)

| fact | source |
|---|---|
| Production still capture = one `rpicam-still -n --timeout 2000 --width 4608 --height 2592 --quality 95 --metadata … -o native.jpg`. The retry ladder drops only `-n`, `--metadata` and the camera controls, and accepts on rc 0 plus a non-empty JPEG. There are 4 attempts, each with a 30 s watchdog and a 60 s delay between attempts, and each failure sends WS `cap_rc` / `cap_timeout` / `retry` | `BM_Devel_Pi/rc_capture.py:174-180, 588-800` |
| Then: in-process crop + lanczos to `still.output_width` (prep ~2.4 s) → progressive-JPEG quality ladder against the message cap (encode ≤ 0.06 s) → transmit | `rc_progressive_jpeg.py:1-45`; Sprint08 spec line 132 |
| Capture 4.8–5.3 s (incl. the 2 s AE timeout). **CMA is the binding constraint: CmaFree bottoms at 1.9 MB during the native capture with `cma=128M`**. "Never run concurrent CMA users." Peak RSS of the encode ~123 MB; MemAvailable ≥ 180 MB. Measured on **bmcam000, Bullseye, `libcamera-still`**, 2026-07-24. Not re-measured on trixie / rpicam on 003/004 | `sprints/Sprint07_pi_jpeg_validation.md:58, 99, 146-154` |
| `still.crop` default `[1504, 846, 1600, 900]` (native px), `still.output_width` 1000 (→ 1000×562), `still.quality_ladder` [15,13,11,9], `still.message_cap` 195 (range 1..500), `still.budget_min` 18 (1..30) | `config_registry.py:261-286` |
| `REGISTRY_VERSION = 7` | `config_registry.py:39` |
| **Chunk size:** registry default `uplink.chunk_chars` 300 (= 225 raw B). **The deployed units run 384 chars = 288 raw B** (bmcam003/004 pulled configs, bmcam001 profile). This spec computes with **288 B** and shows 225 B where it matters | `config_registry.py:362`; `runs/s4a_soak_20260928/pulled/bmcam00{3,4}_camera_config.yaml:115`; `device_profiles/bmcam001/camera_schedule.yaml:146` |
| Pacing 1.3 s/msg (Sprint25, decided for bmcam003/004); bmcam001 runs 1.0 s | `device_profiles/bmcam003/camera_schedule.yaml:145`; memory note Sprint25 |
| Wake timing on the bench, **video cycles** (record + fit, not stills): bus on → START at **+57..+66 s**, bus on for 592 s in that run. Production bus: 10 min/hour; listen tail 150 s; halt margin 30 s (ASSUMPTION in Sprint26). **No still-cycle START time is recorded**; R0/R4 measure it | `sprints/Sprint25_transmit_timing_resend/RESULTS.md:18-23`; `sprints/Sprint26_bus_window_transmit/SPEC.md` §3-4 |
| **No bus deadline in the code:** the Sprint26 §5 deadline design was not built. The cycle budget is `still.budget_min × 60` from process start (**8 min = 480 s** on bmcam003/004), and the process starts at ~+21 s. Halt before bus off is measured, never enforced | `runs/s4a_soak_20260928/pulled/bmcam003_camera_config.yaml:73`; Sprint26 SPEC §3, §5 |
| Heal slot: up to 40 heal chunks go out **before** START on a wake that has heals (≈ 52 s at 1.3 s/msg) | `rc_heal.py:53`; Sprint26 SPEC §2 |
| `uplink.network_type` 1 (Iridium fallback) is allowed for stills; the bench units run 2 (cellular only) | `config_registry.py:359-361`; pulled config `:114` |
| Heal: ≤ 40 chunks per wake (camera) and per command (backend), 8 heals per command, heal cap 24/day per Spotter (production) | `rc_heal.py:53`; nvd `heal_commands.py:43-44`; RELEASE_PLAN §2a |
| START: `fmt=` is a core RC field (never dropped), START payload budget 285 B; video already sends `fmt=h264` | `rc_uplink_messages.py:15-30, 60-70, 110` |
| rpicam `--metadata` carries `ColourGains` [2], `ColourCorrectionMatrix` [9], `SensorBlackLevels` [4096×4] (16-bit scale = 64 at 10 bits), `ScalerCrop`, `SensorTemperature` | `runs/sprint10_phaseB_20260727/*capture_metadata.json` |
| Units run Debian 13 trixie (bmcam003 measured); boot = kernel 5.9 s + userspace 29.8 s | `TODO.md:346, 518` |
| `cjxl` / `numpy` on bmcam003/004: **unknown** (nothing in the deploy scripts installs them; `deploy_rc_runtime.sh:124` checks only PyYAML) | `tools/deploy_rc_runtime.sh` |

### 0.3 Backend (nvd `origin/staging` 108d1e7)

| fact | source |
|---|---|
| START `fmt` is a loose key. Video iff `fmt ∈ {"h264"}`. Everything else is an image. Content type, extension and `MediaFormat` follow the **sniffed** bytes (JPEG, PNG, HEIC, Annex-B), not `fmt` | `services/bm_image_parser.py:23, 28-29, 315, 340-342`; `services/poll_once_ingest.py:503, 535-580` |
| Unknown fmt today: not flagged (`format_disagreement` returns `[]`, contrary to the wire contract). `MediaFormat` follows the filename extension: **jpeg** for a `.jpg` name, **heic** as the last fallback. `normalize_filename` rewrites any extension outside `ALLOWED_EXTS` to `.jpg` | `bm_image_parser.py:201-202, 361-363`; `poll_once_ingest.py:535-547`; `backend/docs/bm_media_wire_contract.md:44-45` |
| New fmt values are added to `backend/docs/bm_media_wire_contract.md` first | contract `:35-45` |
| Gallery "renderable" for images = complete **or** has a `display_key`, served as `display_key or r2_key`. Videos need a `display_key`. Processing enqueues complete images without checking `display_key`; inference falls back to `r2_key` | nvd `main.py:150-158, 1000-1007`; `processing/enqueue.py:25-37`; `services/inference/worker.py:89-92` |
| Keyed grouping (`<I{key}.{n}/{M}>`) only runs with `BM_KEYED_GROUPING` on (code default off; the staging value is not verified here). Heal needs keyed media | `bm_image_parser.py:641-650`; `heal_commands.py:159-217` |
| Partial images: the decodable prefix (chunks 0..first gap) is rendered as a truncated JPEG preview | `bm_image_parser.py:465-472`; `poll_once_ingest.py:1324-1337` |
| Storage: R2 original `{device}/bm_sofar/{Y/m/d}/…{sha16}.{ext}`, display JPEG q90 `{parent}/display/{stem}.jpg`, variants `{parent}/variants/{stem}__{proc}.jpg` | `poll_once_ingest.py:583-595`; `image_derivatives.py:36-43, 198, 247-258` |
| `media` has no width / height / mime columns. `MediaFormat` = jpeg, heic, mp4, h264 (h264 added by `ALTER TYPE … ADD VALUE`, migration 0009). Highest migration 0019 | `models.py:32-41, 100-198`; `alembic/versions/20261002_0019_*` |
| Processing (`cheeca_v3` GRVI) reads `media.r2_key` (the **original**), so an undecodable original fails processing | `processing/worker.py:364-391`; `processing/enqueue.py:25-37` |
| Reusable colour code: `srgb_to_linear`, `solve_white_patch`, `solve_gray_balance`, `solve_ccm3x3`, ΔE2000, `sample_patches`; card / AprilTag detection `bm_reference_card_quality_v2.detect_tags` | `processing/vendor/reference_card_color_utils.py:50-386`; `…/bm_reference_card_quality_v2.py:104` |
| Gallery shows `display_key or r2_key`. It branches only on `type == video`, never on format | `main.py:150-158, 1001-1007`; `dashboard/gallery.html` |
| Runtime: Render native Python 3.13.5 on Debian 12, 512 MB, no Dockerfile / render.yaml / Aptfile in the repo. Deps include Pillow, pillow-heif, numpy, opencv-headless, scipy. **No JPEG XL decoder** | `backend/requirements.txt`; contract `:197-202` |
| Per-Spotter settings live on `external_gateways` (`self_heal`, `remote_commands`, `link`, `heal_cap_per_day`). A new one = migration + model + `rollout.apply_changes` allow-list + `view()` | `models.py:761-815`; `services/rollout.py:68-159`; `admin_rollout.py:43-54` |

### 0.4 Corrections to the brief

1. **225 B per chunk is the code default; the deployed units send 288 B per chunk** (§0.2). At 288 B,
   50 kB = 174 chunks, not 223.
2. **The 1.3 MB hydrium figure is the OpenMV heap.** On the Pi only the time (1.3 s) is reported.
3. **The 6.4 s D2 time uses a fixed distance chosen offline.** Hitting a byte target on the unit needs
   a distance ladder or search, so time = 6.4 s × attempts (§3.5).
4. **Crop-sweep quality beyond 1600×900 was measured with e7 on the Mac.** Zero time/RSS there is unknown.
5. **The only measured bench wake timings are from video cycles**, and the binding limit on a still wake
   today is the 8-min cycle budget, not the 10-min bus window (§0.2).

---

## 1. Goal and question

**Goal (MVP now):** a bm still unit can send a RAW-plane JPEG XL image (`fmt=nrjxl`) of a native-resolution
crop instead of today's 1000×562 progressive JPEG. It uses the same per-wake message budget. The backend
stores the original and renders a viewable image. Any failure on the unit falls back to today's JPEG
in the same wake.

**Question the sprint answers:** on real hardware, over the real link, does `nrjxl` deliver as reliably
as `pjpg` (D1: 100 % complete ≤ 3 h, 0 redundant heals) and fit the production wake? And does it keep
the study's colour gain on outdoor frames (≥ 30 % lower card ΔE00 than today's JPEG at the same bytes)?

**Out of the question:** underwater colour. There is no real-water data. The rig's pool test answers it,
and this sprint does not block on it.

## 2. Shape of the solution

```text
UNIT (per_boot wake, bus 10 min)
 rpicam-still … [+ --raw iff nrjxl] ─▶ native.jpg (today) + native.dng (new, ~24 MB, SD)
   │                         │
   │  [A] today's path        │  [B] new, only if still.format = nrjxl
   ▼                         ▼
 crop+lanczos → pjpg ladder   DNG crop read (still.crop) → 4 planes → sqrt LUT 12-bit
   = fallback payload ready   → cjxl -m 1 -e 5 per plane, distance rungs, RLIMIT + timeout
                              → NR container (header + crc32 + capture colour metadata)
 send [B] if it fits the message budget, else [A]  (START fmt=nrjxl | fmt=pjpg rfb=<why>)
 keyed chunks <I{key}.{n}/{M}> → Spotter → Sofar
BACKEND (Render cron, every 5 min)
 parse START fmt=nrjxl → keyed reassembly → heal missing (unchanged, format-agnostic)
 complete → R2 original .nrjxl → render: NR unpack → crc → JXL decode ×4 → inverse sqrt → Bayer
   → demosaic → camera WB + CCM (from the header) → sRGB → display JPEG q90 → gallery
 (stretch) card-corrected variant on LINEAR data → variants/{stem}__raw_card_v1.jpg
```

The JPEG is built first because it is known-good and cheap (~2.5 s). The RAW attempt then runs under a
hard time and memory cap. The wake always has a sendable payload.

---

## 3. Camera

### 3.1 Capture path (MVP now)

- **Production path, one capture:** the existing `_run_native_full_capture` command plus `--raw`, which
  writes `native.dng` next to `native.jpg` from the **same exposure**. **Only when
  `still.format = nrjxl`**: a pjpg unit runs today's exact command and code path. Picamera2 is rejected for
  the MVP: it is a different capture path with different buffers (CLAUDE.md "Camera path matters").
- **Fallback, MVP now. The existing retry ladder must NOT carry `--raw`.** Every rung of that
  ladder keeps every other argument, accepts on rc 0 plus a JPEG, and never checks for a DNG. A
  hanging `--raw` would cost 4 × (30 s watchdog) + 3 × 60 s ≈ 300 s, could end with no JPEG, and
  would send WS capture-failure messages (§0.2). So:
  1. **One RAW attempt:** the production command + `--raw`, one 30 s watchdog, no retries, no WS
     status on failure (a log line only).
  2. On a non-zero rc, a timeout, a missing / empty JPEG **or** a missing / empty DNG: delete the
     partial files and call the **unchanged pre-Sprint28 `_run_native_full_capture`** at once. That
     is today's command, ladder and WS behaviour. Path [A] continues with `rfb=cap`.
  3. If the RAW attempt gave a JPEG but no DNG, the JPEG is still used (no second capture). This
     saves ~5 s and a CMA cycle.

  Worst case: a hanging `--raw` costs 30 s before today's capture starts. Desk tests cover a
  hanging, failing and DNG-less `--raw` with a fake runner. **A RAW problem can never cost the JPEG.**
- **CMA risk (main hardware unknown):** the native capture already left only 1.9 MB CmaFree in
  Sprint07 (bmcam000, Bullseye, `libcamera-still`; not re-measured on trixie / rpicam). Whether
  `--raw` needs more CMA on bmcam003/004 is **unknown**. nereus002 captured with `--raw` fine, but
  its `cma=` is not recorded. **R0 measures CmaFree min with and without `--raw`, with the Sprint07
  sampling method, before any code ships to a unit.** If `--raw` fails at `cma=128M`, the options in
  order of risk:
  1. `--buffer-count 1` (rpicam option; effect on CMA unmeasured);
  2. raise `cma=` (a `/boot` change: backup + restore command, Nick's OK via the EM);
  3. stop and re-plan. Decision at the S3 gate.
- **DNG handling:** ~24 MB per wake written to the SD, deleted after the encode (≈ 0.6 GB/day of
  writes at 24 wakes, ESTIMATE). Not `/dev/shm`: it is RAM on a 415 MB board, and the RemoveIPC
  wipe applies there (`runs/render_dir_vanish_20261001/`). `still.raw.keep_crop` (§3.7) keeps only
  the crop (2.9 MB at 1600×900) for the outdoor test.
- **Orphan sweep:** a crash or power cut mid-encode leaves a 24 MB DNG. Every still action deletes
  `*.dng` / `*.pgm` work files older than the current action from the capture dir before it
  captures, logged with a count. The stills storage guard then sees the true free space.
- **Time:** the DNG write adds an unmeasured amount to today's 4.8–5.3 s capture. The study's 5.9 s
  was on a different unit and command, so it is not a delta. R0 measures it.

### 3.2 DNG crop reader (MVP now)

- A small reader in a new `BM_Devel_Pi/rc_raw_jxl.py`: parse the TIFF IFDs, find the full-resolution CFA image
  (PhotometricInterpretation 32803; IFD0 or a SubIFD), read only the crop rows by seek. That is
  1600×900×2 B = 2.9 MB, not 24 MB.
- Read `CFAPattern`, `BlackLevel`, `WhiteLevel` and `BitsPerSample` from the tags. **Fail loudly**
  on compressed data, a LinearizationTable, a non-2×2 or non-RGB CFA, a per-position black
  level, or an `ActiveArea` with an odd origin. These are the rig reader's refusals
  (`raw_io.read_dng`, `:242`). An even `ActiveArea` offsets the crop, and the reader applies it.
- Expected values for the IMX708: black 64, white 1023 at 10 bits. These are an ASSUMPTION from
  `SensorBlackLevels` 4096 at 16-bit scale and the study's `bits=10`. The reader takes them from
  the tags, never from constants.
- Parity test: the reader and tifffile return byte-identical crops on the study DNGs (on the Mac).

### 3.3 ROI / crop (MVP now)

- The RAW crop **is `still.crop`**, in native sensor px: one ROI for both paths, so the fallback
  shows the same scene. RAW sends the crop at **sensor resolution, not resized**. Each plane is
  w/2 × h/2.
- With `still.format = nrjxl`, a new camera rule `_rule_raw_crop` requires x, y, w, h to be even
  (this keeps the CFA phase; the registry accepts odd values today) and w × h ≤ `RAW_MAX_PX`.
- **Default RAW crop = 2400×1350** (Nick, 2026-10-02, §10 Q1), conditional on R0. The step-down
  order is 2400×1350 → 2000×1124 → 1600×900. The default is the **largest crop whose R0 rows
  PASS** (time, RSS, CMA and the 8-min budget, §7.2 R0.3). 1600×900 stays as the safe preset.
- Even, near-centred native coordinates (a centred 2400×1350 would have an odd y = 621):

  | preset | `still.crop` [x, y, w, h] | note |
  |---|---|---|
  | 2400x1350 raw default | `[1104, 620, 2400, 1350]` | new; 1 px above centre |
  | 2000x1124 raw step-down | `[1304, 734, 2000, 1124]` | new; centred |
  | 1600x900 safe | `[1504, 846, 1600, 900]` | today's default |
- `RAW_MAX_PX` = the area of the crop R0 proves. Until then the rule allows 1600×900 only, so no
  unit can run an unmeasured crop.
- **How the default applies:** the registry default of `still.crop` stays `[1504, 846, 1600, 900]`, so
  pjpg units keep today's field of view (CLAUDE.md §7). Switching a unit to nrjxl is **one command**
  that sets `still.format=nrjxl` and `still.crop` = the R0-proven raw preset together (~105 B, inside
  the 234 B wire limit). The UI offers that pair as the nrjxl choice. Its pjpg fallback then shows
  the same wider scene, resized to 1000 px wide (`still.output_width`).
- Two existing `still.crop` presets have an **odd y** and would be refused with nrjxl:
  "1000x562 max detail" `[1804, 1015, …]` and "800x450 reef A" `[1904, 1071, …]`
  (`config_registry.py:268-269`). S1 moves each y down by 1 to 1014 / 1070. That is a 1-px shift
  for pjpg users of those presets, stated in the PR.
- Coordinate systems (CLAUDE.md §12):
  - `still.crop` and the header `crop_x/crop_y` are **native sensor px**.
  - Plane px = crop px / 2.
  - The backend render is **crop px** (demosaiced back to w × h).
  - Today's JPEG is **output px** (1000×562, lanczos from the same crop). Any side-by-side must say
    which one is resampled for display.

### 3.4 Plane split and curve (MVP now)

- Split R, G1, G2, B by the CFA (study `split`).
- Apply the integer LUT `floor(sqrt(clip(v − black)) · S + 0.5)`, with `S = 4095 / sqrt(white − black)`,
  giving 12-bit codes (study `sqrt_lut`, `b=12`, pedestal 0). It is built once per black/white
  pair, and a port reproduces it exactly.
- Each plane is written as a 16-bit PGM with maxval 4095 (study `write_pgm`); cjxl reads that as
  12-bit grey.
- Time: 0.09 s split + 0.4 s LUT for 12 MP (REPORT). A 1600×900 crop is 1/8.3 of that → ~0.06 s
  (ESTIMATE). Needs numpy (R0 checks; if it is missing, add `python3-numpy` to the deploy
  dependency check, as for PyYAML).

### 3.5 Encoder (MVP now: cjxl; hydrium = Next sprint unless R0 forces it)

| | cjxl modular e5 (D2) | hydrium (H) |
|---|---|---|
| colour at 1600×900, 50 kB (stress / block ΔE00) | 0.11–0.23 / 0.38–0.51 | 0.13–0.21 / 0.11–0.17 |
| Pi Zero time at 1600×900 | **6.4 s** (4 runs, fixed distance) | **1.3 s** |
| Pi Zero peak RSS | **31 MB** | not reported |
| larger crops on the Zero | unmeasured; full frame does not fit 250 MB | unmeasured |
| where it comes from | distro `libjxl-tools` (trixie version: ASSUMPTION 0.11.x, R0 checks) | our patched BSD-2 C (rig `methods/hydrium/`, `hyd.c`), compiled on the unit |
| output | standard JPEG XL | standard JPEG XL |

**Choice:** cjxl. It has the best measured colour, the only measured Pi RSS, and no C build in
the deploy. hydrium is a strong fast path (5× faster, better block colour), but it adds a vendored
C build to the unit deploy. It joins only if R0 shows cjxl over the time cap at the chosen crop.

**wl53 is rejected for the backend link, although it is fastest** (0.36 s, 2.7 MB, the same colour at
1600×900):
- its bytes are readable only by our own decoder, so there is no independent decoder for the §4.10
  cross-check and no stock tool for customers;
- it breaks down above 2400×1350 at 50 kB (§0.1).

It stays the study's OpenMV fallback.

**Rate control:** a **distance ladder**, as the JPEG quality ladder works today:
- `still.raw.distances`, ≤ 4 rungs, low → high. The defaults come from the S0 Mac calibration on the
  study frames for the default crop. The 50 kB distance spread was 3.62–4.85.
- Encode all 4 planes at rung 1. If the container fits `budget_bytes`, stop. Else try the next rung.
  `budget_bytes = (budget_chunks) × uplink.chunk_chars × 3 / 4`; that is 288 B per chunk on the
  deployed units, derived, never hard-coded. `budget_chunks` is the same message budget the pjpg
  selector uses: `still.message_cap` and the cycle budget's `messages_fit`, with START/END
  reserved.
- Each rung ≈ 6.4 s at 1600×900, so 3 rungs ≈ 19 s. `att=` records the attempts.
- An optional speed-up, decided in S1 and not required: estimate from the first plane's size and skip
  rungs.

**Guards:**
- Every `cjxl` runs as `cjxl in.pgm out.jxl -m 1 -e <effort> -d <distance> --num_threads=0`.
  `--num_threads=0` is the measured setting (`pi_bench.py:196-197`). The default thread pool
  changes time and RSS, and on 64-bit glibc its per-thread arenas could hit `RLIMIT_AS` and
  cause false `rfb=mem` results. Encoding the 4 planes in parallel processes is an unmeasured
  option (S0/R0 may test it; not MVP).
- Each child gets `RLIMIT_AS` 250 MB and `oom_score_adj` 1000, as in the study's `pi_bench.child`.
  An overrun kills the encoder, never the supervisor.
- A wall-clock cap of `still.raw.encode_max_s` per image (default 30 s) applies on top of the existing
  `CycleBudget` check.
- No concurrent CMA user: the encode starts after `rpicam-still` has exited.

### 3.6 Wire and container (MVP now)

**START:** `fmt=nrjxl` (new constant beside `pjpg` / `h264`), `q=<distance × 100, int>`, `att`, `cmp=1`.
On a fallback, the pjpg START also carries `rfb=<code>` as a core field. The codes:

| code | meaning |
|---|---|
| `cap` | `--raw` capture failed or no DNG |
| `dng` | the reader refused the DNG |
| `enc` | cjxl exit ≠ 0 or missing |
| `mem` | killed (RLIMIT / OOM) |
| `time` | encode cap reached |
| `fit` | no rung fits the budget |
| `err` | anything else in path [B] (catch-all, §6) |

With `still.format = pjpg`, the unit behaves as today, but the bytes are not identical. The v8 keys
change the config hash, so `cfg=` / `h=` change in every trace. This is the F-G3-4 precedent
(`tests/golden/README.md:181`). The 23 golden traces are re-recorded. The gate: they differ **only**
in `cfg=` / `h=`, checked byte-for-byte with the hashes masked. Deploy then needs `/refresh`, as after
F-G3-4.

**Filename on the wire:** nrjxl stills are named `<timestamp>_image.nrjxl`, not `_image.jpg`. The
backend's extension witness and `_media_format_for_image` read the extension (§0.3); a `.jpg` name
would be flagged by `format_disagreement` and stored as jpeg. `.nrjxl` goes into the contract table
and `ALLOWED_EXTS` (§4).

**No bounded partial `nrjxl` send in the MVP.** A prefix of 4 concatenated planes renders nothing. If no
rung fits, the wake sends the JPEG (`rfb=fit`). So `nrjxl` is always `cmp=1`.

**Container = the study's `NR` header, unchanged**, so the rig's `raw_planes.decode` decodes production
bytes as an independent check:
- `N` `R`, method id 14 (D2), flags `0x04` (4pl, sqrt), CFA index, `b=12`;
- varints w, h, black, white, pedestal 0, the param count, the params (zigzag), the length count, 4 lengths;
- then the R, G1, G2, B JXL streams.

The production fields ride in the existing `params` list, which the study decoder ignores:

| idx | param | unit |
|---|---|---|
| 0 | profile version = 1 | — |
| 1–2 | crop_x, crop_y | native px |
| 3–4 | native_w, native_h | native px |
| 5 | crc32 of the 4 payloads | uint32 |
| 6 | exposure | µs |
| 7 | analogue gain | ×1000 |
| 8–9 | ColourGains R, B | ×10000 |
| 10–18 | ColourCorrectionMatrix (row-major) | ×10000 |
| 19 | sensor temperature | ×10 °C |
| 20 | distance | ×100 |
| 21 | effort | — |

The header is ~80 B (ESTIMATE), < 0.2 % of 50 kB. The keyed sent record keeps the sha256 of the whole blob
(`rc_media_key.write_sent_record`).

### 3.7 Config keys (registry v8; catalog → nvd vendor → UI)

| key | type / range | default | tier (Sprint27) | why |
|---|---|---|---|---|
| `still.format` | ENUM `pjpg` / `nrjxl` | `pjpg` | **control** | the switch; per unit, from the UI |
| `still.raw.distances` | LADDER, ≤ 4 floats in 0.1..15 | from S0 | **control** | rate rungs; the bench forces fallbacks with it |
| `still.raw.encode_max_s` | INT 5..120 | 30 | **control** | per-image time cap; R3 forces `rfb=time` with it |
| `still.raw.keep_crop` | BOOL | false | **control** | keep the raw crop (2.9 MB as PGM) + the pjpg built that wake for paired analysis (storage guard applies) |
| `still.raw.effort` | INT 1..7 | 5 | engineering (read-only) | 5 is the measured setting on the Zero; not a bench knob |

Why control and not engineering:
- Sprint27 engineering keys are read-only, and the backend refuses a `set` of them as `not_writable`
  (Sprint27 SPEC §2.1, §2.3).
- `hil_change.sh` goes through that backend plan. The ladder's forced fallbacks (R3), its restore
  and O1's `keep_crop` need these keys writable.
- Each has a registry range, and per Sprint27 §9.6 the range is the limit everywhere.

Camera rules (in `config_validate`, same scope as the Sprint27 rules):
- `_rule_raw_crop`: even crop, ≤ `RAW_MAX_PX`.
- `_rule_raw_keyed`: `still.format = nrjxl` needs `uplink.media_key.enabled` (heal needs keyed media).
- `_rule_raw_cellular`: `still.format = nrjxl` needs `uplink.network_type = 2`. Iridium is refused
  because a lost chunk there leaves no image at all.

Backend: the device view warns when `still.format = nrjxl` is reported, or a change sets it, while
the Spotter's `self_heal` is off or its `link` is not cellular (`rollout.gateway_allows`). The
reason: an nrjxl with a missing chunk renders **nothing**, while a pjpg still shows a prefix
preview. Healing is the only recovery.

Then:
- regenerate `docs/bmcam_config_catalog.json` and vendor it into nvd;
- the parity tests run as in Sprint27 §3.2.

ASSUMPTION to confirm with the UI session: the G2 UI renders control keys from the catalog, so a new
ENUM needs no frontend code.

**Rollout:** per unit through remote config (`set still.format=nrjxl`). There is no new per-Spotter
column in the MVP. The backend always accepts `nrjxl`, and receiving it changes nothing for other units.

### 3.8 Wake budget (production 10-min bus, 8-min cycle budget, 1.3 s/msg, 288 B/chunk)

There is no still-cycle timing on 003/004. The pjpg column is built from Sprint07 / 08 component
times plus the video-cycle boot marks; everything in the nrjxl column is an ESTIMATE until R0 / R4.

| segment | pjpg (ESTIMATE from parts) | nrjxl at 1600×900 (ESTIMATE) | source |
|---|---|---|---|
| bus on → `main()` | ~+21 s | same | Sprint26 §3 (video cycles) |
| Spotter UTC read → capture | ~+23..+30 s | same | Sprint26 §3 |
| capture | 4.8–5.3 s | + DNG write (unmeasured, R0) | Sprint07 (bmcam000) |
| JPEG prep + ladder | ~2.5 s | same (fallback is built first) | Sprint08 |
| DNG crop + split + LUT | — | < 1 s | §3.2-3.4 |
| cjxl | — | 6.4 s × rungs (1–3) | REPORT |
| heal slot (wakes with heals only) | ≤ 40 chunks ≈ 52 s | same | `rc_heal.py:53` |
| START (no heals) | ~+40..+45 s | **~+50..+70 s** | — |
| burst, 50 kB | — | 176 msgs = 229 s | arithmetic |
| END → tail 150 s → halt | — | ~+430..+450 s | — |

The binding limits are both checked; the tighter one wins:
- **Cycle budget:** 480 s from process start (≈ +21 s) → ends ≈ +501 s. Whether the 150 s tail is
  reserved inside it is not verified (ASSUMPTION: it is). From START at +70 s: (501 − 70 − 150) / 1.3 − 2
  ≈ **214 chunks ≈ 62 kB**.
- **Bus window:** off at +600 s, halt by +570 s. From +70 s: (570 − 150 − 70) / 1.3 − 2 ≈ 267 chunks.
  Not binding while `budget_min` = 8.
- **Wake with a heal slot (−52 s):** ≈ **174 chunks ≈ 50 kB** under the 8-min budget. **50 kB is the
  largest size that fits every wake.** On heal wakes the encoder's rung walk sees a smaller
  `budget_chunks` and steps down, or falls back with `rfb=fit`.
- **:05 boundary:** a 229 s burst from START +50..+70 s ends at ~+280..+300 s. That is at or past the
  :05 grid boundary when the bus is anchored at :00. Sprint26 §4 point 2: stills lose chunks at
  that boundary (no keyframe repeat), and the lane planner (`uplink.lane.*`) decides. Lost chunks
  become heals: they count against the 24/day cap and are measured in R4 / O1 (heals per image).

---

## 4. Backend (nvd, PR into `staging`)

1. **Wire contract first:** add `fmt=nrjxl`, the `.nrjxl` filename extension and the `rfb` codes to
   `backend/docs/bm_media_wire_contract.md`.
2. **Parser:**
   - add `nrjxl` to `_FORMAT_FAMILY`;
   - `sniff_format` recognises `b"NR"` + method byte 14 (or 19 = hydrium, Next sprint) → `nrjxl`;
   - an `nrjxl` START whose bytes do not sniff as NR is flagged by `format_disagreement`.
3. **Model:** `MediaFormat` gains `nrjxl`. Migration 0020 is a single `ALTER TYPE … ADD VALUE`, the
   0009 pattern (DDL only, per the migrations rule).
4. **Ingest:**
   - content type `application/x-nereus-nrjxl`, extension `.nrjxl` (into `ALLOWED_EXTS`, so
     `normalize_filename` keeps it);
   - **no prefix preview** for nrjxl (the truncated-JPEG preview would fail or mislead): `percent_received` only;
   - keyed reassembly and heal are unchanged (format-agnostic, §0.3).
5. **Render** (new pure module `services/raw_render.py`, called where the display derivative is made today):
   1. `NR` unpack;
   2. crc32 check;
   3. decode 4 JXL planes to uint16 codes;
   4. inverse sqrt (float32);
   5. merge Bayer;
   6. normalise black/white;
   7. demosaic (bilinear as in the study, or OpenCV EA; S2 picks one with a test);
   8. **camera** WB (`ColourGains`) and CCM from the header;
   9. clip, sRGB OETF, 8-bit;
   10. display JPEG q90 at `display_key` (existing path), with `render_version` in the media metadata.

   The original `.nrjxl` stays at `r2_key`: the source of truth, so it can be re-rendered later
   (Sprint21: correct once, from raw).

   Memory: ~17 MB of float32 RGB at 1600×900, ~87 MB at 3600×2024. That is inside 512 MB (ESTIMATE).
6. **Decoder on Render:**
   - No Dockerfile or Aptfile, so `djxl` cannot be apt-installed. Use a pip wheel that bundles libjxl.
   - First candidate: `imagecodecs` (BSD-3) `jpegxl_decode`.
   - ASSUMPTION: its manylinux wheel ships libjxl and returns uint16 for 12-bit grey. S0 verifies this
     on the Mac and in a Render-like Debian 12 container (local docker; no Render change).
   - Fallback candidate: `pillow-jxl-plugin`. The choice is recorded at the S0 gate.
7. **Processing and inference: never the raw original.**
   - Processing: for `format = nrjxl`, the worker reads `display_key`, not `r2_key`, and
     `eligible_media_filter` excludes nrjxl rows without a `display_key`. Otherwise a failed render
     would burn 3 failed jobs, the case the contract already records.
   - Inference: drop the `r2_key` fallback for nrjxl.
   - Tests for both rules.
8. **Gallery:**
   - **Renderable rule:** nrjxl is treated like video, `renderable = bool(display_key)`. Today an image
     is renderable when it is complete, and is served `display_key or r2_key`. A complete nrjxl
     without a render (render failure, or the kill switch on) would serve the `.nrjxl` bytes as an
     `<img>`. With the rule it shows "no preview" plus the download link.
   - Otherwise it shows `image_url` as today.
   - Small additions: a "RAW·JXL" badge and a "download original" link (presigned `r2_key`).
   - The UI session reviews the change.
9. **MVP stretch:** the card-corrected variant `raw_card_v1`:
   - detect the card / AprilTags on the neutral render;
   - sample the patches on the **linear** camera RGB;
   - solve WB with `solve_gray_balance` and the CCM with `solve_ccm3x3`;
   - render to `variants/{stem}__raw_card_v1.jpg`.

   This is the study's "card white balance" step on the server. If it slips, the outdoor colour metric
   is still scored on the Mac (§7.3).
10. **Independent decoder test (CI / Mac, not on Render):**
    - decode every plane of every fixture with **jxl-oxide** (Rust; licence MIT / Apache-2.0 per its
      repo, to confirm) and with the production decoder;
    - for modular streams, expect **bit-identical** codes. This is an ASSUMPTION: modular decoding is
      integer arithmetic. If it is not bit-identical, record the max |Δ| and fail above 1 code;
    - also run the rig's `raw_planes.decode` on the same blobs. The mosaic must equal the production
      decode's mosaic.

Kill switch: env `BM_NRJXL_RENDER=0` skips the render. The original is still stored, and through the
item 8 rule the row shows "no preview". Global env stays kill-switch only (RELEASE_PLAN rollout ruling).

---

## 5. Link budget (288 B/chunk, 1.3 s/msg; START + END = 2 msgs)

### 5.1 Bytes → messages → minutes

Fit columns use §3.8: ≤ 214 chunks on a wake without heals, ≤ 174 with a full heal slot, under
today's 8-min cycle budget.

| payload | chunks | burst | fits, no heals | fits, heal wake | at 225 B/chunk |
|---|---|---|---|---|---|
| 44 kB | 153 | 202 s | yes | yes | 196 chunks |
| **50 kB (study budget)** | **174** | **229 s** | **yes** | **yes (limit)** | 223 |
| 56 kB (= today's 195-chunk cap) | 195 | 256 s | yes | no | 249 |
| 62 kB | 214 | 281 s | limit | no | 276 |
| 75 kB | 261 | 342 s | no at 8 min (yes at `budget_min` ≥ 9) | no | 334 |
| 112.5 kB (2400×1350 at 0.278 bpp) | 391 | 511 s | no | no | 500 |
| 144 kB (registry max 500) | 500 | 653 s | no: longer than the whole window | no | 640 |

Anything above 56 kB also needs `still.message_cap` > 195 (and above 300 the catalog warns). That cap
is shared with the pjpg fallback, so raising it lets the fallback JPEG grow too. Say so in any change
that raises it.

### 5.2 Crop vs bytes

Measured quality at a **fixed 50 kB** is in §0.1. Bytes to **hold** the 1600×900 quality (0.278
bits per sensor px) are an ASSUMPTION: constant bpp ≈ constant quality, which is unmeasured above
1600×900. S0 measures the crop × bytes grid on the Mac.

| crop | area × today | 50 kB (stress ΔE air/water, measured) | bytes at 0.278 bpp | chunks | wakes |
|---|---|---|---|---|---|
| 1600×900 | 1× | 0.14 / 0.17 | 50 kB | 174 | 1 |
| 2000×1124 | 1.6× | 0.17 / 0.20 | 78 kB | 272 | 2 |
| 2400×1350 | 2.25× | 0.25 / 0.30 | 112.5 kB | 391 | 2 |
| 3600×2024 | 5× | 0.45 / 0.55 | 253 kB | 879 | 5 |
| 4608×2592 | 8.3× | 0.56* / 0.81 | 415 kB | 1442 | 7 |

Wakes = ceil(chunks / 214), the no-heal limit under the 8-min budget (§3.8).

Reading: within one wake, the choice is **bigger crop at ≤ 50–62 kB with more colour error** (2400×1350
stays at 0.25–0.30, still half of today's JPEG at 1600×900) or **same crop, better colour**. Nick chose
the bigger crop (Q1): 2400×1350 at the one-wake budget, if R0 proves it.

Budget check for 2400×1350. Time is an ESTIMATE that scales with area, until R0 measures it:
- One rung ≈ 6.4 s × 2.25 ≈ 14 s. With 2 rungs, START ≈ +75..+100 s.
- No-heal wake: (501 − 100 − 150) / 1.3 − 2 ≈ 191 chunks ≈ 55 kB, so 50 kB fits.
- Heal wake (−52 s): ≈ 151 chunks ≈ 43 kB. The rung walk then targets the smaller budget (a higher
  distance, lower quality), or falls back with `rfb=fit`. R4 counts how often.

### 5.3 Nick's transmit window (its own sprint right after Sprint28, Nick 2026-10-02; analysis only)

- **Idea:** replace `*.message_cap` as the user knob with a per-Spotter **transmit window**: minutes, or
  unlimited for field testing.
- **Derivation (window as burst time):** `budget_chunks = floor(window_s / uplink.msg_interval_s) − 2`
  (START + END). It is bounded by the wake itself, which is Sprint26 §5.2's formula,
  `floor((deadline − now − tail − heal_slot) / pacing) − envelope − repeat`. That formula needs a bus
  deadline, which **does not exist in the code today**: the cycle budget is `budget_min × 60` (§0.2).
  So the window knob and the Sprint26 deadline go together.

| window | chunks at 1.3 s | kB at 288 B | kB at 225 B |
|---|---|---|---|
| 3 min | 136 | 39 | 31 |
| 4 min | 182 | 52 | 41 |
| 5 min | 228 | 66 | 51 |
| 6 min | 274 | 79 | 62 |
| 8 min | 367 | 106 | 83 |
| unlimited | bounded by `still.budget_min` (≤ 30 min) and the bus window | — | — |

Shape when built:
- **Backend:** per-Spotter column `tx_window_min` (migration + `rollout.apply_changes`). A change
  becomes a remote-config `set` of a new camera key `uplink.tx_window_s` on each device behind that
  Spotter.
- **Camera:** `budget = min(tx_window_s, bus deadline − now − tail) / pacing`. `message_cap` stays as
  an engineering ceiling.

It touches pjpg and video on the R1 units too, so it is its own change with its own ladder. **Ruled
(Q2): its own sprint right after Sprint28.** Sprint28 keeps `still.message_cap` as the size knob.

### 5.4 One image across several wakes? No (Nick, 2026-10-02)

**Ruling:** no multi-wake images, in Sprint28 or as a planned follow-up. An image should fit in its own
wake. Healing after a **loss** is fine. Never intentionally hold back part of an image, or reserve
messages, for a later wake. So:
- The rung walk targets the current wake's budget. If no rung fits, the wake sends the JPEG (`rfb=fit`).
- There is no `cmp=0` nrjxl send and no camera carry-over. The carry-over idea in r2 is dropped.
- Heals run only for chunks that were sent and lost, as for pjpg today.

Partial rendering of an incomplete `nrjxl` (G1 plane first = grey preview) stays Future. It needs a
plane-order change to the container.

---

## 6. Fallback matrix (unit; every row ends with a delivered image)

| failure | detected by | result | wire |
|---|---|---|---|
| `--raw` capture fails / no DNG | rc ≠ 0, file missing | retry exactly today's command, send pjpg | `fmt=pjpg rfb=cap` |
| DNG layout not supported | reader raises | pjpg | `rfb=dng` |
| cjxl missing / rc ≠ 0 | rc, `shutil.which` | pjpg | `rfb=enc` |
| cjxl killed (RLIMIT / OOM) | signal / rc | pjpg | `rfb=mem` |
| encode over `encode_max_s` or the cycle budget | wall clock | pjpg | `rfb=time` |
| no rung fits the budget | bytes | pjpg | `rfb=fit` |
| bad config (odd crop, too big, keyed off) | `config_validate` at `set` / boot / deploy | `e:xk`, nothing stored | ack |
| anything else in path [B] (numpy missing, ENOSPC / IOError on the DNG or PGM, metadata JSON missing → no WB/CCM params, an unexpected exception) | one `try` around all of path [B] | pjpg | `rfb=err` (the `reason_code` convention, `rc_uplink_messages.py:47-56`) |
| backend cannot render | render raises | original kept, row "no preview" (§4.8 rule), logged, not enqueued for processing | — |

After any failed RAW attempt, the pjpg selector **runs again** against the remaining budget before
sending, because time has passed since the fallback was built. That is the same `select_quality` call
as today.

---

## 7. Test plan

### 7.1 Desk (in the PRs)

bm (`tests/test_s28_*`):
- DNG crop reader == tifffile on the study DNGs (byte-identical; fixture = a small cropped DNG,
  generated and committed with its script);
- LUT == the study `sqrt_lut` for every (black, white) seen;
- container pack == the study `Header.pack`. The **study decoder decodes production blobs**, and
  the params round-trip;
- rung walk + caps with a fake runner (fits at rung 1 / 2 / none, timeout, RLIMIT kill, missing binary);
- the fallback matrix row by row, each ending in a pjpg send;
- START goldens for `nrjxl` and every `rfb`, plus the existing 23 traces re-recorded with `still.format=pjpg`: they differ only in `cfg=` / `h=` (compared with the hashes masked, §3.6);
- capture: a hanging, failing and DNG-less `--raw` each lead to today's unchanged capture call and a pjpg, with no WS capture-failure message for the RAW attempt (§3.1);
- orphan sweep removes old `*.dng` / `*.pgm` work files (§3.1);
- registry v8 / catalog / rule parity tests.

Mac end-to-end: the production module on the study DNGs → blob → rig decoder → stress ΔE within
±0.02 of the study's D2 row at the same bytes.

nvd (scratch Postgres, never staging; never bare pytest):
- sniff / parse / ingest of `nrjxl` with fake Sofar rows (complete, missing chunk → heal plan, unknown fmt);
- render goldens (fixture blobs → mosaic == rig decoder; display JPEG exists);
- jxl-oxide parity (§4.10);
- damaged streams (pool spec §7a.5): 50 truncations + 50 bit-flips per fixture fail loudly (crc or
  decoder error), never a silent image;
- regression: every pjpg / h264 ingest test unchanged;
- migration 0020 up/down on the scratch DB.

### 7.2 HIL ladder (Test Engineer; hil/ format; `runs/s28_ladder_<YYYYMMDD>/`)

The six HIL-ready items (`hil/README.md` §1):

| # | item | value |
|---|---|---|
| 1 | Spec | this §7.2 (moved to `sprints/Sprint28_raw_jxl/LADDER.md` with the code) |
| 2 | Code ref | the merged Sprint28 commit on `development`. Check: `grep -n nrjxl ~/BM_Devel_Pi/rc_uplink_messages.py` and `cjxl --version` on the unit |
| 3 | Criteria | the table below |
| 4 | Inputs | R0: manual `rpicam-still` / `cjxl` over ssh (commands in `LADDER.md`), cron backed up first. R1–R3: console lane via `hil_change.sh`. R4: Sofar lane via `hil_sofar_change.sh`. All `still.*` keys used are control tier (§3.7), so both go through the backend plan. Each kv is written out in the row below |
| 5 | Restore | `{"reset":["still.format","still.raw.distances","still.raw.encode_max_s","still.raw.keep_crop"]}` → `get` hash == the pre-test hash; crontab restored from backup; R0 test files deleted |
| 6 | Budget | R0–R3 ~3 h, 0 cellular. R4 12 h, ~12 × 176 messages (same as pjpg). Nick: none (the bench is already wired) |

Units: R0 runs on **both** bmcam003 (SPOT-33507C) and bmcam004 (SPOT-31593C); each row has a per-unit verdict. R1–R3 run on bmcam003. bmcam004 joins at R4 only if its own R0 rows PASS.

| id | criterion | PASS when | evidence |
|---|---|---|---|
| R0.1 | raw capture works at the unit's CMA | 10/10 `--raw` captures produce DNG + JPEG; no capture error in dmesg; **CmaFree min ≥ 1 MB**, sampled from `/proc/meminfo` every 0.1 s through each capture (the Sprint07 `cma_samples.csv` method); baseline min without `--raw` recorded beside it | `analysis/r0_capture.csv`, `pulled/cma_samples.csv` |
| R0.2 | capture time cost | median(`--raw`) − median(no `--raw`) recorded; ≤ 3 s | `analysis/r0_capture.csv` |
| R0.3 | encode on the unit, per crop: 1600×900 FIRST (gates the feature), then 2000×1124 and 2400×1350 for the opt-in presets | 10 full encodes (4 planes, `--num_threads=0`) from a real DNG crop. **PASS** when: median per rung ≤ 20 s; peak RSS ≤ 120 MB (cap 250 MB); 0 kills; CmaFree as R0.1 (the encode runs after the capture). The predicted wake (measured capture + 2 rungs + 50 kB burst + 150 s tail) must also fit the 480 s cycle budget. The largest PASS sets the default crop and `RAW_MAX_PX` | `analysis/r0_encode.csv`, `analysis/r0_budget.csv` |
| R0.4 | tools present | cjxl version, numpy import, free SD recorded | `snapshots/` |
| R1.1 | console lane, one nrjxl still | START `fmt=nrjxl`; reassembled console bytes sha256 == sent record sha256; rig decoder decodes it | `console/`, `analysis/r1_decode.json` |
| R2.1 | backend | media row `format=nrjxl`, complete, display JPEG present; jxl-oxide == production decode | `api/`, `analysis/r2_parity.json` |
| R3.1 | forced `time` | `set {"still.format":"nrjxl","still.raw.encode_max_s":5}`, then `trg` still. One rung takes ~6.4 s > 5 s → START `fmt=pjpg rfb=time`, and the pjpg arrives complete | `commands.log`, `console/`, `api/` |
| R3.2 | forced `fit` | `set {"still.raw.distances":[0.3],"still.raw.encode_max_s":60}`, then `trg` still. Distance 0.3 is near-lossless, far above the budget (ESTIMATE; S0 confirms its size on the Mac) → `rfb=fit`, and the pjpg arrives complete | same |
| R3.3 | refused config | `set {"still.crop":[1505,846,1600,900]}` with nrjxl on → ack `e:xk`, `get` hash unchanged | `commands.log` |
| R3.4 | other `rfb` codes | `cap`, `dng`, `enc`, `mem`, `err` are **N/A on hardware** (forcing them needs edits on the unit); covered by the §7.1 fake-runner tests | §7.1 test log |
| R4.1 | production wakes | 12/12 wakes deliver an image (nrjxl or pjpg) complete ≤ 3 h, 0 redundant heals | `api/media_*.json` |
| R4.2 | wake fits the window | halt uptime ≤ 570 s on 12/12 | `pulled/` cycle logs |
| R4.3 | fallback rate | ≤ 1/12 wakes fall back | START `rfb` count |
| R4.4 | still START time | uptime at START recorded per wake (no-heal and heal wakes separately); feeds §3.8 r3 | cycle logs |

R0 uses the camera manually: back up crontab, check for running camera processes, restore after
(CLAUDE.md §15). R0 is the only step that can change a unit's `/boot` (only if CMA forces it), and
only with Nick's OK via the EM.

### 7.3 Outdoor test O1 (24 h, both units, the G4/G5 box, card in view)

- **One variable:** `still.format` pjpg → nrjxl. Crop, cadence, bus window and pacing stay at the G5
  values. `still.raw.keep_crop=true`, so each wake also keeps the raw crop and the native JPEG of the
  **same exposure**.
- **Paired comparison:** the unit builds the pjpg fallback first on every wake (§2). With `keep_crop`
  it also keeps that pjpg, so every wake yields both formats from one exposure.
  - **Primary comparison: at the same message budget.** The unit's own pjpg vs the nrjxl it sent.
    This is the production question.
  - **Secondary comparison: at equal bytes.** The Mac rebuilds the pjpg at the nrjxl's byte count.
    The RC encoder was byte-identical Pi ↔ Mac in Sprint08 (108/108, Pillow 11.3.0). The unit's
    Pillow version is recorded, because a version change alters the bytes.
  - Scoring uses the rig's card tools (`metrics.py`, `color.locate`; `pool_score.py` once the pool
    work builds it).

| id | metric | PASS when | evidence |
|---|---|---|---|
| O1.1 | card colour (stress ΔE00 after card WB + CCM, study protocol) | nrjxl median ≥ 30 % lower than the unit's own pjpg at the same message budget, per unit (equal-bytes result reported beside it) | `analysis/o1_colour.csv` |
| O1.2 | AprilTags | 4/4 on 100 % of decoded nrjxl where the native JPEG has 4/4 | `analysis/o1_tags.csv` |
| O1.3 | size | every nrjxl ≤ budget; chunks ≤ cap | START `length` |
| O1.4 | delivery | 100 % complete ≤ 3 h, 0 redundant heals (D1 parity) | backend heal log |
| O1.5 | time to complete | median capture → complete ≤ the G5 pjpg median + 2 min | backend |
| O1.6 | fallback | ≤ 5 % of wakes; 100 % of fallbacks complete | START `rfb` |
| O1.7 | window | halt before bus off on 100 % of wakes | cycle logs / Spotter SD |
| O1.8 | energy | **N/A (agreed: reported, not gated)**: ΔJ per wake vs G5 from the Spotter SD logs, for Nick's decision | `analysis/o1_energy.csv` |
| O1.9 | heal cost | heals per nrjxl image and per day recorded; ≤ 24/day per Spotter (production cap) | backend heal log |

Plus Nick's eye on a before-after page (skill `before-after-report`): today's pjpg vs nrjxl neutral
render vs nrjxl card render, at the same bytes. The neutral render will **not** match today's JPEG look.
The raw path has no lens-shading correction, denoise, sharpening or ISP tone curve. That is
expected; the review is about colour and detail, not look.

---

## 8. Stages, gates, estimates

| stage | what | who | ESTIMATE | gate (all must hold) |
|---|---|---|---|---|
| **S0** desk (Mon 10/5) | Mac: distance rungs for each of the 3 raw presets (2400×1350, 2000×1124, 1600×900); crop × bytes grid (50/62/75/112.5 kB); container v1 frozen; decoder candidate verified in a Debian 12 container; hydrium Pi RSS from the rig data if present | this session | 1 d | rungs per preset + decoder choice written into this SPEC (r4) |
| **S1** camera (Tue–Wed) | `rc_raw_jxl.py`, still_action integration (JPEG first), START fields, registry v8 + rules + catalog, tests | bm dev session | 2 d | §7.1 bm suite green; Mac end-to-end within ±0.02 ΔE of the study; pjpg traces differ only in `cfg=` / `h=` (hashes masked) |
| **S2** backend (Tue–Wed, parallel) | contract, parser / sniff, migration 0020, ingest, `raw_render`, processing rule, gallery badge + download, tests | nvd session | 2 d | §7.1 nvd suite green; jxl-oxide parity; damaged streams fail loudly; no pjpg / h264 regression |
| **S2b** stretch | `raw_card_v1` variant | nvd session | 0.5 d | patch ΔE on fixtures within ±0.05 of the rig's card correction |
| **S3** bench R0–R3 | after the TE hands the bench over | Test Engineer | 0.5 d | R0–R3 rows PASS; R0.3 sets the default crop (largest passing preset); no preset passes = stop, re-plan with the EM |
| **S4** bench R4 | 12 production wakes on bmcam003 | Test Engineer | 12 h | R4 rows PASS |
| **S5** outdoor O1 | 24 h, both units | Test Engineer + Nick (box) | 24 h + 0.5 d analysis | O1.1–O1.7 PASS → Nick decides the release units' default `still.format` |

Merges: bm PRs → `development`, nvd PRs → `staging` (opened after any staging demo push), the EM
merges. Deploy to units only through the TE's hand-over. Field units (bmcam001/002, legacy `main`)
are out of scope until they get v9 through a release.

**Risk to the dates:** Mon–Fri 10/5–10/9 is also R1's slack for fixes and re-runs. S3–S5 need the bench
and the box. The desk stages S0–S2 do not.

### MVP cut

| in (MVP now) | MVP stretch | Next sprint (Sprint29 = transmit window, ruled) | Future |
|---|---|---|---|
| `still.format` switch; D2 cjxl modular e5 + distance rungs; default crop 1600×900 at native density (today's FOV); 2000×1124 / 2400×1350 opt-in presets, set with `still.format` in one change; one image per wake, never spread over wakes; JPEG fallback in the same wake; START `fmt`/`rfb`; NR container v1; backend ingest + neutral render + original kept + gallery; jxl-oxide + rig-decoder parity tests; HIL R0–R4; outdoor O1 | `raw_card_v1` card-corrected variant | hydrium fast path; lens shading from the IMX708 tuning file in the render; DNG download for customers | grey preview from G1-first planes; lossless "RAW on command" (study C packer, ~3 bpp); OpenMV units; video |

---

## 9. Risks

| risk | effect | mitigation |
|---|---|---|
| `--raw` exceeds CMA at `cma=128M` (Sprint07: 1.9 MB left) | RAW never works; capture retries cost time | R0 first; fallback keeps the JPEG; `--buffer-count` / `cma=` options at the S3 gate |
| cjxl on trixie differs from the study's 0.11.1 | different bytes per distance | S0 calibrates rungs; R0 records the version; the rung ladder absorbs drift |
| 2400×1350 slow or OOM on the Zero | default steps down to 2000×1124 or 1600×900 | R0.3 per-preset gate; `RAW_MAX_PX` only from measured rows |
| 2400×1350 does not fit 50 kB on heal wakes (≈ 43 kB room, §5.2) | lower quality on those wakes, or `rfb=fit` | rung walk targets the wake's own budget; R4 counts fallbacks |
| Decoder wheel lacks JXL on Render | no render | S0 checks in a Debian 12 container; the original is always stored; kill switch |
| Render memory at big crops | worker OOM | float32; crop limit; measured in S2 |
| No real-water data | colour gain under water unproven | rig pool test (separate); O1 is in air |
| R1 slack week overlaps | bench / box conflict | desk stages first; the EM orders the bench |

---

## 10. Nick's rulings (2026-10-02, via the EM)

| Q | ruling | where applied |
|---|---|---|
| Q1 default crop | **r4: default = 1600×900 at NATIVE density** (today's field of view, no downsampling). Nick wants spatial density, not a wider crop. 2000×1124 / 2400×1350 = opt-in presets only. (r3 "go bigger" withdrawn: it was a framing error) | §3.3, §5.2, §7.2 R0.3, §8 |
| Q2 transmit window | its own sprint, right after Sprint28 | §5.3, §8 |
| Q3 multi-wake images | **no**, in Sprint28 or as a planned follow-up. An image should mostly fit its window; healing after loss is fine; never intentionally reserve messages for a later cycle. The camera carry-over idea is dropped | §5.4, §8 |

Reading recorded for the EM to correct if wrong: "default crop" applies to nrjxl units. The registry
default of `still.crop` stays 1600×900, so pjpg units do not change field of view. The switch to
nrjxl sets the raw crop in the same command (§3.3).

---

## 11. Not covered / known limits

- Underwater colour (no real-water data), Iridium (`nrjxl` is cellular-only, like video), OpenMV
  cameras, video, the bmcam001/002 field units.
- The neutral render's look vs the ISP JPEG (§7.3).
- Energy per wake is reported, not gated.
- All times except the study's Pi rows and Sprint07/25/26 are ESTIMATES until R0.

---

## 12. Review record (r1 → r2, 2026-10-02)

An independent reviewer (fresh context) spot-checked ~35 cited values against the three repos. The
mismatches and design holes it found are all applied here:

| # | finding | where fixed |
|---|---|---|
| 1 | the capture retry ladder would carry `--raw` (≈ 300 s hang, possibly no JPEG, false WS capture errors) | §3.1: single RAW attempt, then today's unchanged capture |
| 2 | engineering-tier keys are read-only, so R3, the restore and `keep_crop` could not run | §3.7: `still.raw.distances` / `encode_max_s` / `keep_crop` are control tier |
| 3 | a failed render would serve the `.nrjxl` bytes in the gallery and feed processing / inference | §4.7-4.8: renderable = has a render; nrjxl without a render excluded |
| 4 | "pjpg goldens unchanged" is impossible (the v8 keys change the hash) | §3.6, §7.1, §8: re-record; only `cfg=` / `h=` differ |
| 5-7, 15 | heal slot, video-only timings, the 8-min cycle budget, the :05 boundary, the misquoted Sprint26 formula | §0.2, §3.8, §5.1, §5.3 |
| 8-14, 16 | CMA provenance + threshold, `--num_threads=0`, wl53 rejection, `.nrjxl` filename, `rfb=err` catch-all, odd-y presets, Iridium / self_heal rules, orphan DNG sweep | §0.2, §3.1, §3.3, §3.5-3.7, §6, §7.2 |
| 17-22 | contract path, hydrium memory wording, chunk arithmetic, derived chunk size, HIL determinism, `ActiveArea` | throughout |


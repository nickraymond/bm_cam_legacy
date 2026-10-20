# Spec: larger crops shrunk to 1600×900 in the NOAA release (S3) (PE 005 rev 3 + PE 006 rev 7.1, tier (a); rev 0 Mon 10/19 8:34 AM PT; rev 1 8:37 AM PT = the Pi data from Sunday's Addendum B replaces rev 0's wrong premise note, G1 becomes product code + live wakes; rev 2 9:31 AM PT = Nick's soak window answer (8 AM–6 PM Pacific); rev 3 9:32 AM PT = the bench units run America/Los_Angeles, so the window is 08:00–18:00 (rev 2 wrongly said ET 11:00–21:00); rev 4 (PE 006) 9:52 AM PT = must do 13 no longer clashes with G1 (b): bmcam004's 3 live-wake images may precede the nvd deploy; PjM → Lead (d59); rev 5 (PE 006) 9:56 AM PT = QE 009 FAIL on rev 4 (R1–R3, N1–N6) fixed: search ≤ 36 s + the crop-sweep card's 450 s predicted wake and TG-7 reef set restored (Nick decides their replacement, card nick_s3_bars), a real shrink memory guard, the G1 (b) images checked by /hq, rects named, presets named; rev 5.1 (PE 006) 9:58 AM = QE 009's rev 5 nits N1–N2, text only; rev 6 (PE 006) 6:38 PM = Nick's "yes to both." on nick_s3_bars: the 450 s predicted-wake bar becomes report-only (0 hard cuts etc. in G1 b), and G1 (c) may read the 30 TG-7 frames on Nick_R_SSD read-only; rev 7 (PE 006) 6:55 PM = Nick's approved-list rule for remote still.crop (must dos 11–12, tighter); rev 7.1 (PE 006) 7:04 PM = must do 6's diff list names the generator lines must do 8 needs)

Tier (a) (TEAM_RULES §13): it changes the frozen field-unit camera build, BM production (nvd `staging`) and the 10/27 field
day. QE spec PASS comes before any code. Three linked PRs, one per repo or ticket (§11): **bm_cam_legacy** (camera, → `development`),
**nereus-vision-dev** (decode + catalog + frontend presets, → `staging` = production). Home after PASS: bm_cam_legacy
`docs/specs/crop_view_s3.md`, byte-identical, first commit of the bm PR; the nvd PR body links it. Board: decision d60,
proposal `prop_crop_view`, ticket card `c_crop_view`. Replaces SPEC_crop_cap_s3 (native-pixel crops; its stop line was met
Sun 9:09 PM: every crop ≥ 1920×1080 ran out of the 250 MiB encoder guard on the Pi, c_crop_rois).

- **Why (Nick, his own comment, "the user (owner)", weekly plan page https://claude.ai/artifact/FBT3uPBDSE6GfEh36nJUvy, thread
  a76bb4f9, Mon 10/19 8:30 AM PT; recorded as d60):** "Now that we know that the pi can support larger crops at the fixed
  1600x900 rez, I want to add this formally to the NOAA release. So, we should make the code update to the production code so
  taking a larger crop image does not hit an error. Them we use the weekend as the RC soak candidate and make a call Monday AM.
  During that soak, all the crops need to be commanded over console twice and verified to work. We can widen the allowable on
  time window to be 8am to 6pm for the testing to get a more images. This is important because it lets me set larger crops and
  test how good the new JPEG-XL format is compared to the old JPEG using the existing coral reef we have access to."
- **Pi evidence already in hand (Sunday's Addendum B on bmcam004, Sun 10:28–10:43 PM under Nick's TE3-chat line; RESULTS.md
  "Addendum B", B_summary.csv in ~/NereusData/bm_cam_legacy/2026-10-18_crop_sweep_bmcam004/; page
  https://claude.ai/artifact/QZ3VnLU1ZPNg7AotCgWz9s):** a bench harness (banded area taps, not product code) shrank 1920×1080,
  2400×1350, 3200×1800 and the whole sensor to 1600×900 and ran the production search: 25/25 OK, 0 rfb, 0 OOM, MemAvailable
  min 73.8 MB, cjxl peak 114.2 MiB, Python peak 52.8 MiB (today) → 150.6 MiB (whole sensor), worst-frame total 30.95 s (today)
  → 34.06 / 35.29 / 29.62 / 40.14 s, ≤ 194 msgs, d ≤ 2.345. **Not measured:** a live wake (the camera software is ~76 MB resident
  on top of the shrink's Python peak), and whether the extra seconds come out of the image wake's listen tail or overrun it.
  (PE 005's rev 0 said the Pi had not run the shrink; that was wrong.)
- **Facts at start (bm_cam_legacy `development` @ 5c2c6f04, now 08c40a56 with `git diff 2cc1afdb 08c40a56 -- BM_Devel_Pi/` empty
  (PE 006, Mon 9:50 AM); nvd `staging` @ c51356e):**
  - The container already reserves the field: B3a profile v2 `params[26]` = output scale ×10000 (rc_raw_jxl.py `build_params_v2`,
    `P_OUTPUT_SCALE` = 26; sprints/Sprint28_raw_jxl/DESIGN_B3a.md:49 "Always 10000 in R1 (Nick: no downscale)"). The payload is
    one h × w × 3 JPEG XL stream.
  - The backend reads the field but does not apply it: nvd backend/app/services/nrjxl.py:161 only reports `output_scale`;
    `validate` (:292–309) checks the coded w × h against the crop and the sensor.
  - The cap: `RAW_MAX_PX = 1600 * 900`, config_validate.py:305, checked at :325 (`_rule_raw_crop`, nrjxl only), imported by
    rc_raw_jxl.py:99 (encode guard :1347) and used by rc_progressive_jpeg.py:468 (larger crop → no `--raw` capture, pjpg rfb=err).
  - Crop presets already in the registry (config_registry.py:293–302), all even: 1600×900 default [1504, 846, 1600, 900], full
    frame [0, 0, 4608, 2592], 3072×1728 [768, 432, 3072, 1728], 2304×1296 [1152, 648, 2304, 1296], 1000×562, 800×450, 640×360.
  - Demosaic = bilinear in 64-row strips (rc_raw_jxl.py `rgb_codes`, `RGB_STRIP_ROWS` = 64). The cjxl child runs under
    `ulimit -v` 250 MiB (`ENCODER_MEM_LIMIT_BYTES`, :102); the supervisor (DNG read, demosaic) has no such guard.
- **Must do (bm PR, camera):**
  1. **Shrink path, B3a only:** for an nrjxl still whose crop is larger than 1600×900, demosaic the crop exactly as today and
     area-average it to exactly **1600 × 900** (exact fractional pixel-area weights, i.e. what `cv2.resize(…, INTER_AREA)` does),
     strip by strip, so the full-resolution RGB of the crop is never held whole. `params[26]` = round(1600 / w × 10000). A crop
     ≤ 1600×900 keeps native density (`params[26]` = 10000). The container header's w × h and the crop params stay the native
     crop rect (nvd nrjxl.py:305–308 checks x + w ≤ native); only the JPEG XL stream is 1600 × 900. For every even 16:9 crop wider
     than 1600, round(w × s) × round(h × s) = exactly 1600 × 900 (QE 009 checked all 94).
  2. **Byte-identical regression:** at every crop ≤ 1600×900 the blob is byte-identical to S2 (2cc1afdb) on the same DNG and
     settings (test on the 5 bmcam004 pool DNGs, sha256 in the crop-sweep MANIFEST).
  3. **Validator:** `_rule_raw_crop` accepts an even crop inside the sensor whose **output** is ≤ 1600 × 900: crops ≤ 1600×900 as
     today; a larger crop only if w : h = 16 : 9 exactly (w × 9 = h × 16). Anything else is refused with an error code, at set,
     trg, boot and deploy. The same rule replaces the `RAW_MAX_PX` checks at rc_raw_jxl.py:1347 and rc_progressive_jpeg.py:468.
  4. **The JPEG fallback of a larger crop** (any rfb) is the same crop, resized to `still.output_width` as pjpg does today. Test it.
  5. **Shrink memory never crashes a wake (rev 5, QE 009 R2: Linux overcommit means a big allocation rarely raises MemoryError;
     the OOM killer acts instead).** Two guards, both ending in the existing JPEG fallback with a new code **rfb=shrink**, never an
     unhandled exception or a kernel OOM. Both apply only to crops larger than 1600×900; a crop ≤ 1600×900 keeps S2's path
     unchanged (must do 2):
     (i) **child process under a limit:** DNG read + demosaic + shrink run in a child process (as cjxl does) under `RLIMIT_AS` =
     `SHRINK_MEM_LIMIT_BYTES`, a code constant. Its value = G1 (a)'s largest measured child VmPeak (any candidate, any input) × 1.25,
     committed with that measurement before G1 (b); a child killed or failing on the limit → rfb=shrink.
     (ii) **pre-check:** the shrink starts only if MemAvailable ≥ `SHRINK_MIN_AVAIL_BYTES` = G1 (a)'s largest child VmHWM + 30 MiB
     (also a committed constant, set before G1 (b)); else rfb=shrink without starting it.
     Tests force both paths (a tiny limit; a pre-check above MemAvailable) and check the sidecar says rfb=shrink and a JPEG of
     the same crop is sent.
  6. **Diff rule (QE checks):** `git diff 2cc1afdb <S3> -- BM_Devel_Pi/` shows only must dos 1, 3, 4, 5 and the presets line (must
     do 9). Outside BM_Devel_Pi/: tests, the regenerated catalog, docs/specs/crop_view_s3.md, and in tools/gen_config_catalog.py only the lines that put must do 8's rule in the catalog (rules.nrjxl output size 1600×900 + the 16:9 rule; rev 7.1). If another PR touched BM_Devel_Pi/
     on development since 2cc1afdb, stop and tell the PE + Lead before building.
  7. Tests: must dos 1–5 (output dims, `params[26]`, area weights vs a reference within ±1 code, the 16:9 rule both sides, the
     fallback, rfb=shrink); full suite on the head and on the merge result, counts quoted.
  8. Regenerate docs/bmcam_config_catalog.json with the generator; it carries the new rule (output cap 1440000 px + the 16:9
     rule) and the presets; test_s27_config_catalog passes.
  9. **Presets = the supported crops** (Nick, prop_crop_cap thread d5d0c4a6: "the backend needs to be updated to match whatever
     values we support so the front end shows the correct settings to send remotely"): the registry's `still.crop` presets stay
     1600×900 and the smaller ones unchanged, plus whichever of the registry's 2304×1296, 3072×1728 and full frame pass G1 on all
     5 inputs (at most 7 presets). A preset that fails G1 is removed, not shipped. G1's Addendum B sizes (1920×1080, 2400×1350,
     3200×1800) are measured for information only and are not added (rev 5, QE 009 N3).
- **Must do (nvd PR, backend + frontend):**
  10. `nrjxl` decode honours `params[26]`: coded size = round(w × s) × round(h × s); `validate` checks that against the payload,
      keeps the crop as the sensor rect, and refuses a scale that isn't one of the camera's (s = 1, or 1600 / w for a 16:9 crop
      > 1600 wide). The image page renders 1600×900 for a shrunk image; the image's details show crop, scale and "shrunk".
      Tests use a real scaled blob from G1 (fixture ≤ 1 MB in tests/fixtures/).
  11. Vendor S3's catalog byte for byte. **Remote `still.crop` = an approved list only (rev 7; Nick, his own typed turn in the
      nvd session "Crop view S3 (backend)" local_b9c5acba…, Mon 10/19 evening (after PE 006's "aligned"), verbatim: "For sending remote commands keep it
      simple, just an approved list. … I dont want customers being able to manual enter a crop size we have not tested. Ok to
      build"):** remote_config's plan check accepts a `still.crop` only if it equals one of the catalog's `still.crop` presets
      (pjpg and nrjxl), and refuses anything else, a 16:9 crop that isn't a preset included, before it is sent.
      test_s28_config_catalog_v8 flips to "the 2304×1296 / 3072×1728 / full presets accepted (as G1 leaves them); 2400×1352
      (not 16:9) and 2400×1350 (16:9, not a preset) refused". The camera's own validator (must do 3) is unchanged; the backend is
      the stricter of the two. Video crop is out of scope (unchanged).
  12. The dashboard's still-tab crop control offers exactly the catalog's presets, with no typed crop field and no box drag; a
      test asserts the menu equals the catalog and each preset passes the plan check. Frontend QC sweep (desktop + 375 px,
      viewer + admin) on any dashboard file.
  13. **Order:** the nvd PR merges and deploys **before** any S3 build is deployed for the RC soak (must do 15) and before any S3
      image from bmcam003 (a decoder that accepts both s = 1 and the scaled sizes is safe for S2 units too). The one exception is
      G1 (b) on bmcam004 (bench, report-only), which may run before the deploy: production keeps each image's original blob, but
      the ingest display render fails with today's decoder and leaves display_key None, and display_refresh skips such rows
      (nvd poll_once_ingest.py:2173–2192, display_refresh.py:44; QE 009 R3). So those 3 images (and any G1 (b) repeat) stay on the
      placeholder in the gallery, viewer, timelapse and map; that is accepted for these bench images. G1 (b) is judged on the Mac
      decode; after the deploy the TE checks `/media/{id}/hq` (nrjxl_media.py:55–77) returns a 1600×900 render for each of them.
      The Lead times the staging merge (it redeploys production).
- **Must do (gates, soak, release):**
  14. **G1, the product code on the Pi (first, before the bm PR goes to QE for code):** TE3 on bmcam004, inside the command test
      window (Mon → Wed noon). Needs Nick's OK in TE3's chat. Candidates, as rects [x, y, w, h] (rev 5, QE 009 N2: a centred
      2400×1350 has an odd y, which config_validate.py:323 refuses): registry (shippable) [1152, 648, 2304, 1296],
      [768, 432, 3072, 1728], [0, 0, 4608, 2592]; information only (Addendum B / crop-sweep 6b rects) [1344, 756, 1920, 1080],
      [1104, 620, 2400, 1350], [704, 396, 3200, 1800].
      - **(a) encode-only**, the bm PR head's `rc_raw_jxl.py` with the real validator (no `--allow-any-crop`), the 5 pool DNGs, the
        NOAA profile's still.raw.*: per input 0 rfb; cjxl `peak_rss_kb` ≤ 170 MiB; MemAvailable min (1 s samples) ≥ 60 MiB; worst
        total (read + shrink + search) ≤ today's worst + 10 s (Addendum B's bar: ≤ 40.95 s on these frames); **each encode's search
        ≤ 36 s** (rev 5, QE 009 R1: the crop-sweep card's 6b bar; Addendum B's max was 29.01 s); ≤ 195 msgs at d ≤ 10.4; no OOM in dmesg.
        The predicted image wake (the crop-sweep card's definition: W_S2 + the candidate's search seconds − 1600×900's on that
        input, W_S2 from the soak, written in the MANIFEST) is **reported, not a bar**: rev 6 replaces the crop sweep's ≤ 450 s bar
        (S2's wakes already reach 480.94 s, wakes_bmcam004.csv) with (b)'s "0 hard cuts; shrink seconds come out of the listen
        tail; messages sent per wake reported vs S2's" (Nick, Mon 10/19 6:36 PM PT, his own comment "the user (owner)" on the Mon
        EOD update https://claude.ai/artifact/S13Ps9yPW3ENqn1Lb3ANRc, thread aa2ac983, item "Crop bars" naming card nick_s3_bars,
        verbatim: "yes to both."). It also measures the shrink child's VmPeak and VmHWM for must do 5's constants. Output within
        ±1 code of Addendum B's harness on the same frame is reported.
      - **(c) reef-like desk set (the crop-sweep card's 6b):** on the Mac, each shippable candidate shrunk with the bm PR head's code
        on the 30 TG-7 frames (bm `runs/s28_crop_sweep_20261005/tg7_selection.json`) at 195 messages: fits 30/30 AND distance
        P90 ≤ 10.4. The ORFs are on Nick_R_SSD (the crop sweep did not read them, its RESULTS.md:34, "off-limits"); Nick OK'd
        reading those 30 frames read-only (same comment, "yes to both."). Nothing on the SSD is written, moved or deleted; copies
        for the run go under ~/NereusData/bm_cam_legacy/<date>_crop_view_s3/. One heavy Mac slot with the Lead's all-clear.
      - **(b) live wakes:** S2 + this PR's camera files on bmcam004 (bench only), NOAA profile, crop = full frame (the worst case),
        3 consecutive in-window image wakes: 0 rfb; MemAvailable min over the whole wake ≥ 50 MiB; 0 hard cuts and wake → halt
        ≤ 481 s (S2 already reaches 480.94 s, so this means "no hard cut": the shrink's seconds come out of the listen tail);
        ≤ 195 msgs; images decode on the Mac with the nvd PR's decoder; per wake, listen-tail seconds and messages sent are
        reported next to S2's (QE 009 N6, feeds F7). Plus one live wake at the largest passing preset with the shrink fallback
        forced (must do 5), with (b)'s bars (0 hard cuts, wake → halt ≤ 481 s, MemAvailable min ≥ 50 MiB, ≤ 195 msgs, sidecar
        rfb=shrink, a JPEG of the same crop arrives), reporting the JPEG fallback's memory and time. If full frame fails, repeat (b) at the next smaller
        registry candidate; the largest that passes caps the shipped presets.
      - If no registry candidate > 1600×900 passes (a), (b) and (c): stop; d60 falls back to S2.
  15. **RC soak (Fri 10/23 PM → Mon 10/26 AM):** TE deploys S3 + the NOAA profile to bmcam003 and bmcam004 under a new soak card
      rev that names this spec. Capture window **8 AM–6 PM Pacific** (Nick, PE 005 chat, Mon 10/19 ~9:30 AM PT,
      verbatim: "8 AM–6 PM  for all soak testing should be local time, Pacific"), set as window 08:00–18:00 in the bench units'
      own zone, America/Los_Angeles (their base zone: every cycle log in 2026-10-17_soak_noaa/ shows `tz=America/Los_Angeles`,
      today ws=0700 we=1200 = 10–15 ET; the NY overlay drops at boot, c_tz_overlay_boot), with no timezone change. The TE writes
      the zone and the hours on the soak card.
      bmcam002's shipped window is unchanged by this (it covers soak testing). **On each unit, every preset is
      set over the USB serial console twice** (Nick: "all the crops need to be commanded over console twice"): ok-ack; the next
      wake sends that crop (shrunk to 1600×900 above 1600×900, native at or below); a cold reboot keeps it; then the next preset.
      One even step off 16:9 → refused with an error code. Finally `reset` → 1600×900.
  16. **RC soak bars (every wake, worst wake decides):** the soak card's existing bars (images complete ≤ 24 h, commands 100 %
      answered ≤ 3 h, 0 FALLBACK/ERR, every scheduled wake) AND per image wake: 0 rfb, cjxl `peak_rss_kb` ≤ 170 MiB, MemAvailable
      min ≥ 50 MiB (live wake), search ≤ 36 s, 0 hard cuts (wake → halt ≤ 481 s), ≤ 195 messages (shrink time, listen-tail seconds
      and messages sent per wake reported next to S2's); the backend renders every
      image at the size must do 10 predicts. Heal chunks per wake and the backlog within the d28 bars.
  17. **Remote revert proof (once):** through the backend, set bmcam003's crop to full frame, then back to 1600×900; both images
      arrive as predicted. NOAA can go back without a dive.
  18. PE amends SPEC_e4_noaa_release (rev 11 → rev 12): the update-day decode check reads the shipped crop's output, and the
      profile hash is S3's. QE re-gates. The Lead updates the 10/27 envelope and runbook to that rev.
- **Failure modes in the field:**

  | # | What goes wrong | How it's detected | Then |
  |---|---|---|---|
  | F1 | In a live wake the shrink + camera software (~76 MB) run the Pi out of memory (the supervisor is not under the 250 MiB guard) | MemAvailable min, supervisor VmHWM, dmesg OOM (G1 b, soak); rfb=shrink in the sidecar | rfb=shrink → JPEG of the same crop; remote set to a smaller preset; a preset that fails G1 never ships |
  | F2 | Reef scenes need more attempts than pool frames: search past the 45 s cap (bars hold it to ≤ 36 s, plus G1 (c)'s reef-like set); or the ~9 s shrink overruns the 480 s image wake | rfb=time in the sidecar; wake → halt and hard cuts in the soak log | Remote set to 1600×900 (must do 17 proves it) |
  | F3 | Backend can't decode a shrunk blob (camera ahead of backend) | Decode errors on the image page; missing images | Must do 13 order; check the deployed nvd sha before the first S3 image |
  | F4 | Dashboard offers a crop the camera refuses | Command NACK with the error code | Must do 12 test; must do 15's refusal check |
  | F5 | Cloud colour (E5) assumes native pixels, so the card isn't found on shrunk images | E5 card-not-found rate on shrunk images | Report-only for this release; E5 on shrunk images is a later card |
  | F6 | 8–6 window: more wakes per day than the power budget assumed (bench test only unless Nick says otherwise) | Spotter battery telemetry, wakes/day | Bench only; bmcam002's shipped window is unchanged (Nick's window line, PE 005 chat ~9:30 AM, covers soak testing) |
  | F7 | Heal backlog grows with more wakes per day | d28 bars: heal chunks/wake, backlog trend | Flag it; Nick decides at the Mon call |
  | F8 | S3 differs from S2 by more than this spec | Must do 6's diff | Stop; rebuild from 2cc1afdb |
  | F9 | Code + QE not done by Fri 10/23 6 PM, or G1 fails | Lead's clock; G1 bars | Ship S2 (today's soak build, d60 fallback); larger crops go to the first post-10/27 build |
  | F10 | The RC soak fails a bar | Must do 16 | Ship S2 at 1600×900 at the Mon 10/26 call |

- **Pass / fail (set now, before any S3 data):** pass = must dos 1–18 met, judged on the worst wake of the RC soak (bmcam003 scored,
  bmcam004 report-only) and the worst input of G1. Fail = any miss → F9/F10. These bars may only equal or tighten the crop-sweep
  card's and the soak card's bars, except the predicted-wake ≤ 450 s bar, which Nick replaced (rev 6, nick_s3_bars).
- **Kill lines (with check times):** G1 not run by Wed 10/21 6 PM → stop (F9). bm PR not QE PASS by Thu 10/22 6 PM → stop. nvd PR not
  deployed by Fri 10/23 noon → stop. Each stop is said on c_crop_view and to the PE + Lead.
- **Test plan / cost:** desk ~4 h (bm builder) + ~3 h (nvd session) + QE + Frontend QC; bmcam004 ~2 h for G1 (a) + 3 live wakes (b), inside the command
  test window; bmcam003 + bmcam004 for the RC soak Fri PM → Mon AM; Nick ~30 min at the console (2 × 7 presets × 2 units,
  ~1 min each, plus reboots). One heavy Mac slot for G1 (c) (the Lead's all-clear).
- **Out of scope:** crops ≤ 1600×900 (unchanged); non-16:9 larger crops; raising the encoder guard or `rgb_encode_max_s`; MHC
  demosaic; the E5 colour pipeline on shrunk images (F5); the pjpg path beyond must do 4; changing bmcam002's shipped crop (Nick
  sets crops by remote command after 10/27).
- **Done:** TEAM_RULES §4 + every must do and F-row checked off with evidence; PE + QE accept item by item; Nick's call Mon 10/26 AM.

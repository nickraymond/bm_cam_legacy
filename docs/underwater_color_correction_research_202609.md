# Underwater Color Correction — Research Brief (Sep 2026)

Purpose: hand this to a Claude Code session to implement color-correction experiments on Nereus BM camera stills that have a color reference card in frame.

## 1. The paper you remembered: Sea-thru (Derya Akkaynak & Tali Treibitz, CVPR 2019)

**What it is.** Sea-thru is a *physics-based* method — no machine learning, no training data. It "removes the water" by inverting a corrected underwater image formation model (Akkaynak & Treibitz, CVPR 2018):

    I_c = J_c · exp(−β_D,c(z) · z)  +  B∞_c · (1 − exp(−β_B,c · z))

- `I` = captured pixel, `J` = true color, `z` = distance camera→object, `c` = R/G/B channel.
- First term = the object's light getting attenuated (mostly red loss). Second term = backscatter (the blue-green haze).
- Key insight: the attenuation coefficient for the direct signal (β_D) is **different** from the one for backscatter (β_B), and β_D **depends on distance z**. Older methods assumed one constant coefficient — that's why they failed.

**How the algorithm works, step by step.**
1. Get a per-pixel **range map z** (distance in meters). The paper used structure-from-motion (many overlapping photos) and a known-size object for scale.
2. **Estimate backscatter**: bin pixels into ~10 distance clusters, take the darkest 1% of pixels in each bin (assume they are ~black objects, so what you see is pure backscatter), fit `B∞_c(1−e^{−β_B z})` to them.
3. Subtract backscatter from the image.
4. **Estimate the illuminant map** with Local Space Average Color (LSAC): iteratively average pixels in a neighborhood at similar depth — a spatially varying gray-world estimate.
5. From the illuminant map get a per-pixel `β_D(z) = −log(E_c)/z`, then fit a **2-term exponential** in z and refine by optimization.
6. Recover `J_c = D_c · exp(β_D,c(z)·z)`, then a final white balance.
7. **Color charts were used only for evaluation** (RGB angular error of gray patches vs neutral gray) — not as an input.

**Is the code available?**
- **Official code: no.** Akkaynak's site says the technology is proprietary and patent-protected (owned by Carmel Ltd and SeaErra); use requires written approval. Academic collaboration is by contact.
- **Unofficial re-implementations: yes**, several, MIT-licensed. The most used is [hainh/sea-thru](https://github.com/hainh/sea-thru) (Python, includes a `seathru-mono-e2e.py` that substitutes a monocular depth network for SfM so it works on a **single photo**). Others: [Teragion/Sea-Thru-Impl](https://github.com/Teragion/Sea-Thru-Impl), [nehamjain10/sea-thru_neham](https://github.com/nehamjain10/sea-thru_neham). These are clean-room implementations of a published paper; the patent risk is the same as for any Sea-thru derivative if used commercially — fine for internal experiments.
- **Dataset: yes.** The Sea-thru dataset (raw images + SfM depth maps + color charts at multiple distances) is on [Kaggle](https://www.kaggle.com/datasets/colorlabeilat/seathru-dataset) and Treibitz's lab page [viseaon.haifa.ac.il/datasets](https://viseaon.haifa.ac.il/datasets). Useful as a benchmark for whatever you build.

**Related Akkaynak papers worth knowing.** "What is the space of attenuation coefficients in underwater computer vision?" (CVPR 2017) — the one that actually photographed color charts at many distances and showed β varies with camera, water type and distance. "A Revised Underwater Image Formation Model" (CVPR 2018) — the model above.

## 2. What came after Sea-thru (2023–2026)

| Method | Year / venue | Type | Inputs | Code |
|---|---|---|---|---|
| **DeepSeeColor** (WHOI, Jamieson/How/Girdhar) | ICRA 2023 | Sea-thru model, parameters learned online by a small NN; real-time on AUV | RGB + depth map | [warplab/DeepSeeColor](https://github.com/warplab/DeepSeeColor), AGPL-3.0 |
| **SeaThru-NeRF** (Levy et al.) | CVPR 2023 | Sea-thru model inside a NeRF | Many overlapping images + poses | [deborahLevy130/seathru_NeRF](https://github.com/deborahLevy130/seathru_NeRF) |
| **OceanLens** (IISc) | arXiv 2024 | NN with physics losses; claims ~65% lower gray-patch angular error than Sea-thru/DeepSeeColor | RGB + depth (Depth-Anything-V2 OK) | [AIRLabIISc/OceanLens](https://github.com/AIRLabIISc/OceanLens), MIT |
| **Osmosis** | ECCV 2024 | Diffusion prior + physics model; single image | RGB only | [osmosis-diffusion](https://github.com/osmosis-diffusion/osmosis-diffusion-code) |
| **SeaSplat** (MIT/WHOI, Yang/Leonard/Girdhar) | ICRA 2025 — the May 2025 "removes the effect of water" news | Sea-thru-style model inside 3D Gaussian Splatting | Many images + COLMAP poses, desktop GPU | [dxyang/seasplat](https://github.com/dxyang/seasplat) |
| **UIEAnything** | Pattern Analysis & Applications 2025 | Zero-shot: modern depth model + white balance + "improved Sea-thru" (nonlinear backscatter, adaptive attenuation) | RGB only | No code found |
| Learned enhancers (CLIP-UIE, SS-UIE, Histoformer, AquaClarity…) | 2024–2025 | Trained end-to-end on paired datasets; pretty pictures, not radiometrically faithful | RGB only | See [CXH-Research/Underwater-Image-Enhancement](https://github.com/CXH-Research/Underwater-Image-Enhancement) list |

Trend: the physics model won. Newer work keeps the Akkaynak-Treibitz equation and swaps in better depth (Depth Anything V2), better optimization (NNs, NeRF, splats), or a diffusion prior. Pure "make it look nice" networks are mostly for aesthetics and are not trusted for scientific color.

## 3. Methods that use a color card in frame

This is a different lineage — **colorimetric calibration**, borrowed from photography/lab imaging — and it's the most direct way to use what you already have in your images.

- **Chart detection**: [colour-science/colour-checker-detection](https://github.com/colour-science/colour-checker-detection) (BSD-3; segmentation, template and YOLOv8 modes; ColorChecker Classic 24 only). Find the 24 swatches automatically.
- **Fit a correction from measured → reference swatch values** with `colour.characterisation` (in the [colour](https://github.com/colour-science/colour) library): 3×3 linear matrix (Cheung 2004), root-polynomial (Finlayson 2015, exposure-invariant, the usual best pick), or higher polynomial (Vandermonde). [colour discussion #747](https://github.com/colour-science/colour/discussions/747) is a worked example.
- ML variants: [collinswakholi/ML_ColorCorrection_tool](https://github.com/collinswakholi/ML_ColorCorrection_tool) (linear/PLS/polynomial regression from ColorChecker Classic).
- Underwater-specific chart work: the ISPRS 2022 SfM-MVS paper photographed ColorChecker charts at several distances and fitted 2nd-order backscatter/exponential absorption curves from the white/black patches to make a **per-pixel, depth-dependent** correction — basically Sea-thru with the card supplying the coefficients. No code released, but it's ~100 lines given a depth map.

**The catch with a card**: a chart correction is exactly right *at the chart's distance and location*. Objects farther away have more red loss and more haze than the card; nearer objects less. So a chart-only fix is a global fix; Sea-thru is a per-distance fix. The best answer combines them.

## 4. Options for your BM camera photos

| Option | What it does | Pros | Cons |
|---|---|---|---|
| A. Chart-only colorimetric fit (root-polynomial, colour-science) | Detect card, fit measured→reference, apply to whole image | Simplest; exact at the card; radiometrically meaningful; no depth needed; hours to build | Only correct at the card's distance; can't remove backscatter haze; clips saturated channels (red often near zero) |
| B. Sea-thru with monocular depth (hainh/sea-thru + Depth Anything V2) | Physics model, per-pixel distance-aware | Handles haze and distance-varying red loss; single image; MIT code exists | Relative (unscaled) depth; parameters estimated from image statistics, so colors are plausible not calibrated; slow-ish; patent for commercial use |
| C. Hybrid: Sea-thru geometry + card as ground truth (recommended) | Run B, then use the card (known distance from depth map, known reflectances) to solve/anchor β_D, β_B and the final white balance, then validate with gray-patch angular error | Card gives absolute scale to the depth map and true illuminant → calibrated colors everywhere, not just at the card; card doubles as a built-in metric | Most work (~days); needs the card visible and roughly planar |
| D. Learned models (OceanLens, DeepSeeColor) | Train/adapt a small network on your own footage | Fast at inference, could run on edge later; OceanLens MIT | Need depth for training; AGPL for DeepSeeColor; outputs less interpretable |

**Recommendation: start with A (a day), then build C on top of it.** A gives you an immediate, defensible correction and — more importantly — a **metric**: the gray-patch angular error from the Sea-thru paper, computed on your own card. Every later experiment (B, C, D) gets scored against that same number. C is the "latest and greatest" for a card-in-frame workflow; nobody has released it as a package, so it's a real contribution you could open-source alongside the urchin model.

## 4b. Commercial reuse — what you can ship in a paid web feature

**What licence to look for.** Not CC-BY. Creative Commons licences are written for content (images, datasets, papers); the CC people themselves say don't use them for software, and CC-BY-2.0 in particular is an old version. For code you want a *permissive* open-source licence: **MIT, BSD-2/3-Clause, or Apache-2.0**. All three let you use, modify, and sell the code inside a closed product; you only have to keep the copyright notice (and, for Apache, a NOTICE file and a note of changes). Avoid for a hosted service: **GPL** (must release your derived code if you distribute it), **AGPL** (worse — the obligation triggers when users interact over a network, i.e. exactly your website), **CC-BY-NC / "research only"** (no commercial use at all). Apache-2.0 also includes an explicit patent grant from contributors, which MIT/BSD lack — a plus if you have the choice. This matches your existing preference for Apache-2.0 tooling.

**Component-by-component verdict (checked Sep 2026):**

| Component | Licence | OK in a paid product? | Notes |
|---|---|---|---|
| Akkaynak–Treibitz image formation model (the equations) | Published science | Yes | Equations and published methods are not copyrightable; only code and patents matter |
| Sea-thru *algorithm* as a whole | Patent claimed by Carmel Ltd / SeaErra (her site) | **Caution** | Her site states it is patent-protected and use requires written approval. I could not locate the patent number in public search (the Treibitz patent US10885611 is a different haze-lines method and has lapsed). Before selling a Sea-thru-derived feature, do a patent search on Carmel Haifa University Economic Corp / SeaErra and have counsel read the claims; or contact SeaErra for a licence; or lean on the chart-anchored variant (C) whose novelty is the card, not the dark-pixel backscatter trick |
| hainh/sea-thru, Teragion, nehamjain10 reimplementations | MIT | Yes (copyright-wise) | Patent caveat above still applies to what the code *does* |
| colour-science `colour` library | BSD-3 | Yes | |
| colour-checker-detection | BSD-3, **but YOLOv8 inference path is AGPL-3.0** | Yes if you use the *segmentation/templated* detectors only; **no** if you use the YOLO detector | Or train your own detector under Apache (YOLOX, RF-DETR) |
| Depth Anything V2 — Small (incl. metric-depth Small/Base) | Apache-2.0 | Yes | |
| Depth Anything V2 — Base / Large / Giant | CC-BY-NC-4.0 | **No** | Non-commercial |
| OceanLens | MIT | Yes | Check any depth model it bundles (see above) |
| DeepSeeColor (WHOI) | AGPL-3.0 | **No** for a hosted service without open-sourcing your stack | Could negotiate a commercial licence with WHOI |
| SeaThru-NeRF | Apache-2.0 | Yes | Impractical for single stills anyway |
| SeaSplat | Inherits **INRIA/MPII Gaussian Splatting licence — research/non-commercial** | **No** | Confirmed from repo LICENSE.md |
| Osmosis | MIT code; pretrained diffusion model trained on KITTI/DIODE etc. | Probably not | KITTI is non-commercial; the checkpoint's status is unclear — treat as research-only |
| Sea-thru dataset (Kaggle) | Check dataset page terms | Test/benchmark only | Fine for validation; don't ship the images |
| Your own color card's reference values | Manufacturer's data | Usually yes for correcting images | Some vendors restrict redistributing the reference spectra; check your card's terms |

**Also patent-check independently:** Arashi Vision (Insta360) holds US12373929B2 (granted Jul 2025) on a specific *empirical* red/blue gain + fusion pipeline. It does not cover physics-based methods, but it shows the space is being patented; a simple "boost red by channel means" fallback could brush against it.

**Bottom line for the product:** Option A (chart colorimetric fit) is fully clean — BSD/MIT stack, well-known prior art, no patent exposure I can find. Option C is your differentiator; build it on Depth Anything V2 **Small** (Apache) + MIT Sea-thru code + BSD colour tools, and get a patent opinion on the Sea-thru claims before launch. Skip DeepSeeColor, SeaSplat, and the large Depth Anything models for the commercial path.

## 4c. Using the card's length scale to get distance (your question 2)

Short answer: yes, and doing geometry first and color later is exactly the right order.

First a wording note: what the physics methods need is a **range map** — the distance from the camera to each pixel's object, in meters — not "depth of field" (which in photography means the in-focus zone) and not water depth (your pressure sensor). Sea-thru calls it z.

**What the ruler gives you.** A pinhole camera says `z = f · W / w`, where W is the card's real width, w its width in pixels, f the focal length in pixels (from a one-time calibration of your camera *in its housing, underwater* — the flat port changes the effective focal length by about the refractive index of water, ~1.33). That yields the distance to the card, one number, typically to a few percent. If you detect all four corners you also get the card's tilt, so you get a small *range* of distances across the card rather than a single point — useful but still local.

**What it does not give you.** Distance to everything else in the frame. For that you still need a dense range map from a monocular depth model (or stereo/SfM). Monocular models come in two flavors: *relative* models output depth that is correct only up to an unknown scale (and often an unknown offset), while *metric* models try to output meters but are typically 10–20% off and were trained on land scenes. Either way the card is the fix: it is a known-size object at a known place in the image, which is precisely what the original Sea-thru paper used ("objects of known size") to put SfM depth into meters.

**How to combine them (this becomes step 7a of the plan):**
1. Run Depth Anything V2 Small — metric variant — to get a dense map `d(x,y)`.
2. Compute `z_card` from the ruler; sample the model's depth at the card's pixels.
3. Solve for a scale factor `s = z_card / d_card` and apply to the whole map (if you use a relative model that also has an offset, two anchors are needed: a second known-distance object, or the near and far edges of a tilted card, or the camera-to-seafloor distance when the camera is fixed and the bottom is flat).
4. Sanity-check with the dark-pixel backscatter fit — if scaled range is wrong, the fitted `β_B` will be physically implausible (attenuation coefficients for real water fall in a known range, per Akkaynak 2017).

**Then the color patches add:** the absolute illuminant/white point at `z_card`, a direct measurement of `β_D` at that distance (known reflectance × known distance → coefficient per channel), and the scoring metric. Because your camera is fixed, once you have characterized the water at a site you may only need the card in occasional frames; the range map can also be reused across frames if nothing moves.

**Practical tips:** make the ruler high-contrast and at least ~10% of the frame width so corner detection is sub-pixel; keep the card roughly perpendicular to the lens for the first experiments; and calibrate the housing+camera once with a checkerboard in a tank — that calibration is what makes the meters real.

## 4d. Getting the range measurements repeatably (tools, not tape)

Field sheet for the deployment team: artifact "Reef Card Range Survey" (measurements H, θ, D1, X1, φ, W1–W3, Z; one-time C1 card geometry, C2 in-housing focal length).

**Do construction laser distance meters work underwater?** Physically yes, with a fixed correction: light travels 1.33–1.34× slower in seawater, so a phase-shift or time-of-flight meter reads ~33% long; divide by the refractive index (varies <0.5% with temperature/salinity — negligible here). The real limits are optical, not the speed: the red 635–650 nm beam is absorbed within a few metres, suspended particles bounce light back early (backscatter confuses the receiver — the same haze problem the colour correction is fighting), and the receiver optics were designed for an air return. Consumer meters in a housing typically work for ~1–3 m in clear water and fail in turbidity. Purpose-built underwater rangefinders use green 520–532 nm lasers and phase modulation (e.g. ROVMAKER TOF ~$600, 60 m claimed; IADIY 100 m green module) and handle the correction internally.

| Option | Gives | Pros | Cons |
|---|---|---|---|
| Tape + plumb line (current plan) | H, D1, X1, W | Free, unambiguous | Diver time, not repeatable frame-to-frame |
| Consumer laser meter in a housing | D1, W | Cheap, in hand | Red laser dies in a few m; ×1/1.33 correction; unreliable in turbidity |
| Underwater green-laser rangefinder module | D1, W, per-frame if mounted | Long range, corrected | $600+, another housed device, one point per reading |
| Single-beam sonar altimeter (Blue Robotics Ping2, $430, 0.3–100 m, 1 cm at 2 m, 25° beam, UART) | **H** continuously, logged with each frame | Robust to turbidity, cheap, integrates with the Pi/BM node; H is the number a fixed mount most needs | Wide beam averages the bottom; not for the coral wall |
| **Camera-mounted laser scalers** (two parallel green dots at known separation, or two converging beams) | Distance wherever the dots land, in every frame, from the image itself | Standard fisheries/ROV technique; ~$100–300 in lasers; no diver action; also a second known-size object for scaling; used in the literature to auto-scale SfM models | Dots must land on visible surface; need to detect dots in software; alignment must be rigid |
| Stereo camera pair | Full metric range map every frame | Best possible geometry, no card needed | Second camera + housing + calibration; different product |

**Recommendation.** For a fixed mount, stop relying on the diver: mount a pair of green laser dots (or a converging pair) rigidly next to the lens so every frame carries its own distance anchor, and put a Ping2 on the mount so camera height H is logged automatically. Keep the card for colour and as the initial cross-check. Tape measurements are then only needed on the first deployment to validate the lasers. A handheld laser meter is not worth housing for this job.

## 5. Implementation plan for a Claude Code session

1. **Repo skeleton** `uw-color/` with `pyproject.toml`; deps: `numpy`, `opencv-python`, `colour-science`, `colour-checker-detection`, `rawpy` (if you can save RAW/DNG from the camera — strongly preferred; JPEG has already been white-balanced and gamma'd), `torch` + Depth-Anything-V2 for option B/C.
2. **Ingest**: load linear RGB (demosaic RAW with no WB, or de-gamma sRGB JPEG as a fallback). Record camera, exposure, depth (pressure sensor) and time in a sidecar JSON.
3. **Card module**: detect swatches (`colour_checker_detection.detect_colour_checkers_segmentation`, YOLO fallback), sample patch means with a center crop, store measured RGB + reference sRGB/XYZ for the exact card model you use (get the manufacturer's reference values; X-Rite Classic post-Nov-2014 values are in `colour`).
4. **Metric module**: gray-patch RGB angular error ψ̄ (Sea-thru paper, Sec. 6), plus ΔE2000 over all 24 patches; also log the red-channel SNR of the card.
5. **Method A**: `colour.characterisation.matrix_colour_correction` / `polynomial_expansion_Finlayson2015` (degree 2–3, root-polynomial). Apply, score, save before/after.
6. **Method B**: vendor `hainh/sea-thru` as a module; swap depth to Depth-Anything-V2 **Small, metric variant** (Apache-2.0 — do not use Base/Large in the product); expose backscatter/attenuation fits as functions returning parameters, not just images.
7. **Method C**: (a) scale the depth map using the card's ruler as in §4c (camera intrinsics from a tank checkerboard calibration in the housing); (b) use the card's black/white patches as the "known reflectance at known z" constraints to solve `B∞`, `β_B` and `β_D(z_card)`; (c) run the Sea-thru recovery; (d) final white balance from the card's neutral patches, not gray-world; (e) score.
8. **Evaluation harness**: one CLI `uwc run --method a|b|c --in dir/ --out dir/` producing a side-by-side contact sheet and a CSV of metrics per image. Validate the harness on the Sea-thru Kaggle dataset first (has charts + true depth), then on your BM stills.
9. **Later**: distill the winner into a per-deployment lookup (β per water type) that the AE3 or Pi could apply cheaply in the field.

Practical notes: keep the card ~1 m from the lens and roughly perpendicular; include it in every N-th frame, not every frame, once the water is characterized; if the red channel of the white patch is < ~5% of full scale the card is too far for any method to recover red — move it closer or add light.

10. **Licensing hygiene**: keep a `THIRD_PARTY_LICENSES.md`; pin only MIT/BSD/Apache dependencies (add a CI check, e.g. `pip-licenses --fail-on="GPL;AGPL;CC-BY-NC"`); use colour-checker-detection's segmentation detector, not its YOLO path.

## Sources
- Akkaynak & Treibitz, Sea-thru, CVPR 2019 — https://openaccess.thecvf.com/content_CVPR_2019/papers/Akkaynak_Sea-Thru_A_Method_for_Removing_Water_From_Underwater_Images_CVPR_2019_paper.pdf
- Akkaynak & Treibitz, Revised Underwater Image Formation Model, CVPR 2018 — https://openaccess.thecvf.com/content_cvpr_2018/papers/Akkaynak_A_Revised_Underwater_CVPR_2018_paper.pdf
- Akkaynak & Treibitz, Space of Attenuation Coefficients, CVPR 2017 — https://openaccess.thecvf.com/content_cvpr_2017/papers/Akkaynak_What_Is_the_CVPR_2017_paper.pdf
- Sea-thru official page (licensing) — https://www.deryaakkaynak.com/sea-thru
- Sea-thru dataset — https://www.kaggle.com/datasets/colorlabeilat/seathru-dataset ; https://viseaon.haifa.ac.il/datasets
- hainh/sea-thru — https://github.com/hainh/sea-thru
- SeaSplat (MIT News, May 2025) — https://news.mit.edu/2025/imaging-technique-removes-water-effect-underwater-scenes-0520 ; code https://github.com/dxyang/seasplat
- DeepSeeColor — https://arxiv.org/abs/2303.04025 ; https://github.com/warplab/DeepSeeColor
- OceanLens — https://github.com/AIRLabIISc/OceanLens
- SeaThru-NeRF — https://github.com/deborahLevy130/seathru_NeRF
- UIEAnything — https://link.springer.com/article/10.1007/s10044-025-01422-z
- Underwater enhancement method list — https://github.com/CXH-Research/Underwater-Image-Enhancement
- Systematic review of color correction methods (PLOS One 2025) — https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0317306
- colour-checker-detection — https://github.com/colour-science/colour-checker-detection
- colour ColorChecker correction example — https://github.com/colour-science/colour/discussions/747
- ML_ColorCorrection_tool — https://github.com/collinswakholi/ML_ColorCorrection_tool
- Depth Anything V2 (model licences) — https://github.com/DepthAnything/Depth-Anything-V2
- Treibitz/Berman/Avidan haze-lines patent US10885611 (lapsed; not Sea-thru) — https://patents.google.com/patent/US20190114747A1/en
- Arashi Vision underwater color restoration patent US12373929B2 — https://patents.google.com/patent/US12373929
- ISPRS 2022 colour charts + SfM-MVS — https://isprs-archives.copernicus.org/articles/XLIII-B2-2022/959/2022/isprs-archives-XLIII-B2-2022-959-2022.pdf

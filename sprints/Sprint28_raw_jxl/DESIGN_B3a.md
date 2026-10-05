# Sprint28 B3a: linear camera RGB → JPEG XL VarDCT (container profile v2), design note

Status: **DRAFT for the EM / backend** (2026-10-05). Decision (Nick, 10/5 ~15:20 PDT, via the
EM): B3a is the R1 JPEG XL direction. The ROI stays `still.crop [1504, 846, 1600, 900]` at native
1600×900, the cap stays 195 msgs, and every byte goes to detail (lowest d that fits). Evidence:
`runs/s28_density_sweep_20261005/` (6f5e172, b756acf) and `runs/s28_crop_sweep_20261005/`
(387bdae). On 30 TG-7 frames ("TG-7 resampled to IMX708 geometry, an approximation"),
SSIMULACRA2 is +20.6 / +11.8 vs today's JPEG (P50 / P90) and equal quality takes 0.59× / 0.71× the
JPEG's bytes. The 4-plane v1 path stays the fallback and opt-in until B3a passes the bench.

## 1. Pi pipeline (rc_raw_jxl, new `encode_rgb_still`), memory plan

| step | what | memory (1600×900) |
|---|---|---|
| 1 | `--raw` capture + DNG crop read: **unchanged** from #120 | mosaic uint16 2.9 MB |
| 2 | headroom scale, one pass over the mosaic: `s = max(1, max_c g_c · max(norm. samples of colour c))`, with `g = DigitalGain · [gR, 1, gB]` built from the **rounded** header params (§2), so decode inverts exactly. A bilinear output is a convex mix of same-colour samples, so `s` is an **exact** no-clip bound | ~0 |
| 3 | numpy bilinear demosaic in **row strips** (64 rows + 1-row halo), per strip: normalise → × g / s → sqrt LUT (§3 of CONTAINER v1, b = 12) → uint16 codes | codes 8.6 MB + strip temporaries < 4 MB |
| 4 | write `x.ppm` (16-bit P6, maxval 4095) to the work dir, then drop the arrays | 8.6 MB on disk |
| 5 | `cjxl x.ppm x.jxl -m 0 -e 5 -d D --num_threads=0` in the **same guarded child** as v1 (`oom_score_adj 1000; ulimit -v 250 MB; exec`) | **Measured on Linux arm64 (Docker, Debian trixie, cjxl 0.11.2), real 004 frame: VmPeak 123.8–125.3 MiB under the guard, 0 kills, 2.0× headroom; VmHWM ~99 MiB** (`runs/s28_b3a_vmpeak_20261005/`). Pi CPU time = bench B0 |
| 6 | byte-target search (§4), seal the container (§2), keyed send as #120 | blob ≤ 56 kB |

Supervisor extra RSS is ESTIMATED at ~20 MB, logged as today (`supervisor_rss_kb_at_encode`). There
is **no OpenCV on the units**, so the demosaic is our own numpy code. A 4608×2592 or 2880 crop is
**out of scope**: ESTIMATE 303–558 MB, which does not fit.

**Demosaic:** bilinear now (`demosaic_id 1`): it matches what was scored and the backend's v1
render. Malvar-He-Cutler 5×5 (`demosaic_id 2`) is a later option: better edges, ~3× the numpy
work, and it can overshoot, so `s` would need a measured max instead of the bound. Cost to an
algorithm developer: the demosaic is fixed on the unit, so no custom, edge-aware or AI demosaic and no CFA-domain
denoise later. WB, CCM, tone and his colour correction are unaffected (the data is camera-native
linear).

## 2. Container profile v2 (header table for the backend; v1 §1–§4 rules apply unless listed)

| field | v2 value | note |
|---|---|---|
| magic | `NR` | unchanged |
| method (byte 2) | **20** = "B3a: RGB VarDCT" | v1 = 14. The backend dispatches on this |
| flags (byte 3) | **0x06** = layout `rgb3` = 2 (bits 0–1), curve `sqrt` = 1 (bits 2–3) | new layout value |
| cfa (byte 4) | the sensor CFA at the crop origin | informational in v2 (the demosaic already ran) |
| b (byte 5) | **12** | code bits per channel |
| w, h | output px = crop px (1600, 900) | even |
| black, white | DNG BlackLevel / WhiteLevel | the DN scale of the linear data |
| pedestal | 0 | |
| params[0] | **2** = profile version | v1 = 1 |
| params[1..23] | **same meaning and scale as v1** | gains [8,9], CCM [10–18], DigitalGain [22] are the coding gains |
| params[24] | **headroom scale s** ×10000 (≥ 10000) | new, required |
| params[25] | **demosaic_id**: 1 = bilinear (v2.0), 2 = MHC (reserved) | new, required |
| params[26] | **output scale** ×10000 (10000 = native density) | new, required. Always 10000 in R1 (Nick: no downscale) |
| n_len | **1** | |
| payload | one JPEG XL stream, VarDCT, **h × w × 3** (R, G, B), codes 0..4095 (12-bit) | decode at the stream's 12-bit depth (djxl to PPM keeps maxval 4095, even with `--bits_per_sample=16`), or to a 16-bit PNG and `code = round(v · 4095 / 65535)`. Always honour the output's maxval |
| crc | crc-v1b, unchanged (header with params[5] = 0, then the payload) | |

Backend decode → camera-native linear RGB: `lin_c = ((code / S)² ) / (white − black) · s / g_c`,
where `S = 4095 / sqrt(white − black)` and `g = DigitalGain · [gR, 1, gB]` from params
(DigitalGain = 1 if absent). Neutral render = v1 §5 steps 8–10 on `lin` (× DigitalGain · WB,
CCM, sRGB). **LinearRaw DNG (backend):** PhotometricInterpretation 34892 (LinearRaw), 3 samples
per pixel; `lin` stored as 16-bit; BlackLevel 0, WhiteLevel 65535; AsShotNeutral = `1 / [gR, 1, gB]`;
ColorMatrix1 = the XYZ→camera matrix derived from the CCM (`inv(M_sRGB→XYZ · CCM)`, normalised).
Lightroom / RawTherapee / darktable open LinearRaw. **Not tested yet**: the backend needs one
export check. The values are lossy (JPEG XL) camera-linear, not sensor-exact.

## 3. Byte search (reuse)

`_Walk` / `target_search` / `choose_rate` are unchanged: the same fit rule, ≤ 3 encodes, fill 0.97,
`still.raw.d_max` floor → `rfb=floor`, and the encode-time guards. Only the encode step changes:
1 RGB encode instead of 4 planes. Its prior is set from the sweeps for VarDCT:
B_REF 54 kB at D_REF 2.6, K 0.85 (TG-7: P50 d 2.58 at 54 kB, 5.36 at 29 kB). The one-point
correction and secant handle the scene. The fixed `still.raw.distances` fallback rungs get a VarDCT set
(proposed: [2.0, 2.6, 3.5, 5.0]).

## 4. Fallback (any failure → today's pjpg, same wake, loud `rfb=`)

The existing codes and meanings are kept: `cap` (--raw capture), `dng` (crop read), `enc` (cjxl
exit), `mem` (guard kill), `time` (encode cap / budget), `fit`, `floor`, `err` (metadata missing,
demosaic / prep exception, header rule). **No new wire code.** The detail goes to the log and the
sidecar `raw_fallback_detail`. The v1 4-plane path is **not** an automatic second try: one RAW
attempt per wake, as #120.

## 5. Config and wire changes vs #120

- New key `still.raw.layout` ∈ {`bayer4`, `rgb`}. Default `bayer4` until the bench passes, then
  flip it to `rgb` (registry v10; #133 is v9). The validate rules are #120's. `still.raw.demosaic`
  is not added (only bilinear exists).
- START: **unchanged** (`fmt=nrjxl q=<d×100> att=<n> cmp=1`, keyed chunks, `rfb=` on the fallback).
  The variant is inside the container (method 20 / params[0] 2). A backend that predates v2
  refuses it as "unknown method", which is safe (original kept). **Ship order: backend first.**
  If the backend wants the format visible in START instead, the alternative is `fmt=nrrgb`. I
  prefer one format, because the MIME, filename and heal path stay one code path.
- Filename `<stem>_compressed.nrjxl`, MIME and heal handling: unchanged.

## 6. Tests, goldens, estimate, bench

- **Desk tests:** container v2 pack/unpack/crc and refusals; the no-clip bound (property test: the
  bilinear output max ≤ the bound); strip demosaic == full-frame numpy reference; inversion exactness
  (lossless d 0 round trip → `lin` within 1 code); the encode under the fake runner; config / render
  / catalog. **Goldens:** `v9_nrjxl_rgb` (+ `rfb` variants for `err` from prep). All pjpg / v1 traces
  hash-only (the new key).
- **Mac e2e:** decode with an independent decoder (djxl + numpy, separate from rc_raw_jxl) and
  compare to the sweep's B3a scores on 3 TG-7 + 3 IMX708 frames.
- **Estimate:** ~14 h desk: container 2 h, numpy prep + bound 3 h, encode + search wiring 2 h,
  config / registry / catalog 1.5 h, goldens + fake runner 1.5 h, Mac e2e 1.5 h, docs (CONTAINER
  v2 §, SPEC, LADDER) 1.5 h, review fixes 1 h.
- **Bench (TE, LADDER B0–B2):**
  - **B0, before anything ships:** 10× on a bench unit, a 1600×900 RGB PPM through the real guard.
    PASS if VmPeak < 250 MB with 0 kills, cjxl VmHWM is recorded, the median encode is ≤ 10 s at
    d 2.6 e5, the 3-encode search is ≤ `encode_max_s` 30 s, prep time and supervisor RSS are
    recorded, and the predicted wake (capture + search + 195 msgs × 1.3 s + tail) is ≤ 480 s.
    If VmPeak > 250 MB: try e4, or raise the guard (the unit has CMA 256 of 512 MB: measure the
    headroom first).
  - **B1:** one B3a still on the console lane. The reassembled sha matches; backend decode + render
    + LinearRaw DNG opened in RawTherapee.
  - **B2:** forced fallbacks (time, fit, floor) → pjpg with `rfb=` complete.

Open for the EM / backend: (a) method byte vs `fmt=nrrgb`; (b) the param indices 24–26;
(c) the DNG ColorMatrix derivation (backend owns it).

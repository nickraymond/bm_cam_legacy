# Sprint28: the `nrjxl` container, profile v1 (camera ⇄ backend contract)

Status: **v1, agreed** (2026-10-02; camera session approved 7ca92d1 + the §7 filename change). This is the one shared contract between the camera build session
("Build Sprint28 camera side") and the backend build session ("Build Sprint28 backend side").
Both sessions agreed positions 1–7 on 2026-10-02 and approve this doc in the PR on
`docs/sprint28-container`. SPEC.md §3.6 and §4 describe the design. Where this doc and the SPEC
differ on bytes, this doc wins. The nvd wire contract (`backend/docs/bm_media_wire_contract.md`)
links here and does not copy the layout.

Owner of the bytes: the camera (`BM_Devel_Pi/rc_raw_jxl.py`). Owner of the refusals: the backend
(`backend/app/services/nrjxl.py`). The rig's study decoder (`compression_study/methods/raw_planes.decode`,
rig `origin/main` 372d6f2) must decode every production blob. That is the independent check.

---

## 1. Byte layout

The study's `NR` header (`compression_study/common.py` `Header.pack`), unchanged:

| offset / order | field | encoding | v1 value |
|---|---|---|---|
| 0–1 | magic | 2 bytes | `b"NR"` |
| 2 | method | u8 | **14** (study D2). 19 (H, hydrium) is reserved for a later profile, and the backend refuses it in v1 |
| 3 | flags | u8 | **0x04**: layout `4pl` = 0 (bits 0–1), curve `sqrt` = 1 (bits 2–3, so `1 << 2`), binned = 0 (bit 4) |
| 4 | cfa | u8 | index into `("RGGB", "BGGR", "GRBG", "GBRG")`. It is the CFA **at the crop origin**. The crop origin is even, so it equals the sensor CFA |
| 5 | b | u8 | **12** (code bits of every plane) |
| then | w | uvarint | crop width in **native sensor px**, even, > 0 |
| | h | uvarint | crop height in native sensor px, even, > 0 |
| | black | uvarint | the DNG `BlackLevel` (IMX708: 64 at 10 bits, never a constant) |
| | white | uvarint | the DNG `WhiteLevel` (IMX708: 1023), > black |
| | pedestal | uvarint | **0** |
| | n_params | uvarint | ≥ 24 in v1 (§2) |
| | params[i] | uvarint of zigzag(int) | §2 |
| | n_len | uvarint | **4** |
| | len[0..3] | uvarint | byte length of each payload |
| then | payloads | bytes | R, G1, G2, B, in this order, concatenated. **No trailing bytes**: header + Σ len == blob length |

- uvarint = LEB128 (7 bits per byte, low group first, high bit = continue).
- zigzag: `v ≥ 0 → 2v`, `v < 0 → −2v − 1` (study `_zz` / `_unzz`).
- G1 is the first green in raster order of the 2×2 CFA cell (study `plane_offsets`).

### 1.1 Each payload

- One JPEG XL stream: a bare codestream (`FF 0A`) or an ISO-BMFF container. The backend accepts both.
- It decodes to exactly **h/2 rows × w/2 columns, 1 channel (grey)**, with integer codes **0..4095**.
- Producer: a 16-bit P5 PGM with **maxval 4095** of the codes from §3, then
  `cjxl in.pgm out.jxl -m 1 -e <effort> -d <distance> --num_threads=0`. It is untagged, and cjxl
  treats grey as sRGB-transfer grey. The decoder returns the same code space, so that is fine.

## 2. Params, profile v1

All values are signed integers. Scaled values are rounded **half away from zero**:
`sign(x) · floor(|x| · s + 0.5)`.

| idx | param | unit / scale | required | source on the camera |
|---|---|---|---|---|
| 0 | profile version | = **1** | yes | constant |
| 1 | crop_x | native px, even | yes | `still.crop[0]` |
| 2 | crop_y | native px, even | yes | `still.crop[1]` |
| 3 | native_w | native px | yes | DNG full width (4608) |
| 4 | native_h | native px | yes | DNG full height (2592) |
| 5 | crc32 | uint32, stored as a non-negative int | yes | §4 |
| 6 | exposure | µs | yes | `ExposureTime` |
| 7 | analogue gain | ×1000 | yes | `AnalogueGain` |
| 8 | ColourGains R | ×10000, > 0 | yes | `ColourGains[0]` |
| 9 | ColourGains B | ×10000, > 0 | yes | `ColourGains[1]` |
| 10–18 | ColourCorrectionMatrix, row-major | ×10000 | yes | `ColourCorrectionMatrix[0..8]` |
| 19 | sensor temperature | ×10 °C | optional | `SensorTemperature` |
| 20 | distance | ×100 | yes | the rung that was sent |
| 21 | effort | — | yes | `still.raw.effort` |
| 22 | DigitalGain | ×1000 | optional | `DigitalGain` |
| 23 | ColourTemperature | K | optional | `ColourTemperature` (the AWB estimate) |

- **Sentinel:** an optional value that is absent is written as **−32768**. The backend reads it as
  "missing" and uses DigitalGain = 1.0 when [22] is missing.
- **Required means required.** If any of [6]–[18] is missing, the camera does not send an nrjxl blob.
  It falls back to pjpg with `rfb=err` (SPEC §6).
- All colour values come from the **same exposure's** rpicam `--metadata` JSON. The `--raw` capture
  writes the JPEG, DNG and metadata in one run. `ColourGains` is libcamera's `[red, blue]`, with
  green = 1.
- **Forward compatibility:** new fields are **appended** (idx 24, 25, …), never reordered or
  re-scaled. The backend ignores params it does not know. A breaking change bumps `params[0]` and
  the backend refuses unknown profiles.

## 3. The curve (camera forward, backend inverse)

- Forward (study `sqrt_lut`, `b = 12`, pedestal 0): `S = 4095 / sqrt(white − black)`,
  `code = floor(sqrt(clip(raw − black, 0, white − black)) · S + 0.5)`. Raw values above `white` are
  clipped to `white` first. Built once in float64 per (black, white) and applied as an integer LUT.
- Inverse (study `sqrt_inverse`): `raw = (code / S)² + black`, in float.

## 4. crc32

`crc32 = zlib.crc32(R ‖ G1 ‖ G2 ‖ B) & 0xFFFFFFFF`, over the four payloads in container order and
**excluding the header**. It is stored in params[5] as a non-negative integer. A mismatch means the
backend refuses the blob: no render, original kept.

## 5. Backend render (neutral, `render_version` = `nrjxl-neutral-v1`)

1. Unpack and validate (§6). 2. crc32. 3. Decode the 4 planes to uint16 codes.
4. Inverse curve (§3) to float32 sensor counts. 5. Merge into the Bayer mosaic by `cfa`.
6. Normalise: `(raw − black) / (white − black)`, clipped to 0..1.
7. Demosaic (OpenCV bilinear in v1). 8. × DigitalGain, then camera WB: R × gain_R, G × 1, B × gain_B.
9. CCM (row-major, applied as `rgb_out = CCM · rgb_wb`). Per libcamera's RPi IPA documentation (not
   measured by us), this maps white-balanced, black-subtracted linear camera RGB to linear sRGB /
   Rec.709 primaries. 10. Clip to 0..1, sRGB OETF, 8-bit, JPEG q90 at the display key. Output
   px = crop px (w × h).

There is no lens shading, denoise, sharpening or tone curve, so this will not match the ISP JPEG
look (SPEC §7.3).

## 6. Backend refusals (fail loudly; the original is always kept and the row shows "no preview")

The backend refuses a blob when any of these is true:
- magic ≠ `NR`, method ≠ 14, flags ≠ 0x04, cfa > 3, b ≠ 12 or pedestal ≠ 0;
- w or h is odd or 0, w × h > 4608 × 2592, white ≤ black, or white > 65535;
- n_len ≠ 4, or header + Σ len ≠ blob length (truncated or trailing bytes);
- n_params < 24, or params[0] ≠ 1;
- crop_x or crop_y is odd, or the crop does not fit inside native_w × native_h;
- a required param is the sentinel, or a ColourGain is ≤ 0;
- the crc32 does not match;
- a payload does not decode, decodes to the wrong size or channel count, or holds a code > 4095.

## 7. Wire (SPEC §3.6)

- Filename: `<stem>_compressed.nrjxl`, e.g. `2026-10-05T17:00:41Z_image_compressed.nrjxl` (today's pjpg is
  `<stem>_compressed.jpg`; the SPEC's `_image.nrjxl` was loose). The backend keys on the `.nrjxl` extension only.
- START: `fmt=nrjxl q=<distance × 100, int> att=<attempts> cmp=1`, with keyed chunks
  `<I{key}.{n}/{M}>` as for pjpg. nrjxl is never `cmp=0`: there is no partial send.
- The pjpg fallback START carries `rfb=<code>`, a core field. The codes are `cap`, `dng`, `enc`, `mem`,
  `time`, `fit` and `err` (SPEC §3.6 table).
- Backend: MIME `application/x-nereus-nrjxl`, stored extension `.nrjxl`, `media.format = nrjxl`.

## 8. Fixtures

- Camera: 3 production blobs and their sha256 under bm `tests/fixtures/s28/` on `feature/sprint28-camera`.
  They are built from the study DNGs (rig `data/s4_20260930/`, cool/warm stop −1, `still.crop`
  `[1504, 846, 1600, 900]`) by `rc_raw_jxl`.
- Backend: these blobs are copied by hash into nvd `backend/tests/fixtures/s28/`. Until they land, the
  backend tests use blobs built by its own fixture script from the same DNG + metadata JSON, which
  follows this doc.
- Both suites decode every fixture with the rig decoder and compare its mosaic to their own.

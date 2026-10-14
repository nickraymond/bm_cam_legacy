# Progressive JPEG XL previews for nrjxl stills — design note (desk study, 2026-10-09)

Question (Nick): a customer should see first pixels while an nrjxl still's chunks arrive, as a progressive JPEG
does; today an nrjxl row shows a placeholder until all N chunks are in. Data: `runs/jxl_progressive_20261009/`
(results.csv, partial.csv, holes.csv, thumb_first.csv, tiles_*.png, run_manifest.json). 6 s28_sunrise frames
(0.3–590 lx), B3a v2 encode reproduced with the Phase-A desk copy of `rc_raw_jxl.py`, equal bytes = the unit's
byte-target search to 56,160 B (195 × 288 B), cjxl 0.11.1 e5, decode = libjxl `JxlDecoderFlushImage`.

## What the numbers say

1. **libjxl can only flush a preview once every DC group has arrived.** For today's stream that point is
   **37–53 % of the chunks (72–101 of ~192; median 82 ≈ 107 s at 1.3 s/msg)**. Before it libjxl returns no
   image at all (5/10/25 % prefixes: 0/6 frames, every VarDCT flag set). The preview is the 1/8-res DC
   (effective 1/4–1/8), 30–36 dB PSNR vs the full decode; card and scene are recognisable at 50 % in both
   tile sheets. The DC share is large because at ~0.3 bpp the DC + per-block metadata is a third to a half
   of the stream; that is intrinsic, not an ordering problem (the DC sections are already first).
2. **Flags.** `-p` (= `--qprogressive_ac`): +1.6 % bytes at equal distance (max +2.7 %), −0.1 s2 at equal
   bytes, cjxl +6 %; it does **not** move the first preview but turns the 75 % prefix into a near-final
   picture (40.7 dB / s2 69 vs 34.7 dB / s2 24 today). `--progressive_dc=1`: first preview 4–10 chunks
   earlier (35–48 %) but **−2.0 s2 at equal bytes** (the DC frame is lossy and costs quality); with `-p`
   −2.6 s2. `--progressive_dc=2`: 67 %, +33 % bytes. `--group_order=1`: no change. `-m 1 -p` (modular
   squeeze): first flush at 76 %, −4.9 s2. `--resampling=2`: first preview at 16–23 % (DC count ÷ 4) but
   −6.5 s2 at equal bytes and 1/16-res previews; not a still-quality trade we want.
3. **Holes (36/36 cases).** Stall run of 20 chunks at chunk 60: prefix-only (what the backend's
   `prefix_image_bytes` already is) → **no image** for every VarDCT variant, because 60 < the DC point
   (only `--resampling=2` previews, 31 dB). Singles at 13 and 100 → nothing (13 chunks = 3.7 kB). Feeding
   the received chunks with the gap closed or zero-filled → `JXL_DEC_ERROR`, nothing past the hole,
   confirmed. So a preview needs an **intact prefix up to the DC point**; nothing after a hole helps.
4. **Thumbnail-first (side probe, thumb_first.csv).** A 1/8-res (200×112) JPEG XL of the same B3a codes
   costs **0.4–5.7 kB = 2–20 chunks (0.7–10 % of the budget) at d 1–3** and scores 26–32 dB vs the
   reference — the same class as the DC flush, available after ~10 chunks instead of ~80. 1/4-res at d=2:
   3–9 kB (12–31 chunks), 26–34 dB. Not re-encoded at equal total bytes; the main stream would lose
   ~3–5 % in distance.

## Design

**Camera (MVP now):** add `-p` to the B3a cjxl command (`rc_raw_jxl.cjxl_rgb_command`): +1.6 % bytes,
same first preview, much better late previews. **Do not** add `--progressive_dc` (quality for 4–10 chunks)
or `--resampling`. Container v2 unchanged; `fmt=nrjxl` START unchanged.
**Camera (next sprint, the real early preview):** container **v3 = a preview payload first** (1/8-res,
d≈2, ~1–3 kB, `n_len = 2`): first pixels after ~10 chunks (~13 s), independent of the main stream's DC
point, and it survives any later hole. Costs ~3–5 % of the budget. The backend keys on `n_len`/profile.

**Backend (MVP now, no camera change needed for a 40–50 % preview):** `imagecodecs.jpegxl_decode`
refuses a truncated stream (`JXL_DEC_NEED_MORE_INPUT`); the flush path needs the C API. The imagecodecs
wheel bundles libjxl (0.12, `imagecodecs/.dylibs` / `imagecodecs.libs`), so a ctypes call works with no new
dependency: proven in `runs/jxl_progressive_20261009/tools/jxl_flush_ctypes.py`. Change: in the partial
path (`poll_once_ingest` → `create_and_upload_partial_jpeg_derivative`), branch on `nrjxl.sniff(prefix)`
(magic `NR` in chunk 0) before the HEIC/JPEG converter; parse the header from the prefix (it fits in chunk
0; the crc cannot be checked on a prefix, so this is never the original), flush-decode the codestream
prefix, run the neutral render on the result, upload as `.partial.jpg`, and label the row **"preview from
n/N chunks"** (`partial: true`, plus the libjxl downsampling 8/2/1 as `preview_detail`). Re-run on each
poll while the prefix grows; the complete row's full render replaces it as today. Refuse (keep the
placeholder) when the flush returns no image, exactly like `MIN_RECOVERED_FRACTION` does for HEIC.

**Wire:** nothing for the chunk order — the DC sections are already the first bytes; their size is the
limit. The only wire change with a payoff is the v3 preview payload above.

**Interactions.** (a) **#146 heals-after-image:** the measured loss pattern rejects START + the first
6–8 chunks of the new image when heals go first; chunk 0 missing = no decodable prefix = no preview
ever, so `heal_order: after` is a precondition for any prefix preview, progressive or not. (b) **nvd
#136/#137 (START-lost still labelled heic):** a mislabelled row routes the prefix to the HEIC partial
converter (fails → placeholder); the nrjxl branch must key on the sniffed magic, not `media.format`,
and #137's label-from-config fix makes the complete row decode. (c) The heal itself completes the row
from the tail; previews never improve from heal chunks until the prefix hole is filled.

Limits: Mac cjxl seconds (Zero ≈ 10×, `-p` +6–10 %); desk render = neutral v1 (no roll-off); the
`--resampling=2` fit hit the d=0.5 floor on s0810 (32 kB, its equal-bytes s2 is understated there).

# Sprint28 B3a B1: the two bmcam004 v2 stills decoded with the BACKEND's decoder (desk)

**Inputs:**
- The TE pulled the exact sent payloads read-only from bmcam004 (104ee3c, `still.raw.layout rgb`):
  vigilant-proskuriakova worktree, `runs/s28_ladder_20261004/pulled/B1_containers/`.
- Decoder: nvd **23a4324** (#102, B3a decode), `backend/app/services/{nrjxl,raw_render,nrjxl_dng}.py`, run in a
  scratch venv with the backend's pinned `imagecodecs==2026.8.16` (bundled libjxl 0.12.0),
  `opencv-python-headless==5.0.0.93`, numpy 2.5.3.

| still | bytes / msgs | sha256 = sent.json | header | decode | render | LinearRaw DNG |
|---|---|---|---|---|---|---|
| 19:00:38Z (backend 57901) | 53506 / 186 | YES (f23771ac…) | method 20, flags 0x06, profile 2, crop 1504,846 1600×900, d 4.35, e5, headroom 2.2323, demosaic 1, scale 10000 | OK; imagecodecs codes **bit-identical** to djxl 0.11.1 (max 4095) | PNG 1.47 MB, JPEG q90 258 kB | 8,641,192 B; **LibRaw opens it** (LinearRaw, 3 colours, WB 2.223/1/1.747; postprocess OK) |
| 20:00:38Z (backend 57914) | 53656 / 187 | YES (7f3e624c…) | same, d 4.02, headroom 2.3188 | OK, bit-identical | PNG 1.40 MB, JPEG 255 kB | 8,641,186 B; LibRaw opens it |

- **By eye:** both renders look right: natural colour, highlights rolled off, no magenta. The camera is mounted inverted on the bench, hence the upside-down view.
- **Minor, for the backend:** `nrjxl_dng.dng_size()` / `raw_info.dng_bytes` predicts 8,644,096 B; the built DNG is 8,641,192 B (2.9 kB smaller).
- Local outputs (renders PNG, DNGs) are in the camera session's scratchpad; their sha256 is in `SHA256_local_outputs.txt`.

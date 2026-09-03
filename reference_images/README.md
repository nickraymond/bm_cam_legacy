# Reference Images

Source imagery for the BM camera image-quality / compression DOE (see `sprints/`, e.g.
`Sprint06_jpeg_partial_transmission_sweep.md`). Files were renamed 2026-07-21 for clarity and
searchability; the original camera filenames are kept in the **Original** column.

| File | Role | AprilTags? | Original |
|---|---|---|---|
| `reference_card_native_imx708.jpg` | **Reference card** — high-res native IMX708 capture of the kitchen reference card. Best-quality card image; the base for all crop + downsample steps and the AprilTag-detection "quality" score. | yes | `imx708.jpg` |
| `reference_reef_coral_primary.jpg` | **Primary coral scene** — real-world reef image for realistic file size / transmission budget and sharpness/contrast/PSNR. Used for the primary analysis. | no | `P7071008.JPG` |
| `reference_reef_coral_alt_01.jpg` … `reference_reef_coral_alt_07.jpg` | **Robustness batch** — additional coral scenes to re-check results once the primary analysis is done. Not used in the first pass. | no | `P7070996, P7070997, P7071000, P7071001, P7071002, P7071003, P7071004` |
| `reference_reef_coral_card_01.jpg` | **Render-calibration reference** — Olympus surface-camera shot of the AOML deployment reef WITH the V2 card in frame (2026-09). Source of the GRVI reference-render targets: `tools/bm_grvi_calibrate_reference.py` reads its card into `tools/reference_card_color_correction/render_targets_p9.json` for `bm_grvi_correct.py --render-targets`. | yes | `P9011394` |
| `prepared/` | Prepared / derived inputs (crops, downsamples) produced by `tools/prepare_reference_images.py`. For every coral scene the committed set is `synthetic_native_4608x2592.jpg` (the sweep input) + `preparation_manifest.json`; the other prep outputs (comparison sheet, normalized original, 16:9 source) are regenerable from the originals above and are not committed. | — | — |

**Naming convention:** `reference_card_*` = AprilTag target; `reference_reef_coral_*` = reef
scenes (`_primary` = main input, `_alt_NN` = robustness batch).

# F-G3-5 daylight usable-range probe — RESULTS (`runs/s27_ip_range_probe_20261002`)

**DONE: usable ranges measured on bmcam003 in daylight, 2026-10-02 13:08 PDT.** Recommended UI/backend
limits: contrast 0.5–2.0, saturation 0.0–2.0, brightness −0.25–0.25.

Tool: `hil/tools/hil_ip_range_probe.sh` (rpicam-still, the runtime's native size/quality, every camera
control at rpicam default: auto exposure / AWB / AF, ONE image-processing flag per still). Runtime paused
13:07–13:16 PDT with cron backed up/disarmed, then re-armed + rebooted (`gate.log`). Indoor scene lit by a
daylight window. Files: `stats.csv`, `SUMMARY.md` (metric table), `contact_sheet.jpg` (thumbnails scaled
to 400x225, NOT 1:1), `thumbs/`, `base_argv.txt`.

| key | default | measured: metric blank (sd < 2, mean < 10 / > 245) | visual | **recommended range** | warn band |
|---|---|---|---|---|---|
| contrast | 1.0 | never blank, 0.25–8 (mean 125 → 83, sd 23 → 103) | 0.25 washed out; 0.5–2.0 good; ≥ 3 shadows crushed to black, ≥ 4 extreme | **0.5 – 2.0** | 2.0–3.0 (also: at NIGHT with manual exposure, 2.0 was near-black: G3 07:27Z) |
| saturation | 1.0 | blank at ≥ 6 (mean 1.3, sd 1.8) | 0–2 fine (0 = greyscale, valid); 3 strong green cast; 4 garish; ≥ 6 black | **0.0 – 2.0** | — |
| brightness | 0.0 | not blank at ±0.75 by the metric (mean 9.6 / 249.3) but visually near-blank | −0.25…0.25 good; ±0.5 very dark / washed out; ±0.75 almost blank | **−0.25 – 0.25** | ±0.25–0.5 |

Baselines (defaults, start/end): mean 107.9 / 112.8, sd 60.2 / 56.2, sat 84.9 / 74.2.

Limits: one scene, one unit, stills only (video uses the same libcamera controls; not re-measured). Auto
exposure partly compensates brightness/contrast; that is how production runs unless manual exposure is set,
in which case extremes are worse (G3 night data). The visual calls are mine (Test Engineer), from the
contact sheet.

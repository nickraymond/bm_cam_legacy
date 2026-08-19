# Sprint17 denoise sweep — bmcam003, 2026-08-19 02:28-02:33Z

Tool: `tools/sprint17_denoise_sweep.py`. Unit: bmcam003 on the Sprint17
runtime. Recorder stopped for the sweep and restarted after (clip rolling
again 02:34:48Z).

## Method

ONE variable. Held identical across all five clips: preset `wide_1080p`,
1920x1080 @15 fps, sensor mode 2304x1296, full-sensor crop, 9.3 Mbps cap,
30 s each, same camera controls, back-to-back on one unit.

The cap was held HIGH on purpose so no clip is bitrate-limited — then the
achieved bitrate becomes a reading rather than a setting.

Scene stability check: mean luma 132.0-132.8 across the whole sweep
(0.8 levels of drift). This IS a clean one-variable comparison.

## Results

| --denoise | size | achieved | % of cap | sharpness proxy | encode_s |
|---|---|---|---|---|---|
| auto (flag absent — today's default) | 34.2 MB | 9.12 Mbps | 98 % | 185.2 | 30.6 |
| off | 34.2 MB | 9.11 | 98 % | 184.1 | 30.7 |
| cdn_off | 34.2 MB | 9.13 | 98 % | 184.9 | 30.8 |
| cdn_fast | 34.2 MB | 9.11 | 98 % | 183.9 | 31.3 |
| cdn_hq | **32.2 MB** | **8.60** | 92 % | 184.6 | 31.3 |

Temps 45.6-46.7 C. All rc=0.

## Verdict: denoise is NOT the big lever we expected

On this scene the five modes are indistinguishable. Sharpness proxy spans
183.9-185.2 — a 0.7 % spread, i.e. noise in the measurement. The 1:1
native-pixel crops (`cut_sheet_denoise_1to1_frame1.png`, centre 640x640 of
the 1920x1080 frame, never resized) show no difference an eye can call.

The one real effect: **cdn_hq shaves ~6 % of bitrate at no measurable
detail cost** (8.60 vs 9.12 Mbps, sharpness 184.6 vs 185.2). That is a
small, free saving — worth taking, not worth a sprint.

## The caveat that matters

This was a well-lit indoor scene. Denoise can only remove noise that
exists, and a clean scene gives it nothing to do. The hypothesis in
TODO-BM-013 — that denoise is "likely a big lever for underwater
particulate scenes" — is NOT disproven here; it is untested, because the
test scene had no particulate and little sensor noise.

A fair test needs the condition denoise is for: low light / high analogue
gain, or real turbid water. Until then the honest statement is: **denoise
does nothing measurable on a clean, well-lit scene, and cdn_hq is a free
~6 % bitrate saving.**

## Note on scene complexity vs the earlier A/B

At 20:34Z the same unit at the same 9.3 Mbps cap achieved only 5.42 Mbps
(58 % of cap); here it reaches 98 %. Same camera, same settings, different
hour. That is the overnight run's finding #1 again, and it is the reason
the A/B methodology insists on paired simultaneous clips: sequential
comparisons across hours measure the scene, not the setting.

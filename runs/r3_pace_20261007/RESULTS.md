# R3-PACE — RESULTS (bmcam003 / SPOT-33507C)
Gate: `hil/gates/R3_PACE.md`. Arms A = 1.0 s, B = 1.3 s; delay 0; reef config; self_heal OFF; no commands.

| wake (Z) | PDT | arm (delay_s) | msgs | loss % | gaps | max gap | queue_full | stalls n/longest/rejects | HDR crossed → HDR/other | START | END | wake→halt | clean | flag |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 16:00 | 09:00 | A (1.0) | 170 | **18.82** (32/170) | 45×10, 124×10, 158×12 | 12 | 33 (16:01:24–16:03:31; END not on the console) | 11 / 43.9 s / 33 of 33 | none → HDR 0 / other 33 | 16:00:35 | ~16:03:31 | 376 s | no | – |
| 17:00 | 10:00 | B (1.5) | 188 | **12.77** (24/188) | 34×7, 86×7, 163×7, 176×3 | 7 | 24 (17:01:29.8–17:05:08.6) | 13 / 34.3 s / 24 of 24 | crossed 17:05:01 → HDR 3 / other 21 | 17:00:34 | 17:05:22 | 486 s | no | **crossed HDR** |

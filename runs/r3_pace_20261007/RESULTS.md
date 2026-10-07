# R3-PACE — RESULTS (bmcam003 / SPOT-33507C)
Gate: `hil/gates/R3_PACE.md`. Arms A = 1.0 s, B = 1.3 s; delay 0; reef config; self_heal OFF; no commands.

| wake (Z) | PDT | arm (delay_s) | msgs | loss % | gaps | max gap | queue_full | stalls n/longest/rejects | HDR crossed → HDR/other | START | END | wake→halt | clean | flag |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 16:00 | 09:00 | A (1.0) | 170 | **18.82** (32/170) | 45×10, 124×10, 158×12 | 12 | 33 (16:01:24–16:03:31; END not on the console) | 11 / 43.9 s / 33 of 33 | none → HDR 0 / other 33 | 16:00:35 | ~16:03:31 | 376 s | no | – |
| 17:00 | 10:00 | B (1.5) | 188 | **12.77** (24/188) | 34×7, 86×7, 163×7, 176×3 | 7 | 24 (17:01:29.8–17:05:08.6) | 13 / 34.3 s / 24 of 24 | crossed 17:05:01 → HDR 3 / other 21 | 17:00:34 | 17:05:22 | 486 s | no | **crossed HDR** |
| 18:00 | 11:00 | B (1.5) | 189 | **16.93** (32/189) | 3×7, 14×7, 80×7, 146×7, 170×4 | 7 | 32 (18:00:52.3–18:05:10.7) | 17 / 51.0 s / 32 of 32 | crossed 18:05:01 → HDR 11 / other 21 (HDR count includes stalls < 60 s after the 18:00:01 HDR) | 18:00:44 | 18:05:33 | 496 s | no | **crossed HDR** |
| 19:00 | 12:00 | A (1.0) | 185 | **11.35** (21/185) | 4×10, 24×1, 115×10 | 10 | 21 (19:00:50.7–19:02:53.0) | 14 / 53.6 s / 21 of 21 | none crossed → HDR 10 (after the 19:00:0x bus-on HDR) / other 11 | 19:00:43 | 19:03:53 | 397 s | no | – |
| 20:00 | 13:00 | A (1.0) | 168 | **6.55** (11/168) | 7×10, 87×1 | 10 | 11 (20:00:53.8–20:02:15.3) | 8 / 44.1 s / 11 of 11 | none crossed → HDR 9 (after the 20:00:01 bus-on HDR) / other 2 | 20:00:43 | 20:03:36 | n/a (Pi not checked) | no | – |
| 21:00 | 14:00 | B (1.5; spacing 1.52 s on the console, switch recorded by hand) | 186 | **5.91** (11/186) | 102×7, 171×4 | 7 | 11 (21:03:21.5–21:05:10.9) | 5 / 34.6 s / 11 of 11 | crossed 21:05:01 → HDR 4 / other 7 | 21:00:43 | 21:05:27 | n/a (halted at 21:14Z) | no | **crossed HDR** |
| 22:00 | 15:00 | B (1.5; pacing line delay_s=1.5; 169 msgs / 258.2 s burst = 1.53 s) | 169 | **4.14** (7/169) | 85×7 | 7 | 7 (22:02:54.7–22:03:03.8) | 4 / 37.6 s / 7 of 7 | none crossed (END 22:05:00, HDR 22:05:01.99) → HDR 0 / other 7 | 22:00:42 | 22:05:00 | 466 s | no | – (END 2 s before the :05 HDR) |

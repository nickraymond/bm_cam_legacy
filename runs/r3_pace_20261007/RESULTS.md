# R3-PACE — RESULTS (bmcam003 / SPOT-33507C)
Gate: `hil/gates/R3_PACE.md`. Arms A = 1.0 s, B = 1.5 s (header said 1.3 until 10/8; B was 1.5 throughout); delay 0; reef config; self_heal OFF; no commands.

| wake (Z) | PDT | arm (delay_s) | msgs | loss % | gaps | max gap | queue_full | stalls n/longest/rejects | HDR crossed → HDR/other | START | END | wake→halt | clean | flag |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 16:00 | 09:00 | A (1.0) | 170 | **18.82** (32/170) | 45×10, 124×10, 158×12 | 12 | 33 (16:01:24–16:03:31; END not on the console) | 11 / 43.9 s / 33 of 33 | none → HDR 0 / other 33 | 16:00:35 | ~16:03:31 | 376 s | no | – |
| 17:00 | 10:00 | B (1.5) | 188 | **12.77** (24/188) | 34×7, 86×7, 163×7, 176×3 | 7 | 24 (17:01:29.8–17:05:08.6) | 13 / 34.3 s / 24 of 24 | crossed 17:05:01 → HDR 3 / other 21 | 17:00:34 | 17:05:22 | 486 s | no | **crossed HDR** |
| 18:00 | 11:00 | B (1.5) | 189 | **16.93** (32/189) | 3×7, 14×7, 80×7, 146×7, 170×4 | 7 | 32 (18:00:52.3–18:05:10.7) | 17 / 51.0 s / 32 of 32 | crossed 18:05:01 → HDR 11 / other 21 (HDR count includes stalls < 60 s after the 18:00:01 HDR) | 18:00:44 | 18:05:33 | 496 s | no | **crossed HDR** |
| 19:00 | 12:00 | A (1.0) | 185 | **11.35** (21/185) | 4×10, 24×1, 115×10 | 10 | 21 (19:00:50.7–19:02:53.0) | 14 / 53.6 s / 21 of 21 | none crossed → HDR 10 (after the 19:00:0x bus-on HDR) / other 11 | 19:00:43 | 19:03:53 | 397 s | no | – |
| 20:00 | 13:00 | A (1.0) | 168 | **6.55** (11/168) | 7×10, 87×1 | 10 | 11 (20:00:53.8–20:02:15.3) | 8 / 44.1 s / 11 of 11 | none crossed → HDR 9 (after the 20:00:01 bus-on HDR) / other 2 | 20:00:43 | 20:03:36 | n/a (Pi not checked) | no | – |
| 21:00 | 14:00 | B (1.5; spacing 1.52 s on the console, switch recorded by hand) | 186 | **5.91** (11/186) | 102×7, 171×4 | 7 | 11 (21:03:21.5–21:05:10.9) | 5 / 34.6 s / 11 of 11 | crossed 21:05:01 → HDR 4 / other 7 | 21:00:43 | 21:05:27 | n/a (halted at 21:14Z) | no | **crossed HDR** |
| 22:00 | 15:00 | B (1.5; pacing line delay_s=1.5; 169 msgs / 258.2 s burst = 1.53 s) | 169 | **4.14** (7/169) | 85×7 | 7 | 7 (22:02:54.7–22:03:03.8) | 4 / 37.6 s / 7 of 7 | none crossed (END 22:05:00, HDR 22:05:01.99) → HDR 0 / other 7 | 22:00:42 | 22:05:00 | 466 s | no | – (END 2 s before the :05 HDR) |
| 23:00 | 16:00 | A (1.0; hand-recorded, YAML 1.0 read live 22:02Z; 179 msgs / 183.5 s burst = 1.03 s) | 179 | **0.56** (1/179) | 138×1 | 1 | 1 (23:03:05.3) | 5 / 48.5 s / 1 of 1 | none crossed → HDR 0 / other 1 | 23:00:41 | 23:03:45 | 386 s | **yes** | – |
| 00:00 (10/8) | 17:00 | A (1.0; pacing line delay_s=1.0 via the driver; 189 msgs / 193.6 s = 1.02 s) | 189 | **6.35** (12/189) | 33×1, 133×10, 153×1 | 10 | 12 (00:01:17.1–00:03:19.4) | 13 / 49.5 s / 12 of 12 | none crossed → HDR 0 / other 12 | 00:00:40 | 00:03:54 | 396 s | no | – |
| 01:00 (10/8) | 18:00 | B (1.5; pacing line delay_s=1.5; 189 msgs / 288.6 s = 1.53 s) | 189 | **3.70** (7/189) | 168×7 | 7 | 8 (01:04:58.5–01:05:07.6) | 3 / 42.7 s / 8 of 8 | none crossed (END 01:05:28; no :05 HDR logged this hour) → HDR 0 / other 8 | 01:00:39 | 01:05:28 | 495 s | no | – |
| 02:00 (10/8) | 19:00 | B (1.5; pacing line delay_s=1.5; 180 msgs / 275.0 s = 1.53 s) | 180 | **6.11** (11/180) | 77×7, 174×4 | 7 | 11 (02:02:39.2–02:05:11.2) | 5 / 45.2 s / 11 of 11 | crossed 02:05:02 → HDR 4 / other 7 | 02:00:38 | 02:05:13 | 475 s | no | **crossed HDR** |

## VERDICT (2026-10-08 ~02:25Z / 7:25 PM PDT): STOPPED at the interim, **1.0 s final**

The EM stopped the run per Nick's rule ("switch unless the difference is meaningful").

- Data at A5/B6:
  - median first-send loss: A 6.55 % vs B 6.01 % (B −0.54 pp);
  - mean: 8.73 % vs 8.26 %;
  - exact one-sided Mann-Whitney p = 0.33 (U 18/30).
- No meaningful difference. The pre-registered STOP rule (B median ≥ A median) was technically not met; the EM/Nick
  decided on effect size.
- 1.5 s bursts run ~275–290 s and crossed the :05 HDR in 4 of 6 B wakes (17Z, 18Z, 21Z, 02Z).
- bmcam003 stays on 1.0 s (base YAML `uplink.msg_interval_s: 1.0`, written at the 02Z switch).
- Switch driver and scoring stopped at ~02:25Z. 03Z (8 PM) was not scored.
- Still pending from TE1's restore list:
  - self_heal is still OFF for SPOT-33507C. REEF-RC turns it ON (the EM flips it at REEF-RC wake 1).
  - The R2 single-file patch stays until the REEF-RC deploy replaces the runtime.

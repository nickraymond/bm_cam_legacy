# R2-DELAY — RESULTS (bmcam003 / SPOT-33507C)

Gate: `hil/gates/R2_DELAY.md`. Setup 2026-10-07 05:00Z (patch f1dfe38 installed, sha 544f48b3; pacing 1.0 s; effective
config in `pulled/bmcam003_effective_setup.txt`). 06:00Z wake = setup (driver trg + setup command), not counted.

| wake (Z) | PDT | arm | loss % | gaps (start idx × len) | max gap | queue_full (first–last) | START | burst end | wake→halt | clean |
|---|---|---|---|---|---|---|---|---|---|---|
| 07:00 | 00:00 | A | **12.75** (13/102) | 61×1, 65×12 (07:01:45–07:02:03) | 12 | 13 (07:01:47.8–07:02:03.0) | 07:00:43 | 07:02:28 | 308 s | **no** |

07Z note: no Spotter-own event in the loss window (health check 07:03:22, HDR 07:05:01). The Spotter → Notecard hand-off
(`Added` → `Queuing message`) stalled from 07:01:45: drain 0.05–0.08 s before, then 2.7 / 2.5 / 1.3 / **12.6 / 13.3 s**,
back to 0.08 s by 07:02:09. Wire len 528 B per chunk (384 chars + keyed framing) — between T2's fast (≤ 450 B) and
slow (≥ 600 B) path sizes. heal_msgs / budget_left of this wake: from its cycle log at the 08Z window.

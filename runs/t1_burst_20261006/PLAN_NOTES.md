# T1 plan as registered (2026-10-06 ~15:45Z, before the first burst)

SPOT-33507C sync timing read from the nereus000 console 14Z/15Z: health check `Running health check!` ~:03:33 →
sync → `All messages sent successfully!` ~:04:18; hourly report queued :10:00 → sync → done ~:10:42.

**Deviation from the gate doc (stated before the first burst):** a burst lasts 120 × 1.3 s = 156 s, so the a-overlap
burst (:09:30 → :12:06) would still be running at "report sync end + 45 s" (:11:27). b-settle therefore uses the
OTHER hourly sync: the health check (end ~:04:18 + 45 s = **:05:03**). a-overlap stays on the report sync (**:09:30**).
Clear slots :25/:45 steady, :35/:55 pair (no health check in them). n = a 3, b 3, clear-steady 6, clear-pair 6.
Bars unchanged (gate doc). `hil-r1-cmdres.timer` stopped 15:4xZ (it would send set/trg to BMCAM_003 at :20).

## Incident 001 — ack 805 lost device→backend (2026-07-27 ~19:36Z, confirmed 22:00Z)
Device applied+acked 805 and 806 ~1 s apart (cycle_20260727T193604Z.log, paced 1.0 s).
Backend shows acks 801, 802, 804, 806 — 805 never arrived after 2 h+.
Classification: silent Spotter 2-slot queue drop (Sprint09-documented mode), first
quantified instance this soak. Ack-path loss ≈ 1/6 so far — within the ≥95 % target
only if rare; tracking cumulative rate in the report.
Mitigation already in design: acks carry FULL state (D4) — 806's st shows exp=4, so
805's outcome is still operator-visible. GUI lifecycle for a lost-ack command stays
"awaiting node" → operator guidance: re-send (dedupe-safe) or read st from any later ack.

## Incident 002 — image chunk loss <100% (reported by Nick from website, 21:15Z; quantified)
Backend ground truth (soak_reconcile, independent of website parser):
- bmcam000 OLD pre-update code, hourly cycles 13:01-19:01Z: 5 of 7 cycles missing 1-3
  chunks (~1-2% loss); 2 complete. NOT a new-code regression.
- bmcam003 NEW code, back-to-back outdoor cycles 20:36-21:09Z: 8/8 incomplete, losses
  1-47 chunks (0.5-26%), including consecutive-run gaps (1-7, 15-21, 30-36, 113-116)
  = queue-overflow burst signature under sustained ~180 msgs/5 min cadence.
- Zero unparseable rows in sweeps -> parsing (ours and site's) ruled out; chunks are
  absent from the backend, lost in the Spotter-queue -> Notecard -> cloud leg.
OPEN: Pi-side per-chunk send logs to prove device emitted every index (bmcam003 WiFi
down since ~20:47Z outdoors — unit still cycling on cellular; logs on SD. bmcam000:
grab cron_logs at a wake). Field-cadence takeaway: hourly cycles ≈98-100% chunks/image;
back-to-back hammering makes loss much worse — report will split metrics by cadence.

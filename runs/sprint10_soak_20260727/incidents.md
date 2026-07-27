## Incident 001 — ack 805 lost device→backend (2026-07-27 ~19:36Z, confirmed 22:00Z)
Device applied+acked 805 and 806 ~1 s apart (cycle_20260727T193604Z.log, paced 1.0 s).
Backend shows acks 801, 802, 804, 806 — 805 never arrived after 2 h+.
Classification: silent Spotter 2-slot queue drop (Sprint09-documented mode), first
quantified instance this soak. Ack-path loss ≈ 1/6 so far — within the ≥95 % target
only if rare; tracking cumulative rate in the report.
Mitigation already in design: acks carry FULL state (D4) — 806's st shows exp=4, so
805's outcome is still operator-visible. GUI lifecycle for a lost-ack command stays
"awaiting node" → operator guidance: re-send (dedupe-safe) or read st from any later ack.

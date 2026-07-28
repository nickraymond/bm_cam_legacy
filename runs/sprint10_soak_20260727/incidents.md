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

## Incident 003 — Spotter→cloud delivery STALL under sustained load (21:14–21:57Z)
Device-side alibi is airtight: cycles 10–17 (21:22–21:55Z) each logged
"transmit done: sent=N/N complete=True", ~1300 messages accepted by the bus with
zero device errors — yet the backend's last row is 21:09:31Z. The Spotter accepted
and did not forward for ~45 min. After Nick's ebox power cycle: sdmq size = 0
(SD queue empty — messages either in Notecard flash pending sync, or dropped live;
power cycle destroyed the distinction). cellularErrorState OK throughout.
Trigger correlation: sustained ~37 msg/min for ~80 min (back-to-back cycles),
outdoors. The field cadence (hourly ~180-msg bursts) has NEVER shown a stall —
only the 1–2 % chunk loss of incident 002.
ACTIONS: (a) soak loop throttled to 1 cycle/15 min (sustainable), (b) watch later
sweeps for straggler delivery of the 21:14–21:55 backlog (would prove Notecard
buffering, and exercise gid straggler recovery), (c) report flags sustained-rate
ceiling as a Sofar question. Wednesday field risk: LOW (customer cadence is hourly).

## Deploy record — media gid live on bmcam003 (22:00Z)
rc_media_id.py + updated rc_uplink_messages/rc_transmit/rc_progressive_jpeg
scp'd (backup /home/pi/backups/pre_media_gid_*.tgz), py_compile OK, media_gid
island enabled in live YAML. bmcam003 = gid-format A/B unit; bmcam000 stays
legacy. Deployed from branch (not yet merged) — bench mule only, per Nick's
approval of the A/B plan.

## Incident 004 — bmcam000 stuck dark: Spotter not restoring bus power (20:44Z→)
Node halted 20:44Z after the manually-invoked field cycle (cron-runner path,
power_halt real). Spotter never restored bus power: no SSH, no backend activity
(last image 20:41Z), 2+ expected hourly wakes missed. Hypotheses: (a) power
controller schedule differs from assumed 20/40 (SoC/night gating?), (b) the
mid-window MANUAL halt confused the controller's node-monitor state (it may
only cycle power around ITS OWN on-windows), (c) its Spotter did the ~60-min
post-boot source-7 reset and wedged. No remote fix available (no console on
that Spotter; cloud commands can't return output). Nick paged 23:12Z. Cloud
mailbox holds 900/901 (+ scheduled 902...) — no expiry, dedupe-safe whenever
it wakes. Overnight bmcam000 soak stalled until power returns.

## Incident 005 — Spotter (33507C) self-reset ~60 min after every boot (pattern confirmed)
Boot 16:16Z → reset 17:21Z (65 min). Boot 21:57Z → reset ~22:57Z (60 min).
First post-boot ORC health check votes "rebootctl reset 2. Source: 7"; later
votes suppressed by "Reboot limit reached". Every reset cuts BM bus power =
node hard power cycle. Field impact: one spurious node power-cycle per Spotter
boot (rare in field); bench impact: constant disruption. Sofar support thread
material — console captures in runs/sprint10_phaseC_20260727/.

## Incident 003 — RESOLVED (23:35Z): Notecard auto-sync stall, not message loss
Root cause: the Notecard stopped auto-syncing ~21:14Z (reached 25% full, was 2-5%
all morning) while accepting new notes. Cellular + signal OK throughout. A forced
`note sync` (23:30:56Z) drained 25% -> 2% in ~4 min; ~2200 backlogged rows landed
at the backend including the "lost" cycles. Post-drain cycle 23:27Z delivered
130/130 COMPLETE — loss (4-5% in backlogged material) correlates with Notecard
congestion, not radio/device. The ebox power cycle at 21:57Z did NOT clear the
stall (Notecard preserves backlog + state across host power cycles).
Residual: WHY auto-sync stalls under sustained inbound load = Sofar/Blues question
for the support thread. Bench workaround live: forced sync every 15 min.
Distinct from ack-805 (19:36Z, synced era) — that remains a true Spotter-queue drop.
Field note: hourly cadence never triggered the stall; risk is sustained bursts.

## FINDING 006 — mailbox drains chase the cycle tail: commands land in daemon-down gaps (00:01Z)
DEFINITIVE queue-while-off answer (Nick's Bridge-gating question): the Spotter does
NOT hold BM traffic for the node. Observed live, both units, same mechanism:
- bmcam003: cmds 812/813/804dup/814 drained 23:58:38-00:00:51Z — cycle 5 had ended
  23:57:45Z (frames=0). All four consumed from the mailbox and silently lost.
  Reset cmd 815 likewise at 00:01:36Z. Cause: the drain is triggered by the sync
  that our own transmit initiates, so it fires ~1-4 min AFTER the cycle ends.
- bmcam000 first 20/10 wake: frames=0 — pings 900/901/902 not delivered during
  the wake window either.
MITIGATION (design already supports): re-send same ids on missing ack — dedupe
makes it free; executed live for 812/813/804/814 at 00:02Z. Bench test-side fix:
soak loop gap 600->120 s (~65% daemon coverage). FIELD RECOMMENDATION for the
report: lengthen pre_capture_listen_s and/or add a short post-transmit listen
tail (bounded, e.g. 120-180 s) in a post-freeze sprint — the drain predictably
arrives 1-4 min after transmit start; a listen tail converts most gap losses
into same-wake applies. GUI operator guidance: "awaiting node" past 2 wake
cycles => re-send.

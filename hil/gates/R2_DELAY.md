# R2-DELAY — does holding the first send until uptime ≥ 230 s change first-send loss? (bmcam003, reef config)

Card from the EM (Nick decided in the EM chat 2026-10-06 21:55 PDT): the RC is delayed; the dropped-message issue
comes first; prototype the delay by hand on the bench (no remote key, no Spotter-UTC fix); no SAMI/soft-temp emulation
(the bench without those nodes is the best case). Owner: Test Engineer. Evidence: `runs/r2_delay_20261006/`. Times
UTC unless PDT. **Rules below are fixed before the first counted wake.**

**Question:** with the reef buoy's camera config, does holding the first uplink send until Pi uptime ≥ 230 s
(capture/compress happen during the wait) change first-send loss vs no delay?

## Unit and fixed conditions

bmcam003 / SPOT-33507C only (it matches the reef: WBGLW Notecard, report at :10, ticks 0, bus 10 min from :00).
Held fixed: still every wake; pjpg; reef geometry (crop 1504,846,1600,900 → 1000 px wide); still.message_cap 195;
384 chars; cellular-only 0x02; **pacing 1.0 s** (reef value; set locally in the base YAML, backup kept); production
per_boot + halt; `hil-r1-cmdres.timer` OFF (stopped 2026-10-07 04:5xZ); **no commands during the test** (an inbound
command adds ~40 s to the sync hold). Lane guard OFF on bmcam003. bmcam004 is not part of this card.
Setup command (before the first counted wake, the only one): `set mode.media=still` cid 1000159 (supersedes the
driver's 1000157 video). The driver's trg 1000158 + 1000157/1000159 land at the 05:10Z sync → the **06:00Z wake is
setup, not counted**; mode.media=still applies from the 07:00Z boot.

## Prototype (bench only)

Branch `hil/r2-delay-prototype` f1dfe38 = development a50636e (bmcam003's runtime) + 32 lines in
`rc_progressive_jpeg.py`: right before the first uplink send (heals before START, then START; after capture and
encode), sleep until `/proc/uptime` ≥ START_DELAY_S read from `<app>/r2_start_delay_s` (absent file = no-op, no output;
budget-checked like the C2 lane wait; logs `[R2DELAY] start_delay_s=… uptime=… wait=… burst_est=… skipped=…`).
Installed as a single-file copy over the deployed module (original backed up); switching arms = editing that one file.
The file is read once per wake at the send decision (~uptime 60 s), so an arm switch is written during the PREVIOUS
wake after its send has started (it then applies to the next wake only).
Restore: copy the backed-up `rc_progressive_jpeg.py` back, delete `r2_start_delay_s`, restore the YAML backup
(pacing 1.3 s), restart the cmdres timer if the EM wants it.

## Arms, order, n

A = 0 s (no hold), B = 230 s. ABBA blocks from 07:00Z: A 07, B 08, B 09, A 10 | A 11, B 12, B 13, A 14 | A 15, B 16,
B 17, A 18 → up to 6 per arm.

## Per wake (hil/tools/hil_r2_score.py + hil_wake_report.sh + hil_sync_profile.py)

first-send loss % (chunks of the wake's key the Spotter accepted vs START length), contiguous gaps (start index, length,
time), queue_full times vs the Spotter's own events (HDR messages — every 5 min at :x4:59/:x9:59 on this Spotter —
health check, :10 report/sync), burst start/end, wake→halt, `[R2DELAY]` line (from the cycle log at the next window,
read-only) or the START uptime.
Clean wake = first-send loss ≤ 2 % AND no contiguous gap ≥ 5 chunks.

## Pre-registered decision rules

1. After 2 wakes per arm (first ABBA block): if ALL 4 are clean → **STOP**. Answer: "reef config on this Spotter has
   no holes; the delay costs nothing measurable".
2. If A shows holes and B does not → continue to 6 per arm; **PASS** if B median loss ≤ 1 % AND one-sided
   Mann-Whitney (B < A) p < 0.10.
3. If B shows holes that A does not → **STOP** (the delay hurts).
(Mixed outcomes not covered above → report to the EM after the block, no unilateral continuation.)
**Applied check (added 2026-10-07 05:1xZ, before the first counted wake, EM informed):** the patch drops the hold
when wait + burst (incl. heals sent before START) exceeds the per_boot budget left (`[R2DELAY] … skipped=True`, the
same rule that made bmcam004's C2 lane skip on heal wakes). A B wake with skipped=True is reported as **B not applied**
and is not counted as B unless the EM decides otherwise; heals per wake are logged.
**Safety stop:** any wake whose burst ends after :09:00 or whose wake→halt > 570 s.

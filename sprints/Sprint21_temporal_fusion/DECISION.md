# Sprint 21 — decision memo

**To:** Nick · **2026-09-05** · Evidence in `runs/sprint21_day{0..4}_*/REPORT.md`

---

## The four answers

**A. Can the daily frames be combined into a richer image?** Yes, within one day. Scored
against a known answer, the fused composite beats the *best* single frame, not just a
typical one (SSIM 0.8824 against 0.8735) and recovers detail closest to truth. It is
cleaner, more complete and more reproducible. It is not higher resolution; that needs
Sprint 20.

**B. Nereus v3 per frame or once at the end?** Fuse the raw frames, then correct once. The
simplest option, one card read instead of five. Honest caveat: with three days the four
orderings are not statistically separable, so this is chosen on best mean plus lowest cost.
What the data does firmly say is which to avoid: card-anchored per-frame normalisation is
worst and least stable, because the card sits on bright sand and does not track the shaded
coral wall.

**C. What process exploits the fixed camera?** One composite per day, plus the stability map
that falls out of it, which is the only place a change flag can ever be trusted. Full
recipe and settings in `SPEC.md`.

**D. Can we detect bleaching?** As a flag, not a diagnosis, and not yet. Pilot sensitivity
is a sustained blue-over-green rise of about 10 % on a colony of 3,000 pixels or more,
against a two-to-four week baseline. The pipeline destroys about a third of any real change
before it reaches us, and changes under 10 % are destroyed entirely.

## The finding nobody asked for

**bmcam001 has not delivered a single complete image since at least 2026-09-01.** Twenty-four
consecutive cycles. Invisible because a truncated progressive JPEG still renders as a full
picture. The Pi sends everything; roughly 45 messages per cycle never arrive, and the cut is
time-aligned at 177–180 s into every 5-minute lane. Your call: a Spotter timing bug, tracked
as TODO-SPOT-001.

This is the largest lever on everything above. Every frame in this sprint was a partial.

## What I recommend

1. **Ship the daily composite** as a backend derivative next to the frames. It is a clear,
   low-risk quality win, and the customer-visible improvement is immediate.
2. **Start banking the baseline now**: daily composites, real colony outlines from someone
   who knows these colonies, and coral health chart readings on every dive. Sensitivity
   improves with colonies and ground truth far more than with time.
3. **Do not ship change flags.** Three days cannot set a threshold, and the null is
   unverified — early September is peak thermal stress in the Keys, so ask AOML whether
   anything was happening at Cheeca on 09-02 to 09-04. If it was, the floor is overstated.
4. **Fix the Spotter timing bug before anything else.** Complete frames raise the transfer
   directly and improve every number here.

## Still yours to decide

| # | Decision | My recommendation |
|---|---|---|
| 1 | Internal memo, or a gallery feature? | Gallery feature for the composite; memo for the change work |
| 2 | Composite cadence | End of day, plus a rolling 7-day view once the baseline exists |
| 3 | Who draws colony outlines | AOML — they know these colonies by name |
| 4 | Schedule Sprint 20 this month? | Yes, after the Spotter fix; it is the second-largest lever |
| 5 | Alert policy | Defer until a 3–4 week baseline exists |
| 6 | Permission to ask AOML for records and health-chart readings | Recommend yes, this week |
| 7 | Commit and open a PR into `development` | Your call; nothing is committed yet |

## Defensible customer wording

The composite: "A cleaner daily view of the reef, built from the day's frames. Haze,
particles and passing fish are averaged out. It is not sharper than the camera's
transmitted resolution."

The flag, once a baseline exists: "Marks a sustained rise in blue-over-green on a marked
colony against its own multi-week baseline. Pilot sensitivity is roughly a 10 % change on a
colony of 3,000 pixels or larger, persisting across at least two daily composites. It means
worth a diver check. It is not a bleaching diagnosis, it has not been evaluated against
ground truth, and algal turf, sediment, turbidity and lens fouling can trigger or mask it."

Do not use the phrase "bleaching detection".

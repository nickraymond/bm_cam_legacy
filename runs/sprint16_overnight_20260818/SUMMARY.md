# Fleet overnight HIL — 2026-08-18 08:06Z → 15:07Z (~7 h), sprint16 build (a4f0da7-sprint16-rsync)

All three units, continuous video, client WiFi (nereus-hq), same build.
Configs differ by Nick's quality experiments (set via GUI ~07:24-07:45Z,
before the fleet reboot):

| Unit | Config (5-min clips) | Clips | Boundary gap s (min/med/max) | Temp °C max | Failures | Debris |
|---|---|---|---|---|---|---|
| bmcam000 | 15 fps @ 2 Mbps | 82 | 4 / 5 / 11 | 42.4 | 0 | 0 |
| bmcam004 | 15 fps @ 4 Mbps | 81 | 4 / 5 / 23 | 41.9 | 0 | 0 |
| bmcam003 | 30 fps @ 8 Mbps | 79 | 5 / 7 / 45 | 42.9 | 0 | 0 |

## Verdict

- **No drops.** Cadence continuous on all three; every inter-clip gap
  accounted for by mux/boundary work (below). No missing intervals.
- **No lost videos.** manifest count == mp4 count on all three
  (117/133/115); exactly one in-flight .part each; zero .tmp; zero
  "clip failed"; zero ERROR/Traceback lines in 7 h of cron logs.
- **Network rock-solid.** Zero network events after the 08:07Z boot
  join on all three (network_ap.log silent all night) — Sprint16 boot
  path + client mode steady.
- **Ring:** 0 deletions (usage 8-14%, far from the 75% cap).

## Findings (explained anomalies, not failures)

1. **Clip sizes track daylight, not just the bitrate setting.** All
   night the dark office undershot the encoder targets (003 averaged
   ~1.4 Mbps despite its 8 Mbps cap). At ~13:20Z (≈6:20 a.m. local,
   lights/sunrise) scene complexity rose and clips jumped to their
   caps: 003 → ~300 MB/clip (8 Mbps), 004 → 150 MB once (4 Mbps),
   000 → 75 MB (2 Mbps). Bitrate is a ceiling, not a floor.
2. **Boundary gaps grow with file size** (D-S15-2 per-clip mux):
   ~5 s at ≤75 MB, ~23 s at 150 MB, 33-45 s at 300 MB (17 such gaps on
   003 after daylight) — ~13 % dead time at 30 fps/8 Mbps. Cost of the
   crash-safe per-clip architecture on Zero 2W SD; input for
   TODO-BM-013 (GoPro-class bitrates will pay this unless the mux is
   optimized or accepted).
3. **Storage burn at daylight rates:** 003 at 8 Mbps ≈ 86 GB/day in
   daylight — its ring will begin pruning within ~a day if left (by
   design, but the footage window shrinks to ~1 day).
4. **Free A/B corpus for today's quality work:** same build, same
   scene class, three bitrate/fps points (2/15, 4/15, 8/30) sitting in
   the three galleries — direct eyeball comparison material for the
   1080p preset design. Note 003's 30 fps halves per-frame bits vs
   15 fps at the same bitrate; for crispness, 15 fps is the likely
   ship point.

## Files

clips_<unit>.csv (per-clip sidecar rollup), network_<unit>.log (tails),
this summary. Analysis window: sidecar utc >= 2026-08-18T08:06Z.

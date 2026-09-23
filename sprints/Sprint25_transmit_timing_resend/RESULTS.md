# Sprint25 — transmit timing over the BM bus (part 1) — RESULTS

Written 2026-09-22 (Sprint25 session). Every number below comes from the two Spotter SD
cards pulled 2026-09-22 15:41Z (`runs/sprint25_sd_20260922T1640Z/`, card contents
git-ignored), joined with Sofar (`SOFAR_API_TOKEN_BM_REEF`) and staging. Tool:
`tools/bm_video_soak_report.py` (extended this sprint: `--sd-log`, the Sprint24 step-7
columns, `loss_stage`, SVG cut sheet). Camera `rc_cycle_*.log` files were NOT available
(units halted between cycles) — the SD `BM_TX.log` supplied the burst windows instead.

## Rigs (pre-swap; the cards cover 2026-09-21 18:00Z → 2026-09-22 15:41Z)

| Rig | Spotter | Notecard | Camera / node | Backend | Boot (SD insertion reset) | Hourly report | Health check |
|---|---|---|---|---|---|---|---|
| A | SPOT-33507C | NOTE-WBGLW fw 6.2.5 | bmcam004 `0xe6fe83ea6b4a2b7f` | BMCAM_004 | 18:01:09Z, self-reset 19:01:11Z (`rebootctl reset 2. Source: 7`) | **:05:00** | **:01:09** |
| B | SPOT-31593C | NOTE-WBNA-500 fw 4.2.1 | bmcam003 `0x53171fa3d81a8e6f` | BMCAM_003 | 18:02:15Z, no resets | **:10:00** | **:02:0x** |

Bus schedule on both (committed 20:06:57Z): 960 s interval / 600 s duration /
`alignmentInterval5Min 1` → windows on :00/:20/:40, bus on for 592 s. Cycle shape as
measured on the SD: bus on → capture at boundary **+30..+40 s** → burst (START) at
**+57..+66 s** → last chunk at **+215..+226 s** (158 s of UART at 1 msg/s, ~125 chunks +
~30 keyframe repeats) → halt. **80 s of margin before the next 5-minute boundary.**

## 1. Where does loss fall (video cycles only)

| | Rig A (SPOT-33507C) | Rig B (SPOT-31593C) |
|---|---|---|
| video bursts on the SD | 62 | 49 |
| complete at Sofar (all chunk indices) | 33 (53 %) | 30 (61 %) |
| staging: complete / partial rows, mean `percent_received` | 33 / 26, 97 % | 30 / 18, 97 % |
| cycles with 1–5 chunks rejected by the Spotter (baseline) | 23 | 16 |
| chunks lost that way | 49 | 22 |
| cycles hit by a LEGACY report inside the burst | 2 (health-check alerts 03:01, 04:01) → 27 + 30 chunks | 0 |
| cycles cut by bus-off / reset mid-burst | 1 (20:07 commit window) + 2 whole clips lost to the 19:01 self-reset | 2 (20:07 commit, 13:28 commit) |
| cycles with an HDR push inside the burst | 1 (18:54, the pre-alignment cycle at phase +271 s) | 0 |
| whole clip accepted by the Spotter but absent at Sofar | 0 | 1 (15:20 clip; card pulled at 15:41 — still absent 2 h later) |

**Every missing chunk at Sofar is matched by a `Queue MS_Q_CELLULAR_ONLY is full` line in
MS.log for that burst** (stage `spotter-reject`), except the bus-cut cycles and the two
whole clips A lost at its self-reset. Nothing is lost between the Spotter accepting a
message and Sofar (stage `after-spotter` = 0 on both rigs). Sofar and staging agree to the
chunk.

Baseline rejections (no report, no HDR inside the burst) are spread across the whole
burst (+70 s … +220 s on the folded grid), 1–5 per cycle, in 40 % (B) to 45 % (A) of
cycles; the WBGLW/6.2.5 Notecard rig rejects about twice as many chunks as the
WBNA-500/4.2.1 rig. Timing cannot remove these: they are the 2-slot queue vs 1 msg/s
during Notecard hand-off stalls. **Only a resend (part 2) or slower pacing can.**

## 2. The Spotter's own traffic, measured

1. **HDR push**: every :00/:05 boundary at +0.4..+0.8 s, 6.1 KB. Never inside an aligned
   burst (bursts start at +57 s).
2. **Hourly LEGACY report (50 B)** = the post-boot report (171 B) **rounded up to the
   next 5-minute boundary**, then every 60 min: A 19:03:00 → :05; B 18:05:00 → :10.
   Sprint23 finding 10 confirmed on both cards. `bridge cfg commit` / topology-change
   reports (340 B, "network") do **not** move it. Each report → `Attempting to Sync`
   +1..3 s → `Checking for Rx Messages` +26 s (B) / +45 s (A) → **40–50 s of
   rejections**. With the report on a boundary and the burst ending at +226 s, it never
   overlaps an aligned burst.
3. **Hourly health check** (`[ORC] Running health check!`) at **Spotter boot minute + ~1**
   (A :01:09, B :02:0x). 4 of 42 checks queued a 37 B LEGACY alert (each time GPS was
   flapping NO_SIGNAL indoors) → full sync inside the burst → **27–30 chunks lost**
   (A 03:00 and 04:00 cycles). Boot-anchored: a Spotter reset re-rolls it; SPOT-33507C
   resets itself ~60 min after every boot. The camera cannot know this minute.
4. **Spotter reset** loses whatever the Notecard has not synced: A's 19:01 self-reset
   dropped the 18:54 and 19:01 clips entirely (accepted by the Spotter, never at Sofar).
   Spotter → Notecard is `note.add … "sync":false` into `sofar_bm.qo`; the Notecard is in
   periodic mode (outbound 30 min); `hub.sync` is only issued at LEGACY reports. No SD
   spool (`outbox/ sent/ msgdata/` empty).

## 3. Timing recommendation (with numbers)

- **Keep `alignmentInterval5Min 1`.** Aligned bursts sit at +57..+226 s of a 5-minute
  cell: after the HDR push (+0.5 s, 0 collisions in 111 bursts) and clear of the hourly
  report, which lands on a boundary (0 collisions in 53 hourly reports). Unaligned (the
  raw 16-minute period) walks the burst across both every few cycles; Sprint22 measured
  10–17 rejections per HDR crossing and ~30 per report crossing.
- **Where a ~160 s burst should start: +55..+65 s after the boundary, as today.** Earlier
  risks the HDR/Rx-check tail (Rx checks land at +25..+45 s after a boundary report);
  later than +80 s pushes the tail past +240 s and into the next boundary's HDR at 1 msg/s.
- **Do not enable `transmit_phase`** (Sprint11 C2, guards 30 s / 20 s): the aligned bus
  already satisfies both guards, and its lane wait would only add delay and a failure
  branch. Nothing to tune there.
- **The hourly report minute is predictable (boundary rule) but the health-check minute
  is not** (boot + 1 min; re-rolled by resets). No camera-side timing can dodge it; it
  collides when the Spotter booted at :00–:03 (mod 20) and only when the check has an
  alert to send. Outdoors (GPS steady) it fired 0 of 4 times on B; indoors 3 of 21 on A.
- Camera-side: keep pacing 1.0 s/msg for now. A pacing test (1.0 → 1.2 s/msg, burst
  +190 s, still 50 s clear of the boundary) is the next one-variable test for the baseline
  1–5-chunk rejections if part 2 (resend) is not taken.

## 4. Indoor test today — one variable (needs Nick's go + hands)

**Variable: `alignmentInterval5Min` 1 → 0 on rig A only.** Rig B stays 1 (control).
Prediction: A's bursts walk 16 min per cycle across the grid and will cross an HDR push in
~2 of 6 cycles (10–17 rejections each) and the :05 hourly report in ≤1 (≈30 rejections);
B stays at baseline (≤5). If A instead stays at baseline, alignment is not what protects
the burst and the recommendation is withdrawn.

Steps (one commit per bridge BEFORE the run, cellular only, no `note sync`):
1. Cards back in both Spotters → each Spotter resets ("SD card insertion reset"); record
   the new boot times from the console; the new report minutes = post-boot report rounded
   up to the next 5-minute boundary (verify on the console within ~5 min).
2. Consoles: `spotter_serial_monitor.py --only SPOT-33507C --only SPOT-31593C` PID 26536
   is already logging both ports — do not start another.
3. Rig A: `bridge cfg set alignmentInterval5Min 0` + `commit` (forces bus ON 120 s and a
   report; do it before the run, not during). Rig B: no commit.
4. ≥ 6 cycles per rig (~2 h). Then `log flush` on both consoles, pull the cards, unzip
   into `runs/sprint25_indoor_<ts>/`, run the tool per rig, compare `loss_stage` and
   `queue_full_count_in_cycle` against the overnight table above.
5. Restore rig A: `alignmentInterval5Min 1` + commit. Original schedule values for a full
   restore: SPOT-33507C `1800000 / 900000 / controller 0`; SPOT-31593C `3600000 / 900000 /
   controller 0`.

## 5. Backend Gate 2 record (spec §7) — see `nereus-vision-dev` PRs #44, #45, #46

Swap direction verified from the consoles at 16:04Z: bmcam003 → SPOT-33507C, bmcam004 →
SPOT-31593C. Shadow detection proven from the DB before any fix: the registry advanced
both nodes at 16:21–16:23Z while zero media rows landed and the last ingest events on both
bindings stayed pre-swap. Three gaps found and fixed live: resolver tie-break (#44),
reconcile-from-registry-state (#45), move collision must not skip the node (#46).
BMCAM_004 moved (history closed/opened, `device_moved` event); BMCAM_003's move is blocked
by `uq_external_source_system_node` until the paused duplicate row's node id is cleared
(SQL handed to Nick; migration 0011 is the permanent fix). Media are labelled by the
gateway they arrived through (W3b) on both cameras since the first flag-on poll.

## Artifacts

- `runs/sprint25_sd_20260922T1640Z/run_manifest.json`
- `runs/sprint25_sd_20260922T1640Z/report_SPOT-33507C/{cycles.csv,cycles.md,loss_by_stage.md,timeline_SPOT-33507C.svg}`
- `runs/sprint25_sd_20260922T1640Z/report_SPOT-31593C/{cycles.csv,cycles.md,loss_by_stage.md,timeline_SPOT-31593C.svg}`
- `tools/bm_video_soak_report.py` — SD sources, step-7 columns, `loss_stage`, SVG cut sheet

## Open / not done

- Camera logs not joined (pull `BM_Devel_Pi/cron_logs/rc_cycle_*.log` during a bus window).
- B's 15:20Z clip: accepted by the Spotter (155 submissions, 0 rejections), absent at Sofar
  2 h later. Card pull at 15:41 or the swap power-cut are the suspects; undecided.
- The stills cycles before 18:49Z are listed (`stills-not-parsed`) but not joined.

---

# Part 1b — indoor 15-minute run, 2026-09-22 18:26Z → 22:00Z (cards pulled 22:00Z)

Run folder `runs/sprint25_indoor15_20260922T1840Z/` (SD sets: SPOT-31593C `0002`+`0003`,
SPOT-33507C `0004`..`0006`; the 19:23–21:39Z stretch has no console record, the SD is the
only witness). Cameras swapped: rig A = bmcam003 on SPOT-33507C, rig B = bmcam004 on
SPOT-31593C. Schedule: `sampleIntervalMs 840000`, duration 600 s, alignment 1 → a
**15-minute period**, both rigs in phase (:40/:55/:10/:25 from 18:40, then :05/:20/:35/:50
after Nick's 19:01Z ebox power cut re-anchored both). Restore = `sampleIntervalMs 960000`.

## What the bridge actually does with the interval (measured)

| commit / boot | interval | next windows | period |
|---|---|---|---|
| 2026-09-21 20:06:57Z | 960 s | :20, :40, :00 | 20 min |
| 2026-09-22 18:26:37Z | 840 s | 18:40, 18:55, 19:10 | 15 min |
| boot 19:01:18Z | 840 s (persisted) | 19:05, 19:20, 19:35 | 15 min |

The period is the interval rounded up to 5-minute multiples, but the grid is anchored at
the first window after the commit/boot, not at the hour. Every commit and every boot costs
one cut camera cycle (the forced 120 s bus-on boots the Pi and cuts its burst).

## How each rig did (12 cycles each, 19:05 → 21:50Z)

| | Rig A bmcam003 / SPOT-33507C / WBGLW 6.2.5 | Rig B bmcam004 / SPOT-31593C / WBNA-500 4.2.1 |
|---|---|---|
| clips complete at Sofar | 5 of 12 (21:35 stuck at 81/125 and 21:50 never arrived: SPOT-33507C self-reset 22:01:15Z, `Reboot Controller`) | 8 of 12 |
| chunks rejected by the Spotter | 22 (0–4 per clip) | 11 (0–2 per clip) |
| HDR push inside a burst | 0 | 0 |
| hourly report on the window boundary (:05:00) | 2 cases, both clean | 2 cases, both clean |
| health-check alert inside a burst | 0 (check at :01, in the off gap) | 0 |
| clips lost to resets | 18:55 (Nick's 19:01 power cut) + 21:50 whole and 44 chunks of 21:35 (22:01 self-reset) | 18:55 (19:01 power cut) |
| Notecard backlog | ≤ 30 %, drains within minutes | up to 42 %, periodic 30-min sync, clips land 30–40 min late |

Staging matches Sofar chunk for chunk; the cron ingests with `BM_AUTO_PROVISION=1` (Gate 2
closed 18:27Z).

## Why chunks are dropped — the mechanism, measured on both rigs

The Spotter hands each accepted message to the Notecard (`card.status` + `note.add`) and
the MS task logs `Sending Cellular message to Notecard` for each. During a burst those
hand-offs are spaced 1.0 s (1581 of ~1650 on A, 1558 on B). **Occasionally one hand-off
stalls**; the camera keeps sending at 1 msg/s; the Spotter's 2-slot cellular queue absorbs
two arrivals and rejects the rest, silently.

| hand-off stall | rig A: count → rejections | rig B: count → rejections |
|---|---|---|
| 2–3 s | 29 → 0 | 83 → 0 |
| 4–5 s | 15 → 15 | 12 → 9 |
| 7 s (camera-side gaps: keyframe repeat / END) | 12 → 0 | 1 → 0 |
| 10–13 s | 6 → 6 | 1 → 0 |

So: a stall under ~3 s is free, a stall of 4–5 s costs exactly one chunk, and rig A stalls
more often and longer than rig B (Notecard model / firmware). Only 3 of 22 (A) and 2 of 11
(B) rejections fell inside a Notecard sync session; the stalls are not phase-locked to any
10 s tick (start phase mod 10 s is flat). The "pairs 10 chunks apart" seen from Sofar were
two consecutive stalls, not one periodic job. Every rejected chunk is a one-second hole:
one chunk (288 bytes) of video, decoded with local damage, the clip marked partial.

What timing can and cannot do: the aligned 15-minute grid removed every HDR and report
collision (0 of 24 bursts). The stall losses are independent of where the burst sits.
They can only be reduced by (a) slower pacing — at 1.5 s/msg a 4.5 s stall still lets 3
messages arrive against 2 slots (marginal), at 2.0 s/msg the 155-message burst takes 310 s
and crosses the next HDR push; (b) a bigger Spotter queue (Sofar firmware); (c) a resend of
the exact missing indices (part 2, ~2 messages per clip here).

## Recommendation for the outdoor test

Keep everything as it is, plus one operating rule:

1. `alignmentInterval5Min 1`, `sampleIntervalMs 840000`, `sampleDurationMs 600000` on both
   bridges (15-minute period, 4 clips/hour, burst at +57..+226 s of its window). Do not
   enable `transmit_phase`; keep pacing 1.0 s and `keyframe_repeat_max 30`.
2. **No ebox/Spotter power cuts within 40 minutes of a burst** (WBNA-500 rig) — a cut
   erases every unsynced clip (four events today; on the 22:01Z self-reset SPOT-33507C's `Notecard is N pct full` dropped 21 % → 5 % at the reset itself, i.e. the Spotter re-init empties the Notecard's outbound queue rather than syncing it). If a restart is unavoidable, check
   `Notecard is N pct full` on the console first and wait for it to drop.
3. Accept 1–2 stall rejections per clip as the outdoor baseline (97–99 % of chunks) and
   measure it there: outdoors the GPS is steady, so the health-check alert that cost ~30
   chunks twice indoors should be rare — that is the one number the outdoor run adds.
4. Deploy nothing new to the cameras for this run. The resend feature (part 2) is the fix
   for the residual and is designed, not built.

If Nick prefers a camera-side change for outdoors, the only safe single variable is
pacing 1.0 → 1.2 s/msg (`pacing_delay_seconds` in the YAML on both units; burst 186 s,
ends at +243 s, 57 s clear of the boundary) — it should convert some 4 s stalls into
free ones. It costs a redeploy to both units and a second variable versus today's data.

---

# Part 1c — outdoor 15-minute run, 2026-09-22 22:35Z → 2026-09-23 02:20Z (16 cycles per rig)

Run folder `runs/sprint25_outdoor_20260923T0240Z/`. Sources: both cards, whole history (SPOT-33507C sets
`0000`..`0007`, SPOT-31593C `0000`..`0004`; outdoor = `0007` / `0004`, no Spotter reset during the run),
Sofar `sensor-data` to 02:49Z. Same rigs as part 1b (A = bmcam003 on SPOT-33507C / WBGLW 6.2.5,
B = bmcam004 on SPOT-31593C / WBNA-500 4.2.1), same settings. Both Spotters booted on SD insertion at
22:20:35Z / 22:20:42Z; bus windows :05/:20/:35/:50; hourly LEGACY report :25:00 on both (verified).
Excluded: the 22:21 cycles (insertion boot, burst cut by the forced 120 s bus-on) and the 02:35 cycles
(Nick powered off mid-burst). Tool check: the indoor15 SPOT-33507C table regenerated from this card
matches the committed one exactly.

## Comparison (rows keyed by Spotter/Notecard — the stall behaviour follows the Notecard, not the camera)

| | A overnight 20-min | A indoor 15-min | **A outdoor 15-min** | B overnight 20-min | B indoor 15-min | **B outdoor 15-min** |
|---|---|---|---|---|---|---|
| camera | bmcam004 | bmcam003 | bmcam003 | bmcam003 | bmcam004 | bmcam004 |
| clips (aligned, window) | 58 | 12 | **16** | 47 | 12 | **16** |
| complete at Sofar | 33 (57 %) | 5 (42 %) | **5 (31 %)** | 29 (62 %) | 7 (58 %)¹ | **9 (56 %)** |
| chunks lost / clip, stall only | 0.88 | 1.1 | **1.13** | 0.49 | 0.42 | **0.47** |
| chunks lost / clip, all causes | 1.83 | 4.6 | **1.13** | 0.49² | 0.42 | **0.50** |
| Spotter rejections / clip | 2.9 | 1.75 | **2.5** | 0.74 | 0.83 | **1.4** |
| hand-off stalls ≥ 3 s / clip | 1.34 | 1.75 | **1.38** | 0.76 | 0.83 | **0.69** |
| stall length (min / median / max) | 3.65 / 3.66 / 3.68 s | 3.65 / 3.66 / 3.81 s | **3.65 / 3.66 / 3.68 s** | 3.36 / 3.37 / 3.39 s | 3.35 / 3.37 / 3.61 s | **3.36 / 3.37 / 3.38 s** |
| stalls ≥ 4 s outside report syncs | 0 | 0 | **0** | 0 | 0 | **0** |
| hourly-report collisions | 0 | 0 (2 on the boundary, clean) | **0** | 0 | 0 (2 on the boundary, clean) | **0** |
| health-check alert inside a burst | 2 → 57 chunks | 0 | **1 → 1 chunk** | 0 | 0 | **1 → 1 chunk** |
| HDR push inside a burst | 0 | 0 | **0** | 0 | 0 | **0**³ |
| clips lost to resets / cuts | 0 in window⁴ | 21:35 (44 chunks) + 21:50 whole | **0** | 13:28 commit cut, 15:20 whole | 0 | **0** |
| Notecard drain (burst end → first `pct full` drop), median / max | 16 / 27 min | 26 / 28 min | **13 / 21 min** | 11 / 31 min | 21 / 36 min | **13 / 26 min** |

¹ Part 1b said 8 of 12; the regenerated table in the window 19:05–21:50 gives 7 (one clip counted in a different window or later at Sofar). ² excludes the 13:28 commit cut (68 chunks). ³ The tool shows 1 on B's 01:05 row: the 01:20Z START line was dropped from BM_TX.log (logging-queue drop), so 01:05 and 01:20 were merged into one 1054 s "burst". The 01:20 clip is complete at Sofar (121/121) with 0 rejections in MS.log. ⁴ The 19:01Z self-reset (2 whole clips) was before the aligned window.

Stall method (replaces part 1b's): a stall is the gap between consecutive MS `Queuing message <id>` lines
(one per `note.add`), not the `Sending Cellular message to Notecard` line (one per batch, so its spacing
also includes draining the backlog after a stall; that is why part 1b's table showed 4–13 s). Rejections =
stalls exactly on every run (e.g. A outdoor 22 stalls ↔ 22 rejections outside the one sync).

## What changed outdoors (evidence per claim)

1. **Stall length: nothing.** Every stall outside a report sync lasts a fixed time per Notecard:
   **A 3.65–3.68 s, B 3.36–3.39 s**, the same to 0.01 s in all three runs (177 stalls). The enclosure
   temperature (HTU.csv) went from 23–27 °C indoors to **15–26 °C** outdoors; the stall length did not
   change. A constant this tight looks like a Notecard firmware timeout, not radio conditions.
   **Cellular signal could not be measured**: neither card logs bars/RSSI (`card.wireless` only
   returns modem on/off at boot).
2. **Stall rate: nothing.** A 1.38 per clip outdoors vs 1.34 / 1.75 indoors; B 0.69 vs 0.76 / 0.83.
   Chunks lost to stalls per clip: A 1.13 vs 0.88 / 1.1; B 0.47 vs 0.49 / 0.42.
3. **Health check: collided by construction, but cheap this time.** The check runs at the **boot
   instant every hour** (22:20:35 → 23:20:38, drifting −0.6 s/h), not at boot minute + 1. Outdoors
   the insertion boot landed 35–42 s after a 15-min window boundary, so every hourly check falls
   15–20 s before a :20 burst. **The first check after a boot usually queues a 37 B alert** (7 of the 10 boot
   sets that ran a check, both rigs; later checks alerted 2 of 51 times, both indoors, GPS flapping):
   23:20 on both rigs → hub.sync → 19 (A) / 12 (B) rejections. They hit chunks 3–20, inside the
   keyframe-repeat prefix (chunks 0–29 are sent twice), so each cost **1 chunk**. The same collision
   mid-burst indoors cost 27–30 chunks. Later outdoor checks (00:20, 01:20, 02:20): 0 of 6 alerted. Too
   few samples to say GPS-steady made a difference (indoors 2 of 45 later checks alerted).
4. **Resets: none.** SPOT-33507C did not self-reset in 4 h 15 min outdoors (indoors 19:01 and 22:01 on
   2026-09-22, but 20 h without one overnight). No clips lost to resets; the only lost clips were operator
   events (insertion boot, power-off).
5. **Notecard drain faster** (median 13 min on both vs 11–26 min indoors), but it is a proxy (first
   `pct full` drop after the burst), and nothing was lost in the queue either way.

**Net: outdoors made no difference to the loss mechanism.** The residual is ~1.1 (A) / ~0.5 (B)
chunks per clip, all from fixed-length note.add stalls. That is why only 31 % / 56 % of clips are
complete even though 99 % of chunks arrive.

## Why a fixed stall costs exactly one chunk, and what pacing does

The MS task hands a message to the Notecard ~0.05–0.12 s after it arrives, so a stall always starts
just after an arrival. The next arrivals land at `T − lag`, `2T − lag`, `3T − lag` (T = arrival
spacing, 1.02 s today). The 2-slot queue absorbs two; **the third is rejected if `3T − lag < S`**.
Measured against every stall on the cards:

| arrival spacing T | A stalls that still cost a chunk (121) | B stalls that still cost a chunk (56) |
|---|---|---|
| 1.02 s (today) | 121 | 56 |
| 1.10 s | 121 | 56 |
| **1.20 s** | **121** | **1** |
| 1.25 s | 1 | 0 |
| **1.30 s** | **0** | **0** |

So **1.2 s/msg fixes rig B but not rig A.** 1.3 s/msg fixes both, with 0.17 s of margin on A's
longest normal stall. Burst 158 s → ~203 s: it ends at +260..+265 s, 35–40 s before the next
boundary's HDR push and the :25:00 hourly report. Note T is the *arrival* spacing: today's 1.0 s setting
arrives every 1.02 s, so set 1.3 and check the measured spacing on the SD.

## Ranked: what would reduce dropped messages (measured effect targeted)

1. **Pacing 1.0 → 1.3 s/msg** (`pacing_delay_seconds`), camera-side, one YAML value. Targets the stall
   loss: A 1.13 and B 0.47 chunks per clip, i.e. every outdoor loss except the health-check sync.
   Prediction from the table above: 0 stall rejections on either rig. Cost: +45 s bus-on per cycle
   (energy) and a smaller boundary margin (40 s). **Test: one variable, rig A only (the harder
   Notecard), B unchanged as control, ≥ 12 cycles**; pass = A rejections outside report syncs → 0.
   Needs Nick's go and a redeploy to bmcam003.
2. **Resend/heal** (`SPEC_resend_heal.md` v3, approved). Covers what pacing cannot: health-check syncs,
   bus cuts, reset partials (21:35 indoors: 44 chunks). At today's pacing it would need ~1–2 resends
   per clip (A) and ~0.5 (B); with 1.3 s pacing it becomes the safety net rather than the main fix.
3. **Keep Spotter boots away from window boundaries.** The first health check after any boot always
   alerts, at the boot instant + 1 h, then every hour. A boot 0–4 min after a 15-min window boundary puts
   every check in front of or inside a burst. After an SD insertion or reset, either re-commit the
   bus grid (one cut cycle) or check where boot mod 15 min falls. Outdoors it cost 1 chunk per rig
   (it hit the keyframe-repeat prefix); mid-burst it costs 27–30.
4. **No power cuts / card pulls while the Notecard holds unsynced clips.** Drain takes 13 min median,
   21–26 min worst outdoors. Wait ≥ 30 min after the last burst, or check `Notecard is N pct full` on the console.
5. **Ask Sofar**: (a) what the fixed 3.37 s (WBNA-500 4.2.1) / 3.66 s (WBGLW 6.2.5) `note.add` stall is,
   and whether a Notecard setting removes it; (b) whether `MS_Q_CELLULAR_ONLY` can be ≥ 4 slots.
   Either one removes the stall loss with no camera change.

Rigs were not changed during or after this analysis. Restore commands (`sampleIntervalMs 960000`) are
still pending, per the kickoff.

## Artifacts

- `runs/sprint25_outdoor_20260923T0240Z/run_manifest.json`
- `runs/sprint25_outdoor_20260923T0240Z/report_SPOT-33507C/{cycles.md,loss_by_stage.md,timeline_SPOT-33507C.svg}`
- `runs/sprint25_outdoor_20260923T0240Z/report_SPOT-31593C/{cycles.md,loss_by_stage.md,timeline_SPOT-31593C.svg}` (01:05/01:20 merge, see ³)
- `runs/sprint25_outdoor_20260923T0240Z/analysis/{sofar_dump.py,stall_analysis.py,stall_lengths.py}`

---

# Part 1d — overnight indoor pacing test (SETUP, results pending)

One variable: **rig A (bmcam003 / SPOT-33507C) `bm_serial.image_transmit_delay_seconds` 1.0 → 1.3**,
patched 2026-09-23T04:00:49Z with `tools/patch_camera_schedule.py` while the unit was awake. Backup:
`/home/pi/BM_Devel_Pi/camera_schedule.yaml.bak_20260923T040049Z`. Cron left armed; no code deploy.
Rig B (bmcam004 / SPOT-31593C) unchanged at 1.0 s (control). Both bridges stay on `sampleIntervalMs 840000`.

- Both Spotters powered on 03:45Z with the SD cards in (first power-on, not an insertion reset; SPOT-33507C also did a `Reboot Controller` reset at
  03:46:03Z). Bus windows :00/:15/:30/:45. SD cards and console monitor PID 26536 both record.
- First cycle at 1.3 s: 04:15Z, measured arrival spacing on the console **1.320 s**, 0 rejections so far.
  The 04:00 cycle had already loaded 1.0 s (1.019 s).
- Known confound, on both rigs: the hourly health check runs at the boot instant (A :46:03, B :45:2x),
  at the start of each :45 burst. The first check after boot usually alerts, so expect the 04:45 bursts
  to take a sync hit. The analysis tags sync stalls separately.
- Prediction (Part 1c model): rig A stall rejections outside syncs → 0 (from ~1.4 per clip); rig B unchanged,
  ~0.7 per clip. Pass = A has 0 in ≥ 12 cycles.
- Rollback: `cp /home/pi/BM_Devel_Pi/camera_schedule.yaml.bak_20260923T040049Z /home/pi/BM_Devel_Pi/camera_schedule.yaml`
  (or `patch_camera_schedule.py --set bm_serial.image_transmit_delay_seconds=1.0`) during a bus window.

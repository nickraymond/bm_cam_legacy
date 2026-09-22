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

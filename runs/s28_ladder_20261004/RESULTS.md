# Sprint28 HIL ladder — RESULTS (`runs/s28_ladder_20261004`)

**R0 on bmcam004: PASS (R0.1–R0.4). R1/R2: the first nrjxl still was captured, sent, healed and RENDERED (media 57389, 2026-10-05 09:40Z)** — the Zero 2 W captures `--raw` at its CMA with margin and encodes nrjxl in
time and memory at all three presets; 1600×900 (the gating default) predicts a 410 s wake against the 480 s budget.

Spec: `sprints/Sprint28_raw_jxl/LADDER.md` (origin/feature/sprint28-camera e44dde5, review-fixed).
Code under test (R0): e44dde5's `rc_raw_jxl.py` / `config_registry.py` / `config_validate.py`, copied to /tmp by the
probe (no deploy). Tools: e44dde5 `hil/tools/hil_s28_r0_probe.sh` + `hil_s28_r0_analyze.py`, run from a detached
e44dde5 worktree with this branch's `hil/hil.env`. Unit state: runtime development 34a6222, bus held on
(`hil_bus_always_on.sh`, Nick OK 2026-10-04 15:46Z), cron disarmed, runtime stopped, cjxl 0.11.2 installed for R0.4.

| id | criterion | result | measured (analysis/r0_*_bmcam004.*) |
|---|---|---|---|
| R0.1 | raw capture at the unit's CMA | **PASS** | 10/10 `--raw` (DNG + JPEG); CmaFree min 6.3 MB during `--raw` (46.5 MB without); 0 dmesg errors |
| R0.2 | capture time cost ≤ 3 s | **PASS** | median 3.62 s with `--raw` vs 3.35 s without: +0.27 s |
| R0.3 | encode ≤ 20 s/rung, RSS ≤ 120 MB, predicted wake ≤ 480 s | **PASS** all presets | 1600×900: 6.4 s/rung, 38.9 MB, 58.7 kB at d 3.8, wake 410 s · 2000×1124: 9.9 s, 45.1 MB, 38.7 kB at d 7.3, 417 s · 2400×1350: 14.1 s, 70.3 MB, 43.9 kB at d 9.0, 426 s. Largest PASS: 2400×1350 |
| R0.4 | tools + memory guard | **PASS** | cjxl v0.11.2 [NEON_WITHOUT_AES], numpy 2.2.4, / 25 G free; guard killed the 400 MB allocation (`kind mem`) |

Notes: the budget model's pre_capture 9 s and dng_planes 1 s are the analyzer's stated ASSUMPTION/ESTIMATE; the burst
assumes 176 + 2 msgs at 1.3 s. The 1600×900 encode at d 3.8 is 58.7 kB (above a 56 kB ≈ 176-msg payload): the
runtime's rung search picks the distance that fits, so R1 will show the real distance/size on the wire.
Restores (gate.log): `apt-get remove -y libjxl-tools && apt-get autoremove -y`; crontab
`~/hil_backup/20261005T044807Z/crontab_ARMED.txt`; `hil_restore_schedule.sh SPOT-31593C`.

## R1 / R2 on bmcam004 (in progress, 2026-10-05)

Setup: e44dde5 deployed (`hil_deploy_unit.sh … --ref feature/sprint28-camera`, FIELD-UPDATE PASS, registry v8,
cfg 7a9ed97b); bench YAML `mode.run: stay_on` (trigger-only; backup + restore in gate.log); bus held on; nvd #83 live.
`hil_refresh` skipped (console lane needs Nick's console OK); the backend plan accepted the change without it.

| step | time (UTC) | evidence |
|---|---|---|
| set still.format=nrjxl + crop via backend (Sofar) | sent 05:24:45, applied ~06:06 (at the 06:05 hub.sync) | cid 1000061; unit `still.format: pjpg -> nrjxl (next action)`; `<CF h=42c14d76 still.format=nrjxl still.crop=1504,846,1600,900>` |
| trg {med: still} via backend | sent 05:25:57, fired 06:07:40 | cid 1000062; `one still action on a video unit` |
| nrjxl encode | 06:07:47 capture | 54,569 B, 190 msgs at d=4.342, att=3, encode 19.4 s (R0 predicted d 3.8 → 58.7 kB: the rung search found the fit) |
| **first nrjxl START on air** | 06:08:15 | `<START IMG> …nrjxl, length 190, key=0e9fp2, tg=1000062, r=1600x900+1504+846, fmt=nrjxl, q=434, att=3, sha=e44dde54e26f` |
| backend row | first seen ~06:42 (last chunk 06:40:36) | media 57389, 186/190, format nrjxl, placeholder (by design until complete). Upload lag ≈ 28 min = Notecard periodic upload (no hub.sync in the off-hour) |
| rsd 1 | created 06:55:37, rx 07:06:22, serviced at once (stay_on) | 100131 chunks 81-84; only 81, 82 on air; 83, 84 + `<HL>` dropped by the Spotter's 2-slot queue during the sync → issue #126 |
| row 188/190 | ~07:40 | 81, 82 landed via the periodic upload |
| rsd 2 | created 08:31:21 (= rsd 1 + REASK_S 5400 s + next autosend pass), rx 09:05:36, on air 09:05:37/38 | 100132 chunks 83-84; both sent (2 chunks fit the 2-slot queue) |
| **R1/R2: complete + renderable** | **09:40:30** | media 57389 190/190, format nrjxl, `render_state renderable`, display JPEG 1600x900 (`api/nrjxl_first_57389_display.jpg`), size 54,569 B = the unit's file. **D1 = 3 h 32 m 43 s** (one partial heal: #126 + REASK 90 min + sync alignment) |

## R2.1 parity on media 57389: **PASS** (run by the nvd/backend session, relayed by the EM 2026-10-05)

| check | result |
|---|---|
| NR header + CRC | OK: BGGR 1600×900 at native [1504, 846], black/white 64/1023 |
| production libjxl 0.12 vs djxl 0.11.1 | bit-exact |
| jxl-oxide vs libjxl | ≤ 1 code after clamping to 0..4095 (jxl-oxide's float output shows the lossy undershoot below black, which libjxl clamps) |
| rig study decoder mosaic | equal |
| staging render vs a local render | equal |
| DNG | opens in LibRaw with the right CFA / levels / WB |

Evidence: the nvd session's parity run (backend side); this run's `api/nrjxl_first_57389_display.jpg` is the staging render.

## R3 forced fallbacks on bmcam004 (Sofar lane, EM-approved; LADDER e44dde5 order R3.1 → R3.2 → R3.5 → R3.3)

| step | change | sent / fired (UTC) | on the wire | backend complete? | verdict |
|---|---|---|---|---|---|
| R3.1 | encode_max_s 5 (cid 1000063) + trg (1000064) | 09:49 / 10:06:33 | `[RAW] FALLBACK rfb=time` (cjxl killed at 5.2 s); START 10:06:47 `fmt=pjpg … rfb=time`, key 0e9qqx, 162/162 | media 57443 COMPLETE 14:20:25 (254 min; stay_on heals 100135/100136) | **PASS** |
| R3.2 | distances [0.3], target_fill 0, encode_max_s 60 (cid 1000065, supersede) + trg 1000066 | 11:03 / 11:07:42 (the 1000063 ack was LOST and stay_on never re-sends it → in_flight 48 min → supersede) | `[RAW] FALLBACK rfb=fit: nothing fits (cap 195)`; START 11:07:56 `fmt=pjpg … rfb=fit`, key 0e9tkv, 154/154 | media 57457 COMPLETE 16:10:15 (303 min; last 3 chunks before START at the 16:00 per_boot boot) | **PASS** |
| R3.5 | target_fill 0.97, distances default, d_max 0.5 (cid 1000067, supersede: 1000065's ack also lost) + trg 1000068 | 11:58 / 12:07:13 | `search1 d=0.5: 420529 B, 1461 msgs … FALLBACK rfb=floor: the room needs d>=6.434 > d_max 0.5`; START 12:07:34 `fmt=pjpg … rfb=floor`, key 0e9wca, 157/157 | media 57472 COMPLETE on first send 12:40:46 (33 min) | **PASS** |
| R3.3 | crop [1505,846,1600,900] (cid 1000069; backend plan ok: defers to the unit) | 12:16 / applied 13:05:40 | **ACCEPTED**: `still.crop: 1504,… -> 1505,… (next action)`, ack ok h=315ea3dd; no e:xk at either layer | n/a | **FAIL** — `_nrjxl_still()` needs `mode.media == still`; on a video unit taking trg stills (`kv med:still`) the nrjxl rules (crop / keyed / cellular) never run. Fix in #120: validate whenever `still.format == nrjxl` (or validate the trg media override). Mitigation: crop reset with the R3 reset |

Observation (for the camera session): the runtime rung search on the first nrjxl still logged `peak_rss=139384 KiB`
(136 MB), above R0.3's 120 MB criterion (R0 measured the encoder alone at 39 MB).

stay_on heal finding (#126, severe): the 3 rsds serviced in stay_on put 4 of 30 heal chunks on air (57389 rsd1 2/4,
57443 rsd 100133 0/13, 57457 rsd 100134 2/13): the heal burst fires at the hub.sync and the 2-slot queue drops the
rest. The fallback pjpgs (R3.1/R3.2/R3.5) therefore complete only after the unit is back in per_boot (heals before START
at boot), so "arrives complete" is scored after the restore.

**R3 final: R3.1, R3.2, R3.5 PASS (fallback on the wire AND the pjpg arrives complete); R3.3 FAIL (nrjxl validation
skipped on a video unit taking trg stills: #120 fix needed). R3.4 N/A on hardware (golden vectors).**

## R4 production wakes on bmcam004 (per_boot, hourly bus, mode.media=still, still.format=nrjxl, defaults)

| wake (Z) | media | START | delivered / complete | wake→halt | notes |
|---|---|---|---|---|---|
| 16:00 (1) | 57521 | nrjxl len 195 (d 9.70, att 2, encode 15.4 s) | 195/195 complete 18:06 (126 min, healed) | 483 s | CmaFree 72→96 MB, 0 CMA errors; peak_rss 138 MB |
| 17:00 (2) | 57534 | **pjpg len 180, q 30, att 7, rfb=floor** (fallback) | 180/180 complete 17:10 (10 min) | 503 s | queue_full 0 |
| 18:00 (3) | 57549 | **pjpg len 193, q 30, att 7, rfb=floor** (fallback) | 190/193 at 22:17, **> 3 h, stuck** | 504 s | queue_full 45 |
| 19:00 (4) | 57563 | **pjpg len 167, q 30, att 7, rfb=floor** | 73/167 at 22:17, **> 3 h, stuck** | 504 s | queue_full 94 |
| 20:00 (5) | 57580 | **pjpg len 194, q 40, att 6, rfb=floor** | 147/194 at 22:17 (last chunk 20:15:55) | 505 s | queue_full 50; END not on console |
| 21:00 (6) | — (no row) | **pjpg len 178, q 40, att 6, rfb=floor** | **0 at backend** | 505 s | queue_full 1; Notecard 5 → 19 % |
| 22:00 (7) | — (no row) | nrjxl len 195, q 860, att 2 | **0 at backend** | 505 s | queue_full 145; Notecard 26 → 34 % |

Mid-check 2026-10-05 22:17Z: **SPOT-31593C stopped uploading after ~20:16Z** (last backend chunk 20:15:55; the
Notecard fill climbs 5 → 19 % in the 21:00 wake and 26 → 34 % in the 22:00 wake, where earlier wakes drained to 3 %).
bmcam003 / SPOT-33507C ingests normally (rows to 22:16Z) → not the backend or Sofar: a Spotter-side cellular/Notecard
stall on SPOT-31593C (rig event, not a camera result). Fallbacks 5/7 (all rfb=floor). Wake→halt 483–505 s every wake;
START every wake. R4.4 START uptime: from the cycle logs at the Tue 17:00Z window pull (no extra ssh).

**External event X1 (EM 2026-10-05 22:30Z): SPOT-31593C upload stall from ~20:16Z.** Evidence: no backend rows for the
21:00Z / 22:00Z wakes; last chunk 20:15:55; Notecard fill not draining (5 → 19 %, 26 → 34 %); SPOT-33507C ingest normal
over the same hours. Spotter not touched (Nick decides any console post/reset). If not drained by ~01:00Z: R4.1 gets
the external-event exclusion or R4 delivery = NOT ASSESSABLE; the camera-side criteria (R4.2, R4.4, fallback count) stand.

X1 desk forensic ("Raw compression test spec" session, read-only, `~/Downloads/x1_spotter_incident_20261005/X1_VERDICT.md`,
from `console_full/`): **delay** = Notecard → cellular (no cellular transmission 20:13:52Z → 22:16:54Z; 21:15:02 "Have
not transmitted message over cellular in 30 minutes" → Iridium fallback; recovery 22:16:54Z). **Loss** = only at the
Pi → Spotter queue (`MS_Q_CELLULAR_ONLY is full`); accepted = Sofar = backend chunk counts for 20/21/22/23Z.
Second, independent cause for no complete bmcam004 image since 17Z: SPOT-31593C's health check (~:02:55) and hourly
report (~:05) fall inside the :01–:05 burst; SPOT-33507C syncs at :10, after its burst. (C1 phase 1 found no
significant :00 vs :30 effect on 5+5 wakes; reconcile with the forensic before acting on it.)

**X1 decision: NO RESET (degraded).** Evidence: 00Z wake delivered (57635 137/193, last chunk 00:10:18Z, row present); Notecard 00Z 6 -> peak 20 % -> 3 % at 00:15Z, 3-6 % since (sawtooth, drains each sync); 00:05:23 sync -> 'All messages sent successfully' 00:06:00 (35 s, normal); 23Z also drained to 3 %. Backlog falling, not rising -> rule (a)/(b) not met. Loss continues only at the Pi->Spotter queue (queue_full 46 @23Z, 59 @00Z): the burst/report overlap cause, not the uplink. (00:55Z 10/6). Spotter not reset; X1 stays the R4.1 external event (stalled 20:13–22:17Z, degraded after).

Mid-check 2026-10-05 18:45Z: **R4.3 (≤ 1/12 fallback) already FAILED**: 2 fallbacks in 3 wakes, both `rfb=floor`
(nrjxl search hit the d_max floor without fitting; the unit fell back loudly with a reason = RC.5 behaviour, not
silent). R4.1 / R4.2 / R4.4 still running (all 3 wakes delivered or healing; wake→halt 483–504 s ≤ 570; START every
wake). Sources: console decode (`hil_con_decode.py`), `analysis/r4_media_midday.csv`, backend `/media/{id}` format.

## Transmission Phase A (bm #129) notes

Phase A timing assumptions (EM 2026-10-05, no code change, no extra ssh): the Pi logs no per-message send time,
so the Spotter console's `[BM_TX] Submitted … to cell-only queue` time is the send-time proxy (assumes UART/mote delay
≪ the 1.3 s pacing; not measured). Pi `rc_cycle_*.log` files exist only from deploy-window pulls, not every wake.
A per-message Pi log is a Phase C item if the ranking needs it.

| 23:00 (8) | 57621 | nrjxl len 194, q 875, att 2 | 149/194 at 23:05 (X1 degraded) | 504 s | queue_full 46 |

## R4 VERDICT — STOPPED by Nick at 8 of 12 wakes (2026-10-05 ~23:55Z, via the EM)

Reason (Nick): R4.3 already answered the question, and the B3a pivot makes the 4-plane fallback rate moot.

| id | result | basis |
|---|---|---|
| R4.1 delivery ≤ 3 h, 0 redundant heals | **NOT ASSESSABLE (external event X1)** | SPOT-31593C cellular outage 20:13:52Z → 22:16:54Z plus queue-full loss at the Pi → Spotter hand-off; 57521 (16Z) and 57534 (17Z) complete (126 / 10 min), 18Z onward incomplete |
| R4.2 halt uptime ≤ 570 s | **PASS (8/8)** | wake→halt 483–505 s |
| R4.3 ≤ 1/12 fallback | **FAIL: product, not a defect** | 5/8 fell back, all `rfb=floor` (loud, with the reason); nrjxl at 16Z, 22Z, 23Z |
| R4.4 START uptime recorded | **PASS so far** (START every wake on the console); per-wake uptime values read from the cycle logs at the next deploy-window pull | console START lines |

Camera health over R4: no CMA errors (wake 1), nrjxl search peak RSS 138 MB (above R0.3's 120 MB: known finding),
START every wake, 0 missed wakes.

## B3a B0 on bmcam004 — baseline build 1d0ff4f (2026-10-06 03:00Z + 04:00Z windows)

Harness `hil/tools/hil_b0_b3a_run.sh` (deployed app, `rc_raw_jxl.py --layout rgb`, production guard), ONE night DNG
(`rpicam-still --raw` at 03:03Z, 24.0 MB; dark noisy scene), 1600×900 crop. Data: `pulled/bmcam004_b0/` (b0_summary.csv,
run_*/result.json). Before the encodes, the sunrise-loop smoke (2 pairs) passed: `runs/s28_lowgain_sunrise_20261006`.

| effort | n | attempts | per-encode (s) | search total (s) | d chosen / bytes | cjxl VmPeak / VmHWM (kB) | CPU (s) | kills / rfb |
|---|---|---|---|---|---|---|---|---|
| e5 | 10 | 3 on 9/10, **2 on run 7** | 9.3–10.5 | median 28.0 (max 29.0); run 7: 20.4 | 2.365 / 54407 (run 7: 2.573 / 49099) | max 129468 (126 MiB) / 98284 | median 31.3 | 0 / none |
| e4 | 2 | 3 | 1.13–1.18 | 3.4 | 3.047 / 54644 | 121884 / 87116 | 6.3 | 0 / none |

Reading: VmPeak 126 MiB of the 250 MiB guard (PASS); per encode at e5 ~9.3–10 s (at the ≤ 10 s bar); the search is
**time-bound at e5**: with encode_max_s 30, run 7's first attempt took 10.5 s instead of ~9.3 s, so the third rung did
not fit and the still went out at d 2.573 (49 kB) instead of 2.365 (54 kB) — same DNG, different result. Prep:
rgb_prep 1.72–1.76 s, dng_read 0.5–1.1 s. e4 is ~8× faster per encode and needs d ≈ 3.0 for the same bytes.
Predicted wake: + ~13 s over the 4-plane path (R4 halts 483–505 s) → ~500–520 s: ≤ 570 s window, > B0's 480 s bar.
The fix 4584436 (rgb_encode_max_s 45, d-prior, e4 last attempt) is re-measured Tue 17:00Z on the same DNG + 2 daylight DNGs.

## B3a B0 re-run on the search fix 4584436 (staged, not deployed) — bmcam004 17:00Z window 2026-10-06

Same harness; 4584436 BM_Devel_Pi staged in /home/pi/b0_b3a_4584436 (rgb_encode_max_s 45 default). Data:
`pulled/bmcam004_b0_fix/<set>/`. Daylight DNGs = sunrise pair 36 (14:57Z, ~2300 lux); night = the 03:03Z baseline DNG.

| scene × build | n | attempts | search (s) | d / bytes | VmPeak / VmHWM (kB) | CPU (s) | rfb |
|---|---|---|---|---|---|---|---|
| day auto × 4584436 | 2 | **2** (2.595 over cap 71.0 kB → 3.613) | 19.8–20.0 | 3.613 / 53287 | 129236 / 98236 | 23.1 | none |
| day low_gain × 4584436 | 2 | **2** (2.595 over cap 69.5 kB → 3.508) | 19.8–19.9 | 3.508 / 53509 | 127956 / 98236 | 23.1 | none |
| night × 4584436 | 6 | 3 (2.595 under → 2.298 over → 2.366) | 27.9–28.0 | 2.366 / 54357 (6/6 identical) | 129444 / 98236 | 31.2 | none |
| night × 1d0ff4f (baseline) | 10 | 3 on 9/10, 2 on 1 (time-bound) | 20.4–29.0 | 2.365 / 54407 | 129468 / 98284 | 31.3 | none |
| day × 1d0ff4f | — | not run (window time) | | | | | |

Reading: daylight now takes 2 encodes (~20 s); night still 3 (~28 s) but with 45 s of cap it is deterministic (6/6
identical; the baseline's run-7 truncation cannot recur). The e4 last attempt never fired (no time pressure).
VmPeak ≤ 126.4 MiB everywhere (guard 250). Predicted wake vs the 4-plane path (R4: encode ~15 s, halts 483–505 s):
day +5 s → ~488–510 s; night +13 s → ~496–518 s (EM's estimate 490–510; ≤ 570 window; above B0's 480 s bar at night).

## B1 — first production-lane B3a still (bmcam004 104ee3c, still.raw.layout rgb, 2026-10-06 19:00Z wake)

| item | result |
|---|---|
| deploy | 104ee3c (= 4584436 runtime) in the 18:00Z window; `still: raw: layout: rgb` added locally; effective `nrjxl rgb 45`, dropped [] |
| START | fmt=nrjxl q=435 att=2 cmp=1 length 186, key 0eca52, cfg 9e346071 (no rfb) |
| wake | END sent 186, burst 246.7 s, wake→halt 501 s; daylight et 3023 µs ag 1.12 lux 5085 |
| queue | 5 rejected (`is full`) = chunks 178–182, sent ≈ 19:04:58–19:05:04Z: the Spotter's :05 hourly report (the T1/X1 overlap effect) |
| backend 19:46Z | media **57901**, format nrjxl, 181/186, render_state placeholder (`api/B1_media.json`, URLs stripped) |
| console reassembly | 181/186 (the 5 rejected chunks never reached the console); full-container check waits for the heal |
Status: OPEN — B1 completes when the heal fills 178–182 (REASK 5400 s → expected at the 21:00Z or 22:00Z wake); then
render + LinearRaw DNG check (QC gets 57901).

Second sample, 20:00Z wake: START fmt=nrjxl q=402 att=2 cmp=1 length 187, key 0eccx2 (no rfb); END sent 187, burst
248.0 s, wake→halt 502 s. **queue_full 49**, all between 20:05:01 and 20:06:06Z, i.e. from the Spotter's :05 report
sync (`Attempting to Sync` 20:05:00) on (≈ chunks 138–186 by send time; START 20:01:59). Health check 20:02:34 caused
none. No heal chunks for 57901 (0eca52) in this wake (REASK 5400 s → its heal is due later). B3a encode + send are
consistent (2 attempts, ~187 msgs, ~500 s halt); delivery loss is the :05 overlap, not B3a.

**B1 format/decode verdict: PASS** (2026-10-06 ~22:15Z). The complete containers for 57901 (0eca52) and 57914 (0eccx2),
pulled read-only from bmcam004's sent records (sha256 MATCH, `pulled/B1_containers/`), decoded by the camera session
with the backend's v2 decoder (nvd 23a4324): method 20 / profile 2, geometry 1504,846 1600×900, decoded codes
bit-identical to djxl, render OK by eye, LinearRaw DNG opens in LibRaw (record: runs/s28_b3a_b1_decode_20261006/ on
feature/sprint28-b3a 2272f87). **Delivery is OPEN**: backend rows 57901 181/186, 57914 138/187, 57928 143/185 —
chunks lost to the :05 report overlap (T1/X1), heals pending. QC prod check (size line, Linear DNG, hq) once a row is complete.

**B1 delivery (2026-10-07 00:2xZ):** 57901 COMPLETE 186/186 at 22:10:34Z (healed at the 22:00Z wake; 190 min after
capture → D1 3 h missed by 10 min), render_state renderable, display JPEG present, size 53506 B = the sent container
(`api/B1_media_complete.json`, URLs + GPS stripped). Sent to frontend QC for the prod size line / Linear DNG / hq.
57914 178/187 (healing). Later stills 57928/57942/57955 lost 38–50 chunks each to the :05 overlap → the lane +
re-phase (Nick GO 10/6 22:50Z) is the fix under test. **B1 overall: PASS for format/decode/render; delivery OK via
heal.**
QC prod check of 57901 (frontend QC session, 22:26Z): **PASS** — size line "Linear RGB 1600×900 · 3×16-bit · 8.6 MB
unpacked · Sent (compressed) 53.5 kB (34:1 vs RAW) · 186 msgs"; Linear DNG = LinearRaw 34892 3×16-bit (rawpy opens);
/hq 1600×900, 0 % magenta, 17 % clipped highlights; prod original 53,506 B, sha256 f23771ac8b963ae9… = the unit's
sent record → the heal is byte-exact end to end.

## bmcam004 wake shift (Nick GO 2026-10-06 22:50Z): lane block + re-phase — as run

- Lane block ON since 00:00:43Z 10/7 (`hil_lane_block.sh`): effective uplink.lane True 3600/375/20/180, dropped [];
  print-config "transmit_phase (C2): ON grid=3600s guards=375/20s lane=3205s … fits".
- Re-phase: attempt 1 (00:00Z) never reached the commit (script hung on ssh to a halting Pi); attempt 2 (01:00Z)
  aborted (pkill self-match killed the remote shell before the halt; unit halted by hand 01:03:15Z); attempt 3
  (02:00Z, rehearsed) committed — but **at 02:02:22Z instead of 02:03:32Z**: `ticks 04` → the tool's embedded Python
  rejected the leading zero (`minute=04`), the commit time came back empty and the wait was skipped. Bridge read-back:
  ticksSamplingEnabled=1, controller 1, 3600000/600000 → **windows at ~:02:25–:02:33**, not ~:03:40.
- The commit's console call hung ~6 min (the perl-alarm bound killed only the wrapper, not the ssh child holding the
  pipe), so the stub-window boot (Pi on 02:02:32Z, `<WS` 02:02:52Z, cron armed) was not caught: **the bus cut it at
  02:04:25Z, ~112 s into a cycle** (hard power-off). Next window ~03:02:33Z.
- Fixed in hil_bridge_phase.sh: `int('$MM')`, an abort if the commit time is not computed, and a process-group kill
  bound (tested: a hung pipeline returns rc 124 in 3 s).
- Captures skipped tonight: 00:00, 01:00, 02:00Z.

### LANE-W1 — 03:02Z wake (lane ON, windows ~:02:25) — **FAIL (clear)**
| item | value |
|---|---|
| bus / Pi | bus on 03:02:22 (power 03:02:25) → off 03:12:22; Pi 03:02:34 → halt 03:10:40 (wake→halt 498 s) |
| lane | START 03:06:12 (held by the lane as designed; capture 03:03:03), fmt=nrjxl q=239 att=2 cmp=1, length 195 |
| burst | 03:06:15 → ~03:10:30 (258.6 s); margin to the window end ≈ 112 s |
| queue_full | **141 / 195**, continuous 03:06:16 → 03:10:07 |
| cause (console) | 03:05:03 the Spotter queued its own HDR message (6129 B) into MS_Q_CELLULAR_ONLY slot 1 + the :05 LEGACY report; 03:05:06 "Attempting to Sync" → "Waiting for TX" with no completion until the Notecard drain at ~03:10:20. With slot 1 held, every 2nd message of the burst finds the queue full for the whole burst. |
| health check | `Running health check!` 03:02:31 (= the bus power-on); no burst traffic in it (boot heal/ack not separately analysed) |
Reading: the 375 s post-guard assumes the :05 sync is over by :06:15. On SPOT-31593C it is not: its syncs wait on
cellular TX for minutes (X1: 20:05:14 → 20:13:11 = 478 s; tonight ~5 min), while SPOT-33507C's take 32–46 s.
A fixed-time guard cannot clear a sync of variable length; re-phasing to :03:40 (option B) would not change this.

### Spotter sync profile (EM ask 10/7 03:4xZ; existing console only, no commands) — `hil/tools/hil_sync_profile.py`
`analysis/sync_profile_SPOT-31593C.csv`, `…_SPOT-33507C.csv` (10/6 12:00Z → 10/7 03:3xZ). At each hourly report both
Spotters queue LEGACY:50 + their own **HDR message (6129 B; 6062–6070 B twice) into MS_Q_CELLULAR_ONLY**, which holds
1 of the 2 cellular slots until it is handed to the Notecard.

| Spotter | report at | n | sync TX wait (s) | HDR slot hold (s) | overlap with the camera burst |
|---|---|---|---|---|---|
| SPOT-31593C (bmcam004) | :05 | 8 | 34–196 (median 44) | 34–244 (median 71) | yes: the burst runs :01–:05 (production) or :06:15–:10:30 (lane) → inside the hold |
| SPOT-33507C (bmcam003) | :10 | 10 | 32–49 (median 43) | 44–308 (median 115) | no: its burst ends ~:05, before :10 → queue_full 0 at every report |
Reading: the HDR hold is long on BOTH Spotters; what differs is where the report falls relative to the burst. bmcam003's
:10 report is after its burst; bmcam004's :05 report is inside it, and the lane's 375 s guard puts the burst straight
into the HDR hold (03:05:03 + 244 s). A burst that ends before :05 on SPOT-31593C (option 1) mirrors bmcam003.

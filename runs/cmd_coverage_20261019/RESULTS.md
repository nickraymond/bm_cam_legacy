# c_cmd_coverage on bmcam004: RESULTS (Tier 1)

Written by TE5, Thu 10/22 ~10 AM PDT. Card `runs/cmd_coverage_20261019/TEST_CARD.md @ d8946b15`; spec
`SPEC_cmd_coverage_bmcam004.md` rev 4 (sha256 9ae529792a38); matrix `e436794c`. Approvals: APPROVAL.txt (Nick to TE3 Mon
9:48 AM, TE4 Tue 8:46 AM, TE5 Thu 9:52 AM). Rows were run by TE3 (Mon) and TE4 (Tue). **All evidence predates the Wed noon
cut** (last row Tue ~7 PM PDT). This write-up was due Wed noon; it is late (stand-down Wed, approvals Thu 9:52 AM). No
new rows were run.

Inputs → outputs: `gen_verdicts.py` (sha256 08230cdf993b) reads matrix.csv, `matrix_verdicts_draft.csv` (TE4's Tue
desk tally), TE4_rows_tue.md, timing_groups.json / TIMING_RESULTS.md and the PE 006/007 rulings 1–10 (sha256 d5c097847c7f).
It writes `matrix_final.csv` (sha256 a51ce193d0c0; committed to git as matrix.csv) and `verdict_changes.csv`.

## Answer
- **Tier 1 = 78 rows (10 verbs + 68 backend-writable keys): every row has a verdict with evidence.**
  66 SAFE, 6 UNSAFE, 5 NOT OBSERVABLE, 1 LIMITED, 0 INCOMPLETE.
- **The NOAA build does NOT pass yet:** 5 UNSAFE key rows are still reachable from the backend for SPOT-33361C (below).
  The spec's fix is backend-only (block them for SPOT-33361C before 10/27). It needs no camera change, so the freeze holds.
- **One UNSAFE verb: `trg`. Nick decides** (spec: an UNSAFE verb can't be blocked at the backend by key).
- **Desk check (must do 7): catalog v14 = camera registry @ 2cc1afdb, 0 mismatches** on every field of all 68 Tier 1
  keys (all 115 keys equal; X3_catalog_vs_registry.md).
- **bmcam004 restore: NOT done yet.** nereus000 (the console host) lost external power Wed 6:45 PM and shut down on an
  empty battery at 8:50 PM. A Thu 10 AM power cycle booted it on battery only (VIN 22 mV), so the supply needs fixing
  first. The restore runs after that (Nick's Thu 9:52 AM line).

## UNSAFE rows, in plain words
| row | what happened | catalog tier | action |
|---|---|---|---|
| mode.run = stay_on | The unit never halts by design: awake 1615 s (bar 481 s). On a buoy it drains the battery. | control | backend blocks stay_on for SPOT-33361C (PE 006 ruling 1) |
| mode.interval_s, mode.heartbeat_s | They act only with stay_on (same 1615 s boot) | control | same as stay_on (key or value, backend's call) |
| still.budget_min = 12 | Raises the wake ceiling to 720 s; commanded wake ran 591 s | control | backend: only 8 (today's value) stays sendable (rulings 4, 8) |
| video.send.budget_min = 15 | Raises the ceiling to 900 s; commanded wake ran 678 s | control | as still.budget_min: only 8 sendable |
| verb `trg` | Two triggers in one wake ran 527 s (V2-2). Separately, a firing trg ends the listen window after 4 s, so 3 other commands that wake got no ack (received + answered; 3 C2 losses, ruling 10) | verb | **Nick:** e.g. at most one trg per wake at the backend, or a camera change (ruling 2) |

Also UNSAFE for the backend (ruling 9): **video.record.crop at the full sensor [0,0,4608,2592]**. Its
row is SAFE at [768,432,3072,1728] (timed in G9, 421.5 s). The full-sensor value ran only on a commanded boot (507 s
commit→dark, information only), so its wake time was never measured on a command-free boot (ruling 5 / C6). Backend
blocks that value (value-level if the catalog can).

Tier 2, outside the backend's reach: **power.bus_always_on = true is UNSAFE** (held wake 624 s, ruling 1). Catalog v14
already marks it `blocked` (not backend-writable), so the backend needs no change.

## LIMITED and NOT OBSERVABLE
- **LIMITED: schedule.timezone.** Accepted, but dropped at boot (`[CFG][ERR] tzdata`, c_tz_overlay_boot). → 10/27 runbook
  known-limits list: the NOAA timezone must be in the base YAML.
- **NOT OBSERVABLE (stated, not failures):**
  - camera.focus.mode: already manual.
  - camera.exposure.mode: builds no flag.
  - camera.image_processing.hdr "off": no argv or hrpm line shows it (ruling 3).
  - still.raw.distances: only used if the search fails.
  - still.raw.encode_max_s: bayer4-only cap, not seen under rgb.

## Wake time (d)
- Spec must do 3(d), PE 006 rulings 5–6: 10 grouped command-free timing boots, membership written first
  (TIMING_GROUPS.md sha256 eceb398b5675). **All 10 ≤ 481 s (180.7–421.5 s)**, 0 FALLBACK/ERR beyond the named ones:
  - G1: rfb=err from the SPOT-33507C UTC confound (pre-reset).
  - G6: rfb=floor, the key's expected effect.
  - G7: rfb=time, the anti-brick row's expected fallback.
- Window, heal-cap and mode.output rows were timed in their own runs (desk tally X1).

## Findings to carry (not rows)
1. **`reset` / `get` take at most 4 names** (`k is a list of 1..4 names e=val`). The backend must split larger resets.
2. **SPOT-33507C stopped serving Spotter UTC for ~7.5 h** (Tue 9:26 AM → console reset 5:06 PM). Images went unkeyed
   (JPEG fallback) until the reset. Cause unknown; PE 006 adds an E4 runbook line.
3. **A boot with no WiFi and no AP** (bmcam004 Tue 1:43 PM): `No suitable device found` at 20:43:20Z; the "waited ~90s"
   message is wrong (it bailed after 1 s). The BM path was unaffected (195 msgs). A field unit would lose its AP
   recovery for that boot (n = 1).
4. **The mote replays commands; the repeated-id dedupe holds** (C2 resends answered `duplicate: original answer`).
5. **Video sensor_mode is `WxH`**, and the whole-config geometry check refuses a crop outside the mode's field of view
   (matrix error: test value `2304:1296`).
6. **mode.media `video_logger` and mode.output `save_local` are blocked values at the backend** (catalog v14), so the
   backend can never send them; the camera accepts them (save_local tested on the console).

## PE rulings applied after the desk draft (PE 007 rulings 7–10, same file, sha256 d5c097847c7f)
- **7, hld:** passes (d). Both hld boots (V2-1, V3-1) ran 509 s commit→dark, within the run's commanded-boot baseline of
  510 s (B52-exposure-manual-set, step.log:265-270; hld_baseline_ruling7.txt). H2-effect's hld 5 ran 618 s commit→dark only because
  power.bus_always_on was true, which lifts the clamp; that row is UNSAFE and catalog-blocked.
- **8, budget keys:** the value in use today is 8 min (NOAA soak profile), and the catalog default of 18 was never
  timed, so **only 8 stays sendable** to SPOT-33361C for still.budget_min and video.send.budget_min.
- **9, video.record.crop full sensor:** G9 doesn't bound it (2.25× the pixels), so it **counts UNSAFE for the backend**.
- **10, trg C2:** 3 losses (one per command), not 1. The verdict is unchanged (already UNSAFE). Cause: a firing trg ends the
  listen window after 4 s, so other commands in that wake get no ack until a same-id resend.

## Crop answer (spec must do 6, Nick's 195-message question)
From the Sun 10/18 encode-only sweep (~/NereusData/bm_cam_legacy/2026-10-18_crop_sweep_bmcam004/RESULTS.md):
- **On S2 the Pi encodes up to 1600×900** (≤ 28.7 s, ≤ 114 MiB, ≤ 192 msgs).
- Every larger crop, the full frame included, falls back cleanly with rfb=mem (no OOM, no hang).
- **The full frame can't fit 195 messages even with unlimited memory** (needs d ≥ 14.09 > the 10.4 floor).
- The shrink-to-1600×900 path is separate work (crop view S3, bm PR #186, G1 a/b).

## Not done / limits
- Tier 2: 42 rows not run (after 10/27, many dangerous, never unattended). 5 Tier 2 rows were run: 4 SAFE (power.halt.enabled,
  commands.enabled no-cfm, a locked key, an unsigned service key) and power.bus_always_on UNSAFE (above).
- commands.topic, uplink.uart.port, uplink.uart.baudrate (must do 3b): not run. They are catalog `blocked`.
- The backend → Spotter → camera path is not shown on bmcam004 (SPOT-33507C rollout OFF during the test). It is covered
  by the soak's command bars on bmcam003 and the X3 desk check.
- Restore (must do 9): pending (see Answer).

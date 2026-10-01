# S6b step 2 — W9 (chunk total) bench proof (2026-09-29)

Code: development 3c1801d (bm #92, W9) on the units; nvd #65 (`/M` parser) + #68 live on staging;
BM_HEAL_AUTOSEND unset; conductor and bm-heal-driver off. Nick's GO relayed by the S4w session.

## bmcam003 — PASS (20:15:42Z)

| step | result |
|---|---|
| deploy | 7d30fea → 3c1801d, SUMMARY PASS, hash 580ce986 after `reset mode.run` (53001, exit 72) |
| proof clip | python run directly (the wrapper does NOT forward args: `"$@"` inside `run_rc()`, run 1a aborted), `--bench-drop-chunks start,5,17`: `[VTX][BENCH] NOT sending START`, key 0dz8um, chunks `<I0dz8um.n/185>` |
| console | no START for 0dz8um; 5 and 17 absent; 79–85 also absent (real Spotter queue-full, F1); END sent |
| backend | row 55503 captured 18:03:58 from the key, **expected 185 from `/M`**, missing `5,17,79-85` after arrival settled (not length_unknown) |
| heal 1 | rsd 100077 (POST, media_ids [55503]) at subscribe → `sent 9 heal chunk(s) before START` → `<HL a=sent n=9 r=ok>` on the unit; 5,17,83–85 landed, 79–82 lost again at the Spotter (queue full) |
| heal 2 | rsd 100078 `79-82` → 4 chunks before START |
| **proof** | **185/185 complete; stored h264 53,219 B, sha256 afd117a8…8391 = the unit's sent record** |

Notes: row 55502 is aborted run 1a (22/185, START sent), not part of the proof. The backend
reports `has_start_metadata: true` on 55503 although no START was sent (console-proven): a
backend flag to check (S6b). `<HL>` at the backend was not checked. No mp4 (H6).

## bmcam004 — PASS (2026-09-30 03:35:58Z)

`w9_proof.sh` (the bmcam003 recipe as one script). Deploy 9491f1f-era 7d30fea → 3c1801d
PASS; `reset mode.run` (63001) → 580ce986; proof clip key **0dzwa7** (183 chunks),
`NOT sending START`, 5 and 17 dropped; row **55608** captured from the key, expected 183
from `/M`, missing exactly `5,17` (no Spotter losses this time); heal `rsd` 100070 → 5, 17
sent before START; **183/183 complete; stored h264 52,599 B, sha256 3600a0e3…7289 = the unit's
sent record.**

Script issues (fixed or noted, no effect on the result):
- The first launch hung at 20:19Z for ~6 h: `cd … && nohup setsid … &` over ssh backgrounds
  the whole list as a subshell that keeps ssh's stdout open (fixed: `cd …; nohup …`).
- The post-heal wait broke at once (it checked `received_age_s ≥ 600` before the heal chunks
  arrived), so passes 2 and 3 (rsd 100071, 100072) re-sent 5 and 17 needlessly; harmless
  (one pending heal per key, dedupe by id).
- bmcam004 lost bus power 3 times on 2026-09-30 (SPOT-31593C "Neighbor e6fe83ea6b4a2b7f
  added" at 00:25, 01:37, 02:08Z); the last one hard-rebooted the Pi (cause unknown, bench power
  or Spotter).

## State at the end

Both units on development 3c1801d, config 580ce986 (overlay empty), **disarmed** (armed
crontab in `/home/pi/w9proof/backup/crontab_ARMED.txt`), halted, buses HELD (SPOT-33507C,
SPOT-31593C). bm-heal-driver and the conductor stopped. Next: the S6b HIL test (PLAN_S6 §9.14).

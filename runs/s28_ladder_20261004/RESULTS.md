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

# Sprint28 HIL ladder — RESULTS (`runs/s28_ladder_20261004`)

**R0 on bmcam004: PASS (R0.1–R0.4)** — the Zero 2 W captures `--raw` at its CMA with margin and encodes nrjxl in
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

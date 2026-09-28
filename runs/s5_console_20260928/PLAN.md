# S5 console proof — bmcam003 first (2026-09-28)

Spec: `sprints/Sprint26_remote_command_loop/DESIGN_supervisor.md` §6 + §8.3 S5 row;
`PLAN_S4.md` (G1–G17); `docs/bmcam_command_reference.md` (v9, generated).
Code: `development` 0f67784 (S4a #83 + S4b #84 + S4c #85; catch-up PR #87).
Branch for fixes and this record: `feature/sprint26-s5-console-proof`.

Authorization (Nick, 2026-09-28): this session owns bmcam003, bmcam004, SPOT-33507C,
SPOT-31593C and nereus000. Nick at 16:35Z: "change the Ebox power settings to be always on
so that you can push new software"; "do not sit idle waiting for their power on cycles".

## Start state (from the brief + runs/s4a_soak_20260928)

| unit | code | config | cron | bus |
|---|---|---|---|---|
| bmcam003 | S3c cabb79f | v2, 62e0a1cb (registry v4) → 580ce986 under v5, supervisor per_boot video transmit, real halt | armed | SPOT-33507C bridge c3c564b91856226c, 3600000/600000 |
| bmcam004 | S3b 9491f1f | v2, 72a12186, supervisor per_boot | armed (control) | SPOT-31593C, 3600000/600000 |

## Steps (bmcam003)

| # | what | tool |
|---|---|---|
| 0 | hold SPOT-33507C bus ON (Pi was halted, bus off: no hard cut); read back 0 | `console.sh` |
| 1 | the held bus boots bmcam003 ARMED; watcher catches it, disarms (crontab_ARMED backed up), SIGTERMs the boot cycle, backs up config/state/crontab to `/home/pi/s5bench/backup` | `watcher_s5.sh` |
| 2 | deploy development 0f67784, `--leave-disarmed`; service key (create if absent, copy to `~/.config/nereus/unit_keys/bmcam003.key`) | `rc_field_update.sh`, `deploy_rc_runtime.sh --create-service-key` |
| 3 | `config_v2_upgrade.py` dry-run, then `--write`; hash before/after | |
| 4 | one ARMED production wake (reboot on the held bus, real halt) → staging delivery with START `cfg=` and `<WS cfg= up=>` | `delivery.sh` |
| 5 | heal driver stopped (state.json backed up); S5 ladder over the console (below) | |
| 6 | restore: config (original or agreed baseline), crontab re-armed, bus schedule back (Pi disarmed + halted, commit, re-arm in the stub window), read back 3600000/600000, heal driver running | |
| 7 | RESULTS.md, PASS/FAIL per step | |

## Ladder (evidence per step: console log, state sha256 before/after, cfg hash, START/END)

L1 ping/help/get (help adds nothing to the cellular queue) · L2 duplicates → original answer +
`d:1` (mote replay ~60 s) · L3 rejections NaN / space / oversize / cross-key / unknown key →
hash + state unchanged · L4 set/reset back to the original hash · L5 manual WB gains in END `cg`
· L6 `trg kv` on both media (hash unchanged, START tg/r/m/d) + `med` override · L7 2×2×2
modes (media × run × output) · L8 `hld` on the held bus, then the clamp on the scheduled bus ·
L9 guarded stage (`power.halt.enabled true` + `cfm`) and guarded revert (`commands.topic` or
`mode.output save_local`, no `cfm`) across a bus power cycle · L10 `rsd`, `wap` · L11 a 270 B
console line · L12 a signed service `set` (`tools/bm_service_sign.py`).

## Rollback

bmcam003 → `rc_field_update.sh --ref cabb79f` (S3c) + backup `camera_config.yaml` from
`/home/pi/s5bench/backup`; bmcam004 → 9491f1f. Bus: `bridge cfg set <bridge> s u
bridgePowerControllerEnabled 1` + commit with the Pi disarmed and halted, re-arm in the ~2 min
stub window.

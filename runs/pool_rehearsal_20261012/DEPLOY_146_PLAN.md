# Deploy bm #146 (uplink.media_key.heal_order) to bmcam003 + bmcam004: TE2 procedure (Tue 10/13 ~9:40 PM PDT)

**Scope:** #146 only (EM: #147 on HOLD).
- #146 adds `uplink.media_key.heal_order` before|after, registry v12. **Default "before" = today's behaviour.** The deploy
  alone changes nothing on the wire; the switch-on is a separate console `set`.
- If #147 ever ships: its `still.raw.progressive` defaults to **True** in the PR (32a22ba), so deploying it turns `-p` on
  at once, not off. A memory check (cjxl peak RSS ≤ 150 MiB, < 250 guard) would have to come before that deploy.

**Units now:** runtime 9ec4cb7 + pool files, production, hourly bus (the Spotters are outdoors with GPS), focus 0.8
(cfg 44b81292).
- Pairing: bmcam003 ↔ SPOT-31593C, bmcam004 ↔ SPOT-33507C (hil.env + tools/rig_map.json; #170 guard in hil_common).
- `git diff 9ec4cb7 origin/development -- BM_Devel_Pi` = pool files only, so the merged sha brings #146 + pool files.

## 0. Before the window (Mac, no unit writes)

1. The EM sends the merged development sha `S`. Then:
   - `git diff --stat 9ec4cb7 S -- BM_Devel_Pi tools/rc_field_update.sh` → expect only the #146 files + pool files;
   - run the #146 tests locally (`.venv-dev`, numpy 2.5.3 installed).
2. Pairing check:
   `grep -E '^export HIL_RIG_[AB]=' hil/hil.env` → `SPOT-33507C=BMCAM_004=bmcam004`, `SPOT-31593C=BMCAM_003=bmcam003`.
   The #170 guard in hil_common refuses a mismatch with tools/rig_map.json.
3. Offline parity rehearsal (as on 10/9):
   - load each unit's pulled camera_config.yaml + bm_command_state_v2.json with S's loader;
   - expect strict load OK and **cfg hash unchanged (44b81292)**: the new key is at its default, not in the base;
   - the print-config diff should be exactly the new `heal_order` line. That reviewed diff is the only reason to pass
     `ACCEPT_DIFF=1`.

## 1. Deploy (hil_deploy_window.sh, one unit per window, bmcam003 first)

```bash
ACCEPT_DIFF=1 HIL_RUN_DIR=runs/pool_rehearsal_20261012 hil/tools/hil_deploy_window.sh bmcam003 development <S7> bmcam003/live_20260925 2026-10-14T05:00:00Z h146
```

- **The script:** catches the Pi at :00, disarms + kills the cycle, runs rc_field_update `--ref development` (runtime tar
  backup in /home/pi/backups), then:
  - verifies software_sha.txt starts with S;
  - re-arms from the ARMED backup;
  - halts before :10.
- **Then I check:** the crontab is byte-identical to the production @reboot line, and `print-config` hash = 44b81292.
- **The deploy wake makes no image**, and the deploy drops the unit's pending heal list ("re-ask after a deploy"). The EM /
  backend re-asks the open heals after each deploy.
- **bmcam004:** the same at the next window (11 PM, 06:00Z), after bmcam003's 10 PM deploy is verified.
- **Indoors / no GPS fallback:** hil_bus_always_on → deploy → re-arm → halt → hil_restore_schedule, swapped pairs.

## 2. Switch on (console `set`, in a listen window; console lane via nereus000)

Per unit, at the first production wake after its deploy (the focus_send.sh pattern: wait for the post-transmit listen
window, then publish):
- bmcam003 via SPOT-31593C: `bm pub bmcam/cmd {"id":66006,"c":"set","kv":{"uplink.media_key.heal_order":"after"}} 1 1`
- bmcam004 via SPOT-33507C: `bm pub bmcam/cmd {"id":66007,"c":"set","kv":{"uplink.media_key.heal_order":"after"}} 1 1`

Expect `[bmcamNNN] OK id=… uplink.media_key.heal_order: before -> after (next action) cfg=<new hash>`.

## 3. Verify (next 2 wakes per unit)

- **Cycle log:** keyed nrjxl, `[CFG] … hash=<new hash>`. Heal chunks are sent AFTER the image burst (the heal line comes
  after END, not "before START"). No `[CFG][ERR]` / skips / overruns.
- **Console:** WS / START / END and the heal chunks in order; transmit-data submits vs msgs.
- **Media table:** the new images complete; open heals get filled; the first-send START/early chunks are no longer lost
  to the heal burst (the purpose of #146).

## Rollback

- **Behaviour:** console `set {"uplink.media_key.heal_order":"before"}` per unit (next action).
- **Code:** rc_field_update's runtime tar in /home/pi/backups (printed in gate.log), or a redeploy of 9ec4cb7 with
  hil_deploy_window.
- **Crontab:** ~/hil_backup/<TS>/crontab_ARMED.txt (byte-check).

## Approval line Nick pastes in TE2's chat

> OK: deploy development <S> (bm #146) to bmcam003 and bmcam004 with hil_deploy_window in their hourly windows (bus holds allowed if a window can't be used), then console set uplink.media_key.heal_order after on bmcam003 (SPOT-31593C) and bmcam004 (SPOT-33507C); rollback = set heal_order before.

# Deploy bm #146 (uplink.media_key.heal_order) to bmcam003 + bmcam004: TE2 procedure (Tue 10/13 8:55 PM PDT)

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

---

## IN FORCE (Nick 9:03 PM typed OK): #146 + #147 together (one merged sha S, registry v13; still.raw.progressive default flipped to OFF), held bus

1. **Pre-checks:**
   - pairing (hil.env + rig_map);
   - `git diff 9ec4cb7 S -- BM_Devel_Pi` = #146 + #147 + pool files only;
   - tests;
   - `still.raw.progressive` default False in S's registry.
2. **Bus hold on both** (hil_bus_always_on, swapped pairs): the Pis come up disarmed, with the ARMED backup recorded.
3. **Runtime update on each held unit:**
   - `tools/rc_field_update.sh --ref <S> --profile bmcam00X/live_20260925 --leave-disarmed`, the same step hil_deploy_window
     runs (staged from the unit checkout after git fetch);
   - print-config parity: the diff must be exactly the 2 new key lines, else stop;
   - software_sha = S.
   - Then: re-arm from the bus-hold ARMED backup → byte-check → halt.
4. **Memory check on bmcam003 FIRST:**
   - bus power-cycle (bridge commit) → production cycle → in its listen window:
     `set {"still.raw.progressive":true}` (id 66006) → OK;
   - halts → commit again → cycle 2 encodes with `-p`. Read the cycle log:
     - cjxl args include `-p`;
     - `[RAW] … peak_rss` (cjxl VmHWM);
     - msgs vs the 195 cap;
     - no `[RAW] FALLBACK`, no kill.
   - **Bar:** cjxl VmHWM ≤ 150 MiB (and < the 250 guard), msgs ≤ 195. FAIL → `set still.raw.progressive false` and stop.
5. **PASS:**
   - bmcam003: `set {"uplink.media_key.heal_order":"after"}` (id 66007);
   - bmcam004: one cycle → `set {"still.raw.progressive":true,"uplink.media_key.heal_order":"after"}` (id 66008) → OK.
6. **hil_restore_schedule both** (halted + armed checks) → the hourly wakes verify 2 wakes per unit:
   - `-p` in the cjxl args;
   - heal chunks after END;
   - keyed nrjxl;
   - delivery in the media table;
   - a dashboard preview.
   - The backend re-asks heals dropped by the deploy wake.
7. **Rollback:**
   - console `set {"still.raw.progressive":false,"uplink.media_key.heal_order":"before"}` per unit;
   - code: rc_field_update runtime tar or a 9ec4cb7 redeploy.

**Approval line (revised):**
> OK: deploy development <S> (bm #146 + #147) to bmcam003 and bmcam004 (always-on bus holds and bus power-cycles allowed to force production cycles), console set still.raw.progressive true and uplink.media_key.heal_order after on bmcam003 (SPOT-31593C) and bmcam004 (SPOT-33507C) after the bmcam003 memory check passes, then restore the hourly schedule; rollback = set both keys back (progressive false, heal_order before).

---

## DECISION 9:00 PM (Nick via EM): #146 ONLY tonight

- **#147 skipped:** not yet tested on real hardware. No -p memory check, no `still.raw.progressive` set.
- **Plan = sections 0–3 above + Rollback**; the original #146 approval line stands. S = development after the EM merges
  #146 (QE pending). Bus holds are allowed.
- **If S also contains #147** (its registry key present, default OFF): the print-config diff will show 2 lines, not 1.
  Verify `still.raw.progressive` = false before accepting the diff, and leave it unset.

## FINAL 9:03 PM: Nick's typed OK covers #146 + #147. The "IN FORCE" section above (steps 1–7) is the sequence; the #146-only decision just above is void

### Rollback point (recorded 9:00 PM, read-only, during the 9 PM wake)

| | bmcam003 | bmcam004 |
|---|---|---|
| runtime `software_sha.txt` | 9ec4cb7ae7c1 | 9ec4cb7ae7c1 |
| effective cfg (cycle log `[CFG]`) | hash=44b81292, registry v11, overlay 6 keys | hash=44b81292, registry v11 |
| crontab | production `@reboot … rc_run_capture_cycle.sh` (armed) | same |
| pre-deploy tar | written by tonight's deploy: `/home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam003_<TS>.tgz` (path → gate.log) | same pattern, bmcam004 |

- Older tars on the units (003 20261008T200147Z, 004 20261009T210139Z) are 9ec4cb7 too, but with older config.
  Don't use them.
- **Never `tar xzf` the whole backup to roll back.** It would restore:
  - `bm_media_key_last.txt`: the key counter, so media keys sent after the deploy would be REUSED;
  - `sent/` and `bm_command_state_v2.json`: command high-water + pending heals.
  `am_rollback.sh code` restores only the runtime files the deploy overwrote.

### One-command AM rollback (run on the Mac from this folder)

- **Behaviour** (Nick-approved; ~1 wake; start it at ~:55, or inside a :00–:07 window):
  `bash runs/pool_rehearsal_20261012/am_rollback.sh keys`
  - Sends a console `reset` of both keys (ids 66009 bmcam003, 66010 bmcam004) in the next listen window. That drops the
    keys from the overlay: progressive false, heal_order before, cfg hash back to 44b81292.
  - S with defaults is wire-identical to 9ec4cb7 (#146 default "before" = today's wire; #147 default OFF renders
    byte-identical to registry v11).
  - Note: `reset` instead of `set …false/before` gives the same values and leaves no v13 key in the overlay, which a
    9ec4cb7 code rollback needs.
- **Code** (back to 9ec4cb7; this is a deploy, so it needs Nick's own typed OK; after `keys`):
  `bash runs/pool_rehearsal_20261012/am_rollback.sh code`
  - Bus hold → restore only the overwritten runtime files + software_sha.txt from tonight's tar → verify sha
    9ec4cb7ae7c1 + `[CFG] hash=44b81292 registry=v11` → re-arm (byte-check) → halt → hil_restore_schedule.

### Regression bars (set before the test; per unit: the forced verify boot(s) + the next 2 hourly wakes vs the 8 PM / 9 PM wakes)

| Bar | Baseline (8 PM / 9 PM wakes, 9ec4cb7) | Pass |
|---|---|---|
| Delivery | complete or heal-tracked | every new image complete, or open in the media table with heals planned |
| msgs | ≤ 195 (8 PM: 191 / 191) | ≤ 195 |
| CFG | `[CFG] … hash=44b81292`, no `[CFG][ERR]` / dropped keys | new hash with exactly the 2 overlay keys; no ERR / dropped |
| Fallbacks | no `[RAW][FALLBACK]`, keyed nrjxl | the same |
| cjxl memory (-p) | peak_rss ~98 MiB (9 PM, 003) | ≤ 150 MiB, < 250 guard, no kill |
| Cycle time / halt | from the baseline logs (deploy_s/<host>/baseline_*) | within the 480 s budget, and halt ≤ baseline + 30 s |
| Heal order | heals BEFORE START | after the switch-on: heal chunks after END |
| -p proof | n/a | cjxl argv contains `-p` (deploy_s sampler: the cycle log does not print it) + dashboard preview |

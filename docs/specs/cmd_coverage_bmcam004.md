# Spec: every remote command, tested on a real camera before NOAA (bmcam004, console) (PE 004 rev 4, tier (a); rev 1 = the TE's console-path facts; rev 2 = Nick's larger-crop decision; rev 3 = must dos 6/6b judged on the production encode search; rev 4 = QE 007's rev 2/3 findings R1–R7 + nits; before any QE PASS)

Tier (a) (TEAM_RULES §13): bench hardware, and it gates what the backend may send to a field unit. QE spec PASS comes
before the first command. Home after PASS: bm_cam_legacy `docs/specs/cmd_coverage_bmcam004.md`, byte-identical (the TE
commits it). Board: card c_cmd_coverage (PjM). Author: Principal Engineer 003, Sun 10/18 5:26 PM PT; rev 3 and rev 4
Principal Engineer 004, Sun 10/18 7:56 PM and 8:01 PM PT.

- **Why (Nick, Sun 10/18 5:21 PM PT, comment in the NOAA release review doc, thread 6a9c38a2, verbatim):** "Have all
  possible commands been tested over the console fist to verify the cameras accept them and then take an action
  correctly? … I think part of this soak testing needs to be having one of the cameras run through all the different
  commands we can send and verify it works as expected. If that means taking one camera for just this test then thats
  worth it - we can't afford to brick a unit in the ocean because we never tested a remote command. and i still want to
  know if I can have the unit take a full crop image and compress it to 195 messages." Also 5:37 PM, thread 30ec0dbe, on
  "The TE's one-command check of the live catalog": "Run test on all of the commands with bmcam004". Epic E4 (bar: remote
  commands).
- **Provenance rule for anyone quoting Nick from a Claude Doc:** only rows with `via` "frame" are Nick's own. Replies that
  sessions post through the docs connector also show actor "Nick", self:true, but `via` "mcp". All quotes here are `via`
  "frame" rows.
- **Facts at start:**
  - S2 2cc1afdb (the freeze candidate) has 10 command verbs (`command_v9.render_help`): ping, help, get, set, reset,
    cfm, trg, hld, rsd, wap.
  - It has 115 config keys (`config_registry.py`). Guard classes: none, guarded_revert, guarded_stage, locked
    (5 file-path keys, never settable), service (HMAC-signed only).
  - **Guarded keys (rev 4):** the guard applies only to the values in the key's `guard_when` (other values behave as class
    none). guarded_revert = mode.output (guard_when save_local), uplink.uart.port, uplink.uart.baudrate, commands.enabled,
    commands.topic. They apply at once and revert by themselves unless confirmed with `cfm`, within `boot3_2h` for
    mode.output and `tx2_boot3` for the rest (`command_guards.limit_for`, command_guards.py:62-63; command_v9.py:564).
    guarded_stage = power.halt.enabled and power.bus_always_on (guard_when True): staged, applied only after `cfm`. A
    signed service key is revert-guarded (`guard_class`).
  - Hardware runs so far covered ping/get/set/rsd/hld and a handful of keys (window, heal_offwin_max, the v8 roi/hlt/twn
    fold in S2's bench, 9/26).
  - Today's soak gate found an accepted remote key that is dropped at the next boot (c_tz_overlay_boot). That is the
    class of bug this test hunts.
  - The NOAA format refuses crops above 1600 × 900 px (`RAW_MAX_PX`, config_validate.py:305,325).
  - **Encode time (rev 3):** the NOAA B3a path (`still.raw.layout` rgb) runs a byte-target SEARCH of several cjxl encodes per
    image, and `still.raw.rgb_encode_max_s` (registry default 45 s) caps the whole search, not one encode: over it the unit
    sends the JPEG instead (rfb=time; rc_raw_jxl.py `encode_cap_s` / `attempt` @ 2cc1afdb). S2 at 1600×900 in Sunday's
    gate: att=2 in 19.9–21.9 s, att=3 in 29.3–32.5 s (~10 s per attempt). Hypothesis, not a result: if time scales with
    area, 2400×1350 needs ~22 s per attempt and the two larger crops can't finish 2 attempts in 45 s. Monday measures it.
  - **Quality at 195 messages, desk (rev 4):** bm `runs/s28_crop_sweep_20261005/crop_table.md`, 30 TG-7 frames, rgbwa (linear
    RGB VarDCT, the B3a encoder) at 195 messages. Distance P50 by crop: 1600×900 2.58; 2304×1296 5.94; 2880×1620 10.30;
    3200×1800 13.70; 4608×2592 21.59 (fits 20/30). Central-detail score vs today's JPEG (P50): +20.6 / −2.0 / −22.6 / −36.5 /
    −55.4. Only 1600×900 beats today's JPEG at P90. 2400×1350 isn't in the table. So K2 and K3 below are expected to fail
    `d_max` 10.4 on reef-like scenes, and K1 trades central detail for field of view.
  - **Memory (rev 4):** the product logs cjxl's VmHWM (`peak_rss_kb`, rc_raw_jxl.py:819-858, 910), not VmPeak. The encoder
    guard is `ulimit -v` 250 MiB, which caps VmPeak; a breach kills cjxl and the unit sends the JPEG (rfb=mem). Desk B3a at
    1600×900 (bm `runs/s28_b3a_vmpeak_20261005/README.md`): VmPeak 123.8–125.3 MiB vs VmHWM 98.3–98.9 MiB, so VmPeak runs
    ~26 MiB (×1.27) above VmHWM. No VmPeak sampler for rc_raw_jxl.py's own cjxl exists in `hil/tools` on development 5c2c6f0.
  - **CLI budget (rev 4):** the `rc_raw_jxl.py` CLI runs with a fixed 3600 s budget and reserve_msgs 0 (rc_raw_jxl.py:1335,
    1459-1470). The field's room check includes the heal reserve (:1019), so 6b's predicted-wake bar is the only stand-in for
    the wake budget. Its rgb search cap is `DEFAULT_CONFIG` `rgb_encode_max_s`, the registry's 45 s (:154); there is no flag.
- **Console-path facts (TE2, Sun ~5:30 PM, from today's gate; binding on the method):**
  - `get` over the console does not echo values. Its reply is `OK id=N get <keys>: n key(s) cfg=<hash>`, so values are
    verified by that hash plus the next boot's `[CFG]` lines (config v2 … hash=, applied/effective), or by print-config
    over ssh.
  - A command in the listen tail keeps the Pi awake to the 480 s budget. So wake time is measured only on a separate boot
    with no commands (an uncommanded off-window wake halts at ~174 s).
  - Command ids are deduplicated persistently; a reused id answers "duplicate: original answer". Ids come from a file
    counter, never reused, except the deliberate repeated-id row. The TE's ids stay in 1–99999; backend ids (heal asks,
    d30-B re-sends) are ≥ 100000.
  - rsd limits: ≤ 40 chunks per command (RSD_MAX_CHUNKS), ≤ 8 [key, ranges] pairs (RSD_MAX_HEALS), one pending heal per
    media key (a second rsd for the same key replaces the first).
  - `schedule.timezone` by overlay always fails at boot (c_tz_overlay_boot). One confirmation row, expected LIMITED (known).
  - About 8 min per forced boot, halt included. Tools: the TE's gate_step.sh (per-step boot + commands + evidence) and
    summarize_gate.py (~/NereusData/bm_cam_legacy/2026-10-17_gate_offwin_heals/).
  - SPOT-33507C delivers nothing today, so on bmcam004 only the console path can be verified (see must do 7). Nick moves
    it to a window on Monday (NOAA doc thread 1527f387), so it may start delivering mid-run (must do 2, C8).
- **Must do:**
  1. **Matrix first.** From `config_registry.py` and `command_v9.py` @ 2cc1afdb, plus the backend's live catalog v14 GET
     (which keys the backend lets an admin send), write `matrix.csv`: one row per verb and per key, with type,
     range/enum, guard and `guard_when`, apply timing (next_action / next_boot), the backend-writable flag, the test value
     (valid, non-default), one invalid value, and the expected observable effect ("none on the bench" is allowed, stated
     per row). Commit it before the first command.
     - **Tier 1** (must finish by Wed 10/21 noon): all 10 verbs; every key the backend can send to SPOT-33361C; the crop
       keys.
     - **Tier 2:** every other settable key.
  2. **Unit, hand-over and Monday order (rev 4).** bmcam004 on S2 2cc1afdb with its soak config. It leaves the soak
     (unscored there). Commands go over the USB console path only (no cellular). In this order, each step done before the
     next:
     1. The TE writes the hand-over.
     2. Back up the unit's config, the v9 state file and crontab, with sha256; check the S2 rollback tar (must do 8).
     3. Nick's OK on the TE's test card, in the TE chat, citing the card's sha.
     4. **Keep backend traffic out (R6):** the TE turns SPOT-33507C's backend rollout `self_heal` and `remote_commands` OFF
        (as d49 A did for the gate) and logs the GET that shows both off. They go back ON at the restore (must do 9). This is
        a per-Spotter admin setting on a bench Spotter, not a code deploy; Nick's card OK covers it. Nick can still move the
        Spotter on Monday.
     5. Encode-only runs (must dos 6/6b), never overlapping a camera wake (between wakes, or with the wake cron held per the
        card).
     6. Commit `matrix.csv`, then start the commands.
  3. **Per settable key, guard class none (and guarded keys at values outside `guard_when`), in batches:**
     - (a) set the valid test value → ok-ack; `get`'s cfg hash and the next boot's `[CFG]` lines (or print-config) show the
       value; the hash changes as predicted;
     - (b) the expected effect is seen on the next action or wake (e.g. window → skip_win; crop → image size; quality
       → bytes/messages);
     - (c) cold reboot → the value is still in effect (no `[CFG][ERR]`, not dropped);
     - (d) safety: on the effect boot, 0 FALLBACK/ERR, 0 kernel OOM (dmesg), SD free; for keys that change the encode, cjxl
       `peak_rss_kb` ≤ 170 MiB and 0 rfb=mem (memory method under 6b); supervisor RSS reported where the product logs it, not
       gated; wake time (≤ 481 s) on a separate boot with no commands, only for keys that can change wake work (capture,
       encode, transmit, heal, window);
     - (e) the invalid value → refused with an error code, no change;
     - (f) `reset` to the soak value → `get` and hash back to the soak state.
     A `set` with 2 keys checks "all-or-none" once (one valid key + one invalid → neither applied).
     **Batching (to fit ~8 min per boot):** keys whose effects can be told apart are set in one batch, up to 8 keys, each
     with its own `set` and id. A batch is: sets → effect boot → cold reboot (survival for all of them) → invalid values
     (refused at set, no boot) → reset all → verify boot. That's ~3 boots (~25 min) per batch. A batch with any failure is
     re-run key by key. Keys whose effects interact (e.g. two encode settings) never share a batch.
  3b. **Guarded, locked and service keys (rev 4, R2): each runs alone, never in a batch.**
     - **guarded_stage** (power.halt.enabled, power.bus_always_on at True): set → `get`/hash show it staged and the next
       boot shows it NOT applied → `cfm` → applied → then (b), (c), (d), (e), (f) as in must do 3.
     - **guarded_revert, harmless value** (mode.output save_local): set + `cfm` → (b) → (c) survives past its `boot3_2h`
       limit → (e) → (f). Its no-cfm revert is must do 4's row.
     - **guarded_revert keys that cut the command path** (commands.enabled false, commands.topic, uplink.uart.port,
       uplink.uart.baudrate at any non-soak value): tested ONLY without `cfm`. Pass = the unit applies it, then reverts by
       itself within `tx2_boot3`, and answers ping and a reset afterwards. The confirmed path (set + `cfm`) is never run on
       the bench: it would leave the unit deaf. In the ocean that is a brick, so for SPOT-33361C each of these keys is ruled
       UNSAFE-by-design at a confirmed non-soak value: it must be not backend-writable, or blocked by the backend (desk
       check in must do 7, against catalog v14's writable flag).
     - **locked** keys: only (e), a refusal. **Service** keys: only (e) unsigned → refused. Signed service sets are out of
       scope (no service key is issued for this test).
  4. **Per verb:**
     - ping, help, get (key / short / group / "journal");
     - reset (key, group, `{"all":1}` once, then restore);
     - cfm on a guarded_stage key (staged until confirmed; must do 3b);
     - a guarded_revert key set without cfm reverts by itself within its limit: commands.enabled false (rev 4: the one that
       matters in the ocean), plus mode.output save_local for the `boot3_2h` limit;
     - trg 0–4; hld (n minutes, then 0 releases);
     - rsd (a valid shape `{"h":[[key,"0-39"]]}`; 41 chunks refused; 9 pairs refused; a 2nd rsd for the same key replaces
       the 1st; `{"x":1}` cancel);
     - wap 0–2 (WiFi on, then off; the unit sleeps normally after);
     - a locked key → refused; a service key without a signature → refused;
     - a repeated command id → not re-applied.
  5. **Anti-brick check:** before the run, the TE picks one in-range setting expected to make a capture fail (e.g.
     `still.raw.rgb_encode_max_s` 5, too short to finish an encode) and writes it in the matrix. This key has no guard, so
     "recovers" means (rev 4): on that wake and the next 2 the unit answers ping, sends the fallback JPEG (rfb=time) instead
     of hanging or crash-looping, and accepts the reset; the wake after the reset sends a JPEG XL. The guard-based recovery
     is must do 4's commands.enabled row.
  6. **Crop question (Nick):** run the encode-only measurement on bmcam004 (~1.5 h), with the full frame (4608 × 2592) as one
     measure-only candidate, run only through rc_raw_jxl.py's R0.3 opt-in flag that lifts RAW_MAX_PX for an encode-only
     test (rc_raw_jxl.py:1492–1497), never through the production set path, with the inputs, method and bars of 6b. Through
     `set`, the full-frame crop is expected to be refused (row (e)). That is the safety check working, not a failure.
  6b. **Larger crop for the freeze (Nick, NOAA doc, Sun 5:28 PM, thread 6a9c38a2: "I think we should be testing if the larger
     crop works with the camera, and if it does then that should go into the code freeze"; 5:38 PM, thread 57adfd92, on the
     call "Test the larger crop on bmcam004 in the soak; into the freeze if it passes": "YES YES YES!". His YES came before
     the PE's 5:43 PM correction that a pick needs a new build, so the build decision stays his, Mon PM.)** Must do 6's
     encode-only run comes in the Monday order of must do 2.
     - **Candidates (centred, even x/y, so the measured rect is the one a build would ship):** K0 1600×900 [1504, 846, 1600,
       900] (reference); K1 2400×1350 [1104, 620, 2400, 1350]; K2 3200×1800 [704, 396, 3200, 1800]; K3 4608×2592 [0, 0, 4608,
       2592].
     - **Pi inputs (rev 4, named now; n = 5 per candidate, the same 5 for each):** bmcam004's own underwater frames from the
       pool test run Wed 10/14 (focus 0.8, the NOAA focus), under
       `~/NereusData/bm_cam_legacy/2026-10-17_pool_pull/bmcam004/pool_runs/20261014_190821_bmcam004/`, each with its
       `.metadata.json` beside it (sha256 first 12):
       1. `loop/20261014T205004Z_loop_01_still.dng` fa4792d34c71
       2. `loop/20261014T205204Z_loop_02_still.dng` 8e0cf4436ccf
       3. `loop/20261014T205404Z_loop_03_still.dng` 54efc2e008b7
       4. `sweep/204023/20261014T204041Z_sweep_lp0.80.dng` 7995a7903682
       5. `sweep/210724/20261014T210743Z_sweep_lp0.80.dng` 622f8b2ae229
       The TE copies them to the unit, checks the full sha256 there, and records the full hashes in the run MANIFEST. No
       night inputs: the NOAA profile takes new images only 07:00–12:00 America/Los_Angeles (10:00–15:00 ET), so night isn't
       in the pick. Pool frames compress better than a reef, which is why the desk bar below exists.
     - **Pi method (rev 3, rev 4 nits):** `rc_raw_jxl.py` CLI with `--allow-any-crop`, running the PRODUCTION search with the
       NOAA soak profile's `still.raw.*` values: `--layout rgb`, the profile's effort, distances, progressive, target_fill
       (never `--target-fill 0`, which times one rung) and d_max 10.4, and `--message-cap 195`. The rgb search cap comes from
       the registry default (45 s; the CLI has no flag for it). If the profile's `still.raw.rgb_encode_max_s` isn't 45, the TE
       stops and tells the PE before any encode. Per input, record: rfb code (if any), attempts, every attempt's seconds, total
       search seconds, final distance, messages, and cjxl `peak_rss_kb`.
     - **Memory method (rev 4, R3):** the bar is on what the product logs, cjxl `peak_rss_kb` (VmHWM): ≤ 170 MiB. That is
       225 MiB VmPeak less the ~26 MiB (×1.27) gap measured at 1600×900, rounded down. The `ulimit -v` 250 MiB guard is the
       VmPeak check itself: a breach shows as rfb=mem, which fails the 0-rfb bar. No new sampler, so nothing new goes into
       `hil/tools` before Monday.
     - **Desk set for site-like scenes (rev 4, R7):** on the Mac, rerun the 10/05 desk sweep for K1 2400×1350 only, on the
       same 30 TG-7 frames (`runs/s28_crop_sweep_20261005/tg7_selection.json`) with the same tool and arguments as its
       `manifest_crop.json`; table by `tools/s28_crop_table.py`. One heavy Mac slot (PjM all-clear). The TE runs it, or a
       session the PjM names. K2 and K3 already have desk rows.
     - **A candidate is pickable only when all of these hold (worst case, set now):**
       - Pi, on all 5 inputs: 0 rfb of any kind (time at the 45 s cap and mem included) AND `peak_rss_kb` ≤ 170 MiB AND each
         encode ≤ 36 s AND ≤ 195 messages at distance ≤ `d_max` 10.4 AND predicted image wake ≤ 450 s. Predicted wake =
         W_S2 + (the candidate's search seconds − K0's on that input). W_S2 = the longest in-window image wake (wake → halt)
         of S2 in the soak, either unit, read once at the start of the Monday run from the TE's soak summary
         (summarize_gate.py output over the soak run folder) and written in the MANIFEST with the file name.
       - Desk, on the 30 TG-7 frames at 195 messages: fits 30/30 AND distance P90 ≤ 10.4.
       Anything else = not pickable, with the reason.
     - **One unit:** the Pi numbers rest on bmcam004 alone (TEAM_RULES §9: no recommendation from n = 1 unit). They can rule
       a crop out, not prove it safe. A pick is proven only by the restarted soak's full-wake bars on the new build.
     - **Information only (not a pass path):** for a candidate that fails only on rfb=time, report its first attempt's
       seconds. Raising `rgb_encode_max_s` is a profile change; it would need its own soak, so it isn't part of this pick.
     - **For Nick:** one sample per candidate per Pi input set (day), decoded the same way as K0 on the same input, so he
       compares like for like, beside the desk table's distance and central-detail rows for each candidate (Facts). The TE
       posts the table and samples by **Mon 2 PM**; the PE turns them into a side-by-side page for his pick.
     - **What a pick means:** the cap is one code constant (`RAW_MAX_PX`, config_validate.py:305; rc_raw_jxl.py imports it;
       tests/test_s28_raw_jxl.py:985 pins it; docs/bmcam_config_catalog.json exports it as `raw_max_px`). The backend refuses
       crops above its vendored catalog's `raw_max_px` (nvd remote_config.py:294), so a pick also needs an nvd catalog
       re-vendor. A pick means a new build under its own tier (a) spec (SPEC_crop_cap_s3, PE) + QE, the re-vendor, a soak
       restart and a Mon 10/26 go/no-go. That's not this test's scope. If no candidate is pickable, nothing changes.
  7. **What bmcam004 can't show (desk check instead):** the backend → Spotter → camera path for each key. The transport is
     covered by the soak's command bars on bmcam003 and the TE's d38 one-command check. On top of that, a desk cross-check:
     for every Tier 1 key, the backend catalog v14 entry (name, short name, type, range or enum, encoding) must equal the
     camera registry's @ 2cc1afdb. A mismatch = a key the backend could send wrong → blocked as UNSAFE until fixed. Also
     record, per must do 3b, whether catalog v14 marks each command-path key backend-writable.
  8. **Recoverability during the run:** the bus held on (hil_bus_always_on), ssh kept, the S2 rollback tar on the unit
     (/home/pi/backups/BM_Devel_Pi_before_rc_deploy_bmcam004_20261018T1536*Z.tgz) checked before the first command. Every
     key is reset before the next batch.
  9. **Restore before Thu:** at Wed 10/21 noon the restore comes before any further rows (rev 4, R5). bmcam004 back to S2 +
     its soak config. The config hash, sha and crontab match the backups, and SPOT-33507C's `self_heal` and
     `remote_commands` are back ON (GET logged), by Wed 10/21 6 PM, in time for the Thu rehearsal (E4 must do 4/9).
  10. **Report:** `matrix.csv` filled (verdict + evidence per row), RESULTS.md, and a one-page summary for Nick (counts by
     verdict, every UNSAFE/LIMITED row in plain words, the crop answer with the desk rows).
- **Verdicts per row (set now; rev 4 maps every case, R1):**
  - **SAFE:** every step that row runs ((a)–(f), or its must do 3b / 4 steps) as expected.
  - **UNSAFE:** any hang, crash loop, data loss, a wake > 481 s, cjxl `peak_rss_kb` > 170 MiB or an rfb=mem, or the unit deaf
    to commands. Also UNSAFE, because each accepts what it should refuse, re-applies, or leaves the unit off its known state:
    - (e) an invalid value accepted; all-or-none broken (one key of the pair applied);
    - a locked key, or an unsigned service key, accepted;
    - a repeated command id re-applied;
    - (f) `reset` doesn't bring `get`'s hash back to the soak state;
    - a guarded_revert key not reverted within its limit; a guarded_stage key applied before `cfm`;
    - C2: 2 lost commands on one row (deaf), unless ping over the same console shows a console-path fault at that time; then
      fix the path and re-run the row, and a repeat loss is UNSAFE.
  - **LIMITED:** only a documented-behaviour difference: the effect differs from the help text but stays in range and
    harmless, or the value doesn't survive a reboot (e.g. the known timezone row).
  - **NOT OBSERVABLE:** accepted and persisted, but no bench-visible effect. Stated, not a failure.
- **What happens on findings (set now):**
  - UNSAFE on a key the backend can send → the backend marks that key not writable for SPOT-33361C before 10/27 (catalog or
    plan check). No camera change, so the freeze holds. That is a staging (= BM production) change, so it is its own tier (b)
    nvd PR + QE, landing after the Wed 10/21 soak read-out and before the Fri 10/23 go/no-go (rev 4, N6); the PjM times it.
  - UNSAFE in a verb or in the base build itself (can't be blocked at the backend) → Nick decides: fix it, which means a new
    build, a soak restart and a Mon 10/26 go/no-go, or ship with the verb blocked or documented.
  - LIMITED → the 10/27 runbook's known-limits list.
- **Pass / fail:**
  - Done = 100 % of Tier 1 rows have a verdict with evidence by Wed noon (the must do 7 desk check included), and bmcam004 is
    restored.
  - **A Tier 1 key with no verdict at Wed noon counts as UNSAFE for the backend rule (not writable for SPOT-33361C), unless
    Nick decides otherwise in writing (rev 4, R5).** An untested verb with no verdict goes to Nick as a base-build finding.
  - The NOAA build passes = 0 UNSAFE rows left reachable from the backend for SPOT-33361C, and 0 cases needing hands to
    recover.
  - Tier 2 rows not done by Wed noon continue after 10/27, listed.
- **Failure modes:**

  | # | What goes wrong | How it's detected | Then |
  |---|---|---|---|
  | C1 | The unit needs hands to recover (bricked on the bench) | No ping/ack after the guard's wakes | TE power-cycles; row = UNSAFE; stop and tell PE + PjM before continuing |
  | C2 | A command is lost on the console path | No ack within 2 wakes | Resend the same id (not re-applied); 2 losses on one row → UNSAFE (deaf) unless ping shows a console-path fault (see Verdicts) |
  | C3 | A changed value leaks into the next row | `get` + hash check after each (f) | Reset all to the backup, re-run that row |
  | C4 | The SD fills (e.g. `keep_crop`) | SD free per wake; stop < 2 GB | Clear the run's own files, re-check |
  | C5 | Wifi left on (wap) drains the battery | `wap` row ends with WiFi off; power reading | Turn off; if refused → UNSAFE |
  | C6 | Time runs out | Tier 1 progress at Tue 6 PM | Tier 2 deferred; never cut Tier 1 or the restore; Tier 1 rows left at Wed noon count as UNSAFE for the backend (Pass / fail) |
  | C7 | bmcam004 not restored for the Thu rehearsal | Hash/sha/crontab check Wed 6 PM | Rehearsal waits; PjM re-plans (E4 rev 11 timing) |
  | C8 | Backend traffic reaches bmcam004 mid-run (rev 4, R6): a heal ask replaces the TE's pending rsd, a d30-B re-send lands on a "no commands" wake-time boot, or state changes that C3 would blame on the last row | Any journal command id outside the TE's 1–99999, or an rsd the TE didn't send; the logged GET shows SPOT-33507C's switches | Turn the switches off again if they were on; re-run every row that ran since the last clean check |
  | C9 | A command-path key confirmed by mistake (must do 3b) leaves the unit deaf | No ack, no ping on the console | Recover over ssh from the backup (must do 8); row = UNSAFE; stop and tell PE + PjM |

- **Test plan:** TE, bmcam004 over the console, Mon 10/19 AM → Wed 10/21 noon (Tier 1), with the TE's test card committed
  first (templates/test_card.md) and approved by Nick in the TE chat. n = every row once. A reboot-survival repeat (c) is
  needed only for keys whose apply timing is next_boot, or that are kept in the overlay. Desk K1 run: one heavy Mac slot Mon
  AM. Cost: ~2 TE days; Nick ~10 min (the card); one heavy Mac slot (< 1 h). Displaces: bmcam004's report-only soak health
  and its share of the d28 bench (now carried by bmcam003 alone, PE ruling 7:57 PM on pe_specs).
- **Out of scope:** Spotter-level commands (remote reset, hrpm; Sofar's); signed service sets; any code change before the
  freeze; backend plan/UI changes beyond marking a key not writable.
- **Done:** TEAM_RULES §4 + every must do and C-row checked off with evidence; PE + QE accept item by item.

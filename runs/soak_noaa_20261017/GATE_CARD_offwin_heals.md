# Bench gate card: off-window heal budget (p_e4_offwin), bmcam003 + bmcam004, Sat 10/17 AM (pre-registered)

- Owner: TE2. Drafted Thu 10/15.
- Runs ONLY on Nick's own approval line in the TE2 chat, citing this file's sha. The soak card (TEST_CARD.md, rev 3) is approved separately.
- **Build under test:** bm development at the merge sha **S2** (TBD at Sat AM merge). It is fdeb01a + the camera change "every wake (in or out of the window) sends ≤ `<heal_offwin_max>` heal chunks; the window restricts NEW images only".
  - Key name/range per the builder's PR, confirmed at QE; written `uplink.media_key.heal_offwin_max` below. Default 40; 0 = today's behaviour.

## Question

On the camera:
- do off-window wakes send heal chunks up to the cap and nothing else (no capture, no START, no new image);
- are in-window wakes unchanged;
- what does a capped off-window heal cost in energy per wake?

This card tests the mechanism only. 24 h completeness stays in the soak.

## Conditions

- **Units:** bmcam003 / SPOT-31593C, bmcam004 / SPOT-33507C, dry on the bench at home.
  - Spotters outdoors with open sky (GPS for media keys + cellular).
  - nereus000 on the 3 A supply, both consoles live.
- **Path:** held bus (Spotter controller off) with forced production boots: console `bridge cfg commit` = bus power-cycle → the armed production cycle runs → it halts itself. The same pattern as the 10/13 deploy and memcheck.
- **Profile:** d35 keys set at the start: `schedule.timezone=America/New_York`, `schedule.window.enabled=true`, `start=10:00`, `end=15:00`.
- **Making "now" off-window during Sat AM:** Sat 8–11 AM PDT = 11:00–14:00 ET, which is INSIDE d35. So for the off-window phase the window is temporarily `03:00–04:00` (ET) by console `set`, then returned to `10:00–15:00` for the in-window phase and the soak.
- **Heal work:** before each off-window boot, inject a known heal backlog by console:
  - `{"c":"rsd","h":[["<key>","0-59"]]}`: 60 chunks of an image key the unit already holds in `sent/` (the newest image's key);
  - the backend dedupes the re-sent keyed chunks.
- **Varied:** `uplink.media_key.heal_offwin_max` ∈ {0, 40}, crossover:
  - bmcam003: 0 → 40;
  - bmcam004: 40 → 0;
  - this removes unit and Spotter (Notecard model) bias.
- **Held fixed:** code S2, pacing 1.0 s/msg, focus 0.8, keys progressive=true / heal_order=after, the bench scene.

## n (why enough)

- **Mechanism:** each cell (unit × cap) = 6 off-window boots, so 24 off-window boots. Plus 2 in-window boots per unit. A deterministic mechanism should show 24/24; one counter-example fails it.
- **Energy:** 12 off-window boots per cap level (6 per unit, crossover).
  - Report the mean per-wake energy per cap, and the difference with a 95 % CI (Welch).
  - Based on earlier per-wake spreads, 12 vs 12 resolves a difference of roughly the size of 40 extra 1-s transmit slots.
- **Time:** about 4.5 min per off-window boot (boot ~45 s, `<WS>`, ≤ 40 heals at 1 s, 150 s listen tail, halt). 12 per unit in parallel ≈ 70 min + setup and in-window boots: **~2.5 h**.

## Metrics + pass bars (set now)

| # | Check | Pass |
|---|---|---|
| G1 | Off-window boot, cap 40, backlog 60 | cycle log `[HEAL] sent 40 heal chunk(s)` (= min(cap, backlog)); console transmit-data submits = `<WS a=skip_win>` + 40 heal (+ `<HL>` status), within ±1 status message; NO `[RC] native ready` / `nrjxl ready`; NO `<START` on the console; NO new backend media row from that wake. 12/12 |
| G2 | Off-window boot, cap 0 | 0 heal chunks; `<WS a=skip_win>` only (today's behaviour). 12/12 |
| G3 | Backlog < cap | sends exactly the backlog. 1 boot per unit (inject 10 chunks, cap 40 → 10) |
| G4 | In-window boot (window `10:00–15:00` ET, Sat AM = in window) | unchanged from fdeb01a: capture, `nrjxl ready`, START…END, heals after END, msgs ≤ 195, cycle end ≤ 470.7 s / halt ≤ 481 s. 2/2 per unit |
| G5 | Commands during off-window wakes | one console `get` per unit, sent in an off-window listen tail → `OK id=`. 2/2 |
| G6 | Errors | 0 `[CFG][ERR]` / FALLBACK / crash in any gate log |
| G7 | **Energy per off-window wake, cap 40 vs cap 0** | **Bar: ______ (Nick sets it before the test)** |

**Proposed energy measure (G7):**
- E_wake = Σ V·(I − I_idle)·Δt over the bridge `power |` samples (10 s) from the commit (bus-on) to the Pi halt (current drops to the halted baseline I_idle, measured over 60 s after each halt).
- Report J per off-window wake for cap 0 and cap 40, and their difference.
- Also report the d35 daily projection: 19 × E_off(cap) + 5 × E_in, against today's fdeb01a all-day schedule (24 × E_in), in Wh/day and as a percentage.
- Suggested bar shape for Nick: "cap 40 adds ≤ __ J per off-window wake" or "d35 + cap 40 daily energy ≤ __ % of today's".

## Stop rules

- Any G1/G2 counter-example (a capture or START outside the window, or heals > cap) → stop: the mechanism FAILS; restore the units to fdeb01a production (below) and report.
- nereus000 VIN < 4.9 V or VBAT falling while the bus is held, or Spotter battery < 3.80 V → end the holds, restore the schedule.
- SPOT-33507C `THERMAL_FAULT` with a falling battery → stop bmcam004's part.
- A boot that misses its listen window twice → stop that unit, bench investigate.
- Never: SPOT-33361C / bmcam001 / bmcam002.

## Exact verbs (Sat AM, ~8:15 AM PDT start)

Units halted (start :12–:55). `deploy_s.sh` = `bash runs/pool_rehearsal_20261012/deploy_s.sh`. Console via nereus000.

1. **Pre:**
   - `grep -E '^export HIL_RIG_[AB]=' hil/hil.env`;
   - `git diff --stat fdeb01a <S2> -- BM_Devel_Pi` = the off-window heal change only;
   - sky-view `post` on both Spotters.
2. **Hold:** `HIL_RUN_DIR=<run> hil/tools/hil_bus_always_on.sh SPOT-31593C`, then the same for `SPOT-33507C`.
3. **Deploy S2:**
   - `deploy_s.sh deploy bmcam003 <S2_7>` and `deploy_s.sh deploy bmcam004 <S2_7>` (rc_field_update `--ref development --leave-disarmed`);
   - print-config parity must pass. If the only diff is the new key's line, re-run with `accept`. Any other diff → STOP;
   - check software_sha = S2.
4. **Re-arm + halt:** `deploy_s.sh rearm <host>`, `deploy_s.sh halt <host>`.
5. **Profile, in the first forced boot's listen window per unit** (`deploy_s.sh boot <host>` → `deploy_s.sh send <host> <id> '<kv>'`):
   - 66201 / 66202: `{"schedule.timezone":"America/New_York","schedule.window.enabled":true,"schedule.window.start":"03:00","schedule.window.end":"04:00"}` (off-window phase);
   - plus the cap: bmcam003 66203 `{"uplink.media_key.heal_offwin_max":0}`, bmcam004 66204 `{"uplink.media_key.heal_offwin_max":40}`.
6. **Off-window boots, 6 per unit:**
   - before each one, console `{"id":662NN,"c":"rsd","h":[["<newest key>","0-59"]]}` (ids 66210–66239);
   - then `deploy_s.sh boot <host>`, `deploy_s.sh report <host>`.
   - Crossover: set the cap the other way (bmcam003 66240 `{"uplink.media_key.heal_offwin_max":40}`, bmcam004 66241 `{"uplink.media_key.heal_offwin_max":0}`), then 6 more boots per unit.
   - G3: one boot with an `rsd` of `0-9`.
   - G5: console `{"id":662NN,"c":"get","k":["camera.focus.lens_position"]}` in one off-window listen tail per unit.
7. **In-window phase:** set the window back with 66250 / 66251 `{"schedule.window.start":"10:00","schedule.window.end":"15:00"}`, then 2 boots per unit (G4).
8. **Soak config:** 66252 / 66253 `{"uplink.media_key.heal_offwin_max":40}` on both. Then `hil/tools/hil_restore_schedule.sh SPOT-31593C` / `SPOT-33507C` (hourly 3600000 / 600000). The soak clock (TEST_CARD rev 3) starts at the next in-window wake.

**Rollback / restore:**
- Behaviour: console `reset` of the cap key + the window keys, which returns to the d35-less fdeb01a behaviour on S2 code.
- Code: redeploy fdeb01a (`deploy_s.sh deploy` on a held bus at ref fdeb01a, via rc_field_update; needs Nick's line), or keep S2 with cap 0 (= today's behaviour).
- The schedule always ends at `hil_restore_schedule` hourly.

## Cost

- Both bench units + Spotters + nereus000, Sat ~8 AM → ~11 AM PDT.
- ~30 forced boots: real cellular sends, including ~600 re-sent heal chunks.
- TE2 ~3.5 h.

## Is it worth it?

d35 + off-window heals is the NOAA release candidate. Without the mechanism proven, the Sat–Mon soak can't attribute a late-window completeness result to the change. Nick also needs an energy number before he sets the cap.

---

## Rev 2 (Thu 10/15 ~11:40 AM PDT): QE on bm #181 (d35)

- **Cap key:** `uplink.media_key.heal_offwin_max`, range 0..40, default 40. Substituted throughout above.
- **Evidence line for G1/G2/G3:** every off-window wake prints, at cap 0 too:
  `[HEAL] off-window wake: cap= heal_chunks= hl= uplink_msgs= heal_s= tail_s= cycle_s= uptime_s=`.
  - Pass for G1: `heal_chunks` = min(cap, backlog) and ≤ cap.
  - Pass for G2: `cap=0 heal_chunks=0`.
  - The console counts must agree with `uplink_msgs` (± 1 status).
- **QE nit 1, tail_s on full-backlog wakes (added to G1):** on every cap-40 / backlog ≥ 40 off-window wake:
  - `tail_s` ≥ the configured post-transmit listen tail (150 s), so heals never shorten the command listen;
  - `cycle_s` ≤ 480 s budget;
  - `uptime_s` at halt ≤ 481 s.
  12/12.
- **QE nit 2, give-up horizon (new G8):** `wakes_left` now ages on off-window wakes too.
  - Expected from the code: HEAL_WAKES = 3, so a pending heal list is given up after **3 wakes ≈ 3 h on the hourly schedule (0.125 day)**.
  - Before #181, under d35 only in-window wakes aged it: up to ~1 day.
  - Test, bmcam003 only: cap 40, inject `rsd` `0-149` (150 chunks), then 4 off-window boots.
  - Expect `heal_chunks` 40 / 40 / 40, then the list given up (the 4th wake plans 0 from it; the remaining 30 chunks are left for the backend re-ask).
  - Record `wakes_left` per wake and report the horizon in hours and days. Pass = matches the #181 code's constant.
  - The 24 h completeness impact belongs to the soak: the backend re-ask (REASK 5400 s) must refill it.
- **Time:** G8 adds ~20 min (4 boots). Total ~3 h.

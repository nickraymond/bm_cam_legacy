# Spec card: d35 heals on every wake, new images only in the window (BACKFILL, draft rev 3)

Backfill (TEAM_RULES §13, team PR #14): the code is built (bm #181 @ c7f08a9b, QE PASS-with-nits, not merged). This card
sets what "done" is accepted against. It adds no new build before the soak. rev 1 = QE spec review fixes (Thu 10/15
5:40 PM) + Nick's energy bar (d45); rev 2 = G7 read as the heal increment (Nick, Thu ~10 PM). Home when QE passes: bm_cam_legacy `docs/specs/d35_offwin_heals.md` (routed by the PjM).
Author: Principal Engineer, Thu 10/15.

- **Ticket / board card:** p_e4_offwin (decisions d35, d45) · **Goal / epic:** `noaa` / E4 · **Owner session:** "Camera: capped heals on off-window wakes (d35, E4)" [9bec13] · **Repo / branch:** bm_cam_legacy, PR #181 → `development`
- **Why (Nick, d35, 10/15 ~9:35 AM, PgM chat):** "Sadly we don't have the energy to send images all day long." The window limits NEW images only; every wake spends a capped heal budget on images still missing within 2 weeks.
- **Must do (numbered, testable):**
  1. In-window wake: unchanged from the v13 build (capture, send new image, heals per the existing order).
  2. Off-window wake: 0 captures, 0 START, 0 new images; sends only backend-requested (`rsd`) heal chunks, newest first, at most `uplink.media_key.heal_offwin_max` chunks (INT 0..40, default 40), then the normal command listen tail, then halt.
  3. `heal_offwin_max = 0` gives exactly the v13 off-window wake (golden `v9_offwin_heal_cap0`).
  4. The cap is a remote key in catalog v14 and changes by one remote command, no SSH, no dive.
  5. The off-window heal pass never shortens the command listen tail: `tail_s == listen_tail_s` on the **full-backlog case** (40 chunks pending plus the lane wait), the case of the earlier QE nit.
  6. **Cross-repo contract (backend):** the backend asks (`rsd`) for missing chunks of every image ≤ 14 days old, including on off-window wakes. The camera never finds holes itself, so the 2-week lookback Nick asked for exists only if the backend reach-back is ON for that unit. Today that setting has no owner (c_heal14).
- **Failure modes in the field:**

  | # | What can go wrong | How we detect it (where Nick sees it) | What happens then |
  |---|---|---|---|
  | F1 | Night heals drain the battery (cap too high; no solar at night) | Energy per wake from the bridge `power` samples vs the d45 bar; Spotter battery V on the dashboard | Lower the cap by remote command (40 → 20 → 0); 0 = v13 behaviour |
  | F2 | Spotter UTC read fails (bmcam002 profile has `allow_system_clock_fallback: false`): every wake sends `<WS a=skip_win>` with reason "window", so **no new images while heals keep flowing and the unit looks healthy**. Predates d35 (v13 skipped the same way); d35 makes it quieter | (a) the image bar read as "an image for every scheduled in-window wake" (below); (b) backend check: a `skip_win` at an in-window hour by the backend clock, or with empty `local_time` | Soak: fails the image bar. Field: a backend alert is a gap (p_alerts), not before 10/22 |
  | F3 | Timezone wrong but valid (not America/New_York): window shifts, captures land at the wrong hours | First in-window images' capture times vs 10:00–15:00 ET; config hash on update day | Fix the profile by remote command; update-day checklist checks the tz and hash |
  | F4 | Backend on catalog v13 refuses the key (`unknown_key`) | Command result on the dashboard | d38: unit default 40 applies; the TE's one-command check after nvd #151 merges |
  | F5 | Backend reach-back OFF for bmcam002: old images never re-asked | Images incomplete > 24 h on the dashboard; backend setting read on update day | Turn reach-back ON (backend, needs an owner); must do 6 |
  | F6 | Late-window images (13:00/14:00 ET) miss the 24 h bar: heals arrive 20–22 h later and a second heal round misses (pre-registered risk) | Soak headline bar | Raise the cap or change the window by remote command; Nick decides at go/no-go |
  | F7 | Bus-off or power loss mid-heal-send | Exception in the wake log; tail still runs | The pending list lives in CommandState (`command_state.py:325-340`: unique tmp + fsync + os.replace + dir fsync). Check it is intact and the next wake resends the unsent chunks (gate check (a) below) |
  | F8 | Heal asks expire after ~3 wakes (`wakes_left` now ages off-window), so a long gap drops them (`<HL a=dropped r=expired>`) | `HL dropped` lines; backend re-ask count | Backend re-asks; PjM decision on `wakes_left` pending (builder REC: keep) |
  | F9 | Uplink volume: up to 40 chunks on every off-window wake with pending asks (d28's ask cap is off) multiplies daily messages and can back up the Spotter queue | Per-day sum of `uplink_msgs` from the `[HEAL]` off-window wake lines; commands ≤ 3 h and images ≤ 24 h bars catch the harm | Report the daily count in RESULTS; lower the cap by command if the queue backs up |

- **Pass / fail (pinned before the Sat soak; soak card rev 3.2 + gate card **Rev 4 @ 92921f76** (`feature/r1-hil-test-engineer`, `runs/soak_noaa_20261017/GATE_CARD_offwin_heals.md:61-80`) hold the rest):**
  - pass = **an image for every scheduled in-window wake** (n = 22, of which 10 late-window) AND 100 % of them complete ≤ 24 h AND commands 100 % answered ≤ 3 h AND every off-window wake: heal chunks ≤ cap, 0 captures, 0 START AND 0 FALLBACK/ERR AND every scheduled wake present AND halt ≤ 481 s AND the energy bar.
  - **Energy bar (G7, Nick d45).** His words: "Less than 25% of an image wake, which should be comparable to the number of messages sent." (PE chat, Thu 10/15 by 9:26 PM). Asked which part, he picked "The heals only" (PE chat, ~10 PM), confirmed on the board thread 635f1305 at 10:15 PM PT: "G7=B, only the messages being sent".
    - **The bar:** the energy the heals ADD must be < 25 % of the median in-window image wake on the same unit (~125 J; TE predicts ~30–35 J). Added energy = median(off-window wake at cap 40) − median(off-window wake at cap 0).
    - **Method (gate card Rev 4 @ 92921f76, :61-80):** G1/G2 boots only (backlog 60); n = 6 per cell per unit; a cap-40 boot counts only if it sent heal_chunks > 0 (so an idle wake can't pass it trivially); E_img = median of the unit's G4 boots (n = 2); joules from the bridge `power` samples.
    - **Report beside it, not gated:** the whole-wake ratio (~46 % at 0 messages per the TE, mostly the 150 s listen tail) and the message ratio (heal chunks ÷ image messages, ~40/195 ≈ 21 %).
  - fail = any of the above missed. Worst performer rule: the worse unit of bmcam003/004 decides.
- **Test plan:** TE gate G1–G8 on bmcam003/004 (Sat AM, after the merges), then the ≥ 48 h soak Sat–Mon on the NOAA profile; read-out Tue 10/20 ~11:30 AM PT. `PROPOSED` (TE decides if it fits the gate), two added checks, no code: (a) cut power mid-heal-send once per unit; confirm the CommandState pending list is intact and the next wake resends; (b) one wake with the Spotter UTC read failing, if the console can force it, else write "not tested". Cost: the soak already booked; ~30 min of TE time.
- **Out of scope:** heal packer and asks per day (d28, nvd #149); same-id re-send (d30-B, nvd #152); FEC; new alert engine (p_alerts); the window hours themselves (Nick, remote key).
- **Done means:** TEAM_RULES §4 + every must do checked off with evidence (soak RESULTS lines); every F row tested or marked "known risk, accepted by Nick" on the go/no-go card; the energy bar met; must do 6 has a named owner and is verified ON for bmcam002 before 10/22.

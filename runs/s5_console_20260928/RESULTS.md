# S5 console proof — bmcam003 RESULTS (2026-09-28)

**bmcam003 ladder: PASS with two findings fixed on the branch (F7, F8), one wire limit found
at the Spotter console (F5, L11 FAIL as specified), and one Spotter-side delivery problem
(F1).** bmcam004 and the 24 h conductor loop are not done yet (see "Not done").

Code: `development` 0f67784 (S4 via catch-up PR #87), then `feature/sprint26-s5-console-proof`
93501f2 (F7) from 17:22Z. Evidence: `ladder.log` (every command, the unit's console answer,
decoded cellular payloads, state sha256 before/after), `gate.log`, `pulled/`.
Tools written here: `cmd.sh`, `con_decode.py`, `pistate.sh`, `wake.sh`, `waitlog.sh`,
`watcher_s5.sh`, `console.sh` (from S3c).

## Setup

| step | when (Z) | result |
|---|---|---|
| 0 hold SPOT-33507C bus ON | 16:36:01 | read back 0; `power on for: 4294967295`; Pi was halted (no cut) |
| 1 catch + disarm | 16:36:57 | watcher: SIGTERM, cron disarmed, backups in `/home/pi/s5bench/backup` |
| 2 deploy development | 16:38 | 0f6778492c87, SUMMARY PASS, print-config + v2 parity OK, hash 580ce986 |
| 2 service key | | present on the Pi since 2026-09-26; Mac copy matches (sha256 bdefebb42c15) |
| 3 config_v2_upgrade | 16:39:20 | dry-run then `--write`: `video.storage.*` → `storage.*`, hash 580ce986 unchanged; old file kept `.before_upgrade_20260928T163920Z` |
| 4 armed production wake | 16:40 | supervisor per_boot video, real halt at 480 s. START **lost at the Spotter** (F1), so 0dxabb is `length_unknown` (unhealable). Wake 2 (16:49, 0dxar5): 169/185, healed by L10 |

W8b on the wire: `<WS v=1 a=skip_win cfg=55e424b4 up=6 …>` (16:58), START
`… key=0dxcaf, cfg=d9e81104, tg=51081, r=1600x900+1504+846, m=30 …` (17:23),
`… key=0dxcmd, cfg=d9e81104, tg=51092, m=80, d=3.0 …` (17:30). Staging: 0dxcaf 37/37 and
0dxcmd 80/80 COMPLETE.

## Ladder

| # | step | result | evidence |
|---|---|---|---|
| L1 | ping (mid-burst → inbox → answered after the burst), ping in tail, help, get mode/r, get power, get journal | PASS; `help` added **0** cellular sends. `help` omits get/cfm/hld (F2) | 51001–51006 |
| L2 | duplicates: get 51003, set 51010, set 51070 after its reset, hld 51091, rsd 100056, wap 51150, signed set 100000010 | PASS: original answer + "duplicate", state sha unchanged, nothing re-applied, no second cellular copy. The mote delivered many commands 2–3× within 0.5 s; every copy deduped | |
| L3 | NaN, space (` `), non-ASCII (`é`), oversize, cross-key (b manual w/o gains), unknown key, locked (`commands.runtime`), unsigned service key, v8 verb `roi`, out of range, video cap 40, bool-as-int, duplicate JSON key | PASS: `DROPPED (no ack)` for NaN / dup key; `e=val` space, non-ASCII, range, bool; `xk` WB + cap floor; `key`; `lock`; `auth`; `cmd`. Hash, overlay, guarded and journal unchanged (the state file changes only by `result_cache`, by design). Oversize: never reaches the unit (F5) | 51040–51062 |
| L4 | set/reset back to the original hash; CAS | PASS: 55e424b4 → bfaf6f4a → 55e424b4; `b` mismatch `e=cas`, match OK | 51069–51074 |
| L5 | manual WB gains in END `cg` | PASS: END `cg: 1.62:1.91` | 51080, 51081 |
| L6 | trg kv both media, hash unchanged, START tg/r/m/d; `med` override | PASS (see W8b lines above); `med=still` ran a still on a video unit | 51081, 51092 |
| L7 | 2×2×2 media × run × output | PASS, 8/8: P·V·T (production), P·S·T (51081), P·V·SL (51100 one-shot), P·S·SL (51101 one-shot), S·V·T (51110), S·V·SL (51112), S·S·SL (51114), S·S·T (51120). stay_on next-boot sets → exit 72, restart in 5 s, not counted | |
| L8 | hld on the held bus; clamp | PASS: without bus_always_on `hld 30` → 1 min "clamped"; after `cfm` + next boot `hld 120` granted. Clamp on the *scheduled* bus: at restore (below) | 51020–51024, 51091 |
| L9 | guarded stage + revert across a bus power cycle | PASS: `power.bus_always_on` and `power.halt.enabled` staged (hash unchanged), `power.halt.enabled` survived a clean halt + bus cycle staged, `cfm` after boot applied it. `mode.output save_local` (51111) unconfirmed: boots 1,2 (exit 72), 3 (bus cycle), reverted at boot 4 (bus cycle): `<CF v=1 h=0209090f reverted=mode.output lim=boot3 ref=51111>` cellular | 51021/22, 51111, 51130–51132 |
| L10 | rsd, wap | PASS: backend-allocated rsd 100056 (0dxar5 38-45,124-131) → 16 heal chunks before START, `<HL … r=ok id=100056>`; dup not re-queued. wap 2 dispatched once (dup not); wap 1 AP up (`ssid=bmcam003`, seen from the Mac), ssh blocked; wap 0 → HQ, ssh back in 3 s | |
| L11 | a 270 B console line | **FAIL (Spotter, F5)**: the USB console takes ≤ 256 B incl. newline; 257+ → `RX overflow - clearing buffer!`. 256 B line (234 B JSON) reaches the unit | 51050–51056 |
| L12 | signed service set | PASS: signed `uplink.chunk_chars 360` OK + cellular ack + `<CF>`; replay → duplicate; flipped sig `e=auth`; older signed id `e=old`; signed reset back to 0209090f; service high-water 100000012. Guard record showed inherited uptime (F8) | 100000005–12 |

## Findings

- **F1 (Spotter, not fixed here):** after the bus-hold commit, SPOT-33507C's Notecard synced
  every 1–6 min, each sync holding `MS_Q_CELLULAR_ONLY` full for up to ~40 s: 90 `Unable to
  submit` in the first production wake (START, END and `<HL>` lost → 0dxabb unhealable until
  W9), 16 lost in the second. Sprint25 measured 3.4 s stalls. Triggered small media later
  arrived complete.
- **F2 (fix pending, wire):** v9 `help` has no lines for get / cfm / hld / help
  (`command_v9.render_help`). The help text is on the wire (golden v9_still), so the fix is its
  own commit with the re-recorded golden.
- **F3 (minor, text):** `cfm` of a next-action key prints "applied"; it governs from the next
  decision point (hld in the same tail was still clamped).
- **F4 (minor):** the journal's `old` is the overlay value (`none -> 03:00`), not the effective
  value the console line shows (`00:00 -> 03:00`).
- **F5 (wire limit):** the Spotter USB console line limit is 256 B incl. newline, not 270 B. On
  the console the largest command is 234 B JSON (signed: ~210 B + sig). Space and non-ASCII
  can't be typed on the console (the Spotter splits on spaces; the monitor drops non-ASCII);
  only JSON escapes reach the unit. Whether the Sofar API lane has the same limit is S6.
- **F6 (tooling):** my Bash tool expands `\uXXXX` in command text; escaped payloads were
  written from Python.
- **F7 (FIXED 93501f2):** a trg heard during a hld on a held bus waited for the next boot: the
  W10 check compared it to the spent 480 s boot budget. With `power.bus_always_on` it now fires
  at once on a fresh budget. Proven on hardware (51100/51101 fired during the hold).
- **F8 (FIXED, commit pending):** `note_guards` returned early when nothing was guarded without
  moving the uptime mark, so a new guarded key inherited all unguarded time (377 s on the bench;
  a stay_on unit up > 2 h would revert a fresh guarded set at the first note). A per_boot hold
  noted nothing until it ended. Now the mark always moves and a hold notes once a minute.
- **O1 (pre-existing stills behaviour):** `still.message_cap` is not a ceiling when even q9
  does not fit: m=30 sent 38 (`<WS a=inc rsn=cap pln=38>`).
- **O2 (by design):** a per_boot save_local still sends one `<WS a=cap>`; stay_on sends none.

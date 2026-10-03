# Test Engineer state — G4 in progress (written 2026-10-03 ~03:55Z / 20:55 PDT, before a context compaction)

Read this first after the compaction. Times UTC unless marked PDT (UTC−7). Worktree:
`/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.claude/worktrees/vigilant-proskuriakova-a3337c`,
branch `feature/r1-hil-test-engineer` (PRs go to the EM, who has Nick's standing merge authority; open PR
#112, plus later commits on the branch not yet in a PR).

## 1. Timeline

| item | value |
|---|---|
| G4 | T0 = the **04:00Z** wake (21:00 PDT Fri 10/2) → end **16:00Z** (09:00 PDT Sat 10/3); 3 h completion tail after |
| G4 RESULTS | `runs/g4_outdoor12h_20261002/RESULTS.md` due **~08:15 PDT (15:15Z)** with a G5 go/no-go |
| G5 | Sat 09:00 PDT → Sun 09:00 PDT (shifted with G4), solar, **no nereus000**, no human; schedule (1 h vs 30 min) decided in the morning from G4 phases |
| ship decision | Sun 10/4 (Nick) |

## 2. G4 criteria (hil/gates/G4_outdoor_tethered_12h.md, amended)

- G4.1–G4.9 as in the file; `post` (G4.9) N/A: **console is OBSERVE ONLY during G4 (no console sends)**.
- **D1 rule (G4.3):** count only clips from COMPLETE cycles; clips cut by a power event are OBSERVED EVENTS (G4.10).
- **G4.10:** Spotter power-cycle / stub cuts = observed events (time, unit, next wake normal?); FAIL only on
  SD/filesystem damage or loss beyond that one cycle. **No protective SSH halts** of stub-booted Pis (Nick
  dropped the stub guard) — EXCEPT inside `hil_restore_schedule.sh`, which halts the stub boot by design.
- **G4.11:** commanded media = captured media (allowing the measured lag, in wakes).
- **G4.12:** every backend command acked, or confirmed by a later `<WS>`/START hash.
- Amended plan: still↔video hourly via backend remote-config (Sofar lane); D3 `trg` via backend; self-heal auto.

## 3. Automation running (stop rules!)

| what | where | schedule | stop |
|---|---|---|---|
| **alternator** `hil-g4-alternator.timer` (+ `.service`) | nereus000 (pi@192.168.1.45), script `/home/pi/hil_g4/hil_g4_alternator.py`, log `/home/pi/hil_g4/alternator.jsonl` | hourly **:20 UTC**, until 15:00Z; target media for the NEXT wake = still on even UTC hours, video on odd; `trg` at 05:20, 09:20, 13:20Z; allow-list BMCAM_003/004; retries 409 | `sudo systemctl disable --now hil-g4-alternator.timer && sudo rm /etc/systemd/system/hil-g4-alternator.* && sudo systemctl daemon-reload` — **MUST be stopped before G5** (nereus000 leaves) |
| **red watcher** (Mac, read-only) | `/private/tmp/claude-501/…/scratchpad/g4_watch.sh` → `runs/g4_outdoor12h_20261002/watch.log`; background task id `b3c8q55vl` | hourly :14 for 03–15Z; exits with `RED …` on: unit didn't wake, wake→halt > 590 s, nereus000 unreachable twice, alternator send failed; logs AMBER (rebootctl/charge mode) | TaskStop `b3c8q55vl` |
| **caffeinate** (Mac awake 14 h) | pid 88617, started 02:21Z | until ~16:21Z | `kill 88617` |
| **05:38Z timer** (reminder to run the 22:50 PDT analysis) | background task `buo825em2` | fires 05:38Z | — |

nereus000 hardening (done, persists): Wi-Fi power save off `/etc/NetworkManager/conf.d/90-hil-wifi-powersave.conf`
(proven across a reboot 02:22Z); journald persistent `/etc/systemd/journald.conf.d/90-hil-persistent.conf`.
Units already had power save off (hand-made drop-ins). Standard: `hil/procedures/BENCH_HOST_SETUP.md`,
`bmcam-provision` Phase 1b.

## 4. Units / rigs now

| unit | Spotter / bridge | runtime | config | mode | bus |
|---|---|---|---|---|---|
| bmcam003 | SPOT-33507C / c3c564b91856226c | development 34a6222 (#106, registry v7) | base 67f930c4 (overlay empty except what the alternator sets) | per_boot, cron armed, real halt | production 1/3600000/600000, outdoors in the box |
| bmcam004 | SPOT-31593C / 0e582dd12c1e1480 | same | same | same | same |

Heal cap 24/day per Spotter (EM). BMCAM_000 on SPOT-31593C is retired: ignore. Never send to SPOT-33361C.

## 5. Phase plan (Nick, staged, one variable at a time)

| phase | when | change | how | measure |
|---|---|---|---|---|
| 1 | 04:00Z → | baseline (cap 184, 1 wake/h) | — | per-clip loss, heals served |
| **2** | **~05:50Z (22:50 PDT), only if heals aren't clearly improving** | `video.send.message_cap` 184 → **126** on BOTH | `hil/tools/hil_sofar_change.sh P2.<dev> BMCAM_00x '{"set":{"video.send.message_cap":126}}'` (Sofar lane; plan pre-checked OK both; handle 409 rate_limited by retrying after retry_after_s; keep clear of the :20 alternator) | log in gate.log as "G4 phase 2", T = first VIDEO wake on 126 (expect 07:00Z; video = odd UTC hours); ≥ 2 video wakes per unit: chunks/clip, first-send loss (count + where), queue-full per burst, heal chunks served, older clips completing? Hypothesis: a shorter burst cuts tail loss below the ~31/h that 40-chunk heals can repair |
| **3** | after phase 2 has ≥ 2 video wakes per unit → realistically the **:20 off-window after the 09:00Z wake (~09:20Z, 02:20 PDT)** | both bridges every 30 min: `hil/tools/hil_restore_schedule.sh SPOT-33507C 1800000 600000`, then SPOT-31593C (each refuses if its Pi is up; commits; halts the stub boot; reads back); keep cap 126; EM raises heal cap to 48/day at the same time (tell the EM when) | **Nick's OK ALREADY GIVEN in this chat at 03:50Z:** "OK: bridge cfg set/commit on SPOT-33507C and SPOT-31593C to change the bus schedule to every 30 min (1800000/600000) for G4 phase 3" | time-to-complete per clip, queue drain between 30-min bursts (queue-full trend), alternator cadence: decide hourly vs adjust (the timer's "next wake" target logic assumes hourly wakes; with :30 wakes, a :20 command lands at :30) and tell the EM |

No code changes (Nick). Sprint27 is preparing a heal fix (one source of truth for heal chunks/wake, > 40, maybe 100) in parallel.

## 6. Deliverable at 22:50 PDT (05:50Z) to the EM

Per clip since 04:00Z, both units: missing at first send → missing now, which rsd served it (console
`Remote message received … "c":"rsd"` + `<HL …>` lines), any normal clip on track for ≤ 3 h; and whether a
100-chunk heal fits the 10-min window at the measured 1.54 s/msg (wake→halt today ≈ 494–505 s with ≤ 1 heal
command; +100 chunks ≈ +154 s → ≈ 650–660 s > 600 s window → likely needs a longer window or fewer
chunks; confirm from the per-wake heal counts). Backend read-only via nereus000:
`GET /admin/ingest/devices/<dev>/heal-candidates?hours=6` (admin token in `~/.config/nereus/heal_driver.env`
on nereus000 only); remote-config view `GET /devices/<dev>/remote-config`.

## 7. Approvals from Nick (in this chat) and their scope

| approval | scope |
|---|---|
| "console cmd.txt OK for G3 on both Spotters" (10/1 23:21 PDT) | G3 console sends. **Not** for G4 (console observe-only). |
| "bridge cfg OK for production schedule on both Spotters" (10/2) | the production switch (done) |
| phase-3 bridge OK (10/2 20:50 PDT, quoted above) | the 30-min schedule change, both Spotters |
| EM relays (not approvals): merges, heal caps, plan decisions | — |

## 8. Open findings

- **SPOT-33507C self-reboot** at its boot-anchored hourly health check (:04): `[ORC] Running health check!` →
  `[SYS] [ERROR] rebootctl reset N. Source: 7` (23:04:23Z and 00:04:28Z) → bridge 120 s stub → Pi cut
  (00:04Z: mid-burst clip 0e510h, then stub-boot clip 0e59kp 5/183). Observed events; on Nick's Sofar ticket.
- **Heal starvation (D1):** backend packs ONE rsd/wake, newest clip first, ≤ 40 chunks, truncating; older
  clips `left_out` → never complete when hourly loss (~40–45 tail chunks on SPOT-31593C) ≈ cap. 40 is
  hard-coded: unit `command_messages.RSD_MAX_CHUNKS=40` (refuses > 40), `rc_heal.HEAL_CAP_PER_WAKE=40`, nvd
  `heal_commands.MAX_CHUNKS_PER_COMMAND=40` (le 60). Notes: `analysis/heal_mechanism_notes.md`.
- Queue-full per burst (console): SPOT-31593C 48–62, SPOT-33507C 13–16 (02:00–03:00Z).
- Power-event clips (tracked separately, not D1): 004 23:04Z stub 0e50ig, 003 00:05Z 0e59kp, 003 00:04Z 0e510h.
- nereus000 outage 00:22–02:10Z = network-only (Wi-Fi power save), fixed.
- Earlier (in RESULTS): F-G3-1..10, M106 PASS, F-G3-5 ranges.

## 9. Where things are

| what | path |
|---|---|
| G4 run | `runs/g4_outdoor12h_20261002/` (gate.log, watch.log, wakes.csv, analysis/, pulled/) |
| shakedown / M106 / production switch | `runs/s27_m_ladder_20261002/` (RESULTS.md, gate.log, analysis/d1_clip_tracker.csv) |
| G3 | `runs/g3_hardmode_20261001/RESULTS.md` |
| gate specs | `hil/gates/G3_*.md`, `G4_*.md`, `G5_*.md`, `X1_15s_clips.md` |
| tools (all rig-guarded; `hil/tools/test_hil_guards.sh` must print 0 failures) | `hil/tools/`: hil_wake_report.sh (per-wake read-only report), hil_sofar_change.sh, hil_restore_schedule.sh (SPOT [interval ms] [window ms]), hil_g4_alternator.py, hil_change.sh, hil_cmd.sh/hil_console.sh, hil_unit_snapshot.sh, hil_deploy_unit.sh, hil_refresh.sh, … |
| current run pointer | `hil/.current_run` (= runs/g4_outdoor12h_20261002) |
| memory | `~/.claude/projects/-Users-nickbuemond-Documents-GitHub-bm-cam-legacy/memory/project-r1-test-engineer.md` |

Reporting: ONE line per step to "Engineering Manager coordination" (session id local_f63137ac-…); red
items immediately; ≤ 5 bullets to Nick.

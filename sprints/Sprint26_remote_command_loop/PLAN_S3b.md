# Sprint26 S3b — stay_on: implementation plan

Written 2026-09-27. Status: **APPROVED (Nick, 2026-09-27): H1–H12 as proposed, heartbeat_s default 300, bench RSS option (a).**
Branch `feature/sprint26-s3b-stay-on` from `origin/development` 1636c8b (S3a / PR #79 merged).
Spec: `DESIGN_supervisor.md` §4 (stay_on, SIGTERM, hardening, Budget, Time), §8.2, §8.3 S3b
row, §10 O3/O5; `REVIEW_20260925.md` K4, K6, R7 (+ A's amendment); `PLAN_S3a.md` G1–G5 + W6
note; `runs/s3a_bench_20260926/RESULTS.md` §4–5.

Baseline on this Mac (.venv-dev, PyYAML 6.0.2, Pillow 12.3.0): suite **1112 passed**
(`tests/test_reference_card_color_utils.py` excluded), 146 s. Every commit re-runs the goldens
(legacy + supervisor), `tests/test_config_v2_parity.py` and the full suite BEFORE it is
committed. Legacy vectors never change (G2). per_boot supervisor vectors change only in the
W10 commit.

Facts this plan rests on (inspection at 1636c8b):
- Nothing reads `mode.run` at runtime. The registry allows only `per_boot`
  (`config_registry.py:114-116`, `runnable=`), enforced by `config_v2._cross_key_errors`
  (`:107-111`). At boot, a v2 file saying `stay_on` is unusable, so the unit falls back to the
  v1 file (legacy, per_boot). `mode.interval_s` and `mode.heartbeat_s` (§5.2) are not in the
  registry.
- `Boot.start` (`rc_supervisor.py:73-94`) welds three things together: the per-action budget,
  the port session (`bm_port.new_session`) and the daemon start. A second call raises
  `PortRefused`. After `finish()` the port is closed (`get()` is refused) and the daemon can't
  restart (`_stop` is permanent).
- `daemon.heal_events` is never cleared (`command_daemon.py:217,438`). Under stay_on, every
  action would re-send every old `<HL>`, and the list grows without bound.
- No signal handling exists anywhere. The bench/field tools stop a cycle with
  `pkill -TERM` and rely on "SIGTERM = no finally = no halt" (watcher, bmcam-field-update).
- The capture retry sleeps `time.sleep(60)` hard-coded between attempts
  (`rc_capture.py:783`). The worst case is ≈ 4×30 + 3×60 s.
- `cron_logs/` has no rotation. It holds one `rc_cycle_*.log` per boot, and
  `supervisor_actions.jsonl` is append-only. The only rotation pattern in the repo is
  `config_journal.py` (`os.replace` → `.1`).
- The action log's `rss_kb` is `ru_maxrss`: a peak that never falls, so it can't drive a
  ceiling.
- Staging's `<WS>` parser maps an unknown `a=` value to `wake_action=<code>` and still advances
  `last_seen` (`nereus-vision-dev backend/app/services/bm_sofar_telemetry.py:123-133`). So
  `a=idle` and `a=crashloop` ingest today with no backend change.
- The golden harness already has `at_clock` rules and answers every re-subscribe with a fresh
  UTC. It needs:
  - an exit for a long-lived `main()`;
  - a supervisor-only scenario class (no legacy counterpart);
  - per-action summaries (today only the last is kept).

## 0. Gaps in the spec — for Nick's ruling

**H1 — Registry keys (REGISTRY_VERSION 2 → 3, as S3a.4).**
- New keys:
  - `mode.interval_s`: INT, default 0 (= trigger-only), 0 or 60–86400.
  - `mode.heartbeat_s`: INT, default 300, 0 = off, else 60–86400.
- `mode.run` becomes runnable for `stay_on`, with two cross-key rules: stay_on needs
  `commands.enabled: true` (no daemon means no trg or heartbeat) and `commands.runtime: supervisor`.
  - A strict load (deploy) refuses a violation.
  - At boot, the existing fatal → v1 fallback applies, which is per_boot legacy with a loud line.
- The hardening limits are module constants in S3b, not keys: backoff, crash cap, RSS ceiling,
  log sizes. S4 can promote them.
- `mode.run` gets no guard class in S3b, because there is no `set` until S4 and it is set only
  in YAML. S4 picks its guard.
- Every v2 hash changes again. bmcam003/004 get a re-verified hash at deploy.

**H2 — stay_on schedule.**
- Boot sequence: daemon, then drain, then decide.
  - A pending trg fires at once.
  - With `interval_s > 0`, the first scheduled action runs at boot. The next is due at the
    previous scheduled action's start + `interval_s`.
- A trg action does not move the schedule.
- A slot missed because an action ran long is not caught up: next = max(due + interval, now).

**H3 — Window skips in stay_on.**
- A scheduled repeat outside the window goes through the action's gate as today: a fresh time
  read, and `trg` bypasses it (D-S12-4).
- Proposal: only the FIRST skip of a run of skips sends `<WS a=skip_win>`. Later ones log only.
  - With per_boot parity instead (every skip sends), `interval_s: 60` outside the window would
    cost 60 cellular messages an hour.
- No listen tail after a skip (see H4).

**H4 — No listen tail in stay_on.**
- The idle loop is the tail: commands every 0.2 s, acks and console drained each tick.
- Acks are still flushed after END, as today.
- per_boot keeps the tail (W3 etc. unchanged).

**H5 — Heartbeat wire (new, supervisor stay_on only).**
- Proposal: `<WS v=1 a=idle …>`, using today's `send_wake_status` fields
  (`tz lt ws we rk q ct sha hn`).
- `up=` and `cfg=` stay in W8 (S4, the slim form).
- The heartbeat is sent `heartbeat_s` after the last uplink of any kind (an action or a heal
  pass resets the timer).
- Cost at the §5.2 default of 300 s: 12 cellular messages/h/unit (≈ 288/day). Keep 300 as the
  default, or pick another?
- Staging already ingests it (facts above).
- It is part of the stay_on vectors, which are new and reviewed whole (see "Goldens").

**H6 — SIGTERM.**
- The flag handler is installed **in stay_on only**. per_boot keeps today's default SIGTERM
  (immediate death, no finally, no halt), so the watcher, `rc_field_update.sh` and `dev_mode.sh`
  keep working unchanged.
- In stay_on the loop exits at the next safe point **without a halt**: acks flushed, daemon
  stopped, port closed, exit 0. The wrapper does not restart.
- Where "the next safe point" is:
  - Idle: ≤ 0.2 s.
  - During a capture retry: the 60 s wait between attempts polls the flag every 1 s, and the
    attempt running (≤ 30 s) ends first. That is the bounded wait. The legacy path sleeps the
    same 60 s, so its goldens are unchanged.
  - During a transmit burst: the burst finishes (≤ ~5 min on video).
- "Ignored once halting" applies only to the crash-loop fallback (H7), which is per_boot with a
  dry-run halt.
- The documented stop step (K6) for the tools: `pkill -TERM`, wait for the process to exit,
  and only then `-KILL`.

**H7 — Restart wrapper and crash loop.** The loop lives in `rc_run_capture_cycle.sh`, with
the same cron line and flock, so tools that match `rc_run_capture_cycle.sh|rc_progressive_jpeg.py`
are unchanged.
- **Exit codes:**
  - 0 = stop (SIGTERM or normal).
  - 70 = watchdog trip or uncaught error in stay_on. The wrapper restarts with backoff
    10, 20, 40, 80, 160 s, capped at 300 s.
  - 71 = RSS ceiling (a clean exit). The wrapper restarts after 5 s.
  - Also restarted: a signal death (>128, not 143) while the process's stay_on marker
    `/dev/shm/bmcam_stay_on` exists, i.e. an OOM kill.
  - per_boot exits 0/1/2 and never loops.
- **Crash loop:** 5 restarts within 10 min (uptime-based, held in the wrapper) → one final
  `rc_progressive_jpeg.py --transmit --crashloop`.
  - That run is per_boot, with the halt forced to dry-run and one `<WS a=crashloop>` before its
    action.
  - Then the wrapper exits. The unit stays up and reachable over ssh, with no RC process until
    the next boot.
  - Note: the §4 "per_boot with halt disabled keeps listening and services trg" loop is not
    built in S3a. Accept "idle until reboot" for S3b?

**H8 — Watchdog.** Checked on every idle tick. It trips when:
- the reader thread is not alive, or
- its consecutive read errors reach ≥ 20 (≈ 10 s at 0.5 s).

The consecutive-error counter is a new, additive daemon counter that resets on a good read.
There is no periodic re-subscribe unless the bench shows a bridge reset drops the subscription,
as the spec says.

**H9 — RSS.**
- Current RSS is read from `/proc/self/status` VmRSS (on the Mac, the `ru_maxrss` fallback).
- It is logged per action as `rss_now_kb`; `rss_kb` stays the peak.
- The ceiling is checked after each action → exit 71.
- The value comes from the bench's 20-action measurement. Until then the placeholder is
  180 MB (bmcam004 `free -m`: 415 MB total).

**H10 — Log rotation.**
- **stay_on:**
  - Per-action markers (`[SUP] ===== action N (trg|scheduled|heal) utc=… =====`).
  - The process rotates its own stdout at 5 MB (`os.dup2` onto `rc_cycle_<ts>.log.N`).
  - It keeps the newest 200 `rc_cycle_*.log*` files.
- **Supervisor, both modes:** `supervisor_actions.jsonl` rotates at 2000 lines → `.1`.
- per_boot log pruning stays as today (future hardening).

**H11 — Heals in stay_on.**
- `heal_events` are cleared only after they go out as `<HL>` (§4 "Heal events"). This is
  daemon/rc_heal code shared with legacy, and per_boot goldens stay identical.
- For `wakes_left` ageing, a "wake" is a transmitting action or an idle heal pass.
- **O5:**
  - Trigger: 10 min with no uplink AND non-empty `pending_heals` → one heal pass
    (`rc_heal.begin_wake` → `send_before_start(reserve 0)` → `<HL>`s).
  - It uses the media's own tx: stills use the configured network, video is cellular-only.
  - Budget: a fresh per-pass budget.
  - Lane plan: the phase lane plan, if the unit uses one.
  - Frequency: at most one pass per 10 min.

**H12 — W10 (O3): a trg heard in the per_boot listen tail fires this boot.**
- Supervisor only.
- The tail ends early when a trg is armed.
- **Fits** = `budget.remaining_s() ≥ TAIL_SAFETY_S + min_action_s`:
  - stills: 60 s;
  - video: clip duration + 60 s.
  - Otherwise the trg stays armed for the next boot, with one line.
- Same budget (G1). The decision repeats after each extra action, and the budget bounds it.
- Honest note: bmcam003 video spends ~460 s of its 480 s budget, so W10 will almost never fire
  on a video unit with today's settings. It matters for stills and short clips.

## 1. Commits (each small, each green)

| # | commit | gate / tests |
|---|---|---|
| S3b.1 | **Registry** (H1): `mode.interval_s`, `mode.heartbeat_s`, stay_on runnable + cross-keys, `REGISTRY_VERSION` 3, `config_v1_reader` defaults; the `test_config_v2` cases that used stay_on as the non-runnable example move to the cross-key rule | goldens + settings goldens byte-identical; registry/migrate/v2 tests; old-v2-file-loads test |
| S3b.2 | **Boot split**: process scope (owner, daemon, port session; once) vs action scope (budget, summary, end line; per action). per_boot calls both once, as today. `heal_events` cleared after they are sent (H11). | goldens identical across both runtimes; unit tests: second action reuses owner/daemon/uart, sent events cleared, unsent kept on failure |
| S3b.3 | **stay_on loop** `run_stay_on` (H2–H6): decision loop, 0.2 s idle tick, interval + window (first-skip WS), trg, per-action settings from the base (one-shot trg keys never leak) + overlay re-resolve, no tail, `power.halt` ignored (one loud line), heartbeat, SIGTERM flag (stay_on only), action markers, action log `run`, `rss_now_kb`, jsonl rotation. Harness: supervisor-only stay_on scenarios in `tests/golden/vectors_stay_on/` (still + video: boot action, trg at t, scheduled repeat, a skip run, heartbeats, SIGTERM via a new `at_clock` `sigterm` rule), per-action summaries | legacy + per_boot vectors identical; stay_on vectors new, reviewed in full; unit tests per rule |
| S3b.4 | **Hardening** (H7–H10): watchdog, exit codes, wrapper loop + backoff + crash cap + `--crashloop` (`<WS a=crashloop>`), stay_on marker, RSS ceiling, stdout rotation + pruning | per_boot goldens identical; wrapper tested with a stub python (exit-code table, backoff, cap, TERM, per_boot never loops); watchdog/RSS unit tests |
| S3b.5 | **Capture retry wait interruptible** (H6): 60 s in 1 s steps, polls the stay_on flag | goldens identical (capture-retry scenario); unit test: flag ends the wait ≤ 1 s + the running attempt |
| S3b.6 | **O5 idle heals** (H11) | stay_on vector with an `rsd` + 10 min idle; unit tests (no pass before 10 min, one pass per 10 min, ageing) |
| S3b.7 | **W10** (H12), its own W-commit | new scenario `still_trigger_in_tail` (legacy: trg stays armed; supervisor: second action) in `SUPERVISOR_DIFFERS`; README table row; unit tests (fits / doesn't fit / video) |
| S3b.8 | **50-action fake-time soak** `tests/test_s3b_soak.py`: real `CommandDaemon` + reader thread on the FakeUart of `test_command_daemon`, fake clock, real video actions (the `test_rc_video_tx_cycle` fakes) plus stills with fake capture; trg + scheduled + heal passes + heartbeats. Asserts: the SAME uart object throughout, one port OPEN, thread count and `/dev/fd` count constant after action 1, `heal_events` bounded, 50 action-log lines | runtime ≤ 60 s, else video-only and say so |

Docs in the same commits: the golden README ("stay_on" section and the W10 row), the
`bmcam-field-update` skill (the stay_on stop step, H6), and the runtime manifest if a module is
added.

Before the bench: an independent reviewer (subagent, fresh context) checks the whole diff
against DESIGN §4, K4, K6, R7 and this plan. Every finding is fixed or answered in writing.

## 2. Stage gate (bmcam003 under stay_on; bmcam004 = control, S3a per_boot supervisor)

A stay_on Pi on SPOT-33507C's scheduled bus is hard-cut at :10 (SD risk). The gate therefore
needs that bus **held on** (`bridgePowerControllerEnabled 0` + commit + read-back; the §7
recipe from the S1/S2 benches), and it is restored after with the Pi disarmed and halted
(`rearm.sh` pattern). Every console command is recorded in `runs/s3b_bench_<date>/`.

1. Catch bmcam003 awake, disarm (backup), deploy S3b by SHA, record the new hash.
2. Put `mode.run: stay_on`, `interval_s: 0` in its v2 file. Start the wrapper by hand with the
   cron line (so the wrapper itself is under test).
3. **4 triggered cycles, no reboot:** four `trg 2` over the console (`bm pub`, while the Pi is
   subscribed). Each must be complete at Sofar, with the same process, same PID and one port
   OPEN. Heartbeats appear between them.
4. **20-action RSS measurement:** 20 actions with `rss_now_kb` per action. H9 sets the ceiling
   from it. **Cellular cost needs a ruling:**
   - (a) stills, `interval_s: 120`, with a smaller `txd`/`win` budget so each action sends
     ≈ 20–30 messages (≈ 500 messages total), or
   - (b) the media as configured (video: ~185 messages × 20 ≈ 3700).

   Recommend (a): stills is where the PIL RSS risk lives.
5. SIGTERM stop, one forced watchdog trip (restart seen), then restore: `mode.run: per_boot`
   (or Nick's pick for the overnight), cron re-armed, bus schedule restored, state recorded.

PASS = 3 and 4 complete with no reboot and no second port open, the RSS ceiling chosen, and
legacy + per_boot goldens unchanged except W10.

## 3. Not in S3b

- save_local actions and the recorder under the supervisor (S3c).
- `hld`, keep-alive, `set`, `mode.run` guard class, W8 (`up=`/`cfg=`), W9 (S4).
- per_boot with the halt disabled as a listening loop, and per_boot log pruning (future
  hardening).
- Deleting the legacy runtime (after S5).

## 4. S3b step 0 (done 2026-09-27, `runs/s3b_bench_20260927/`)

- **bmcam004** moved to S3a + supervisor in the 01:00Z window:
  - Scripts: `watcher_host.sh` + `follow_bmcam004.sh`, ref `development` 1636c8b (same tree
    as S3a 77d97ec).
  - Field update PASS, parity OK. Hash 81e05dee → e3449650 (registry v2) → **5e679ef9** with
    `runtime: supervisor` (same as bmcam003).
  - Re-armed, dark at 01:01:51Z.
  - That hour's clip was lost, as planned: the watcher's SIGTERM caught the cycle mid-mux, and
    the ffmpeg orphan was ended by the halt.
- **RESULTS F3 answered** (bmcam003's 23:00Z cycle, log `rc_cycle_20260926T222008Z.log`):
  - Ping 26305 (queued while the bus was off) was **not** in the W4 boot drain (no
    `[SUP] boot drain` line).
  - It was applied **this boot**, at ≈ +50 s uptime, by the pre-burst command pump, between
    the video fit and the heal plan.
  - It was acked after END.
  - The first time read stepped the clock (W6).

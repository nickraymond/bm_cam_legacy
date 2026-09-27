# Sprint26 S3a — one runtime, per_boot parity: implementation plan

Written 2026-09-26. Status: **DONE 2026-09-26** — bench gate PASS on bmcam003 (runs/s3a_bench_20260926/RESULTS.md); PR #79. G1–G5 ruled as proposed; O3 → W10 in S3b; bmcam004 follows at the start of S3b (Nick).
Branch `feature/sprint26-s3a-runtime` from `origin/development` a1341f8 (S2 / PR #78 merged).
Spec: `DESIGN_supervisor.md` §4, §8.1–8.3, §10–11; `REVIEW_20260925.md` K1, K3–K6, R7;
`PLAN_S2.md` G1 (v8 state stays in the `v8` section) and G2 (v1-shaped render on /dev/shm).
O11/W9 ruled 2026-09-26: accepted, **not in S3a** (nvd parser first, W9 in S4) — commit 12b4e89.

Baseline on this Mac (.venv-dev, PyYAML 6.0.2, Pillow 12.3.0): suite **1058 passed**
(`tests/test_reference_card_color_utils.py` excluded). Every commit re-runs the goldens,
`tests/test_config_v2_parity.py` and the full suite. Every non-W commit leaves
`tests/golden/vectors/` byte-identical, including the port-open count.

Facts this plan rests on (from inspection, file:line at a1341f8):
- Cycle bodies: stills `run_cycle` (`rc_progressive_jpeg.py:477-890`), video
  `run_video_tx_cycle` (`rc_video_tx.py:206-428`); each starts the daemon, anchors its own
  `CycleBudget`, and in its `finally` does `HK.shutdown` → `bm_port.close` → halt.
- They differ in ways the extraction must keep: stills starts the daemon BEFORE its `try`
  (a UART failure there skips close and halt) and anchors the budget after the daemon; video
  anchors the budget first and starts the daemon inside the `try`. Close predicates differ
  (`transmit or daemon` vs `opened`). Video runs the gate even with `--skip-time-window`.
- Port opens today (golden traces): commands off = 2 per transmitting cycle (the gate's private
  `with serial.Serial` in `spotter_time_sync.py:548` + the lazy `bm_port.get()`); commands on = 1.
  `bm_port.close()` nulls the handle and the next `get()` silently reopens (the review BLOCKER).
- The harness swaps `rc.run_cycle` / `rc_video_tx.run_video_tx_cycle` by module attribute and
  injects video fakes through `run_video_tx_cycle.__kwdefaults__`; `main(argv, **cycle_overrides)`
  must keep forwarding `sleep_fn`/`clock`. A supervisor that bypassed those two functions would
  silently lose the fakes (unknown kwdefaults are ignored → real sleep/ffmpeg).

## 0. Gaps in the spec — **Ruled (Nick, 2026-09-26): all five as proposed.**

**G1 — where the one per_boot `CycleBudget` is anchored.** §4/K4 say "at process start".
Today stills anchors after daemon start, video before it; the fake clock does not advance
there, so the goldens cannot see the difference, but on the unit a literal process-start
anchor (before imports + config load) would shrink the ladder / video budget by the boot's
import+load time. **Proposal:** the supervisor creates ONE budget immediately before
`daemon.start()` (= video's anchor today; moves stills' anchor earlier by the daemon start,
tens of ms) and passes it to the action; never re-anchored within a boot. Bench A/B records
the stills ladder choice under both runtimes.

**G2 — W2–W6 change the supervisor only.** The legacy runtime is the rollback path until S5,
so it stays byte-identical to S2. **Proposal:** each W-commit is gated on the runtime
(supervisor passes `defer=True`, runs the tail after a skip, etc.); legacy vectors never change.
Supervisor-only expected output lives in `tests/golden/vectors_supervisor/<scenario>/`, only
for scenarios that differ, and a test asserts that set equals the scenarios the landed
W-commits name (README table), so an unintended difference cannot hide there.

**G3 — how the harness and the bench select the runtime.** `commands.runtime` is v2-only; the
plain golden runs are v1 YAML. **Proposal:** a CLI override `--runtime legacy|supervisor`
on `rc_progressive_jpeg.py` (precedence CLI > `commands.runtime` > `legacy`; v1-only units =
legacy; one `[RUNTIME]` boot line naming the choice and its source). The harness passes it;
the bench runs bench copies with it without editing the unit's config. No environment knob.

**G4 — what the supervisor runs in S3a.** Only `output: transmit` for both media
(still × transmit, video × transmit with `video_tx.enabled`). The recorder path
(`video_recorder.run_video_mode`, a third port owner), `--capture-only`, heic and
`--print-config` fall through to today's code unchanged, with one log line. They move in S3c.

**G5 — halt after a failed daemon start.** Legacy stills skips close AND halt when the UART
fails at daemon start (daemon started outside the `try`). No golden covers it. **Proposal:**
legacy keeps it (parity); the supervisor always runs shutdown → close → halt from the owner,
pinned by a unit test. Stated in the PR as the one intended non-wire difference.

## 1. Commits (each small, each green)

| # | commit | gate / tests |
|---|---|---|
| S3a.1 | **Extract the stills action.** `run_cycle` keeps its signature and summary; its body splits into prologue (daemon start, budget) → `still_transmit_action(ctx)` (gate … listen tail, same call order) → epilogue (the `finally`). Legacy = prologue + action + epilogue exactly as today. | goldens byte-identical (15 wire + 15 settings), v2 parity, suite |
| S3a.2 | **Extract the video action** the same way; the `__kwdefaults__` fakes stay parameters of `run_video_tx_cycle` and are forwarded in `ctx`. | same |
| S3a.3 | **PortOwner** (`bm_port.py` grows it; stays the ONE accessor): owns the long-lived handle; a lazy open after close, or a second concurrent descriptor, is refused loudly (log + raise), never a silent reopen. The gate's private short read (commands off) goes through the owner as a scoped open/close, so the commands-off count stays 2. `shutdown → close → halt` becomes `owner.finish()`, called from both epilogues. New modules added to the harness `APP_MODULES`. | goldens byte-identical incl. OPEN/CLOSE lines; unit tests: reopen-after-close refused, second descriptor refused, commands-off scoped read, finish order |
| S3a.4 | **Registry key** `commands.runtime` (enum `legacy\|supervisor`, default `legacy`, apply next boot, v2-only like `mode.run`): `REGISTRY_VERSION` 1 → 2, `config_v1_reader` sets `legacy`, registry/migrate coverage tests know it; `--runtime` CLI + `[RUNTIME]` line (G3). The runtime does nothing new yet. Note: the config hash of every v2 file changes (hash covers every key); an older `camera_config.yaml` without the key still loads. | goldens + settings goldens byte-identical; registry/migrate/v2 tests; old-v2-file-loads test |
| S3a.5 | **Supervisor, per_boot** (`rc_supervisor.py`): config (unchanged G2 render path) → owner/daemon start → budget (G1) → decide (pending stills `trg`, as `main` does today) → action via `run_cycle` / `run_video_tx_cycle` with the owner injected (so harness hooks and fakes keep working) → `owner.finish()`. One JSON line per action in `cron_logs/supervisor_actions.jsonl`. Goldens parameterised: every wire scenario runs under both runtimes against the SAME vector (the two `field_*_main` scenarios stay legacy-only); the action log is the only allowed extra file, named in the test. | goldens identical across runtimes (13 scenarios × 2); G5 unit test; supervisor with commands off reproduces 2 opens |
| S3a.6 | **W2** acks always deferred out of a stills burst (supervisor) | reviewed diff: `still_bench` (+ any stills scenario with a mid-burst command) under `vectors_supervisor/` |
| S3a.7 | **W3** listen tail after a window skip; a video skip sends a `<WS>` (supervisor) | `still_window_skip`, `video_window_skip` |
| S3a.8 | **W4** boot drain applies queued commands this boot (non-blocking; never waits for more) | `still_bench` (+ video equivalents if a boot command is scripted) |
| S3a.9 | **W5** video services `trg` at the decision point | `video_trigger_pending` |
| S3a.10 | **W6** fresh time read per action: clear the daemon's raw buffer under `_raw_lock` before the subscribe write, accept only post-marker frames, monotonic sanity check, step the clock only on drift | `still_trigger` (clock now set on a trigger boot); unit tests: stale buffered stamp never returned, second read returns the new publish |

Each W-commit message names its W-item; the diff of `tests/golden/` is in that commit and
summarised in the message. Legacy vectors never change (G2).

Before the bench: an independent reviewer (subagent, fresh context) reviews the whole diff
against DESIGN §4, K3–K6, R7 and this plan; every finding fixed or answered in writing.

**Implementation note (W6, 2026-09-26):** the golden world's Pi clock always
agrees with the Spotter (datetime is frozen), so a literal "step only on drift"
removed `SETCLOCK` from every supervisor scenario and would have moved all 13
into `vectors_supervisor/`, emptying the "supervisor = legacy" net for every
later commit. Smallest correction, flagged for Nick: the process's FIRST gate
read always steps (a Pi without an RTC boots with a wrong clock); every later
read is drift-gated (`CLOCK_STEP_MIN_DRIFT_S` = 2 s). per_boot reads once per
boot, so the rule first bites in stay_on (S3b); tests/test_s3a_time_read.py
pins it. With that, W6 changes `still_trigger` only, as planned.

## 2. Stage gate (bmcam003 under the supervisor, bmcam004 control)

Recipes from `runs/s2_bench_20260926/` (console.sh, live_side.sh, make_bench_dirs.py);
record everything in `runs/s3a_bench_<date>/`. Any Spotter config change (e.g. holding the
bus on) only with Nick's explicit OK in chat at the time.

1. Catch bmcam003 awake, disarm cron (backup), record processes, crontab, config hash, SHA.
2. Deploy the S3a SHA with `deploy_rc_runtime.sh` (config v2 untouched; record the new hash
   from the registry bump). bmcam004 stays on S2 f73129a.
3. Bench copies (halt dry_run): one still and one video cycle each under `--runtime legacy`
   and `--runtime supervisor` over the console → UART byte comparison per pair (START/chunks/END
   counts, complete on the console), port opens from the log, boot-to-transmit, peak RSS.
4. One armed production wake with `commands.runtime: supervisor` on bmcam003 (real halt),
   bmcam004 same hour as control → both complete at Sofar, halt on time.
5. Restore: bmcam003 back to `commands.runtime: legacy` unless Nick rules to keep the
   supervisor on for the overnight hourly run; cron re-armed; state recorded in RESULTS.md.

PASS = goldens identical across runtimes except the W2–W6 vectors; steps 3–4 complete on
both media with the same message counts as legacy (plus the intended W differences).

## 3. Not in S3a

- stay_on, heartbeat, watchdog, restart wrapper, crash-loop fallback, SIGTERM flag, RSS
  ceiling, log rotation (S3b).
- O3 (a `trg` heard in the listen tail fires this boot if it fits the budget): a second
  per_boot action is a new wire behaviour with no W-number yet — **propose W10 in S3b**.
- save_local actions and the recorder path under the supervisor (S3c).
- W9 self-describing chunks (S4, after the nvd parser), W8 and the v9 verbs (S4).
- Deleting the legacy runtime (after S5).

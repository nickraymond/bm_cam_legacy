# Golden vectors — the Sprint26 refactor safety net

Purpose: pin what the camera does today, byte for byte, so every Sprint26 commit
proves it changed nothing it did not mean to (DESIGN_supervisor.md §8.1–§8.2).

What is pinned:
- **`vectors/<scenario>/trace.txt`**: every serial port open and close, every
  frame written to the Spotter (decoded: `tx.<network> <payload>`, `printf`,
  `sub`), every frame the fake Spotter delivered (`RX`), the camera command line
  (`CAM`), and each clock set / halt / network call.
- **`vectors/<scenario>/summary.json`**: the cycle's own summary, the command
  state file afterwards, capture sidecars, sent records, and every file left
  behind (size + hash).
- **`settings/<target>/settings.json`**: every config loader's resolved output
  for each repo profile, plus bmcam003 under each v1 command-state fixture (the
  S2 migration must reproduce these).
- **`--print-config --json` agreement** (Sprint26 S2b): each settings run also
  writes `json_check.json` (not recorded) and the test fails if the runtime's
  JSON probe disagrees with any loader above. Deploy and migration parity
  (S2f) use that probe on the unit.

The real runtime runs unmodified; `world.py` fakes everything outside the process
(serial, subprocess, clock, host identity). Each scenario runs in its own
interpreter against a temp copy of the runtime, so nothing is written into the
repo. Scenario list and what each one pins: `scenarios.py`.

## Setup (once)

```bash
python3 -m venv --system-site-packages .venv-dev
.venv-dev/bin/pip install -r requirements-dev.txt
```

PyYAML is mandatory: without it bm_serial silently falls back to 300 chars /
5.0 s pacing, so vectors recorded without it would pin the wrong behaviour. The
runner exits 3 if it is missing. The Pillow version is recorded in
`vectors/_env.json` because JPEG bytes depend on it; a mismatch fails loudly.

## Check (every commit)

```bash
.venv-dev/bin/python -m pytest -q tests/test_golden_vectors.py
```

About 8 s. A failure prints the diff and keeps the actual output directory.

## Re-record (ONLY in a commit that is meant to change behaviour)

```bash
GOLDEN_RECORD=1 .venv-dev/bin/python -m pytest -q tests/test_golden_vectors.py
git diff --stat tests/golden/
```

The reviewed diff of `tests/golden/` goes in that same commit, and the commit
message names the W-item (DESIGN §8.2) it implements.

## One scenario by hand

```bash
.venv-dev/bin/python tests/golden/run_scenario.py wire still_bench /tmp/out
.venv-dev/bin/python tests/golden/run_scenario.py wire still_bench /tmp/out --app-src /path/to/BM_Devel_Pi
.venv-dev/bin/python tests/golden/run_scenario.py settings bmcam003+roi5 /tmp/out
```

`--app-src` runs another copy of the runtime (e.g. a mutated one to prove the
harness notices). Two field scenarios run `main`'s committed runtime
(`field_bmcam001_main`, `field_bmcam002_main`): that is the wire the backend
must keep ingesting (DESIGN §11).

## Both runtimes (Sprint26 S3a)

Every wire scenario runs twice: under the legacy runtime and under
`--runtime supervisor` (`test_wire_supervisor_<name>`), compared with the SAME
vector. The two `field_*_main` scenarios pin `main`'s runtime and run once.
The supervisor's action log (`app/cron_logs/supervisor_actions.jsonl`) must
exist (proof the supervisor ran) and is left out of the compared file list.

A deliberate supervisor-only wire change (W2–W6, PLAN_S3a.md G2) records its
expected output under `vectors_supervisor/<scenario>/`, only for scenarios
listed in `SUPERVISOR_DIFFERS` (tests/test_golden_vectors.py) with their
W-item; a test fails if that directory holds anything else. Legacy vectors
never change in S3a.

```bash
.venv-dev/bin/python tests/golden/run_scenario.py wire still_bench /tmp/out --runtime supervisor
```

## stay_on (Sprint26 S3b)

`scenarios.STAY_ON_SCENARIOS` are supervisor-only: a long-lived loop has no
legacy counterpart, so each is recorded once under `vectors_stay_on/<name>/`
(`test_wire_stay_on_<name>`) and the whole record is new wire, reviewed in full.
Each migrates its v1 profile to config v2 and then sets the scenario's `v2` keys
(`mode.run: stay_on`, `commands.runtime: supervisor`, `mode.interval_s`,
`mode.heartbeat_s`). Commands arrive on `at_clock` rules; a rule with
`"signal": "TERM"` sends the runtime a real SIGTERM (trace `NOTE SIGTERM ...`),
which is how every stay_on scenario ends. `summary.json` adds `cycles` (every
action's summary, in order). `camera_config.lkg.json` and
`state/config_journal.jsonl` embed the real boot wall time, so their listed
sha256 is `wall-time` (the size is still pinned).

What they pin (PLAN_S3b.md H2–H6): one port OPEN and one CLOSE for the whole
process, no HALT, a boot Spotter time read (the only clock step), a scheduled
action at boot (interval_s > 0) or none (trigger-only), `<WS a=idle>` heartbeats
heartbeat_s after the last uplink, commands applied and acked while idle, a trg
action, only the first window skip of a run sending `<WS a=skip_win>`, and no
listen tail after an action. `stay_on_idle_heal` (S3b.6, O5) is an `rsd` heard
while idle, sent 10 min after the last send as chunks + `<HL>` with no capture.
`stay_on_crashloop_fallback` (S3b.4, H7) is the
wrapper's `--crashloop` run: per_boot, one `<WS a=crashloop>` as soon as the port
is up (before the time read, so its `lt` is the Pi clock), halt dry-run (no HALT).

```bash
.venv-dev/bin/python tests/golden/run_scenario.py wire stay_on_still /tmp/out
```

## Behaviours the vectors pin that later stages change on purpose

Recorded here so the matching golden diffs are expected, not surprising:

| scenario | today | changed by |
|---|---|---|
| `still_bench`, `still_heal` | acks go out mid-burst (`defer_acks_during_transmit: false`) | W2 (S3a, supervisor only: `vectors_supervisor/`) |
| `still_window_skip`, `video_window_skip` | no listen tail after a window skip | W3 (S3a, supervisor only) |
| `video_window_skip` | a video unit outside its window sends nothing, not even a `<WS>` | W3 (S3a, supervisor only) |
| `still_bench` | a command waiting at boot applies only on the next boot | W4 (S3a, supervisor only) |
| `video_trigger_pending` | a video unit never services `trg`; it stays armed | W5 (S3a, supervisor only) |
| `still_trigger` | a trigger boot skips the gate, so the system clock is NOT set from the Spotter | W6 (S3a, supervisor only) |
| `still_trigger_in_tail` | a trg heard in the listen tail stays armed for the next boot | W10 (S3b, supervisor only): the tail ends and it fires this boot on the same budget |
| all stills | ~~START carries `bf`/`zh` (HEIC-era storage fields)~~ done in S1.9: dropped; `lg` now fits in 3 scenarios | W1 (S1) |
| `bmcam003+foc0_over_manual` | `foc 0` replaces the whole focus block (lens position dropped) | the S2 migration must keep this |

## Known limitations

- Video recording and the x264 fit are faked at function level; the recorder's
  `rpicam-vid`/`ffmpeg` argv is not traced yet.
- One physical UART is modelled as "inbound goes to every open port with a read
  timeout"; a second descriptor shows up as a second `OPEN` in the trace.
- JPEG bytes are this Mac's Pillow build. The on-device check is a separate
  bench step: the same scenario before and after a change on bmcam003.

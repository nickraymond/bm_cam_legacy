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

## Behaviours the vectors pin that later stages change on purpose

Recorded here so the matching golden diffs are expected, not surprising:

| scenario | today | changed by |
|---|---|---|
| `still_bench` | acks go out mid-burst (`defer_acks_during_transmit: false`) | W2 (S3) |
| `still_window_skip`, `video_window_skip` | no listen tail after a window skip | W3 (S3) |
| `video_window_skip` | a video unit outside its window sends nothing, not even a `<WS>` | W3 (S3) |
| `still_bench` | a command waiting at boot applies only on the next boot | W4 (S3) |
| `video_trigger_pending` | a video unit never services `trg`; it stays armed | W5 (S3) |
| `still_trigger` | a trigger boot skips the gate, so the system clock is NOT set from the Spotter | W6 (S3) |
| all stills | ~~START carries `bf`/`zh` (HEIC-era storage fields)~~ done in S1.9: dropped; `lg` now fits in 3 scenarios | W1 (S1) |
| `bmcam003+foc0_over_manual` | `foc 0` replaces the whole focus block (lens position dropped) | the S2 migration must keep this |

## Known limitations

- Video recording and the x264 fit are faked at function level; the recorder's
  `rpicam-vid`/`ffmpeg` argv is not traced yet.
- One physical UART is modelled as "inbound goes to every open port with a read
  timeout"; a second descriptor shows up as a second `OPEN` in the trace.
- JPEG bytes are this Mac's Pillow build. The on-device check is a separate
  bench step: the same scenario before and after a change on bmcam003.

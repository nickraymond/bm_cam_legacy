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

## save_local (Sprint26 S3c)

`mode.output: save_local` needs the supervisor (a config v2 cross-key), so these
scenarios have no legacy counterpart either. `scenarios.SAVE_LOCAL_SCENARIOS` are
per_boot and recorded under `vectors_save_local/<name>/`; stay_on + save_local ones
sit in `STAY_ON_SCENARIOS` (`vectors_stay_on/`). `scenarios.SUPERVISOR_ONLY` is the
union and `scenarios.VECTOR_DIRS` maps each name to its dir; a catalogue test per
dir keeps them exact. A scenario with `"disk": "full"` puts the SD over every limit
(the stills guard and the video ring fake); every other run pins the guard's disk
UNDER the limits (`world.FIXED_DISK_USAGE`), so the guard is a no-op there.

What they pin (PLAN_S3c.md §5): no START/chunks/END; per_boot keeps its one status
line (`<WS a=cap>` for stills); stay_on sends no per-action `<WS>` and its
heartbeats keep their 300 s rhythm THROUGH the save actions (C1); a command applied
at boot or while idle does not turn a save into a send (C2); trg 2 saves (C8); the
window-off unit reads Spotter time itself (C9); a pending heal still goes out (C14);
a full SD refuses the capture with `<WS a=skip_err r=storage_full>`.

```bash
.venv-dev/bin/python tests/golden/run_scenario.py wire save_local_still /tmp/out
```

## commands v9 (Sprint26 S4)

v9 speaks only on the supervisor path of a migrated (config v2) unit (PLAN_S4.md
G1), so `scenarios.V9_SCENARIOS` are supervisor-only per_boot scenarios under
`vectors_v9/<name>/` (`test_wire_v9_<name>`). Their commands use the §6.2 id
ranges (remote 1e6.., console 1..99 999, heal 1e5..), so the reply lanes are
pinned. They were recorded on the v8 verbs first (S4 b.2a) and change only in
the W8a commits, so each diff shows exactly what v9 changes. The per_boot v1
scenarios above stay v8 under both runtimes (legacy parity net).

```bash
.venv-dev/bin/python tests/golden/run_scenario.py wire v9_still /tmp/out
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
| `save_local_video` | (new in S3c: no earlier vector) a per_boot video save_local action | W11 (S3c, supervisor only): one `<WS a=saved>` per action |
| `still_trigger_in_tail` | a trg heard in the listen tail stays armed for the next boot | W10 (S3b, supervisor only): the tail ends and it fires this boot on the same budget |
| all stills | ~~START carries `bf`/`zh` (HEIC-era storage fields)~~ done in S1.9: dropped; `lg` now fits in 3 scenarios | W1 (S1) |
| `bmcam003+foc0_over_manual` | `foc 0` replaces the whole focus block (lens position dropped) | the S2 migration must keep this |
| every stay_on / save_local scenario | migrated `camera_config.yaml` + LKG bytes (registry v4 names) | V1 (S4 a.1, vector bytes only, no wire): registry v5 adds 4 keys and renames `video.storage.*` → `storage.*` |
| `stay_on_video`, `stay_on_save_local_still`, `save_local_still` | the tmpfs render holds the YAML base; a v8 `txd` reaches the runtime through the v8 bindings | V2 (S4 b.1, render bytes only, no wire): the supervisor on a migrated unit renders the EFFECTIVE config (base ⊕ overlay), so `image_transmit_delay_seconds` reads 1.5 after `txd 1`; trace.txt identical |
| every v2 scenario with a command (`vectors_v9`, stay_on, save_local) | v8 ack `{"id","ok","st":{11 indices}}`, every ack cellular, no console answer | W8a part 1 (S4 b.2b, supervisor on a migrated unit only): slim ack `{"id","ok","h"[,"d"]}` cellular only for remote/service ids (G13), one `[host] OK id=.. cfg=..` console line per answer; ~1 s less ack pacing |
| every v2 scenario | the v8 verbs dispatch through CommandState (roi, awb, txd, ...; 32-id dedupe, duplicate re-acked cellular) | W8a part 2 (S4 b.2c): v9 dispatch on V9State (the v8 section folded into the overlay once); inputs switched to `set` (r, b, uplink.msg_interval_s); a duplicate answers on the console only (G9); v9_still adds an `xk` and a retired-verb `cmd` rejection; state-file bytes change (V9State layout) |
| `v9_still`, `v9_video` | no `get`; no config summary on the uplink | W8a part 3 (S4 b.3): a remote `get` answers on the console (value + source) and as `<CF v=1 h=..>` through the ack pacer; a cellular `set`/`reset` is followed by a `<CF>` of the keys it changed |
| every v2 scenario | the state file's `boot_counter` stays 0 | V3 (S4 b.5, state bytes only, no wire): every counted (`--transmit`) run increments it before any port opens; cached answers record `b: 1` |
| `save_local_still`, `save_local_video`, `v9_still`, `v9_trigger_kv` | a per_boot unit halts right after its listen tail | W12 (S4 b.6c, keep-alive, DESIGN §4): after the tail a per_boot unit stays up until its last command + `commands.keepalive_s` (300 s), at most `keepalive_max_s` past the normal end, clamped to the budget; wire identical, fake elapsed longer |
| `v9_still`, `v9_video`, `v9_heal` | mid-burst commands are parsed, persisted and answered in the pacing slot | S4 b.7 (durable inbox, DESIGN §6.2): the pump only stashes raw payloads; they are handled at the next decision point (an rsd before `<HL>`, so the `<HL>` is unchanged); a byte-identical re-send is held once (v9_still's duplicate ping); keep-alive counts from the later answer |
| every v9 scenario with a remote command | the journal holds settings changes only | S4b review #5 (journal bytes only, no wire): each ok remote/service answer that moves a high-water leaves an `hw` journal line, so a lost state file re-seeds it |
| every v2 scenario | `<WS v=1 a=.. tz=..>`; START without a config hash | W8b (S4 c.1, supervisor on a migrated unit): `<WS>` gains `cfg=<hash8> up=<s>` right after `a`; START gains core `cfg=` and, on a triggered action, `tg=<id>` + the resolved `r=` (still crop) / `m=` (cap) / `d=` (clip s); worst case <= 285 B (tested) |
| every v2 scenario that sends a START (`vectors_v9`, `vectors_stay_on`) | keyed chunks `<I{key}.{i}>`; the sent record has no `chunk_total` | W9 (S4w, supervisor on a migrated unit): chunks `<I{key}.{i}/{M}>`, M = START `length` (keyframe repeats keep their `i/M`); the sidecar gains `"chunk_total": true` (+22 B); heals of pre-seeded records (`v9_heal`, `stay_on_idle_heal`, save_local) unchanged; worst-case line 402 B |
| `v9_still`, `v9_video`, `v9_lane`, `save_local_still`, `stay_on_video`, `stay_on_video_set_d`, `stay_on_save_local_still` | a `set`/`reset` journals the overlay's own old/new (`"old":null` for a key only the YAML had) | S5 F4 (journal bytes only, no wire): the journal's old/new are the EFFECTIVE values the console answer shows, e.g. `still.crop` `null` -> `[1504,846,1600,900]`; `config_journal.jsonl` bytes change in these 7 vectors |
| every v2 scenario, every settings target | `video.encoder` holds `denoise` / `sharpness`; the v2 render writes them; registry v6 | Sprint27 F-G3-4 (Nick 2026-10-02, "one owner per camera option"): `video.record.encoder.denoise` / `.sharpness` RETIRED (registry v7; `camera.image_processing.*` own `--denoise` / `--sharpness`). settings.json loses the two encoder fields; render / state / journal bytes shrink; every config hash changes, so `cfg=` / `h=` change in the 23 traces — and NOTHING else on the wire (checked byte-for-byte with the hashes masked) |

| every v2 scenario | registry v7; no `still.format` / `still.raw.*` | Sprint28 S1 (registry v8): 7 still keys (`still.format` pjpg/nrjxl, `still.raw.distances` / `encode_max_s` / `keep_crop` / `effort`, and the byte-target search's `still.raw.target_fill` / `d_max`, Nick 2026-10-03). Every config hash changes, so `cfg=` / `h=` change in the 23 traces — and NOTHING else on the wire (`tools/golden_hash_masked_diff.py`: 24 identical, 23 hash-only); migrated `camera_config.yaml` / LKG grow by the 7 keys; the tmpfs render is byte-identical (its `still_raw:` island is written only when a key differs from its default). New: `vectors_v9/v9_nrjxl` (nrjxl sent; the search fills 189 of 195 chunks in 3 encodes) and `v9_nrjxl_rfb_{cap,dng,enc,mem,time,fit,err,floor}` (each a pjpg START with `rfb=`; `fit` runs with `target_fill 0` = the fixed rungs): the world writes `tests/fixtures/s28/mini.dng` on `--raw` and cjxl is a deterministic size/time model (`run_scenario.fake_cjxl_runner`), so they pin the wire, not JPEG XL bytes |
| every v2 scenario | registry v8; no `camera.exposure.profile` / `max_shutter_us` / `max_gain` | Sprint28 low gain (registry v9, Nick 2026-10-05 via the EM): 3 exposure keys. Every config hash changes, so `cfg=` / `h=` change in the 32 v2 traces and NOTHING else on the wire (`tools/golden_hash_masked_diff.py --ref` the parent commit: 24 identical, 32 hash-only, 0 otherwise); the tmpfs render is byte-identical (its `exposure_profile:` island is written only when the profile is not auto). New: `vectors_v9/v9_low_gain` (pjpg still: one `rpicam-hello --list-cameras` — the world answers bmcam003's `imx708_wide` — then `rpicam-still ... --tuning-file <app>/exposure_profile/tuning/imx708_wide_lowgain_s30000_g16.json`, a patched copy of the REAL bmcam003 tuning file `tests/fixtures/s28/tuning/imx708_wide.json`; sidecar `exposure_*` fields) and `v9_low_gain_nrjxl` (the same on the `--raw` capture, s33333_g4). They pin the argv and files, not what the AGC does with the file (that is the bench's R5) |
| every v2 scenario | registry v9; no `still.raw.layout` | Sprint28 B3a (registry v10, Nick 2026-10-05 via the EM): `still.raw.layout` bayer4 (default) / rgb. Config hashes change: masked diff 24 identical, 34 hash-only, 0 otherwise; the tmpfs render is byte-identical (the island gets `layout:` only when rgb). New: `vectors_v9/v9_nrjxl_rgb` (one `rgb.ppm` VarDCT encode per search step in the fake cjxl model, a profile-v2 blob, START unchanged) and `v9_low_gain_rgb` (low_gain + rgb on one unit: `--tuning-file` on the `--raw` capture, then the rgb encode; the keys do not interact) |
| every v2 scenario; `v9_nrjxl_rgb`, `v9_low_gain_rgb` | registry v10; rgb search with the constant-slope prior | B3a search calibration after bmcam004 B0 (registry v11 `still.raw.rgb_encode_max_s`): all other traces hash-only (masked diff 24 identical, 34 hash-only); the two rgb traces change their search steps (median-curve first guess, fill-dependent slope, accept ≥ 0.90) |
| every v2 scenario | registry v11; heal chunks always BEFORE START | `uplink.media_key.heal_order` before (default) / after (registry v12, Nick 2026-10-08, EPIC_transmission_reliability: 40 heal chunks ahead of START stalled the Spotter hand-off queue on START, REEF-RC 7 + 8 AM wakes). Every config hash changes, so `cfg=` / `h=` change in the 36 v2 traces and NOTHING else on the wire (`tools/golden_hash_masked_diff.py --ref 786b100`: 24 identical, 36 hash-only, 0 otherwise); migrated `camera_config.yaml` / LKG / state grow by the key; the tmpfs render is byte-identical (`media_key.heal_order` is written only when after). New: `vectors_v9/v9_heal_after` (v9_heal with the image first: START, chunks, END, console lines, the heal chunk `<I{KEY}.2>` with its pacing sleep BEFORE it, `<HL a=sent>`) and `v9_video_heal_after` (the clip first, the mid-burst ping's cellular ack in the post-END flush, then the heal chunk, then `<HL>`) |

V-items (PLAN_S4.md) change pinned file bytes in `summary.json` but no wire
byte; like W-items, each is its own commit with the reviewed diff.

## Known limitations

- Video recording and the x264 fit are faked at function level; the recorder's
  `rpicam-vid`/`ffmpeg` argv is not traced yet.
- One physical UART is modelled as "inbound goes to every open port with a read
  timeout"; a second descriptor shows up as a second `OPEN` in the trace.
- JPEG bytes are this Mac's Pillow build. The on-device check is a separate
  bench step: the same scenario before and after a change on bmcam003.

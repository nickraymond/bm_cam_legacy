# Sprint26 S2 — settings storage (config v2): implementation plan

Written 2026-09-25. Status: **awaiting Nick's approval — no code before it.**
Branch `feature/sprint26-s2-settings` from `origin/development` 684d9c7 (S1 / PR #75 merged).
Spec: `DESIGN_supervisor.md` §5, §6.2–6.3, §8.1–8.3, §10–11; `REVIEW_20260925.md` K7–K9, X3–X5, R1–R6.

Baseline on this Mac (.venv-dev, PyYAML 6.0.2, Pillow 12.3.0): goldens 27/27; suite 915 passed
(`tests/test_reference_card_color_utils.py` excluded, numpy-only, as in the review).
Every commit below re-runs both and must leave `tests/golden/vectors/` byte-identical.

## 0. Two gaps in the spec (need a ruling)

**G1 — v8 commands keep running until S4, but state v2 stores values, not table indices.**
Until S4 the daemon still speaks v8 (`roi 5`, `hlt 3`, `rsd`), every ack carries `st` = table
indices (wire goldens pin it), and `CommandState` persists indices + `touched`. If S2 converts
the state to `overlay {path: value}`, a v8 command arriving on a migrated unit has nowhere to
go, and the ack `st` would need a value→index reverse map (ambiguous for `foc 0` = "YAML value").
**Proposal:** `bm_command_state_v2.json` carries the v2 fields (`overlay: {}`, boot counter,
empty high-water marks, empty `result_cache`) **plus a `v8` section** holding today's body
(`settings`, `touched`, `applied_ids`, `pending_trigger`, `pending_heals`). `CommandState`
reads/writes that section unchanged (so acks and heals stay byte-identical). The §5 state
mapping (`roi`→`still.crop`, `foc/awb/exp` group replace, `hlt`→`power.halt.*`, …) is written
and tested in S2c as `overlay_from_v8()` and applied **at load** to build the effective config
and hash. S4 folds the `v8` section into `overlay` once, when the v8 verbs are deleted.
The v1 `bm_command_state.json` stays untouched (rollback reads it; commands received between
migration and a rollback are lost — acceptable on bench rigs, written into the rollback note).

**G2 — how `to_v1_settings()` reaches 11 loaders without touching them.**
**Proposal:** when `camera_config.yaml` exists, boot loads v2 (strict=False, fallback chain),
renders the effective config as a **v1-shaped YAML on tmpfs** (`/dev/shm/bmcam/camera_schedule.v1.yaml`,
regenerated every boot, never on the SD), and points `--config-path` and
`BM_CAMERA_CONFIG_PATH` at it before the loaders run. Every v1 loader — line parsers included —
then reads exactly what it reads today. The source YAML is never rewritten. The gate test is
literal: profile → migrate → v2 → render → every v1 loader → per-key equal to the v1 golden.
Inventory caveats the render must cover (key inventory, 2026-09-25): `BristlemouthSerial` and
`bm_port` read `BM_CAMERA_CONFIG_PATH` (set at import, `bm_serial.py:14-17`), not
`--config-path`; `wap` reads a hard-coded `CONFIG_PATH` (`rc_command_hooks.py:78,100`). Both are
pointed at the render. Today a config error exits 2 before `run_cycle`, so the halt never runs
(`rc_progressive_jpeg.py:935`); the v2 fallback chain must not inherit that.

**Ruled (Nick, 2026-09-25): plan approved, G1 and G2 as proposed; bmcam004 read-only pull OK.**

## 1. First step after approval: pull the live config (read-only)

- bmcam003 (armed, hourly 10-min bus): `scp` of `camera_schedule.yaml`,
  `bm_command_state.json`, `software_sha.txt`, `crontab -l` during a scheduled window.
  No disarm, no Spotter change. Record in `runs/s2_live_pull_20260925/`.
- Stored at `device_profiles/<unit>/live_20260925/` (+ `PULL.md` with sha256, time, sha).
  **Not** over `device_profiles/bmcam003/camera_schedule.yaml`: every wire golden starts from
  that file, so replacing it would change the goldens. The repo profiles are swapped for the
  live ones only after the S2 gate.
- bmcam000: pull if it is reachable. **bmcam004: needs your OK even for a read-only scp**
  (hands-off rule). Confirms no bench unit runs `media_gid`.

## 2. Commits (each small, each green, goldens unchanged)

| # | commit | gate / tests |
|---|---|---|
| S2a | `BM_Devel_Pi/config_registry.py`: every key with path, type, default (= v1 absent-key behaviour), range/enum, guard class (`guarded_revert`/`guarded_stage`/`locked`/`service`), apply timing, short name, help, presets, `nullable`, `group_replace`, `v1_sources`, `validate_when`, `wire_visible`, `since`. Plus an explicit `REMOVED_V1_KEYS` list with the reason. W7 is NOT done: `camera.*.enabled` and `exposure.mode` stay as keys. | coverage test: every key in every profile (repo + live pulls + examples) is mapped or explicitly removed, and every registry key has a v1 source or is new-with-default; a profile with each block deleted migrates to v1-identical behaviour; no key segment ends in `len/length/chunks/buffer(s)/filename`; registry is PIL-free and import-light (`test_boot_imports`). |
| S2b | `rc_progressive_jpeg.py --print-config --json`: one JSON with every loader's output (the same set the settings goldens collect) + the overlaid view. Text `--print-config` unchanged. Settings goldens added for the live pulls (new targets only). | harness asserts `--json` == its own per-loader collection for every target; existing settings goldens byte-identical. |
| S2c | `tools/config_migrate_v1_v2.py` (+ pure module in `BM_Devel_Pi/config_migrate.py`): each key read by its OWN v1 loader; dry-run default; writes `camera_config.yaml` + `bm_command_state_v2.json` + a diff report; refuses unknown/ambiguous values; STOPS for a human on missing/`heic` capture_mode, `network_type` that resolved to 0x01 by absence or missing PyYAML, and any `media_gid` block enabling it. State: tables v8 only (commands-off units archive theirs); `applied_ids`, `pending_heals` copied; `pending_trigger` gets empty `kv`; high-water empty. | migrate every golden profile + fixture (roi5, foc0-over-manual, hlt3, win/txd/cap, awb/exp, twn/tmz) and the live pulls; refusal tests for each stop condition; migrated file spells out every registry key. |
| S2d | v2 loader `load_config(path, state, strict)` → `.effective/.derived/.source/.hash`; `load_for_boot()` chain v2 → v1-migrated-in-memory → last-known-good → safe-minimal, one loud line + a recorded `<CF err>` reason each (sent in S4; logged now). `to_v1_settings()` + tmpfs render (G2). Active only if `camera_config.yaml` exists; boot validity judged on the active mode's keys. | **stage-gate test**: for every profile/fixture, render → all v1 loaders → per-key equal to the v1 golden `settings.json`; wire scenarios re-run on a migrated copy → traces byte-identical; fallback tests (corrupt v2, missing v2 + v1, corrupt everything, no PyYAML) never exit non-zero before the daemon would start. |
| S2e | state v2 (G1 shape) + config hash (8 hex, sha256 of canonical effective config, `hv=1`; **logged only**) + `config_journal.jsonl` (2×250 rotated by rename, fsync per append, torn-line tolerant). All writes: unique tmp name, fsync, `os.replace`, fsync dir. | kill-during-write tests (subprocess SIGKILL at each step: state and journal always readable, old-or-new, never torn); rotation test; torn last line skipped. |
| S2f | deploy: stage into `BM_Devel_Pi.next`, `import yaml` check, compileall, `--print-config --json` parity old vs new (same file → identical), validate the migration candidate, refuse unless cron is disarmed, copy only after all pass, record hashes; keep `camera_schedule.v1.yaml` beside the v2 file. `rc_field_update.sh` explicit `--profile` (no hostname default) and v2-aware. `dev_mode.sh` on v2 state. Refuse `media_gid` at deploy. Service key created by `--fresh` and the provision skill (`/home/pi/.config/nereus/service.key`, 600; copy to `~/.config/nereus/unit_keys/<host>.key`). Skills (field-update, provision, hotspot) + manifest updated. Recorder settings page and `patch_camera_schedule.py` read-only. | import-closure test passes; manifest test; deploy dry-run on a temp tree; recorder POST refused with a clear message. |

## 3. Stage gate (bmcam003)

1. Mac: every migrated profile == its v1 golden per key; wire goldens byte-identical.
2. bmcam003: catch awake (field-update skill), disarm, back up, then **dry-run** migration →
   report; **real** migration + S2 deploy; one stills and one video cycle over the console,
   compared with the S1 run; **rollback rehearsal**: redeploy 684d9c7, confirm it reads the
   untouched v1 files (`--print-config --json` equal to pre-migration), redeploy S2, restore.
3. Holding the bus on for this needs **your explicit OK in chat at the time** (as in S1).
4. bmcam004 follows after 24 h, when you release it from the mote session.

## 4. Not in S2

W7; any v9 verb, `<CF>` on the wire, hash on the wire (S4); supervisor (S3); bmcam001/002.

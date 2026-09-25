# Sprint26 — one supervisor, one config, one command path (design spec)

Written 2026-09-24. Status: **DRAFT for Nick's approval.** N1–N9, O1, O2, O9, O10 and the stage order (§8) were
ruled by Nick in the session that wrote this, and O3–O7 on 2026-09-25; §10 records every ruling. Revised 2026-09-25
after a three-reviewer technical review with two consensus rounds (`REVIEW_20260925.md`); the
agreed changes are folded into §4–§8.

Supersedes KICKOFF.md §3 stages R1–R2 and decision D1 (mode is no longer YAML-only). KICKOFF
D2–D5, the two-lane loop (§2), R3–R5 and the guardrails (§6) stand. The §5 trigger controls
survive as one-shot `kv` overrides on `trg` (stage S4).

Evidence for §2 and §7–§8 comes from a full read of the runtime on `origin/development`
05832c8, plus two repo-wide audits (config key usage; dead code and legacy inventory) run the
same day. File:line references point at that commit.

## 1. Why

The camera grew as an experiment and is becoming a product. Three things got in each other's
way:

- **Behaviour.** Four boot routines each wire the UART daemon, time sync, gate and halt their
  own way. Features land in one and not the others: video got the daemon only in Sprint25 S3,
  and it still ignores `trg`.
- **Config.** ~100 settings are spread over 9 loaders with 3 parsing styles and different
  true/false rules. About half the keys are HEIC-only or dead, several concepts appear twice,
  and two live islands (`video_tx`, `media_key`) exist in no repo profile.
- **Commands.** The MVP covers ~10 settings through preset tables. Adding one setting touches
  ~7 files, every ack carries the whole settings snapshot, and `help`/`cfg` answers can't be
  seen remotely.

The unit is underwater, so every setting has to be changeable remotely, safely, from one path.

## 2. How it works today (facts)

Cron `@reboot` → `rc_run_capture_cycle.sh` (flock) → `rc_progressive_jpeg.py --transmit`, one
process per boot. `main()` resolves YAML, loads the saved command state, applies the overlay,
then picks a routine by `capture_mode`. **The decision is made before the daemon starts.**

| routine | behaviour | command daemon | `trg` |
|---|---|---|---|
| `main_pi_camera.py` (HEIC, own cron wrapper) | still per boot | none | no |
| `rc_progressive_jpeg.run_cycle` | still per boot | from cycle start to halt | yes |
| `rc_video_tx.run_video_tx_cycle` | clip per boot | since S3 | **no** |
| `video_recorder.run_video_mode` | records to SD; sends only status lines; halts after `session_minutes` if > 0 | whole session | no (a pending `trg` stays armed on a video unit) |

- Commands apply on the **next boot** (Sprint11 C1/D2); only `wap` and `rsd` act within a boot.
- Media type is YAML `capture_mode` only. "Per boot" vs "stay on" falls out of `power_halt`
  plus Spotter bus power.
- A window-gate skip returns straight to the halt with no listen tail, so a bad window leaves
  the unit nearly deaf (`run_cycle`, `skip_win` path).
- `help`/`cfg` answers go to the Spotter **console** (`spotter_print`), which nobody can see
  over cellular.

Command arrival timing (why the new design never waits at boot):
- Held commands replay ~10 s after bus power, but the Pi is up at ~38 s, so they are lost
  until the mote cache lands (Sprint23, TODO-BM-017).
- The mailbox drain that our own transmit triggers arrives 1–4 min after the cycle
  (finding 006).
- Sprint11 deleted a 90 s pre-capture listen: it caught nothing and pushed transmits into the
  :05 blackout.

## 3. Decisions

| # | decision | status |
|---|---|---|
| N1 | One supervisor for all modes. The UART daemon starts first and lives for the whole process. Actions never own the daemon, UART, time sync or halt. | Nick |
| N2 | Operating mode = `media: still\|video`, `run: per_boot\|stay_on`, `output: transmit\|save_local`. Each is a YAML default and each is commandable. | Nick |
| N3 | Every setting is remotely changeable. YAML is the shipped baseline; remote changes live in an **overlay file**; `reset` returns to YAML. | Nick |
| N4 | Keys that can cut the unit off are **guarded** (§6.3). File paths are locked. | Nick |
| N5 | The continuous SD recorder folds in later. | Nick |
| N6 | Mode commandability is in scope this sprint (reopens D1). | Nick |
| N7 | **Config v2**: one schema registry drives the loader, validation, defaults, commands, `help`, the GUI and the docs. The file is restructured by concept, dead keys go, and one loader replaces nine (§5). | Nick |
| N8 | **Commands v9**: the device takes values, not preset indices. The eleven preset verbs are replaced by `set`/`get`/`reset`/`cfm`; presets move to the dashboard and GUI. Acks shrink to a config hash (§6). | Nick (MVP verbs on no deployed unit, O1) |
| N9 | **Cleanup ships first** (S1, §8): retired and dead code goes before the refactor, so the refactor moves less. | Nick |

## 4. The supervisor

```text
boot
  config = load(YAML ⊕ overlay)          # one loader, one dict (§5)
  daemon.start()                         # subscribe + reader thread, first thing after config
  drain queued commands (non-blocking)   # applies what is already here; never waits for more
  Spotter UTC read (buffer cleared first)
  loop:
    re-resolve config if the overlay changed
    decide: pending trg?  scheduled action due?  nothing?
    action(media, output, one-shot kv)   # capture → encode → [heals] → send | save
    flush acks; <HL> status lines
    per_boot: listen tail (always, including after a window skip) → halt per power.halt
    stay_on:  idle; process commands every 0.2 s; <WS a=idle up=.. cfg=..> every
              heartbeat_s; until interval_s elapses or a trg arrives
```

- **Keep-alive**: every command received, and every request from a connected GUI client,
  pushes a per_boot unit's halt back by `commands.keepalive_s` (default 300 s), up to
  `commands.keepalive_max_s` (default 1800 s) per boot. An operator talking to the unit is
  never halted mid-conversation, and the energy cost stays bounded. **Clamped to the per_boot
  budget** (the Spotter cuts bus power on its own schedule) unless `power.bus_always_on: true`.
- **Explicit hold** (Nick): `hld` holds the unit awake for N minutes, whatever the mode
  (`v:0` releases). It is not persisted: a reboot clears it, so a forgotten hold can't drain
  a battery unit for more than one boot. Capped at `commands.hold_max_min` (default 120) and
  by the same bus-power clamp; the ack echoes the minutes actually granted. Lands in S4.
- **Decision points**: boot (after the drain) and after every action. A setting applied
  between decision points governs the next action, not the next boot.
- **per_boot**: one action per power-on, the listen tail, then `power.halt`. With halt
  disabled, the unit keeps listening and still services `trg`, but does not repeat on its own.
- **stay_on**: never halts (`power.halt` ignored, one loud line per boot).
  - `interval_s: 0` means trigger-only.
  - Scheduled repeats obey the window; `trg` bypasses it (D-S12-4).
- **`trg`** is serviced at every decision point, in every mode, for both media types.
- **Heals** ride every transmitting action, exactly as `rc_heal` does today.
- **Time**: the supervisor owns Spotter UTC. Before each read it clears the daemon's 4 KB raw
  buffer under `_raw_lock` **before** writing the subscribe frame, and accepts only frames
  after that point: `_find_clock_payload` returns the FIRST (oldest) match
  (`spotter_time_sync.py:548-568`), so a long-lived buffer would feed an hours-old time to the
  system clock and the media key. Monotonic sanity check; the system clock is stepped only
  when drift exceeds a few seconds. Bench-test that a re-subscribe yields a fresh publish.
- **Heal events**: only the `heal_events` already sent as `<HL>` are cleared, after they go out,
  and kept if the action fails. Clearing at action start would lose the `<HL>` for an `rsd` that
  arrived in the previous listen tail.
- **Acks** are always deferred out of a burst. Video already works this way; stills do NOT today
  (`defer_acks_during_transmit` is false in every profile, so stills acks take paced mid-burst
  slots). Making it universal is a deliberate wire change in its own commit (§8).
- **SIGTERM** sets a flag only (raising inside `subprocess.run` would kill the capture or the halt
  script); the loop exits at the next safe point: acks flushed, daemon stopped, port closed.
  Ignored once halting has started. A capture retry (up to 3×(30+60) s) gets a bounded wait.
- **Action log**: one JSON line per action in `cron_logs/supervisor_actions.jsonl` (trigger
  id, mode, stage, media key, sent/planned, elapsed, RSS).
- **Port (review BLOCKER)**: today both cycle bodies close the shared port in their `finally`
  (`rc_progressive_jpeg.py:874-890`, `rc_video_tx.py:413-426`); `close_bm_serial` nulls the
  global, and the next `_get_bm_serial()` silently opens a SECOND descriptor with no timeout.
  A long-lived unit would go deaf after its first action. So a PortOwner holds the one port;
  a lazy second open is refused; shutdown → close → halt move out of the action bodies into
  the owner. One owned accessor, not explicit port passing (that reaches deep into the capture
  watchdog's telemetry; deferred). Goldens record every port open/close.
- **stay_on hardening**: reader-thread watchdog on every idle tick → exit non-zero into a
  restart wrapper with backoff (stay_on only; a per_boot unit never loops). Crash-loop cap 5 in
  10 min → fall back to per_boot with halt forced to dry-run and one `<WS a=crashloop>`, so a
  wall-powered unit stays reachable. RSS ceiling → clean exit and restart (measure 20 actions on
  bmcam003 first; fallback: encode stills in a forked child). Periodic re-subscribe if the bench
  shows a bridge reset drops the subscription. Per-action log markers + log rotation.
- **Commands off** (`commands.enabled: false`): no daemon, no subscribe; the owner reproduces
  today's port opens exactly (parity for commands-off units). N1 applies when enabled.
- **Cron**: same `@reboot` line and flock. The per-boot `py_compile` of 22 files moves to
  deploy time. It already misses `command_*`, `rc_command_hooks`, `bm_frame_decoder` and
  `rc_media_id`; its boot cost is not measured yet.

### Actions (what the four routines shrink to)

| media × output | action |
|---|---|
| still × transmit | capture → prepare → quality ladder → send (today's `run_cycle` body) |
| video × transmit | record one clip → fit to budget → send (today's `run_video_tx_cycle` body) |
| still × save_local | capture → prepare → encode → save; storage guard (natives grow ~1.4 MB/cycle and nothing prunes them today) |
| video × save_local | record one clip at recorder quality → save; the ring applies |

Each action is a function `(config, services) -> summary`. **Budget**: a per_boot unit keeps ONE
`CycleBudget` anchored at process start. It is the power-window guard (the tail is clamped to
it minus `TAIL_SAFETY_S`) and it sizes the stills ladder and the video message budget, so
re-anchoring it would change the wire. An extra per_boot action (O3) must fit the same budget,
else the `trg` stays armed for the next boot. stay_on uses one budget per action.

## 5. Config v2

### 5.1 One registry, one loader

`BM_Devel_Pi/config_registry.py` lists every key with: path, type, default, range or enum,
guard class, apply timing, an optional 1–3 char short name (for byte-tight commands), a help
line, and optional dashboard presets. It is the single source for:

- **Loader**: `load_config(path, state, strict) -> Config` (`.effective`, `.derived`, `.source`
  per key, `.hash`). **Strict at deploy, migrate and `set`**: unknown keys and bad types are
  rejected with the key named. **Never bricks at boot**: `load_for_boot()` tries the v2 file,
  then v1 migrated in memory, then last-known-good, then safe-minimal, with one loud line and a
  `<CF err>` for each fallback. Boot validity is judged on the ACTIVE mode's keys only (v1 is
  mode-scoped too). Last-known-good carries power halt, UART, network_type, topic and
  commands.enabled. Safe-minimal (fresh unit / corrupt SD only): commands on, no media action,
  `network.default: none`, network_type 0x02, halt enabled + dry-run. PyYAML is a hard
  dependency, checked at deploy (today three loaders silently return `{}` without it).
- **Defaults**: v1's absent-key behaviour (e.g. no `network:` block = do nothing, no
  `media_key` = legacy wire), so a migrated unit behaves identically. Migrated files spell out
  EVERY key, so defaults matter only for new hand-written files. Registry entries carry
  `nullable`, `group_replace` (v1 `foc/awb/exp` replace the whole sub-block and force it on),
  `v1_sources`, `validate_when`, `wire_visible`, `since`. Derived values (output size, message
  budget, video geometry) live outside the registry and outside the hash.
- **Command validation, `help`, the bench GUI, and the command reference doc**: generated.
- **Coverage test**: every key in every profile must be in the registry, and vice versa.

It replaces 11+ loaders (9 island loaders plus the camera_controls and GUI readers), 3 copies of `_strip_yaml_value`, two readers each for `uart_port` and
`bm_serial.*`, and the `BM_CAMERA_CONFIG_PATH` vs `--config-path` split (`bm_serial.py:12`).

### 5.2 Structure (grouped by concept, one name per concept)

```yaml
schema: 2
mode:     {media: still, run: per_boot, output: transmit, interval_s: 0, heartbeat_s: 300}
schedule: {timezone: America/New_York, window: {enabled: true, start: "08:00", end: "15:00"}}
time:     {source: spotter_utc, set_system_clock: true, timeout_s: 60,
           allow_system_fallback: false, rtc: {hwclock_path, plausible_after_utc}}
power:    {halt: {enabled: false, dry_run: true, mode: halt, script_path: ...}}
camera:   {backend: rpicam, native: {width: 4608, height: 2592, jpeg_quality: 95},
           focus: {mode, lens_position, range, speed},
           white_balance: {mode: auto, gains: null},   # mode auto|daylight|cloudy|manual;
                                                       # manual needs gains [red, blue]
           exposure: {ev: 0.0}}
still:    {crop: [x, y, w, h], output_width: 1000, quality_ladder: [90, 80, ...],
           message_cap: 195, budget_min: 12}
video:    {record: {framing: stills_roi_1000p, fps: 15, bitrate_mbps, lead_in_s, encoder: {...}},
           send: {duration_s: 5, size: 480x270, fps: 10, message_cap: 126,
                  keyframe_repeat_max: 30, x264_preset: medium, budget_min: 12},
           logger: {clip_minutes, session_minutes},       # continuous recorder (N5)
           storage: {max_used_pct, min_free_gb}, ui: {enabled, port}}
uplink:   {uart: {port: /dev/ttyAMA0, baudrate: 115200}, network_type: 0x02,
           chunk_chars: 384, msg_interval_s: 1.3,
           lane: {enabled, grid_s, pre_guard_s, post_guard_s, max_wait_s},
           media_key: {retain_days: 14}}
commands: {enabled: true, topic: bmcam/cmd, listen_tail_s: 150}
network:  {default: nereus_hq, ap_fallback_s: 90, ap_timeout_min: 60}
```

| today | v2 | why |
|---|---|---|
| `capture_mode` + `video_tx.enabled` | `mode.media` (+ `run`, `output`) | one switch; `heic` goes with the HEIC path |
| `timezone_preset` + `timezone` | `schedule.timezone` | duplicate; the preset silently wins today |
| `enforce_time_window` + `enforce_spotter_time_window` | `schedule.window.enabled` | mirrors; whichever comes last in the file wins |
| `image.*`, `image_pipeline.{enabled,crop,spatial,heic}` | removed | HEIC-only, yet `image.*` is validated on every RC boot (a bad value blocks boot) |
| `image_pipeline.source.*`, `.capture_backend`, `.camera_controls.*` | `camera.*` | the only block stills and video actually share |
| `camera_controls.exposure.mode` | `camera.exposure.ev` | `mode` builds no CLI flag today (dead); `ev` is what the `exp` table sets |
| `progressive_jpeg.quality.{q_max,q_min,step}` | removed | dead whenever `ladder` is set (every profile) |
| `progressive_jpeg.max_run_time_min` | `still.budget_min`, `video.send.budget_min` | video borrows the stills key today |
| `video.preset` vs `video_tx.preset` | `video.record.framing` vs `video.send.x264_preset` | same word, unrelated meanings |
| `video.fps` vs `video_tx.fps` | `video.record.fps` vs `video.send.fps` | record rate vs send rate |
| `bm_serial.image_buffer_size`, `image_transmit_delay_seconds` | `uplink.chunk_chars`, `uplink.msg_interval_s` | names that say what they are |
| `media_gid.*`, `bm_commands.pre_capture_listen_s`, `defer_acks_during_transmit` | removed | retired (wire rev 5, Sprint11) or always on |
| `video_tx.*`, `media_key.*` (on units, in no profile) | `video.send.*`, `uplink.media_key.*` | the repo becomes the truth again |

**Migration**: `tools/config_migrate_v1_v2.py` maps v1 → v2 and refuses unknown keys. The bench
units' live YAMLs are pulled into `device_profiles/` first (they have drifted: `video_tx` and
`media_key` exist only on the units). v2 uses NEW filenames
(`camera_config.yaml`, `bm_command_state_v2.json`); the v1 files stay untouched, so rollback is
a plain redeploy of the old SHA. Dual-read until every unit is migrated. The migrator reads
each key with its OWN v1 loader (they parse bools, quotes and `#` differently), is dry-run by
default, writes a diff report, refuses unknown or ambiguous values, and stops for a human on:
a missing or `heic` capture_mode, and a network_type that resolved to 0x01 because the key was
absent or PyYAML was missing. It migrates the command state (tables v8 only; commands-off
units archive theirs): `roi`→`still.crop` (roi 5/6 output clamp becomes derived), `foc/awb/exp`
→ whole group with explicit nulls, `win`→ both budget keys, `txd`, `cap`, `hlt` 1-3 →
`power.halt.*`, `src≠0` dropped and reported; `applied_ids`, `pending_heals` copied;
`pending_trigger` gets an empty `kv`; per-range high-water marks start empty.

**One mutation path.** The recorder's settings GUI edits the YAML in place, non-atomically, for
18 keys (`video_settings.patch_yaml`). `tools/patch_camera_schedule.py` regex-patches it. It is made
read-only from S2 until S7, when the camera GUI writes through the same overlay and validation
as a remote `set`. YAML is changed only
by deploy or migrate.

## 6. Commands v9

### 6.1 Verbs

Every command must be ≤ 270 B including the newline (the Sofar API limit,
`docs/sofar_command_api_reference.md:74`).

| verb | wire | effect |
|---|---|---|
| `set` | `{"id":N,"c":"set","kv":{"video.send.duration_s":8,"mode.run":"stay_on"}}` | Full path or short name. All keys valid → all applied at the next decision point. Any invalid → none applied; ack `e:"key"`/`"val"`. |
| `get` | `{"id":N,"c":"get","k":["mode","video.send"]}` | Effective values + source (`yaml` / `cmd <id>`) as a `<CF>` uplink. A group name returns the whole group. |
| `reset` | `{"id":N,"c":"reset","k":["video.send.duration_s"]}` / `{"id":N,"c":"reset","all":1}` | Overlay key(s) removed → YAML value. |
| `cfm` | `{"id":N,"c":"cfm","ref":<set id>}` | Confirms a guarded change (§6.3). |
| `trg` | `{"id":N,"c":"trg","v":2,"kv":{"d":8}}` | Action now. `v`: 0 cancel · 1 capture, save only · 2 capture + output per mode · 3/4 reference image. `media` can be overridden one-shot in `kv`, which replaces KICKOFF `v:5/6`. `kv` applies to this action only and is never persisted. |
| `hld` | `{"id":N,"c":"hld","v":30}` | stay awake 30 min (§4); `v:0` releases |
| `rsd` | unchanged (SPEC_resend_heal §5) | heals |
| `wap` | unchanged | WiFi AP, immediate, self-reverting |
| `ping`, `help` | `help` generated from the registry | |

**Retired (N8):** `roi foc awb exp win txd cap src hlt twn tmz` and `cfg` (replaced by
`get`). Each maps to one or two `set` keys:

| old | new |
|---|---|
| `roi` | `still.crop` |
| `foc` | `camera.focus.*` |
| `awb` | `camera.white_balance.mode` |
| `exp` | `camera.exposure.ev` |
| `win` | `still.budget_min` |
| `txd` | `uplink.msg_interval_s` |
| `cap` | `still.message_cap` |
| `src` | `trg 3/4`, one-shot only (a persistent reference-image mode is a test hazard) |
| `hlt` | `power.halt.*` |
| `twn` | `schedule.window.*` |
| `tmz` | `schedule.timezone` |

Their preset lists move into the registry's `presets` field, so the dashboard and bench GUI
still offer "1 m manual focus" without the device carrying tables.

**Validation is of the whole result, not one key.** Before acking a `set`, the unit builds
the effective config it would run with and checks it: per-key type and range, cross-key rules
(e.g. `white_balance.mode: manual` needs `gains`; `still.crop` inside the native frame), and
environment checks for the new mode (e.g. `mode.media: video` needs `ffmpeg` and a valid
`video` block). A failure is acked `ok:0` with the reason in `<CF>`, and nothing changes.

**Reply lanes.** The unit cannot tell which lane a command came in on (both arrive as the same
`bm pub` frame), so the command id decides. Every answer is printed on the Spotter console
(free, instant, visible on USB with no internet). A cellular copy is queued only for the
remote id ranges (§6.2). Rejections carry their reason in the ack (`e`, `k`) only. A cellular
`get` is capped at 3 `<CF>` parts (else `e:"big"`); `"to":"con"` skips the cellular copy.
All cellular sends (acks, `<CF>`, `<WS>`, `<HL>`) share one pacer with the lane guard, so
nothing lands in the 30 s after a 5-minute boundary. Console text is ASCII only
(`spotter_print` sends the character count as the length, `bm_serial.py:229-230`).
The console copy is written for a person (Nick: "I want to see a reply that the camera
received and made the change"); the cellular copy is compact:

```text
[bmcam003] OK id=7 camera.white_balance.mode: auto -> manual, gains: none -> 1.62,1.91 (next capture) cfg=a41c09e2
[bmcam003] REJECTED id=8 camera.white_balance.mode=manual: needs camera.white_balance.gains
[bmcam003] OK id=9 hold awake 30 min (until 14:32 UTC)
```

### 6.2 Wire and state

- **Ack**: `{"id":N,"ok":1,"h":"<cfghash8>"}` ≈ 40 B, down from 119 B today (the `st` snapshot
  of 11 indices grows with every setting). An error ack adds `"e"`. For a staged key the ack
  adds `"s":1`. The backend does not parse ack `st` today (grep of nvd staging), so this change
  needs only the bench tools (`sofar_poll_acks.py`, the GUI) updated.
- **`<CF v=1 h=<hash8> k=v …>`** uplink: carries `get` answers and a config summary after any
  change, chunked if long. The heartbeat and START carry `cfg=<hash8>`, so the backend sees
  drift. The parser is an nvd stage.
- **Config hash = fingerprint, not history.** It is the first 8 hex digits of sha256 over
  the canonical effective config (every registry key, sorted, YAML ⊕ overlay). Same settings
  → same hash; any change from any source → a new hash. That includes a boat-GUI edit, a
  `reset`, a guarded revert, or a field update of the YAML. It rides on every ack, `<WS>` and
  START. It answers "is the unit running exactly the config I think it is?" in 8 bytes.
- **History lives in two places:**
  - **Backend command log (authoritative, fleet-wide):** every command with who, when, lane,
    ack, and hash before/after, plus every `<CF>` snapshot keyed by its hash. A hash is then
    a lookup key: "a41c09e2 = this exact config, first seen 2026-10-02 14:02".
  - **Unit journal (`config_journal.jsonl`, two files of 250 lines rotated by rename, fsync per
    append, torn-line tolerant):** time, source
    (id-range name / `local_gui` / `revert` / `deploy` / `field_update`), command id, key, old,
    new, hash. Readable with `get journal` or on the camera's GUI. This is the record that
    survives when the backend never heard about a change made on the boat.
- **State file `bm_command_state_v2.json`**: `overlay {path: value}`, `guarded {...}`,
  `result_cache` (last 256 ids → original `ok/e/h`), per-range high-water marks, a persisted boot
  counter, `pending_trigger` (with its one-shot `kv`, re-validated on load), `pending_heals`.
  Migrated from v1 on the bench rigs (they rely on it: `hlt 3` dev mode, pending heals).
  Writes: unique tmp name, fsync, rename, fsync the directory.
- **Duplicates and replays**: Sofar's cellular mailbox never expires, and v1 dedupe keeps only
  32 ids, so an old command could re-apply over a newer one. A duplicate id returns its ORIGINAL
  result plus `"d":1` (a duplicate `get` re-sends `<CF>` at most once per 10 min). In the
  remote ranges an id below the range's high-water mark is refused `e:"old"`; `"b":<hash8>`
  allows compare-and-set.
- **Strict JSON**: finite numbers only (Python's `json` accepts NaN, which passes every range
  check), strings `[A-Za-z0-9_:./+-]{1,48}`, lists of at most 4 numbers, no bool-as-int, JSON
  ≤ 248 B (the 270 B console line includes 22 B of `bm pub bmcam/cmd ` + ` 1 1` + newline).
- **Mid-burst commands (S4)**: appended raw to a durable inbox (≤ 1 KB each, 64 entries /
  16 KB, drop-oldest with a loud log, torn-line tolerant); parsed, validated, applied, persisted
  and acked at the next decision point, then the inbox is cleared. No validation or subprocess
  on the burst path. D15 holds.
- **Command-id ranges per sender** (documented in the command reference):

  | range | sender | reply lanes | high-water |
  |---|---|---|---|
  | 1 – 99 999 | console by hand; `dev_mode.sh` (moves off epoch-second ids) | console | no |
  | 100 000 – 999 999 | backend heals (`heal_commands.FIRST_COMMAND_ID`; S6 adds a guard refusing ≥ 1e6) | console; `<HL>` is the cellular answer | no |
  | 1 000 000 – 99 999 999 | dashboard, bench GUI (floor moves from 1000 to 1e6 in S4), `sofar_send_command` (range-checked) | console + cellular | yes |
  | 100 000 000 – 199 999 999 | signed service commands (§6.3) | console + cellular | yes |
  | 2 000 000 000 – 2³²−1 | bench conductor | console | no |

  Journal `source` = the range name, `local_gui`, `revert`, `deploy` or `field_update`.

### 6.3 Guarded keys

- **`guarded_revert`**: comms-path keys (`uplink.uart.*`, `commands.enabled`,
  `commands.topic`, `mode.output` → `save_local`). The new value applies at once. If no `cfm`
  for that `set` id arrives within **2 transmitting actions or 3 boots, whichever comes first**
  (no wall clock: a broken comms key can kill the time read), the unit reverts, journals which
  limit fired, and reports `<CF reverted=>` on its next uplink. The boot counter is incremented
  and persisted before the UART opens, and the revert is evaluated then; with commands turned
  off the supervisor reverts on its own. The confirm can only arrive over the new path, so it proves the
  path works.
- **`guarded_stage`**: failures the unit can't undo, chiefly `power.halt.enabled: true` on wall
  power (dark until a physical power cycle). The value is staged, not applied, until `cfm`
  arrives.
- **`locked`**: filesystem paths (`power.halt.script_path`, state/sent/inbox paths,
  `time.rtc.hwclock_path`), `uplink.network_type` (0x01 = Iridium fallback: one bad value
  would send whole bursts on satellite credits; registry default 0x02; it applies to stills
  only, video forces cellular-only per message) and `uplink.chunk_chars` (the backend decoder
  assumes it). Customers and the dashboard never see a control for these.
- **`service`** (Nick, O6): `uplink.network_type` and `uplink.chunk_chars` are locked for
  customers, but Nereus can force a change with a **signed command**:
  `{"id":N,"c":"set","kv":{"uplink.chunk_chars":320},"sig":"<16 hex>"}`, where `sig` =
  HMAC-SHA256(unit service key, canonical JSON of the command without `sig`), truncated to
  64 bits.
  - The key is 32 random bytes generated per unit at provisioning. On the unit it lives at
    `/home/pi/.config/nereus/service.key` (mode 600, a locked path), and on Nick's side in
    `~/.config/nereus/unit_keys/<host>.key`. It never goes in the repo, a YAML or a command.
  - A signature, not a PIN, because every command is visible in Spotter console logs and in
    Sofar's records: a PIN would leak on first use, while a signature is only good for that
    one command.
  - Replay is blocked by the id: the service range has a high-water mark (§6.2), so a
    captured signed command cannot be re-used.
  - A signed change is also `guarded_revert`: it reverts unless a `cfm` arrives over the new
    setting, so a wrong forced value heals itself.
  - Missing or bad `sig` → `e:"auth"`, nothing changes.
  - A unit without a key file refuses every service change. Recovery is a field update, the
    same as today.
  - Tool: `tools/bm_service_sign.py <host> '<json>'` prints the signed console line. Stdlib
    `hmac` only.
  - Cost: `,"sig":"<16 hex>"` adds 24 B to the 248 B JSON budget.
  - Lands in S4. The provisioning skill and `deploy_rc_runtime.sh --fresh` create the key in
    S2f; existing bench units get one in the S2 bench step.

### 6.4 Operator surfaces (Nick, 2026-09-24)

| where | surface | how |
|---|---|---|
| boat, camera in hand, no internet | **camera-hosted GUI** (GoPro-style): the camera raises its hotspot at boot; a laptop or phone joins it and opens the settings page | view / change / save / reset every setting, see the journal and the latest capture |
| boat / bench, USB to the Spotter | raw console: `bm pub bmcam/cmd {"id":7,"c":"get","k":["mode"]} 1 1` | human-readable reply printed on the console (§6.1) |
| deployed, laptop with internet | **web UI** (bench GUI now, customer dashboard later) | Sofar command API; command status per §6.5 |

**Camera-hosted GUI.** Served by the supervisor process (stdlib `http.server` thread; no new
dependencies). It **does not edit the YAML.** It posts the same JSON commands as the wire
(`set`, `get`, `reset`, `hld`, `trg`) to the supervisor's local inbox. So there is one
validation path, one overlay, one journal, and a boat edit shows up in the backend's hash and
`<CF>` like any other change. The page is generated from the registry (groups, labels, help
text, presets, guard badges). Guarded keys ask for confirmation in the page itself; the local
GUI is the proof-of-path, so no `cfm` round trip is needed. It replaces the recorder's
19-key YAML editor (`video_settings.patch_yaml`, non-atomic). The gallery stays.

**Hotspot at boot.** This already exists: `network.default: ap` (Sprint16, `network_config.py`,
`network_ap.sh`) raises an **open** AP with SSID = hostname. Ruled (O9, O10): no password by
default; the AP turns off if nobody logs in within `network.ap_boot_min` (default 5).
The GUI's presence counts as activity for keep-alive (§4), so the camera doesn't halt while
someone is on the page.

### 6.5 Command status in the web UI (desired vs reported)

The web UI keeps, per unit, the **desired** config (what we asked for) and the **reported**
config (what the unit last confirmed). A setting whose two values differ shows as pending,
next to its command's status:

| status | meaning | signal |
|---|---|---|
| Sent | Sofar accepted it into the Spotter's cellular mailbox | HTTP 202 (the only delivery signal Sofar gives) |
| Waiting for camera | no answer yet; shows "expected by HH:MM" | none yet: the ack rides the uplink, which lags 15–45 min |
| Late | past the expected time | offer re-send with the SAME id (the unit returns the original result); if a newer command has gone out since, re-issue with a new id (a stale id is refused `e:"old"`) |
| Saved | the camera received, validated and saved it; applies at the next capture/boot | ack `ok:1` + new hash |
| In effect | a later heartbeat or START carries that hash | `<WS>` / START `cfg=` |
| Rejected | the camera refused it; the reason is shown | ack `ok:0` + `<CF>` reason |
| Staged / Reverted | a guarded key awaits `cfm`, or was reverted for lack of one | ack `s:1` / `<CF> reverted=` |
| Superseded | a newer command changed the same key before this one was answered | backend log |
| Changed elsewhere | the hash moved without a command from us (boat GUI, field update) | hash + `<CF>` |

"Expected by" = the Spotter's next report + the camera's next wake + the uplink lag, with
Sofar exposure lag included (rows appear 11–30 min late).

**A halted camera hears nothing.** A per_boot unit is only awake from boot through its listen
tail. To talk to one in hand, wake it first by power-cycling the bus from the Spotter console
(`spotter-usb-power-cycle` skill); keep-alive (§4) then holds it awake while you work.
Remote commands to a halted unit depend on delivery timing (KICKOFF §0): until the mote-side
command cache (TODO-BM-017), a command replayed at bus power arrives before the Pi is up and
is lost, so the sender retries until acked.

## 7. Cleanup (all in S1)

### Runtime: memory, boot time, simplicity

| # | item | evidence | action |
|---|---|---|---|
| C1 | `process_image_v2.py` (2,728 lines) loads on every boot | ~48% used by RC, ~42% HEIC-only, ~49 lines dead (`split_image_jpeg`, `encode_to_base64`, `COMPRESSION_QUALITY`) | move the RC half into `capture.py`, `telemetry.py`, `media_meta.py`; the rest leaves with HEIC (C3). |
| C2 | PIL loads on every boot, including video boots that never make a JPEG | imported at `process_image_v2.py:16`, `rc_jpeg_encoder.py:38`, and `command_bindings.py:57` (the last for arithmetic only) | lazy-import inside the still action |
| C3 | HEIC path | `main_pi_camera.py`, `run_capture_cycle.sh`, `heic_encode_helper.py`, `crop_downsample_helper.py`, `read_CPU_temp.py`, `tools/deploy_runtime.sh`; the base YAML still defaults `capture_mode: heic` (a fresh unit on it logs "Nothing to do" every boot) | delete (O2) |
| C4 | `media_gid` (retired by wire rev 5) | `rc_media_id.py`, wired through `rc_progressive_jpeg`, `rc_transmit`, `rc_uplink_messages`, plus its tests | delete; move `chunk_prefix` into `rc_media_key` |
| C5 | CRC16/COBS implemented 3 times | `bm_serial:249/272`, `spotter_time_sync:493/501`, `bm_frame_decoder:63` | one module; inbound COBS `FrameAccumulator` is test/mock-only |
| C6 | Per-cycle full scans of `images/`, `buffer/`, `cron_logs/` | `collect_storage_health`, `process_image_v2.py:396-427`; cost grows with the SD | `statvfs` + counters; the buffer dir and `zh` count are HEIC-era (START `bf`/`zh` are safe to drop: backend `safe_int`) |
| C7 | Boot `py_compile` of 22 files, a list that misses the command modules | `rc_run_capture_cycle.sh` | compile at deploy |
| C8 | Stale comments and labels | "Sprint08" in runtime logs, "topic provisional until Q11", video "inherits progressive_jpeg.crop" (ended Sprint17) | fix as files are touched |

### Repo: speed of work

| # | item | evidence | action |
|---|---|---|---|
| C9 | 235 MB tracked; `runs/` 122 MB and `reference_images/` 107 MB | jpg 145 MB, png 73 MB; `.gitignore` excludes `*.jpg/*.png/*.h264` but they were force-added; 76 byte-identical groups (37 MB) | **`reference_images/` stays tracked as-is (Nick, 2026-09-25: used for testing now and later).** For `runs/`: stop tracking new run imagery (keep manifests, CSV, RESULTS.md, cut-sheet thumbnails); existing files stay. History rewrite = O4 |
| C10 | ~45 one-off tools (HEIC/JPEG sweeps, DOE, cut sheets, Sprint17/21/22 research, soak rollups); `tools/README.md` covers only Sprints 02–03 | audit list | move to `tools/archive/` with a one-line index; rewrite the README for the operational set |
| C11 | Non-tests in `tests/`; tests of retired code | `bm_image_quality_doe_capture.py`, `run_bm_image_doe_boot.sh`, `test_rc_media_id.py`, HEIC cases | move or delete with their code |
| C12 | Tracked `.DS_Store` ×6, duplicate example YAML, two diverged `watch_bmcam_boot_logs_split*.py` | | delete |

## 8. Stages and rollout (order ruled by Nick, 2026-09-24)

Order: **clean up → new settings storage → one runtime → new verbs → prove every command over
the console → remote → UI dead last.** Each stage is its own feature branch from `development`
and its own PR back into `development`. Each passes its gate on the bench before the next
starts.

### 8.1 The safety net (built first, used by every stage)

- **Goldens.** A FakeUart factory replaces `serial.Serial` everywhere a port is opened (`bm_serial`,
  `spotter_time_sync`, `rc_command_hooks`). Every fake shares ONE ordered event log: bytes written,
  port opens and closes, timeouts. The harness drives the real `rc_progressive_jpeg.main()`, with
  stills fakes passed through `cycle_overrides` and video fakes through
  `run_video_tx_cycle.__kwdefaults__`, so production code does not change to be testable.
- **Output.** Frames are decoded into `wire.txt` (topic, network byte, payload per frame), plus a
  sha256 of the raw stream and `events.json` with the port opens.
- **Pinned for repeatability.** Time, software sha, hostname, CPU temperature, storage health,
  every path, scripted Spotter UTC frames, and a fake monotonic clock. The fake `sleep_fn` waits
  until the reader is idle instead of sleeping in real time.
- **Scenarios.**
  - Stills: complete, `a=inc`, window skip and capture-retry WS, each with a mid-burst command,
    an `rsd` heal, a `trg`, media_key on/off and phase on.
  - Video: complete, window skip, mid-burst command, heal and bench drop.
  - The help/cfg console tail.
  - Resolved-settings JSON per profile, and v1 state fixtures (roi 5, foc 0 over lens 1.82,
    hlt 3, win, txd).
- **Where they are recorded.** Only with PyYAML installed (the harness fails without it), in a
  pinned dev venv (`requirements-dev.txt`). Without PyYAML this Mac resolves bmcam003 to 300/5.0
  pacing instead of 384/1.3. JPEG bytes come from a deterministic fake encoder off-device, plus
  one on-device golden run on bmcam003 (real Pillow/libjpeg, fake UART).
- **Live config.** YAML and state files are pulled read-only from the bench units (bmcam000,
  003, 004) before S2. They replace those repo profiles as the source of truth (bmcam004 has no
  profile). The pull also confirms that no bench unit runs `media_gid`. bmcam001/002 are
  unreachable and out of scope (O1); their profiles stay as historical record.

### 8.2 Deliberate wire changes (each its own commit with a reviewed golden diff)

Every other commit must leave the goldens byte-identical, including the port-open count.

| # | change | stage |
|---|---|---|
| W1 | drop START `zh`/`bf` (C6; the goldens fake storage health, so it also gets a bench START comparison) | S1, last |
| W2 | acks always deferred out of a stills burst | S3 |
| W3 | listen tail after a window skip | S3 |
| W4 | boot drain applies queued commands this boot | S3 |
| W5 | video services `trg` | S3 |
| W6 | fresh time read per action (buffer cleared, clock stepped only on drift) | S3 |
| W7 | `exposure.mode` (`rem`) and the `*.enabled` switches reshaped | S2 or later, never inside a parity commit |
| W8 | slim ack; `<CF>`; `<WS>` `up=`/`cfg=`/`a=idle`; START `cfg` (core field) + `tg/r/m/d` (worst-case START ≤ 285 B, tested) | S4 |

### 8.3 Stages and commits

| stage | commits (each green, goldens unchanged unless it is a W-commit) | stage gate |
|---|---|---|
| **S1 cleanup** | (1) harness + goldens at 05832c8, deterministic across 3 runs; the same harness also records a goldens set from `main` (the bmcam001/002 wire, for the S6 backend regression test, §11) · (2) dead symbols and comments no log parser reads · (3) `media_gid` out; `chunk_prefix` → `rc_media_key`; the loopback tool updated; a YAML enabling it is refused at deploy/migrate and warned + ignored at boot · (4) one CRC/COBS module with a randomized oracle test against a frozen copy of the old encoder (0–1200 B, zero runs, 254-byte boundaries) · (5) C1 split with `process_image_v2` as a re-export shim and exactly ONE port accessor (the `rc_command_hooks.py:132` swap would otherwise open the UART twice) · (6) PIL lazy; `output_size_for_crop` moved to a PIL-free module; a test that a video `--print-config` never loads PIL · (7) HEIC deleted (incl. `rc_field_update.sh:74` and the extra shell tools); `capture_mode: heic` keeps today's RC behaviour exactly ("nothing to do", no transmit) with the message "heic path retired; set capture_mode" · (8) boot `py_compile` → deploy-time compileall · (9) W1 · (10) repo cleanup PR (C9 `runs/` only, C10–C12) | bmcam003: one stills and one video cycle over the console, byte-identical on the wire; boot-to-transmit time and peak RSS before/after |
| **S2 settings storage** | S2a registry + coverage test (every pulled key mapped or explicitly removed; a file with each block missing migrates to v1-identical behaviour; key segments may not end in `len/length/chunks/buffer(s)/filename`) · S2b v1 reference values + `--print-config --json` in the legacy runtime · S2c migrator for YAML and state · S2d v2 loader + `to_v1_settings()` adapter, active only if the v2 file exists; boot fallback tests · S2e state v2, hash (logged only), journal; kill-during-write tests · S2f deploy (staged `BM_Devel_Pi.next`, `import yaml` check, print-config parity old vs new, refuse unless cron is disarmed), `rc_field_update` (explicit `--profile`), `dev_mode.sh`, skills, manifest; recorder settings page read-only | bmcam003: dry-run, real migration, rehearsed rollback (old SHA reads the untouched v1 files); bmcam004 follows after 24 h |
| **S3a one runtime, per_boot parity** | pure extraction of the cycle bodies into actions (same call order) → PortOwner (shutdown/close/halt move to the owner) → supervisor behind `commands.runtime: legacy\|supervisor`, goldens parameterised over both runtimes → W2–W6, one commit each | goldens identical across runtimes (except W2–W6); one bench cycle each for still and video |
| **S3b stay_on** | loop, interval, heartbeat, watchdog + restart wrapper, crash-loop fallback, SIGTERM flag, RSS ceiling, log rotation; a 50-action fake-time soak asserting the same uart object and stable thread and descriptor counts | 4 triggered cycles, no reboot; 20-action RSS measurement on bmcam003 |
| **S3c save_local** | still + video self-logger actions, stills storage guard | stay_on × save_local 1 h, SD bounded |
| **S4 new verbs** | (1) pure v9 wire module + hostile-input tests · (2) pure whole-config validator (test asserts no subprocess) · (3) dedupe v2 + replay tests · (4) daemon read-only verbs `ping help get` · (5) `set` `reset` · (6) `cfm` + guard classes + boot counter · (7) `trg kv`, `hld`, keep-alive + clamp; `rsd`/`wap` regression tests · (8) durable inbox · (9) W8 · (10) tools: GUI (floor 1e6, `verify_ack` without `st`), `sofar_send_command`, `sofar_poll_acks`, `soak_reconcile`, `dev_mode.sh`, hotspot skill · (11) delete the v8 tables; regenerate the command reference | unit tests for every verb and rejection path + a tools smoke test |
| **S5 console proof** | scripted ladder on bmcam003 then bmcam004 (back up, stop the heal driver, monitor log; evidence per step: console log, state sha256 before/after, hash, END): ping/help/get (help adds nothing to the cellular queue) → duplicates return the original result → rejections (NaN, space, oversize, cross-key, unknown key) leave hash and state unchanged → set/reset back to the original hash → manual WB gains in END `cg` → `trg kv` on both media (hash unchanged, START `tg/r/m/d`) → 2×2×2 modes → `hld` on an always-on bus, then the clamp on a scheduled bus → guarded stage and revert across a bus power cycle → `rsd`, `wap` → a 270 B console line → 24 h conductor loop | both rigs pass; 24 h with 0 lost clips; then the legacy runtime is deleted |
| **S6 remote** | Sofar lane from nereus000 (send log, latency report); prove nested `kv` JSON over Sofar; backend: `<CF>` parser with dotted keys, ack parser, `a=idle`, START `cfg`/`tg`, one shared id allocator with the ≥ 1e6 guard, one Sofar sender for the 1 req/min cooldown, command log + hash→snapshot, desired vs reported; heal auto-send; 24 h remote loop | KICKOFF R3–R5 gates |
| **S7 UI (last)** | camera-hosted settings page + boot hotspot window; web UI command status (§6.5); `CommandState` two-writer safety verified before the GUI thread lands | laptop on the camera's AP with no internet: read, change, reset; AP off after 5 min with no login; a remote command walks Sent → Saved → In effect |

**Hardware**
- **bmcam003** takes each stage first.
- **bmcam004** stays on the previous stage as the control until the stage passes on bmcam003,
  then follows.
- Both rigs are armed and heal-driven overnight as of 2026-09-24. Before S1's bench gate:
  catch each awake, stop `bm-heal-driver` for that rig, back up crontab and YAML, and record
  in `runs/<tag>/`.
- **Field units (bmcam001/002, `main`, commands off) are untouched** until a release
  decision. The first release candidate point is after S5 (console-complete). Moving a field
  unit then means the S2 migration plus a field update.

**Rollback.** Every stage deploys by SHA via `deploy_rc_runtime.sh`. Rollback = redeploy the
previous SHA. From S2 on, the v1 YAML is kept next to the v2 file (`camera_schedule.v1.yaml`),
and restoring it is part of the rollback command written into each stage's `runs/<tag>/`.

## 9. Constraints carried from R1 into S4 (KICKOFF §5)

- One-shot keys are applied by the supervisor to a per-action copy of the config, so they can
  never reach the overlay or the state file.
- The START envelope carries the resolved `r/m/d` values. The backend records them per media.
- A `media` override on a still-configured unit needs the video config loaded. With one
  loader and one dict (S2) that is free.

## 10. Open items and rulings

| # | question | proposed default |
|---|---|---|
| O1 | Do any deployed units run the command daemon? | **RULED (Nick, 2026-09-24/25): reef units have commands off, and remote commands to bmcam001/002 are not supported.** Their command conventions may break. The one hard requirement: they keep sending data and the backend keeps ingesting it (§11). They get a full software update after the mote cache (TODO-BM-017) lands. |
| O2 | Delete the HEIC path now (C3)? | **RULED (Nick, 2026-09-24): delete now, in S1.** |
| O3 | per_boot: a `trg` heard in the listen tail fires this boot? | **RULED (Nick, 2026-09-25): yes, if it fits the boot's budget**; otherwise it stays armed for the next boot. |
| O4 | Rewrite git history to drop the committed imagery? | **RULED (Nick, 2026-09-25): not now.** |
| O5 | stay_on: send pending heals when idle, with no trigger? | **RULED (Nick, 2026-09-25): yes**, after 10 min idle (S3b). |
| O6 | Backend-related exceptions to "everything settable" | **RULED (Nick, 2026-09-25): lock `uplink.network_type` and `uplink.chunk_chars` for customers; Nereus can force a change with a signed service command** (§6.3). `uplink.media_key.*` stays settable. |
| O7 | Short names for byte-tight commands: keep the KICKOFF §5 letters (`r m d f b e`)? | **RULED (Nick, 2026-09-25): yes.** |
| O8 | Until nvd parses `<CF>`, `get` answers are only in raw Sofar data | acceptable until S6 |
| O9 | Hotspot password? | **RULED (Nick, 2026-09-24): no password by default** (as today, D-S16-6); revisit if customers ask. |
| O10 | Hotspot on every boot? | **RULED (Nick, 2026-09-24): a setting, `network.ap_boot_min`, default 5.** The AP comes up at boot and turns off if nobody logs in within 5 min; 0 disables it. Energy measured in S7. |

## 11. Guardrails (in addition to KICKOFF §6)

- **bmcam001/002 data path must keep working** (Nick, 2026-09-25). They run `main` with
  commands off and cannot be updated until the mote cache lands. Every backend change (S6) is
  additive: new fields optional, and the old START/END/chunk/`<WS>` wire still parses. S6 adds
  an nvd regression test that ingests a wire sample recorded from `main`'s code (the S1 golden
  harness run against `main`) and checks the rows it produces are unchanged.
- Field units run `main`. All of this is bench-only on `development` until a release decision.
- Before touching a unit: check processes and crontab, back up, record in `runs/<tag>/`.
- The legacy runtime stays selectable until S5 passes on both rigs.
- A `set` that fails validation changes nothing; a state-file write failure means no ok ack (D15).
- Every stage records boot-to-transmit time and peak RSS, so the cleanup's gains are measured,
  not assumed.

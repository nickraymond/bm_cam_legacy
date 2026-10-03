# R1.1 — camera `post` self-report command (SPEC, draft r2)

Status: **draft r2** (2026-10-03: terse console per Nick's review of r1), docs only, nothing built. Base: bm `development` 32f7c9f (commands v9, registry v7). Labels: **MVP** = R1.1 · **Later** = a later sprint. ASSUMPTION = not measured yet, HIL checks it.

## 1. Purpose

One request, `{"id":N,"c":"post"}`, and the camera reports its current setup and the health it measures itself, like the Spotter's `post` (style: `runs/sprint13_bench_20260801/spotter_post_capture.txt`). The unit only reads files and runs one `vcgencmd get_throttled`. Nobody has to parse logs.

| | answers | cellular | when |
|---|---|---|---|
| `ping` | "alive" + config hash | ack only | on request |
| `get` | config values + where each came from (`<CF>`, ≤ 3 parts) | ack + `<CF>` | on request |
| `<WS>` heartbeat | wake action, window, `ct`, `sha`, `cfg`, `up` | 1 msg | every wake, unasked |
| **`post`** | **setup fit check + health: thermal, power, SD, RAM/CMA, camera, clock, budget, heals, commands, errors** | ack + 1 `<PS>` | on request |

## 2. Transport

| item | rule (existing patterns reused) |
|---|---|
| listening | Only while awake: bus 10 min/h in production; `hld` extends it. A `post` sent while the bus is off follows today's delivery rules (Sprint23, mote buffer #76), unchanged here |
| when answered | Like every verb: on the main thread at the next decision point or listen pass, never inside the burst (`command_v9.py:7-8`; mid-burst payloads wait in `command_inbox`). A `post` sent during the burst is answered after it, so the since-boot bits include the burst |
| request | `{"id":N,"c":"post"}`; optional `"to":"con"` = console only. Fields `{"to","sig"}`, as `ping` |
| console reply | Always, in full: the `OK` line + the grouped block in §3.1, each line ≤ 240 chars (`MAX_CONSOLE_CHARS`), 0.05 s apart |
| cellular reply | Remote/service id ranges only, not with `"to":"con"`: the usual slim ack `{"id","ok","h"}` + **one** `<PS v=1 …>` line ≤ **280 B** (`MAX_CF_BYTES`, same cap as `<WS>`/`<CF>`), queued on the 1.0 s ack pacer |
| duplicate | Same as `get`: the console always answers; the cellular ack `d:1` + a fresh `<PS … d=1>` at most once per id, never within 10 min (G9). The mote's 60 s replay therefore costs nothing |
| errors | Existing codes only: `id`, `old`, `auth`, `key` (extra field, `command_wire.py:284-286`), `cmd` (legacy/v8 unit or older v9 build) |

### 3.1 Console mock (human form: `label: value`, like the Spotter `post`)

Each value is one word or a short phrase. Details show only when something is not OK. Every line carries the usual `[host] ` prefix (`command_wire.console_line`), and the Spotter adds its own `<UTC> <uptime> <node id>,` in front.

```text
[bmcam003] OK id=42 post cfg=f7c9194f
[bmcam003] post
[bmcam003] setup.media:     still
[bmcam003] setup.output:    transmit
[bmcam003] setup.window:    12:00-15:00 open
[bmcam003] setup.still:     1600x900 -> 1000x562 q90
[bmcam003] setup.cap:       195 msgs
[bmcam003] setup.camera:    auto
[bmcam003] setup.pending:   none
[bmcam003] runtime:         2c819ad1 v7
[bmcam003] thermal.cpu:     52C (peak 61C)
[bmcam003] power.throttle:  OK
[bmcam003] storage.sd:      57% (12.4 GB free)
[bmcam003] memory:          OK
[bmcam003] camera:          imx708 OK
[bmcam003] clock:           OK
[bmcam003] wake.budget:     412/480 s
[bmcam003] heals:           12 pending
[bmcam003] errors:          0
[bmcam003] post end
```

| line (others: value only, as in the mock) | shown when OK | shown when not OK (status word first) |
|---|---|---|
| setup.output | `transmit` / `save_local` | `save_local, reverts unless cfm` |
| setup.camera | `auto` | only the non-auto controls: `ev -1, focus 0.5` |
| setup.pending | `none` | `cfm o`, `staged halt`, `trg 2` |
| thermal.cpu | `52C (peak 61C)` | — (the soft-limit bit shows under power.throttle) |
| power.throttle | `OK` | `WARN undervolt`, `WARN throttled`, `WARN earlier: undervolt` (bits named only when set) |
| storage.sd | `57% (12.4 GB free)` | `FAIL full` (guard cannot meet the limits), `FAIL read-only` |
| memory | `OK` | `WARN CMA 12 MB` / `WARN 40 MB free` (thresholds: ASSUMPTION, set at HIL) |
| camera | `imx708 OK` | `FAIL rc 1`, `WARN 2 retries`, `n/a` (no capture yet) |
| clock | `OK` | `WARN skew 7 s`, `FAIL no sync` (\|skew\| > 5 s = ASSUMPTION) |
| errors | `0` | `WARN 3 (uart 2, rejected 1)` |

Cellular `<PS>` for the same state (machine form, raw values; 218 B typical, **260 B** worst case with every field at its widest):

```text
<PS v=1 id=1000123 h=a41c09e2 t=1790000000 up=412 bc=137 sha=2c819ad1 rv=7 ct=52.3 cp=61.0 th=0 sd=29.1,12.4,57.4 sg=ok ro=0 ma=212 cf=98 cam=imx708 crc=0 sk=-0.4 ts=391 wu=412 hp=12 hw=2 pa=0 lc=1000121:ok er=0,0,0,0>
```

The console status words follow the fixed rules above. The backend and the rig apply their own thresholds to the raw `<PS>` values, so those thresholds can change without a deploy.

## 3. Fields

**Setup (console only, MVP, first in the block).** A fit check before anyone sends a `set`. Every value comes from the effective config (`Dispatcher.effective()`, `command_v9.py:104`). Cellular sends only `h`, because the backend already maps the hash to its config snapshot (`<CF>` after a set).

| line | keys shown | why an operator needs it before a `set` |
|---|---|---|
| media | `mode.media` | still vs video decides which caps and sizes apply |
| output | `mode.output` | transmit vs save_local |
| window | `schedule.window.*`, `schedule.timezone`, inside now? | a scheduled capture outside the window will not run |
| still | `still.crop` WxH (native px) → output WxH (`still.output_width`; height follows the crop), quality **used** for the last still (START `q`); production ladder 90→9 (`camera_schedule.yaml:86`), not the q_max fallback 15/13/11/9 | ROI vs output size vs quality actually reached; before the first still the console shows the span `q90-9` |
| video | `video.send.size/fps/duration_s` | clip cost; no-upscale fit |
| cap | `still.message_cap` (195) or `video.send.message_cap` (126), the media in play | the cellular cost ceiling |
| camera | `camera.exposure.ev`, `camera.focus.mode`, `camera.white_balance.mode`, `camera.controls_enabled` | a control that is set while `controls_enabled` is off does nothing |
| pending | staged/guarded keys awaiting `cfm`, `pending_trigger_v9` | a `set` on top of an unconfirmed change |

**Health (MVP unless marked).** Since per_boot units reboot every wake, "since boot" means "this wake".

| key | field | unit | source on the Pi | why it matters | tier |
|---|---|---|---|---|---|
| `t` `up` `bc` | Pi UTC, uptime, boot count | epoch s, s, n | `time.time()`, `/proc/uptime`, `V9State.boot_counter` (counted boots, `command_state_v9.py:17`) | unexpected reboots; with the receive time, Pi clock vs backend | MVP |
| `sha` `rv` `h` | runtime git sha, registry version, config hash | text | `rc_telemetry.get_software_sha()` (`software_sha.txt`), `config_registry.REGISTRY_VERSION`, `current_hash()` | which code and config is really running | MVP |
| `ct` `cp` | CPU temp now, peak this wake | °C | `/sys/class/thermal/thermal_zone0/temp` ÷ 1000 (ASSUMPTION: the same sensor as `vcgencmd measure_temp` in `<WS>`); peak = max of the samples taken at every decision point, `<WS>`/START/END, and `post` | sealed housing and thermal throttling | MVP |
| `th` | throttled flags | hex | `vcgencmd get_throttled`. Bits 0-3 now: under-voltage, freq capped, throttled, soft temp limit. Bits 16-19: the same, since boot | brown-outs on the 24 V bus, heat | MVP |
| `sd` | SD total, free, % used | GB, GB, % | `rc_telemetry.collect_storage_health()` (`shutil.disk_usage("/")`) | a full SD stops captures | MVP |
| `sg` | storage guard state, last this boot | ok/prune/full/dry/na | `rc_still_storage.ensure_room()` / `video_ring.ensure_room()` result (`full`, `pruned`, `dry_run`), held by the supervisor | `full` = save_local refuses captures | MVP |
| `ro` | root fs read-only (proposed) | 0/1 | `/proc/mounts`, `/` options | SD corruption after hard bus cuts shows up first as a read-only remount | MVP |
| `ma` `cf` | MemAvailable, CmaFree | MB | `/proc/meminfo` (console also prints CmaTotal) | Pi Zero 2W RAM and CMA limits cause capture failures | MVP |
| `cam` `crc` | sensor model, last capture rc | text, int | the last native's `.stderr.log` (ASSUMPTION: rpicam names the sensor there) and the supervisor's capture result (`rc_capture`); `na` before the first capture | a missing or failing camera | MVP |
| `sk` `ts` | Pi − Spotter UTC at the boot sync, age of that sync | s, s | the supervisor's `spotter_time_sync.read_spotter_utc()` result, taken before the clock step; `na` on timeout/fallback | the clock-skew gotcha; image timestamps | MVP |
| `wu` | budget used this wake (console: `used/budget`, 480 s = `still.budget_min` 8 on bench) | s | `rc_time_budget.TimeBudget.elapsed_s` | how close a wake comes to the bus-off cut | MVP |
| `hp` `hw` `pa` | pending heal chunks, fewest wakes left, acks still queued | n | `pending_heals` (sum of `n`, min `wakes_left`), `daemon.pending_acks` | heals that are waiting; acks lost at halt | MVP |
| `qn` `qb` `qa` | unsent media count, bytes, oldest age | n, kB, h | **No queue exists today.** Each wake sends its own capture; missing chunks come back only by `rsd`. Add these with the transmit-window sprint | backlog | Later |
| `lc` | newest command id before this post + its result | id:ok/e | `V9State.result_cache` (last 256 answers); no console line (every answer is already printed there) | did my last command land? | MVP |
| `er` | capture retries, encode fails, uart read errors, cmds rejected (since boot) | n,n,n,n | capture attempts − 1 (`rc_capture.py:177`); **new** encode-fail counter in the action summary; `daemon.stats` read_errors, rejected + unackable | hidden instability | MVP |
| — | Spotter queue-full drops | — | **Not visible to the Pi**: the Spotter drops silently (`command_daemon.py:195-197`). The backend infers them from missing acks | — | n/a |
| `lw` `hc` | previous-wake snapshot (budget used, peak temp, throttle bits, errors; Nick: Later), clean halt vs hard bus cut (needs a marker in `rc_power_halt`); live camera probe (`rpicam-hello --list-cameras`, start-up time and memory unmeasured), Wi-Fi/AP state, per-directory sizes | | needs a persisted `health_state.json` / halt-path change | trends; hard cuts corrupt the SD | Later |

## 4. Cost

| transport | messages | bytes | Pi time |
|---|---|---|---|
| console (console, heal or conductor id, or `"to":"con"`) | 0 cellular; ~20 console lines | ~0.8 kB on USB | gather target **< 2 s** (ASSUMPTION ~0.2 s: 1 `vcgencmd`, `/proc` + `/sys` reads, `disk_usage`, 1 small log read; measured in H1). No camera, no libcamera |
| cellular (remote/service id) | **2**: ack (~36 B) + 1 `<PS>` (218 B typical, ≤ 260 B worst, cap 280 B) | ≤ ~296 B | same; plus 1.0 s ack pacing |

A unit test pins the worst-case `<PS>` at ≤ 280 B. If a later field would push past 280 B, one is dropped first; the `<PS>` is never split.

## 5. Wire contract, backend, rig

| area | work | tier |
|---|---|---|
| wire (needs **Nick's approval**) | Additive: `command_wire.VERBS` + `FIELDS["post"]={"to"}`, `_verb_post` in `command_v9.py`, help text, regenerated `docs/bmcam_command_reference.md` (the stale-doc test enforces this), new cellular tag `<PS v=1>`. Older units answer `e:"cmd"`. `post` changes no config (`h` unchanged); a remote id moves the high-water as usual | MVP |
| unit | `rc_health.py` (gather, each collector returns `na` on failure and never raises), the supervisor keeps `cp`/`sg`/`crc`/`sk`/the used `q`/error counters in memory; console status-word rules (§3.1) | MVP |
| backend (nvd) | Parse `<PS>` into a `camera_health` row (node, cmd id, received_at, fields JSON; migration DDL only). Dashboard: latest post per camera, decoded `th`, thresholds (ASSUMPTION, set at HIL): any `th` bit = WARN, under-voltage / `sg=full` / `ro=1` / `crc≠0` = FAIL, \|`sk`\| > 5 s = WARN. Logs page: raw + decoded rows. "post" button on the command panel (remote range) | MVP |
| rig (nereus000 spotter-monitor) | Each wake at ~:03, `bm pub bmcam/cmd {"id":<console range>,"c":"post"} 1 1` on each bench Spotter, parse the block between `post` and `post end`, show the latest per unit + a CSV history. Console-range id = zero cellular. **Approved by Nick 2026-10-03 (bench rigs only)** | MVP |

## 6. Test plan

| # | test | PASS when |
|---|---|---|
| U1 | `tests/test_command_post.py`: decode (`post`, `to:con`, extra field → `e:key`), console vs cellular lanes per id range, duplicate (console always, cellular once after 10 min) | all green |
| U2 | every collector failing (no `vcgencmd`, unreadable meminfo, no capture yet) | `post` still answers, with `na` in those fields |
| U3 | worst-case `<PS>` and the `th` bit decode (0x50005, 0xe000f) | ≤ 280 B; bits named correctly |
| U4 | console rules of §3.1: each not-OK case, `q` before/after the first still, video vs still lines | one word/phrase per value; every line ≤ 240 chars |
| G1 | golden trace: a `post` scenario in `tests/golden/scenarios.py` (`vectors_v9`) with a fixed world | ack + `<PS>` + console bytes pinned |
| H1 | bmcam003, console via `hil/tools/hil_cmd.sh`, in the listen tail | full block; gather < 2 s (logged); values match ssh `vcgencmd get_throttled`, `/proc/meminfo`, `df`, the sha |
| H2 | `post` sent mid-burst | answered after the burst; burst chunk count same as baseline |
| H3 | remote-range `post` (cellular send: **needs Nick's OK**) | ack + 1 `<PS>` at Sofar, ≤ 280 B, decoded on staging |
| H4 | mote 60 s replay of H1 | console only, no second cellular copy |

## 7. Open questions for Nick

Ruled 2026-10-03: nereus000 sends `post` to the bench rigs every wake (bench only). The previous-wake snapshot is **Later** (so `lw` moved to Later as well).

1. Approve the wire change: new verb `post` + new cellular tag `<PS>`? (`<CF>` is not reused, because it feeds the backend's hash → config snapshot.)
2. On request only (the default), or also sent unasked, e.g. once a day from field units (+2 cellular msgs each time)?

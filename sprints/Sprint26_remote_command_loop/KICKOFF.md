# Sprint26 — remote command loop on an always-on bench (kickoff)

> **Superseded in part (2026-09-25):** §3 stages R1–R2 and decision D1 are replaced by
> `DESIGN_supervisor.md` (one supervisor, config v2, commands v9; stages S1–S7). §2, D2–D5,
> R3–R5 and §6 still stand; §5's trigger controls become one-shot `kv` on `trg` (S4).

Written 2026-09-24 from the Sprint25 close-out; decisions §4 taken with Nick the same day.
Module status was checked against `origin/development` (bm_cam_legacy) and `staging`
(nereus-vision-dev); "gap" means the code does not exist yet, not that it is untested.

## 0. Where Sprint25 ended (facts)

| piece | state | evidence |
|---|---|---|
| Device S0–S5 (keyed media, `.sent` record, `rsd`, pending list, `<HL>`) | merged to `development` (#70, #71) | ladders 2+3 PASS, 4/4 real heals byte-exact |
| Backend S0–S2b (keyed ingest, chunk map, heal-candidates, heal-commands, `<HL>` ingest) | live on staging | nvd #49/#50/#52/#56/#57 |
| Receive-time `still_arriving` + `media.last_received_at`; cron `*/5`, `--hours 3` | live, cadence verified 315/299 s | nvd #58, #60; BUGS.md B19 |
| logs.html: heal_request / heal_status rows, type filter, image vs video kinds, probe reads keyed video | live on staging | nvd #62, #63, #64; B20 repaired |
| nereus000: `spotter-monitor.service` (both consoles), `bm-heal-driver.service` (console `rsd` at bus-on; `still_arriving` no longer burns a wake) | running | driver fdd9d3f rolled 21:27Z |
| Sofar downlink sender | Mac CLI only: `tools/sofar_send_command.py` (POST user-rest/devices/{id}/command, 1 req/min/Spotter guard, send log) | Sprint10 §7 |
| Remote delivery reality | a command reaches the Spotter ~72 s after its hourly LEGACY report; held commands replay 10 s after bus power (lost if the Pi is not up) until the mote-side cache (Matt, TODO-BM-017) | Sprint23, SPEC_resend_heal §6 |
| Backend M5 auto-send | **cut from MVP, not written**: no Sofar command-API client in the backend | SPEC_resend_heal §3 M5, Q6 |

## 1. The plan and its gaps

1. eboxes always on → config only (`bridgePowerControllerEnabled 0`, done before on both rigs).
2. cameras never halt → `power_halt.enabled: false` **is not enough**: the runtime is one cron
   `@reboot` cycle + a bounded `post_transmit_listen_s` tail, then the process exits; the Pi idles
   **with no listener**. → **GAP A: always-on runtime mode.**
3. remote command from nereus000 → CLI exists; needs the token on nereus000 and a timestamped
   send log. → **GAP B (small).**
4. image then video per command → no verb picks the media type or its parameters.
   → **GAP C: tables v9, `trg` with one-shot keys** (§5).
5–6. backend issues heals → **GAP D: backend M5 auto-send** (Sofar command-API client, flag,
   guards, events).

Verdict: ready to start, not ready to run. Steps 1 and 3 today; 2 and 4 need A + C; 5–6 need D.

## 2. Loop design (decided)

Two lanes, deliberately split so a failure is compartmentalised:

- **Triggers over the console** (nereus000 → `bm pub bmcam/cmd …`, seconds). This keeps the
  cycle rate at the delivery rate (13–25 min measured), 3–4 cycles/h.
- **Heals over Sofar** (backend → Sofar command API → Spotter mailbox → BM bus). Honest test of
  the remote path end to end, at Sofar's real latency (hourly report until the mote cache). A
  heal that lands while the camera is mid-burst is queued by the device's pending list — that
  is a feature of the test, not a hazard.

One cycle: conductor issues `trg` on the console → media lands at the backend (complete or
partial) → conductor issues the **next trigger immediately** → if partial, the backend issues
the heal via Sofar whenever its walk runs; the heal completes some later cycle; logs.html shows
request, camera answer and completion. The loop never waits on Sofar.

## 3. Stages (each gated, each its own PR)

| stage | repo | deliverable | gate |
|---|---|---|---|
| R0 bench prep | hardware/config | both eboxes always on; bmcam003/004 `power_halt.enabled: false` (or `hlt 3`); confirm the Pi stays up and idle after one cycle | ssh reachable 30 min after a cycle; console shows no bus power cycling |
| R1 always-on mode + conductor skeleton (A) | bm_cam_legacy | YAML `bm_commands.mode: always_on`: daemon persists across cycles, `trg` runs a capture/transmit cycle in-process, pending `rsd` heals serviced on the same loop, `<WS>` heartbeat every 5 min, clean SIGTERM; stock cycle byte-identical when off (D14). nereus000 `bm-bench-conductor.service`: trigger → poll backend for the media row → next trigger; the heal driver's logic folds in as one optional step | console `trg 2` twice in a row → two media rows at the backend, no reboot; conductor runs 4 cycles unattended |
| R2 tables v9 (C) | bm_cam_legacy | `trg` one-shot keys per §5; help/cfg text; command reference; tests | conductor alternates `v:5` / `v:6` with `r`/`d`/`m` → one still, one clip per pair, both complete, YAML unchanged after |
| R3 Sofar lane for heals (B) | bm_cam_legacy (nereus000) | token in `~/.config/nereus/`; `sofar_send_command.py` runnable on nereus000; `tools/remote_latency_report.py` joining send log × console × backend → CSV `t_post, t_console, t_ack, t_media_complete`; conductor's heal step switched to "send via Sofar" as the interim before R4 | 10 heals via Sofar over ~10 h: delivery %, latency per hop p50/p95 |
| R4 backend auto-send (D) | nereus-vision-dev | `BM_HEAL_AUTOSEND=1`; Sofar command client using the gateway's `token_env_var`; scheduled candidate walk in the cron tick; 1 req/min/Spotter guard; `BM_HEAL_AUTOSEND_MAX_PER_DAY` (bench 96, production default 24); `heal_requested` event `sent_via=sofar`; logs.html shows it; conductor's heal step off | driver/conductor heal step stopped; a real loss (queue stall) healed end-to-end by the backend alone; `[heal]` line shows `received_age_s ≥ 600`; no double-issue |
| R5 24-h loop | both | conductor + backend auto-send for 24 h on both rigs; RESULTS.md: cycles, delivery %, latency per hop, heals issued/needed/redundant, stills vs clips | 0 clips lost; redundant heals 0; every command visible on logs.html |

Order: R1 → R2 → R3 → R4 → R5. R3's latency numbers size R4's cap and guard.

## 4. Decisions (Nick, 2026-09-24)

| # | decision |
|---|---|
| D1 | nereus000 drives; nobody in the loop. Mode is unit YAML (`bm_commands.mode`), not a CLI flag; the conductor is a nereus000 service. |
| D2 | Triggers carry optional one-shot keys (§5); persistent settings verbs stay for the product's "set and forget" controls. |
| D3 | Heartbeat 5 min in always-on mode (= one poll tick). |
| D4 | Console lane for triggers (max cycles), Sofar lane for heals (honest remote test). Backend auto-send cap is env, bench 96/day. |
| D5 | No waiting for the mote cache. Next trigger fires when the backend has the media; the heal is fire-and-forget on the Sofar lane. |

## 5. Trigger controls — tables v9 spec (what the product can control)

Command shape (≤ 270 B incl. newline, wire-checked; typical ~70 B):

    {"id":N,"c":"trg","v":<0-6>,"r":<roi>,"m":<msgs>,"d":<sec>,"f":<foc>,"b":<awb>,"e":<exp>}

Every key after `v` is optional and **one-shot**: applies to the cycle this trigger fires,
then the unit is back on YAML. Missing key = YAML value. Unknown key = ignored (forward-
compatible). Validation per key against the same tables the settings verbs use, so a garbled
int can at worst pick a vetted preset. `v`: 0 cancel · 1 capture SD only · 2 capture+send
(YAML media type) · 3 reef reference · 4 reference card · **5 still capture+send** ·
**6 video capture+send**.

| dashboard control (product) | today's device path | trigger key | range / presets | still | video | status |
|---|---|---|---|---|---|---|
| crop / zoom (ROI) | `roi` setting → ROI_TABLE (7 concentric centred presets, native coords) | `r` | 0–6 | ✓ | ✓ (recording geometry) | exists |
| output resolution (stills) | `progressive_jpeg.output_width` (1000) / `image.resolution_key` | — | YAML | ✓ | – | exists, **not commandable** — add `o` preset table if wanted (1000 / 1600 / 2688 width) |
| send geometry (video) | `video_tx.output` (480x270), `fps` 10 | — | YAML | – | ✓ | exists, not commandable; fps/geometry change the x264 ladder — keep YAML this sprint |
| still quality | progressive ladder `90…9`, chosen by budget | via `m` | — | ✓ | – | exists; quality follows the message budget |
| max messages | `cap` setting (CAP_TABLE) / `video_tx.message_cap` | `m` | 8–300 (int) | ✓ | ✓ | exists |
| video duration | `video_tx.duration_s` | `d` | 2–10 s (5 s is the only ladder-validated value; others are allowed and reported) | – | ✓ | exists |
| video bitrate | derived: `≈ m × 384 B × 0.75 × 8 / d` (2-pass x264 fits the budget) | via `m`,`d` | — | – | ✓ | exists; **not a direct knob by design** — the wire budget is the truth |
| focus | `foc` setting → FOC_TABLE (auto + 5 manual distances); applied to video too (`_resolve_controls`) | `f` | 0–5 | ✓ | ✓ | exists |
| white balance | `awb` setting → AWB_TABLE (auto / daylight / cloudy) | `b` | 0–2 | ✓ | ✓ | exists |
| exposure bias | `exp` setting → EXP_TABLE (−2…+2 EV) | `e` | 0–6 | ✓ | ✓ | exists |
| shutter speed / gain | `camera_controls.exposure.mode: auto` only; `ExposureTime`/`AnalogueGain` **not plumbed** | `s`, `g` (reserved) | — | ✓ | ✓ | **not built**: needs a table + libcamera plumbing on both capture paths; the END metadata already reports requested vs actual (rsh/rag) so verification is ready |
| colour correction | backend Sprint05 pipeline (METHOD_REGISTRY); nothing device-side | none | — | ✓ | ✓ | **backend control, not a device command**: a per-media / per-device processing option on the dashboard (processing worker), no wire traffic |
| image source (live / reference) | `src` setting; `trg 3/4` one-shot | `v` 3/4 | — | ✓ | – | exists |
| capture SD-only | `trg 1` | `v` 1 | — | ✓ | ✓ | exists |
| transmit pacing | `txd` setting | — | persistent only | ✓ | ✓ | exists; not per-trigger (queue lever, not a media property) |
| cycle budget | `win` setting | — | persistent only | ✓ | ✓ | exists |

Rules for R2: (1) one-shot keys never write `bm_command_state.json`; (2) the ack echoes the
resolved one-shot values so the conductor can log what the unit actually used; (3) the START
envelope carries the resolved `r/m/d` (it already carries `fmt`, `fps`, `length`) so the backend
records them per media without a new message; (4) stills use the progressive ladder as today —
`m` caps it, nothing else changes; (5) `d` outside the validated 5 s is allowed on the bench and
flagged in the ack (`d=8!`) so a customer-facing default can be chosen from data.

Reserved for a later table bump (not this sprint): `o` output-width preset (stills), `s` shutter
preset, `g` gain preset, video fps/geometry presets. Colour correction is a backend option.

## 6. Guardrails carried over

- Field units run `main`; everything here is bench (`development` + staging).
- Never leave a unit with `hlt 1` on constant power or `hlt 3` on battery.
- Sofar command API: 1 successful request/min/Spotter; a cooldown rejects ALL requests — every
  sender goes through the rate-limit guard and the send log.
- Two heal senders must never be live at once (conductor step vs backend M5).
- Backend: DDL-only migrations; test-push to staging then reset; PR opened AFTER the reset.
- `runs/<tag>/` with manifest, logs, CSV, RESULTS.md for every stage.

## 7. Kickoff prompt (next session)

    Sprint26 — remote command loop on an always-on bench. Repo bm_cam_legacy, branch from
    development (never main). Read sprints/Sprint26_remote_command_loop/KICKOFF.md first (§2
    loop design, §4 decisions, §5 trigger spec are settled — do not reopen them), then
    CLAUDE.md, docs/bmcam_command_reference.md, BM_Devel_Pi/command_daemon.py,
    command_tables.py, rc_command_hooks.py, main_pi_camera.py, rc_heal.py, rc_video_tx.py,
    tools/bm_heal_driver.py.

    This session = stage R1: (a) unit side — YAML `bm_commands.mode: always_on`: the command
    daemon persists across cycles, a `trg` runs a capture/transmit cycle in-process, pending
    rsd heals are serviced on the same loop, a <WS> heartbeat goes out every 5 min, SIGTERM
    stops cleanly; with the mode off the stock cycle is byte-identical (D14). (b) nereus000
    side — `tools/bm_bench_conductor.py` (+ systemd unit): for each rig, publish a trigger on
    the console, poll the backend until the media row is complete or its heal budget is spent,
    then publish the next trigger; the heal driver's logic becomes an optional step of the
    conductor (default on until R4). Everything appended to <log-root>/conductor/events.jsonl.

    Inspect → plan → Nick's approval before code. Tests: extend the FakeDaemon harness in
    tests/test_s5_heal.py; conductor tests with a fake backend like tests/test_s5_heal_driver.py.
    Bench gate: console `bm pub bmcam/cmd {"id":N,"c":"trg","v":2} 1 1` twice → two media rows
    at the backend with no reboot; conductor runs 4 unattended cycles on one rig. Hardware:
    bmcam003/bmcam004 via nereus000 (pi@nereus000.local; spotter-monitor + bm-heal-driver
    services). Check crontab and processes before touching a unit, back up before editing,
    record every change in runs/<tag>/. Do not start R2 (tables v9) in this session; leave a
    note of anything in R1 that constrains §5.

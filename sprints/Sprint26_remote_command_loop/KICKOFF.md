# Sprint26 — remote command loop on an always-on bench (kickoff)

Written 2026-09-24 from the Sprint25 close-out. Status of every module named here was
checked against `origin/development` (bm_cam_legacy) and `staging` (nereus-vision-dev) on
that date; "gap" means the code does not exist yet, not that it is untested.

## 0. Where Sprint25 ended (facts)

| piece | state | evidence |
|---|---|---|
| Device S0–S5 (keyed media, `.sent` record, `rsd`, pending list, `<HL>`) | merged to `development` (#70, #71) | ladders 2+3 PASS, 4/4 real heals byte-exact |
| Backend S0–S2b (keyed ingest, chunk map, heal-candidates, heal-commands, `<HL>` ingest) | live on staging | nvd #49/#50/#52/#56/#57 |
| Receive-time `still_arriving` + `media.last_received_at`; cron `*/5`, `--hours 3` | live on staging, cadence verified 315/299 s | nvd #58, #60; BUGS.md B19 |
| logs.html: heal_request / heal_status rows, type filter, image vs video kinds, probe reads keyed video | live on staging | nvd #62, #63, #64; B20 data repaired |
| nereus000: `spotter-monitor.service` (both consoles), `bm-heal-driver.service` (console `rsd` on bus-on; `still_arriving` no longer burns a wake) | running | driver fdd9d3f rolled 21:27Z |
| Sofar downlink sender | Mac CLI only: `tools/sofar_send_command.py` (POST user-rest/devices/{id}/command, 1 req/min/Spotter guard, send log) | Sprint10 §7 |
| Remote delivery reality | command reaches the Spotter ~72 s after its hourly LEGACY report; held commands replay 10 s after bus power (lost if the Pi is not up) until the mote-side cache (Matt, TODO-BM-017) | Sprint23, SPEC_resend_heal §6 |
| Backend M5 auto-send | **cut from MVP, not written**: no Sofar command-API client in the backend | SPEC_resend_heal §3 M5, Q6 |

## 1. Nick's plan (2026-09-24) and what it needs

1. eboxes always on → config only (`bridgePowerControllerEnabled 0`, done before on both rigs).
2. cameras never halt, stay up while powered → `hlt 3` / `power_halt.enabled: false` **is not enough**:
   the runtime is one cron `@reboot` cycle + a bounded `post_transmit_listen_s` tail, then the
   process exits. With no halt and no reboot the Pi idles **with no listener** and never cycles
   again. → **GAP A: an always-on runtime mode** (daemon owns the UART continuously; a `trg`
   runs a capture/transmit cycle in-process; pending heals are serviced; a `<WS>` heartbeat
   every N min so the backend's liveness/sealing still work).
3. send a remote command from the Sofar API, ideally from nereus000 → the CLI exists; it needs
   the token on nereus000 and a send log that timestamps the POST so latency can be measured.
   → **GAP B (small): sender on nereus000 + latency correlation** (POST time → console `bm pub`
   seen by the monitor → device ack `<T>` → media at backend).
4. cycle image, then video, per command → `trg 2` (capture + send) uses the YAML media type;
   there is no verb that picks still vs video. → **GAP C: tables v9** — `trg 5` = still
   capture+send, `trg 6` = video capture+send (one-shot, never touches the persisted YAML), or a
   `med` setting. Prefer the trg form: one-shot, no state-file doctrine change.
   Heals meanwhile stay on the nereus000 console driver (works today).
5–6. backend issues heal commands itself → **GAP D: backend M5 auto-send** behind
   `BM_HEAL_AUTOSEND=1`: scheduled heal-candidate walk (the endpoint exists), per-gateway token
   (`token_env_var`), Sofar command-API client with the 1 req/min/Spotter guard, one pending
   command per device, N/day cap, `heal_requested` event + logs.html row (the endpoint/UI from
   #62 already shows requests; add the `sent_via=sofar` field). The nereus000 driver must be
   stopped (or set read-only) while the backend drives, or both will issue commands.

Verdict: **ready to start, not ready to run.** Steps 1 and 3 can begin today; 2 and 4 need A + C
on the device; 5–6 need D on the backend. Sofar delivery latency will be dominated by the
hourly report until the mote cache lands — that is the measurement, not a blocker.

## 2. Stages (each gated, each with its own PR)

| stage | repo | deliverable | gate |
|---|---|---|---|
| R0 bench prep | hardware/config | both eboxes always on; bmcam003/004 `power_halt.enabled: false`; cron `@reboot` kept; confirm the Pi stays up and idle after one cycle | ssh reachable 30 min after a cycle; console shows no bus power cycling |
| R1 always-on mode (GAP A) | bm_cam_legacy | `bm_commands.mode: always_on` (or `--command-loop`): daemon persists across cycles; `trg` runs a cycle in-process; heals serviced on the same loop; `<WS>` every `heartbeat_min`; clean stop on SIGTERM; tests with the FakeDaemon harness | bench: `bm pub bmcam/cmd {"id":N,"c":"trg","v":2}` on the console → media at backend within one cycle, twice in a row, no reboot |
| R2 tables v9 (GAP C) | bm_cam_legacy | `trg 5` still / `trg 6` video (one-shot, bypass window, YAML untouched); help/cfg text; tests | console `trg 5` then `trg 6` → one still, one clip, each complete at backend |
| R3 sender + latency (GAP B) | bm_cam_legacy (nereus000) | `sofar_send_command.py` runnable on nereus000 with the token in `~/.config/nereus/`; `tools/remote_latency_report.py` joining send log × console × backend into a CSV: `t_post, t_console, t_ack, t_media_complete` | 10 remote `trg` commands alternating 5/6 over ~5 h: 10/10 delivered; latency table with p50/p95 per hop |
| R4 backend M5 auto-send (GAP D) | nereus-vision-dev | `BM_HEAL_AUTOSEND` flag; Sofar command client; scheduled candidate walk in the cron tick; 1/min + N/day guards; `heal_requested` event with `sent_via=sofar`; logs.html shows it; driver parked | bench: driver stopped; a real loss (queue stall) is healed end-to-end by the backend alone; `[heal]` proof line shows `received_age_s ≥ 600` |
| R5 full loop | both | remote `trg 5/6` cycling + backend-driven heals for 24 h; RESULTS.md with delivery %, latency per hop, heals issued/needed/redundant | 0 clips lost; redundant heals 0; every command visible on logs.html |

Order matters: R1 before R2 (R2 is a table change on top of the loop), R3 can run in parallel
with R1/R2 on the Mac, R4 only after R3 has latency numbers (the N/day cap and the 1/min guard
are sized from them).

## 3. Guardrails carried over

- Field units run `main`; everything here is bench (`development` + staging).
- Never leave a unit with `hlt 1` on constant power or `hlt 3` on battery (stranding trade,
  command reference).
- Sofar command API: 1 successful request/min/Spotter, and a cooldown rejects ALL requests —
  every sender goes through the rate-limit guard and the send log, no exceptions.
- Two command sources must never be live at once (nereus000 driver vs backend M5).
- Backend: DDL-only migrations; test-push to staging then reset; PR opened AFTER the reset.
- `runs/<tag>/` with manifest, logs, CSV, RESULTS.md for every stage.

## 4. Open questions for Nick
1. R1 mode name and where it lives: YAML `bm_commands.mode` vs CLI flag on `main_pi_camera.py`?
2. `trg 5/6` (one-shot) vs a `med` setting (persistent) — recommendation: trg.
3. Heartbeat period in always-on mode (proposal 5 min = one poll tick).
4. N/day cap for backend auto-send (proposal 24, i.e. ≤ 1/h/device).
5. Does R4 wait for Matt's mote cache, or run with hourly-report delivery and measure?

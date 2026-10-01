# Sprint27 SPEC r1 — review record and consensus (2026-10-01)

Three independent read-only reviewers on SPEC r1:
A = camera / firmware correctness (ran probes in `.venv-dev` against 04a5b92),
B = backend design + operational safety (nvd `origin/staging` 715d3ef),
C = scope, simplicity, field risk.

Verdicts: A "not safe as written" (2 BLOCKER) · B "works; fix 2 MAJOR first" · C "not ready
until tiers are per key and the MVP is one-command changes" (2 BLOCKER).

## Verified by the author before acting

- A1 confirmed: a video unit whose effective render fails `load_video_config` /
  `load_video_tx_config` does `return 2` (`rc_progressive_jpeg.py:1607-1625`) before the daemon
  starts, and the wrapper treats 2 as done (`rc_run_capture_cycle.sh` header). A bad remote video
  `set` is acked ok today and bricks the next start until SSH.
- `supervisor_config.resolve` already DROPS overlay keys named by an `effective`-scope violation
  (G3, `supervisor_config.py:75-117`). So a camera rule in `config_validate` fixes both the set
  answer (`e:xk`) AND boot recovery for an already-stored bad overlay.

## Consensus fix list (r2)

| # | finding (who) | decision in r2 |
|---|---|---|
| 1 | Bad geometry / send size bricks a video unit (A1, A3); `resolve_geometry` raises TypeError for crop-xor-output without framing (A2) | **Camera rule mandatory** before any remote video control: pure in-memory check in `config_validate` effective+strict scope, catches **any** exception, mirrors `rc_video_tx` (even, ≥16) and the clip no-upscale (send ≤ record output); names `mode.media` and the keys involved; stdout suppressed (A10). Pinned by a test that the rule agrees with the real v1 loaders on the rendered config. Recovery comes from the existing G3 boot drop. |
| 2 | Tiers by section expose risky keys (A6, C1, C2, C3) | **Explicit per-key allowlist**; anything not listed is `blocked`. Only `control` is writable in the MVP; `engineering` is read-only (C2). Blocked: `power.* time.* network.* storage.* commands.* uplink.* video.ui.* video.logger.* camera.backend camera.native.width/height`. Engineering (read-only): `camera.image_processing.*` (free strings / unranged into argv, video has no fallback: A7), `camera.exposure.mode` (no flag), `still.save.quality` (save_local blocked). Per-key table generated into the catalog, summarised in the spec. |
| 3 | `mode.run=stay_on` on a scheduled bus = hard cuts (A6, C1) | Backend refuses `stay_on` unless `power.bus_always_on` is **reported** true. `mode.interval_s`/`heartbeat_s` writable, warn below 600 s. |
| 4 | Multi-part packing: too_big is possible (236 B / 286 B, A4); `e:old` strands part 1 if parts arrive out of order (C4, B9); a later change strands earlier unsent parts (B2); switch-last contradicts groups (A5) | **MVP = one change = one command** (one `set` or one `reset`, ≤ 234 B). Over the limit → `too_big` with the per-key byte costs so the UI can split. Packing groups, switch-last, multi-id allocation and `cas` are cut (multi-part = Next sprint, ack-gated). |
| 5 | Dead rows still count as desired / in flight; time expiry ≠ unsendable (B1) | `dead` = unsent row matching the send endpoint's `newer_sent` predicate (higher id sent, acked or observed). Dead rows are excluded from desired / in-flight and shown `expired`. No time-based expiry (C7). |
| 6 | Device-wide stranding (B2) | `changes` refuses `pending_unsent` while the device has any sendable unsent admin remote row (any key); `supersede:true` accepts that it will go dead. Per-key `in_flight` (sent, unanswered) refusal stays. |
| 7 | Cross-key rules on unknown inputs (C5, C6) | Backend **refuses only when every rule input is known** (reported or in the change); otherwise a warning and the unit decides. Geometry arithmetic ported once, checked against `selftest` vectors generated from `resolve_geometry` (A2). |
| 8 | `speaks_v9` too loose (B5, A12) | Writable only with a sighting **via ack or cf** (daemon-only) within 30 days, `commands.enabled` not reported false, `mode.media` not reported `video_logger`. |
| 9 | ui_status table gaps (B6) | add `needs_cfm` (staged, awaiting_cfm); `triggered` / `answered` / `observed` → `other` with the raw status. |
| 10 | Imports wider than listed; `current_gateway` returns paused gateways (B7) | Spec lists every import; `_load` copied locally; `send_enabled` uses `SC.sendable_gateway`. Owned files unchanged. |
| 11 | Catalog load at import could take the web app down on deploy (B8) | Lazy load; 503 `catalog_unavailable`; an import test. |
| 12 | Parity only proves sha (B11) | Backend test: catalog guarded/service sets == `command_status.GUARDED_REVERT(_WHEN)` and `command_outbound.SERVICE_KEYS`. |
| 13 | View token sees raw answers (B10) | View-token responses drop `sends[].response` and `ack_raw`. |
| 14 | Bench GUI ids invisible for 11–45 min (B4) | `changes` accepts `min_id`. |
| 15 | Typed values (A8) | INT keys must be JSON ints (5.0 refused, as `check_value`); comparison stays `values_equal`. |
| 16 | Wire details (A9, C3) | `still.quality_ladder` ≤ 4 rungs (wire `MAX_LIST`); `reset` takes catalog dotted keys only (no group prefixes), ≤ 4; `""` refused with "use reset". |
| 17 | Warnings table too big (C7) | Keep: requires-switch off, WB gains override mode, send fps > record fps, interval < 600 s. |
| 18 | #97 only a caveat (C10) | Ladder gate: #97 AND this PR's camera rule on the unit, else the ladder does not start. |
| 19 | Citations (A11) | fixed. |
| 20 | Scope question (C11) | Q6 added: a "capture now with these settings" (`trg` + one-shot kv) button? Not built in the MVP. |

## Round 2 (consensus check on this list, same three reviewers)

All three: **AGREE**, with these amendments (adopted in r2):

| from | amendment |
|---|---|
| A | Row 1 parity test must sweep every `video.*` key at its range edges plus odd / uppercase WxH against the REAL v1 loaders on the rendered config (not one config). "send ≤ record" uses the resolved (evened) output. Units holding a bad overlay recover only once the rule is deployed (row 18 gate). |
| A | Warning when `video.send.duration_s` ≠ 5 (only 5 s is ladder-validated). `video.record.encoder.*` stays `control` (rpicam enums). |
| B | Row 5: "unsent" = `sent_at IS NULL` (a `send_unknown` row has `sent_at` set). Row 6: a forgotten allocated row (sofar or console lane) blocks new changes until `supersede:true`; stated in the spec. |
| B | Row 8 replaced: writable = an ack/cf sighting at ANY age (the daemon exists) AND a ws/start sighting within 30 days (alive) AND `commands.enabled` not reported false AND `mode.media` not reported `video_logger`. A never-commanded v9 unit becomes eligible after one `ping` through the existing admin endpoint. |
| C | Message caps and budgets stay controls, but the backend clamps them: `still.message_cap` ≤ 300 (largest registry preset), `video.send.message_cap` ≤ 200 (**proposal**, no registry preset), `*.budget_min` ≤ 30 (**proposal**; largest preset 16); warn above the current production values (195 / 126 / 18). Higher = a deploy change. Nick decides (Q7). |

Consensus reached 2026-10-01; SPEC r2 implements rows 1–20 + these amendments.

## Code review of the implementation (r3, one independent reviewer, 2026-10-01)

No blocking bug; all tests reproduced green. Findings and what was done:

| # | finding | action |
|---|---|---|
| 1 | Boot G3 drop removes EVERY overlay key a violation names: a stored `mode.media=video` + bad `send.size` + a valid `record.fps` are all dropped (unit stays on stills, reachable, `<CF err>`) | **Kept, documented** (SPEC r2 §3.4): fail-safe; narrowing it means changing `supervisor_config` G3 (boot path) — Next sprint if wanted. `mode.media` must stay named for the `set` refusal. |
| 2 | Race: two concurrent `/changes` both pass `pending_unsent` (checked before the device-row lock) | **Fixed**: re-check under the lock after `allocate_remote_id`; test simulates the stale read |
| 3 | Non-ASCII digit (`"²x2"`) → 500 (`int()` after `isdigit()`) | **Fixed**: wire charset checked first; `check_value` errors → `bad_value` |
| 4 | Odd `video.send.size` already stored + `mode.media=video` not refused by the backend | **Fixed** (`cross_key`) |
| 5 | Backend refused a `video.send.*` change on a unit whose stored geometry is already broken (the unit would accept it) | **Fixed**: geometry refusal only when the change touches geometry keys or `mode.media`; else a warning |
| 6 | `redirect_stdout` in the camera rule swaps `sys.stdout` process-wide (daemon thread prints could be lost) | **Fixed**: the output is evened first (as `parse_output` would, within the encoder ceiling), no redirect; parity sweep unchanged |
| 7 | `camera.white_balance.gains` writable with no upper bound (into rpicam argv) | **Fixed as a proposal**: backend clamp ≤ 8.0 each (Q7, Nick decides) |

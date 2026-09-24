# S5 ladder 2 — live heal on bmcam004 / SPOT-31593C (2026-09-24T02:58Z – 03:57Z) — PASS

Code: `feature/s5-rsd-heal` @ f8a1bbf (bm #70), deployed with `tools/rc_field_update.sh --ref feature/s5-rsd-heal
--profile bmcam003 --leave-disarmed`, then `patch_camera_schedule.py --ensure media_key.enabled=true`.
Unit: video_tx, 1.3 s/msg, bm_commands on (tail 150 s), real halt, `transmit_phase` OFF. Uplink = cellular
(cellular_only). Downlink = Spotter USB console `bm pub` via nereus000 (Nick: console until vetted).
Backend: staging with `BM_KEYED_GROUPING=on`; fps + captured_at backfills applied first (264/264, 263/263).

| Step | UTC | Evidence | Result |
|---|---|---|---|
| Cycle 1: keyed clip, `--bench-drop-chunks 63` | 02:58:47 | `cycle1_*.log`: key `0dotlz`, 121 msgs, 63 not sent | sent 121/121 (63 skipped on wire) |
| Natural loss | 03:00:04–09 | SPOT-31593C console: 5x `Queue MS_Q_CELLULAR_ONLY is full` (5-min boundary stall; lane planner off) | chunks 36–40 lost |
| Backend partial | 03:21 | heal-candidates: media 53421, 115/121, missing `36-40,63` | keyed partial held by chunk |
| Heal command | 03:21:11 | `heal_command_post.json`: id 100000, `{"c":"rsd","h":[["0dotlz","36-40,63"]]}` | recorded, nothing sent |
| Console publish | 03:28:49 | console line 17859 `bm pub bmcam/cmd {...} 1 1` | — |
| Cycle 2 (key `0doutv`) | 03:25–03:31 | `cycle2_*.log`: `rsd id=100000 ... accepted`, `ack sent ... "ok":1` | persisted (6 chunks, 3 wakes); arrived in the listen tail AFTER END -> no `<HL>` this wake (by design) |
| Cycle 3 (key `0dov77`) | 03:33–03:39 | `cycle3_*.log`: `[HEAL] sent 6 heal chunk(s) before START: 0dotlz:[36..40, 63]`; `<HL v=1 key=0dotlz a=sent n=6 r=ok id=100000 w=0dov77>` after END | no new queue rejections; pending list empty |
| Backend complete | 03:56:51 | media 53421 (SAME row) 121/121, `is_complete`, 34,705 B; HL telemetry 160613 linked to 53421; heal_commands.last_hl_action=`sent` | PASS |
| Byte-exact | — | `healed_0dotlz_backend.h264` sha256 `87602408e72a…c72dd447` == unit sent record `2026-09-24T02-58-47Z_video_5s.sent` | PASS |

Latency: command delivered -> heal on the wire = next wake; heal on wire -> backend complete ~20 min (Notecard sync + ingest).

Not verified: that the partial R2 objects were deleted (the row's r2/video/display keys are final, non-partial
names; the bucket was not listed). `media_chunks` rows for 53421 = 0 after completion.

Findings
- The console path is fast and deterministic, but the command must land BEFORE the heal plan (`begin_wake`,
  ~+35 s into the cycle) to heal in the same wake; a tail arrival heals on the next wake. My launch script's
  wait loop stalled (the ssh that starts a cycle stays open until halt), so cycle 2 missed its slot — test-harness
  issue, not device code.
- bmcam004 still has `transmit_phase.enabled: false` -> bursts cross the 5-min boundary and lose ~5 chunks there.

Hardware left: bmcam004 awake, DISARMED (`#S3BENCH`), S5 code, media_key on, command state `applied_ids [100000]`,
`pending_heals []`. SPOT-31593C bus ALWAYS ON. Restore lines: see ../s3_bench_20260923T2325Z/run_manifest.json.

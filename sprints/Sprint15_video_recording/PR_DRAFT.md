# Sprint15: video recording + ring storage + customer download UI

PR: `claude/sprint15-video-recording-20630a` → `development`

## What this is

`capture_mode: video` — a third value of the existing runtime switch.
A video unit and a stills unit differ by ONE YAML value (SPEC
constraint 10); the cron @reboot line, deploy manifest path, overlay
doctrine, and every stills test are unchanged. Implements DESIGN
D-S15-1..10 as written.

New modules (all in `tools/rc_runtime_manifest.txt`):

| Module | Job |
|---|---|
| `video_recorder.py` | video island config, geometry derived from the stills keys (crop→`--roi`), crash-safe per-clip pipeline, record loop, daemon/UI wiring |
| `video_ring.py` | storage guard: `max_used_pct` 75 primary + `min_free_gb` 10 backstop, oldest-triple pruning, dry-run, pause-not-brick |
| `video_manifest.py` | sidecars, `manifest.json`, per-clip status JSON + bounded retry queue |
| `videoui_server.py` | stdlib gallery: posters, native playback, per-file download, HTTP Range (Safari scrubbing) |
| `network_ap.sh` | `wap` AP flip, revert-first (timer armed + verified BEFORE the flip) |

Touched existing files: `rc_progressive_jpeg.py` (mode dispatch after
overlay; ladder fail-soft in video mode), `spotter_time_sync.py`
(accept `video`; geometry validation), `command_tables.py` v5→v6
(`wap` + `IMMEDIATE_COMMANDS`), `command_daemon.py` (`wap_action_fn`
hook), `command_help.py` (wap help/cfg rows), `rc_command_hooks.py`
(wap dispatch wiring), `rc_run_capture_cycle.sh` (py_compile list),
`camera_schedule.yaml` (video island, heic mode untouched).

## Acceptance gates (SPEC → TRACKER for artifacts)

1. **Full suite green incl. all stills tests untouched** — 655 OK
   (baseline 555; +100 across 5 new test files; the only edited
   existing tests are the tables version-guard asserts, whose designed
   workflow is to be updated WITH a version bump).
2. **Bench (bmcam000)** — 8 clean 15 s triples, ffprobe-verified
   h264 1000x562@15.0; ~2 s boundary gap; artifacts in
   `runs/sprint15_bench_20260818/`. Two hardware-found fixes landed:
   explicit `-f mp4`/`-f image2` (.tmp suffix hides the extension from
   ffmpeg), poster `-ss 1` (frame-0 AGC ramp).
3. **Crash contract** — two live sysrq-b hard resets (mid-encode and
   mid-boundary): debris swept at boot, cron auto-resume ~48 s, ext4
   clean, zero completed clips lost. Bonus finding: fake-hwclock
   stamps stale cron-log names on crash boots; clip names stay correct
   because the Spotter clock sync runs before the first clip.
4. **HIL overnight** — running since 2026-08-18T00:43Z on this exact
   tip (5-min clips, session 0, cron-armed). Morning evidence: clip
   count vs wall clock, gaps, temps, ring. Status lines sent per clip
   over the shared UART; KNOWN ISSUE: SPOT-33507C cellular stall
   (latest Sofar row 2026-07-31T19:03:49Z, reconfirmed via API) —
   console-side evidence per SPEC gate 4.
5. **UI** — laptop check PASS (Nick, 2026-08-17); Range 206 verified;
   iPhone LAN check optional wording completion.
6. **(Prunable) wap** — code + tests + deploy DONE, deliberately NOT
   flipped: bench rehearsal needs an attended iPhone and kills WiFi
   while active. Gate 6 stays open until rehearsed.

## Storage math (measured)

Static bench scene undershoots the 2 Mbps cap (~28 MB/5 min ≈
8 GB/day); worst case at cap is 75 MB/5 min ≈ 21 GB/day → ≥3.8-day
rolling window at the 75 % ring cap on the 116 GB card.

## Not in this PR

wap bench rehearsal (gate 6), bmcam003 flash (post-merge), Spotter
antenna fix, settings GUI (follow-up commit in this branch if it lands
before review).

🤖 Generated with [Claude Code](https://claude.com/claude-code)

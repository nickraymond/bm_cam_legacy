# Sprint26 S3a bench gate — bmcam003 (bmcam004 control): **PASS**

Date: 2026-09-26 (UTC). Code: `feature/sprint26-s3a-runtime` fcd7bfd (draft PR #79).
Authorized by Nick in chat: bench gate on bmcam003, Spotter (SPOT-33507C) config as needed;
end state "whatever supports the next session" (S3b on the same rig).
Gate (PLAN_S3a.md §2): one still and one video cycle under each runtime; one armed
production wake under the supervisor with bmcam004 as control.

## 1. Steps (UTC)

| time | step | result | evidence |
|---|---|---|---|
| 21:43 | survey: bmcam003 dark, SPOT-33507C hourly (3600000/600000), controller on; nereus000 monitor up, heal driver off; no other session on nereus000 | ok | `console_commands.log` |
| 21:44 | hold bus on (`bridgePowerControllerEnabled 0` + commit), read back 0 | ok | `console_commands.log` |
| 21:45 | watcher caught bmcam003 at boot +~60 s: cron disarmed, boot cycle stopped before any halt | ok (see §4 gotcha) | `watcher_first_run.log` |
| 21:46 | backups → `/home/pi/s3abench/backup/` (code tgz, v1+v2 config, LKG, both states, media key, sha, **crontab_ARMED.txt**) | ok | `bmcam003_survey_before.txt` |
| 21:46 | deploy S3a fcd7bfd over S2 4c4fe8f (`rc_field_update.sh --ref feature/sprint26-s3a-runtime --profile bmcam003/live_20260925 --leave-disarmed`) | **PASS**: print-config parity OK, config JSON 13/13, v2-vs-v1 parity OK; config hash 81e05dee → e3449650 (registry v2, expected) | `bmcam003_deploy_s3a.log` |
| 21:47–22:16 | four live cycles from bench copies (halt dry-run): legacy/supervisor × video/stills | **4/4 complete over UART** | §2, `live/`, `bench.log` |
| 22:17 | live config: `commands.runtime: "supervisor"` added (hash 5e679ef9, level v2) | ok | this file |
| 22:18 | halt cleanly, restore controller (`… 1` + commit, read back 1); catch the stub-window boot, restore ARMED crontab, halt | armed + dark 22:20:12 | `rearm.log` |
| 22:21 | W4 probe: `ping` 26305 queued on the Spotter while the bus is off ("Queuing serial command") | queued | `console_commands.log` |

## 2. Live cycles (bench copies, `--skip-time-window`, bus held on)

| cycle | sent / planned | UART s | wall s | peak RSS self | time read (subscribe → decoded) | media key | Spotter: queued / queue-full |
|---|---|---|---|---|---|---|---|
| legacy video | 182/182 + 30/30 keyframe | 280.9 | 461.1 | 24.7 MB | 4.7 s (gate over the daemon, legacy read) | 0dtz7k (gate) | 215 / 0 |
| supervisor video | 183/183 + 30/30 keyframe | 282.2 | 456.4 | 27.2 MB | **1.9 s (fresh read)** | 0dtzkc (gate) | 183 / **32** (see F2) |
| legacy stills | 178/178 | 241.3 | 398.6 | 134.1 MB | 12 ms (buffered stamp, explicit key read) | 0dtzx4 (explicit) | 185 / 0 |
| supervisor stills | 180/180 | 238.7 | 402.0 | 134.4 MB | **2.9 s (fresh read)**; gate runs on the bypass (W6) | 0du08i (gate) | 187 / 1 |

Every cycle: exactly ONE `shared UART open`, no `[PORT]` line, halt dry-run reached,
`cycle end` printed; supervisor cycles wrote one `supervisor_actions.jsonl` line each.

W2 on hardware (mid-burst console `ping`):
- legacy stills, 26303 at 22:04:57: 4 acks went out MID-BURST (22:04:57–22:05:05, paced
  slots between chunks);
- supervisor stills, 26302 at 22:11:48: all 5 acks went out right AFTER END
  (22:13:54–22:13:58); burst 2.6 s shorter.

K5 (review): a re-subscribe yields a fresh publish within 1.9–2.9 s on this Spotter; the
supervisor's fresh reads cost no media key.

Backend (staging admin API, heal-candidates, 23:19Z): legacy video 0dtz7k and legacy stills
0dtzx4 complete; supervisor stills 0du08i 179/180 (chunk 129 = the one Spotter queue-full);
supervisor video 0dtzkc 166/183 (chunks 30–46 = the F2 Notecard-stall block; the keyframe
repeat covered the rest of it). Both are ordinary heal candidates (rsd preview 18 chunks).

## 3. Armed production wake under the supervisor (23:00Z window) — **PASS**

bmcam003 booted armed on the aligned 23:00 window (bus on 22:59:57), ran ONE supervisor
per_boot video action and halted itself; bmcam004 (S2, legacy) ran the same hour.

| | bmcam003 (supervisor) | bmcam004 (legacy, control) |
|---|---|---|
| Spotter console 22:59–23:12 | 203 queued, 16 queue-full | 205 queued, 11 queue-full |
| backend 23:40Z | 0du2kx `2026-09-26T23-00-33Z_video_5s.h264`: START known (expected 185), 177/185, missing 121–128 (one contiguous run = the queue-full block) → heal candidate | 0du2kz still arriving (Sofar exposure lag) |
| halt | bus current 0.035 A → 0.018 A (halted Pi) at ~23:08:45, before the 23:10 bus cut | (not measured) |

Queue-full losses of this size are the known per-burst Spotter/Notecard behaviour (S2 soak:
~11–13 per burst on both units), not the runtime. PASS criteria met: the armed supervisor
boot transmitted a keyed media the backend can heal, and halted on its own.

## 4. Findings

- **F1 (for Matt; grows S2 F1):** each console `bm pub` reached the Pi 4–5× within
  ~30 ms, and every duplicate is acked → 4–5 cellular acks per command
  (`[CMD] duplicate id=… acked, not re-applied`). S4 dedupe v2 should answer a
  duplicate without a new cellular ack.
- **F2 (Spotter, not runtime):** the supervisor video burst lost 32 messages in ONE
  contiguous block 21:56:06–21:56:35, starting 3 s after the Spotter's Notecard began a
  sync at 19 % full (it had just taken the previous 215-message burst). Same Notecard
  hand-off stall as S2/Sprint25; back-to-back bench bursts provoke it. In production those
  chunks are heal targets.
- **F3 (W4 on hardware):** a command published while the Pi is between processes
  (26301, 22:09:36) is forwarded by the mote into an unread UART and lost; the mote only
  buffers commands until cmdWaitMs (60 s) after ITS boot and releases them at ~+61 s, while
  the Pi subscribes at ~+21.5 s. So the W4 boot drain (non-blocking, right after
  subscribe) will usually find nothing on this rig; the buffered command arrives ~40 s
  later and is applied by the burst pump (next boot, as before). §3 measures it.
- **Gotcha (Pi clock at boot):** `rc_run_capture_cycle.sh` names its log from the Pi's
  clock BEFORE the Spotter time step (no RTC: fake-hwclock ≈ last shutdown), so a filter on
  the log name by wall time misses the current cycle. `observe_wake.sh` did, and saw nothing.
- **Gotcha (bench tooling):** the S1/S2 watcher pattern `[r]c_progressive_jpeg.py` also
  matched the survey's own `python3 rc_progressive_jpeg.py --print-config` in the same
  remote command, so `pkill` killed the watcher's shell after disarming (backups were then
  taken by hand). `watcher.sh` now calls `rc_progressive_jp[e]g.py` (a glob).

## 5. State at close (for the S3b session)

- **bmcam003:** S3a fcd7bfd, config v2 with `commands.runtime: "supervisor"` (hash
  5e679ef9), production crontab ARMED, real halt, hourly. Bench files and backups in
  `/home/pi/s3abench/` (`backup/crontab_ARMED.txt` is the armed crontab; the field-update's
  own `crontab_before_field_update_*.txt` holds the DISARMED one — do not restore that).
  Rollback: `commands.runtime` line out (= legacy on S3a code), or the S2 code tarball
  `backup/BM_Devel_Pi_before_s3abench.tgz` + v2 files as backed up.
- **SPOT-33507C:** controller ON, 3600000/600000, aligned (read back 1 at 22:19).
- **bmcam004:** unchanged (S2 f73129a, legacy, hash 81e05dee, armed); SPOT-31593C untouched.
  **Moves to S3a + supervisor at the start of S3b (Nick, 2026-09-26):**
  `watcher_host.sh bmcam004` at an :00 window, then `follow_bmcam004.sh` (deploy, runtime
  line, verify, re-arm, halt — fits in the 10-min window; that hour's image is lost).
- **nereus000:** console monitor running, heal driver DISABLED, no crontab (as found).
- **Open evidence to collect in S3b (on the Pi, read-only):** bmcam003's 23:00Z cycle log
  (`cron_logs/rc_cycle_*.log`, named with the pre-sync clock ~22:2x — see §4) and its
  `supervisor_actions.jsonl` line: where ping 26305 (queued while the bus was off) landed —
  boot drain (`[SUP] boot drain`) or later — and the time-read/clock-step lines.
- **Heal candidates left for the heal flow:** 0du08i chunk 129, 0dtzkc chunks 30–46
  (bench bursts).

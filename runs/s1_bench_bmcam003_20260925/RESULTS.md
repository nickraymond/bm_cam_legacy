# Sprint26 S1 bench gate — bmcam003, 2026-09-25

**Verdict: PASS.** The S1 branch behaves exactly like `development` on real hardware,
apart from the one intended wire change (W1), and boots lighter. bmcam003 and
SPOT-33507C were restored to their pre-bench state afterwards (verified).

| | |
|---|---|
| unit | bmcam003 (Pi Zero 2W, Debian 13, Python 3.13.5, Pillow 11.1.0, PyYAML 6.0.2, ffmpeg) on SPOT-33507C (bridge `c3c564b91856226c`) |
| dev | `development` 362e6cb (BM_Devel_Pi identical to cdadc95, the S1 base) |
| branch | `feature/sprint26-s1-cleanup` 6f21533 (PR #75) |
| approved by | Nick, 2026-09-25 (bench check + SPOT-33507C bus-cycle change via nereus000) |
| coordinated with | the mote-code session (SPOT-31593C / bmcam004 in use there: not touched) |
| conditions | bench moved outdoors mid-run (network drop ~19:20Z, power cycles ~19:40Z and ~19:44Z) |

## 1. Byte-for-byte on the Pi (harness, fake UART/camera/clock)

The golden harness ran all 12 scenarios against both runtimes ON bmcam003
(`harness_on_pi_comparison.txt`, hashes per trace):

- **0 unexpected differences.** Video and the window-skip scenarios: dev and branch
  traces byte-identical. Stills: the only difference is W1 (START `bf`/`zh` dropped;
  the freed budget lets `lg` fit).
- **Every branch trace on the Pi equals the golden recorded on the Mac** (Pillow
  12.3.0 there, 11.1.0 here) — JPEG bytes included. The committed goldens hold on
  the unit.
- The branch's video and window-skip cycles no longer load PIL (dev: always).

## 2. Real cycles over the BM bus (`--transmit --skip-time-window`)

Bench copies of the unit's own YAML (`cfg_*.yaml`): halt dry-run, command state copied
(the live state file was never touched). Measured with `rssrun.py`.

| | dev video | branch video | dev stills | branch stills |
|---|---|---|---|---|
| exit | 0 | 0 | 0 | 0 |
| wall time | 461.3 s | 461.1 s | 411.1 s | 404.3 s |
| peak RSS, python | 30.1 MB | **22.8 MB** | 133.8 MB | 133.7 MB |
| peak RSS, ffmpeg child | 122.9 MB | 128.9 MB | n/a (1) | n/a (1) |
| CPU python / children | 3.1 / 29.9 s | 3.0 / 31.0 s | 4.2 / 1.2 s | 4.6 / 1.2 s |
| main_entry -> transmit_start | 35.9 s (2) | 32.0 s (2) | 6.7 s | 6.8 s |
| sent / planned | 183/183 + 30 kf repeats | 185/185 + 30 kf repeats | 191/191 q60 (4 att) | 186/186 q30 (7 att) (3) |
| media key | 0drxte | 0drz63 | 0dry66 | 0drziv |

(1) For stills the "children" peak equals the parent's (a fork before exec inherits
the parent's RSS); it is not a real child measurement.
(2) Dominated by the Spotter UTC answer (spotter_utc_read +9.1 s dev / +3.8 s branch)
and the clip fit; not a code difference (section 1 proves the code path identical).
(3) Different scene: the bench was moved outdoors between the dev and branch stills
runs; the ladder settles lower on a brighter, busier frame.

## 3. Boot cost

| | dev | branch |
|---|---|---|
| `import rc_progressive_jpeg` (5 warm runs) | 0.622–0.642 s, PIL loaded | **0.494–0.502 s, no PIL** (−21 %) |
| per-boot `py_compile` in rc_run_capture_cycle.sh | 985 ms (22 files) | removed (S1.8) |

About **1.1 s less per boot**, and ~7 MB less resident memory on every video cycle.

## 4. Delivery at the backend (staging)

Final check 20:58Z (`backend_after.txt` has the 20:09Z snapshot; baseline in `backend_before.txt`):

| media | row | result |
|---|---|---|
| branch still (0drziv) | 54069 | **complete** |
| branch video (0drz63) | 54068 | 177/185 — one 8-chunk gap (169–176) |
| dev video (0drxte) | 54059 | 107/183 — tail 100–126, 134–182 missing |
| dev still (0dry66) | — | never arrived |

Not a code signal. The dev media went out while the bench was being moved (network
drop ~19:20Z; SPOT-33507C power-cycled ~19:40Z and ~19:44Z, which drops the Notecard's
unsynced messages — known behaviour). The branch video's single 8-chunk gap has the
shape of a 5-minute-boundary blackout (transmit_phase is off on this unit; the rigs lose
~5 chunks that way routinely). All four media are keyed and their sent records are on
the unit, so the heal loop can recover them; bm-heal-driver was not running after
nereus000's reboot (left for the rig owners). Byte-level equivalence is carried by
section 1, not by delivery.

## 5. What happened, in order (UTC)

| time | event |
|---|---|
| 19:08 | SPOT-33507C bridge config recorded (`spotter_bridge_cfg_BEFORE.txt`): hourly, 10 min on, controller ON, aligned |
| 19:10:01 | scheduled bus off; bmcam003 down |
| 19:16:24–40 | `bridgePowerControllerEnabled 0` + commit + read-back (0) via nereus000; bus on 19:16:33 |
| 19:17:21 | bmcam003 caught at boot: cron disarmed, boot cycle SIGTERMed mid-fit (nothing transmitted) |
| 19:18 | backups: code tarball, YAML, command state, key file, armed crontab, repo branch/HEAD |
| 19:18–19:21 | on-device harness, 24 runs, 0 unexpected diffs |
| 19:21:38 | `development` deployed (rc_field_update, --leave-disarmed): PASS |
| 19:22–19:37 | dev live cycles (video, stills) — completed; network dropped meanwhile (bench move) |
| ~19:40 | power cut (move); clean ext4 orphan recovery |
| 19:41 | branch deploy attempt 1: exit 127 (/tmp cleared by the reboot); re-staged: PASS 6f21533 |
| 19:42–19:44 | branch run 1 cut by a power cycle mid video transmit (logs kept: `live_branch_interrupted_1944Z*`) |
| 19:51–20:06 | branch live cycles (video, stills) — completed |
| 20:06 | unit restored + verified (`restore_unit.log`); clean halt 20:06:46 |
| 20:07:17–34 | `bridgePowerControllerEnabled 1` + commit + read-back (1); stub window from 20:07:24 |
| 20:08:13 | bmcam003 caught in the stub: armed crontab restored, clean halt; dark 20:08:42 |

## 6. End state (verified)

- bmcam003: runtime = pre-bench tarball (a71b6c7, file-for-file), bench-only modules
  removed, YAML identical to backup, command state unchanged, repo on
  `feature/s5-rsd-heal` a71b6c7, crontab ARMED, halted. Next boot = 21:00 window.
- `bm_media_key_last.txt` deliberately left at 0drziv (pre-bench 0drwt0): the bench
  media own those keys; rolling it back could let the unit reuse one.
- SPOT-33507C: `sampleIntervalMs 3600000, sampleDurationMs 600000,
  bridgePowerControllerEnabled 1, alignmentInterval5Min 1` — identical to before.

## 7. Lessons (applied to tooling in the same PR)

- `pkill -f 'rc_run_capture_cycle.sh|…'` inside `ssh 'script'` matches its OWN remote
  shell (the pattern is in its command line) and kills the rest of the script. The
  disarm still worked (cron edited first); the survey was lost. The
  bmcam-field-update skill now uses the `[r]c_…` bracket form.
- A disarmed Pi on a scheduled bus is hard-cut at the window end: hold the bus on
  (controller 0) for any session longer than the window, and restore with cron
  DISARMED, re-arming inside the ~2 min stub window (worked as planned here).
- A reboot clears `/tmp`: re-stage `rc_field_update.sh` after any power event.
- `ssh 'nohup … &'` hangs unless stdin is closed (`< /dev/null`).

## Files

`watcher.sh`/`rearm_watcher.sh` (catch-awake), `make_bench_cfgs.py`, `live_side.sh`,
`rssrun.py`, `import_time.py`, `restore_unit.sh`; logs `*.log`, `live_*/cycle_*.log`,
`deploy_*.log`, `console_commands.log`, `harness_logs/harness.log`,
`harness_on_pi_comparison.txt`, `survey_before.txt`, `backup_before.txt`.

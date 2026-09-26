# Sprint26 S2 bench gate — bmcam003 (+ bmcam004 control), 2026-09-26

**Verdict: PASS.** Config v2 runs bmcam003 exactly like its v1 files did: the same
config on every check, the same wire, 34/34 soak cycles on config v2 with no
fallback, and a rehearsed rollback that reads the untouched v1 files byte-for-byte.

| | |
|---|---|
| S2 unit | bmcam003 / SPOT-33507C, `feature/sprint26-s2-settings` 4c4fe8f, **config v2** (hash 81e05dee) |
| control | bmcam004 / SPOT-31593C, `development` 9771d53 (S1), v1 config (was f8a1bbf; moved so the A/B is S1 vs S2 only) |
| both | Pi Zero 2W, Debian 13, Python 3.13.5, PyYAML 6.0.2; motes on `nereus_cam` (command cache, #76/#77) |
| approved by | Nick, 2026-09-26 (push, both Spotters/cameras "owned", 30-min schedule, heal driver) |
| code delivery | a git bundle over scp (the push was blocked by the permission classifier until the morning) |

## 1. Gate steps on bmcam003 (DESIGN §8.3 S2)

| step | result | evidence |
|---|---|---|
| deploy S2 over the unit's runtime a71b6c7 (staged in `BM_Devel_Pi.next`, checks before install) | **PASS**: print-config parity old vs new; UART open 0x02 | `bmcam003_deploy_s2.log` |
| migration dry-run on the unit's own current state | **PASS**: every key mapped, no stop condition | `bmcam003_migrate_dryrun.log` |
| real migration + redeploy | **PASS**: strict load OK; on-unit v1-vs-v2 json parity (13 loader outputs equal); v1 files byte-identical | `bmcam003_migrate_write.log` |
| service key | created (600), copied to `~/.config/nereus/unit_keys/bmcam003.key` (sha prefix bdefebb4 both sides) | same |
| live A/B over the BM bus (bench copies, halt dry-run), S2+v2 vs S1 | **PASS**: 4/4 media complete on UART (below) | `live_bmcam00{3,4}/` |
| v8 commands into the v2 state (live listener, real bus) | **PASS**: hlt 3 / roi 5 / twn 2 in the v8 section, journaled; ping not journaled; repeated id not re-applied; hash 532af8d9 → 0c96fc56, 5 overlay keys | `cmd_test_report.txt` |
| boot fault injection (bench copies) | **PASS 5/5**: corrupt v2 → v1; bad ACTIVE value → v1; unwritable render dir → v1; everything unreadable → SAFE-MINIMAL (exit 0, 0.8 s, nothing opened); a real stills cycle on a corrupt v2 → 184/184 on the fallback | `fault_test.log` |
| rollback rehearsal: redeploy a71b6c7, then S2 again | **PASS**: a71b6c7's print-config byte-identical to the pre-bench capture (untouched v1 files); forward again: every check passes, hash 81e05dee | `rollback_rehearsal.log` |

### Live A/B (00:18–00:33Z, same wall clock, bench copies)

| | bmcam003 S2 + v2 | bmcam004 S1 |
|---|---|---|
| video: wall / sent | 461.4 s / 185/185 + 30 kf | 461.0 s / 185/185 + 30 kf |
| video: peak RSS python | 24.2 MB | 22.3 MB |
| stills: wall / sent | 406.0 s / 184/184 | 418.5 s / 193/193 (other scene) |
| stills: peak RSS | 91.9 MB | 93.2 MB |
| `import rc_progressive_jpeg` | 0.494 s | 0.496 s |
| whole config step (`--print-config`) | ~1.26 s | ~1.10 s |
| first armed boot, main_entry → cmd_subscribed | 0.79 s | 0.65 s |

S2's cost: ~0.15 s per boot (v2 load, render to tmpfs, proving the render against the
v1 loaders) and ~1.9 MB on a video cycle. The fallback path (v1 file migrated in
memory) peaked at 100 MB on stills (it imports the v1 reader), only when it is used.

## 2. Soak: 2026-09-26 01:00–17:30Z, 30-min schedule, nereus000 as controller

Both Spotters `sampleIntervalMs 1800000` (10 min on, aligned). nereus000 ran
`bm-heal-driver` (console-path heals) and a cron controller (`note sync` at :20/:50 and
the queued cache-test commands). The units moved indoors ~04:20Z (no power event).

| | bmcam003 (S2, v2) | bmcam004 (S1) |
|---|---|---|
| wakes / cycles | 34 / 34, every one sent complete on UART (175–186 msgs) | 34 / 34, same (173–186 msgs) |
| config level at boot | **v2 on all 34**, 0 `[CFG][ERR]`; LKG written once (00:48), never rewritten | v1 |
| complete at the backend (17:57Z) | all but the 2 newest (17:00, 17:30: heal due at 18:00) | all but the 2 newest, and `0dtdkz` (see F3) |
| heals | 23 commands, 31 media healed, 0 expired; 23 of 24 done in ONE wake | 20 commands, 29 media healed; 1 expired (54338), complete later anyway |
| Spotter `MS_Q_CELLULAR_ONLY is full` | 389 (~11/burst) | 439 (~13/burst) |
| Notecard before each forced sync | 18–25 % (never climbed) | 20–24 % |

**Mote-cache test (sent with the bus OFF, via the controller):** `twn 2` (id 26101) at
01:20 and `twn 0` (id 26102) at 01:50 reached BOTH cameras at the next wake, applied
once each. On bmcam003 both are in `config_journal.jsonl` (v8.twn null→2 at 01:31:00,
2→0 at 02:00:59). twn 2 = the window both units already use, so behaviour was unchanged.

## 3. Findings

- **F1 (for Matt): triple delivery on a held bus.** During the command test every console
  `bm pub` reached the camera 3x within ~1 ms (listener log). Dedupe held, but each copy
  sent its own cellular ack, adding to the queue-full rejections. Commands delivered at
  a wake from the cache (soak) arrived once.
- **F2 (Spotter-side): cellular queue rejections every burst, both rigs, S2 or S1.** The
  heals recover them: every soak media but the in-flight ones completed.
- **F3 (heal design gap): a media whose START is lost cannot be healed.** bmcam004's
  `0dtdkz` is `length_unknown` at the backend (the START never arrived), so it is
  skipped forever. Not S2 (control unit). Candidate for the self-healing backlog: a way
  to re-request the header.
- **F4:** the heal driver marked 54338 `expired` after 3 wakes, but it completed later
  (Sofar exposure lag). Harmless; the expiry is early for lagging rows.
- **Review before the bench:** an independent review of the S2 diff found 12 issues
  (two broke "never brick"); all fixed and pinned by tests before any hardware step
  (commit "S2 review fixes").

## 4. State at close (18:05Z)

- bmcam003: S2 4c4fe8f on config v2, armed. v1 files untouched (rollback copy);
  backups in `/home/pi/s2bench/backup/`.
- bmcam004: development 9771d53 (S1), v1, armed. Its S2 migration is the next step.
- Both Spotters: 30-min schedule (`sampleIntervalMs 1800000`), controller on. nereus000:
  heal driver enabled, soak controller cron still running. Restore steps: `SOAK.md`.

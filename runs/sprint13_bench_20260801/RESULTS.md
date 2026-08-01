# Sprint13 HIL rehearsal — bmcam003, USB console (2026-08-01 ~07:00-08:00Z)

Full DEMO_RUNBOOK rehearsal, overnight, Nick asleep. Build under test:
branch claude/sprint13-console-help-cfg-966167 (final sha 7468bff on
unit). Serial path: SPOT-33507C monitor cmd.txt injection. Unit was
DISARMED for the whole session (crontab backup
crontab_armed_sprint13_backup_20260801T065232Z.txt) and RE-ARMED at the
end. Quota spent: 2 images (Nick pre-authorized rehearsal).

## PASS — every runbook step

| Step | Evidence |
|------|----------|
| Deploy via git pull + rc_field_update | PASS x3 (79fc3c9, 4992c10→, 7468bff) |
| Spotter help/post style capture | spotter_help_capture.txt / spotter_post_capture.txt |
| **T1: spotter/printf echoes on v2.16.6** | help_echo_cycle2.txt — 123/123 non-blank lines intact, node-id prefixed, zero drops at 0.05 s/line (cycle2.log: 143 queued/143 sent) |
| help dedupe (no re-print) | cycle2.log: 1 applied + 3 duplicate-acked, single print |
| cfg renders + boxes align on real console | cfg_echo_cycle3.txt |
| cfg live next-boot view | cycle3: hlt 2 applied seconds before cfg → "halt DRY-RUN (logs only) / command hlt=2" |
| cfg source-column flip | twn row: "config file" (cycle3) vs "command twn=1" (cycle6) |
| hlt 2 next-boot dry-run | cycle5/6/7/8 all ended halt=dry_run, box stayed up |
| **trg 2 live capture+send** | cycle5_trg2.log: gate BYPASSED, camera image 105/105 COMPLETE, uart 107.9 s |
| **trg 3 reef reference** | cycle7_trg3.log: camera skipped, 192/192 COMPLETE, uart 196.4 s, persisted src untouched, trigger self-cleared |
| twn 0 / hlt 0 restore | cycle8 + final cfg: every row "config file", pending none |
| Re-arm field-normal | crontab restored (flock @reboot line verified); box up; next boot real-halts |

Sofar backend rows for the two images: pending at write time (13-30 min
lag is normal); re-poll before merge.

## Bugs found by the rehearsal (all fixed + tested + deployed)

1. `command_help.py` missing from tools/rc_runtime_manifest.txt — cycle
   died on ImportError. Fix + renderer import hardened (render_fn=None
   degrades gracefully). Commit 4992c10.
2. `--bench-commands` cycles had NO listen window post-Sprint11 (~15 s
   daemon life) — console-untestable. Now hold the bounded listen tail,
   production paths byte-identical. Commit 2088064.
3. cfg rendered the frozen boot-time value with a live source column
   (mixed truth right after a command). Now re-resolves YAML + overlay
   at query time. Commit 7468bff.

## Operational lessons (runbook updated)

- Manual cycles MUST take cron's flock: an overlapped manual cycle
  (cycle 4 started during cycle 3's live tail) got the box really
  halted mid-run by the older cycle's boot settings. Not a code bug.
- Spotter `reset` is rate-limited ("Reboot limit reached, ignoring") —
  space resets ~2.5 min apart; the retry loop handles it.
- Pi /tmp is tmpfs — bench logs go under /home/pi/s13_bench/.
- Practical per-cycle rhythm: bench cycle ≈ 15 s work + 150 s tail;
  spam injections land reliably from ~T+20 s.

## Open for the demo

- Out-of-window boots are command-deaf outside the bench flag (gated
  cycle → no tail). Demo before 10:00 ET should use --bench-commands
  cycles (this run's pattern) or wait for the window. Field doctrine
  question flagged for Sprint14.

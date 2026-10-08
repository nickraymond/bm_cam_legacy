# Bench gotchas — read before scripting anything that touches a bench Pi or a Spotter bridge

Each line cost captures or put a unit in a bad state. Newest first. Bench = bmcam003/SPOT-33507C, bmcam004/SPOT-31593C,
monitor nereus000 (192.168.1.45). Times UTC.

## 2026-10-07 — bmcam004 re-phase (three failures in a row, 3 captures lost, one hard power-off)

1. **ssh to a Pi that is halting can hang forever.** A plain `ssh pi@host true` loop after `tuned_halt.sh` stalled
   until the outer job's time limit killed it, before the bridge commit ran. **Every** ssh in a bench script needs
   `-o ConnectTimeout=3 -o ServerAliveInterval=2 -o ServerAliveCountMax=2` AND an outer time bound.
2. **`pkill -f` self-match (known since 2026-09-25, and it still bit).** `ssh host "pkill -TERM -f 'rc_run_capture_cycle.sh|…'; …halt"`:
   the remote `bash -c` command line contains the pattern, pkill kills its own shell, and the commands after it (the
   halt) never run. Proven on nereus000: the plain pattern ends the ssh with rc 255. Use bracket patterns
   (`'[r]c_run_capture_cycle.sh|[r]c_progressive_jpeg.py|[m]ain_pi_camera.py'`) for pkill AND for any pgrep/grep in
   the same command line (a plain `grep 'rc_run_capture_cycle'` later in the line makes `pgrep '[r]c_…'` match the
   shell too). macOS pkill does NOT reproduce this: test on Linux.
3. **Leading zeros + a bound that does not bound.** `hil_bridge_phase.sh SPOT ticks 04` passed `04` into embedded
   Python (`minute=04` = SyntaxError), the commit time came back empty, the wait was skipped and the commit went out
   70 s early (windows landed at :02:25, not :03:40). Then `perl -e 'alarm N; exec @ARGV'` around the console tool
   killed only the wrapper: the tool's ssh child kept the pipe open and the pipeline hung ~6 min, so the stub-window
   boot was not halted and the bus hard-cut the Pi ~112 s into a cycle. Fixed in the tool: `int('$MM')`, abort when
   no commit time is computed, and a process-group kill (`fork; setpgrp; alarm; kill -TERM -pgid`).
   **Rehearse with the real arguments** (a dry run with minute `8` did not exercise minute `04`).

Also from the same night: rewriting a script file in place (`cat > script.sh`) while an earlier copy of it is still
running corrupts what bash reads next (it died with a syntax error at its wake-up). Never edit a running script; copy
it to a new path.

## 2026-10-07 day — R2-DELAY / R3-PACE / remote reset

4. **Session crons fire late when the session is busy** (seen: 6 min and 28 min late). A switch that must act inside a
   wake (an A wake halts at ~:05) cannot hang on an hourly cron: launch a background job ~25 min early that waits for
   the exact second, keep a second launcher, and make the tool idempotent (a wake already handled = no-op).
5. **The budget check silently drops a wait.** The C2 lane wait (and the R2 start-delay patch) skip their wait when
   wait + burst (incl. heals sent before START) > the per_boot budget left (`[PHASE][WARN] skipping … skipped_no_budget`).
   It hits exactly the wakes after a lossy one (they carry heals). Log budget_left/heal_msgs every wake.
6. **Before a Spotter reset (cloud `reset`, `debug reset`, bridge commit): put the Pi in safe mode** — back up the ARMED
   crontab, comment the cycle, `@reboot sleep 45 && sudo -n tuned_halt.sh` — so any bus drop (stub window, reset at the
   :05 sync) finds it halted. Restore the ARMED crontab after.
7. **Spotter console `cfg get <key>` / `cfg list` return `ERR`** on the bench firmware; plain `cfg` prints the whole
   config read-only (e.g. `smrr = 1 - "" (0,1)`, `onsp = 30 - "" (1,2048)`).
8. **The Spotter queues its own HDR message (≈ 6129 B; 4045–6070 B seen) about every 5 min (:x4:59 / :x9:59 / :x0:01
   at bus-on)**, plus the hourly report (:05 on 31593C, :10 on 33507C) and a boot-anchored health check (:03:18–:03:33
   on 33507C, :02:31–:02:55 on 31593C). Their syncs block the cellular queue's hand-off to the Notecard for their whole
   TX wait (≥ ~45–60 s, up to 196 s).
9. **Attribute rejects by hand-off stalls, not by queue_full alone:** per message, `Added message(id N)` → `Queuing
   message N` > 1 s = a stall; rejects inside a stall within 60 s after an HDR = HDR, else "other" (Notecard-side
   stalls ~70–105 s after boot happen with no Spotter trigger in the console). `hil/tools/hil_r2_score.py`.
10. **zsh does not word-split `$var`** in `for x in …; set -- $x` — run such loops under `bash <<'EOF'`.
11. **Background jobs hit the 30-min/2-h tool limit**; an `ssh … nohup … &` that keeps the channel open dies with it
    (the remote job survives if fully detached: `setsid nohup … < /dev/null > log 2>&1 &`).

12. **Session crons may not fire at all** (TE2 2026-10-07: CronCreate jobs at :16/:35/:50 never fired in 2 h). Drive
    must-hit steps with a detached `nohup bash driver.sh > log 2>&1 < /dev/null &` (e.g.
    `runs/r3_pace_20261007/scripts/r3_driver_te2.sh`). For steps the session must finish, chain run_in_background jobs that
    exit at the step time, so their completion notice wakes the session. Run-folder scripts that `cd` into a worktree path
    break when the TE changes: copy them, never edit a running one.
13. **A cloud `reset` lands ~80 s after bus-on** (3 of 3 at the first hourly sync after send, 43–58 min later; 2 of 3
    logged NO `Remote message received … reset` line, only the banner + `Reset Reason: Debug reset`). The safe-mode
    `@reboot sleep 45 && tuned_halt` reaches the halted current only at ~88 s, so reset #3 hard-cut bmcam004 at ~78 s.
    Safe mode must halt in < 60 s (e.g. `sleep 15`), or the Pi must stay dark through the sync. Watchers must match
    `Reset Reason:` too, not only the receipt line.

14. **The Mac idle-sleeps on battery and freezes every timed job** (2026-10-07 18:59→20:34 PDT: the 03Z restore,
    deploy and inspection never ran, and they were killed at their time limit with no output). Before arming any
    window job, hold `nohup caffeinate -i -t 36000 >/dev/null 2>&1 </dev/null &` and check it with `pmset -g assertions`.
    A closed lid still sleeps. After a gap, check `pmset -g log | grep -E ' (Sleep|Wake) '`.

## Rules that follow

- Bench script = dry-run mode first, with the exact production arguments, then the real run.
- A failure must leave the unit **halted with cron armed** (or, worst case, up + armed for the scheduled bus cut) —
  never disarmed on an always-on bus, never mid-cycle at a bus cut. Don't disarm cron unless the step needs it.
- A bridge commit resets the bridge: expect a 120 s stub window that boots an armed Pi; catch + halt it, or disarm
  first and re-arm inside the stub window.

Older notes (2026-09-25 … 10-03: scheduled-bus hard cuts, /tmp cleared at reboot, `ssh 'nohup …'` needs `< /dev/null`,
cmd.txt needs Nick's OK, charge mode after a power-switch toggle, health-check reboots): see the TE memory notes and
`hil/procedures/BENCH_HOST_SETUP.md`.

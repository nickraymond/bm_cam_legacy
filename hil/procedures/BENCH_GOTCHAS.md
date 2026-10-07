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

## Rules that follow

- Bench script = dry-run mode first, with the exact production arguments, then the real run.
- A failure must leave the unit **halted with cron armed** (or, worst case, up + armed for the scheduled bus cut) —
  never disarmed on an always-on bus, never mid-cycle at a bus cut. Don't disarm cron unless the step needs it.
- A bridge commit resets the bridge: expect a 120 s stub window that boots an armed Pi; catch + halt it, or disarm
  first and re-arm inside the stub window.

Older notes (2026-09-25 … 10-03: scheduled-bus hard cuts, /tmp cleared at reboot, `ssh 'nohup …'` needs `< /dev/null`,
cmd.txt needs Nick's OK, charge mode after a power-switch toggle, health-check reboots): see the TE memory notes and
`hil/procedures/BENCH_HOST_SETUP.md`.

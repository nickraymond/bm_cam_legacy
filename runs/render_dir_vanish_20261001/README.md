# Render dir vanishes under stay_on — 2026-10-01 (desk only)

## Symptom (bmcam003 + bmcam004, development 3c1801d, stay_on, config v2)

`~/BM_Devel_Pi/cron_logs/rc_cycle_20261001T052205Z.log`, ~10×/unit in the first hour,
at every decision point (action, heal pass, 300 s heartbeat):

```text
[SUP][ERR] settings re-resolve failed (FileNotFoundError: [Errno 2] No such file or
directory: '/dev/shm/bmcam/.camera_schedule.yaml.0j9qc2i0.tmp'); keeping the last good settings
```

Not observed on the units by this session: the S6b HIL test owns them, so nothing was run there.

## Cause

- `supervisor_config.make_reresolve()` rewrites the tmpfs render with
  `atomic_io.write_text()`, which calls `tempfile.mkstemp(dir=/dev/shm/bmcam)`.
  If the **directory** is gone, mkstemp raises exactly this error, naming the
  tmp file. Reproduced byte-for-byte on the desk with `mkstemp(dir=<missing>)`.
- The directory is created once, at boot (`config_v2.render_dir()`), and
  never again.
- Nothing in the repo deletes it. **Inference (not verified on a unit):**
  systemd-logind `RemoveIPC=yes` (the Debian / Pi OS default) deletes every
  `/dev/shm` entry owned by a non-system user when that user's **last** login
  session ends. The cron-started supervisor is not a logind session, but
  `h3_arm.sh` ran several short `ssh pi@…` sessions. The last one
  (05:22:42Z bmcam004, 05:23:06Z bmcam003; `runs/s6b_hil_20260930/gate.log`)
  closed right after stay_on started (~05:22Z), so the wipe would land in
  that first minute. Every later re-resolve then fails.
- How to confirm after the HIL (read-only): `ls -la /dev/shm` (expect no
  `bmcam/`, `bmcam_stay_on`, `bmcam_stay_on_sched`),
  `grep -i RemoveIPC /etc/systemd/logind.conf`, `loginctl show-user pi -p Linger`.

## Effect today (without the fix)

| change arrives as | takes effect in stay_on? |
|---|---|
| `set` of a next-action key (e.g. `still.message_cap`, `uplink.msg_interval_s`, `video.send.*`) | **No.** It is persisted and acked, and the ack `h` carries the NEW hash (`make_hash_fn` reads the state file), but the process keeps the boot settings until it restarts. |
| `set`/`reset` of a next-boot key (`mode.run`, …) | Yes: exit 72 → the wrapper restarts → boot `render_dir()` recreates the dir. |
| `trg` with kv | Yes for that one action: `one_shot_render()` goes through `render_dir()` (makedirs). |

Also while the render file is missing, the readers that re-read it mid-action
**silently fall back to defaults** (no error line): the schedule gate
(`load_camera_schedule` → `CameraSchedule()`), `video_recorder.load_video_config`,
`rc_video_tx.load_video_tx_config`, and `bm_port` / `bm_serial` (buffer 300,
delay 5 s, `network_type` 0x01 = cellular with Iridium fallback). Video clips
force cellular-only on every message (`rc_video_tx._default_tx_open`), so clip
traffic is unaffected. Any send without an explicit network type gets the
0x01 default.

## Fix (MVP now)

`supervisor_config.write_render()`: recreate the render's directory if it is
missing (one loud `[CFG][WARN] render dir … was gone … recreated` line), then
the usual atomic write. Used by `apply()` and `make_reresolve()`. Each
re-resolve therefore restores the render file before the action's readers
run. `atomic_io` is unchanged: it also writes SD files, where silently
recreating a missing directory would hide a real fault.

## Not fixed here (follow-ups)

- `/dev/shm/bmcam_stay_on` (the wrapper's OOM-kill marker) and
  `/dev/shm/bmcam_stay_on_sched` get the same wipe. A signal death after
  the wipe is then not classed as a stay_on death (`rc_run_capture_cycle.sh:159`).
- Root cause on the host: `loginctl enable-linger pi` or `RemoveIPC=no`.
  This is a system config change, so it needs Nick's OK and belongs in
  deploy/provision.

## Evidence

- `test_before_fix.txt`: the new tests against development 04a5b92. 2 errors
  (the same FileNotFoundError on `.camera_schedule.yaml.<rand>.tmp`) and 1
  failure (`195 != 150`: the guarded settings fn keeps the stale value, so
  the `set` never runs).
- `test_after_fix.txt`: 13/13 OK.

## Host fix runbook: `RemoveIPC=no` on bmcam003 / bmcam004

Status: **prepared on the desk, not run.** Owner: the Test Engineer session, after it
takes over the bench. Approved by Nick 2026-10-01 (R1 plan, bm #99). Run it per
unit (`H=bmcam003`, then `H=bmcam004`). Record the step 1 and step 4 output on
bm #97.

### 0. Preconditions (all must hold)

- G1 (the R5 24 h run) has ended, and you own the bench (the S6b HIL session
  handed it over).
- The unit is up on the tailnet (`ssh pi@$H true`).
- You are not mid-capture. The edit itself touches no camera, cron or bus
  state. The change only takes effect at the **next boot**, so pick a moment
  when a Pi reboot (or the unit's normal halt/wake) is acceptable.
- Use bracket patterns with pgrep (`'[r]c_progressive_jpeg'`), or it matches
  your own shell.

### 1. Confirm the root cause (read-only)

Run this on a unit whose supervisor has been up since before at least one ssh
logout:

```bash
ssh pi@$H 'hostname; date -u; pgrep -af "[r]c_progressive_jpeg|[r]c_run_capture_cycle"; \
  ls -la /dev/shm; ls -la /dev/shm/bmcam 2>&1; \
  grep -n RemoveIPC /etc/systemd/logind.conf /etc/systemd/logind.conf.d/*.conf 2>/dev/null; \
  loginctl show-user pi -p Linger; \
  grep -c "settings re-resolve failed" $(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1)'
```

The cause is **confirmed** when a supervisor is running, `/dev/shm/bmcam` is
missing (also `bmcam_stay_on`), RemoveIPC is unset or `#RemoveIPC=yes`
(default yes), `Linger=no`, and the error count is > 0. If `/dev/shm/bmcam`
is present and the count is 0, the hypothesis is not confirmed: still apply
the fix (it is harmless), but say so on #97.

Reproduce directly (optional; it wipes the render, which code without #97
does not recover until a restart). Close every ssh session to the unit, wait
≥ 15 s (logind's user stop delay is 10 s by default), ssh back in and run
`ls -la /dev/shm`.

### 2. Back up

```bash
ssh pi@$H 'TS=$(date -u +%Y%m%dT%H%M%SZ); \
  sudo cp -p /etc/systemd/logind.conf /etc/systemd/logind.conf.bak_$TS && \
  ls -l /etc/systemd/logind.conf* ; ls -l /etc/systemd/logind.conf.d 2>&1'
```

### 3. Apply

The fix is a drop-in, so `logind.conf` itself stays unedited:

```bash
ssh pi@$H 'sudo mkdir -p /etc/systemd/logind.conf.d && \
  printf "[Login]\nRemoveIPC=no\n" | sudo tee /etc/systemd/logind.conf.d/90-bmcam-removeipc.conf && \
  systemd-analyze cat-config systemd/logind.conf | grep -n RemoveIPC'
```

Expect the last line to show `RemoveIPC=no` from the drop-in. Do not restart
systemd-logind on a running unit. The setting is read at the next boot.

### 4. Verify (after the next boot)

1. Check the render exists while the supervisor runs:
   ```bash
   ssh pi@$H 'pgrep -af "[r]c_progressive_jpeg"; ls -la /dev/shm/bmcam'
   ```
2. Make sure your session is pi's only one: `ssh pi@$H loginctl list-sessions`.
   Then close **every** ssh session to the unit and wait ≥ 15 s.
3. Open a **new** session and check:
   ```bash
   ssh pi@$H 'ls -la /dev/shm; ls -la /dev/shm/bmcam; \
     grep -c "settings re-resolve failed" $(ls -t /home/pi/BM_Devel_Pi/cron_logs/rc_cycle_*.log | head -1)'
   ```

**PASS:** `/dev/shm/bmcam/camera_schedule.yaml` and `/dev/shm/bmcam_stay_on`
(stay_on) still exist after the logout, and the error count is 0. Repeat step
4 once more for confidence. **FAIL:** the directory is gone again. Then
restore, and report on #97 with the step 1 and step 4 output.

### 5. Restore (undo)

```bash
ssh pi@$H 'sudo rm -f /etc/systemd/logind.conf.d/90-bmcam-removeipc.conf && \
  systemd-analyze cat-config systemd/logind.conf | grep -n RemoveIPC'
```

Then reboot (or wait for the next boot). `logind.conf` was never edited.
`logind.conf.bak_<TS>` is the original, if a byte-compare is wanted:
`sudo cmp /etc/systemd/logind.conf /etc/systemd/logind.conf.bak_<TS>`.

### Notes

- With #97 deployed, the supervisor recreates the render on its own (one
  `[CFG][WARN] render dir … recreated` line per wipe). The host fix also
  protects `/dev/shm/bmcam_stay_on` and `bmcam_stay_on_sched`, which #97 does not.
- New units: provisioning should carry the same drop-in (follow-up for
  deploy/provision; not done here).

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

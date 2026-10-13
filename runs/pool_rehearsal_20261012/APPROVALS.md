# Approvals for the pool rehearsal / pool (TE2 chat)

- 2026-10-12 ~11:00 AM PDT, Nick (typed in TE2 chat), verbatim:
  "Approved for Mon 10/12 and the pool Tue 10/13, bench Spotters SPOT-33507C and SPOT-31593C only (never field units):
  console cmd.txt / bm pub writes for (a) set camera.focus.mode manual + camera.focus.lens_position 1.82, (b) pool commands
  status/cal/loop/mark/stop/preview, (c) hil_bus_always_on.sh and hil_restore_schedule.sh, (d) pool_mode on/off on bmcam003/004."
- Not covered (needs its own OK): anything else on the units or nereus000 (deploys of the new PRs are hil_deploy_window.sh = pre-approved
  wrapper, but YAML/base edits, cron edits, deletions on the units are not), anything on field units, backend/cloud sends.
- 2026-10-12 ~11:03 AM PDT, Nick (typed in TE2 chat), verbatim: "You should be using nereus000 to issue commands over console for
  anything that is time sensitive, and you also have permission to force the spotters to be "always on" over serial console to help
  you perform your tasks". Scope as read by TE2: bench Spotters SPOT-33507C / SPOT-31593C, hil_bus_always_on.sh + hil_restore_schedule.sh
  via the nereus000 console whenever a task needs the units up; restore the hourly schedule after each use.
- 2026-10-12 ~11:58 AM PDT, Nick (typed in TE2 chat), verbatim: "OK set contrast 1.0 on bmcam003 via console."
- 2026-10-13 ~11:15 AM PDT, Nick (typed in TE2 chat), verbatim: "OK sweep on SPOT-33507C and SPOT-31593C"
  Scope as read by TE2: the pool `sweep` verb via the nereus000 console on the two bench Spotters (T-FOCUS and the pool).
- 2026-10-13 ~2:10 PM PDT, Nick (typed in TE2 chat), verbatim: "OK sensors on SPOT-33507C and SPOT-31593C"
  Scope as read by TE2: the read-only Spotter `sensors` console command via nereus000 on the two bench Spotters.
- 2026-10-13 ~2:45 PM PDT, Nick (typed in TE2 chat), verbatim: "Approved: update nereus000 /home/pi/hil_health/config.json to the new pairing (bmcam003 ↔ SPOT-31593C, bmcam004 ↔ SPOT-33507C)."

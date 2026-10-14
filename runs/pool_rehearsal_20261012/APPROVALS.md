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
- 2026-10-13 ~2:55 PM PDT, Nick (typed in TE2 chat), verbatim: "Approved: console set camera.focus.mode manual + camera.focus.lens_position 0.8 on bmcam003 (SPOT-31593C) and bmcam004 (SPOT-33507C), with a short bus hold."
- 2026-10-13 ~3:18 PM PDT, Nick (typed in TE2 chat), verbatim: "OK: console post on SPOT-33507C and SPOT-31593C, and one production capture + cellular transmit cycle on bmcam003 and bmcam004 via a short bus hold."
- 2026-10-13 ~5:32 PM PDT, Nick (typed in TE2 chat), verbatim: "OK install pool-preview as an enabled systemd service on nereus000"
- 2026-10-13 ~7:30 PM PDT, Nick (typed in TE2 chat), verbatim: "OK: turn off the visibility LEDs on SPOT-33507C and SPOT-31593C via the console (and revert command noted)."

## 2026-10-14T03:59:30Z (Tue 10/13 8:59 PM PDT): Nick typed in TE2's chat, verbatim
> OK: deploy the QE-passed development merge of bm #146 + #147 (sha from the EM) to bmcam003 and bmcam004 (always-on bus holds and bus power-cycles allowed to force production cycles), console set still.raw.progressive true and uplink.media_key.heal_order after on bmcam003 (SPOT-31593C) and bmcam004 (SPOT-33507C) after the bmcam003 memory check passes, then restore the hourly schedule; rollback = set both keys back (progressive false, heal_order before).

## 2026-10-14T16:05:37Z (Wed 10/14 9:05 AM PDT): Nick typed in TE2 chat, verbatim
> pool plan approved from above.

Refers to the pool-day line TE2 posted in this chat at ~9:00 AM:
> OK for Wed 10/14 (pool day), bench Spotters SPOT-31593C and SPOT-33507C only (never field units): hil_bus_always_on.sh and hil_restore_schedule.sh on both; pool_mode.sh args --lens-position 0.8 / on / start / stop / off on bmcam003 and bmcam004 and the §9 restore; console cmd.txt / bm pub writes for pool commands status/cal/loop/mark/stop/preview; a nereus000 LiFePO4 power cycle if the Spotter consoles don't come back; and console set still.raw.progressive false if the pool preview memory check fails.

# Mote command buffering (nereus_cam firmware) — bmcam004 / SPOT-31593C, 2026-09-25

Firmware from Matt (Sofar): the mote buffers anything published to `bmcam/cmd` until
`cmdWaitMs` (default 60000 ms) after the mote boots, then sends it to the camera. After that,
commands pass through as they arrive.

## Flash
- Mote `e6fe83ea6b4a2b7f` (bmcam004), file `bm_mote_bristleback_v1_0-nereus_cam-dbg.elf.dfu.bin`
  (244,744 B) from the SPOT-31593C SD card: `bridge dfu <file> 0xe6fe83ea6b4a2b7f 300000 force`.
- Before: `serial_bridge@ENG-v0.13.11-6-g54aff0a3`. After: `nereus_cam@ENG-v0.13.12-6-gfcbcc11a`.
- 18:09:24 → 18:10:04Z (transfer 28 s, total ~41 s). Procedure: `.claude/skills/bm-mote-dfu/SKILL.md`.
- The mote reboot at the end of the DFU hard-cut bmcam004, which had booted at bus-on
  (`uptime` showed a boot after 18:09:52).

## Regression — PASS
Production video burst `0ds02r` (20:11Z, 185 msgs at 1.3 s): **180/185 at Sofar**. The 5 missing
(151–155) are the Spotter's own `Unable to submit message to cell-only queue` at the 20:15:00
5-min boundary. That is bmcam004's known transmit_phase-off loss; old-firmware bursts that
morning lost chunks at :05 boundaries the same way. The mote delivered every message to the
Spotter. Earlier bursts `0druhs` (18:10) and `0drvek` (18:30) were queued 0-rejected but never
appeared at Sofar: SPOT-31593C had no GPS fix after an SD-swap reboot (17:57), and Sofar
published nothing for it until the unit was moved outside (fix at 19:45).

## Test 1 — bus on, live console command — PASS
`cfg` (92501) and `ping` (92502) during the production listen tail: camera ack ~1.5 s after
the console `bm pub`.

## Test 2 — 5 min on / 1 min off, 19/19 PASS
Bench listener (`tools/bm_cmd_bench_listener.py`, subscribe at uptime 21 s, halt at 240 s) plus
console driver (`tools/bm_cmd_buffer_test_driver.py`) sending `ping` at fixed offsets. Camera
ack times are measured from bus power-on (Spotter clock):

| sent at | cycles | delivered |
|---|---|---|
| off+20 (bus unpowered) | 4/4 | +60.5 to +60.7 s. The Spotter logs `Queuing serial command` and replays it at power-on. |
| on+5 (Pi not subscribed yet) | 5/5 | +60.7 to +61.7 s |
| on+30 (Pi subscribed at ~21.5 s) | 5/5 | +30.4 s **and again** +61.7 to +62.7 s (duplicate) |
| on+90 (after cmdWaitMs) | 5/5 | +90.5 s (live) |

**Finding for Matt:** a command that arrives after the camera subscribes but before
`cmdWaitMs` is delivered twice: live, then from the buffer. The camera dedupes by command id
(acks it, doesn't re-apply), so this is harmless for bmcam today.

## Test 3 — remote command via Sofar API — PASS
`ping` 927001 enqueued 19:51:06Z (`tools/sofar_send_command.py`, HTTP 202). The Spotter fetched
it at 20:11:18 (`Remote message received`), at the sync that a `bridge cfg commit` triggers.
The camera got it (live + buffer duplicate), acked at 20:16:26 (after END), and the ack row was
at Sofar by 20:55. The Spotter only checks its cloud mailbox ~hourly (:30 on this unit) or after a
boot/commit, so natural remote latency is up to ~1 h, driven by the Spotter and not the mote.

## Production subscribe timing (video cycle, bmcam004)
`[BOOT] cmd_subscribed uptime_s=21.48` / `21.53`. Against `cmdWaitMs` 60000 that leaves ~38 s of
margin. Sprint23's ~38–40 s figure was the older path.

## bmcam003 / SPOT-33507C — flashed + checked
- Mote `53171fa3d81a8e6f` (bridge `c3c564b91856226c`) flashed 22:20:28–22:21:06Z with bmcam003 halted:
  `serial_bridge@ENG-v0.13.11-6-g54aff0a3` → `nereus_cam@ENG-v0.13.12-6-gfcbcc11a`.
  Bus forced on for the flash, then restored to 3600000/600000/aligned/controller on. bmcam003 stays ARMED.
- 23:00 window ping check: 928000 (on+5) was released from the buffer before transmit start;
  928001 (on+90) passed through live. Each was applied once and acked, and both acks were at Sofar
  (23:05:48/49).
- Production burst `0ds7x2`: **166/185 at Sofar**. The 19 missing (105–112, 166–173, 182–184) all
  match `Queue MS_Q_CELLULAR_ONLY is full` rejections at 23:03:24, 23:04:44 and 23:05:05, during
  12–13 s Spotter→Notecard hand-off stalls. The mote delivered every chunk to the Spotter.

## Notecard fill after the 23:00 bursts
Both Notecards climbed 8% → 20–22% during the bursts and did not drain; the last sync was 22:50.
A forced `note sync` at 23:24:27 dropped both to 2–3% within 30 s (4% at 23:35).

## Open concerns
1. **Duplicate delivery (Matt / mote firmware).** A command that arrives after the camera subscribes
   (~21.5 s) but before `cmdWaitMs` is forwarded live AND replayed from the buffer (~+62 s). Seen in
   6/6 opportunities, including the Sofar-API path. The camera dedupes by id, so it is harmless
   today, but it is worth fixing or documenting in the firmware. The Pi also got a duplicate when
   it re-subscribed without a mote reset (listener smoke test).
2. **Spotter cellular-queue overflow (Spotter/Notecard side).** 12–13 s hand-off stalls overflow
   `MS_Q_CELLULAR_ONLY` and drop 8 chunks per stall at 1.3 s pacing. That is much longer than the
   3.4–3.7 s stalls measured in Sprint25. SPOT-33507C logged 173 queue-full rejections on
   2026-09-24. Next: check whether stall length follows Notecard fill or sync activity; a
   pre-burst `note sync` is one cheap mitigation to test.
3. **Notecard drains only at its ~hourly sync.** Fill reached 22% between syncs; a forced sync
   clears it. It is not yet known whether a fuller Notecard makes the stalls worse (see 2).
4. **Sofar drops data without a GPS fix.** SPOT-31593C lost GPS after its 17:57 SD-swap reboot,
   and its 18:10/18:30 bursts never appeared at Sofar. Bench Spotters now need sky view
   (moved outside 2026-09-25). `rtc set` alone did not fix it.
5. **Remote command latency is Spotter-bound.** The cloud mailbox is only checked ~hourly
   (:30 on SPOT-31593C) or right after a boot or `bridge cfg commit`. The mote buffer fixes the
   "replayed before the Pi listens" loss (Sprint23), but not the up-to-1-h wait.
6. **bmcam004 5-min boundary loss.** bmcam004 has transmit_phase off, so bursts that cross :05/:15
   lose ~5 chunks (`0ds02r`). This is known and unrelated to the mote.
7. **`bm-heal-driver` is disabled on nereus000.** It has not run since the ~19:40Z reboot, so
   no automatic rsd heals for the chunks lost above. It is owned by the S5 work; re-enable it with
   `sudo systemctl enable --now bm-heal-driver` if still wanted.
8. **`cmdWaitMs` not readable.** `bm cfg get <mote> s cmdWaitMs` returns `Failed to get cfg` on
   the fresh firmware; the key is probably unset, so the 60000 default applies. Ask Matt for the
   right get/set syntax before tuning it.
9. **Ops gotchas** (now in the `bm-mote-dfu` skill): the DFU mote reboot hard-cuts a running Pi,
   and every `bridge cfg commit` power-cycles the bus (plus a 120 s stub window when the controller
   is on). Halt the Pi first.

## Hardware left as found
SPOT-31593C bridge back to 3600000/600000/aligned/controller on (network config CRC
1092887805, same as before). bmcam004 production crontab restored from
`~/backups/crontab.before_mote_cmd_buffer_test_20260925T183809Z`. The mote stays on nereus_cam.
SPOT-33507C back to its hourly schedule; bmcam003 armed, mote on nereus_cam. Both Notecards ~4% at 23:35Z.
Raw evidence: nereus000 `/home/pi/spotter_logs/SPOT-31593C/console_20260925.log`,
`/home/pi/spotter_logs/mote_cmd_buffer_test/`, bmcam004 `cron_logs/bench_listener_*`.

# Sprint23 — Results (running log)

Unit: SPOT-31593C, USB console on Nick's Mac. All times UTC. Latency = console
line host timestamp (Mac clock, 1 s resolution) minus send time (Mac clock).

## Firmware

| When | Version | Source |
|---|---|---|
| 2026-07-29 | v2.16.6, bridge v0.13.11 | `~/spotter_logs/SPOT-31593C/console_20260729.log` banner |
| 2026-09-21T04:49:25Z | **v2.16.8** | banner after Nick's flash, `console_20260921.log` |

Health after flash (`post` 04:49:37Z): cellular OK, bridge OK, SD OK, bus
V/I OK. Non-OK only: `gpsErrorState NO_SIGNAL` (cleared to OK at 04:49:51Z),
`solarErrorState LOW` — both expected on an indoor bench.

Baseline on v2.16.6 was NOT taken (flash started first). "Before" reference is
July: 21–66 min on SPOT-33507C; SPOT-31593C 0/16 delivered, ever
(`runs/remote_cmd_diagnosis_20260731/REPORT.md`).

## Deliveries

| # | Sent | Command | Via | Received | Latency | Sofar id | Notes |
|---|---|---|---|---|---|---|---|
| 1 | 04:50:09 | `uptime` + `clear_command_queue` | CLI `sofar_send_command.py` | 04:50:55 | **46 s** | 38184 | First remote delivery this Spotter has ever had. Spotter uptime 0.04 h — it was in its post-boot burst of syncs (Rx checks 04:49:36, 04:50:21, 04:51:01), so this is NOT a steady-state number. |
| 2 | 04:54:46 | `uptime` | tester UI | 05:06:47 | **721 s (12.0 min)** | — | No sync happened 04:51→05:06. Delivered at the sync that the 05:05:34Z `bridge cfg commit` caused (network-config uplink → `Checking for Rx` 05:06:08 → message 40 s later). First live end-to-end catch by the tester. |
| 3 | 05:08:04 | `bm pub bmcam/s23test {"id":3,"via":"remote"} 1 1` | tester UI | 05:30:24 | **1340 s (22.3 min)** | 38186 | INVALID as a bus-off test: it landed inside the 120 s bus-ON re-init window caused by my 05:29:43Z schedule commit (which also produced the uplink that pulled it down). Ran straight through, silent — matches the bus-on USB template. |
| 4 | 05:31:55 | `bm pub bmcam/s23test {"id":4,"via":"remote"} 1 1` | tester API | 05:51:06 | **1151 s (19.2 min)** | 38187 | First delivery on an UNFORCED uplink (standard report queued 05:50:00 → Rx check 05:50:26 → message 05:51:06). Bus was ON (window 05:50:00–05:52:00), so not held; silent. `note sync` at 05:32:11 did nothing. |
| 5 | 05:54:20 | `bm pub bmcam/s23test {"id":5,"via":"remote"} 1 1` | tester API | 06:01:15 | **415 s (6.9 min)** | 38188 | INVALID as a bus-off test: pulled down by the 06:00:05Z always-on commit (Nick asked for bus-on to wire the camera). Bus ON, silent. |

Receive signature on v2.16.8 — unchanged from v2.16.6:

```text
[SYS] [INFO] Remote message received(16)! "uptime
id:38184
"
Uptime: 0.04 hours
Command not recognised.  Enter 'help' to view a list of available commands.
```

(`id:NNNNN` is appended by Sofar and runs as an unrecognised command. Harmless.)

## USB reference outputs (v2.16.8) — the template for remote commands

Typed over the USB console via `cmd.txt`, 05:04–05:08Z. A remote message runs
as the same console line, so these are the expected outputs.

| Command | Bus | Console output |
|---|---|---|
| `uptime` | on | `Uptime: 0.26 hours` |
| `bm topo` | on | `Bristlemouth toplogy: 0e582dd12c1e1480` (bridge only — no camera attached) |
| `bm info 0e582dd12c1e1480` | on | `Successfully sent info request` + `Neighbor information:` block (Node ID, GIT SHA 1595F804, `bridge@v0.13.11`) |
| `bm pub bmcam/s23test {"id":1} 1 1` | **on** | **nothing** — silent on success |
| `bm pub bmcam/s23test {"id":2,"via":"usb"} 1 1` | **off** (0.2 V) | `[BRIDGE] [INFO] Queuing serial command: bm pub bmcam/s23test {"id":2,"via":"usb"} 1 1` |
| `bridge cfg status 0e582dd12c1e1480 s` | on | 16 keys; `bridgePowerControllerEnabled` = 0 before the change |

**Queue signature found:** `[BRIDGE] [INFO] Queuing serial command: <line>`.
It fires for a USB-typed `bm` command too, not only for remote messages.
Release signature: pending the 06:00Z bus-on.

## Bench configuration change (reversible)

05:05:26Z, over USB, Nick-approved:

```text
bridge cfg set 0e582dd12c1e1480 s u bridgePowerControllerEnabled 1
bridge cfg commit 0e582dd12c1e1480 s
```

Was 0 (bus always on). Interval 3,600,000 ms / duration 900,000 ms unchanged.
Effect seen: bridge re-init, bus on 120 s, then `Sample enabled 1`,
`Bridge bus power: 0`, `power off for: 3152000` at 05:07:36Z.
Restore: same two lines with `0`. No node was on the bus (`bm topo`).

05:29:27Z, Nick-approved, to shorten test cycles: `sampleIntervalMs` 3600000 →
**600000**, `sampleDurationMs` 900000 → **120000**, then commit. Read back in
`Bridge network config`. Observed: `Sample Duration: 120 s / Interval: 600 s`,
bus off 05:31:45Z, `power off for: 502000` → next on ~05:40:08Z.
Restore: set 3600000 / 900000 and commit.

05:51:50Z: `sampleDurationMs` 120000 → **30000** (30 s on / 9.5 min off),
commit, read back. Reason: finding 6. **A Pi cannot boot in 30 s and would be
hard-cut every 10 min — restore the schedule BEFORE any camera is wired to
this Ebox.**

05:59:39Z, Nick's request (camera about to be wired in): controller **disabled**
(`bridgePowerControllerEnabled 0`) and schedule restored to the original
3600000 / 900000, one commit, read back. Console: `Bridge bus power: 1`,
`power on for: 4294967295` (always-on signature), bus 23.9 V. This commit
invalidates test 5 as a bus-off test (forced uplink + bus on).

Side effects of ANY `bridge cfg commit`: bridge re-init, bus forced ON 120 s,
and a network-config uplink → cellular sync → mailbox check. Do not commit
while a bus-off test is in flight.

## Findings so far

1. The wedged-mailbox theory held: one `clear_command_queue` and SPOT-31593C
   delivered on the next Rx check.
2. Delivery happens at a cellular sync (`[MS] Checking for Rx Messages`), same
   as v2.16.6, and lands ~35–40 s after that line. A sync happens when the
   Spotter has something to uplink: test 2 sat 12 min with no sync at all,
   then rode the uplink my config commit produced. Implication: latency to
   the Ebox ≈ time until the Spotter's next uplink. A camera that transmits
   often will pull its own commands down quickly; an idle Spotter will not.
   The idle sync period is still unmeasured.
3. `note sync` (USB) makes the Notecard sync but does not make the Spotter run
   `[MS] Checking for Rx Messages`. It is not a way to force delivery.
4. Release of a held command printed NOTHING: USB id 2 was queued at
   05:07:45Z (`Queuing serial command`), the bus came on at 05:29:45Z, and no
   line mentioned it. Either release is silent, or the bridge re-init dropped
   the queue. Undecidable from the console alone — needs a listener on the
   bus (bmcam000) or Sofar's answer.
6. **Clock collision.** Unforced standard reports (`MS_Q_LEGACY`) are hourly
   and land on a round boundary: 04:49:54 (post-boot) then 05:50:00. Bus
   windows anchor to the same round boundaries. Delivery comes ~66 s after
   the report, so with an on-window ≥ ~70 s starting at that boundary the
   command always arrives bus-ON. For a real camera this is GOOD news if its
   window covers the Spotter's report minute, and the July failure mode if it
   does not. `HDR` messages (every 5 min, `MS_Q_CELLULAR_ONLY`) and bus-on
   events (05:40:00 clean window) do NOT cause a mailbox check.

5. **(Resolved 05:05Z — controller enabled.)** Original note: at 04:50:43Z the bridge printed `Sample enabled 0`
   (Sample Duration 900 s / Interval 3600 s) and the bus sat at 23.9 V,
   0.000 A at 04:55Z. That reads as: bridge power controller disabled, bus
   always ON, nothing drawing current. The queue-until-bus-on feature can
   only be seen with the bus OFF at delivery time, so Phase 2b needs the
   power controller enabled (a `bridge cfg … commit`, which re-evaluates bus
   power immediately) — Nick's call, not done.

## Camera on the bus — bmcam003 (from 06:05Z)

Nick wired bmcam003 to SPOT-31593C and handed over ownership. Bus always-on.
`Neighbor 53171fa3d81a8e6f added` 06:05:19Z; bus current 0.09–0.12 A.

State found on the Pi (read-only look, nothing changed):

- cron `@reboot` armed → `rc_progressive_jpeg.py --transmit`,
  `capture_mode: "video"`, continuous 5-min clips, 1920x1080@15, 9.3 Mbps.
- Command daemon ALREADY listening: `[CMD] subscribed topic=bmcam/cmd`; the
  process holds `/dev/ttyAMA0` (single port owner). `TABLES_VERSION = 7`.
- YAML says `power_halt enabled: true / dry_run: false`, but the stored
  command `hlt=3` (developer mode, always awake) overrides it → no self-halt.
- In video mode commands are received at once but APPLIED + ACKED only at the
  clip boundary (`video_recorder.py` loop: record clip → boundary work →
  daemon drain). Measured: encode 303 s + boundary 46 s.

### Step A — bus ON, USB `ping` → PASS

| Event | UTC |
|---|---|
| USB `bm pub bmcam/cmd {"id":2301,"c":"ping"} 1 1` | 06:07:30 |
| Pi `[CMD] applied id=2301 ping=0` + `[CMD] ack sent: {"id":2301,"ok":1,…}` | ~06:12:15 (clip boundary) |
| Spotter console `[BM_TX] Submitted spotter/transmit-data … Len: 114` | 06:12:15 |

Command-to-ack 4 min 45 s, all of it waiting for the clip boundary.

### Step B — bus OFF, held command, release at power-on → NOT RUN YET

Open question this step answers: the Pi needs ~60 s from bus power-on to
`subscribed`. If the Ebox replays held commands the instant the bus powers
on, the camera is not listening yet and the command is lost. "Once the bus is
on and ACTIVE" (Sofar's wording) may mean it waits for a neighbor — unknown.
Needs an on-window ≥ ~7 min in video mode (boot + one clip + boundary).

06:26Z, Nick's decision: switch bmcam003 to stills, keep halt OFF (`hlt=3`),
accept hard power cuts (OK as long as no MP4 is mid-write).

- 06:26:30Z `capture_mode` "video" → **"progressive_jpeg"** via the camera's
  own settings page (:8080, "Save and restart now" — clean restart, recorder
  stopped properly). Backup on the Pi:
  `camera_schedule.yaml.before_gui_20260921T062630Z`. Restore = set Camera
  mode back to Video in the same page (the page only runs in video mode, so
  restoring needs the YAML edited or the backup copied back on the Pi).
- Verified after restart: `[RC] capture_mode=progressive_jpeg`,
  `power_halt: enabled=False … hlt=3`, `[CMD] subscribed topic=bmcam/cmd`.

**Finding 7 — a stills unit outside its transmit window is deaf.** Window is
10:00–15:00 America/New_York; at 02:27 local the cycle logged `a=skip_win`,
stopped the daemon and exited after **3.1 s** (`[RC] cycle end: elapsed=3.1s`).
With halt off the Pi stays up but nothing owns the UART, and the settings UI
is down too (it lives inside the video session). The documented un-brick,
`twn=2` (ALL DAY), is itself a command and needs a listener. Product
implication for remote config: outside the window a unit listens ~3 s per
boot, so a remote command — including the one that widens the window — has
almost no chance. Worth a TODO: keep the bounded listen tail on skipped
cycles.

Unblock (needs one command run on the Pi by Nick): a manual bench cycle,
`python3 -u rc_progressive_jpeg.py --bench-commands --skip-time-window`
(no image transmit, holds the 150 s listen window). Claude sends `twn=2` over
USB inside that window; it persists in `bm_command_state.json`, and every
later boot runs a full cycle at any hour.

06:29Z unblocked: Nick started the bench cycle; USB
`bm pub bmcam/cmd {"id":2302,"c":"twn","v":2} 1 1` at 06:29:17Z → Spotter
console ack `[BM_TX] Submitted spotter/transmit-data … Len: 114` at
06:29:18Z (**~1 s command-to-ack in stills mode**, vs ~5 min in video mode).
State file read back: `twn=2, hlt=3`, 2302 in `applied_ids`.

### Step B — run 1 (in progress)

Bus schedule 06:29:41Z: interval 1200000 / duration 600000 / controller 1
(10 min on / 10 min off, windows :00 :20 :40). Read back OK. Bus OFF
06:32:04Z, `power off for: 484000` → on ~06:40:09Z.

| Event | UTC |
|---|---|
| USB `bm pub bmcam/cmd {"id":2303,"c":"ping"} 1 1`, bus OFF | 06:32:13 |
| Console `[BRIDGE] [INFO] Queuing serial command: …2303…` | 06:32:14 |
| Remote `…{"id":2304,"c":"ping"}…` via tester, HTTP 202 | 06:32:21 |
| `Bridge bus power: 1` | 06:40:01.04 |
| `Neighbor 53171fa3d81a8e6f added` (camera's BM node, not the Pi) | 06:40:01.94 |
| Pi `[CMD] subscribed topic=bmcam/cmd` / Spotter UTC decoded | ~06:40:40 |
| Pi `transmit done: sent=158/158 complete=True uart=161.7s` | ~06:43:25 |
| Pi `post-transmit listen window done: 0 command(s) processed` | ~06:45:55 |
| Pi `[CMD] stopped: applied=0 … frames=0 sig_hits=0` ; `cycle end: elapsed=327.1s` | ~06:46:00 |

**Run 1 result: FAIL — held command 2303 never reached the camera.** The
daemon listened for the whole 327 s cycle and saw zero frames on `bmcam/cmd`.
The Spotter printed nothing about a release (BRIDGE / BRIDGE_SYS / BRIDGE_CFG
logs are already at DEBUG per `log list`). Two explanations remain:
(a) the Ebox replays at bus power-on / neighbor-added (≤1 s), ~39 s before
the Pi subscribes; (b) the held command is never replayed at all.

**Finding 8 — the camera's image transmit does not trigger a mailbox check.**
158 messages went out 06:40:40→06:43:25 on the cell-only queue; no
`[MS] Checking for Rx Messages` followed (last one 06:30:42, from my commit).
Matches the `HDR` observation. So on v2.16.8 the 150 s post-transmit tail did
not catch anything, and the Sprint10 assumption that the unit's own burst
pulls its mail down does not hold here. Remote ping 2304 (sent 06:32:21) was
still undelivered at 06:46.

### Step B — run 2: timestamp the replay with `bm info`

`bm info <node>` is a `bm` command (held while the bus is off, per Sofar) and
prints `Neighbor information:` on the console when it executes. Holding one
across a bus-off window gives the replay time relative to `Bridge bus power:
1`, with no camera involved.

| Event (Spotter's own timestamps where shown) | UTC |
|---|---|
| Bus OFF | 06:50:00 |
| USB `bm info 0e582dd12c1e1480` → `Queuing serial command` | 06:50:15.57 |
| USB `bm info 53171fa3d81a8e6f` → `Queuing serial command` | 06:50:23.75 |
| **Remote** 2304 (sent 06:32:21): `Remote message received(53)!` + `id:38189` | 06:50:55.01 |
| `[BRIDGE] [INFO] Queuing serial command: bm pub bmcam/cmd {"id":2304,"c":"ping"} 1 1` | 06:50:55.01 |
| `Bridge bus power: 1` | 07:00:00.15 |
| `Neighbor 53171fa3d81a8e6f added` | 07:00:01.05 |
| Both held `bm info` execute: 2× `Successfully sent info request`, 2× `Neighbor information:` (bridge, then `serial_bridge@ENG-v0.13.11-6-g54aff0a3`) | host 07:00:07 (≈07:00:08–09 Spotter clock) |

**Results**

- **First remote command held — the v2.16.8 feature works as Sofar described.**
  Remote ping 2304 arrived bus-OFF and was queued in the same millisecond.
  Ebox latency 1112 s (18.5 min), delivered on the 06:50 hourly report as
  predicted.
- **Replay happens ~8 s after bus power-on**, all held commands at once, in
  order. Not at subscribe time.
- **bmcam003's Pi subscribes ~40 s after power-on** (06:40 cycle: power
  06:40:01, subscribed ~06:40:40). The replay lands ~32 s before anyone is
  listening. Explanation (a) confirmed; this is why 2303 was lost.
- 07:00 cycle confirmed the same loss for the REMOTE held ping 2304: Pi
  `spotter UTC decoded: 07:00:40.5`, `[CMD] stopped: applied=0 … frames=0`.
- Not configurable from the console on v2.16.8 (checked 07:04Z): nothing in
  `help` (saved output: 128 lines; `bm` gained only `resources`), none of the
  bridge's 16 system keys, none of the Spotter's 142 `cfg` keys.
- bmcam003 boot budget (`systemd-analyze`, read-only): kernel 5.9 s +
  userspace 29.8 s; `cron.service` starts at 12.3 s userspace, ~6 s of that
  chain is cloud-init; NetworkManager 13.9 s is off the critical chain. App
  start → `subscribed` takes the rest, landing at ~40 s from bus power. GPU
  firmware/bootloader time before the kernel is NOT measured. A 5 s boot is
  not reachable on this OS: kernel alone is 5.9 s. Trimming cloud-init and
  starting a tiny early listener might reach ~12–15 s (estimate, untested),
  still slower than the ~8 s replay.
- Gap to close: ~32 s. Options, none built: Sofar-side delay/replay-on-
  subscribe (asked via Nick); the camera's BM node (`serial_bridge`, up in
  1 s) buffering the last message per topic; operator-side retry-until-ack
  (already the Sprint10 doctrine, GUI retry engine); faster Pi boot does not
  plausibly reach 8 s.

### Step C — dodge the race: make the command arrive LIVE (in progress)

Idea: no firmware change. The mailbox check follows the Spotter's hourly
report at :50:00, and the message lands 55–66 s later (measured 05:51:06,
06:50:55). If a bus window STARTS at :50:00, the Pi subscribes at ~:50:40 and
the command arrives ~:50:55 onto a live bus with a listener — margin 15–26 s.

07:06:10Z schedule: interval 600000 / duration 360000 (6 min on / 4 min off,
windows :00 :10 … :50; a stills cycle needs ~5.5 min). Read back OK.
Remote `bm pub bmcam/cmd {"id":2305,"c":"ping"} 1 1` sent 07:08:44Z (202),
after the commit's own mailbox checks (07:06:58, 07:07:06) and with the bus
off. Expected: delivery ~07:50:55, ack within seconds.

Earlier block, 06:13Z: Claude's session is not permitted to run state-changing
commands on the Pi, so it cannot halt it cleanly before a bus power cut.
Waiting on Nick's choice (he halts it / accepts hard cuts / grants the rule).

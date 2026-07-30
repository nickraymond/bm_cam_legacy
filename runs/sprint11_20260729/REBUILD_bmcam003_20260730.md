# bmcam003 rebuild — fresh SD to Sprint11-candidate config (2026-07-30)

Context: bmcam003 went down 2026-07-30T01:59:33Z (hard bus cut mid-cycle,
RESULTS.md lesson 2 / DEV_LOG F5). Nick reflashed a fresh SD and deleted
the old machine from the Tailscale admin console. This log covers the
rebuild from LAN discovery to end-to-end verification.

## Timeline (UTC)

- ~04:0xZ  Fresh Pi found at 192.168.1.230 (router UI; MAC 88:A2:9E:BC:F4:62
           — note: this Pi-vendor prefix is missing from the
           pi-tailscale-setup sweep list, which is why the ARP sweep missed
           it). Pi was intermittently unreachable until WiFi power save was
           disabled.
- 04:1xZ   Stale bmcam003 SSH host keys cleared from Mac known_hosts
           (fresh flash = new host keys).
- ~04:2xZ  ssh-copy-id by Nick; trixie NOPASSWD gotcha hit again (skill
           already documents it); fixed by Nick.
- 04:24Z   WiFi power_save OFF (immediate + persistent via
           /etc/NetworkManager/conf.d/wifi-powersave.conf). Likely the cause
           of the fresh Pi dropping off the LAN.
- 04:27Z   Tailscale installed, registered as bmcam003 → 100.108.182.50
           (old IP 100.103.35.24 is dead; run scripts pinning it need the
           new address).
- 04:30Z   Repo cloned at development tip ac9bab9 (contains 648c889).
           setup_bm_uart.sh + reboot; CHECK PASS after reboot.
- 04:32Z   deploy_rc_runtime.sh --fresh --profile rc_field_template
           --install-cron. software_sha.txt=ac9bab920d4c. (First attempt
           failed: /home/pi/backups was root-owned from sudo'd UART script;
           chown pi:pi fixed. Deploy script or UART script should own this.)
- 04:33Z   DISARMED immediately: @reboot cron commented
           ("# DISABLED rebuild 20260730T043307Z"), crontab backup at
           /home/pi/crontab_backup_rebuild_20260730T043307Z.txt,
           power_halt enabled:false dry_run:true. Verified via
           yaml.safe_load on-device.
- 04:33Z   Sprint11 candidate config patched via tools/patch_camera_schedule.py
           and read back through rc_progressive_jpeg.py --print-config
           (all three required lines present — see below).
           bm_command_state.json seeded src=1 (schema v1, tables_version 2,
           touched=["src"]).
- 04:34Z   Validation: UART CHECK PASS incl. bm_serial open test;
           --capture-only produced refsrc native 4,178,802 B (src=1 skips
           the camera, so the ribbon was verified separately with
           rpicam-still: IMX708 4608x2592, 1,314,328 B — camera OK).
- 04:36:03Z First --transmit attempt exited at the schedule gate (window
           08:00-15:00 PT; it was 21:36 PT). Spotter time-sync worked.
- 04:36:44Z Real cycle: --transmit --skip-time-window,
           log /home/pi/manual_cycle_20260730T043644Z.log (on the Pi).
- 04:40:30Z Burst on-lane per C2: [PHASE] phase=106.7s burst=195s lane=250s
           -> wait=223.3s (start@+30s). q-ladder settled q60: 55,070 B,
           192 msgs (under cap 195).
- 04:43:47Z transmit done: sent=192/192 complete=True uart=196.4s;
           150 s listen tail ran (0 commands); cycle end elapsed=571.8s
           of 780s; halt disabled.
- 03:24Z(!) SPOT-33507C USB console vanished from /dev — the serial monitor
           has no record of this cycle (Spotter itself stayed up: it kept
           powering the Pi throughout). Console verdict UNAVAILABLE;
           Sofar API is the delivery verdict for this cycle.
- 04:5xZ   Sofar API poll (poll_manual_cycle.sh SPOT-33507C
           refsrc_20260730T043645Z): PENDING — see verdict below.

## Resolved config (via --print-config, the runtime's own parser)

```
[CMD] bm_commands enabled: topic=bmcam/cmd tail=150.0s defer_acks=True state=... (loaded from file)
[CMD] override source_image_path: None -> reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg (src=1)
[RC] cycle budget: max_run_time_min=13 (780 s)
[RC] message cap: 195 msgs (field-tested hard cap)
[RC] pacing (bm_serial block, source=yaml): chunk_b64_chars=384 delay_s=1.0
[RC] power_halt: enabled=False dry_run=True mode=halt
[RC] transmit_phase (C2): ON grid=300s guards=30/20s lane=250s
[RC] transmit_phase rule: cap 195 x 1.0s = 195s vs lane 250s -> fits
```

## Delivery saga (post-rebuild, same night)

- Cycle 1 (refsrc_20260730T043645Z) NEVER REACHED THE API. Root cause: the
  Spotter commanded its own reset at 04:59:16Z ("[ORC] Running health
  check!" -> "[SYS][ERROR] rebootctl reset 2. Source: 7"), ~15 min after
  the burst was queued; the boot's "Removing old notecard tx messages"
  took the staged image with it. This was the ONLY firmware-commanded
  reset in 2 days of console logs — hourly health checks normally pass.
  The 03:06Z and ~03:59Z reboots have no rebootctl line (physical/power
  events during bench recovery; 03:59 is what un-wedged the fresh Pi).
- Spotter health re-checked after: post/sensors identical to the 03:07Z
  healthy baseline (baro INIT_ERR = the red LED, GPS/solar indoor states,
  everything else OK). SPOT-31593C delivered 183 API rows in the same
  window -> Sofar API path healthy; problem was 33507C-local.
- Cycle 2 (refsrc_20260730T052401Z, 05:25:22-05:28:39Z): Pi 192/192,
  console verdict 193/194 submitted with ONE queue-full reject pair.
  sdmq size = 0 after burst (Spotter queue fully drained to Notecard,
  17 % full). API delivery still pending at 05:38Z — jam, if any, is
  Notecard->cloud.
- NOTE: first Submitted line at 05:25:22Z = phase +22 s, but the Pi
  planned start@+30 s — the Spotter-BM time sync appears ~8 s off the
  Spotter's own log clock. Erodes the post-boundary guard 30 -> 22 s.
  Worth measuring properly next sprint.

## End state (2026-07-30T05:40Z — ARMED for overnight soak, per Nick)

- Deployed sha: ac9bab920d4c (development tip; 648c889 ancestor confirmed
  on-device).
- ARMED: @reboot cron ACTIVE, power_halt enabled (real, mode=halt).
  Sprint11 candidate config verified via --print-config after arming.
- BENCH-ONLY config deltas for the soak: enforce_time_window false,
  enforce_spotter_time_window false (template window 08:00-15:00 PT would
  have gated out every night cycle). RESTORE BEFORE FIELD DEPLOY.
- Pi cleanly halted 05:35:28Z BEFORE the schedule commit.
- SPOT-33507C 15/15 schedule committed 05:36:29Z and read back:
  bridgePowerControllerEnabled 1, sampleIntervalMs 1800000,
  sampleDurationMs 900000, samplesPerReport 1, alignmentInterval5Min 1.
- Tailnet: bmcam003 = 100.108.182.50 (NEW — update any pinned scripts;
  old 100.103.35.24 is dead). WiFi power save disabled persistently.
- Sofar API verdicts: cycle 1 LOST (Spotter self-reset at 04:59Z).
  Cycle 2 (manual, 05:28Z) LOST — staged into the Notecard during the
  broken window, drained (16 % -> 2 % at 06:44Z) without ever reaching
  the API. Delivery resumed for everything staged AFTER ~06:02Z.

## Overnight soak (live log)

- 05:36:30Z  Schedule commit BLIPPED bus power and re-booted the HALTED
  Pi (halted != unpowered). Its cron cycle was then HARD-CUT by the
  schedule's first bus-off at 05:38:29Z — the F5 scenario despite
  halt-first. Pi/SD survived. TRAP: after a bridge cfg commit, verify
  the Pi stayed down; consider disarming cron for the commit.
- Window 06:00: burst 195 msgs clean (06:00:29-06:03:56Z, 0 rejects),
  in_lane start@+49s. API: tail chunks 84-191 + END delivered; head
  (START+0-83) lost with the pre-06:02 backlog.
  Filename refsrc_20260730T053807Z — STALE CLOCK: Pi Zero has no RTC;
  fake-hwclock stamps cron-start filenames until Spotter time-sync.
  Cosmetic, but filenames don't match wall clock.
- Window 06:30: burst 193 msgs, 2 queue rejects; API 190/192, missing
  exactly [108, 118] = the two rejects (sporadic-collision population,
  F6/D10 — head-chunk duplication is the planned fix).
- note sync accepted (06:45Z); confirmed backlog was gone, not stuck.
- Persistent watcher running: per-window console burst + API chunk
  counts, ALERT on stall/no-burst/stale console. Intervention policy
  (Nick pre-approved): Spotter reset in a bus-off window only if new
  windows stop delivering.
- Timing note: console shows first Submitted at phase +22 s vs planned
  +30 s — Spotter-BM time sync ~8 s off Spotter log clock; erodes the
  post-boundary guard. Measure next sprint.
- Window 07:00: burst 187 msgs on console but UPLINK DEAD AGAIN — zero
  API rows (even telemetry) after ~06:46Z. Second occurrence of the
  silent Notecard->cloud stall; both follow ~30-45 min of operation.
- 07:19:40Z RESET FLUSH executed (Nick pre-approved) during the
  07:15-07:30 bus-off window. Clean boot 07:19:43Z, Notecard OK,
  cellular OK. NOTE: Spotter boot grants the bus a 2-min power grace
  (power on for: 120000) which boots the Pi and then hard-cuts it
  mid-boot — unavoidable from the Spotter side; Pi has survived these.
  If the stall recurs post-reset, this is a Sofar-side/Notecard problem
  beyond bench remediation -> support ticket for SPOT-33507C.
- 07:51Z RESET DID NOT RESTORE DELIVERY: 07:30 window burst clean on
  console (0 rejects), 0 API rows. No console sync lines since the
  07:19 boot. Earlier syncs at 06:10 and 07:00 BOTH reported "All
  messages sent successfully" while their data never reached Sofar —
  device-side success + cloud-side absence. Verdict: Notecard/carrier/
  Sofar-cloud issue for SPOT-33507C; SPOT-31593C on the same account
  delivers fine. Continuing the soak for Pi-side data; delivery gap
  documented for a Sofar ticket. No further resets planned.
- 08:14Z forced `note sync`: ran at 08:19:45, "All messages sent
  successfully", Notecard drained 16 % -> 2 %, signal OK — and ZERO rows
  at Sofar (checked 08:25Z). Two more reef images vanished in transit.
- Full-day Sofar row map (SPOT-33507C, queried 08:2xZ):
  00h=368, 01h=179 (sprint run, healthy) | 02-05h=0 (incident+reboots)
  | 06h=302 (06:02-06:46 recovery — our two partial images) | 07-08h=0.
  No mis-timestamped rows. SUPPORT TICKET SUMMARY: since ~06:46Z the
  Notecard on SPOT-33507C (IMEI 351077454541593, NOTE-WBGLW, fw
  notecard-6.2.5.16868) reports successful syncs while no data reaches
  the Sofar API; Spotter reset at 07:19Z did not help; sibling
  SPOT-31593C delivers normally on the same account.
- OVERNIGHT POSTURE (from 08:30Z): soak continues on the 15/15 schedule
  (Pi-side data valid regardless of uplink); watcher logs each window;
  NO further resets/syncs — proven ineffective.
- 10:33Z UPLINK SELF-RECOVERED (dark 06:46-10:30Z): window 10:30 image
  refsrc_20260730T100649Z delivered 191/192 + END. Emerging pattern:
  intermittent cloud-side availability (~45 min alive, ~3.5-4 h dark);
  dark-period images are lost (Notecard drains into the void), alive-
  period images arrive ~99 % complete. Ticket evidence strengthened:
  nothing bench-side changed at 10:30.

## Overnight soak — full three-layer reconciliation (06:00-14:30Z)

Each cycle puts 195 messages on the wire: 1 WS status + 1 START + 192
chunks + 1 END. "UART" counts chunks only (the Pi's sent=N/N line);
"accepted/rejected" counts ALL message types at the Spotter queue
(console `Submitted` lines); "API chunks" counts `<I n>` rows only —
START/END arrive as their own rows. For every uplink-alive window the
ledger balances EXACTLY: accepted = API chunks + START + END + WS.

| Window | UART chunks | accepted/rejected | API /192 | START | END | verdict |
|---|---|---|---|---|---|---|
| 06:00 | 192 | 195/0 | 108 | no | yes | head lost to 04:59 reset aftermath |
| 06:30 | 192 | 193/2 | 190 | yes | yes | good |
| 07:00 | 192 | 187/8 | 0 | no | no | uplink dark |
| 07:30 | 192 | 195/0 | 0 | no | no | uplink dark |
| 08:00 | 192 | 193/2 | 0 | no | no | uplink dark |
| 08:30 | 192 | 195/0 | 0 | no | no | uplink dark |
| 09:00 | 192 | 193/2 | 0 | no | no | uplink dark |
| 09:30 | 192 | 193/2 | 0 | no | no | uplink dark |
| 10:00 | 192 | 194/1 | 0 | no | no | uplink dark |
| 10:30 | 192 | 194/1 | 191 | yes | yes | good |
| 11:00 | 192 | 193/2 | 190 | yes | yes | good |
| 11:30 | 192 | 126/69 | 124 | no | yes | queue-full collision w/ Notecard sync |
| 12:00 | 192 | 195/0 | 192 | yes | yes | COMPLETE |
| 12:30 | 192 | 143/52 | 140 | yes | yes | queue-full collision |
| 13:00 | 192 | 194/1 | 191 | yes | yes | good |
| 13:30 | 192 | 189/6 | 186 | yes | yes | good |
| 14:00 | 192 | 195/0 | 192 | yes | yes | COMPLETE |
| 14:30 | 192 | 194/1 | 191 | yes | yes | good |

Completeness summary:
- Camera unit: 18/18 scheduled cycles transmitted 192/192 chunks on
  UART. Zero unit-side failures across 18 hard power cycles.
- COMPLETE at the Sofar API (START + 192 chunks + END):
  refsrc_20260730T113648Z_compressed.jpg (12:00Z window) and
  refsrc_20260730T133706Z_compressed.jpg (14:00Z window). These two are
  the backend test cases: a correct backend must show them complete.
- Near-complete (186-191/192, START+END present): 6 more windows —
  losses match console queue rejects one-for-one (sporadic collisions;
  head-chunk duplication D10 is the planned fix).
- Lost wholesale: 07:00-10:00 windows (uplink dark; Notecard reported
  successful syncs, Sofar received nothing — SPOT-33507C ticket).
- Loss mechanisms are BOTH Spotter-side: (a) dark-uplink periods
  (06:46-10:30Z, self-recovered), (b) queue-full clusters when a burst
  overlaps a Notecard backlog sync (11:30, 12:30 windows).
- API decode note for backend work: sensor-data `value` fields arrive
  HEX-ENCODED (`units: "hex"`); decode hex -> ascii to get `<I n>`,
  `<START IMG>`, `<END IMG>`, `<WS ...>` frames. Filenames carry
  fake-hwclock timestamps ~30 min behind the true window (Pi Zero has
  no RTC); map cycles by burst timestamp, not filename.

## Loose ends

- SPOT-33507C USB console link down since 03:24Z — reseat cable, then
  spotter-health-check before the next bench run.
- pi-tailscale-setup skill: add 88:A2:9E to the Pi MAC sweep list.
- /home/pi/backups root-ownership trap when setup_bm_uart.sh (sudo) runs
  before deploy_rc_runtime.sh (user): worth a chown -R pi:pi or mkdir -p
  as pi in the deploy script.
- rc_field_template quality block: yaml shows q_max 15 but the runtime
  resolves the explicit ladder [90..9] — print-config is authoritative;
  noted only to avoid future grep confusion.

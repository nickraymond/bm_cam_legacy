# Sprint13 morning demo — runbook (bmcam003, USB console)

Nick's acceptance test before PR approval: read `help`/`cfg` on the
terminal, then personally trigger a live camera capture (`trg 2`) and a
reef reference transfer (`trg 3`). Two quota images, both his.

State going in: bmcam003 ARMED (real halt, Pi down between cycles),
Spotter SPOT-33507C always-on at /dev/cu.usbmodemSPOT_33507C1, serial
monitor running (Sprint12 instance, log root
`.claude/worktrees/sprint10-phase-e-queue-char-91ff36/runs/sprint12_bench_20260731/spotter_logs`).
The unit still runs the Sprint12 v4 build — STEP 0 IS MANDATORY.

## Step 0 — deploy the Sprint13 build (~10 min, needs network restored)

1. Wake the Pi: write `reset` to the SPOT-33507C `cmd.txt` (Spotter
   power-cycles, bus bounces, Pi boots and runs a cycle).
2. Catch it awake over SSH (LAN-direct), disarm: back up crontab, then
   `crontab -r` equivalent per bmcam-field-update flow.
3. `tools/rc_field_update.sh` to branch sha `42acce0`
   (claude/sprint13-console-help-cfg-966167). Expect drift = sprint13
   files only. Keep the bmcam003 profile (now win=12).
4. Sanity on-unit: `python3 rc_progressive_jpeg.py --print-config`
   → tables v5 path loads, max_run_time_min=12, everything source=yaml.

## Step 1 — printf echo proof (T1 close-out, ~5 min)

With the unit awake and a cycle running (or `--bench-commands`), inject
on the console:

    bm pub bmcam/cmd {"id":901,"c":"help"} 1 1

(use the spam pattern if single-shot misses the subscribe frame).
EXPECT: ~143 reference lines printed on the USB console via
spotter/printf + the ack `{"id":901,"ok":1,...}`.
Frame layout is VERIFIED against upstream bm_core/bm_common_messages
source (v1 struct, matching our proven fprintf framing — DESIGN
D-S13-9). The one remaining unknown is behavioral: whether v2.16.6
echoes printf publications on the USB console at all. If the ack
arrives but nothing prints, that answer is no — stop and rethink
transport with Nick (framing is not the suspect).

## Step 2 — Nick's readability pass (gate 3)

At his own terminal (screen /dev/cu.usbmodemSPOT_33507C1 115200 or the
monitor tail):

    bm pub bmcam/cmd {"id":902,"c":"help"} 1 1
    bm pub bmcam/cmd {"id":903,"c":"cfg"} 1 1

cfg must show: win 12 min, halt ON (power savings), window 10:00-15:00,
timezone America/New_York — every row `config file`.

## Step 3 — live capture trigger (Nick types it, from help's own text)

    bm pub bmcam/cmd {"id":904,"c":"trg","v":2} 1 1

Ack = ARMED. Then `reset` → the trigger boot: window gate BYPASSED,
live camera capture, full transmit. EXPECT image COMPLETE at the
Spotter, then the Sofar row (backend lag 13–30 min is normal).

## Step 4 — reef reference transfer

    bm pub bmcam/cmd {"id":905,"c":"trg","v":3} 1 1

then `reset`. EXPECT: "camera skipped this boot", reef reference
transmitted, persisted src untouched.

## Step 5 — leave field-normal + close out

- `cfg` one last time: no stray overrides (all `config file`),
  pending trigger none.
- Re-arm from the saved crontab backup; verify @reboot line; unit will
  real-halt at next cycle end.
- Artifacts → runs/sprint13_demo_<date>/ (console log slices, cfg
  captures, Sofar screenshots); tick TRACKER §3/§4; PR → development.

## If something misfires

- No ack at all: command sent before the daemon's subscribe frame —
  re-send (dedupe makes repeats free). First-byte-eaten: echo-verify.
- help prints garbled/interleaved with cycle logs: raise
  console_line_delay_s (daemon arg, 0.05 provisional) and note the
  measured value in DESIGN D-S13-9.
- trg image blocked: it can't be the window (trigger bypasses it) —
  check cycle log for capture/encode errors.

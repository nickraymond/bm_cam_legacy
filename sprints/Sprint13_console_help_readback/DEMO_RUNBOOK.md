# Sprint13 morning demo — runbook (bmcam003, USB console)

REHEARSED END-TO-END 2026-08-01 ~07:00-08:00Z — every step below passed
on this exact build (7468bff). Artifacts + step-by-step evidence:
runs/sprint13_bench_20260801/RESULTS.md. Nick's demo is a re-run.

State going in (left by the rehearsal): bmcam003 ARMED (crontab
restored, flock @reboot), box UP, Sprint13 build 7468bff deployed,
win 12 live, command state all-zeros (YAML governs, REAL halt on next
boot), no pending trigger. Two rehearsal images already spent.

## The one operational rule learned the hard way

NEVER start a manual cycle while another cycle may be running — the
older cycle's halt WILL kill the box mid-run. Always use the flock:

    /usr/bin/flock -n /tmp/bmcam_rc_capture.lock python3 -u rc_progressive_jpeg.py ...

(and check `pgrep -f rc_progressive` first). Spotter `reset` is
rate-limited — if "Reboot limit reached, ignoring" appears, wait ~2.5
min and re-send.

## Demo flow (Nick at his terminal)

The camera hears commands while a cycle's daemon is listening. Before
10:00 ET the armed boot cycle is GATED (short, ~20 s of daemon life) —
so for the demo, run a bench cycle over SSH (150 s listen window,
box stays up if hlt 2 is active; first one real-halts):

    ssh pi@bmcam003
    cd /home/pi/BM_Devel_Pi && /usr/bin/flock -n /tmp/bmcam_rc_capture.lock python3 -u rc_progressive_jpeg.py --bench-commands

Then at the Spotter console (screen /dev/cu.usbmodemSPOT_33507C1 115200
or the serial monitor), paste during the cycle — repeat a line if no
ack (repeats are free):

1. Readability pass (gate 3 — yours):
       bm pub bmcam/cmd {"id":950,"c":"help"} 1 1
       bm pub bmcam/cmd {"id":951,"c":"cfg"} 1 1
   EXPECT: full reference prints (~30 s at line pacing); cfg shows
   win 12, halt ON (power savings), window 10:00-15:00, all
   "config file".

2. OPTIONAL keep-up convenience: send hlt 2 first so later cycles
   don't halt the box between steps (restore with hlt 0 at the end):
       bm pub bmcam/cmd {"id":952,"c":"hlt","v":2} 1 1

3. Live capture (your acceptance image 1) — paste from help's own
   QUICK ACTIONS:
       bm pub bmcam/cmd {"id":953,"c":"trg","v":2} 1 1
   Ack = ARMED. Then run a --transmit cycle (or `reset` if armed-boot
   flow preferred):
       cd /home/pi/BM_Devel_Pi && /usr/bin/flock -n /tmp/bmcam_rc_capture.lock python3 -u rc_progressive_jpeg.py --transmit
   EXPECT: "window gate BYPASSED for this boot only" → live image
   COMPLETE on console (rehearsal: 105/105, 108 s) → Sofar row within
   ~13-30 min.

4. Reef reference (image 2): same with {"id":954,"c":"trg","v":3} —
   EXPECT "camera skipped this boot", reference COMPLETE (rehearsal:
   192/192, 196 s).

5. Close-out: hlt 0 (if you sent hlt 2), final cfg (all rows
   "config file", pending none), leave armed. Merge PR #33 only after
   BOTH your images land at Sofar.

## If something misfires

- No ack: command landed outside the listen window — re-send during
  the cycle (watch for "[CMD] subscribed" in the cycle output).
- First-byte-eaten ("m pub"): re-send; dedupe makes repeats free.
- help prints interleaved with cycle logs: cosmetic; raise
  console_line_delay_s if wanted (0.05 s was clean in rehearsal).
- Box halted unexpectedly: `reset` at the Spotter console (respect the
  rate limit), then check cycle log under /home/pi/s13_bench/.

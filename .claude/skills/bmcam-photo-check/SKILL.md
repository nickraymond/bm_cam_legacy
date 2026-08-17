---
name: bmcam-photo-check
description: Quick visual camera verification on any bmcam unit — find the unit (tailnet, LAN fallback), take a safe direct rpicam-still capture (never the RC cycle script on armed units), download the image, and either show it in the session or save it to ~/Downloads with a timestamped name. Use when asked to "take a picture", "check the camera", or visually verify image quality on a bmcamNNN. For full provisioning use bmcam-provision; for code rollouts use bmcam-field-update.
---

# bmcam Photo Check (fast visual camera verification)

Purpose: answer "is the camera on bmcamNNN working, and what does it see?" in
under a minute, without disturbing the unit's runtime state. Proven on
bmcam003 and bmcam004 (2026-08).

## Golden safety rule

**NEVER run `rc_progressive_jpeg.py` (any flag, including `--capture-only`)
for a quick check.** On any unit with `power_halt: enabled` (all armed
field/soak units) the halt runs in a `finally` block after ANY cycle — a
"quick capture" will cleanly HALT the box. Use a direct `rpicam-still` to
`/tmp` instead: no RC config touched, no halt path, no `images/` pollution.

## Step 1 — Find the unit

Try in order (stop at first success):

1. Tailnet name: `ssh -o BatchMode=yes -o ConnectTimeout=10 pi@bmcamNNN`
2. Tailnet IP from `/Applications/Tailscale.app/Contents/MacOS/Tailscale status | grep bmcamNNN`
   (the Mac has no `tailscale` on PATH — use the app binary)
3. LAN mDNS: `pi@bmcamNNN.local`
4. LAN sweep (units often power up seconds before you ask — bus-scheduled
   Spotter power means "offline 6 days" can be "booting right now"):
   ```
   for i in $(seq 1 254); do ping -c 1 -W 300 192.168.86.$i >/dev/null 2>&1 & done; wait
   arp -a | grep -i "2c:cf:67\|b8:27:eb\|dc:a6:32\|e4:5f:01\|d8:3a:dd"
   ```
   then ssh the found IP.

If a host key mismatch blocks you (`Host key verification failed`), the unit
was re-flashed or renamed: `ssh-keygen -R <name-and-ip>` and retry with
`-o StrictHostKeyChecking=accept-new`.

If nothing answers: the unit is unpowered or its Spotter bus is in an OFF
window. Report that and (if wanted) start a background watcher loop — do not
declare the camera broken.

## Step 2 — Pre-capture state check (one ssh)

```
ssh -o BatchMode=yes pi@<unit> 'hostname; uptime; pgrep -af "rc_[rp]|main_pi_camera" || echo "camera free"'
```

- `pgrep` self-match gotcha: the `bash -c` line itself matches loose patterns;
  the `rc_[rp]` character-class trick avoids matching your own command.
- If an RC cycle owns the camera (`rpicam-still`/`rc_progressive_jpeg`
  running): WAIT for it or report — do not fight over `/dev/video*`
  (`Pipeline handler in use by another process`). On an armed unit the box
  will halt at cycle end anyway; catch it on the next power-up.
- Fresh boot (uptime ≤ ~2 min) on an ARMED unit means the `@reboot` cycle may
  start/halt imminently — capture immediately, don't dawdle.

## Step 3 — Capture (native full-res, matches first-light convention)

```
ssh -o BatchMode=yes pi@<unit> 'TS=$(date -u +%Y-%m-%dT%H:%M:%SZ); /usr/bin/rpicam-still -n --timeout 2000 --width 4608 --height 2592 --quality 95 -o /tmp/bmcamNNN_check_$TS.jpg 2>/tmp/bmcamNNN_check.stderr; ls -la /tmp/bmcamNNN_check_*.jpg && echo CAPTURE_OK || tail -5 /tmp/bmcamNNN_check.stderr'
```

Plausibility: a healthy IMX708 native q95 JPEG is roughly **0.8–2.5 MB**.
A few-hundred-KB file usually means lens cap / pitch-dark scene; `no cameras
available` in stderr means ribbon not seated (power down before reseating —
CSI is never hot-plugged).

## Step 4 — Deliver the image (ask user preference if not stated, default a)

Ask once per session if not already known; the user may want either:

**(a) Show in session** (default): scp to the session scratchpad, then send
with SendUserFile (`display: render`), caption = unit, resolution, quality,
file size. Keeps Downloads clean; good for quick "does it look right".

**(b) Save to Downloads**: scp directly to the Mac:
```
scp -q "pi@<unit>:/tmp/bmcamNNN_check_<TS>.jpg" ~/Downloads/bmcamNNN_check_<TS>.jpg
```
Filename already carries the UTC timestamp — keep it. Confirm with
`ls -la ~/Downloads/bmcamNNN_check_*.jpg` and tell the user the exact path.
(Doing BOTH — render in session and save to Downloads — is fine when the
user wants to file the image.)

## Step 5 — Clean up the Pi

```
ssh -o BatchMode=yes pi@<unit> 'rm -f /tmp/bmcamNNN_check_*.jpg /tmp/bmcamNNN_check.stderr && echo cleaned'
```

## Report format

One short block: unit, capture size + resolution + quality, where the image
went, plus any state observations worth flagging (fresh boot / cron state
unexpected / file size implausible / scene notes like "much darker than last
check — encapsulation housing?").

## Known failure modes

- `Operation timed out` on tailnet name but MAC found on LAN → unit just
  booted; use the LAN IP, Tailscale catches up in ~1 min.
- `no cameras available` → ribbon not seated; power down to fix.
- `Pipeline handler in use by another process` → RC cycle or another capture
  owns the camera; wait, never kill a production cycle.
- Tiny file (<300 KB) → dark/blocked scene, not a transfer error; look at it
  before judging the camera.
- Unit halts mid-check → it's an armed unit whose @reboot cycle finished;
  expected behavior, catch it next power window.

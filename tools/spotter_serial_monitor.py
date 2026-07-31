#!/usr/bin/env python3
# filename: spotter_serial_monitor.py
# description: Sprint10 — standalone multi-port Spotter console monitor for a Raspberry Pi.
"""
Spotter serial monitor — runs on a dedicated Raspberry Pi with one or
more Spotter eboxes on USB. Replaces the Mac-side console logger.

What it does
  - Auto-discovers Spotter USB consoles (/dev/serial/by-id/*SPOT*,
    fallback /dev/ttyACM*) and follows them through unplugs, Spotter
    reboots, and USB re-enumeration (reconnect loop per port).
  - Logs every console line, timestamped, to a per-port daily file:
        <log-root>/<port-id>/console_YYYYMMDD.log
  - Extracts noteworthy events (queue overflows, Notecard fill %,
    remote mailbox commands, reboot votes, errors) into a shared
        <log-root>/events.log        (one grep-able stream)
  - Command injection: writing a line to <log-root>/<port-id>/cmd.txt
    sends it to that Spotter's console once (file is emptied after
    send) — same FIFO convention as the bench sessions, e.g.:
        echo "note sync" > /home/pi/spotter_logs/SPOT-33507C/cmd.txt
  - Optional periodic `note sync` per port (--sync-min N) that only
    fires when the console has been transmit-idle >30 s (the
    sync-during-transmit blackout, finding 007).

Install on the monitoring Pi
    sudo apt install -y python3-serial   # or: pip3 install pyserial
    mkdir -p /home/pi/spotter_logs
    python3 spotter_serial_monitor.py --log-root /home/pi/spotter_logs

Run at boot (systemd):
    sudo tee /etc/systemd/system/spotter-monitor.service >/dev/null <<'UNIT'
    [Unit]
    Description=Spotter serial console monitor
    After=multi-user.target
    [Service]
    ExecStart=/usr/bin/python3 /home/pi/spotter_serial_monitor.py --log-root /home/pi/spotter_logs --sync-min 15
    Restart=always
    RestartSec=5
    User=pi
    [Install]
    WantedBy=multi-user.target
    UNIT
    sudo systemctl enable --now spotter-monitor

Outputs are plain text; pull them with scp/rsync, or tail events.log.
Known limitations: console-only visibility (the Spotter side of each
ebox); the camera Pis are separate devices. One process handles all
ports; ~zero CPU. Tested patterns come from the 2026-07-27/28 bench
sessions (see runs/sprint10_soak_20260727/incidents.md).
"""

import argparse
import glob
import os
import re
import threading
import time

try:
    import serial
except ImportError:
    raise SystemExit("pyserial missing: sudo apt install python3-serial")

BAUD = 115200
# A live Spotter publishes `power | ...` every ~10 s, so silence this long
# means the port is dead even though the fd is still open. See the watchdog
# in PortMonitor.run().
SILENT_RECONNECT_S = 120.0
EVENT_PATTERNS = re.compile(
    r"Queue MS_Q.*full|Unable to submit|Notecard is [0-9]+\.|"
    r"Remote message|rebootctl|Reboot limit|\[ERROR\]|Sync request|"
    r"Spotter.*v[0-9]+\.[0-9]+")
# Transmit-activity marker: used to hold periodic syncs while sending
TX_MARKER = "Submitted spotter/transmit-data"


def discover_ports():
    """Spotter console device paths, keyed by a stable short id.

    Linux (monitoring Pi): /dev/serial/by-id/*SPOT*, else bare /dev/ttyACM*.
    macOS (bench Mac):     /dev/cu.usbmodemSPOT_<serial><iface>, e.g.
                           /dev/cu.usbmodemSPOT_33507C1. macOS appends the
                           USB interface index, so one trailing digit is
                           stripped; '_' is normalized to '-' so the id
                           matches the SPOT-33507C form used everywhere
                           else (analyzer --spotter-id, run folders,
                           DEV_LOG). Added 2026-07-28 for Phase E, whose
                           Spotters hang off the Mac, not a monitoring Pi.
    """
    ports = {}
    for p in glob.glob("/dev/serial/by-id/*"):
        base = os.path.basename(p)
        if "SPOT" in base.upper():
            m = re.search(r"(SPOT[-_][0-9A-Za-z]+)", base)
            ports[(m.group(1) if m else base).replace("_", "-")] = \
                os.path.realpath(p)
    for p in sorted(glob.glob("/dev/cu.usbmodem*SPOT*")):  # macOS
        m = re.search(r"(SPOT[-_][0-9A-Za-z]+?)\d?$", os.path.basename(p))
        if m:
            ports[m.group(1).replace("_", "-")] = p
    if not ports:  # fallback: bare ACM devices, id by device name
        for p in sorted(glob.glob("/dev/ttyACM*")):
            ports[os.path.basename(p)] = p
    return ports


class PortMonitor(threading.Thread):
    def __init__(self, port_id, dev, log_root, sync_min, events_lock):
        super().__init__(daemon=True)
        self.port_id, self.dev = port_id, dev
        self.dir = os.path.join(log_root, port_id)
        os.makedirs(self.dir, exist_ok=True)
        self.cmd_path = os.path.join(self.dir, "cmd.txt")
        open(self.cmd_path, "a").close()
        self.events_path = os.path.join(log_root, "events.log")
        self.events_lock = events_lock
        self.sync_min = sync_min
        self.last_tx = 0.0
        self.last_sync = time.time()

    def _event(self, line):
        with self.events_lock:
            with open(self.events_path, "a") as f:
                f.write(f"{self._ts()} [{self.port_id}] {line}\n")

    @staticmethod
    def _ts():
        return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())

    def _logfile(self):
        return os.path.join(
            self.dir, f"console_{time.strftime('%Y%m%d', time.gmtime())}.log")

    def _pending_cmd(self):
        try:
            with open(self.cmd_path, "r+") as f:
                cmd = f.read().strip()
                if cmd:
                    f.seek(0)
                    f.truncate()
                return cmd
        except OSError:
            return ""

    def run(self):
        while True:  # reconnect loop — survives reboots/re-enumeration
            try:
                ser = serial.Serial(self.dev, BAUD, timeout=0.2)
            except (serial.SerialException, OSError):
                self._refresh_dev()
                time.sleep(3)
                continue
            self._event("port OPEN")
            buf = b""
            last_data = time.time()
            try:
                while True:
                    chunk = ser.read(4096)
                    if chunk:
                        last_data = time.time()
                        buf += chunk
                        while b"\n" in buf:
                            raw, buf = buf.split(b"\n", 1)
                            line = raw.decode("utf-8", "replace").rstrip()
                            with open(self._logfile(), "a") as f:
                                f.write(f"{self._ts()} {line}\n")
                            if TX_MARKER in line:
                                self.last_tx = time.time()
                            if EVENT_PATTERNS.search(line):
                                self._event(line)
                    # WATCHDOG (added 2026-07-29 after a silent overnight
                    # stall). If the host sleeps, the USB serial fd stays
                    # OPEN but dead: read() returns b"" forever and never
                    # raises, so the exception-driven reconnect below never
                    # fires and the monitor logs nothing until someone
                    # notices hours later. That is exactly what happened on
                    # 2026-07-29 — the Mac entered Maintenance Sleep at
                    # 01:41 PDT and BOTH consoles went silent for 45 min.
                    # A healthy Spotter emits a `power |` publish every 10 s,
                    # so this threshold cannot fire on a live port.
                    elif time.time() - last_data > SILENT_RECONNECT_S:
                        self._event(
                            f"port SILENT >{SILENT_RECONNECT_S:.0f}s — "
                            f"forcing reconnect (host sleep or dead fd)")
                        raise OSError("silent port watchdog")
                    cmd = self._pending_cmd()
                    if cmd:
                        ser.write((cmd + "\n").encode("ascii", "ignore"))
                        self._event(f">>> SENT: {cmd}")
                    if self.sync_min and \
                            time.time() - self.last_sync > self.sync_min * 60 and \
                            time.time() - self.last_tx > 30:
                        ser.write(b"note sync\n")
                        self.last_sync = time.time()
                        self._event(">>> SENT: note sync (periodic, tx-idle)")
            except (serial.SerialException, OSError):
                self._event("port LOST — reconnecting")
                try:
                    ser.close()
                except Exception:
                    pass
                self._refresh_dev()
                time.sleep(3)

    def _refresh_dev(self):
        """Device path can change on re-enumeration; re-resolve by id."""
        ports = discover_ports()
        if self.port_id in ports:
            self.dev = ports[self.port_id]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    ap.add_argument("--log-root", default="/home/pi/spotter_logs")
    ap.add_argument("--sync-min", type=float, default=0,
                    help="periodic tx-idle 'note sync' every N minutes "
                         "(0 = off)")
    args = ap.parse_args()
    os.makedirs(args.log_root, exist_ok=True)
    events_lock = threading.Lock()
    running = {}
    print(f"[monitor] log root {args.log_root}; scanning for Spotter ports")
    while True:
        for port_id, dev in discover_ports().items():
            if port_id not in running:
                print(f"[monitor] starting {port_id} on {dev}")
                t = PortMonitor(port_id, dev, args.log_root,
                                args.sync_min, events_lock)
                t.start()
                running[port_id] = t
        time.sleep(10)


if __name__ == "__main__":
    main()

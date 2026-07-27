#!/usr/bin/env python3
# filename: mock_mote.py
# description: Sprint10 §4 Phase A — PTY mock mote for the command daemon.
"""
Sprint10 Phase A harness: pretend to be the BM mote on a PTY pair.

Creates a PTY (no socat needed; works on macOS + Linux), prints the
slave device path, then behaves like the mote side of the Pi UART:
  - sends command frames (production wire format) on a schedule
  - decodes and prints every frame the daemon sends back (subscribes,
    acks on spotter/transmit-data, fprintf logs)

Point the daemon/RC at the printed device, e.g.:
  python3 tools/mock_mote.py --send '{"id":1,"c":"roi","v":2}' \\
      --send-after 3 '{"id":2,"c":"ping"}' --listen 30
  # other terminal (or same host bench config):
  #   camera_schedule.yaml: uart_port: <printed /dev/ttys00N>
  #   python3 BM_Devel_Pi/rc_progressive_jpeg.py --bench-commands ...

Outputs: one loud line per event (frame sent, frame received + decode),
plus a summary (frames in/out, acks seen) and exit 0/1 so it can gate
scripts. Inputs are payload JSON strings; they are framed exactly like
the REAL mote delivers them (raw pub frame + CRC16, no COBS — verified
against bmcam003 captures 2026-07-27), while daemon->mote replies are
decoded as COBS (the real outbound format).

PTY has no UART timing — this tests framing/flow, not latency.
"""

import argparse
import json
import os
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import types  # noqa: E402

try:
    import serial  # noqa: F401
except ImportError:  # PTY mode needs no pyserial; stub for bm_serial import
    _stub = types.ModuleType("serial")
    _stub.Serial = object
    sys.modules["serial"] = _stub

from bm_frame_decoder import (  # noqa: E402
    build_raw_pub_frame,
    cobs_decode,
    parse_pub_frame,
    verify_crc,
)

MOCK_NODE_ID = 0xB33FB33FB33FB33F


class _PtyWriter:
    def __init__(self, fd):
        self.fd = fd

    def write(self, data):
        return os.write(self.fd, data)


def build_command_frame(payload_json, topic):
    # RAW mote->Pi format (Phase B capture 2026-07-27): the real serial
    # bridge writes pub frames without COBS or delimiters.
    return build_raw_pub_frame(MOCK_NODE_ID, topic, payload_json)


def describe_frame(block):
    """Decode one 0x00-delimited block from the daemon side, loudly."""
    packet = cobs_decode(block)
    if packet is None:
        return f"undecodable block ({len(block)} B)"
    crc_ok = verify_crc(packet)
    if len(packet) >= 4 and packet[0] == 0x03:
        topic = packet[6:].decode("utf-8", "replace")
        return f"SUBSCRIBE topic={topic!r} crc_ok={crc_ok}"
    frame = parse_pub_frame(packet)
    if frame is None:
        return f"non-pub packet type=0x{packet[0]:02x} ({len(packet)} B) crc_ok={crc_ok}"
    topic = frame["topic"].decode("utf-8", "replace")
    payload = frame["payload"]
    if topic == "spotter/transmit-data" and len(payload) >= 1:
        return (f"TRANSMIT-DATA net=0x{payload[0]:02x} crc_ok={crc_ok} "
                f"payload={payload[1:129]!r}")
    return f"PUB topic={topic!r} crc_ok={crc_ok} payload={payload[:96]!r}"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[1])
    parser.add_argument("--topic", default="bmcam/cmd")
    parser.add_argument("--send", action="append", default=[],
                        metavar="JSON", help="command payload to send at start")
    parser.add_argument("--send-after", nargs=2, action="append", default=[],
                        metavar=("SECONDS", "JSON"),
                        help="command payload to send after a delay")
    parser.add_argument("--listen", type=float, default=30.0,
                        help="seconds to stay up (default 30)")
    args = parser.parse_args(argv)

    # Line-buffer stdout even when piped: scripted consumers read the
    # pty path from our first line while we are still running.
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except AttributeError:
        pass

    controller_fd, device_fd = os.openpty()
    # Raw mode is essential: default tty line discipline echoes input
    # and translates CR/LF, which corrupts binary COBS frames (pyserial
    # does this implicitly on real ports).
    import tty

    tty.setraw(device_fd)
    device_path = os.ttyname(device_fd)
    print(f"[MOTE] pty ready — point the daemon at: {device_path}")
    print(f"[MOTE] topic={args.topic} listen={args.listen}s")
    os.set_blocking(controller_fd, False)

    scheduled = [(0.0, payload) for payload in args.send]
    scheduled += [(float(delay), payload) for delay, payload in args.send_after]
    scheduled.sort(key=lambda item: item[0])

    start = time.monotonic()
    buffer = bytearray()
    stats = {"sent": 0, "frames_in": 0, "acks": 0}

    while time.monotonic() - start < args.listen:
        now = time.monotonic() - start
        while scheduled and scheduled[0][0] <= now:
            _t, payload = scheduled.pop(0)
            json.loads(payload)  # fail fast on operator typos
            frame = build_command_frame(payload, args.topic)
            os.write(controller_fd, frame)
            stats["sent"] += 1
            print(f"[MOTE] t={now:6.2f}s sent command: {payload}")
        try:
            chunk = os.read(controller_fd, 4096)
        except BlockingIOError:
            chunk = b""
        if chunk:
            buffer.extend(chunk)
            while True:
                delim = buffer.find(b"\x00")
                if delim < 0:
                    break
                block = bytes(buffer[:delim])
                del buffer[: delim + 1]
                if not block:
                    continue
                stats["frames_in"] += 1
                text = describe_frame(block)
                if '"ok":' in text:
                    stats["acks"] += 1
                print(f"[MOTE] t={now:6.2f}s rx frame: {text}")
        time.sleep(0.05)

    print(f"[MOTE] done: sent={stats['sent']} frames_in={stats['frames_in']} "
          f"acks={stats['acks']}")
    ok = stats["sent"] == 0 or stats["acks"] >= stats["sent"]
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())

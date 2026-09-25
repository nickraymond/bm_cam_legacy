#!/usr/bin/env python3
# filename: world.py
# description: Sprint26 S1 — the fake world the golden-vector harness runs the real RC runtime in.
"""
The fake world for golden vectors (DESIGN_supervisor.md §8.1).

The real runtime (`rc_progressive_jpeg.main`) runs unmodified. Everything it
touches outside the process is replaced here, and every interaction lands in ONE
ordered trace, so a refactor that changes a byte on the wire, opens the UART a
second time, or shells out to something new shows up as a trace diff:

  serial.Serial      -> FakeSerial: OPEN/CLOSE events + every write decoded
                        (COBS -> CRC -> pub/sub frame) into a readable line
  the Spotter        -> scripted inbound: answers a spotter/utc-time subscribe
                        with a clock frame; delivers bmcam/cmd payloads at
                        scripted moments (on subscribe, after the Nth transmit,
                        when a transmit line contains some text, at a fake time)
  subprocess.*       -> the camera app writes a real native JPEG + fixed
                        libcamera metadata; vcgencmd answers a fixed temperature;
                        date / halt / network scripts are recorded, not run;
                        ANYTHING ELSE fails the run loudly
  wall clock         -> datetime.now() frozen at the scripted Spotter UTC,
                        advancing only with the fake monotonic clock
  monotonic + sleep  -> FakeClock; sleep() advances it and then waits until the
                        daemon's reader thread has consumed everything delivered,
                        so command pickup is deterministic
  host identity      -> hostname "bmcam-golden", sha via BM_CAM_SOFTWARE_SHA,
                        fixed storage-health numbers

Trace line kinds (one event per line, tmp paths shown as {TMP}):
  OPEN / CLOSE       a serial port opened or closed (port, baud, timeout)
  W                  a frame written to the Spotter: `tx.<nt> <payload>`,
                     `printf <text>`, `sub <topic>`, or `raw <hex>`
  RX                 a frame the fake Spotter delivered to the Pi
  CAM                the camera command line (and its exit code)
  SETCLOCK / HALT / NET / VREC / VFIT   recorded side effects
Payload bytes are escaped: printable ASCII as-is, backslash as \\\\, others \\xNN.

Known limitations: one physical UART is modelled as "deliver inbound to every
open port that has a read timeout" (a second descriptor would also receive it —
the trace shows the second OPEN either way). Video recording and the x264 fit
are faked at function level (the recorder's own argv is not traced yet).
"""

import datetime as _dt
import json
import os
import shutil
import struct
import subprocess
import threading
import time

SPOTTER_NODE = 0xF365           # node id the fake Spotter sends from
PI_NODE = 0xC0FFEEEEF0CACC1A    # BristlemouthSerial default node id

# Pinned VALUES for collect_storage_health(). The harness keeps the key set the
# runtime under test really returns (so main's runtime still reports the
# HEIC-era buffer_dir_bytes / zero_byte_heic_count) and replaces only the values;
# a key missing here fails the run loudly (a new storage field needs a value).
FIXED_STORAGE = {
    "sd_total_bytes": 31_000_000_000,
    "sd_used_bytes": 9_000_000_000,
    "sd_free_bytes": 22_000_000_000,
    "sd_used_pct": 29.03,
    "images_dir_bytes": 123_456_789,
    "cron_logs_dir_bytes": 4_567_890,
    "buffer_dir_bytes": 0,              # retired in development (DESIGN W1); main still sends it
    "zero_byte_heic_count": 0,          # retired in development (DESIGN W1); main still sends it
}

# What a real rpicam-still --metadata file carries (the fields the END message reads).
FIXED_LIBCAMERA_METADATA = {
    "ExposureTime": 12000, "AnalogueGain": 1.5, "DigitalGain": 1.0,
    "ColourGains": [1.8, 1.6], "ColourTemperature": 5200, "Lux": 350.0,
    "SensorTemperature": 41.0, "LensPosition": 1.82, "FocusFoM": 900,
    "ScalerCrop": [0, 0, 4608, 2592], "AfState": 0,
}


def esc(data):
    """Bytes -> one-line readable text (printable ASCII kept, rest \\xNN)."""
    out = []
    for b in data:
        if b == 0x5C:
            out.append("\\\\")
        elif 32 <= b < 127:
            out.append(chr(b))
        else:
            out.append(f"\\x{b:02x}")
    return "".join(out)


# ---------------------------------------------------------------------------
# Trace
# ---------------------------------------------------------------------------

class Trace:
    def __init__(self, tmp):
        self.lines = []
        self._lock = threading.Lock()
        self._roots = sorted({tmp, os.path.realpath(tmp)}, key=len, reverse=True)

    def norm(self, text):
        for root in self._roots:
            text = text.replace(root, "{TMP}")
        return text

    def add(self, kind, text=""):
        with self._lock:
            self.lines.append(f"{kind} {self.norm(str(text))}".rstrip())


# ---------------------------------------------------------------------------
# Frame decoding (Pi -> Spotter writes)
# ---------------------------------------------------------------------------

def decode_write(data, decoder):
    """Split one uart.write() into COBS blocks and render each frame."""
    lines = []
    for block in data.split(b"\x00"):
        if not block:
            continue
        pkt = decoder.cobs_decode(block)
        if pkt is None:
            lines.append("raw " + block.hex())
            continue
        if not decoder.verify_crc(pkt):
            lines.append("badcrc " + pkt.hex())
            continue
        kind = pkt[0]
        if kind == 0x03 and len(pkt) >= 6:              # BM_SERIAL_SUB
            tlen = int.from_bytes(pkt[4:6], "little")
            lines.append("sub " + esc(pkt[6:6 + tlen]))
            continue
        if kind != 0x02 or len(pkt) < 16:                # not a pub frame
            lines.append("raw " + pkt.hex())
            continue
        node = int.from_bytes(pkt[4:12], "little")
        prefix = "" if node == PI_NODE else f"node={node:x} "
        tlen = int.from_bytes(pkt[14:16], "little")
        topic, payload = pkt[16:16 + tlen], pkt[16 + tlen:]
        if topic == b"spotter/transmit-data" and payload:
            lines.append(f"{prefix}tx.{payload[0]:02x} " + esc(payload[1:]))
        elif topic == b"spotter/printf" and len(payload) >= 12:
            lines.append(f"{prefix}printf " + esc(payload[12:]))
        elif topic == b"spotter/fprintf":
            lines.append(f"{prefix}fprintf " + esc(payload))
        else:
            lines.append(f"{prefix}pub {esc(topic)} " + esc(payload))
    return lines


# ---------------------------------------------------------------------------
# Serial
# ---------------------------------------------------------------------------

class FakeSerial:
    """Stands in for serial.Serial. Inbound bytes come only from the World."""

    def __init__(self, port=None, baudrate=9600, timeout=None, **_kw):
        self.port, self.baudrate, self.timeout = port, baudrate, timeout
        self.is_open = True
        self._rx = bytearray()
        self._cond = threading.Condition()
        self._processing = False
        self._opener = threading.get_ident()
        self.async_reader = False           # read from a thread other than the opener
        WORLD.opened(self)

    # --- pyserial surface ---------------------------------------------------------
    def write(self, data):
        data = bytes(data)
        WORLD.on_write(self, data)
        return len(data)

    def read(self, n=1):
        if threading.get_ident() != self._opener:
            self.async_reader = True
        with self._cond:
            self._processing = False
            self._cond.notify_all()
            if not self._rx:
                self._cond.wait(0.005)
            if not self._rx:
                return b""
            out = bytes(self._rx[:n])
            del self._rx[:n]
            self._processing = True          # until the reader comes back for more
            return out

    def reset_input_buffer(self):
        with self._cond:
            self._rx.clear()

    def flush(self):
        pass

    @property
    def in_waiting(self):
        return len(self._rx)

    def close(self):
        if self.is_open:
            self.is_open = False
            WORLD.closed(self)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    # --- world side ------------------------------------------------------------------
    def feed(self, data):
        with self._cond:
            self._rx.extend(data)
            self._cond.notify_all()

    def idle(self):
        with self._cond:
            return not self._rx and not self._processing


# ---------------------------------------------------------------------------
# Clock
# ---------------------------------------------------------------------------

class FakeClock:
    START = 1000.0

    def __init__(self):
        self.t = self.START

    def __call__(self):
        return self.t

    def elapsed(self):
        return self.t - self.START

    def sleep(self, seconds):
        self.t += max(0.0, float(seconds))
        WORLD.fire("at_clock")
        WORLD.wait_idle()


class FrozenDateTime(_dt.datetime):
    """datetime.now() == the scripted Spotter UTC + fake-clock elapsed."""

    @classmethod
    def _utc(cls):
        return WORLD.utc_start + _dt.timedelta(seconds=WORLD.clock.elapsed())

    @classmethod
    def now(cls, tz=None):
        utc = cls._utc()
        return utc.replace(tzinfo=None) if tz is None else utc.astimezone(tz)

    @classmethod
    def utcnow(cls):
        return cls._utc().replace(tzinfo=None)


class FrozenTime:
    """Module-level `time` stand-in: wall-clock calls frozen, the rest real."""

    def __getattr__(self, name):
        return getattr(time, name)

    def time(self):
        return WORLD.utc_start.timestamp() + WORLD.clock.elapsed()

    def gmtime(self, secs=None):
        return time.gmtime(self.time() if secs is None else secs)

    def strftime(self, fmt, t=None):
        return time.strftime(fmt, self.gmtime() if t is None else t)


# ---------------------------------------------------------------------------
# Subprocess
# ---------------------------------------------------------------------------

class _FakePopen:
    def __init__(self, argv, returncode=0, stdout="", stderr=""):
        self.args, self.pid, self.returncode = argv, 4242, returncode
        self._out, self._err = stdout, stderr

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        return self.returncode

    def communicate(self, *a, **k):
        return self._out, self._err

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _argv(cmd):
    return [str(c) for c in (cmd if isinstance(cmd, (list, tuple)) else str(cmd).split())]


def _opt(argv, *names):
    for i, a in enumerate(argv):
        if a in names and i + 1 < len(argv):
            return argv[i + 1]
    return None


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------

class World:
    def __init__(self):
        self.trace = None
        self.clock = FakeClock()
        self.ports = []
        self.rules = []
        self.tx_count = 0
        self.utc_start = None           # aware UTC datetime the Spotter reports at t=0
        self.native_jpeg = None         # what the fake camera "captures"
        self.cam_failures = 0           # fail this many camera invocations first
        self.decoder = None             # bm_frame_decoder module (from the app under test)

    # --- setup -------------------------------------------------------------------------
    def setup(self, tmp, utc_start, native_jpeg, decoder, rules=(), cam_failures=0):
        self.trace = Trace(tmp)
        self.utc_start = utc_start
        self.native_jpeg = native_jpeg
        self.decoder = decoder
        self.rules = [dict(r, fired=False) for r in rules]
        self.cam_failures = int(cam_failures)

    # --- ports ---------------------------------------------------------------------------
    def opened(self, port):
        self.ports.append(port)
        self.trace.add("OPEN", f"port={port.port} baud={port.baudrate} timeout={port.timeout}")

    def closed(self, port):
        self.trace.add("CLOSE", f"port={port.port}")

    def _readers(self):
        return [p for p in self.ports if p.is_open and p.timeout is not None]

    def deliver(self, frame, label):
        self.trace.add("RX", label)
        for port in self._readers():
            port.feed(frame)
        self.wait_idle()

    def wait_idle(self, strict_s=3.0, lax_s=0.3):
        """Block until every reader has consumed what was delivered.

        A port read by another thread (the daemon's reader) must go idle within
        strict_s or the run FAILS. A port with pending bytes and no reader thread
        yet (read_spotter_utc reads on the main thread after its own write) gets a
        short lax wait and is left alone."""
        deadline = time.monotonic() + strict_s
        for port in list(self._readers()):
            limit = deadline if port.async_reader else time.monotonic() + lax_s
            while not port.idle() and time.monotonic() < limit:
                time.sleep(0.001)
            if port.async_reader and not port.idle():
                self.trace.add("NOTE", f"reader on {port.port} not idle after {strict_s}s")
                raise RuntimeError(f"golden harness: reader on {port.port} not idle")

    # --- writes (Pi -> Spotter) ----------------------------------------------------------------
    def on_write(self, port, data):
        self.wait_idle()
        for line in decode_write(data, self.decoder):
            self.trace.add("W", line)
            if line == "sub spotter/utc-time":
                us = int(FrozenDateTime._utc().timestamp() * 1_000_000)
                frame = self.decoder.build_raw_pub_frame(
                    SPOTTER_NODE, "spotter/utc-time", struct.pack("<Q", us))
                self.deliver(frame, f"utc {FrozenDateTime._utc().isoformat()}")
            elif line.startswith("sub "):
                self.fire("on_sub", topic=line[4:])
            elif line.startswith("tx."):
                self.tx_count += 1
                self.fire("after_tx", text=line)

    # --- scripted inbound ----------------------------------------------------------------------
    def fire(self, event, topic=None, text=None):
        for rule in self.rules:
            if rule["fired"]:
                continue
            when = rule["when"]
            hit = (
                (event == "on_sub" and when == "on_sub" and topic == rule.get("topic", "bmcam/cmd"))
                or (event == "after_tx" and when == "after_tx" and self.tx_count == rule["n"])
                or (event == "after_tx" and when == "tx_contains" and rule["text"] in (text or ""))
                or (event == "at_clock" and when == "at_clock" and self.clock.elapsed() >= rule["t"])
            )
            if hit:
                rule["fired"] = True
                payload = rule["payload"]
                raw = payload if isinstance(payload, str) else json.dumps(payload, separators=(",", ":"))
                frame = self.decoder.build_raw_pub_frame(
                    SPOTTER_NODE, rule.get("topic", "bmcam/cmd"), raw)
                self.deliver(frame, f"cmd {raw}")

    def unfired(self):
        return [r for r in self.rules if not r["fired"]]

    # --- subprocess ----------------------------------------------------------------------------
    def subprocess(self, cmd, kind, **kw):
        argv = _argv(cmd)
        core = argv[:]
        while core and core[0] in ("sudo", "-n"):
            core = core[1:]
        if core and os.path.basename(core[0]) in ("bash", "sh") and len(core) > 1:
            core = core[1:]                     # `bash script.sh`: the script is the command
        name = os.path.basename(core[0]) if core else ""
        shown = " ".join(argv)
        if name in ("rpicam-still", "libcamera-still"):
            if self.cam_failures > 0:
                self.cam_failures -= 1
                self.trace.add("CAM", f"rc=1 {shown}")
                return self._result(kind, argv, 1)
            out = _opt(core, "-o", "--output")
            meta = _opt(core, "--metadata")
            if out:
                shutil.copyfile(self.native_jpeg, out)
            if meta:
                with open(meta, "w", encoding="utf-8") as fh:
                    json.dump(FIXED_LIBCAMERA_METADATA, fh)
            self.trace.add("CAM", f"rc=0 {shown}")
            return self._result(kind, argv, 0)
        if name == "vcgencmd":
            return self._result(kind, argv, 0, stdout="temp=45.0'C\n")
        if name == "hwclock":
            self.trace.add("RTC", shown)
            return self._result(kind, argv, 0)
        if name == "date":
            self.trace.add("SETCLOCK", shown)
            return self._result(kind, argv, 0)
        if name in ("tuned_halt.sh", "halt", "poweroff", "shutdown", "systemctl", "sync"):
            self.trace.add("HALT", shown)
            return self._result(kind, argv, 0)
        if name == "network_ap.sh":
            self.trace.add("NET", shown)
            return self._result(kind, argv, 0)
        self.trace.add("SUBPROC-UNEXPECTED", shown)
        raise RuntimeError(f"golden harness: unexpected subprocess {shown!r}")

    @staticmethod
    def _result(kind, argv, rc, stdout="", stderr=""):
        if kind == "popen":
            return _FakePopen(argv, rc, stdout, stderr)
        if kind == "check_output":
            if rc:
                raise subprocess.CalledProcessError(rc, argv, stdout)
            return stdout
        return subprocess.CompletedProcess(argv, rc, stdout=stdout, stderr=stderr)


WORLD = World()


def install_process_fakes():
    """Patch the process-wide entry points (serial, subprocess, which, hostname)."""
    import socket
    import sys
    import types

    try:
        import serial
    except ImportError:                                  # pyserial absent: stub it
        serial = types.ModuleType("serial")
        sys.modules["serial"] = serial
    serial.Serial = FakeSerial

    subprocess.run = lambda cmd, *a, **k: WORLD.subprocess(cmd, "run", **k)
    subprocess.call = lambda cmd, *a, **k: WORLD.subprocess(cmd, "run", **k).returncode
    subprocess.check_call = lambda cmd, *a, **k: WORLD.subprocess(cmd, "run", **k).returncode
    subprocess.check_output = lambda cmd, *a, **k: WORLD.subprocess(cmd, "check_output", **k)
    subprocess.Popen = lambda cmd, *a, **k: WORLD.subprocess(cmd, "popen", **k)

    real_which = shutil.which
    fake_bins = {"rpicam-still", "libcamera-still", "rpicam-vid", "ffmpeg", "vcgencmd"}
    shutil.which = lambda name, *a, **k: (f"/usr/bin/{name}" if name in fake_bins
                                          else real_which(name, *a, **k))
    socket.gethostname = lambda: "bmcam-golden"


def patch_everywhere(modules, original, replacement):
    """Replace every module-level reference to `original` (a from-import copies it)."""
    for mod in modules:
        for name, value in list(vars(mod).items()):
            if value is original:
                setattr(mod, name, replacement)


def freeze_wall_clock(modules, keep_real_time=()):
    """Point every module-level `datetime` class (from datetime import datetime) and
    every `datetime` MODULE alias (import datetime as dt) at FrozenDateTime, and every
    module-level `time` at FrozenTime, except modules in keep_real_time (their
    `time` stays real: spotter_time_sync loops on a time.time() deadline)."""
    import types
    frozen_time = FrozenTime()
    dt_proxy = types.SimpleNamespace(**{k: getattr(_dt, k) for k in dir(_dt)
                                        if not k.startswith("__")})
    dt_proxy.datetime = FrozenDateTime
    for mod in modules:
        if vars(mod).get("datetime") is _dt.datetime:
            mod.datetime = FrozenDateTime
        for name, value in list(vars(mod).items()):
            if value is _dt:
                setattr(mod, name, dt_proxy)
        if vars(mod).get("time") is time and mod.__name__ not in keep_real_time:
            mod.time = frozen_time

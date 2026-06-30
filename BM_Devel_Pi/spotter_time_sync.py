#!/usr/bin/env python3
"""
spotter_time_sync.py

Configurable UTC time helper for legacy BM cameras: Spotter/BM UTC or Pi RTC.

Important implementation detail:
- Outbound subscribe from Pi to serial_bridge must be an official BM_SERIAL_SUB
  packet, COBS-framed and terminated with 0x00.
- Inbound messages from this custom potted serial_bridge arrive as raw/decoded
  BM serial publish packets, so receive parsing scans the raw byte stream.

This file does not capture images and does not transmit image data.
"""
from __future__ import annotations

import datetime as dt
import os
import struct
import subprocess
import time
from dataclasses import dataclass
from typing import Dict, Optional, Tuple
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import serial

TOPIC = b"spotter/utc-time"

# Friendly deployment aliases. These resolve to real IANA timezone names.
# Keep the IANA timezone as the final runtime value because Python zoneinfo
# handles daylight saving time correctly for those names.
TIMEZONE_PRESETS: Dict[str, str] = {
    "sf": "America/Los_Angeles",
    "san_francisco": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "us_west": "America/Los_Angeles",
    "florida": "America/New_York",
    "florida_keys": "America/New_York",
    "us_east": "America/New_York",
    "hawaii": "Pacific/Honolulu",
    "sydney": "Australia/Sydney",
    "australia_east": "Australia/Sydney",
    "perth": "Australia/Perth",
    "australia_west": "Australia/Perth",
    "utc": "UTC",
}

# Runtime UTC source selection.
# bmcam002: spotter_utc
# bmcam001: rtc
VALID_TIME_SOURCES = {"spotter_utc", "rtc", "system"}


@dataclass
class CameraSchedule:
    # time_source controls where UTC comes from before applying the local window.
    #   spotter_utc: subscribe to BM/Spotter topic spotter/utc-time
    #   rtc: set/read system clock from Pi hardware RTC using hwclock
    #   system: bench-only; use current Linux system clock
    time_source: str = "spotter_utc"

    timezone: str = "America/Los_Angeles"
    timezone_preset: str = ""
    transmit_start: str = "12:00"
    transmit_end: str = "15:00"
    # Backward-compatible field name retained for existing main_pi_camera.py.
    enforce_spotter_time_window: bool = True
    # New generic alias. Either key in YAML can control the same behavior.
    enforce_time_window: bool = True

    set_system_clock_from_spotter: bool = True
    spotter_time_timeout_seconds: int = 60
    allow_system_clock_fallback: bool = False
    uart_port: str = "/dev/ttyAMA0"
    baudrate: int = 115200

    # Hardware RTC source config. Used when time_source: rtc.
    rtc_hwclock_path: str = "/usr/sbin/hwclock"
    set_system_clock_from_rtc: bool = True
    rtc_require_plausible_after_utc: str = "2026-01-01T00:00:00+00:00"

    resolution_key: str = "720p"
    image_quality: int = 25


def _parse_bool(value: str) -> bool:
    return value.strip().lower() in {"true", "yes", "1", "on"}


def _strip_yaml_value(value: str) -> str:
    return value.strip().strip('"').strip("'")


def _parse_utc_iso(value: str) -> dt.datetime:
    value = value.strip()
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    parsed = dt.datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.timezone.utc)
    return parsed.astimezone(dt.timezone.utc)


def load_camera_schedule(path: str = "camera_schedule.yaml") -> CameraSchedule:
    """
    Tiny parser for the specific camera_schedule.yaml shape.

    Avoids adding PyYAML to the field unit.
    Supported sections:
      transmit_window:
        start: "HH:MM"
        end: "HH:MM"
      rtc:
        hwclock_path: "/usr/sbin/hwclock"
        set_system_clock_from_rtc: true
        require_plausible_after_utc: "2026-01-01T00:00:00+00:00"
      image:
        resolution_key: "720p"
        image_quality: 25
    """
    cfg = CameraSchedule()
    if not os.path.exists(path):
        return cfg

    section = None
    with open(path, "r", encoding="utf-8") as f:
        for raw in f:
            line = raw.split("#", 1)[0].rstrip()
            if not line.strip():
                continue

            stripped = line.strip()
            if stripped == "transmit_window:":
                section = "transmit_window"
                continue
            if stripped == "rtc:":
                section = "rtc"
                continue
            if stripped == "image:":
                section = "image"
                continue
            if ":" not in stripped:
                continue

            key, value = stripped.split(":", 1)
            key = key.strip()
            value = _strip_yaml_value(value)

            # Any non-indented key returns to top level.
            if not raw.startswith((" ", "\t")) and key not in {"transmit_window", "image"}:
                section = None

            if section == "transmit_window":
                if key == "start":
                    cfg.transmit_start = value
                elif key == "end":
                    cfg.transmit_end = value
                continue

            if section == "rtc":
                if key == "hwclock_path":
                    cfg.rtc_hwclock_path = value
                elif key == "set_system_clock_from_rtc":
                    cfg.set_system_clock_from_rtc = _parse_bool(value)
                elif key == "require_plausible_after_utc":
                    cfg.rtc_require_plausible_after_utc = value
                continue

            if section == "image":
                if key == "resolution_key":
                    cfg.resolution_key = value
                elif key == "image_quality":
                    cfg.image_quality = int(value)
                continue

            if key == "time_source":
                cfg.time_source = value
            elif key == "timezone":
                cfg.timezone = value
            elif key == "timezone_preset":
                cfg.timezone_preset = value
            elif key == "enforce_spotter_time_window":
                cfg.enforce_spotter_time_window = _parse_bool(value)
                cfg.enforce_time_window = cfg.enforce_spotter_time_window
            elif key == "enforce_time_window":
                cfg.enforce_time_window = _parse_bool(value)
                cfg.enforce_spotter_time_window = cfg.enforce_time_window
            elif key == "set_system_clock_from_spotter":
                cfg.set_system_clock_from_spotter = _parse_bool(value)
            elif key == "spotter_time_timeout_seconds":
                cfg.spotter_time_timeout_seconds = int(value)
            elif key == "allow_system_clock_fallback":
                cfg.allow_system_clock_fallback = _parse_bool(value)
            elif key == "uart_port":
                cfg.uart_port = value
            elif key == "baudrate":
                cfg.baudrate = int(value)

    return cfg


def resolve_timezone(cfg: CameraSchedule) -> str:
    """Return the real IANA timezone string from preset or explicit timezone."""
    preset = (cfg.timezone_preset or "").strip()
    if preset:
        key = preset.lower()
        if key not in TIMEZONE_PRESETS:
            valid = ", ".join(sorted(TIMEZONE_PRESETS.keys()))
            raise ValueError(f"Unknown timezone_preset '{preset}'. Valid presets: {valid}")
        return TIMEZONE_PRESETS[key]
    return cfg.timezone


def _parse_hhmm(value: str) -> dt.time:
    try:
        hh, mm = value.split(":", 1)
        return dt.time(hour=int(hh), minute=int(mm))
    except Exception as e:
        raise ValueError(f"Invalid time '{value}'. Expected HH:MM, for example 08:00 or 15:00") from e


def validate_schedule(cfg: CameraSchedule) -> None:
    """Fail early with clear messages before opening UART or camera hardware."""
    cfg.time_source = (cfg.time_source or "").strip().lower()
    if cfg.time_source not in VALID_TIME_SOURCES:
        valid = ", ".join(sorted(VALID_TIME_SOURCES))
        raise ValueError(f"Invalid time_source '{cfg.time_source}'. Valid options: {valid}")

    timezone_name = resolve_timezone(cfg)
    try:
        ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as e:
        raise ValueError(f"Invalid timezone '{timezone_name}'. Use an IANA name like America/Los_Angeles") from e

    _parse_hhmm(cfg.transmit_start)
    _parse_hhmm(cfg.transmit_end)

    if cfg.spotter_time_timeout_seconds <= 0:
        raise ValueError("spotter_time_timeout_seconds must be greater than 0")
    if cfg.baudrate <= 0:
        raise ValueError("baudrate must be greater than 0")
    if not cfg.uart_port:
        raise ValueError("uart_port must not be empty")
    if not (0 <= int(cfg.image_quality) <= 100):
        raise ValueError("image.image_quality must be between 0 and 100")
    if not cfg.resolution_key:
        raise ValueError("image.resolution_key must not be empty")
    if cfg.time_source == "rtc":
        if not cfg.rtc_hwclock_path:
            raise ValueError("rtc.hwclock_path must not be empty")
        _parse_utc_iso(cfg.rtc_require_plausible_after_utc)


def _crc(seed: int, src: bytes) -> int:
    for i in src:
        e = (seed ^ i) & 0xFF
        f = e ^ ((e << 4) & 0xFF)
        seed = (seed >> 8) ^ (((f << 8) & 0xFFFF) ^ ((f << 3) & 0xFFFF)) ^ (f >> 4)
    return seed


def _cobs_encode(in_bytes: bytes) -> bytes:
    final_zero = True
    out_bytes = bytearray()
    idx = 0
    search_start_idx = 0

    for in_char in in_bytes:
        if in_char == 0:
            final_zero = True
            out_bytes.append(idx - search_start_idx + 1)
            out_bytes += in_bytes[search_start_idx:idx]
            search_start_idx = idx + 1
        else:
            if idx - search_start_idx == 0xFD:
                final_zero = False
                out_bytes.append(0xFF)
                out_bytes += in_bytes[search_start_idx:idx + 1]
                search_start_idx = idx + 1
        idx += 1

    if idx != search_start_idx or final_zero:
        out_bytes.append(idx - search_start_idx + 1)
        out_bytes += in_bytes[search_start_idx:idx]

    return bytes(out_bytes)


def _finalize_packet(packet: bytearray) -> bytes:
    checksum = _crc(0, packet)
    packet[2] = checksum & 0xFF
    packet[3] = (checksum >> 8) & 0xFF
    return _cobs_encode(packet) + b"\x00"


def _build_subscribe_frame(topic: bytes) -> bytes:
    """
    Official BM_SERIAL_SUB shape:
    03 00 00 00 + topic_len_le_u16 + topic
    """
    packet = bytearray.fromhex("03000000") + len(topic).to_bytes(2, "little") + topic
    return _finalize_packet(packet)


def _utc_from_us(utc_us: int) -> dt.datetime:
    return dt.datetime.fromtimestamp(utc_us / 1_000_000.0, tz=dt.timezone.utc)


def _find_clock_payload(buffer: bytes) -> Optional[Tuple[int, int, dt.datetime]]:
    start = 0
    while True:
        idx = buffer.find(TOPIC, start)
        if idx < 0:
            return None

        payload_start = idx + len(TOPIC)
        payload_end = payload_start + 8
        if len(buffer) < payload_end:
            return None

        payload = buffer[payload_start:payload_end]
        utc_us = struct.unpack("<Q", payload)[0]
        min_us = int(dt.datetime(2020, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1_000_000)
        max_us = int(dt.datetime(2035, 1, 1, tzinfo=dt.timezone.utc).timestamp() * 1_000_000)

        if min_us <= utc_us <= max_us:
            return idx, utc_us, _utc_from_us(utc_us)

        start = idx + 1


def read_spotter_utc(
    timeout_seconds: int = 60,
    port: str = "/dev/ttyAMA0",
    baudrate: int = 115200,
    verbose: bool = False,
) -> dt.datetime:
    if verbose:
        print(f"[SYNC] opening UART port={port} baudrate={baudrate}")
        print(f"[SYNC] sending official BM_SERIAL_SUB for {TOPIC.decode()}")

    with serial.Serial(port, baudrate=baudrate, timeout=0.1) as ser:
        try:
            ser.reset_input_buffer()
        except Exception:
            pass

        frame = _build_subscribe_frame(TOPIC)
        wrote = ser.write(frame)
        ser.flush()

        if verbose:
            print(f"[SYNC] subscribe wrote={wrote} frame_bytes={len(frame)} frame={frame.hex(' ')}")
            print(f"[SYNC] listening up to {timeout_seconds}s for Spotter UTC...")

        deadline = time.time() + timeout_seconds
        buffer = bytearray()

        while time.time() < deadline:
            chunk = ser.read(256)
            if not chunk:
                continue

            if verbose:
                printable = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
                print(f"[SYNC][RAW] rx={len(chunk)} hex={chunk.hex(' ')} ascii={printable}")

            buffer.extend(chunk)
            if len(buffer) > 4096:
                del buffer[: len(buffer) - 4096]

            found = _find_clock_payload(bytes(buffer))
            if found:
                _idx, _utc_us, utc_dt = found
                if verbose:
                    print(f"[SYNC] decoded Spotter UTC: {utc_dt.isoformat()}")
                return utc_dt

    raise TimeoutError(f"No valid {TOPIC.decode()} message received within {timeout_seconds}s")


def set_system_clock_utc(utc_dt: dt.datetime) -> None:
    """
    Set Linux system clock to UTC.

    Uses sudo -n when not root, so it fails fast instead of hanging on a password prompt.
    """
    utc_dt = utc_dt.astimezone(dt.timezone.utc)
    iso = utc_dt.strftime("%Y-%m-%d %H:%M:%S")

    if os.geteuid() == 0:
        cmd = ["date", "-u", "-s", iso]
    else:
        cmd = ["sudo", "-n", "date", "-u", "-s", iso]

    subprocess.run(cmd, check=True)


def set_system_clock_from_rtc(hwclock_path: str = "/usr/sbin/hwclock", verbose: bool = False) -> None:
    """
    Set Linux system time from the hardware RTC.

    The RTC is expected to be kept in UTC. Uses sudo -n when not root, so cron
    will fail fast instead of hanging on a password prompt.
    """
    cmd = [hwclock_path, "-s", "--utc"]
    if os.geteuid() != 0:
        cmd = ["sudo", "-n"] + cmd

    if verbose:
        print(f"[SYNC] setting system clock from RTC: {' '.join(cmd)}")

    subprocess.run(cmd, check=True, capture_output=True, text=True)


def read_rtc_utc_from_system(
    hwclock_path: str = "/usr/sbin/hwclock",
    set_system_clock: bool = True,
    require_plausible_after_utc: str = "2026-01-01T00:00:00+00:00",
    verbose: bool = False,
) -> dt.datetime:
    """
    RTC mode for bmcam001.

    Preferred path:
      1. Run hwclock -s --utc to set Linux system time from the RTC.
      2. Read datetime.now(timezone.utc).
      3. Validate that the result is plausible.

    This avoids parsing hwclock output, which can vary by locale/version.
    """
    if set_system_clock:
        set_system_clock_from_rtc(hwclock_path=hwclock_path, verbose=verbose)
        set_result = "after hwclock -s --utc"
    else:
        set_result = "without setting system clock"

    utc_dt = dt.datetime.now(dt.timezone.utc)
    min_dt = _parse_utc_iso(require_plausible_after_utc)
    if utc_dt < min_dt:
        raise RuntimeError(
            f"RTC/system UTC time is not plausible {set_result}: "
            f"utc_time={utc_dt.isoformat()} min_required={min_dt.isoformat()}"
        )

    if verbose:
        print(f"[SYNC] RTC/system UTC accepted: {utc_dt.isoformat()} ({set_result})")

    return utc_dt


def is_within_local_window(
    utc_dt: dt.datetime,
    timezone_name: str,
    start_hhmm: str,
    end_hhmm: str,
) -> Tuple[bool, dt.datetime]:
    tz = ZoneInfo(timezone_name)
    local_dt = utc_dt.astimezone(tz)
    local_t = local_dt.time()
    start = _parse_hhmm(start_hhmm)
    end = _parse_hhmm(end_hhmm)

    if start <= end:
        allowed = start <= local_t < end
    else:
        # Supports overnight windows, e.g. 22:00-02:00.
        allowed = local_t >= start or local_t < end

    return allowed, local_dt


def should_transmit_now_from_schedule(
    config_path: str = "camera_schedule.yaml",
    verbose: bool = False,
) -> Tuple[bool, Dict[str, str]]:
    cfg = load_camera_schedule(config_path)
    validate_schedule(cfg)
    timezone_name = resolve_timezone(cfg)

    info: Dict[str, str] = {
        "time_source": cfg.time_source,
        "timezone": timezone_name,
        "window": f"{cfg.transmit_start}-{cfg.transmit_end}",
    }
    if cfg.timezone_preset:
        info["timezone_preset"] = cfg.timezone_preset

    if not cfg.enforce_time_window:
        info["source_time"] = "skipped"
        info["reason"] = "Transmit-window check disabled by config"
        return True, info

    if cfg.time_source == "spotter_utc":
        try:
            utc_dt = read_spotter_utc(
                timeout_seconds=cfg.spotter_time_timeout_seconds,
                port=cfg.uart_port,
                baudrate=cfg.baudrate,
                verbose=verbose,
            )
            info["source_time"] = "spotter"
            info["utc_time"] = utc_dt.isoformat()

            if cfg.set_system_clock_from_spotter:
                try:
                    set_system_clock_utc(utc_dt)
                    info["set_system_clock"] = "ok"
                except Exception as e:
                    # Still use Spotter UTC for the window decision.
                    info["set_system_clock"] = f"failed: {e}"

        except Exception as e:
            info["source_time"] = "system"
            info["spotter_time_error"] = str(e)

            if not cfg.allow_system_clock_fallback:
                info["reason"] = f"Spotter time unavailable and fallback disabled: {e}"
                return False, info

            utc_dt = dt.datetime.now(dt.timezone.utc)
            info["utc_time"] = utc_dt.isoformat()

    elif cfg.time_source == "rtc":
        try:
            utc_dt = read_rtc_utc_from_system(
                hwclock_path=cfg.rtc_hwclock_path,
                set_system_clock=cfg.set_system_clock_from_rtc,
                require_plausible_after_utc=cfg.rtc_require_plausible_after_utc,
                verbose=verbose,
            )
            info["source_time"] = "rtc"
            info["utc_time"] = utc_dt.isoformat()
            info["set_system_clock"] = "ok" if cfg.set_system_clock_from_rtc else "not_requested"
        except Exception as e:
            info["source_time"] = "rtc"
            info["rtc_time_error"] = str(e)
            info["reason"] = f"RTC time unavailable or invalid: {e}"
            return False, info

    elif cfg.time_source == "system":
        # Bench-only source. Not recommended for field production unless another
        # service has already synchronized the system clock.
        utc_dt = dt.datetime.now(dt.timezone.utc)
        info["source_time"] = "system"
        info["utc_time"] = utc_dt.isoformat()

    else:
        # validate_schedule should prevent this branch.
        raise ValueError(f"Unsupported time_source: {cfg.time_source}")

    allowed, local_dt = is_within_local_window(
        utc_dt=utc_dt,
        timezone_name=timezone_name,
        start_hhmm=cfg.transmit_start,
        end_hhmm=cfg.transmit_end,
    )

    info["local_time"] = local_dt.isoformat()

    if allowed:
        info["reason"] = (
            f"Within transmit window {cfg.transmit_start}-{cfg.transmit_end} "
            f"{timezone_name}; local_time={local_dt.isoformat()}"
        )
    else:
        info["reason"] = (
            f"Outside transmit window {cfg.transmit_start}-{cfg.transmit_end} "
            f"{timezone_name}; local_time={local_dt.isoformat()}"
        )

    return allowed, info


if __name__ == "__main__":
    allowed, info = should_transmit_now_from_schedule("camera_schedule.yaml", verbose=True)
    print(f"allowed={allowed}")
    for k, v in info.items():
        print(f"{k}: {v}")

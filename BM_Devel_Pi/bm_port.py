#!/usr/bin/env python3
# filename: bm_port.py
# description: Sprint26 S1 — the ONE shared Bristlemouth serial handle + BM transfer settings.
"""
The Pi's single Bristlemouth serial handle, and the transfer settings applied to it.

Before Sprint26 S1 this was the module global `process_image_v2.bm`, which
rc_command_hooks reached into and overwrote so wake status, telemetry and
transmit all used the command daemon's port. It now lives here with an explicit
API, so there is exactly one place a port can come from:

  get()          the installed handle; opens BristlemouthSerial() lazily if none
                 (the no-daemon path, exactly as before)
  open_shared(port, baudrate, timeout)
                 the command daemon's factory opens the one shared port here
  install(bm)    hand over an already-built handle (tests, tools)
  private_read(port, baudrate, timeout)
                 the schedule gate's own short Spotter-time read when no daemon
                 runs (a context manager; opens and closes its own descriptor)
  current()      the handle or None, without opening anything
  close()        close the port; get() after it is REFUSED until new_session()
  new_session()  the port owner (rc_port_owner.PortOwner) starts a runtime

  apply_bm_serial_runtime_settings(configure_serial)  bm_serial YAML block ->
               BUFFER_SIZE / IMAGE_TRANSMIT_DELAY_SECONDS (+ network type on the
               handle when configure_serial)

Sprint26 S3a (DESIGN_supervisor.md §4 "Port", review BLOCKER): before S3a,
close() nulled the handle and the next get() silently opened a SECOND
descriptor with no read timeout, so a long-lived process went deaf after its
first action. Now:
  - get() after close() raises PortRefused (loud line first) instead of
    reopening; the owner calls new_session() once at the start of a runtime.
  - a second concurrent descriptor is refused: open_shared()/install() while
    another handle is held, and private_read() while any handle is held.
The lazy open itself stays (the no-daemon path opens on first use), so every
port open and close is where it was: tests/golden pins them.
"""

import contextlib

import serial

from bm_serial import BristlemouthSerial, load_bm_serial_config

_bm = None
_closed = False          # close() ran in this session: get() must not reopen


class PortRefused(RuntimeError):
    """A second descriptor, or a reopen after close, on the one Spotter UART."""


def _refuse(why):
    print(f"[PORT][ERROR] refused: {why}")
    raise PortRefused(why)


def new_session():
    """Start a runtime's port session: forget an earlier close. Refuses while
    a handle is still open (that would leak a descriptor)."""
    global _closed
    if _bm is not None:
        _refuse("new_session() while the previous port is still open")
    _closed = False


def get():
    """The shared handle; lazily opens BristlemouthSerial() if none is installed.
    Refused after close() in the same session (never a silent reopen)."""
    global _bm
    if _bm is None:
        if _closed:
            _refuse("port use after close (a lazy reopen would be a second, "
                    "timeout-less descriptor)")
        _bm = BristlemouthSerial()
    return _bm


def open_shared(port, baudrate, timeout):
    """Open THE shared port (the command daemon's, with a read timeout) and
    install it as the handle. Refused if a handle is already held or the
    session's port was closed."""
    global _bm
    if _bm is not None:
        _refuse("second shared open while a port is held")
    if _closed:
        _refuse("shared open after close")
    bm = BristlemouthSerial(uart=serial.Serial(port, baudrate, timeout=timeout))
    _bm = bm
    return bm


def install(bm):
    """Make an already-built `bm` the shared handle. Refused if a different
    handle is held (two descriptors on one UART)."""
    global _bm
    if _bm is not None and _bm is not bm:
        _refuse("install() while another port is held")
    _bm = bm


@contextlib.contextmanager
def private_read(port, baudrate, timeout):
    """The schedule gate's own short read (no daemon): opens and closes its own
    descriptor, as before S3a. Refused while a shared handle is held, since
    the two would race for the same inbound bytes."""
    if _bm is not None:
        _refuse("private read while the shared port is held")
    if _closed:
        _refuse("private read after close")
    with serial.Serial(port, baudrate=baudrate, timeout=timeout) as ser:
        yield ser


def current():
    """The shared handle, or None. Never opens a port."""
    return _bm


def close():
    """Close the BM serial if it was ever opened, and end the session: get()
    is refused from here on. Returns 0 (cron exit-code shape)."""
    global _bm, _closed
    _closed = True
    if _bm is None:
        return 0
    try:
        _bm.uart.close()
    finally:
        _bm = None
    return 0


def debug_print(message):
    # Late import: rc_telemetry imports this module.
    from rc_telemetry import debug_print as _debug_print
    return _debug_print(message)


# Safe fallback values if camera_schedule.yaml is missing bm_serial settings.
# For production large-message cellular-only deployments, set these in YAML:
#
# bm_serial:
#   network_type: 0x02
#   image_buffer_size: 960
#   image_transmit_delay_seconds: 16
DEFAULT_BUFFER_SIZE = 300


DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS = 5.0


# Runtime values. These are refreshed from camera_schedule.yaml before each
# image compression/send cycle.
BUFFER_SIZE = DEFAULT_BUFFER_SIZE


IMAGE_TRANSMIT_DELAY_SECONDS = DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS


def _coerce_int_config(name, value, default, min_value=None, max_value=None):
    """Parse an integer config value with bounds and safe fallback."""
    if value is None:
        return default
    try:
        parsed = int(value)
    except Exception:
        debug_print(f"Invalid bm_serial.{name}={value!r}; using default {default}")
        return default

    if min_value is not None and parsed < min_value:
        debug_print(f"bm_serial.{name}={parsed} below minimum {min_value}; using {min_value}")
        return min_value
    if max_value is not None and parsed > max_value:
        debug_print(f"bm_serial.{name}={parsed} above maximum {max_value}; using {max_value}")
        return max_value
    return parsed


def _coerce_float_config(name, value, default, min_value=None, max_value=None):
    """Parse a float config value with bounds and safe fallback."""
    if value is None:
        return default
    try:
        parsed = float(value)
    except Exception:
        debug_print(f"Invalid bm_serial.{name}={value!r}; using default {default}")
        return default

    if min_value is not None and parsed < min_value:
        debug_print(f"bm_serial.{name}={parsed} below minimum {min_value}; using {min_value}")
        return min_value
    if max_value is not None and parsed > max_value:
        debug_print(f"bm_serial.{name}={parsed} above maximum {max_value}; using {max_value}")
        return max_value
    return parsed


def apply_bm_serial_runtime_settings(configure_serial=False):
    """Load BM serial image-transfer settings from camera_schedule.yaml.

    The local deployment config block is:

    bm_serial:
      network_type: 0x02
      image_buffer_size: 960
      image_transmit_delay_seconds: 16

    network_type:
      0x01 / 1 = legacy sat/cell fallback queue
      0x02 / 2 = cellular-only queue for larger payload testing
    """
    global BUFFER_SIZE, IMAGE_TRANSMIT_DELAY_SECONDS

    cfg = load_bm_serial_config()

    # Keep the limits broad enough for development, but avoid accidental
    # pathological values if YAML is mistyped.
    BUFFER_SIZE = _coerce_int_config(
        "image_buffer_size",
        cfg.get("image_buffer_size"),
        DEFAULT_BUFFER_SIZE,
        min_value=1,
        max_value=1200,
    )
    IMAGE_TRANSMIT_DELAY_SECONDS = _coerce_float_config(
        "image_transmit_delay_seconds",
        cfg.get("image_transmit_delay_seconds"),
        DEFAULT_IMAGE_TRANSMIT_DELAY_SECONDS,
        min_value=0,
        max_value=120,
    )

    network_type = cfg.get("network_type")
    network_description = f"configured {network_type}" if network_type is not None else "default"
    network_value = network_type

    # BristlemouthSerial owns parsing/validation for network_type, but only
    # instantiate it when actually preparing to transmit. Compression-only tests
    # only need buffer size and delay.
    if configure_serial:
        serial = get()
        try:
            serial.set_network_type(network_type)
        except Exception as exc:
            debug_print(
                f"Invalid bm_serial.network_type={network_type!r}; "
                f"keeping {serial.describe_network_type()}: {exc}"
            )
        network_value = serial.get_network_type_value()
        network_description = serial.describe_network_type()

    settings = {
        "network_type": network_value,
        "network_description": network_description,
        "image_buffer_size": BUFFER_SIZE,
        "image_transmit_delay_seconds": IMAGE_TRANSMIT_DELAY_SECONDS,
    }
    debug_print(
        "Runtime BM transfer settings: "
        f"network={settings['network_description']}; "
        f"image_buffer_size={settings['image_buffer_size']}; "
        f"image_transmit_delay_seconds={settings['image_transmit_delay_seconds']}"
    )
    return settings

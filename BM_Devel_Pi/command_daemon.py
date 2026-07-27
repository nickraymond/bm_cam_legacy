#!/usr/bin/env python3
# filename: command_daemon.py
# description: Sprint10 — command listener daemon (reader thread + apply/ack on main).
"""
Sprint10 command daemon — bi-directional command flow for the RC cycle.

One CommandDaemon lives inside the per-wake RC process (Q10/D11): the
UART opens ONCE at process start, a single reader thread owns all reads
from t=0, and ALL writes stay on the main thread (subscribes, acks in
pacing slots, image transmit) — single-writer by construction, no lock
needed on the wire (D2/D12).

Thread split (strict, this is the concurrency contract):
  reader thread   reads uart, feeds RawPubScanner (mote->Pi is RAW,
                  not COBS — Phase B finding, see bm_frame_decoder),
                  keeps the rolling
                  raw buffer for the PROVEN clock pattern-scan (D11);
                  enqueues matching command payloads. NEVER writes uart,
                  NEVER touches CommandState.
  main thread     everything else: subscribe writes, process_pending
                  (parse -> dedupe -> persist -> queue ack), drain_acks
                  (bm.spotter_tx), time-sync wait, stop.

Flow per active window (D5 as corrected 2026-07-26):
  start() -> wait_for_spotter_utc() [shared port replaces
  read_spotter_utc's private open] -> listen_window(pre_capture_listen_s)
  -> capture/transmit (drain_acks rides the 1.0 s pacing slots) ->
  final process_pending + drain_acks -> stop() -> early halt as today.

Ack policy (D15): ok=1 acks on persist. If the state file write fails,
the command is NOT acked ok — it re-applies via cloud re-send/dedupe.

Requirements: bm.uart must be opened with a read timeout (the reader
loop polls the stop flag between reads); start() fails loudly if not.

Example (integration wiring lives in rc_progressive_jpeg.py):
  bm = BristlemouthSerial(uart=serial.Serial(port, baud, timeout=0.1))
  daemon = CommandDaemon(bm, CommandState(), topic="bmcam/cmd")
  daemon.start()
  utc = daemon.wait_for_spotter_utc(60)
  daemon.listen_window(120)
  ... capture / transmit(ack_drain_fn=daemon.drain_acks) ...
  daemon.process_pending(); daemon.drain_acks(); daemon.stop()

Known limitations: command topic default is provisional until Q11 /
Phase B `bm pub` verification (D14). Reader-thread death is non-fatal
by design — the capture mission continues without command handling.
"""

import queue
import threading
import time

from bm_frame_decoder import RawPubScanner
from command_messages import build_ack, parse_command
from spotter_time_sync import (
    TOPIC as UTC_TOPIC,
    _build_subscribe_frame,
    _find_clock_payload,
)

try:
    import yaml
except Exception:  # pragma: no cover - same runtime fallback as bm_serial
    yaml = None

# YAML island defaults (D14). Disabled by default: a deploy without the
# island is byte-identical to today's cycle.
DEFAULT_BM_COMMANDS_CONFIG = {
    "enabled": False,
    "topic": "bmcam/cmd",          # provisional until Q11/Phase B
    "pre_capture_listen_s": 120.0,  # Nick 2026-07-26: 1-2 min, tune in Phase B
    "state_path": None,             # None -> command_state.py default
}

# Rolling raw-buffer size for the clock pattern-scan — same 4 KB bound
# as production read_spotter_utc.
_RAW_BUFFER_BYTES = 4096


def _parse_island_fallback(config_path):
    """Line-based parse of the flat bm_commands island for hosts without
    PyYAML (same convention as spotter_time_sync's hand parser; the Pi
    runtime has PyYAML, dev Macs may not). Flat `key: value` pairs only."""
    island = {}
    in_island = False
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            for raw_line in f:
                line = raw_line.rstrip("\n")
                stripped = line.split("#", 1)[0].rstrip()
                if not stripped.strip():
                    continue
                if not line.startswith((" ", "\t")):
                    in_island = stripped.strip() == "bm_commands:"
                    continue
                if not in_island or ":" not in stripped:
                    continue
                key, _, value = stripped.strip().partition(":")
                value = value.strip().strip("'\"")
                lowered = value.lower()
                if lowered in {"true", "yes", "on"}:
                    island[key.strip()] = True
                elif lowered in {"false", "no", "off"}:
                    island[key.strip()] = False
                elif lowered in {"null", "~", ""}:
                    island[key.strip()] = None
                else:
                    island[key.strip()] = value
    except Exception:
        return None
    return island or None


def load_bm_commands_config(config_path):
    """Return the bm_commands YAML island merged over defaults.

    Missing file/keys/parser -> defaults (feature disabled). Bad types
    fall back per-key, loudly, so a config typo can't crash the cycle.
    """
    cfg = dict(DEFAULT_BM_COMMANDS_CONFIG)
    if yaml is None:
        island = _parse_island_fallback(config_path)
    else:
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            island = data.get("bm_commands") if isinstance(data, dict) else None
        except Exception:
            return cfg
    if not isinstance(island, dict):
        return cfg

    cfg["enabled"] = bool(island.get("enabled", cfg["enabled"]))
    topic = island.get("topic", cfg["topic"])
    if isinstance(topic, str) and topic:
        cfg["topic"] = topic
    else:
        print(f"[CMD][WARN] bm_commands.topic={topic!r} invalid; using {cfg['topic']}")
    try:
        listen_s = float(island.get("pre_capture_listen_s", cfg["pre_capture_listen_s"]))
        if listen_s < 0:
            raise ValueError("negative")
        cfg["pre_capture_listen_s"] = listen_s
    except (TypeError, ValueError) as exc:
        print(f"[CMD][WARN] bm_commands.pre_capture_listen_s invalid ({exc}); "
              f"using {cfg['pre_capture_listen_s']}")
    state_path = island.get("state_path", cfg["state_path"])
    if state_path is None or (isinstance(state_path, str) and state_path):
        cfg["state_path"] = state_path
    return cfg


class CommandDaemon:
    """Reader thread + main-thread apply/ack. See module docstring."""

    def __init__(self, bm, state, topic=DEFAULT_BM_COMMANDS_CONFIG["topic"]):
        self.bm = bm              # BristlemouthSerial; uart MUST have a timeout
        self.state = state        # CommandState (main-thread only)
        self.topic = topic
        self.accumulator = RawPubScanner(topic=topic)
        self._inbound = queue.Queue()      # reader -> main: payload bytes
        self._acks = []                    # main-thread only
        self._raw = bytearray()            # rolling buffer for clock scan
        self._raw_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = None
        self.stats = {"read_errors": 0, "applied": 0, "duplicates": 0,
                      "rejected": 0, "unackable": 0, "acks_sent": 0}

    # ------------------------------------------------------------------
    # Lifecycle (main thread)
    # ------------------------------------------------------------------

    def start(self):
        """Subscribe to the command topic and start the reader thread."""
        if getattr(self.bm.uart, "timeout", None) is None:
            raise RuntimeError(
                "command daemon requires bm.uart opened with a read timeout "
                "(e.g. serial.Serial(port, baud, timeout=0.1))"
            )
        try:
            self.bm.uart.reset_input_buffer()
        except Exception:
            pass
        topic_bytes = self.topic.encode("utf-8")
        frame = _build_subscribe_frame(topic_bytes)
        self.bm.uart.write(frame)
        print(f"[CMD] subscribed topic={self.topic} frame_bytes={len(frame)}")

        self._reader = threading.Thread(
            target=self._reader_loop, name="bm-cmd-reader", daemon=True
        )
        self._reader.start()
        print("[CMD] reader thread started")

    def stop(self, join_timeout=2.0):
        """Stop the reader thread. Does NOT close the port (owner does)."""
        self._stop.set()
        if self._reader is not None:
            self._reader.join(timeout=join_timeout)
            if self._reader.is_alive():
                print("[CMD][WARN] reader thread did not stop cleanly")
        print(f"[CMD] stopped: {self.summary_line()}")

    # ------------------------------------------------------------------
    # Reader thread — reads ONLY; never writes, never touches state
    # ------------------------------------------------------------------

    def _reader_loop(self):
        while not self._stop.is_set():
            try:
                chunk = self.bm.uart.read(256)
            except Exception as exc:
                self.stats["read_errors"] += 1
                if self._stop.is_set():
                    break
                print(f"[CMD][WARN] uart read error: {exc}")
                time.sleep(0.5)
                continue
            if not chunk:
                continue
            with self._raw_lock:
                self._raw.extend(chunk)
                if len(self._raw) > _RAW_BUFFER_BYTES:
                    del self._raw[: len(self._raw) - _RAW_BUFFER_BYTES]
            for payload in self.accumulator.feed(chunk):
                self._inbound.put(payload)

    # ------------------------------------------------------------------
    # Time sync over the shared port (main thread; D11)
    # ------------------------------------------------------------------

    def wait_for_spotter_utc(self, timeout_seconds, clock=time.monotonic,
                             sleep_fn=time.sleep):
        """Shared-port replacement for read_spotter_utc: same subscribe
        frame, same rolling-buffer pattern-scan (proven production
        detection logic), but reads come from the daemon's reader thread.
        Returns UTC datetime or raises TimeoutError."""
        frame = _build_subscribe_frame(UTC_TOPIC)
        self.bm.uart.write(frame)
        print(f"[CMD] time-sync subscribe sent; waiting up to {timeout_seconds}s")
        deadline = clock() + timeout_seconds
        while clock() < deadline:
            with self._raw_lock:
                found = _find_clock_payload(bytes(self._raw))
            if found:
                _idx, _utc_us, utc_dt = found
                print(f"[CMD] spotter UTC decoded: {utc_dt.isoformat()}")
                return utc_dt
            sleep_fn(0.1)
        raise TimeoutError(
            f"No valid {UTC_TOPIC.decode()} message within {timeout_seconds}s"
        )

    # ------------------------------------------------------------------
    # Command processing (main thread)
    # ------------------------------------------------------------------

    def process_pending(self):
        """Drain inbound payloads: parse -> dedupe -> persist -> queue ack.

        Returns a list of event dicts (for logs/tests). Never raises on
        payload content; a state-persist failure rejects that command
        (no ok ack -> cloud re-send + dedupe make it safe, D15).
        """
        events = []
        while True:
            try:
                payload = self._inbound.get_nowait()
            except queue.Empty:
                break
            result = parse_command(payload)
            event = {"payload": payload, "result": result}
            if not result["ok"] and result["id"] is None:
                self.stats["unackable"] += 1
                event["action"] = "dropped"
                print(f"[CMD] dropped unackable payload err={result['error']} "
                      f"bytes={payload[:40]!r}")
            elif not result["ok"]:
                self.stats["rejected"] += 1
                event["action"] = "rejected"
                self._queue_ack(result["id"], False, result["error"])
                print(f"[CMD] rejected id={result['id']} err={result['error']}")
            elif self.state.is_duplicate(result["id"]):
                self.stats["duplicates"] += 1
                event["action"] = "duplicate"
                self._queue_ack(result["id"], True)
                print(f"[CMD] duplicate id={result['id']} acked, not re-applied")
            else:
                try:
                    self.state.record(result["id"], result["cmd"], result["value"])
                except Exception as exc:
                    # Persist failed: no ok ack (D15). Loud; extremely rare.
                    self.stats["rejected"] += 1
                    event["action"] = "persist_failed"
                    self._queue_ack(result["id"], False, "err")
                    print(f"[CMD][ERROR] state persist failed for "
                          f"id={result['id']}: {exc}")
                else:
                    self.stats["applied"] += 1
                    event["action"] = "applied"
                    self._queue_ack(result["id"], True)
                    print(f"[CMD] applied id={result['id']} "
                          f"{result['cmd']}={result['value']} "
                          f"st={self.state.settings}")
            events.append(event)
        return events

    def _queue_ack(self, command_id, ok, error=None):
        self._acks.append(build_ack(command_id, ok, self.state.settings, error=error))

    @property
    def pending_acks(self):
        return len(self._acks)

    def drain_acks(self, max_n=None):
        """Send queued acks via the outbound path (main thread only).
        Called from transmit pacing slots (D12) and at idle points.
        Returns the number sent; a send failure re-queues and stops."""
        sent = 0
        while self._acks and (max_n is None or sent < max_n):
            ack = self._acks.pop(0)
            try:
                self.bm.spotter_tx(ack)
            except Exception as exc:
                self._acks.insert(0, ack)
                print(f"[CMD][WARN] ack send failed (requeued): {exc}")
                break
            sent += 1
            self.stats["acks_sent"] += 1
            print(f"[CMD] ack sent: {ack}")
        return sent

    # ------------------------------------------------------------------
    # Pre-capture listen window (main thread; D5 corrected)
    # ------------------------------------------------------------------

    def listen_window(self, seconds, clock=time.monotonic, sleep_fn=time.sleep):
        """Listen/apply/ack for `seconds` before capture. Commands that
        land here govern THIS window's capture (roi/foc/awb/exp; win
        governs the next cycle — the budget is already charged)."""
        print(f"[CMD] pre-capture listen window: {seconds:.0f}s")
        deadline = clock() + seconds
        events = []
        while clock() < deadline:
            events.extend(self.process_pending())
            self.drain_acks()
            sleep_fn(0.2)
        print(f"[CMD] listen window done: {len(events)} command(s) processed")
        return events

    def summary_line(self):
        s = dict(self.stats)
        s.update(self.accumulator.stats)
        return ("applied={applied} dup={duplicates} rejected={rejected} "
                "unackable={unackable} acks={acks_sent} frames={matched} "
                "sig_hits={candidates} crc_scan_fail={crc_scan_fail} "
                "bad_start={bad_start} read_err={read_errors}").format(**s)

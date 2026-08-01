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

Flow per active window (Sprint11 C1-C4; supersedes the Sprint10 order):
  start() -> wait_for_spotter_utc() [shared port replaces
  read_spotter_utc's private open] -> capture -> encode -> phase wait ->
  transmit (process_pending rides the pacing slots; acks are DEFERRED when
  defer_acks_during_transmit, C3/D5) -> ack flush ->
  listen_window(post_transmit_listen_s) [C4/D6] -> stop() -> halt.

There is no longer a pre-capture listen window (C1/D2). Commands apply
from cached state on the NEXT boot, which is already how `win` behaved.

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
from command_tables import QUERY_COMMANDS
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
#
# Sprint11: `pre_capture_listen_s` is GONE (C1/D2). It was the single
# largest cause of blackout collisions -- 90 s of listening pushed transmit
# start from ~:01:00 to ~:03:10, so a 194 s burst crossed the :05:00
# boundary at ~62 % through (measured first-gap mean: 65.5 %). It also
# listened at the one time commands never arrive: finding 006 showed the
# mailbox drain fires 1-4 min AFTER the cycle ends. Replaced by
# `post_transmit_listen_s` (C4/D6).
DEFAULT_BM_COMMANDS_CONFIG = {
    "enabled": False,
    "topic": "bmcam/cmd",          # provisional until Q11/Phase B
    "post_transmit_listen_s": 150.0,   # C4/D6 tail; ~0.017 Wh at ~0.5 W
    "defer_acks_during_transmit": False,   # C3/D5; off == Sprint10 wire
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
    if "pre_capture_listen_s" in island:
        # Loud, not silent: a stale config would otherwise look like it was
        # applied. The key does nothing since Sprint11 C1 (see D2).
        print("[CMD][WARN] bm_commands.pre_capture_listen_s is IGNORED since "
              "Sprint11 (the pre-capture listen window was deleted, D2). "
              "Use post_transmit_listen_s instead.")
    try:
        tail_s = float(island.get("post_transmit_listen_s",
                                  cfg["post_transmit_listen_s"]))
        if tail_s < 0:
            raise ValueError("negative")
        cfg["post_transmit_listen_s"] = tail_s
    except (TypeError, ValueError) as exc:
        print(f"[CMD][WARN] bm_commands.post_transmit_listen_s invalid ({exc}); "
              f"using {cfg['post_transmit_listen_s']}")
    cfg["defer_acks_during_transmit"] = bool(
        island.get("defer_acks_during_transmit",
                   cfg["defer_acks_during_transmit"]))
    state_path = island.get("state_path", cfg["state_path"])
    if state_path is None or (isinstance(state_path, str) and state_path):
        cfg["state_path"] = state_path
    return cfg


class CommandDaemon:
    """Reader thread + main-thread apply/ack. See module docstring."""

    # Minimum spacing between ack sends, ANY code path. Phase B hardware
    # data (2026-07-27): unpaced ack bursts overran the Spotter's 2-slot
    # queue — 2 of 40 acks dropped SILENTLY (ids 605/616, both mid-burst).
    # 1.0 s is the Sprint09-locked uplink pacing; the same floor applies.
    ACK_INTERVAL_S = 1.0

    # Sprint13: spacing between spotter/printf console lines. These never
    # touch the cellular queue, so the ack pacing floor does not apply —
    # this is only UART/console-buffer courtesy. PROVISIONAL until the
    # bench measures the real limit (a full help is ~143 lines).
    CONSOLE_LINE_DELAY_S = 0.05

    def __init__(self, bm, state, topic=DEFAULT_BM_COMMANDS_CONFIG["topic"],
                 ack_interval_s=ACK_INTERVAL_S, query_render_fn=None,
                 console_line_delay_s=CONSOLE_LINE_DELAY_S):
        self.bm = bm              # BristlemouthSerial; uart MUST have a timeout
        self.state = state        # CommandState (main-thread only)
        self.topic = topic
        self.ack_interval_s = float(ack_interval_s)
        # Sprint13 query commands: render_fn(cmd) -> list of console lines
        # (rc_command_hooks wires it to command_help over the RESOLVED
        # settings). None = queries ack but print nothing (pre-wire shape).
        self.query_render_fn = query_render_fn
        self.console_line_delay_s = float(console_line_delay_s)
        self.accumulator = RawPubScanner(topic=topic)
        self._inbound = queue.Queue()      # reader -> main: payload bytes
        self._acks = []                    # main-thread only
        self._console = []                 # queued console lines (main thread)
        self._raw = bytearray()            # rolling buffer for clock scan
        self._last_ack_ts = None           # pacing clock value of last send
        self._raw_lock = threading.Lock()
        self._stop = threading.Event()
        self._reader = None
        self.stats = {"read_errors": 0, "applied": 0, "duplicates": 0,
                      "rejected": 0, "unackable": 0, "acks_sent": 0,
                      "console_lines_sent": 0}

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
                    # Sprint13: a query's "apply" is queueing its console
                    # response. Duplicates deliberately DON'T re-queue
                    # (the blanket re-send doctrine must not print help
                    # ten times) — the ack alone answers a re-send.
                    if result["cmd"] in QUERY_COMMANDS:
                        self._queue_console_response(result["cmd"])
            events.append(event)
        return events

    def _queue_console_response(self, cmd):
        if self.query_render_fn is None:
            print(f"[CMD][WARN] query '{cmd}' acked but no renderer wired; "
                  "no console output")
            return
        try:
            lines = list(self.query_render_fn(cmd))
        except Exception as exc:
            print(f"[CMD][WARN] query '{cmd}' render failed: {exc}")
            return
        # Blank spacer lines render as stamped empty rows on the Spotter
        # console (Nick, demo morning) — structure comes from the headers
        # and dividers, so drop them at the transport boundary.
        lines = [line for line in lines if line.strip()]
        self._console.extend(lines)
        print(f"[CMD] query '{cmd}': {len(lines)} console line(s) queued")

    def _queue_ack(self, command_id, ok, error=None):
        self._acks.append(build_ack(command_id, ok, self.state.settings, error=error))

    @property
    def pending_acks(self):
        return len(self._acks)

    @property
    def pending_console_lines(self):
        return len(self._console)

    def drain_console(self, max_lines=None, sleep_fn=time.sleep):
        """Send queued help/cfg lines to the Spotter console
        (bm.spotter_print). NOT the cellular queue — no ack pacing floor,
        just a per-line courtesy delay. A send failure re-queues the line
        and stops (same recovery shape as drain_acks). Returns lines sent."""
        sent = 0
        while self._console and (max_lines is None or sent < max_lines):
            line = self._console.pop(0)
            try:
                self.bm.spotter_print(line)
            except Exception as exc:
                self._console.insert(0, line)
                print(f"[CMD][WARN] console line send failed (requeued): {exc}")
                break
            sent += 1
            self.stats["console_lines_sent"] += 1
            if self._console and self.console_line_delay_s > 0:
                sleep_fn(self.console_line_delay_s)
        if sent:
            print(f"[CMD] console: {sent} line(s) sent, "
                  f"{len(self._console)} queued")
        return sent

    def drain_acks(self, max_n=None, clock=time.monotonic):
        """Send queued acks via the outbound path (main thread only).
        Called from transmit pacing slots (D12) and at idle points.

        PACED: at most one ack per ack_interval_s on the wire (Phase B
        hardware data — unpaced bursts silently overflow the Spotter's
        2-slot queue). Callers poll; un-sent acks simply wait for the
        next drain call. `clock` must be the same time base the caller
        paces with (run_cycle passes the cycle clock so off-device
        tests using fake time stay deterministic).

        Returns the number sent; a send failure re-queues and stops."""
        sent = 0
        while self._acks and (max_n is None or sent < max_n):
            now = clock()
            if (self._last_ack_ts is not None
                    and now - self._last_ack_ts < self.ack_interval_s):
                break  # pacing floor; next drain call picks it up
            ack = self._acks.pop(0)
            try:
                self.bm.spotter_tx(ack)
            except Exception as exc:
                self._acks.insert(0, ack)
                print(f"[CMD][WARN] ack send failed (requeued): {exc}")
                break
            self._last_ack_ts = now
            sent += 1
            self.stats["acks_sent"] += 1
            print(f"[CMD] ack sent: {ack}")
        return sent

    # ------------------------------------------------------------------
    # Listen window (main thread)
    #
    # Sprint11 C1/D2: the PRE-capture window is deleted. This now runs as
    # the bounded POST-transmit tail (C4/D6) -- the mailbox drain is
    # triggered by the sync our own transmit initiates and fires 1-4 min
    # AFTER the cycle ends (finding 006), so this is when commands actually
    # arrive. Anything applied here governs the NEXT boot from cached state.
    # ------------------------------------------------------------------

    def listen_window(self, seconds, clock=time.monotonic, sleep_fn=time.sleep,
                      label="listen"):
        """Listen/apply/ack for `seconds`. Also flushes queued acks, which
        is what makes it the natural landing place for C3's deferred ack
        burst. Returns the command events processed."""
        print(f"[CMD] {label} window: {seconds:.0f}s")
        deadline = clock() + seconds
        events = []
        while clock() < deadline:
            events.extend(self.process_pending())
            self.drain_acks(clock=clock)
            self.drain_console(sleep_fn=sleep_fn)
            sleep_fn(0.2)
        print(f"[CMD] {label} window done: {len(events)} command(s) processed, "
              f"{self.pending_acks} ack(s) still queued")
        return events

    def summary_line(self):
        s = dict(self.stats)
        s.update(self.accumulator.stats)
        return ("applied={applied} dup={duplicates} rejected={rejected} "
                "unackable={unackable} acks={acks_sent} frames={matched} "
                "sig_hits={candidates} crc_scan_fail={crc_scan_fail} "
                "bad_start={bad_start} read_err={read_errors}").format(**s)

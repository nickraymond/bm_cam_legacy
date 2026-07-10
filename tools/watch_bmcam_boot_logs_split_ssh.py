#!/usr/bin/env python3
"""
bm_cam_watch_logs.py

Split-view SSH watcher for bmcam001 and bmcam002 RC boot-capture logs.

Purpose:
  Watch bmcam001 and bmcam002 simultaneously while they boot and run
  /home/pi/BM_Devel_Pi/run_capture_cycle.sh. The default remote log glob is:
  /home/pi/BM_Devel_Pi/cron_logs/boot_capture_*.log

v0.3 changes:
  - Suppresses transient SSH error text from the log panes.
  - Shows a spinner/status line while each camera is offline, booting, or not yet reachable.
  - Follows only NEW log lines by default: initial lines = 0.
  - Polls quietly while waiting for the first boot_capture log to exist.
  - Reconnects automatically when a camera drops offline or reboots.

Run from macOS/Linux VS Code terminal:
  python .\bm_cam_watch_logs.py

Useful options:
  python .\bm_cam_watch_logs.py --hosts bmcam001.tail079031.ts.net,bmcam002.tail079031.ts.net
  python .\bm_cam_watch_logs.py --initial-lines 25
  python .\bm_cam_watch_logs.py --plain
"""

from __future__ import annotations

import argparse
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque, Optional

DEFAULT_SSH_EXE = "ssh"
DEFAULT_HOSTS = "bmcam001,bmcam002"
DEFAULT_USER = "pi"
DEFAULT_LOG_GLOB = "/home/pi/BM_Devel_Pi/cron_logs/boot_capture_*.log"
SPINNER_FRAMES = "|/-\\"


def now_stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def clip(text: str, width: int) -> str:
    if width <= 0:
        return ""
    text = text.replace("\t", "    ")
    if len(text) <= width:
        return text.ljust(width)
    if width <= 3:
        return text[:width]
    return (text[: width - 1] + "…").ljust(width)


def split_csv(value: str) -> list[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


def find_ssh_exe(preferred: str | None) -> str:
    if preferred:
        return preferred
    return shutil.which("ssh") or DEFAULT_SSH_EXE

def build_remote_tail_command(log_glob: str, initial_lines: int, poll_sec: int) -> str:
    """
    Remote POSIX-shell command.

    It is intentionally quiet while waiting for a log file. This prevents the
    panes from filling with repeated "waiting" messages when cameras are asleep.
    """
    initial_lines = max(0, int(initial_lines))
    poll_sec = max(1, int(poll_sec))

    return f"""LOG_GLOB='{log_glob}'
INITIAL_LINES='{initial_lines}'
POLL_SEC='{poll_sec}'
echo "===== CONNECTED_TO $(hostname) at $(date -Is 2>/dev/null || date) ====="
while true; do
  LOG="$(ls -t $LOG_GLOB 2>/dev/null | head -1)"
  if [ -z "$LOG" ]; then
    sleep "$POLL_SEC"
    continue
  fi

  echo "===== FOLLOWING $LOG at $(date -Is 2>/dev/null || date) ====="
  tail -n "$INITIAL_LINES" -F "$LOG" 2>/dev/null &
  TAIL_PID="$!"

  while kill -0 "$TAIL_PID" 2>/dev/null; do
    sleep "$POLL_SEC"
    NEW_LOG="$(ls -t $LOG_GLOB 2>/dev/null | head -1)"
    if [ -n "$NEW_LOG" ] && [ "$NEW_LOG" != "$LOG" ]; then
      echo "===== SWITCHING_TO_NEW_LOG $NEW_LOG at $(date -Is 2>/dev/null || date) ====="
      kill "$TAIL_PID" 2>/dev/null || true
      wait "$TAIL_PID" 2>/dev/null || true
      break
    fi
  done
done
"""


@dataclass
class DeviceState:
    host: str
    user: str
    label: str
    lines: Deque[str] = field(default_factory=lambda: deque(maxlen=500))
    status: str = "starting"
    last_event: str = ""
    reconnects: int = 0
    quiet_errors: int = 0
    last_error: str = ""
    proc: Optional[subprocess.Popen[str]] = None
    lock: threading.Lock = field(default_factory=threading.Lock)

    def add_line(self, line: str) -> None:
        clean = line.rstrip("\r\n")
        if not clean:
            return
        with self.lock:
            self.lines.append(clean)
            self.last_event = now_stamp()
            self.status = "connected/streaming"

    def set_status(self, status: str) -> None:
        with self.lock:
            self.status = status
            self.last_event = now_stamp()

    def note_quiet_error(self, error_text: str) -> None:
        clean = " ".join(error_text.strip().split())
        if not clean:
            return
        with self.lock:
            self.quiet_errors += 1
            self.last_error = clean[-160:]
            self.last_event = now_stamp()

    def snapshot(self) -> tuple[str, str, str, list[str], int, int, str]:
        with self.lock:
            return (
                self.label,
                self.status,
                self.last_event,
                list(self.lines),
                self.reconnects,
                self.quiet_errors,
                self.last_error,
            )

    def terminate(self) -> None:
        with self.lock:
            proc = self.proc
        if proc and proc.poll() is None:
            try:
                proc.terminate()
            except Exception:
                pass


def _stderr_drain_worker(proc: subprocess.Popen[str], err_queue: queue.Queue[str], stop_event: threading.Event) -> None:
    """Drain SSH stderr without printing it into the terminal panes."""
    try:
        if proc.stderr is None:
            return
        for line in proc.stderr:
            if stop_event.is_set():
                break
            if line.strip():
                try:
                    err_queue.put_nowait(line.rstrip("\r\n"))
                except queue.Full:
                    pass
    except Exception:
        return


def device_worker(
    device: DeviceState,
    ssh_exe: str,
    log_glob: str,
    initial_lines: int,
    poll_sec: int,
    retry_delay_sec: int,
    stop_event: threading.Event,
    plain: bool,
) -> None:
    remote_cmd = build_remote_tail_command(log_glob, initial_lines, poll_sec)

    while not stop_event.is_set():
        device.set_status("waiting for device")
        cmd = [ssh_exe, f"{device.user}@{device.host}", remote_cmd]

        err_queue: queue.Queue[str] = queue.Queue(maxsize=20)
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,  # important: do not merge noisy SSH errors into stdout
                text=True,
                bufsize=1,
                errors="replace",
            )
        except FileNotFoundError:
            device.set_status(f"ssh executable not found: {ssh_exe}")
            stop_event.wait(retry_delay_sec)
            continue
        except Exception as exc:
            device.set_status(f"launch failed; retrying in {retry_delay_sec}s")
            device.note_quiet_error(str(exc))
            stop_event.wait(retry_delay_sec)
            continue

        with device.lock:
            device.proc = proc
            device.reconnects += 1

        stderr_thread = threading.Thread(
            target=_stderr_drain_worker,
            args=(proc, err_queue, stop_event),
            daemon=True,
        )
        stderr_thread.start()

        saw_stdout = False
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                if stop_event.is_set():
                    break
                saw_stdout = True
                clean = line.rstrip("\r\n")
                device.add_line(clean)
                if plain and clean:
                    print(f"[{device.label}] {clean}", flush=True)

            if stop_event.is_set():
                break

            code = proc.wait(timeout=2)
            # Drain a few quiet errors into status metadata only.
            last_err = ""
            while not err_queue.empty():
                last_err = err_queue.get_nowait()
            if last_err:
                device.note_quiet_error(last_err)

            if saw_stdout:
                device.set_status(f"disconnected; retrying in {retry_delay_sec}s")
            else:
                device.set_status(f"waiting for device; retrying in {retry_delay_sec}s")
        except Exception as exc:
            device.note_quiet_error(str(exc))
            device.set_status(f"stream ended; retrying in {retry_delay_sec}s")
        finally:
            if proc.poll() is None:
                try:
                    proc.terminate()
                except Exception:
                    pass
            with device.lock:
                device.proc = None

        stop_event.wait(retry_delay_sec)

    device.set_status("stopped")


class SplitRenderer:
    def __init__(
        self,
        devices: list[DeviceState],
        refresh_sec: float,
        use_alt_screen: bool = True,
        max_render_lines: int | None = None,
    ) -> None:
        self.devices = devices
        self.refresh_sec = max(0.1, refresh_sec)
        self.use_alt_screen = use_alt_screen
        self.max_render_lines = max_render_lines
        self.start_time = time.time()

    def __enter__(self) -> "SplitRenderer":
        if self.use_alt_screen:
            sys.stdout.write("\x1b[?1049h")
        sys.stdout.write("\x1b[?25l")
        sys.stdout.flush()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        sys.stdout.write("\x1b[?25h")
        if self.use_alt_screen:
            sys.stdout.write("\x1b[?1049l")
        sys.stdout.flush()

    def render_loop(self, stop_event: threading.Event) -> None:
        while not stop_event.is_set():
            self.render_once()
            stop_event.wait(self.refresh_sec)

    def spinner(self) -> str:
        idx = int((time.time() - self.start_time) * 4) % len(SPINNER_FRAMES)
        return SPINNER_FRAMES[idx]

    def render_once(self) -> None:
        term = shutil.get_terminal_size((140, 40))
        width = max(80, term.columns)
        height = max(20, term.lines)
        sep = " │ "
        col_width = max(20, (width - len(sep)) // 2)

        left = self.devices[0] if len(self.devices) >= 1 else None
        right = self.devices[1] if len(self.devices) >= 2 else None

        header_lines = 5
        visible_rows = max(5, height - header_lines)
        if self.max_render_lines is not None:
            visible_rows = min(visible_rows, self.max_render_lines)

        left_block = self._device_block(left, col_width, visible_rows)
        right_block = self._device_block(right, col_width, visible_rows)

        title = f"watch_bmcam_boot_logs_split  Ctrl+C to stop  refresh={self.refresh_sec:.1f}s  {now_stamp()}"
        rule = "─" * min(width, len(title))
        rows = [clip(title, width), clip(rule, width)]

        for lrow, rrow in zip(left_block, right_block):
            rows.append(clip(lrow, col_width) + sep + clip(rrow, col_width))

        sys.stdout.write("\x1b[H")
        sys.stdout.write("\n".join(rows))
        sys.stdout.write("\x1b[J")
        sys.stdout.flush()

    def _device_block(self, device: DeviceState | None, width: int, rows: int) -> list[str]:
        if device is None:
            return [""] * (rows + 4)

        label, status, last_event, lines, reconnects, quiet_errors, last_error = device.snapshot()
        spin = self.spinner()
        if "connected/streaming" not in status:
            status_display = f"{status} {spin}"
        else:
            status_display = status

        title = f"{label} ({device.user})"
        status_line = f"status={status_display}"
        event_line = f"last_event={last_event or 'never'} reconnects={reconnects} quiet_errors={quiet_errors}"
        # Keep errors out of the log body. Show only a compact hint in metadata.
        error_line = f"last_quiet_error={last_error}" if last_error else "last_quiet_error=none"

        body_rows = max(1, rows)
        body = lines[-body_rows:]
        if len(body) < body_rows:
            body = ([""] * (body_rows - len(body))) + body

        return [title, status_line, event_line, error_line, "─" * width, *body]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Split-view SSH watcher for bmcam001/bmcam002 RC boot-capture cron logs."
    )
    parser.add_argument("--hosts", default=DEFAULT_HOSTS, help="Comma-separated hosts. Default: bmcam001,bmcam002")
    parser.add_argument("--user", default=DEFAULT_USER, help="SSH username. Default: pi")
    parser.add_argument("--log-glob", default=DEFAULT_LOG_GLOB, help=f"Remote log glob. Default: {DEFAULT_LOG_GLOB}")
    parser.add_argument(
        "--initial-lines",
        type=int,
        default=0,
        help="Existing lines to show when connecting. Default: 0 means only new lines.",
    )
    parser.add_argument("--buffer-lines", type=int, default=500, help="Lines to keep per device. Default: 500")
    parser.add_argument("--retry-delay-sec", type=int, default=5, help="Reconnect delay. Default: 5")
    parser.add_argument("--remote-poll-sec", type=int, default=3, help="Remote polling interval. Default: 3")
    parser.add_argument("--refresh-sec", type=float, default=0.5, help="Split screen refresh interval. Default: 0.5")
    parser.add_argument("--ssh-exe", default=None, help=r'SSH executable path. Default: "C:\Program Files\SSH\ssh.exe" then PATH.')
    parser.add_argument("--plain", action="store_true", help="No split view; print only new stdout log lines with device prefixes.")
    parser.add_argument("--no-alt-screen", action="store_true", help="Disable alternate terminal screen.")
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    hosts = split_csv(args.hosts)
    if not hosts:
        print("No hosts supplied.", file=sys.stderr)
        return 2

    ssh_exe = find_ssh_exe(args.ssh_exe)
    devices: list[DeviceState] = []
    for host in hosts[:2]:
        label = host.split(".")[0]
        d = DeviceState(host=host, user=args.user, label=label)
        d.lines = deque(maxlen=max(10, int(args.buffer_lines)))
        devices.append(d)

    stop_event = threading.Event()
    threads: list[threading.Thread] = []
    for device in devices:
        t = threading.Thread(
            target=device_worker,
            args=(device, ssh_exe, args.log_glob, args.initial_lines, args.remote_poll_sec, args.retry_delay_sec, stop_event, args.plain),
            daemon=True,
        )
        threads.append(t)
        t.start()

    try:
        if args.plain:
            print(f"[{now_stamp()}] watch_bmcam_boot_logs_split plain mode. Ctrl+C to stop.", flush=True)
            while not stop_event.is_set():
                time.sleep(0.5)
        else:
            with SplitRenderer(devices=devices, refresh_sec=args.refresh_sec, use_alt_screen=not args.no_alt_screen) as renderer:
                renderer.render_loop(stop_event)
    except KeyboardInterrupt:
        pass
    finally:
        stop_event.set()
        for device in devices:
            device.terminate()
        for thread in threads:
            thread.join(timeout=2)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

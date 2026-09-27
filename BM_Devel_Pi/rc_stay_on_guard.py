#!/usr/bin/env python3
# filename: rc_stay_on_guard.py
# description: Sprint26 S3b.4 — stay_on hardening helpers: current RSS, stdout rotation, the stay_on marker.
"""
The pieces of stay_on hardening that are not the loop itself
(DESIGN_supervisor.md §4 "stay_on hardening"; PLAN_S3b.md H7, H9, H10).

  current_rss_kb()        resident memory NOW (VmRSS), not the peak: a ceiling
                          needs a value that can fall again after a restart
  rss_over_ceiling(kb)    the RSS ceiling (placeholder until the bmcam003
                          20-action measurement sets it, H9)
  rotate_stdout_if_big()  a long-lived process rotates its own log: at
                          LOG_ROTATE_BYTES the stdout/stderr descriptors move to
                          <log>.N (os.dup2), and cron_logs keeps the newest
                          LOG_KEEP_FILES rc_cycle_*.log* files
  marker_set/_clear()     /dev/shm/bmcam_stay_on while a stay_on process runs,
                          so the cron wrapper can tell an OOM kill of a stay_on
                          process (restart) from a per_boot death (never loops)

Assumptions: Linux /proc for VmRSS and the stdout path (the Pi). Off-device
(macOS) current_rss_kb falls back to the peak and rotation is skipped.
Never raises: logging and memory checks must not cost the loop.

Example:
  kb = current_rss_kb(); rotate_stdout_if_big(); marker_set()
"""

import glob
import os
import re
import resource
import stat
import sys

RSS_CEILING_KB = 180 * 1024          # H9 placeholder; bmcam003 20-action run decides
LOG_ROTATE_BYTES = 5 * 1024 * 1024   # H10
LOG_KEEP_FILES = 200                 # H10: rc_cycle_*.log* files kept in cron_logs
MARKER_PATH = os.environ.get("BMCAM_STAY_ON_MARKER", "/dev/shm/bmcam_stay_on")


def current_rss_kb():
    """Resident set size now, in kB (/proc/self/status VmRSS). Off-device:
    the peak (ru_maxrss), which is all macOS offers without psutil."""
    try:
        with open("/proc/self/status", "r", encoding="ascii") as fh:
            for line in fh:
                if line.startswith("VmRSS:"):
                    return int(line.split()[1])
    except (OSError, ValueError, IndexError):
        pass
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak // 1024 if os.uname().sysname == "Darwin" else peak


def rss_over_ceiling(rss_kb, ceiling_kb=None):
    return rss_kb is not None and rss_kb > (RSS_CEILING_KB if ceiling_kb is None else ceiling_kb)


def _stdout_path():
    try:
        return os.readlink("/proc/self/fd/1")
    except OSError:
        return None


def rotate_stdout_if_big(limit=LOG_ROTATE_BYTES, keep=LOG_KEEP_FILES):
    """If stdout is a regular file past `limit`, continue in <base>.N (the next
    free N) and prune cron_logs. -> the new path, or None. Never raises."""
    try:
        st = os.fstat(1)
        if not stat.S_ISREG(st.st_mode) or st.st_size < limit:
            return None
        path = _stdout_path()
        if not path or not os.path.isabs(path):
            return None
        base = re.sub(r"\.\d+$", "", path)
        n = 1
        while os.path.exists(f"{base}.{n}"):
            n += 1
        new = f"{base}.{n}"
        print(f"[SUP] log rotated at {st.st_size} B: continues in {os.path.basename(new)}")
        sys.stdout.flush()
        sys.stderr.flush()
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        os.dup2(fd, 1)
        os.dup2(fd, 2)
        os.close(fd)
        print(f"[SUP] log continued from {os.path.basename(path)}")
        prune_logs(os.path.dirname(base), keep)
        return new
    except Exception as exc:
        print(f"[SUP][WARN] log rotation skipped ({type(exc).__name__}: {exc})")
        return None


def prune_logs(log_dir, keep=LOG_KEEP_FILES):
    """Keep the newest `keep` rc_cycle_*.log* files (mtime); -> number removed."""
    try:
        files = sorted(glob.glob(os.path.join(log_dir, "rc_cycle_*.log*")),
                       key=os.path.getmtime, reverse=True)
        gone = 0
        for path in files[keep:]:
            os.remove(path)
            gone += 1
        if gone:
            print(f"[SUP] pruned {gone} old rc_cycle log(s) (keep {keep})")
        return gone
    except Exception as exc:
        print(f"[SUP][WARN] log prune skipped ({type(exc).__name__}: {exc})")
        return 0


def marker_set(path=None):
    path = path or MARKER_PATH
    try:
        with open(path, "w", encoding="ascii") as fh:
            fh.write(f"{os.getpid()}\n")
        return True
    except OSError as exc:
        print(f"[SUP][WARN] stay_on marker {path} not written ({exc}); an OOM kill "
              "will not be restarted by the wrapper")
        return False


def marker_clear(path=None):
    try:
        os.remove(path or MARKER_PATH)
    except OSError:
        pass

#!/usr/bin/env python3
# filename: config_journal.py
# description: Sprint26 S2e — the unit's config change journal (config_journal.jsonl, 2 x 250 lines, fsync, torn-line tolerant).
"""
The on-unit record of every config change, the one that survives when the
backend never heard about a change made on the boat (DESIGN_supervisor.md §6.2).

One JSON object per line:
  {"t": UTC ISO, "src": source, "id": command id or null, "key": v2 path | "v8.<cmd>" | "*",
   "old": ..., "new": ..., "h": config hash or null, "note": optional}
Sources: an id-range name (S4), "v8" (a tables-v8 command, S2-S4), "local_gui",
"revert", "deploy", "field_update", "migrate".

Durability (REVIEW K8):
  - append = one write() of a whole line with O_APPEND, then fsync: a power cut
    leaves at most ONE torn last line, which the reader skips
  - two files of at most 250 lines: when the live file is full it is renamed to
    `<name>.1` (replacing the older one) and the directory fsynced
  - a journal failure never fails the caller's change (logged, returned False):
    the state file is the truth; the journal is the history

Example:
  config_journal.append(path, "v8", key="v8.roi", old=0, new=5, cid=417)
  config_journal.read(path)   # oldest first, both files, torn lines skipped
"""

import datetime as dt
import json
import os

MAX_LINES = 250
NAME = "config_journal.jsonl"


def path_beside(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), NAME)


def _count_lines(path):
    try:
        with open(path, "rb") as fh:
            return fh.read().count(b"\n")
    except OSError:
        return 0


def _ends_torn(path):
    """True if the file's last byte is not a newline (a cut-off append)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, os.SEEK_END)
            if fh.tell() == 0:
                return False
            fh.seek(-1, os.SEEK_END)
            return fh.read(1) != b"\n"
    except OSError:
        return False


def _fsync_dir(dirpath):
    fd = os.open(dirpath, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def append(path, src, key, old=None, new=None, cid=None, h=None, note=None, now=None):
    """Append one entry durably. Returns True, or False after a loud log line."""
    entry = {"t": (now or dt.datetime.now(dt.timezone.utc)).strftime("%Y-%m-%dT%H:%M:%SZ"),
             "src": src, "id": cid, "key": key, "old": old, "new": new, "h": h}
    if note:
        entry["note"] = note
    line = (json.dumps(entry, sort_keys=True, separators=(",", ":"), default=str) + "\n")
    try:
        if _count_lines(path) >= MAX_LINES:
            os.replace(path, path + ".1")
            _fsync_dir(os.path.dirname(os.path.abspath(path)))
        data = line.encode("utf-8")
        if _ends_torn(path):
            data = b"\n" + data                 # never glue onto a torn last line
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        return True
    except OSError as exc:
        print(f"[CFG][WARN] config journal append failed ({path}): {exc}")
        return False


def read(path):
    """All entries, oldest first (`.1` then live). Torn or foreign lines skipped."""
    out = []
    for p in (path + ".1", path):
        try:
            with open(p, "rb") as fh:
                data = fh.read()
        except OSError:
            continue
        for raw in data.split(b"\n"):
            if not raw.strip():
                continue
            try:
                entry = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                continue                         # torn last line after a power cut
            if isinstance(entry, dict) and "src" in entry and "key" in entry:
                out.append(entry)
    return out

#!/usr/bin/env python3
# filename: atomic_io.py
# description: Sprint26 S2 — crash-safe file replace (unique tmp, fsync, rename, fsync dir).
"""
One way to replace a file on the SD card so a power cut at ANY instant leaves
either the old file or the new one, never a torn or empty one
(DESIGN_supervisor.md §6.2, REVIEW K8).

  1. write to a UNIQUE tmp file in the same directory (two writers never
     share a tmp name; v1 CommandState used the fixed name `<path>.tmp`)
  2. flush + fsync the tmp file
  3. os.replace(tmp, path)            (atomic rename on the same filesystem)
  4. fsync the directory              (makes the rename itself durable; v1 skipped this)

On any failure the tmp file is removed and the exception propagates: the
caller must not report success (D15: no ok ack without a persisted state).

Example:
  atomic_io.write_text("/home/pi/BM_Devel_Pi/camera_config.yaml", text)
"""

import json
import os
import tempfile


def fsync_dir(dirpath):
    """fsync a directory so a rename inside it survives a power cut."""
    fd = os.open(dirpath or ".", os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bytes(path, data, mode=0o644):
    dirpath = os.path.dirname(os.path.abspath(path))
    fd, tmp = tempfile.mkstemp(prefix=f".{os.path.basename(path)}.", suffix=".tmp",
                               dir=dirpath)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    fsync_dir(dirpath)


def write_text(path, text, mode=0o644):
    write_bytes(path, text.encode("utf-8"), mode)


def write_json(path, obj, mode=0o644):
    """Compact, key-sorted JSON (stable bytes for the same content)."""
    write_text(path, json.dumps(obj, sort_keys=True, separators=(",", ":")) + "\n", mode)

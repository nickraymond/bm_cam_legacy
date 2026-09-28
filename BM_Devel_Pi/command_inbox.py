#!/usr/bin/env python3
# filename: command_inbox.py
# description: Sprint26 S4 a.5 — durable inbox for commands heard mid-burst: raw append only, bounded, torn-line tolerant.
"""
The mid-burst command inbox (DESIGN §6.2 "Mid-burst commands"; REVIEW X2/R5;
PLAN_S4.md a.5, b.7, R26).

During a burst the pacing pump must not parse, validate, persist state or
start a subprocess (D15 holds; a slot is ~1.3 s). It only appends the raw
payload here. At the next decision point the supervisor drains the inbox:
parse -> validate -> persist -> ack, then removes that entry. A power cut
mid-burst therefore loses nothing that reached the file.

File: `bm_command_inbox.txt` beside the state file (derived, not a setting:
locked by construction). One entry per line:

    <base64 payload> <crc32 of the payload, 8 hex>\\n

A line without its newline, with bad base64, or with a CRC mismatch is torn
and skipped (loudly). The file is deleted when the last entry is removed, so
an idle unit leaves no file behind (goldens pin every leftover file).

Bounds: a payload over 1 KB is refused; at most 64 entries and 16 KB of
payload; past that the OLDEST entry is dropped with a loud log line. A payload
byte-identical to one already held is not stored again (the mote delivers each
command twice, the console forwards a bm pub 4-5 times; R26).

Example:
  ib = Inbox(path_beside("/home/pi/BM_Devel_Pi/bm_command_state_v2.json"))
  ib.append(b'{"id":1000001,"c":"ping"}')
  for payload in ib.entries(): ...; ib.remove(payload)

Known limitations: one writer (the supervisor process). O(entries) per
append (<= 16 KB read), fine for a 1.3 s slot.
"""

import base64
import binascii
import os
import zlib

NAME = "bm_command_inbox.txt"
MAX_PAYLOAD = 1024
MAX_ENTRIES = 64
MAX_BYTES = 16 * 1024


def path_beside(state_path):
    return os.path.join(os.path.dirname(os.path.abspath(state_path)), NAME)


def _line(payload):
    crc = zlib.crc32(payload) & 0xFFFFFFFF
    return base64.b64encode(payload) + b" " + f"{crc:08x}".encode() + b"\n"


def _fsync_dir(path):
    import atomic_io
    atomic_io.fsync_dir(os.path.dirname(os.path.abspath(path)))


class Inbox:
    def __init__(self, path, log=print):
        self.path = path
        self._log = log

    def entries(self):
        """Payloads held, oldest first. Torn lines skipped (loud)."""
        try:
            with open(self.path, "rb") as fh:
                data = fh.read()
        except FileNotFoundError:
            return []
        except OSError as exc:
            self._log(f"[CMD][WARN] inbox {self.path} unreadable: {exc}")
            return []
        out, damaged = [], 0
        lines = data.split(b"\n")
        tail = lines.pop()               # b"" when the file ends with a newline
        if tail:
            damaged += 1
            self._log(f"[CMD][WARN] inbox: torn last line ({len(tail)} B) skipped")
        for raw in lines:
            if not raw:
                continue
            try:
                b64, crc = raw.split(b" ")
                payload = base64.b64decode(b64, validate=True)
                ok = int(crc, 16) == zlib.crc32(payload) & 0xFFFFFFFF
            except (ValueError, binascii.Error):
                ok = False
            if not ok:
                damaged += 1
                self._log(f"[CMD][WARN] inbox: damaged line ({len(raw)} B) skipped")
                continue
            out.append(payload)
        if damaged and not out:
            # Review S4a #7: nothing good left: delete it, or it lingers (and
            # warns) forever because no drain ever calls remove().
            try:
                self._rewrite([])
            except OSError as exc:
                self._log(f"[CMD][WARN] inbox: damaged file not removed: {exc}")
        return out

    def _rewrite(self, payloads):
        """Atomic replace with exactly these entries; no file when empty."""
        if not payloads:
            try:
                os.remove(self.path)
                _fsync_dir(self.path)
            except FileNotFoundError:
                pass
            return
        import atomic_io
        atomic_io.write_bytes(self.path, b"".join(_line(p) for p in payloads))

    def append(self, payload):
        """Store one raw payload durably (burst path: no parse). -> True if
        stored, False if refused (too big) or already held."""
        payload = bytes(payload)
        if len(payload) > MAX_PAYLOAD:
            self._log(f"[CMD][WARN] inbox: {len(payload)} B payload > {MAX_PAYLOAD} B refused")
            return False
        held = self.entries()
        if payload in held:
            return False
        kept = list(held)
        while kept and (len(kept) + 1 > MAX_ENTRIES
                        or sum(len(p) for p in kept) + len(payload) > MAX_BYTES):
            dropped = kept.pop(0)
            self._log(f"[CMD][WARN] inbox full: oldest command dropped "
                      f"({dropped[:60]!r}{'...' if len(dropped) > 60 else ''})")
        if len(kept) != len(held):
            self._rewrite(kept + [payload])
            return True
        needs_nl = False
        try:
            with open(self.path, "rb") as fh:
                fh.seek(0, os.SEEK_END)
                if fh.tell():
                    fh.seek(-1, os.SEEK_END)
                    needs_nl = fh.read(1) != b"\n"
        except FileNotFoundError:
            pass
        new = not os.path.exists(self.path)
        fd = os.open(self.path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o644)
        try:
            os.write(fd, (b"\n" if needs_nl else b"") + _line(payload))
            os.fsync(fd)
        finally:
            os.close(fd)
        if new:
            _fsync_dir(self.path)
        return True

    def remove(self, payload):
        """Drop one entry (after its command was persisted and acked)."""
        payload = bytes(payload)
        held = self.entries()
        if payload in held:
            held.remove(payload)
            self._rewrite(held)

    def clear(self):
        self._rewrite([])

#!/usr/bin/env python3
# filename: test_s4_inbox.py
# description: Sprint26 S4 a.5 — durable mid-burst inbox: order, dedupe, bounds + eviction log, torn lines, delete-when-empty.
"""
Sprint26 S4 commit a.5 (PLAN_S4.md a.5, R26; REVIEW X2/R5).

Pins: entries come back oldest first and byte-exact (binary included); a
byte-identical payload is held once; > 1 KB refused; 64 entries / 16 KB with
the oldest dropped and logged; a torn or damaged line is skipped and the next
append still lands on its own line; removing the last entry deletes the file.

Run (repo root):
  python3 -m unittest tests.test_s4_inbox -v
"""

import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_inbox as I  # noqa: E402


class Inbox(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="inbox_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.log = []
        self.ib = I.Inbox(I.path_beside(os.path.join(self.dir, "bm_command_state_v2.json")),
                          log=self.log.append)

    def test_path_is_derived(self):
        self.assertEqual(self.ib.path, os.path.join(self.dir, I.NAME))

    def test_order_and_bytes(self):
        payloads = [b'{"id":1000001,"c":"ping"}', b"\x00\xff\n binary \n", b'{"id":7,"c":"help"}']
        for p in payloads:
            self.assertTrue(self.ib.append(p))
        self.assertEqual(I.Inbox(self.ib.path).entries(), payloads)

    def test_duplicates_held_once(self):
        p = b'{"id":1000001,"c":"set","kv":{"d":8}}'
        self.assertTrue(self.ib.append(p))
        for _ in range(5):
            self.assertFalse(self.ib.append(p))
        self.assertEqual(self.ib.entries(), [p])

    def test_oversize_refused(self):
        self.assertFalse(self.ib.append(b"x" * (I.MAX_PAYLOAD + 1)))
        self.assertTrue(self.ib.append(b"x" * I.MAX_PAYLOAD))
        self.assertTrue(any("refused" in line for line in self.log))

    def test_entry_bound_drops_oldest_loudly(self):
        for i in range(I.MAX_ENTRIES + 6):
            self.ib.append(b'{"id":%d,"c":"ping"}' % i)
        got = self.ib.entries()
        self.assertEqual(len(got), I.MAX_ENTRIES)
        self.assertEqual(got[0], b'{"id":6,"c":"ping"}')
        self.assertEqual(got[-1], b'{"id":69,"c":"ping"}')
        self.assertEqual(len([m for m in self.log if "inbox full" in m]), 6)

    def test_byte_bound(self):
        for i in range(20):
            self.ib.append(bytes([65 + i]) * 1000)
        got = self.ib.entries()
        self.assertLessEqual(sum(map(len, got)), I.MAX_BYTES)
        self.assertEqual(got[-1], bytes([65 + 19]) * 1000)
        self.assertEqual(len(got), 16)

    def test_torn_tail_and_damaged_lines(self):
        self.ib.append(b"one")
        with open(self.ib.path, "ab") as fh:
            fh.write(b"dG9ybg== 000")            # torn: no newline
        self.assertEqual(self.ib.entries(), [b"one"])
        self.ib.append(b"two")                    # lands on its own line
        self.assertEqual(self.ib.entries(), [b"one", b"two"])
        with open(self.ib.path, "ab") as fh:
            fh.write(b"dHdv 00000000\n!!!notbase64 1\n")   # bad crc, bad base64
        self.assertEqual(self.ib.entries(), [b"one", b"two"])
        self.assertTrue(any("damaged" in m for m in self.log))

    def test_remove_and_delete_when_empty(self):
        for p in (b"a", b"b", b"c"):
            self.ib.append(p)
        self.ib.remove(b"b")
        self.assertEqual(self.ib.entries(), [b"a", b"c"])
        self.ib.remove(b"zzz")                    # not held: no-op
        self.ib.remove(b"a")
        self.ib.remove(b"c")
        self.assertFalse(os.path.exists(self.ib.path))
        self.assertEqual(self.ib.entries(), [])
        self.ib.append(b"d")
        self.ib.clear()
        self.assertFalse(os.path.exists(self.ib.path))


if __name__ == "__main__":
    unittest.main()

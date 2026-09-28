#!/usr/bin/env python3
# filename: test_s4_command_reference.py
# description: Sprint26 S4 c.4 — docs/bmcam_command_reference.md is exactly the generator's output; every error code the dispatcher uses is documented.
"""
Sprint26 S4 commit c.4 (PLAN_S4.md c.4, G15; DESIGN §5.1 "the command reference
doc: generated").

Pins: the committed reference equals tools/gen_command_reference.py's output
(regenerate with `--write` after changing the registry or the wire); every
`e` code the dispatcher and the wire module can send is in ERROR_CODES; every
registry key and short name appears in the doc.

Run (repo root):
  python3 -m unittest tests.test_s4_command_reference -v
"""

import os
import re
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import command_wire as W  # noqa: E402
import config_registry as R  # noqa: E402
import gen_command_reference as G  # noqa: E402


class Reference(unittest.TestCase):
    def test_committed_doc_is_fresh(self):
        with open(G.OUT, encoding="utf-8") as fh:
            committed = fh.read()
        self.assertEqual(committed, G.render(),
                         "docs/bmcam_command_reference.md is stale: run "
                         "tools/gen_command_reference.py --write")

    def test_every_error_code_is_documented(self):
        used = set()
        for name in ("command_v9.py", "command_wire.py"):
            with open(os.path.join(REPO_ROOT, "BM_Devel_Pi", name), encoding="utf-8") as fh:
                src = fh.read()
            used |= set(re.findall(r'Rejected\([^,()]+, "(\w+)"', src))
            used |= set(re.findall(r'_reject\([^,()]+, [^,()]+, "(\w+)"', src))
            used |= set(re.findall(r'_answer\([^,()]+, False, "(\w+)"', src))
        self.assertTrue(used)
        self.assertEqual(used - set(W.ERROR_CODES), set())

    def test_every_key_and_short_name_is_in_the_doc(self):
        text = G.render()
        for key in R.KEYS:
            self.assertIn(f"`{key.path}`", text)
        for short in R.SHORT_NAMES:
            self.assertIn(f"`{short}`", text)


if __name__ == "__main__":
    unittest.main()

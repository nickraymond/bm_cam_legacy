#!/usr/bin/env python3
# filename: test_s4_service_sign.py
# description: Sprint26 S4 a.6 — tools/bm_service_sign.py: signed console line round-trips through command_wire; refusals.
"""
Sprint26 S4 commit a.6 (PLAN_S4.md a.6, R7; DESIGN §6.3 service).

Pins: the printed line is `bm pub bmcam/cmd <json> 1 1` <= 270 B; the unit's
decode + verify_sig accept it with the same key file bytes (64 hex) the
deploy creates; refusals exit 2 (not strict JSON, sig present, id outside the
service range, signed JSON > 248 B) or 3 (key missing / malformed).

Run (repo root):
  python3 -m unittest tests.test_s4_service_sign -v
"""

import contextlib
import io
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

import bm_service_sign as T  # noqa: E402
import command_wire as W  # noqa: E402

TOOL = os.path.join(REPO_ROOT, "tools", "bm_service_sign.py")
CMD = '{"id":100000001,"c":"set","kv":{"uplink.chunk_chars":320}}'


class Sign(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="keys_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        self.hex = secrets.token_hex(32)
        path = os.path.join(self.dir, "bmcam003.key")
        with open(path, "w") as fh:
            fh.write(self.hex + "\n")
        os.chmod(path, 0o600)

    def run_tool(self, *args):
        return subprocess.run([sys.executable, TOOL, "--key-dir", self.dir, *args],
                              capture_output=True, text=True)

    def test_line_round_trips(self):
        r = self.run_tool("bmcam003", CMD)
        self.assertEqual(r.returncode, 0, r.stderr)
        line = r.stdout.strip()
        self.assertTrue(line.startswith("bm pub bmcam/cmd {") and line.endswith("} 1 1"))
        self.assertLessEqual(len(line) + 1, W.MAX_CONSOLE_LINE_BYTES)
        payload = line[len("bm pub bmcam/cmd "):-len(" 1 1")]
        cmd = W.decode(payload)
        unit_key = W.parse_service_key(self.hex + "\n")       # what the unit reads
        self.assertTrue(W.verify_sig(cmd.raw, unit_key))
        self.assertFalse(W.verify_sig(cmd.raw, secrets.token_bytes(32)))

    def test_json_only(self):
        r = self.run_tool("bmcam003", CMD, "--json-only")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(W.verify_sig(W.decode(r.stdout.strip()).raw, bytes.fromhex(self.hex)))

    def test_refusals(self):
        big = '{"id":100000001,"c":"set","kv":{%s}}' % ",".join(
            f'"k{i}":"{"a" * 40}"' for i in range(4))
        cases = [("bmcam003", "not json", 2),
                 ("bmcam003", '{"id":100000001,"c":"set","kv":{"a":1},"sig":"0000000000000000"}', 2),
                 ("bmcam003", '{"id":1000001,"c":"set","kv":{"uplink.chunk_chars":320}}', 2),
                 ("bmcam003", '{"id":true,"c":"set","kv":{"a":1}}', 2),
                 ("bmcam003", big, 2),
                 ("bmcam009", CMD, 3)]
        for host, text, code in cases:
            r = self.run_tool(host, text)
            self.assertEqual(r.returncode, code, (text, r.stdout, r.stderr))
            self.assertEqual(r.stdout, "", text)

    def test_malformed_key(self):
        with open(os.path.join(self.dir, "bad.key"), "w") as fh:
            fh.write("abc\n")
        self.assertEqual(self.run_tool("bad", CMD).returncode, 3)

    def test_loose_key_mode_warns(self):
        os.chmod(os.path.join(self.dir, "bmcam003.key"), 0o644)
        err = io.StringIO()
        with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
            T.main(["--key-dir", self.dir, "bmcam003", CMD])
        self.assertIn("chmod 600", err.getvalue())


class ParseKey(unittest.TestCase):
    def test_cases(self):
        h = "ab" * 32
        self.assertEqual(W.parse_service_key(h), bytes.fromhex(h))
        self.assertEqual(W.parse_service_key(h.upper() + "\n"), bytes.fromhex(h))
        self.assertEqual(W.parse_service_key(h.encode()), bytes.fromhex(h))
        for bad in ("", None, "ab" * 31, "ab" * 33, "zz" * 32, h + " x"):
            self.assertIsNone(W.parse_service_key(bad), bad)


if __name__ == "__main__":
    unittest.main()

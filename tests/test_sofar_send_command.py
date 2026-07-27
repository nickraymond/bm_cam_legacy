#!/usr/bin/env python3
# filename: test_sofar_send_command.py
# description: Sprint10 §7 — unit tests for the Sofar Command API sender.
"""
Sprint10 — tests for tools/sofar_send_command.py (no network, no token).

Pins: console-line construction is byte-identical to the Phase B bench
format, Sofar message-format rules are enforced pre-send, the client-side
rate-limit guard reads the send log correctly, and HTTP responses map to
the right exit codes (202 -> 0, else nonzero) with every attempt logged.

Run (repo root):
  python3 -m unittest tests.test_sofar_send_command -v
"""

import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import sofar_send_command as ssc  # noqa: E402


class TestBuildCommandJson(unittest.TestCase):
    def test_settings_command_compact_wire_format(self):
        # Byte-identical to the Phase B bench payloads (no spaces).
        self.assertEqual(ssc.build_command_json(417, "roi", 2),
                         '{"id":417,"c":"roi","v":2}')

    def test_ping_omits_value(self):
        self.assertEqual(ssc.build_command_json(700, "ping", None),
                         '{"id":700,"c":"ping"}')

    def test_rejects_unknown_command(self):
        with self.assertRaises(ValueError):
            ssc.build_command_json(1, "zoom", 0)

    def test_rejects_out_of_table_value(self):
        with self.assertRaises(ValueError):
            ssc.build_command_json(1, "roi", 99)

    def test_rejects_missing_value_for_settings_command(self):
        with self.assertRaises(ValueError):
            ssc.build_command_json(1, "awb", None)

    def test_rejects_bool_and_out_of_range_ids(self):
        for bad in (True, -1, 2**32, "5"):
            with self.assertRaises(ValueError):
                ssc.build_command_json(bad, "ping", None)


class TestConsoleLine(unittest.TestCase):
    def test_matches_phase_b_bench_format(self):
        line = ssc.build_console_line('{"id":101,"c":"ping"}')
        self.assertEqual(line, 'bm pub bmcam/cmd {"id":101,"c":"ping"} 1 1')

    def test_custom_topic(self):
        self.assertEqual(ssc.build_console_line("{}", "other/topic"),
                         "bm pub other/topic {} 1 1")

    def test_topic_whitespace_rejected(self):
        with self.assertRaises(ValueError):
            ssc.build_console_line("{}", "bad topic")


class TestValidateMessage(unittest.TestCase):
    def test_counts_server_appended_newline(self):
        self.assertEqual(ssc.validate_message("abc"), 4)
        self.assertEqual(ssc.validate_message("abc\n"), 4)

    def test_rejects_tabs_and_non_ascii(self):
        with self.assertRaises(ValueError):
            ssc.validate_message("a\tb")
        with self.assertRaises(ValueError):
            ssc.validate_message("café")

    def test_newline_chaining_allowed(self):
        self.assertEqual(ssc.validate_message("a\nb\n"), 4)

    def test_length_limit_enforced(self):
        ssc.validate_message("x" * 269)  # 269 + server newline = 270: ok
        with self.assertRaises(ValueError):
            ssc.validate_message("x" * 270)

    def test_worst_case_v1_command_fits_easily(self):
        line = ssc.build_console_line(
            ssc.build_command_json(0xFFFFFFFF, "roi", 4))
        self.assertLess(ssc.validate_message(line), 60)


class TestRateLimitGuard(unittest.TestCase):
    def _log(self, records):
        fd, path = tempfile.mkstemp(suffix=".jsonl")
        with os.fdopen(fd, "w") as f:
            for r in records:
                f.write(json.dumps(r) + "\n")
        self.addCleanup(os.unlink, path)
        return path

    def test_missing_log_means_no_last_send(self):
        self.assertIsNone(ssc.load_last_success_ts("/nonexistent/x.jsonl", "S"))

    def test_only_202_for_matching_spotter_counts(self):
        path = self._log([
            {"spotter_id": "SPOT-A", "http_status": 202, "ts": 100.0},
            {"spotter_id": "SPOT-A", "http_status": 400, "ts": 200.0},
            {"spotter_id": "SPOT-B", "http_status": 202, "ts": 300.0},
        ])
        self.assertEqual(ssc.load_last_success_ts(path, "SPOT-A"), 100.0)
        self.assertEqual(ssc.load_last_success_ts(path, "SPOT-B"), 300.0)
        self.assertIsNone(ssc.load_last_success_ts(path, "SPOT-C"))

    def test_torn_tail_line_tolerated(self):
        path = self._log([{"spotter_id": "S", "http_status": 202, "ts": 5.0}])
        with open(path, "a") as f:
            f.write('{"torn')
        self.assertEqual(ssc.load_last_success_ts(path, "S"), 5.0)


class TestMainSendPath(unittest.TestCase):
    """main() with post_command mocked — no network ever."""

    def setUp(self):
        fd, self.log = tempfile.mkstemp(suffix=".jsonl")
        os.close(fd)
        os.unlink(self.log)  # main() must create it
        self.addCleanup(lambda: os.path.exists(self.log) and os.unlink(self.log))
        os.environ[ssc.TOKEN_ENV] = "test-token"
        self.addCleanup(os.environ.pop, ssc.TOKEN_ENV, None)
        self.base = ["--spotter-id", "SPOT-TEST", "--id", "700",
                     "--cmd", "ping", "--send-log", self.log]

    def _run(self, argv, status=202, resp=None):
        resp = resp if resp is not None else {"status": "success",
                                              "message": "enqueued"}
        with mock.patch.object(ssc, "post_command",
                               return_value=(status, resp)) as p:
            rc = ssc.main(argv)
        return rc, p

    def test_dry_run_sends_nothing_logs_nothing(self):
        rc, p = self._run(self.base + ["--dry-run"])
        self.assertEqual(rc, 0)
        p.assert_not_called()
        self.assertFalse(os.path.exists(self.log))

    def test_202_exit_zero_and_logged(self):
        rc, p = self._run(self.base)
        self.assertEqual(rc, 0)
        (spotter, token, body), _ = p.call_args
        self.assertEqual(spotter, "SPOT-TEST")
        self.assertEqual(body, {"telemetry": "cellular",
                                "message": 'bm pub bmcam/cmd '
                                           '{"id":700,"c":"ping"} 1 1'})
        with open(self.log) as f:
            rec = json.loads(f.read())
        self.assertEqual(rec["http_status"], 202)
        self.assertNotIn("token", json.dumps(rec))

    def test_400_exit_nonzero_and_logged(self):
        rc, _ = self._run(self.base, status=400,
                          resp={"status": "bad request", "message": "nope"})
        self.assertEqual(rc, 1)
        with open(self.log) as f:
            self.assertEqual(json.loads(f.read())["http_status"], 400)

    def test_rate_limit_guard_blocks_then_force_overrides(self):
        rc, _ = self._run(self.base)
        self.assertEqual(rc, 0)
        rc, p = self._run(self.base)
        self.assertEqual(rc, 3)  # guard fired, no request made
        p.assert_not_called()
        rc, _ = self._run(self.base + ["--force"])
        self.assertEqual(rc, 0)

    def test_missing_token_refused_before_network(self):
        del os.environ[ssc.TOKEN_ENV]
        rc, p = self._run(self.base)
        self.assertEqual(rc, 2)
        p.assert_not_called()

    def test_clear_queue_alone_is_valid(self):
        rc, p = self._run(["--spotter-id", "SPOT-TEST", "--clear-queue",
                           "--send-log", self.log])
        self.assertEqual(rc, 0)
        (_, _, body), _ = p.call_args
        self.assertEqual(body, {"telemetry": "cellular",
                                "clear_command_queue": True})


if __name__ == "__main__":
    unittest.main()

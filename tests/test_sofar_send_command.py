#!/usr/bin/env python3
# filename: test_sofar_send_command.py
# description: Sprint10 §7 / Sprint26 S4 c.3 — unit tests for the Sofar Command API sender (commands v9).
"""
Tests for tools/sofar_send_command.py (no network, no token).

Pins: v9 JSON built per verb and validated by the unit's own decoder
(command_wire.decode) + the registry, --id range-checked (remote /
service; others only with --allow-any-range), JSON > 248 B and console
line > 270 B refused, console-line construction byte-identical to the
Phase B bench format, Sofar message-format rules enforced pre-send, the
client-side rate-limit guard reading the send log correctly, and HTTP
responses mapping to the right exit codes (202 -> 0, else nonzero) with
every attempt logged.

Run (repo root):
  python3 -m unittest tests.test_sofar_send_command -v
"""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import command_wire as W  # noqa: E402
import sofar_send_command as ssc  # noqa: E402


RID = 1_000_101   # a remote-range id


class TestBuildCommandJson(unittest.TestCase):
    def test_set_compact_wire_format(self):
        self.assertEqual(
            ssc.build_command_json(RID, "set", {"kv": {"power.halt.enabled": False}}),
            '{"id":1000101,"c":"set","kv":{"power.halt.enabled":false}}')

    def test_ping_has_no_fields(self):
        self.assertEqual(ssc.build_command_json(RID, "ping"),
                         '{"id":1000101,"c":"ping"}')

    def test_every_verb_decodes_on_the_unit(self):
        for verb, fields in [("set", {"kv": {"d": 8, "r": [0, 0, 4608, 2592]}}),
                             ("get", {"k": ["mode", "journal"]}),
                             ("reset", {"k": ["video.send"]}), ("reset", {"all": 1}),
                             ("cfm", {"ref": RID - 1}), ("trg", {"v": 2}),
                             ("trg", {"v": 1, "kv": {"m": 150}}), ("hld", {"v": 0}),
                             ("help", {}), ("wap", {"v": 2})]:
            cmd = W.decode(ssc.build_command_json(RID, verb, fields))
            self.assertEqual((cmd.verb, cmd.fields), (verb, fields))

    def test_rejects_retired_v8_verbs(self):
        for verb in ("roi", "cfg", "hlt", "twn"):
            with self.assertRaises(ValueError):
                ssc.build_command_json(RID, verb, {"v": 1})

    def test_rejects_bad_shapes_before_sending(self):
        for verb, fields in [("set", {}), ("get", {"k": []}),
                             ("reset", {"k": ["a"], "all": 1}),
                             ("trg", {"v": 5}), ("hld", {"v": -1}),
                             ("cfm", {"ref": True}), ("set", {"kv": {"d": float("nan")}})]:
            with self.assertRaises(ValueError, msg=(verb, fields)):
                ssc.build_command_json(RID, verb, fields)

    def test_registry_checks(self):
        for kv in ({"nope.key": 1}, {"r": "bad"}, {"commands.runtime": "legacy"},
                   {"uplink.chunk_chars": 320}, {"power.halt.enabled": 1}):
            with self.assertRaises(ValueError, msg=kv):
                ssc.build_command_json(RID, "set", {"kv": kv})
        with self.assertRaises(ValueError):   # not a one-shot key
            ssc.build_command_json(RID, "trg", {"v": 2, "kv": {"mode.run": "stay_on"}})

    def test_signed_service_command_passes_the_registry(self):
        obj = {"id": 100_000_001, "c": "set", "kv": {"uplink.chunk_chars": 320},
               "sig": "0123456789abcdef"}
        self.assertIn('"sig":"0123456789abcdef"', ssc.validate_command(obj))

    def test_id_ranges(self):
        self.assertEqual(ssc.check_id_range(1_000_000), "remote")
        self.assertEqual(ssc.check_id_range(199_999_999), "service")
        for bad in (5, 100_000, 2_000_000_000):          # console, heal, conductor
            with self.assertRaises(ValueError):
                ssc.check_id_range(bad)
            self.assertIsNotNone(ssc.check_id_range(bad, allow_any_range=True))
        for bad in (True, -1, 2**32, "5", 200_000_000):
            with self.assertRaises(ValueError):
                ssc.check_id_range(bad, allow_any_range=True)

    def test_json_over_248_bytes_refused(self):
        kv = {"camera.image_processing.denoise": "a" * 48,
              "camera.image_processing.contrast": 1.0,
              "video.record.encoder.denoise": "auto",
              "camera.focus.mode": "manual",
              "camera.image_processing.hdr": "b" * 48,
              "camera.white_balance.mode": "daylight"}
        with self.assertRaisesRegex(ValueError, "248"):
            ssc.build_command_json(RID, "set", {"kv": kv})

    def test_parse_value_is_json_typed(self):
        self.assertIs(ssc.parse_value("false"), False)
        self.assertEqual(ssc.parse_value("8"), 8)
        self.assertEqual(ssc.parse_value("1.5"), 1.5)
        self.assertIsNone(ssc.parse_value("null"))
        self.assertEqual(ssc.parse_value("[1,2,3,4]"), [1, 2, 3, 4])
        self.assertEqual(ssc.parse_value("10:00"), "10:00")
        self.assertEqual(ssc.parse_value('"8"'), "8")
        self.assertEqual(ssc.parse_kv_arg("d=8"), ("d", 8))
        with self.assertRaises(ValueError):
            ssc.parse_kv_arg("nokey")

    def test_guard_notes(self):
        self.assertIn("STAGED", ssc.guard_notes({"power.halt.enabled": True})[0])
        self.assertEqual(ssc.guard_notes({"power.halt.enabled": False}), [])
        self.assertIn("REVERTS", ssc.guard_notes({"o": "save_local"})[0])


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

    def test_max_json_fits_the_console_line(self):
        # 248 B of JSON + "bm pub bmcam/cmd " + " 1 1" + newline = 270 B
        line = ssc.build_console_line("x" * ssc.MAX_JSON_BYTES)
        self.assertEqual(ssc.validate_message(line), ssc.MAX_MESSAGE_BYTES)


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
        self.base = ["--spotter-id", "SPOT-TEST", "--id", "1000700",
                     "--ping", "--send-log", self.log]

    def _run(self, argv, status=202, resp=None, out=None):
        resp = resp if resp is not None else {"status": "success",
                                              "message": "enqueued"}
        with mock.patch.object(ssc, "post_command",
                               return_value=(status, resp)) as p:
            # Swallow main()'s console output so mocked "[OK] enqueued"
            # lines can't be mistaken for a real Sofar API send.
            with contextlib.redirect_stdout(out if out is not None else io.StringIO()):
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
                                           '{"id":1000700,"c":"ping"} 1 1'})
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

    def _body(self, *flags):
        rc, p = self._run(["--spotter-id", "SPOT-TEST", "--id", "1000800",
                           "--send-log", self.log, "--force"] + list(flags))
        self.assertEqual(rc, 0, flags)
        (_, _, body), _ = p.call_args
        return body["message"][len("bm pub bmcam/cmd "):-len(" 1 1")]

    def test_convenience_flags_build_v9(self):
        self.assertEqual(self._body("--set", "power.halt.enabled=false",
                                    "--set", "d=8"),
                         '{"id":1000800,"c":"set","kv":{"power.halt.enabled":false,"d":8}}')
        self.assertEqual(self._body("--get", "mode", "--get", "journal"),
                         '{"id":1000800,"c":"get","k":["mode","journal"]}')
        self.assertEqual(self._body("--reset", "schedule.window"),
                         '{"id":1000800,"c":"reset","k":["schedule.window"]}')
        self.assertEqual(self._body("--reset-all"), '{"id":1000800,"c":"reset","all":1}')
        self.assertEqual(self._body("--cfm", "1000799"),
                         '{"id":1000800,"c":"cfm","ref":1000799}')
        self.assertEqual(self._body("--trg", "2", "--kv", "d=8"),
                         '{"id":1000800,"c":"trg","v":2,"kv":{"d":8}}')
        self.assertEqual(self._body("--hld", "30"), '{"id":1000800,"c":"hld","v":30}')
        self.assertEqual(self._body("--help-cmd"), '{"id":1000800,"c":"help"}')
        self.assertEqual(self._body("--json", '{"c":"wap","v":1}'),
                         '{"id":1000800,"c":"wap","v":1}')

    def test_refusals_exit_2_without_network(self):
        for flags in (["--id", "5", "--ping"],                       # console range
                      ["--id", "1000900", "--set", "nope.key=1"],   # registry
                      ["--id", "1000900", "--json", '{"c":"roi","v":2}'],   # v8 verb
                      ["--id", "1000900", "--json", '{"id":1000901,"c":"ping"}'],  # id clash
                      ["--id", "1000900", "--json", "{not json"]):
            rc, p = self._run(["--spotter-id", "SPOT-TEST", "--send-log", self.log]
                              + flags)
            self.assertEqual(rc, 2, flags)
            p.assert_not_called()
        self.assertFalse(os.path.exists(self.log))

    def test_allow_any_range(self):
        rc, p = self._run(["--spotter-id", "SPOT-TEST", "--id", "5", "--ping",
                           "--allow-any-range", "--send-log", self.log])
        self.assertEqual(rc, 0)
        p.assert_called_once()

    def test_command_needs_id_and_one_verb(self):
        for flags in (["--ping"], ["--id", "1000900", "--ping", "--help-cmd"],
                      ["--id", "1000900", "--kv", "d=8"],
                      ["--id", "1000900", "--ping", "--json", '{"c":"ping"}']):
            with self.assertRaises(SystemExit), \
                    contextlib.redirect_stderr(io.StringIO()):
                self._run(["--spotter-id", "SPOT-TEST", "--send-log", self.log]
                          + flags)

    def test_raw_message_still_bypasses(self):
        rc, p = self._run(["--spotter-id", "SPOT-TEST", "--send-log", self.log,
                           "--raw-message", "bm pub bmcam/cmd not-json 1 1"])
        self.assertEqual(rc, 0)
        (_, _, body), _ = p.call_args
        self.assertEqual(body["message"], "bm pub bmcam/cmd not-json 1 1")

    def test_token_env_selects_the_token(self):
        # Sprint25 S2b: SPOT-33361C is on the AOML token; --token-env picks it.
        os.environ["SOFAR_API_TOKEN_TEST_OTHER"] = "other-token"
        self.addCleanup(os.environ.pop, "SOFAR_API_TOKEN_TEST_OTHER", None)
        del os.environ[ssc.TOKEN_ENV]
        rc, p = self._run(self.base + ["--token-env", "SOFAR_API_TOKEN_TEST_OTHER"])
        self.assertEqual(rc, 0)
        (_, token, _), _ = p.call_args
        self.assertEqual(token, "other-token")

    def test_token_env_missing_refused_before_network(self):
        rc, p = self._run(self.base + ["--token-env", "SOFAR_API_TOKEN_NOT_SET_XYZ"])
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

#!/usr/bin/env python3
# filename: test_s4_tools_smoke.py
# description: Sprint26 S4 c.3 — tools smoke test: every CLI --help, a dry-run of every v9 verb, the GUI send path against a real v9 dispatcher, reply parsers.
"""
Sprint26 S4 commit c.3 gate (PLAN_S4.md c.3 row: "tools smoke test: every
CLI --help, dry-run build of every verb, GUI server against a fake daemon").

  1. every changed tool's CLI answers --help with exit 0 (subprocess, no
     token in the environment);
  2. sofar_send_command --dry-run builds every v9 verb; each printed console
     line decodes with the unit's own command_wire.decode; a bad id refuses;
  3. the GUI's send path (GuiState.send, "now" mode, Sofar POST mocked)
     feeds a REAL CommandDaemon + command_v9.Dispatcher on a temp V9State
     (tests.test_s4_dispatch.Rig: fake BM port); the unit's acks and <CF>
     lines go back through sofar_poll_acks' row parser into the GUI's ack
     poll, and every command lands acked with the unit's config hash;
  4. sofar_poll_acks and soak_reconcile parse a slim ack, a <CF> line and a
     <HL> line (rc_heal.build_hl_message).

No network: Sofar calls are mocked; no socket is opened.

Run (repo root):
  python3 -m pytest -q tests/test_s4_tools_smoke.py
"""

import ast
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
for _p in ("BM_Devel_Pi", "tools", os.path.join("tools", "bm_command_gui")):
    sys.path.insert(0, os.path.join(REPO_ROOT, _p))

import command_wire as W  # noqa: E402
import rc_heal  # noqa: E402
import server as gui  # noqa: E402
import soak_reconcile as sr  # noqa: E402
import sofar_poll_acks as spa  # noqa: E402
import sofar_send_command as ssc  # noqa: E402
from tests.test_s4_dispatch import Rig  # noqa: E402

TOOLS = ("tools/sofar_send_command.py", "tools/sofar_poll_acks.py",
         "tools/soak_reconcile.py", "tools/soak_command_scheduler.py",
         "tools/bm_cmd_bench_listener.py", "tools/bm_command_gui/server.py")

# One command per v9 verb the tools build (rsd = backend heals, --json only).
VERB_FLAGS = [
    ("set", ["--set", "power.halt.enabled=false", "--set", "d=8"]),
    ("get", ["--get", "mode", "--get", "schedule.window"]),
    ("reset", ["--reset", "still.crop"]),
    ("reset", ["--reset-all"]),
    ("cfm", ["--cfm", "1000100"]),
    ("trg", ["--trg", "2", "--kv", "r=[0,0,4608,2592]"]),
    ("hld", ["--hld", "30"]),
    ("ping", ["--ping"]),
    ("help", ["--help-cmd"]),
    ("wap", ["--json", '{"c":"wap","v":1}']),
    ("rsd", ["--json", '{"c":"rsd","h":[["abc123","0,3-5"]]}']),
]


def clean_env():
    env = dict(os.environ)
    env.pop(ssc.TOKEN_ENV, None)
    return env


def run_tool(args):
    return subprocess.run([sys.executable] + args, cwd=REPO_ROOT, env=clean_env(),
                          capture_output=True, text=True, timeout=60)


class TestHelp(unittest.TestCase):
    def test_every_tool_help_exits_0(self):
        for tool in TOOLS:
            r = run_tool([tool, "--help"])
            self.assertEqual(r.returncode, 0, f"{tool}: {r.stderr[-400:]}")
            self.assertIn("usage", r.stdout, tool)


class TestDryRunEveryVerb(unittest.TestCase):
    def test_every_verb_builds_and_decodes(self):
        for i, (verb, flags) in enumerate(VERB_FLAGS):
            cid = 1_000_200 + i
            r = run_tool(["tools/sofar_send_command.py", "--spotter-id", "SPOT-TEST",
                          "--id", str(cid), "--dry-run"] + flags)
            self.assertEqual(r.returncode, 0, f"{verb}: {r.stdout}{r.stderr[-400:]}")
            self.assertIn("[dry-run] nothing sent.", r.stdout)
            line = next(x for x in r.stdout.splitlines() if x.startswith("message :"))
            message = ast.literal_eval(line.split(":", 1)[1].strip())   # the tool prints repr()
            self.assertTrue(message.startswith("bm pub bmcam/cmd {"))
            body = message[len("bm pub bmcam/cmd "):-len(" 1 1")]
            self.assertLessEqual(len(body), W.MAX_JSON_BYTES)
            cmd = W.decode(body, parse_rsd=ssc._parse_rsd)
            self.assertEqual((cmd.id, cmd.verb, cmd.range), (cid, verb, "remote"))

    def test_refusals_exit_2(self):
        for flags in (["--id", "7", "--ping"], ["--id", "1000300", "--set", "r=bad"],
                      ["--id", "1000300", "--json", '{"c":"cfg","v":0}']):
            r = run_tool(["tools/sofar_send_command.py", "--spotter-id", "SPOT-TEST",
                          "--dry-run"] + flags)
            self.assertEqual(r.returncode, 2, flags)
            self.assertIn("[REFUSED]", r.stdout)


class TestGuiAgainstDispatcher(unittest.TestCase):
    """GuiState.send -> console line -> real v9 dispatcher -> acks/<CF> ->
    sensor-data rows -> sofar_poll_acks -> the GUI's lifecycle."""

    CASES = [("ping", {}), ("help", {}), ("get", {"k": ["mode"]}),
             ("set", {"kv": {"power.halt.enabled": True}}),     # staged: s:1
             ("cfm", {"ref": 1_000_003}),                       # confirms it
             ("set", {"kv": {"r": [0, 0, 4608, 2592]}}),
             ("reset", {"k": ["still.crop"]}), ("reset", {"all": 1}),
             ("trg", {"v": 2, "kv": {"r": [0, 0, 4608, 2592]}}), ("trg", {"v": 0}),
             ("hld", {"v": 30}), ("wap", {"v": 1})]

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="guismoke_")
        self.addCleanup(shutil.rmtree, self.dir, True)
        targets = os.path.join(self.dir, "targets.json")
        with open(targets, "w") as f:
            json.dump({"targets": [{"label": "bench", "spotter_id": "SPOT-TEST",
                                    "node_id": "53171fa3d81a8e6f"}]}, f)
        self.state = gui.GuiState(targets, gui_log=os.path.join(self.dir, "gui.jsonl"),
                                  send_log=os.path.join(self.dir, "sends.jsonl"))
        os.environ[ssc.TOKEN_ENV] = "test-token"
        self.addCleanup(os.environ.pop, ssc.TOKEN_ENV, None)
        self.rig = Rig(self)            # the "fake daemon": real dispatcher, fake BM port

    def test_send_path_round_trip(self):
        rows = []
        with mock.patch.object(gui.ssc, "post_command",
                               return_value=(202, {"status": "success"})) as post, \
             mock.patch.object(gui.ssc, "load_last_success_ts", return_value=None):
            for c, fields in self.CASES:
                out = self.state.send("SPOT-TEST", "53171fa3d81a8e6f", c, fields,
                                      override_in_flight=True, mode="now")
                self.assertEqual(out.get("state"), "awaiting_node", (c, out))
                (_, _, body), _ = post.call_args
                message = body["message"]
                self.assertLessEqual(ssc.validate_message(message), W.MAX_CONSOLE_LINE_BYTES)
                self.rig.send(message[len("bm pub bmcam/cmd "):-len(" 1 1")])
                for text in self.rig.daemon._acks:        # what the unit uplinks
                    rows.append({"timestamp": f"T{len(rows):03d}",
                                 "value": text.encode().hex(),
                                 "bristlemouth_node_id": "0x53171fa3d81a8e6f"})
                self.rig.daemon._acks.clear()
        self.assertEqual(post.call_count, len(self.CASES))

        replies = spa.replies_from_rows(rows)
        acks = [(ts, obj, node) for ts, kind, obj, node in replies if kind == "ack"]
        cfs = [obj for _ts, kind, obj, _node in replies if kind == "cf"]
        self.assertEqual(len(acks), len(self.CASES))
        self.assertTrue(cfs, "get / set / reset answer with <CF> lines")
        with mock.patch.object(gui.spa, "fetch_acks", return_value=acks):
            self.state.poll_acks_once()

        cmds = self.state.store.all_commands()
        self.assertEqual([c["cmd_id"] for c in cmds],
                         list(range(1_000_000, 1_000_000 + len(self.CASES))))
        for cmd in cmds:
            self.assertEqual(cmd["state"], "acked", cmd)
            self.assertRegex(cmd["h"], r"^[0-9a-f]{8}$")
        staged = self.state.store.get(1_000_003)
        self.assertTrue(staged["staged"])
        self.assertEqual(self.state.store.get(1_000_010)["granted_min"], 30)
        # the cfm's change summary: the staged value is now in effect
        self.assertIn(("power.halt.enabled", "1", "c1000003"),
                      [item for cf in cfs for item in cf["items"]])

    def test_rejection_and_duplicate_reach_the_gui(self):
        with mock.patch.object(gui.ssc, "post_command", return_value=(202, {})) as post, \
             mock.patch.object(gui.ssc, "load_last_success_ts", return_value=None):
            out = self.state.send("SPOT-TEST", "53171fa3d81a8e6f", "set",
                                  {"kv": {"camera.white_balance.mode": "manual"}},
                                  mode="now")        # needs gains: e:"xk" on the unit
        (_, _, body), _ = post.call_args
        payload = body["message"][len("bm pub bmcam/cmd "):-len(" 1 1")]
        self.rig.send(payload)
        self.rig.send(payload)                       # the mote's 60 s replay
        acks = [json.loads(a) for a in self.rig.daemon._acks if a.startswith("{")]
        self.assertEqual(acks[0]["ok"], 0)
        with mock.patch.object(gui.spa, "fetch_acks",
                               return_value=[("T", acks[0], "53171fa3d81a8e6f")]):
            self.state.poll_acks_once()
        cmd = self.state.store.get(out["cmd_id"])
        self.assertEqual(cmd["state"], "rejected")
        self.assertEqual(cmd["e"], acks[0]["e"])


class TestReplyParsers(unittest.TestCase):
    ACK = W.build_ack(1_000_001, False, h="a41c09e2", e="val", k="still.crop")
    CF = W.build_cf("a41c09e2", [("video.send.duration_s", 8.0, ("cmd", 1_000_751))])[0]
    HL = rc_heal.build_hl_message("k7x2p0", "sent", 3, "ok", 100_001, wake_key="w1q9z3")

    def test_poll_acks(self):
        self.assertEqual(spa.classify_reply(self.ACK)[0], "ack")
        self.assertEqual(spa.fmt("T", spa.extract_ack(self.ACK)),
                         "T  id=1000001 ok=0 h=a41c09e2 e=val k=still.crop")
        kind, cf = spa.classify_reply(self.CF)
        self.assertEqual(kind, "cf")
        self.assertEqual(cf["items"], [("video.send.duration_s", "8.0", "c1000751")])
        self.assertEqual(spa.classify_reply(self.HL), (None, None))
        self.assertEqual(spa.tag_fields(self.HL, "HL")["a"], "sent")

    def test_reconcile(self):
        rows = [{"timestamp": f"T{i}", "value": text.encode().hex()}
                for i, text in enumerate((self.ACK, self.CF, self.HL))]
        out = sr.reconcile(rows)
        self.assertEqual(out["counts"], {"ack": 1, "cf": 1, "hl": 1})
        self.assertEqual((out["acks"][0]["e"], out["acks"][0]["k"], out["acks"][0]["h"]),
                         ("val", "still.crop", "a41c09e2"))
        self.assertEqual(out["cf"][0]["h"], "a41c09e2")
        self.assertEqual((out["heals"][0]["key"], out["heals"][0]["id"]),
                         ("k7x2p0", "100001"))


if __name__ == "__main__":
    unittest.main()

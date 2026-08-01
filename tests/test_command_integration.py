#!/usr/bin/env python3
# filename: test_command_integration.py
# description: Sprint10 §2/§4 — daemon-in-RC-cycle integration on the coral native.
"""
Sprint10 — integration tests: CommandDaemon wired into the REAL
rc_progressive_jpeg.run_cycle (compress-only on the committed coral
native; fakes for camera/halt/wake; real daemon + reader thread on a
fake UART fed production-encoded frames).

Covers the revised TRACKER §4 integration item (2026-07-26 early-halt
decision): a command in the pre-capture listen window applies to THIS
cycle's capture settings; a command arriving during transmit is acked
in a pacing slot and persists for the NEXT cycle. Plus the D14
zero-regression guard (island disabled -> no daemon, no port).

Slow-ish (real encodes). Run (repo root):
  python3 -m unittest tests.test_command_integration -v
"""

import contextlib
import io
import json
import os
import queue
import sys
import tempfile
import time
import types
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

import command_tables as ct  # noqa: E402
import rc_command_hooks  # noqa: E402
import rc_progressive_jpeg as rc  # noqa: E402
from bm_serial import BristlemouthSerial  # noqa: E402
from bm_frame_decoder import build_raw_pub_frame  # noqa: E402
from command_daemon import CommandDaemon  # noqa: E402
from command_state import CommandState  # noqa: E402

CORAL_NATIVE = os.path.join(
    REPO_ROOT, "reference_images", "prepared", "P7071008",
    "synthetic_native_4608x2592.jpg",
)
TOPIC = "bmcam/cmd"


class FakeUart:
    def __init__(self, timeout=0.1):
        self.timeout = timeout
        self.written = []
        self._rx = queue.Queue()

    def inject(self, data):
        self._rx.put(bytes(data))

    def read(self, n):
        try:
            return self._rx.get(timeout=0.01)
        except queue.Empty:
            return b""

    def write(self, data):
        self.written.append(bytes(data))
        return len(data)

    def reset_input_buffer(self):
        pass

    def close(self):
        pass


def make_cmd_frame(payload_dict, topic=TOPIC):
    # Raw mote->Pi format (Phase B capture) — no COBS, no delimiter.
    return build_raw_pub_frame(0xF365, topic, json.dumps(payload_dict))


def write_yaml():
    f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                    encoding="utf-8")
    f.write("capture_mode: \"progressive_jpeg\"\nenforce_time_window: false\n")
    f.close()
    return f.name


class FakeClock:
    def __init__(self, start=1000.0):
        self.now = float(start)

    def __call__(self):
        return self.now


class IntegrationHarness(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmpdir.cleanup)
        self.config_path = write_yaml()
        self.addCleanup(os.unlink, self.config_path)
        self.uart = FakeUart()
        self.bm = BristlemouthSerial(uart=self.uart, node_id=0xF365,
                                     network_type=0x02)
        self.state_path = os.path.join(self.tmpdir.name, "state.json")
        self.state = CommandState(path=self.state_path)
        self.daemon = CommandDaemon(self.bm, self.state, topic=TOPIC)
        self.cfg = {"enabled": True, "topic": TOPIC,
                    "post_transmit_listen_s": 0.0,
                    "defer_acks_during_transmit": False,
                    "state_path": self.state_path}
        # ONE ordered wire log for BOTH image chunks and acks. On hardware
        # they share a single UART and a single 2-slot Spotter queue; the
        # C3 assertion ("no ack between the first and last chunk") is only
        # meaningful if the test models them on the same wire.
        self.wire = []
        real_tx = self.bm.spotter_tx

        def logged_ack_tx(data, network_type=None):
            payload = data.decode("utf-8") if isinstance(data, bytes) else str(data)
            self.wire.append(("ack", payload))
            return real_tx(data, network_type)

        self.bm.spotter_tx = logged_ack_tx

    def run_rc(self, *, transmit, bench_commands=False, enabled=True,
               inject_at_sleep_call=None, inject_frame=None,
               inject_after_end=False):
        settings = rc.resolve_rc_settings(self.config_path)
        if enabled:
            settings = rc._apply_command_overlay(settings, self.state)
        clock = FakeClock()
        sleep_calls = {"n": 0}

        injected = {"done": False}

        def image_finished():
            return any(k == "img" and "<END IMG>" in m for k, m in self.wire)

        def hybrid_sleep(seconds):
            # Fake time for budget math, a slice of real time so the
            # real reader thread can decode injected frames.
            clock.now += float(seconds)
            sleep_calls["n"] += 1
            if inject_at_sleep_call is not None and \
                    sleep_calls["n"] == inject_at_sleep_call:
                self.uart.inject(inject_frame)
            # Land the frame in the C4 tail rather than at a guessed sleep
            # index — the chunk count varies with the encode ladder.
            if inject_after_end and not injected["done"] and image_finished():
                injected["done"] = True
                self.uart.inject(inject_frame)
            time.sleep(0.02)

        tx_messages = []

        def image_tx(payload):
            text = payload.decode("ascii")
            tx_messages.append(text)
            self.wire.append(("img", text))

        out_dir = tempfile.mkdtemp(dir=self.tmpdir.name)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            summary = rc.run_cycle(
                settings,
                transmit=transmit,
                native_path=CORAL_NATIVE,
                skip_time_window=True,
                output_dir=out_dir,
                capture_fn=None,  # native_path provided; never called
                bm_open_fn=lambda cfg: image_tx,
                bm_close_fn=lambda: 0,
                wake_fn=lambda **kw: None,
                # Halt rides the SAME ordered timeline as acks/chunks so
                # the Sprint12 ack-before-halt assertion is meaningful.
                halt_fn=lambda **kw: (
                    self.wire.append(("halt", kw))
                    or {"action": "recorded", **kw}),
                sleep_fn=hybrid_sleep,
                clock=clock,
                bm_commands_cfg=self.cfg if enabled else
                    {"enabled": False},
                command_state=self.state if enabled else None,
                bench_commands=bench_commands,
                daemon_factory=lambda s, c, st: self.daemon,
            )
        return types.SimpleNamespace(summary=summary, tx=tx_messages,
                                     stdout=stdout.getvalue(),
                                     out_dir=out_dir)

    def ack_blob(self):
        return b"".join(self.uart.written)


class TestCaptureFirstOrdering(IntegrationHarness):
    """Sprint11 C1/D2 — commands govern the NEXT boot, not this capture.

    This REPLACES the Sprint10 pre-capture-listen contract. The old
    behaviour (a command applying to the capture that follows it in the
    same cycle) only ever worked on the bench with USB injection: finding
    006 showed the field mailbox drain arrives 1-4 min AFTER the cycle
    ends, so in the field the window was listening at the one time
    commands never come -- while costing every image ~2 min of blackout
    margin.
    """

    def test_command_waiting_at_boot_does_not_change_this_capture(self):
        # Frame already queued when the daemon subscribes — the strongest
        # possible case for same-cycle application, and it must NOT apply.
        self.uart.inject(make_cmd_frame({"id": 417, "c": "roi", "v": 1}))
        r = self.run_rc(transmit=False, bench_commands=True)

        self.assertIn("applied", r.summary["command_events"])
        sidecar = r.summary["final_path"] + ".capture_metadata.json"
        with open(sidecar) as f:
            meta = json.load(f)
        # This cycle kept the YAML crop, NOT the commanded roi=1 full frame.
        self.assertEqual(tuple(meta["crop_native_xywh"]), (1504, 846, 1600, 900))
        self.assertNotEqual(tuple(meta["crop_native_xywh"]),
                            ct.ROI_TABLE[1]["crop"])
        # It is still acked, and still persisted for the next boot.
        self.assertIn(b'"id":417,"ok":1', self.ack_blob())
        self.assertEqual(CommandState(path=self.state_path).settings["roi"], 1)

    def test_the_same_command_governs_the_next_cycle(self):
        """The other half of the contract: what the first cycle refused to
        apply, the second cycle picks up from cached state on boot."""
        self.uart.inject(make_cmd_frame({"id": 418, "c": "roi", "v": 1}))
        self.run_rc(transmit=False, bench_commands=True)

        # Second cycle: fresh state load, exactly like a reboot.
        self.state = CommandState(path=self.state_path)
        r2 = self.run_rc(transmit=False, bench_commands=True)
        sidecar = r2.summary["final_path"] + ".capture_metadata.json"
        with open(sidecar) as f:
            meta = json.load(f)
        self.assertEqual(tuple(meta["crop_native_xywh"]),
                         ct.ROI_TABLE[1]["crop"])

    def test_no_pre_capture_listen_window_is_announced(self):
        r = self.run_rc(transmit=False, bench_commands=True)
        self.assertNotIn("pre-capture listen", r.stdout)

    def test_reject_and_duplicate_in_window(self):
        self.uart.inject(make_cmd_frame({"id": 5, "c": "awb", "v": 42}))
        self.uart.inject(make_cmd_frame({"id": 6, "c": "foc", "v": 2}))
        self.uart.inject(make_cmd_frame({"id": 6, "c": "foc", "v": 4}))
        r = self.run_rc(transmit=False, bench_commands=True)
        self.assertEqual(
            sorted(r.summary["command_events"]),
            ["applied", "duplicate", "rejected"],
        )
        self.assertIn(b'"id":5,"ok":0,"e":"val"', self.ack_blob())
        self.assertEqual(
            CommandState(path=self.state_path).settings["foc"], 2  # not 4
        )


class TestMidTransmitApply(IntegrationHarness):
    def test_command_during_transmit_acks_and_persists_for_next_cycle(self):
        frame = make_cmd_frame({"id": 900, "c": "roi", "v": 4})
        r = self.run_rc(transmit=True, inject_at_sleep_call=20,
                        inject_frame=frame)

        self.assertIn("applied", r.summary["command_events"])
        self.assertIn(b'"id":900,"ok":1', self.ack_blob())
        # This cycle's image used the YAML crop (capture predated the
        # command)...
        sidecar = r.summary["final_path"] + ".capture_metadata.json"
        with open(sidecar) as f:
            meta = json.load(f)
        self.assertEqual(tuple(meta["crop_native_xywh"]), (1504, 846, 1600, 900))
        # ...but the NEXT cycle's overlay picks it up from disk:
        next_state = CommandState(path=self.state_path)
        self.assertEqual(next_state.settings["roi"], 4)
        self.assertIn("roi", next_state.touched)
        # Image send itself is intact (START/chunks/END all present).
        self.assertTrue(r.tx[0].startswith("<START IMG>"))
        self.assertTrue(any(m.startswith("<I0>") for m in r.tx))
        self.assertIn("<END IMG>", r.tx[-1])


class TestDeferredAcks(IntegrationHarness):
    """Sprint11 C3/D5 — no ack on the wire between the first and last chunk.

    An ack is an uplink message into the SAME 2-slot cellular queue as the
    image chunks. Acking mid-transmit manufactures exactly the collisions
    the pacing exists to avoid (incident 001's silent ack drop; the soak's
    "scattered singles = momentary 2-slot collisions"). The ack is not
    dropped -- it is queued and flushed after END.
    """

    def wire_spans(self):
        """(first chunk index, END index) in the shared wire log."""
        first = next(i for i, (k, m) in enumerate(self.wire)
                     if k == "img" and m.startswith("<I"))
        end = next(i for i, (k, m) in enumerate(self.wire)
                   if k == "img" and "<END IMG>" in m)
        return first, end

    def test_no_ack_is_submitted_between_the_first_and_last_chunk(self):
        self.cfg["defer_acks_during_transmit"] = True
        frame = make_cmd_frame({"id": 901, "c": "roi", "v": 4})
        self.run_rc(transmit=True, inject_at_sleep_call=20, inject_frame=frame)

        first, end = self.wire_spans()
        during = [m for k, m in self.wire[first:end] if k == "ack"]
        self.assertEqual(during, [], f"ack(s) rode the image burst: {during}")

    def test_the_deferred_ack_still_goes_out_after_end(self):
        self.cfg["defer_acks_during_transmit"] = True
        frame = make_cmd_frame({"id": 902, "c": "roi", "v": 4})
        r = self.run_rc(transmit=True, inject_at_sleep_call=20,
                        inject_frame=frame)

        _first, end = self.wire_spans()
        after = [m for k, m in self.wire[end:] if k == "ack"]
        self.assertTrue(any('"id":902,"ok":1' in m for m in after),
                        f"deferred ack never flushed: {after}")
        self.assertIn("applied", r.summary["command_events"])

    def test_the_command_persists_immediately_even_though_the_ack_waits(self):
        """The pump parses and PERSISTS mid-burst; only the wire waits. If
        the cycle died before END the setting would still govern next boot."""
        self.cfg["defer_acks_during_transmit"] = True
        frame = make_cmd_frame({"id": 903, "c": "roi", "v": 4})
        self.run_rc(transmit=True, inject_at_sleep_call=20, inject_frame=frame)
        self.assertEqual(CommandState(path=self.state_path).settings["roi"], 4)

    def test_deferring_does_not_change_the_image_wire(self):
        """Byte-for-byte the same image framing as an undeferred send."""
        self.cfg["defer_acks_during_transmit"] = True
        r = self.run_rc(transmit=True)
        self.assertTrue(r.tx[0].startswith("<START IMG>"))
        self.assertTrue(any(m.startswith("<I0>") for m in r.tx))
        self.assertIn("<END IMG>", r.tx[-1])

    def test_undeferred_default_still_acks_inside_the_burst(self):
        """Guard the Sprint10 behaviour Unit B runs as the control arm."""
        frame = make_cmd_frame({"id": 904, "c": "roi", "v": 4})
        self.run_rc(transmit=True, inject_at_sleep_call=20, inject_frame=frame)
        first, end = self.wire_spans()
        during = [m for k, m in self.wire[first:end] if k == "ack"]
        self.assertTrue(any('"id":904' in m for m in during))


class TestPostTransmitListenTail(IntegrationHarness):
    """Sprint11 C4/D6 — the bounded tail that replaces the pre-capture window."""

    def test_tail_runs_after_the_image_and_is_bounded(self):
        self.cfg["post_transmit_listen_s"] = 3.0
        r = self.run_rc(transmit=True)
        self.assertIn("post-transmit listen window: 3s", r.stdout)
        self.assertEqual(r.summary["listen_tail_s"], 3.0)

    def test_a_command_arriving_in_the_tail_persists_for_the_next_boot(self):
        """Finding 006: this is when the mailbox drain actually arrives."""
        self.cfg["post_transmit_listen_s"] = 5.0
        frame = make_cmd_frame({"id": 905, "c": "roi", "v": 2})
        r = self.run_rc(transmit=True, inject_after_end=True,
                        inject_frame=frame)
        self.assertIn("applied", r.summary["command_events"])
        self.assertEqual(CommandState(path=self.state_path).settings["roi"], 2)
        self.assertIn(b'"id":905,"ok":1', self.ack_blob())

    def test_tail_off_by_default_leaves_the_cycle_unchanged(self):
        r = self.run_rc(transmit=True)          # cfg default is 0.0
        self.assertNotIn("post-transmit listen window", r.stdout)


class _StubBudget:
    def __init__(self, remaining):
        self._remaining = float(remaining)

    def remaining_s(self):
        return self._remaining


class TestListenTailBudgetClamp(unittest.TestCase):
    """C4 must never be able to cause a power cut mid-write (TRACKER §4).

    Stubbed budget so the boundary is exact rather than inferred from a
    real encode's timing.
    """

    def setUp(self):
        self.summary = {"command_events": []}
        self.calls = []

        class FakeDaemon:
            state = None

            def listen_window(_self, seconds, clock=None, sleep_fn=None,
                              label=""):
                self.calls.append(seconds)
                return []

        self.daemon = FakeDaemon()
        self.cfg = {"post_transmit_listen_s": 150.0}

    def run_tail(self, remaining_s):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            actual = rc_command_hooks.post_transmit_listen(
                self.daemon, self.cfg, self.summary, _StubBudget(remaining_s))
        return actual, buf.getvalue()

    def test_full_tail_when_the_budget_is_ample(self):
        actual, _out = self.run_tail(600.0)
        self.assertEqual(actual, 150.0)
        self.assertEqual(self.calls, [150.0])

    def test_tail_trimmed_to_the_budget_less_the_halt_margin(self):
        actual, out = self.run_tail(100.0)
        self.assertEqual(actual, 100.0 - rc_command_hooks.TAIL_SAFETY_S)
        self.assertIn("TRIMMED", out)

    def test_tail_skipped_when_only_the_halt_margin_remains(self):
        actual, out = self.run_tail(rc_command_hooks.TAIL_SAFETY_S)
        self.assertEqual(actual, 0.0)
        self.assertEqual(self.calls, [])
        self.assertIn("SKIPPED", out)

    def test_tail_skipped_when_the_budget_is_already_exhausted(self):
        actual, out = self.run_tail(0.0)
        self.assertEqual(actual, 0.0)
        self.assertEqual(self.calls, [])
        self.assertIn("SKIPPED", out)

    def test_no_daemon_is_a_no_op(self):
        self.assertEqual(
            rc_command_hooks.post_transmit_listen(
                None, self.cfg, self.summary, _StubBudget(600.0)),
            0.0,
        )


class TestDisabledRegressionGuard(IntegrationHarness):
    def test_disabled_island_runs_without_daemon_or_port(self):
        r = self.run_rc(transmit=False, enabled=False)
        self.assertEqual(self.uart.written, [])       # no subscribe, no acks
        self.assertNotIn("[CMD]", r.stdout)
        self.assertEqual(r.summary["command_events"], [])
        self.assertTrue(r.summary["selection"]["fits"])

    def test_daemon_stopped_after_cycle(self):
        self.uart.inject(make_cmd_frame({"id": 1, "c": "ping"}))
        self.run_rc(transmit=False, bench_commands=True)
        self.assertFalse(self.daemon._reader.is_alive())


if __name__ == "__main__":
    unittest.main()


class TestSprint12AckBeforeHalt(IntegrationHarness):
    """SPEC Sprint12 hard requirement (D-S12-7): an hlt arriving mid-cycle
    is acked on the uplink BEFORE the cycle's halt fires — otherwise the
    sender's retry engine can never converge on a unit that goes dark at
    cycle end. And the halt itself uses the settings the cycle BOOTED
    with (no same-cycle halt changes, Sprint11 D2)."""

    def _ack_and_halt_indices(self, command_id):
        acks = [i for i, (k, m) in enumerate(self.wire)
                if k == "ack" and f'"id":{command_id}' in m]
        halts = [i for i, (k, _) in enumerate(self.wire) if k == "halt"]
        return acks, halts

    def test_hlt_mid_transmit_ack_precedes_halt(self):
        frame = make_cmd_frame({"id": 950, "c": "hlt", "v": 1})
        r = self.run_rc(transmit=True, inject_at_sleep_call=20,
                        inject_frame=frame)
        self.assertIn("applied", r.summary["command_events"])
        acks, halts = self._ack_and_halt_indices(950)
        self.assertTrue(acks, "hlt ack never reached the wire")
        self.assertTrue(halts, "halt never fired")
        self.assertLess(acks[0], halts[0], "halt fired before the hlt ack")
        # THIS cycle halted with boot settings (harness yaml has no
        # power_halt block -> enabled False), not the commanded real halt.
        halt_kw = self.wire[halts[0]][1]
        self.assertFalse(halt_kw["enabled"])
        # The NEXT boot's overlay applies the commanded mode from disk.
        next_state = CommandState(path=self.state_path)
        settings = rc.resolve_rc_settings(self.config_path)
        settings = rc._apply_command_overlay(settings, next_state)
        self.assertTrue(settings["power_halt_enabled"])
        self.assertFalse(settings["power_halt_dry_run"])
        self.assertEqual(settings["power_halt_source"], "command hlt=1")

    def test_hlt_arriving_in_listen_tail_still_acks_before_halt(self):
        # The field-realistic arrival (finding 006): the command lands in
        # the C4 post-transmit tail; the final flush at shutdown must put
        # the ack out before halt_fn runs.
        self.cfg["post_transmit_listen_s"] = 1.0
        frame = make_cmd_frame({"id": 951, "c": "hlt", "v": 2})
        r = self.run_rc(transmit=True, inject_after_end=True,
                        inject_frame=frame)
        self.assertIn("applied", r.summary["command_events"])
        acks, halts = self._ack_and_halt_indices(951)
        self.assertTrue(acks, "tail hlt ack never reached the wire")
        self.assertLess(acks[0], halts[0], "halt fired before the tail ack")

    def test_stranding_warning_printed_on_next_boot(self):
        # hlt 1 commanded -> the next boot's overlay logs the stranding
        # trade loudly (SPEC: "logged loudly, unambiguously").
        frame = make_cmd_frame({"id": 952, "c": "hlt", "v": 1})
        self.run_rc(transmit=True, inject_at_sleep_call=20,
                    inject_frame=frame)
        next_state = CommandState(path=self.state_path)
        settings = rc.resolve_rc_settings(self.config_path)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rc._apply_command_overlay(settings, next_state)
        self.assertIn("REAL power halt", stdout.getvalue())


class TestSprint12TriggerEndToEnd(IntegrationHarness):
    """trg through the REAL main() wiring: pending trigger -> one-shot
    window bypass + reference source -> stock behaviour restored on the
    following boot (D-S12-3/4/5)."""

    def _write_gated_yaml(self):
        # A window that blocks essentially always (00:00-00:01 UTC), with
        # the daemon island pointed at the harness state file. time_source
        # system: deterministic outside-window verdict without a UART.
        f = tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False,
                                        encoding="utf-8")
        f.write(
            'capture_mode: "progressive_jpeg"\n'
            "enforce_time_window: true\n"
            "time_source: system\n"
            'timezone: "UTC"\n'
            "transmit_window:\n"
            '  start: "00:00"\n'
            '  end: "00:01"\n'
            "bm_commands:\n"
            "  enabled: true\n"
            f'  state_path: "{self.state_path}"\n'
            "  post_transmit_listen_s: 0.0\n"
        )
        f.close()
        self.addCleanup(os.unlink, f.name)
        return f.name

    def _run_main(self, config_path):
        clock = FakeClock()

        def pacing_sleep(seconds):
            clock.now += float(seconds)

        out_dir = tempfile.mkdtemp(dir=self.tmpdir.name)
        wire = []

        def image_tx(payload):
            wire.append(payload.decode("ascii"))

        def no_camera(settings, output_dir):
            raise AssertionError(
                "camera capture attempted — reference trigger must skip it")

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            rcode = rc.main(
                ["--config-path", config_path, "--transmit",
                 "--output-dir", out_dir],
                capture_fn=no_camera,
                bm_open_fn=lambda cfg: image_tx,
                bm_close_fn=lambda: 0,
                wake_fn=lambda **kw: None,
                halt_fn=lambda **kw: {"action": "recorded", **kw},
                sleep_fn=pacing_sleep,
                clock=clock,
                daemon_factory=lambda s, c, st: self.daemon,
            )
        return types.SimpleNamespace(rcode=rcode, wire=wire,
                                     stdout=stdout.getvalue())

    def test_trg3_bypasses_window_sends_reference_then_restores(self):
        config_path = self._write_gated_yaml()
        # Boot 1, no trigger: gate blocks (outside 00:00-00:01).
        r1 = self._run_main(config_path)
        self.assertEqual(r1.rcode, 0)
        self.assertEqual(r1.wire, [])
        self.assertIn("Outside transmit window", r1.stdout)

        # Operator arms trg 3 (reef reference, camera skipped).
        arming = CommandState(path=self.state_path)
        arming.record(970, "trg", 3)

        # Boot 2: trigger consumed -> gate bypassed, reference transmitted.
        r2 = self._run_main(config_path)
        self.assertEqual(r2.rcode, 0)
        self.assertIn("window gate", r2.stdout)
        self.assertIn("BYPASSED", r2.stdout)
        self.assertTrue(r2.wire and r2.wire[0].startswith("<START IMG>"))
        self.assertIn("<END IMG>", r2.wire[-1])
        self.assertIn(ct.SRC_TABLE[1]["path"], r2.stdout)

        # Boot 3: trigger gone -> stock gating again, nothing sent.
        r3 = self._run_main(config_path)
        self.assertEqual(r3.wire, [])
        self.assertIn("Outside transmit window", r3.stdout)
        self.assertIsNone(
            CommandState(path=self.state_path).pending_trigger)

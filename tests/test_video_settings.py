#!/usr/bin/env python3
# filename: test_video_settings.py
# description: Sprint15 settings GUI — safe YAML patcher + /settings routes.
"""
Sprint15 settings-GUI tests (Nick request 2026-08-17).

Patcher (video_settings.py):
  - reads current values incl. the 3-deep camera_controls.focus nesting
  - rewrites ONLY the value part; comments/structure byte-preserved
  - timestamped backup before every save; validation failure restores it
  - unknown keys, off-menu values, and keys absent from the file refused

Routes (videoui_server /settings, /restart) against a live ephemeral
server with an injected restart_fn — no reboot, no network side effects.

Run: python3 -m unittest tests.test_video_settings -v
"""

import contextlib
import io
import os
import shutil
import sys
import tempfile
import types
import unittest
import urllib.error
import urllib.parse
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PI_DIR = os.path.join(REPO_ROOT, "BM_Devel_Pi")

try:
    import serial  # noqa: F401
except ImportError:
    _stub = types.ModuleType("serial")

    class _NoSerialOffDevice:
        def __init__(self, *args, **kwargs):
            raise RuntimeError("serial stub: no UART access in off-device tests")

    _stub.Serial = _NoSerialOffDevice
    sys.modules["serial"] = _stub

sys.path.insert(0, PI_DIR)

import video_settings as vs  # noqa: E402
import videoui_server  # noqa: E402

SAMPLE_YAML = """\
time_source: "system"
capture_mode: "video"     # runtime switch

image_pipeline:
  enabled: true
  camera_controls:
    enabled: true
    focus:
      enabled: true
      mode: "manual"
      lens_position: 1.82
      range: "normal"

progressive_jpeg:
  output_width: 1000       # lanczos output width; height follows aspect

video:
  clip_minutes: 5          # per-clip length
  fps: 15
  bitrate_mbps: 2.0
  session_minutes: 0
  storage:
    max_used_pct: 75
    min_free_gb: 10
    ring_dry_run: false
  ui:
    enabled: true
    port: 8080
"""


class PatchMixin:
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="vidset_test_")
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        self.yaml = os.path.join(self.dir, "camera_schedule.yaml")
        with open(self.yaml, "w") as f:
            f.write(SAMPLE_YAML)


class TestReadCurrent(PatchMixin, unittest.TestCase):
    def test_reads_all_present_fields(self):
        current = vs.read_current(self.yaml)
        self.assertEqual(current["capture_mode"], "video")
        self.assertEqual(current["video.clip_minutes"], "5")
        self.assertEqual(current["video.bitrate_mbps"], "2.0")
        self.assertEqual(current["video.storage.ring_dry_run"], "false")
        self.assertEqual(current["progressive_jpeg.output_width"], "1000")
        self.assertEqual(
            current["image_pipeline.camera_controls.focus.lens_position"],
            "1.82")
        self.assertEqual(
            current["image_pipeline.camera_controls.focus.mode"], "manual")


class TestPatchYaml(PatchMixin, unittest.TestCase):
    def test_patch_preserves_comments_and_structure(self):
        result = vs.patch_yaml(self.yaml, {
            "video.bitrate_mbps": "4",
            "progressive_jpeg.output_width": "1600",
            "image_pipeline.camera_controls.focus.lens_position": "1.0",
        })
        self.assertEqual(sorted(result["changed"]), [
            "image_pipeline.camera_controls.focus.lens_position",
            "progressive_jpeg.output_width",
            "video.bitrate_mbps"])
        text = open(self.yaml).read()
        self.assertIn("bitrate_mbps: 4", text)
        self.assertIn("output_width: 1600   # lanczos output width; "
                      "height follows aspect", text)
        self.assertIn("lens_position: 1.0", text)
        self.assertIn("# per-clip length", text)      # comments intact
        self.assertIn('capture_mode: "video"', text)  # untouched keys intact
        self.assertTrue(os.path.exists(result["backup"]))
        self.assertEqual(open(result["backup"]).read(), SAMPLE_YAML)

    def test_quoted_values_stay_quoted(self):
        vs.patch_yaml(self.yaml, {"capture_mode": "progressive_jpeg"})
        self.assertIn('capture_mode: "progressive_jpeg"',
                      open(self.yaml).read())

    def test_unknown_key_refused(self):
        with self.assertRaises(ValueError):
            vs.patch_yaml(self.yaml, {"video.ui.port": "9999"})

    def test_off_menu_value_refused(self):
        with self.assertRaises(ValueError):
            vs.patch_yaml(self.yaml, {"video.fps": "120"})
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)

    def test_missing_key_refused(self):
        with open(self.yaml, "w") as f:
            f.write('time_source: "system"\ncapture_mode: "video"\n')
        with self.assertRaises(ValueError) as ctx:
            vs.patch_yaml(self.yaml, {"video.fps": "15"})
        self.assertIn("not present", str(ctx.exception))

    def test_validation_failure_restores_backup(self):
        # A file whose video island the runtime validator would reject
        # if fps could get through — force it by patching validate to
        # explode instead (any validator failure must restore).
        original = vs._validate_config
        vs._validate_config = lambda path: (_ for _ in ()).throw(
            ValueError("validator says no"))
        try:
            with self.assertRaises(ValueError):
                vs.patch_yaml(self.yaml, {"video.fps": "30"})
        finally:
            vs._validate_config = original
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)

    def test_real_validator_accepts_good_patch(self):
        result = vs.patch_yaml(self.yaml, {"video.fps": "30"})
        self.assertEqual(result["changed"], ["video.fps"])
        self.assertIn("fps: 30", open(self.yaml).read())

    def test_no_change_is_noop_but_backed_up(self):
        result = vs.patch_yaml(self.yaml, {"video.fps": "15"})
        self.assertEqual(result["changed"], [])
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)


class TestSettingsRoutes(PatchMixin, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.restarts = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.server = videoui_server.start_ui_server(
                self.dir, 0, host="127.0.0.1", config_path=self.yaml,
                restart_fn=lambda: self.restarts.append(1))
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=5) as resp:
            return resp.status, resp.read().decode()

    def _post(self, path, data):
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body)
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, resp.read().decode()

    def test_settings_page_shows_current_values(self):
        status, page = self._get("/settings")
        self.assertEqual(status, 200)
        self.assertIn("bmcam settings", page)
        self.assertIn('value="video" selected', page)
        self.assertIn('value="1.82" selected', page)
        self.assertIn("Save settings", page)
        self.assertIn("Restart camera", page)

    def test_gallery_links_settings(self):
        status, page = self._get("/")
        self.assertIn('href="/settings"', page)

    def test_post_saves_and_reports(self):
        current = vs.read_current(self.yaml)
        form = dict(current)                    # echo everything back
        form["video.bitrate_mbps"] = "4"
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post("/settings", form)
        self.assertEqual(status, 200)
        self.assertIn("Saved: video.bitrate_mbps", page)
        self.assertIn("bitrate_mbps: 4", open(self.yaml).read())

    def test_post_bad_value_rejected_file_untouched(self):
        form = {"video.fps": "120"}
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post("/settings", form)
        self.assertEqual(status, 200)
        self.assertIn("NOT saved", page)
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)

    def test_restart_route_uses_injected_fn(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post("/restart", {})
        self.assertEqual(status, 200)
        self.assertIn("Restarting", page)
        self.assertEqual(self.restarts, [1])

    def _form_echo(self):
        """What a real browser submits: EVERY field's current value."""
        current = vs.read_current(self.yaml)
        return {f["key"]: current[f["key"]] for f in vs.FIELDS
                if f["key"] in current}

    def _save(self, **changes):
        form = self._form_echo()
        form.update({k: str(v) for k, v in changes.items()})
        with contextlib.redirect_stdout(io.StringIO()):
            return self._post("/settings", form)

    def test_float_formatted_yaml_echo_saves(self):
        # THE bmcam000 2026-08-18 bug: yaml holds 2.0, dropdown says "2";
        # the full-form echo must not poison an unrelated field's save.
        with open(self.yaml, "w") as f:
            f.write(SAMPLE_YAML.replace("bitrate_mbps: 2.0",
                                        "bitrate_mbps: 2.0")
                    .replace("fps: 15", "fps: 15.0")
                    .replace("max_used_pct: 75", "max_used_pct: 75.0"))
        status, page = self._save(**{"video.clip_minutes": "1"})
        self.assertIn("Saved: video.clip_minutes", page)
        text = open(self.yaml).read()
        self.assertIn("clip_minutes: 1", text)
        self.assertIn("fps: 15.0", text)        # untouched fields untouched
        # And the form pre-selects the canonical option, not "current: 2.0"
        status, form_page = self._get("/settings")
        self.assertNotIn("current: 2.0", form_page)
        self.assertIn('value="2" selected', form_page)

    def test_combination_saves_persist(self):
        # Multi-field combos through the UI path, applied sequentially
        # like a customer session; each must land and accumulate.
        combos = [
            {"image_pipeline.camera_controls.focus.mode": "auto",
             "progressive_jpeg.output_width": "1600",
             "video.bitrate_mbps": "4"},
            {"video.clip_minutes": "0.25", "video.fps": "30",
             "video.storage.ring_dry_run": "true"},
            {"video.session_minutes": "60",
             "video.storage.max_used_pct": "50",
             "video.storage.min_free_gb": "20"},
        ]
        for combo in combos:
            status, page = self._save(**combo)
            self.assertEqual(status, 200)
            self.assertIn("Saved:", page)
        current = vs.read_current(self.yaml)
        self.assertEqual(
            current["image_pipeline.camera_controls.focus.mode"], "auto")
        self.assertEqual(current["progressive_jpeg.output_width"], "1600")
        self.assertEqual(current["video.bitrate_mbps"], "4")
        self.assertEqual(current["video.clip_minutes"], "0.25")
        self.assertEqual(current["video.fps"], "30")
        self.assertEqual(current["video.storage.ring_dry_run"], "true")
        self.assertEqual(current["video.session_minutes"], "60")
        self.assertEqual(current["video.storage.max_used_pct"], "50")
        self.assertEqual(current["video.storage.min_free_gb"], "20")
        # The whole accumulated file still passes runtime validation.
        vs._validate_config(self.yaml)

    def test_mode_switch_roundtrip(self):
        status, page = self._save(capture_mode="progressive_jpeg")
        self.assertIn("Saved: capture_mode", page)
        status, page = self._save(capture_mode="video")
        self.assertIn("Saved: capture_mode", page)
        self.assertIn('capture_mode: "video"', open(self.yaml).read())

    def test_settings_404_without_config_path(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            bare = videoui_server.start_ui_server(
                self.dir, 0, host="127.0.0.1")
        try:
            with self.assertRaises(urllib.error.HTTPError) as ctx:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{bare.server_address[1]}/settings",
                    timeout=5)
            self.assertEqual(ctx.exception.code, 404)
        finally:
            bare.shutdown()
            bare.server_close()


if __name__ == "__main__":
    unittest.main()

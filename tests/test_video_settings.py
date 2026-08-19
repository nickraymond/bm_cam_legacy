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
from unittest import mock
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

network:
  default: nereus_hq       # ap | nereus_hq (ship value: ap)
  ap_fallback_s: 90
  ap_timeout_min: 60
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
        self.joins = []
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.server = videoui_server.start_ui_server(
                self.dir, 0, host="127.0.0.1", config_path=self.yaml,
                restart_fn=lambda: self.restarts.append(1),
                join_fn=lambda ssid, psk: self.joins.append((ssid, psk)))
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

    def _post_raw(self, path, data):
        """POST WITHOUT following the redirect — returns (code, Location).
        The PRG contract is the thing under test in several places."""
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        body = urllib.parse.urlencode(data).encode()
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", data=body)
        try:
            resp = opener.open(req, timeout=5)
            return resp.status, resp.headers.get("Location")
        except urllib.error.HTTPError as err:
            return err.code, err.headers.get("Location")

    def test_settings_page_shows_current_values(self):
        status, page = self._get("/settings")
        self.assertEqual(status, 200)
        self.assertIn("Nereus Vision camera settings", page)
        self.assertIn('value="video" selected', page)
        self.assertIn('value="1.82" selected', page)
        self.assertIn("Save settings", page)
        # Sprint18: restart rides the save form as an intent marker
        self.assertIn("Save and restart now", page)
        self.assertIn('name="then" value="restart"', page)

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

    def test_save_and_restart_saves_first_then_restarts(self):
        """The combined button must persist the edit BEFORE rebooting."""
        with contextlib.redirect_stdout(io.StringIO()):
            status, location = self._post_raw(
                "/settings", {"video.fps": "30", "then": "restart"})
        self.assertEqual(status, 303)
        self.assertEqual(location, "/restarted")
        self.assertIn("fps: 30", open(self.yaml).read())
        self.assertEqual(self.restarts, [1])

    def test_failed_save_never_costs_a_reboot(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, location = self._post_raw(
                "/settings", {"video.fps": "120", "then": "restart"})
        self.assertEqual(status, 303)
        self.assertIn("/settings?", location)        # back to the form
        self.assertEqual(self.restarts, [])          # no reboot
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)

    def test_then_marker_is_not_treated_as_a_setting(self):
        """'then' is UI intent; the patcher refuses unknown keys."""
        with contextlib.redirect_stdout(io.StringIO()):
            status, location = self._post_raw("/settings", {"then": "restart"})
        self.assertEqual(status, 303)
        self.assertEqual(location, "/restarted")
        self.assertEqual(self.restarts, [1])

    def test_restart_route_uses_injected_fn(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post("/restart", {})
        self.assertEqual(status, 200)
        self.assertIn("Restarting", page)
        self.assertEqual(self.restarts, [1])

    # ---- Sprint16 (D-S16-4): network default + session-only join -----

    def test_settings_page_shows_network_default(self):
        status, page = self._get("/settings")
        self.assertIn("WiFi at power-on", page)
        self.assertIn('value="nereus_hq" selected', page)
        self.assertIn("Connect to a WiFi network", page)

    def test_post_changes_network_default(self):
        current = vs.read_current(self.yaml)
        form = dict(current)
        form["network.default"] = "ap"
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post("/settings", form)
        self.assertIn("Saved: network.default", page)
        self.assertIn("default: ap", open(self.yaml).read())

    def test_join_route_uses_injected_fn(self):
        with contextlib.redirect_stdout(io.StringIO()):
            status, page = self._post(
                "/settings/join",
                {"wifi_ssid": "CustomerNet", "wifi_psk": "hunter2hunter2"})
        self.assertEqual(status, 200)
        self.assertIn("Connecting to", page)
        self.assertIn("forgets this network", page)
        self.assertEqual(self.joins, [("CustomerNet", "hunter2hunter2")])

    def test_join_rejects_short_psk_and_empty_ssid(self):
        with contextlib.redirect_stdout(io.StringIO()):
            _, page = self._post("/settings/join",
                                 {"wifi_ssid": "X", "wifi_psk": "short"})
        self.assertIn("NOT changed", page)
        with contextlib.redirect_stdout(io.StringIO()):
            _, page = self._post("/settings/join",
                                 {"wifi_ssid": "", "wifi_psk": "longenough"})
        self.assertIn("NOT changed", page)
        self.assertEqual(self.joins, [])
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)

    def test_posts_redirect_not_replayable(self):
        # PRG regression (bmcam000 2026-08-18): Safari replayed a cached
        # /restart POST on page refresh and rebooted the camera mid-AP.
        # Every POST must answer 303 so a refresh re-GETs harmlessly.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, *a, **k):
                return None
        opener = urllib.request.build_opener(NoRedirect)
        for route, data in (
                ("/restart", {}),
                ("/settings", {"video.fps": "15"}),
                ("/settings/join",
                 {"wifi_ssid": "X", "wifi_psk": "hunter2hunter2"})):
            body = urllib.parse.urlencode(data).encode()
            req = urllib.request.Request(
                f"http://127.0.0.1:{self.port}{route}", data=body)
            with contextlib.redirect_stdout(io.StringIO()):
                try:
                    opener.open(req, timeout=5)
                    self.fail(f"{route} did not redirect")
                except urllib.error.HTTPError as e:
                    self.assertEqual(e.code, 303, route)
                    self.assertTrue(e.headers["Location"], route)

    def test_open_ap_banner_tracks_mode_file(self):
        mode_file = os.path.join(self.dir, "mode")
        with open(mode_file, "w") as f:
            f.write("ap\n")
        with mock.patch.object(videoui_server, "NET_MODE_FILE", mode_file):
            _, gallery = self._get("/")
            _, settings = self._get("/settings")
        self.assertIn("Open hotspot mode", gallery)
        self.assertIn("Open hotspot mode", settings)
        with open(mode_file, "w") as f:
            f.write("client:nereus-hq\n")
        with mock.patch.object(videoui_server, "NET_MODE_FILE", mode_file):
            _, gallery = self._get("/")
        self.assertNotIn("Open hotspot mode", gallery)

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


class TestShippedTemplateCarriesEveryField(unittest.TestCase):
    """The GUI edits configs, it never authors keys — patch_yaml REFUSES a key
    that is not already in the file. That is not theoretical: on bmcam000
    (2026-08-18) a missing video island made real settings view-only, and
    bmcam004's missing camera_controls block did the same to the focus fields.

    So the shipped template must contain every editable key, or the settings
    page silently degrades on a fresh unit.
    """

    TEMPLATE = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "device_profiles", "rc_field_template", "camera_schedule.yaml")

    def test_every_field_key_present_in_rc_field_template(self):
        present = set(vs.read_current(self.TEMPLATE))
        missing = sorted({f["key"] for f in vs.FIELDS} - present)
        self.assertEqual(
            missing, [],
            f"rc_field_template is missing GUI-editable keys {missing} — the "
            f"settings page would render them view-only on a fresh unit")

    def test_template_values_are_all_valid_choices(self):
        current = vs.read_current(self.TEMPLATE)
        for key, value in current.items():
            self.assertIsNotNone(
                vs.normalize_choice(key, value),
                f"{key}={value!r} in rc_field_template is not an offered "
                f"choice, so the settings form would reject its own echo")


class TestRuntimeManifestCoversTheVideoPath(unittest.TestCase):
    """A field update copies ONLY the files in tools/rc_runtime_manifest.txt.
    A video module missing from that list installs a runtime that cannot
    import — the bmcam003 2026-08-01 lesson, and a live risk again in
    Sprint17 when video_geometry.py was added.
    """

    REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    MANIFEST = os.path.join(REPO, "tools", "rc_runtime_manifest.txt")
    PI_DIR = os.path.join(REPO, "BM_Devel_Pi")

    def _manifest_entries(self):
        with open(self.MANIFEST) as f:
            return {line.strip() for line in f
                    if line.strip() and not line.startswith("#")}

    def test_every_video_module_is_shipped(self):
        entries = self._manifest_entries()
        on_disk = {f"BM_Devel_Pi/{n}" for n in os.listdir(self.PI_DIR)
                   if n.startswith("video") and n.endswith(".py")}
        missing = sorted(on_disk - entries)
        self.assertEqual(
            missing, [],
            f"{missing} exist but are not in rc_runtime_manifest.txt — a field "
            f"update would install a runtime that fails to import")

    def test_boot_syntax_gate_covers_every_video_module(self):
        gate = open(os.path.join(self.PI_DIR, "rc_run_capture_cycle.sh")).read()
        for name in os.listdir(self.PI_DIR):
            if name.startswith("video") and name.endswith(".py"):
                self.assertIn(
                    name, gate,
                    f"{name} is not in rc_run_capture_cycle.sh's py_compile "
                    f"gate, so a syntax error there would only surface when "
                    f"the first clip fails")


if __name__ == "__main__":
    unittest.main()


class TestSprint18Fields(PatchMixin, unittest.TestCase):
    """Sprint18: retired HEIC, mode-aware fields, honest preset labels."""

    def test_heic_is_not_an_offered_choice(self):
        values = [v for v, _ in vs.field_for("capture_mode")["choices"]]
        self.assertEqual(values, ["video", "progressive_jpeg"])

    def test_capture_mode_labels_are_video_and_image(self):
        labels = [l for _, l in vs.field_for("capture_mode")["choices"]]
        self.assertEqual(labels, ["Video", "Image"])

    def test_preset_labels_carry_no_day_estimates(self):
        """The measured Storage panel owns the retention claim; two
        different numbers for it on one screen destroys trust."""
        for _v, label in vs.field_for("video.preset")["choices"]:
            self.assertNotIn("day", label.lower(), label)
            self.assertNotIn("~", label, label)

    def test_every_field_declares_a_mode(self):
        for f in vs.FIELDS:
            self.assertTrue(f["applies"], f["key"])
            for m in f["applies"]:
                self.assertIn(m, ("video", "stills"), f["key"])

    def test_video_only_fields_hidden_in_stills_mode(self):
        self.assertTrue(vs.applies_to("video.clip_minutes", "video"))
        self.assertFalse(vs.applies_to("video.clip_minutes", "stills"))
        self.assertFalse(vs.applies_to("video.preset", "stills"))
        # Sprint17 made the photo width stills-only
        self.assertFalse(
            vs.applies_to("progressive_jpeg.output_width", "video"))
        self.assertTrue(
            vs.applies_to("progressive_jpeg.output_width", "stills"))
        # focus + network follow the camera in both modes
        self.assertTrue(vs.applies_to("network.default", "stills"))

    def test_mode_class_maps_capture_mode(self):
        self.assertEqual(vs.mode_class("video"), "video")
        self.assertEqual(vs.mode_class("progressive_jpeg"), "stills")
        self.assertEqual(vs.mode_class("heic"), "stills")

    def test_groups_and_advanced_cover_every_field(self):
        """No field may go missing from the page."""
        grouped = {k for _t, keys in vs.GROUPS for k in keys}
        grouped |= set(vs.ADVANCED_KEYS)
        self.assertEqual(grouped, {f["key"] for f in vs.FIELDS})


class TestRetiredValueDoesNotPoisonSaves(PatchMixin, unittest.TestCase):
    """Retiring a choice must not break saves on a unit still set to it.

    The form echoes every field back, so without this guard a config
    reading capture_mode: heic would fail EVERY save -- including one
    that only touches frame rate. Same shape as the bmcam000 float-echo
    save-poison, 2026-08-18.
    """

    def test_echoed_retired_value_is_a_noop_not_an_error(self):
        with open(self.yaml, "r") as f:
            text = f.read()
        with open(self.yaml, "w") as f:
            f.write(text.replace('capture_mode: "video"',
                                 'capture_mode: "heic"'))
        result = vs.patch_yaml(self.yaml, {"capture_mode": "heic",
                                           "video.fps": "30"},
                               validate=False)
        self.assertEqual(result["changed"], ["video.fps"])
        self.assertIn('capture_mode: "heic"', open(self.yaml).read())

    def test_a_real_off_menu_value_is_still_refused(self):
        with self.assertRaises(ValueError):
            vs.patch_yaml(self.yaml, {"video.fps": "120"})
        self.assertEqual(open(self.yaml).read(), SAMPLE_YAML)


class TestPendingChanges(unittest.TestCase):
    """Saved != running. Derived from the config mtime vs process start,
    so it needs no state file and clears itself after a reboot."""

    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="pending_")
        self.path = os.path.join(self.dir, "camera_schedule.yaml")
        with open(self.path, "w") as f:
            f.write(SAMPLE_YAML)

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def test_config_older_than_process_is_not_pending(self):
        os.utime(self.path, (1, 1))          # far in the past
        self.assertFalse(videoui_server.pending_changes(self.path))
        self.assertEqual(videoui_server.pending_banner(self.path), "")

    def test_config_newer_than_process_is_pending(self):
        future = videoui_server.PROCESS_START + 60
        os.utime(self.path, (future, future))
        self.assertTrue(videoui_server.pending_changes(self.path))
        self.assertIn("Saved, not yet running",
                      videoui_server.pending_banner(self.path))

    def test_missing_config_is_never_pending(self):
        self.assertFalse(videoui_server.pending_changes(None))
        self.assertFalse(
            videoui_server.pending_changes(self.path + ".nope"))


class TestStoragePanelFollowsMode(PatchMixin, unittest.TestCase):
    """The storage sentence must track the mode selector.

    Nick, 2026-08-19: toggling Video/Image hid the video-only fields but
    left the panel saying "keeps about N days of video" -- the panel
    contradicting the form it sits above. Both readings now ship and the
    selector swaps them, while the MEASURED figures stay put because they
    describe the running camera, not the unsaved selection.
    """

    DISK = {"used": 38.4, "total": 114.7, "cap_pct": 75.0, "days": 1.5,
            "gb_per_hour": 2.5, "clips": 255, "images": 103,
            "oldest": "2026-08-18T04:56:13Z"}

    def _page(self, capture_mode):
        current = vs.read_current(self.yaml)
        current["capture_mode"] = capture_mode
        return videoui_server.render_settings_page(current, disk=self.DISK)

    def test_both_readings_are_present_for_the_selector(self):
        page = self._page("video")
        self.assertIn('data-mode-note="video"', page)
        self.assertIn('data-mode-note="stills"', page)

    def test_video_mode_shows_the_retention_sentence(self):
        page = self._page("video")
        self.assertIn("keeps about <b>1.5 days</b> of video", page)
        # the stills reading ships but starts hidden
        self.assertIn('data-mode-note="stills" hidden', page)

    def test_photo_mode_hides_the_retention_sentence(self):
        page = self._page("progressive_jpeg")
        self.assertIn('data-mode-note="video" hidden', page)
        # the stills reading is the visible one
        self.assertIn('data-mode-note="stills">', page)
        # with no stills history in this fixture it degrades honestly
        self.assertIn("Not enough recent photos", page)

    def test_measured_figures_show_in_both_modes(self):
        """The burn rate and card usage describe the RUNNING camera, so
        they must not vanish when the selector moves."""
        for mode in ("video", "progressive_jpeg"):
            page = self._page(mode)
            self.assertIn("38.4", page, mode)
            self.assertIn("2.5 GB/hour", page, mode)
            self.assertIn("of 114.7 GB used", page, mode)

    def test_saved_mode_is_exposed_for_the_drift_notice(self):
        page = self._page("video")
        self.assertIn('id="saved-mode">video<', page)
        self.assertIn('data-mode-note="drift"', page)
        self.assertIn("takes effect when you save and restart", page)


class TestSprint18Cleanups(PatchMixin, unittest.TestCase):
    """Review round with Nick, 2026-08-19."""

    DISK = {"used": 40.1, "total": 114.7, "cap_pct": 75.0, "days": 1.6,
            "gb_per_hour": 1.22, "clips": 270, "images": 103,
            "oldest": "2026-08-18T04:56:13Z",
            "stills_gb_per_hour": 0.0026, "stills_mean_kb": 50.0}

    def _page(self, mode="video", focus="auto"):
        current = vs.read_current(self.yaml)
        current["capture_mode"] = mode
        current["image_pipeline.camera_controls.focus.mode"] = focus
        return videoui_server.render_settings_page(current, disk=self.DISK)

    def test_storage_lines_say_saved(self):
        page = self._page()
        self.assertIn("Videos saved", page)
        self.assertIn("Images saved", page)
        self.assertNotIn("Videos kept", page)

    def test_save_buttons_are_the_last_boxes_on_the_page(self):
        """Save applies to everything above it, so it must not land
        mid-column in the two-column laptop layout."""
        body = self._page()
        body = body[body.index("<main>"):]
        self.assertLess(body.index("</form>"), body.index(">Save settings"))
        self.assertLess(body.index("Connect to this"),
                        body.index(">Save settings"))
        self.assertLess(body.index(">Save settings"), body.index('class="foot"'))

    def test_save_buttons_stay_bound_to_the_settings_form(self):
        """They sit outside <form> (forms cannot nest around the WiFi
        box), so the form attribute is what keeps them wired up."""
        page = self._page()
        self.assertIn('type="submit" form="setform"', page)
        self.assertIn('name="then" value="restart"', page)
        self.assertEqual(page.count("<form"), page.count("</form>"))

    def test_focus_reason_ships_hidden_so_the_browser_can_reveal_it(self):
        """Server-only greying left the box stuck when focus mode changed
        in the browser; the rule and its reason must be present either
        way for the page to re-evaluate it live."""
        manual = self._page(focus="manual")
        self.assertIn('data-inert-when="autofocus"', manual)
        self.assertIn('class="why" hidden', manual)
        auto = self._page(focus="auto")
        self.assertIn('data-inert-when="autofocus"', auto)
        self.assertIn("Not used while Focus mode is Autofocus.", auto)

    def test_stills_mode_estimates_when_the_card_fills(self):
        page = self._page(mode="progressive_jpeg")
        self.assertIn("average 50 KB", page)
        self.assertIn("not pruned automatically", page)

    def test_preset_help_is_one_sentence(self):
        help_text = vs.field_for("video.preset")["help"]
        self.assertEqual(help_text.count("."), 1, help_text)


class TestStoragePanelReportsTheRingWindow(PatchMixin, unittest.TestCase):
    """Sprint18 fleet HIL (2026-08-19), finding 3.

    The panel and its live estimator both divided HEADROOM TO THE CAP by
    the burn rate, so a unit doing exactly what the ring buffer is for --
    sitting at its cap -- reported "keeps about 0.0 days of video" while
    holding 14.6 GiB of footage, and the bitrate selector was inert
    (1 / 4 / 12 Mbps all read 0.0 d on bmcam000 at cap 60).

    The panel now reports the ring WINDOW, and the estimator predicts
    from the cap ALLOWANCE rather than from current headroom.
    """

    # bmcam000 as observed: 68.8 of 114.7 GB used == exactly the 60% cap,
    # 14.57 GiB of that is prunable video, burning 1.01 GiB/hour.
    AT_CAP = {"used": 68.82, "total": 114.7, "cap_pct": 60.0,
              "days": 14.57 / 1.01 / 24, "gb_per_hour": 1.01,
              "retained_gb": 14.57, "clips": 282, "images": 0,
              "oldest": "2026-08-18T05:39:29Z"}

    def _page(self, disk=None, mode="video"):
        current = vs.read_current(self.yaml)
        current["capture_mode"] = mode
        return videoui_server.render_settings_page(
            current, disk=dict(disk or self.AT_CAP))

    def test_at_cap_the_sentence_is_not_zero(self):
        page = self._page()
        self.assertNotIn("keeps about <b>0.0 days</b>", page)
        self.assertIn("keeps about <b>14 hours</b> of video", page)

    def test_retained_footage_is_shown_as_the_evidence(self):
        """A reviewer must be able to check the division on the panel."""
        page = self._page()
        self.assertIn("Footage kept", page)
        self.assertIn("14.6 GB", page)
        self.assertIn("~1.0 GB/hour", page)

    def test_the_estimator_gets_the_retained_bytes(self):
        """Without retainedGb the client cannot separate video the ring
        can reclaim from bytes it cannot, and falls back to headroom."""
        page = self._page()
        self.assertIn('"retainedGb": 14.57', page)

    def test_the_client_no_longer_predicts_from_headroom(self):
        """The zeroed-by-construction formula must be gone from the page,
        not merely compensated for elsewhere."""
        page = self._page()
        self.assertNotIn("M.total*cap/100-M.used", page)
        self.assertIn("var other=M.used-(M.retainedGb||0)", page)
        self.assertIn("var allow=M.total*cap/100-other", page)

    def test_measured_sentence_is_restored_when_nothing_is_selected(self):
        """"Measured vs predicted" is the distinction Sprint18 built: the
        measured figure must not drift when the selector returns home."""
        page = self._page()
        self.assertIn("var measuredHTML=", page)
        self.assertIn("vnote.innerHTML=measuredHTML", page)

    def test_a_unit_below_its_cap_still_reads_as_a_window(self):
        disk = dict(self.AT_CAP, used=38.4, cap_pct=75.0,
                    retained_gb=60.0, gb_per_hour=1.0,
                    days=60.0 / 1.0 / 24)
        self.assertIn("keeps about <b>2.5 days</b> of video",
                      self._page(disk))

    def test_no_history_still_degrades_honestly(self):
        disk = dict(self.AT_CAP, days=None, gb_per_hour=None,
                    retained_gb=0.0)
        page = self._page(disk)
        self.assertIn("Not enough recording history", page)
        self.assertNotIn("Footage kept", page)

    def test_stills_note_at_cap_does_not_claim_zero_days(self):
        """Same at-cap state, the other sentence: stills are never pruned
        so headroom IS their model, but at the cap there is none to
        divide and the panel printed "about 0 days"."""
        disk = dict(self.AT_CAP, stills_gb_per_hour=0.0026,
                    stills_mean_kb=50.0)
        page = self._page(disk, mode="progressive_jpeg")
        self.assertNotIn("about <b>0 days</b>", page)
        self.assertNotIn("about <b>-", page)
        self.assertIn("already reached its 60% mark", page)
        self.assertIn("not pruned automatically", page)

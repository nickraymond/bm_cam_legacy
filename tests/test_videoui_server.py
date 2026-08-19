#!/usr/bin/env python3
# filename: test_videoui_server.py
# description: Sprint15 chunk 3 — gallery server: routes, Range, hardening.
"""
Sprint15/18 UI server tests against a live ThreadingHTTPServer on an
ephemeral port (stdlib urllib client, loopback only).

Covers: gallery page, manifest (present + fallback), full-file serving,
single-range 206s (start-end, open-ended, suffix), 416 on bad ranges,
and the serving rules (traversal, dotfiles, debris suffixes, unknown
extensions all 404).

Sprint18 adds: the stills gallery (/images.json, /images/<name>), the
per-item detail routes (/clip/<stem>.json, /photo/<stem>.json), and the
live storage block injected into /manifest.json.

Run: python3 -m unittest tests.test_videoui_server -v
"""

import contextlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
import urllib.error
import urllib.request

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "BM_Devel_Pi"))

import videoui_server  # noqa: E402

BASE = "2026-08-17T23-40-00Z_video_1000x562_15fps"
MP4_BYTES = bytes(range(256)) * 4          # 1024 recognizable bytes


class TestVideoUIServer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="vidui_test_")
        with open(os.path.join(cls.dir, BASE + ".mp4"), "wb") as f:
            f.write(MP4_BYTES)
        with open(os.path.join(cls.dir, BASE + "_thumb.jpg"), "wb") as f:
            f.write(b"j" * 64)
        with open(os.path.join(cls.dir, "in-flight.h264.part"), "wb") as f:
            f.write(b"x" * 10)
        with open(os.path.join(cls.dir, "manifest.json"), "w") as f:
            json.dump({"schema": "bmcam_video_manifest_v1", "count": 1,
                       "clips": [{"name": BASE + ".mp4"}]}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.server = videoui_server.start_ui_server(
                cls.dir, 0, host="127.0.0.1")
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    def _get(self, path, headers=None):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}", headers=headers or {})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), resp.read()
        except urllib.error.HTTPError as err:
            return err.code, dict(err.headers), err.read()

    def test_gallery_page(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers["Content-Type"])
        self.assertIn(b"Nereus Vision", body)      # Sprint18 branding
        self.assertIn(b"/manifest.json", body)
        self.assertIn(b"/images.json", body)        # Sprint18 stills tab

    def test_manifest_served(self):
        status, headers, body = self._get("/manifest.json")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["count"], 1)
        self.assertEqual(headers["Cache-Control"], "no-store")

    def test_full_video_download(self):
        status, headers, body = self._get(f"/videos/{BASE}.mp4")
        self.assertEqual(status, 200)
        self.assertEqual(body, MP4_BYTES)
        self.assertEqual(headers["Content-Type"], "video/mp4")
        self.assertEqual(headers["Accept-Ranges"], "bytes")

    def test_thumb_served(self):
        status, headers, body = self._get(f"/videos/{BASE}_thumb.jpg")
        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertEqual(len(body), 64)

    def test_range_start_end(self):
        status, headers, body = self._get(
            f"/videos/{BASE}.mp4", {"Range": "bytes=10-19"})
        self.assertEqual(status, 206)
        self.assertEqual(body, MP4_BYTES[10:20])
        self.assertEqual(headers["Content-Range"], "bytes 10-19/1024")
        self.assertEqual(headers["Content-Length"], "10")

    def test_range_open_ended(self):
        status, headers, body = self._get(
            f"/videos/{BASE}.mp4", {"Range": "bytes=1000-"})
        self.assertEqual(status, 206)
        self.assertEqual(body, MP4_BYTES[1000:])
        self.assertEqual(headers["Content-Range"], "bytes 1000-1023/1024")

    def test_range_suffix(self):
        status, headers, body = self._get(
            f"/videos/{BASE}.mp4", {"Range": "bytes=-16"})
        self.assertEqual(status, 206)
        self.assertEqual(body, MP4_BYTES[-16:])

    def test_range_beyond_eof_416(self):
        status, headers, _ = self._get(
            f"/videos/{BASE}.mp4", {"Range": "bytes=99999-"})
        self.assertEqual(status, 416)
        self.assertEqual(headers["Content-Range"], "bytes */1024")

    def test_hardening_404s(self):
        for path in (
            "/videos/../camera_schedule.yaml",
            "/videos/..%2f..%2fetc%2fpasswd",
            "/videos/.hidden.mp4",
            "/videos/in-flight.h264.part",
            "/videos/nonexistent.mp4",
            "/videos/notes.txt",
            "/etc/passwd",
            "/videos/",
        ):
            status, _, _ = self._get(path)
            self.assertEqual(status, 404, f"path {path} should 404")

    def test_missing_manifest_falls_back_empty(self):
        os.rename(os.path.join(self.dir, "manifest.json"),
                  os.path.join(self.dir, "manifest.json.bak"))
        try:
            status, _, body = self._get("/manifest.json")
            self.assertEqual(status, 200)
            self.assertEqual(json.loads(body)["clips"], [])
        finally:
            os.rename(os.path.join(self.dir, "manifest.json.bak"),
                      os.path.join(self.dir, "manifest.json"))


if __name__ == "__main__":
    unittest.main()


IMG_STEM = "2026-08-01T17-49-30Z_image"
IMG_NAME = IMG_STEM + "_compressed.jpg"


class TestStillsGallery(unittest.TestCase):
    """Sprint18: the Images tab and the per-item detail routes."""

    @classmethod
    def setUpClass(cls):
        cls.vdir = tempfile.mkdtemp(prefix="vidui_v_")
        cls.idir = tempfile.mkdtemp(prefix="vidui_i_")
        with open(os.path.join(cls.vdir, BASE + ".mp4"), "wb") as f:
            f.write(MP4_BYTES)
        # a full sidecar so /clip/<stem>.json has something to flatten
        with open(os.path.join(cls.vdir, BASE + ".json"), "w") as f:
            json.dump({"metadata_schema": "bmcam_video_sidecar_v2",
                       "tmp": 42.4, "du": 17.8, "dt": 114.7, "rd": 0,
                       "encode_s": 300.6, "boundary_s": 12.7,
                       "sensor_mode": "2304x1296", "avail_px": "2304x1296",
                       "encoder": {"denoise": "cdn_hq", "sharpness": 1.0},
                       "sha256_16": "5abde7208c22fb0e",
                       "crop_native_xywh": [1504, 846, 1600, 900],
                       "requested_controls": {
                           "requested_focus_mode": "auto",
                           "requested_lens_position": 1.82}}, f)
        with open(os.path.join(cls.vdir, "manifest.json"), "w") as f:
            json.dump({"schema": "bmcam_video_manifest_v1", "count": 1,
                       "clips": [{"name": BASE + ".mp4", "bytes": 1024,
                                  "utc": "2026-08-17T23:40:00Z"}]}, f)
        # two stills, newest second on disk to prove the sort
        for stem, utc, q in ((IMG_STEM, "2026-08-01T17:49:36Z", 80),
                             ("2026-07-31T09-00-00Z_image",
                              "2026-07-31T09:00:05Z", 50)):
            with open(os.path.join(cls.idir, stem + "_compressed.jpg"),
                      "wb") as f:
                f.write(b"jpegdata" * 8)
            with open(os.path.join(
                    cls.idir, stem + "_compressed.jpg.capture_metadata.json"),
                    "w") as f:
                json.dump({"output_size": [1000, 562], "jpeg_quality_used": q,
                           "utc_capture_timestamp": utc, "enc_attempts": 2,
                           "selector_reason": "fit", "img_format": "pjpg",
                           "message_count": 176, "Lux": 193.13,
                           "ExposureTime": 46954, "AnalogueGain": 2.0,
                           "ColourTemperature": 3537, "LensPosition": 1.82,
                           "FocusFoM": 12371, "SensorTemperature": 26.0,
                           "crop_native_xywh": [1504, 846, 1600, 900],
                           "ScalerCrop": "(0, 0)/4608x2592",
                           "jpeg_sha256": "72b49b7c98c55938cc"}, f)
        # a stranger the gallery must ignore
        with open(os.path.join(cls.idir, "notes.txt"), "w") as f:
            f.write("not an image")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.server = videoui_server.start_ui_server(
                cls.vdir, 0, host="127.0.0.1", images_dir=cls.idir)
        cls.port = cls.server.server_address[1]

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.vdir, ignore_errors=True)
        shutil.rmtree(cls.idir, ignore_errors=True)

    def _get(self, path):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, resp.read()
        except urllib.error.HTTPError as err:
            return err.code, err.read()

    def test_images_json_lists_newest_first(self):
        status, body = self._get("/images.json")
        self.assertEqual(status, 200)
        data = json.loads(body)
        self.assertEqual(data["count"], 2)
        self.assertEqual(data["images"][0]["name"], IMG_NAME)
        self.assertEqual(data["images"][0]["res"], "1000x562")
        self.assertEqual(data["images"][0]["quality"], 80)
        # the .txt stranger never appears
        self.assertTrue(all(e["name"].endswith("_compressed.jpg")
                            for e in data["images"]))

    def test_image_file_served(self):
        status, body = self._get("/images/" + IMG_NAME)
        self.assertEqual(status, 200)
        self.assertEqual(body, b"jpegdata" * 8)

    def test_image_hardening(self):
        for bad in ("/images/../notes.txt", "/images/notes.txt",
                    "/images/.hidden.jpg", "/images/nope.jpg"):
            status, _ = self._get(bad)
            self.assertEqual(status, 404, bad)

    def test_photo_detail_route(self):
        status, body = self._get(f"/photo/{IMG_STEM}.json")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["quality"], 80)
        self.assertEqual(d["msgs"], 176)
        self.assertEqual(d["reason"], "fit")
        self.assertEqual(d["sha"], "72b49b7c98c55938")  # trimmed to 16
        self.assertAlmostEqual(d["lux"], 193.13, places=2)

    def test_clip_detail_route(self):
        status, body = self._get(f"/clip/{BASE}.json")
        self.assertEqual(status, 200)
        d = json.loads(body)
        self.assertEqual(d["tmp"], 42.4)
        self.assertEqual(d["boundary_s"], 12.7)
        self.assertEqual(d["focus_mode"], "auto")
        self.assertEqual(d["enc"]["denoise"], "cdn_hq")
        self.assertEqual(d["sensor_mode"], "2304x1296")

    def test_detail_routes_404_on_unknown_and_traversal(self):
        for bad in ("/clip/nope.json", "/photo/nope.json",
                    "/clip/..%2F..%2Fetc%2Fpasswd.json"):
            status, _ = self._get(bad)
            self.assertEqual(status, 404, bad)

    def test_manifest_carries_live_storage(self):
        status, body = self._get("/manifest.json")
        self.assertEqual(status, 200)
        disk = json.loads(body)["disk"]
        self.assertIn("used", disk)
        self.assertIn("total", disk)
        self.assertEqual(disk["cap_pct"], 75)   # no config_path -> default
        self.assertGreater(disk["total"], 0)

    def test_cap_comes_from_the_units_config_not_the_default(self):
        """A unit configured to 60% must not be drawn against 75%: the
        gauge marker and the retention estimate both hang off this."""
        cfg = os.path.join(self.vdir, "camera_schedule.yaml")
        with open(cfg, "w") as f:
            f.write("video:\n  storage:\n    max_used_pct: 60\n"
                    "    min_free_gb: 10\n")
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            srv = videoui_server.start_ui_server(
                self.vdir, 0, host="127.0.0.1", images_dir=self.idir,
                config_path=cfg)
        try:
            port = srv.server_address[1]
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/manifest.json", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["disk"]["cap_pct"], 60)
        finally:
            srv.shutdown()
            srv.server_close()

    def test_download_flag_sets_content_disposition(self):
        """A bare <a download> is ignored by iOS Safari and defeated by
        the colons in stills filenames; the header is what actually works."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/images/{IMG_NAME}?dl=1")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertEqual(resp.status, 200)
            self.assertIn("attachment", resp.headers["Content-Disposition"])
            self.assertIn(IMG_NAME, resp.headers["Content-Disposition"])
        # video route too
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/videos/{BASE}.mp4?dl=1")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertIn("attachment", resp.headers["Content-Disposition"])

    def test_plain_view_has_no_disposition(self):
        """Inline viewing (posters, the <video> element) must NOT be sent
        as an attachment or the gallery would download every thumbnail."""
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}/images/{IMG_NAME}")
        with urllib.request.urlopen(req, timeout=5) as resp:
            self.assertIsNone(resp.headers.get("Content-Disposition"))

    def test_images_list_carries_the_detail_stem(self):
        """The card fetches /photo/<stem>.json using this field. Deriving
        it by stripping the extension leaves a "_compressed" tail and
        404s -- that bug shipped and showed as empty stills metadata."""
        status, body = self._get("/images.json")
        entry = json.loads(body)["images"][0]
        self.assertEqual(entry["stem"], IMG_STEM)
        self.assertTrue(entry["name"].startswith(entry["stem"]))
        status, _ = self._get(f"/photo/{entry['stem']}.json")
        self.assertEqual(status, 200)

    def test_missing_images_dir_yields_empty_tab(self):
        """A stills-less unit must show an empty Images tab, never a 500."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            srv = videoui_server.start_ui_server(
                self.vdir, 0, host="127.0.0.1",
                images_dir="/nonexistent/path/xyz")
        try:
            port = srv.server_address[1]
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/images.json", timeout=5) as r:
                self.assertEqual(json.loads(r.read())["count"], 0)
        finally:
            srv.shutdown()
            srv.server_close()


class TestRingWindowAtCap(unittest.TestCase):
    """Sprint18 fleet HIL (2026-08-19), finding 3 — the retention figure
    collapsed to zero exactly where customers sit.

    The panel divided HEADROOM TO THE CAP by the burn rate. The ring
    buffer's whole job is to drive used% up to the cap, so at steady
    state headroom -> 0 and the number was zeroed by the ring succeeding.
    Observed live on bmcam000 and bmcam004 at cap 60%: every bitrate from
    1 to 12 Mbps read "keeps ~0.0 days" while the units held 14.6 and
    26.6 GiB of footage.

    The figure now reports the ring WINDOW: retained video / measured
    burn. The measured burn itself was honest and is not touched here.
    """

    GIB = 1024 ** 3
    TOTAL = 114.7 * GIB
    CAP = 60.0

    def _stats(self, clips, used_frac=CAP / 100, cap=CAP, total=TOTAL):
        """storage_stats() against a synthetic card. used_frac defaults to
        sitting exactly ON the cap, i.e. headroom == 0."""
        fake = type("Usage", (), {"total": int(total),
                                  "used": int(total * used_frac),
                                  "free": int(total * (1 - used_frac))})()
        real = videoui_server.shutil.disk_usage
        videoui_server.shutil.disk_usage = lambda _p: fake
        try:
            return videoui_server.storage_stats(".", clips,
                                                {"max_used_pct": cap})
        finally:
            videoui_server.shutil.disk_usage = real

    def _clips(self, n=282, gib_per_clip=14.57 / 282, period_s=300):
        """Newest-first clips, evenly spaced — the manifest's own order."""
        base = 1755576000                     # fixed epoch, no wall clock
        step = period_s
        return [{"utc": videoui_server.time.strftime(
                    "%Y-%m-%dT%H:%M:%SZ",
                    videoui_server.time.gmtime(base - i * step)),
                 "bytes": int(gib_per_clip * self.GIB)}
                for i in range(n)]

    # ---- the regression this ticket exists for -----------------------
    def test_at_cap_with_prunable_footage_the_window_is_not_zero(self):
        clips = self._clips()
        disk = self._stats(clips)
        headroom = disk["total"] * disk["cap_pct"] / 100 - disk["used"]
        self.assertAlmostEqual(headroom, 0.0, places=6,
                               msg="fixture must sit ON the cap")
        self.assertIsNotNone(disk["days"])
        self.assertGreater(disk["days"], 0.0,
                           "a card full of prunable footage keeps footage")
        self.assertGreater(disk["retained_gb"], 14.0)
        self.assertIsNotNone(videoui_server.retention_text(disk["days"]))

    def test_window_is_retained_footage_over_measured_burn(self):
        disk = self._stats(self._clips())
        expected = disk["retained_gb"] / (disk["gb_per_hour"] * 24)
        self.assertAlmostEqual(disk["days"], expected, places=9)

    def test_measured_window_does_not_move_with_the_cap(self):
        """The MEASURED figure describes footage the camera is holding
        now; only the PREDICTED (selector) figure may follow the cap."""
        clips = self._clips()
        at_60 = self._stats(clips, cap=60.0)
        at_90 = self._stats(clips, cap=90.0, used_frac=0.60)
        self.assertAlmostEqual(at_60["days"], at_90["days"], places=9)

    def test_burn_rate_still_measured_from_recent_clips(self):
        """Guard the part that was NOT broken: the burn rate tracked the
        real daylight rise on the fleet and must keep doing so."""
        slow = self._stats(self._clips(gib_per_clip=0.05))
        fast = self._stats(self._clips(gib_per_clip=0.20))
        self.assertAlmostEqual(fast["gb_per_hour"] / slow["gb_per_hour"],
                               4.0, places=6)

    def test_over_cap_still_reports_a_window(self):
        """bmcam004 carried a nudge file and sat slightly OVER the cap;
        negative headroom must not produce a negative or zero window."""
        disk = self._stats(self._clips(gib_per_clip=26.60 / 282),
                           used_frac=0.63)
        self.assertGreater(disk["days"], 0.0)

    def test_no_footage_degrades_to_no_claim(self):
        self.assertIsNone(self._stats([])["days"])
        self.assertEqual(self._stats([])["retained_gb"], 0.0)

    def test_clips_without_sizes_make_no_claim(self):
        clips = [dict(c, bytes=0) for c in self._clips(n=4)]
        self.assertIsNone(self._stats(clips)["days"])


class TestRetentionText(unittest.TestCase):
    """A 14-hour ring window printed as "0.6 days" is true and useless;
    sub-day windows are the NORMAL case on a unit at its cap."""

    def test_days_above_one(self):
        self.assertEqual(videoui_server.retention_text(1.55), "1.6 days")

    def test_sub_day_reads_in_hours(self):
        self.assertEqual(videoui_server.retention_text(14.4 / 24), "14 hours")
        self.assertEqual(videoui_server.retention_text(0.5), "12 hours")

    def test_sub_hour_reads_in_minutes(self):
        self.assertEqual(videoui_server.retention_text(0.5 / 24),
                         "30 minutes")

    def test_one_hour_is_singular(self):
        self.assertEqual(videoui_server.retention_text(1.2 / 24), "1 hour")

    def test_none_passes_through(self):
        self.assertIsNone(videoui_server.retention_text(None))


# ---------------------------------------------------------------------------
# Sprint18 defect: gallery renders empty when /images.json wins the race
# ---------------------------------------------------------------------------
# Found on the 2026-08-19 fleet HIL run (bmcam000 and bmcam004): the page
# arrived showing "showing 0 of 0 (269 total)" and "Nothing in that window."
# load() fires both fetches concurrently and BOTH call render(). Whichever
# lands first runs buildFilters() against DATA[S.media]; if that list is
# still null the date range is seeded from an empty array and every later
# filter compare is false. Measured on bmcam004: /images.json at 736 ms,
# /manifest.json at 2006 ms -- images wins on any unit with more clips than
# stills, which is why bench validation on bmcam003 (real stills) missed it.
#
# A source-string check cannot see an ordering bug, so this runs the page's
# own JavaScript against a headless DOM shim and counts the cards render()
# actually produces. No engine is installed on the Pi, so the JS half skips
# there; test_date_range_init_tolerates_undefined below always runs.

JS_ENGINE_CANDIDATES = (
    "/System/Library/Frameworks/JavaScriptCore.framework/Versions/A/"
    "Helpers/jsc",                       # ships with macOS, no install
)


def _find_js_engine():
    """Return a path/name of a usable JS engine, or None to skip."""
    for path in JS_ENGINE_CANDIDATES:
        if os.path.exists(path):
            return path
    for name in ("node", "deno"):
        found = shutil.which(name)
        if found:
            return found
    return None


class TestGalleryLoadRace(unittest.TestCase):
    """Render the real gallery script with the two fetches resolving in a
    chosen order and assert the clip cards are there."""

    CLIPS_PER_DAY = 20
    DAYS = ("2026-08-19", "2026-08-18")
    PAGE_SIZE = 26                        # PAGE in the gallery script

    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp(prefix="vidui_race_")
        with open(os.path.join(cls.dir, "manifest.json"), "w") as f:
            json.dump({"schema": "bmcam_video_manifest_v1", "count": 0,
                       "clips": []}, f)
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cls.server = videoui_server.start_ui_server(
                cls.dir, 0, host="127.0.0.1")
        cls.port = cls.server.server_address[1]
        cls.engine = _find_js_engine()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()
        shutil.rmtree(cls.dir, ignore_errors=True)

    # -- payloads ---------------------------------------------------------
    @classmethod
    def _manifest(cls):
        """A fleet-shaped manifest: many clips, two days, newest first."""
        clips = []
        for day in cls.DAYS:
            for i in range(cls.CLIPS_PER_DAY):
                stem = f"{day}T{23 - i:02d}-00-00Z_video"
                clips.append({
                    "name": stem + ".mp4", "thumb": stem + "_thumb.jpg",
                    "utc": f"{day}T{23 - i:02d}:00:00Z",
                    "dur": 300, "bytes": 150 * 1024 * 1024,
                    "res": "1920x1080", "fps": 15, "br": 6.0,
                    "scale": 0.833, "preset": "wide_1080p_lean"})
        return {"schema": "bmcam_video_manifest_v1", "count": len(clips),
                "clips": clips,
                "disk": {"used": 30.0, "total": 58.0, "cap_pct": 60,
                         "days": 2.0}}

    # -- harness ----------------------------------------------------------
    def _page_scripts(self):
        """The <script> bodies of the page as actually served."""
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        blocks = re.findall(r"<script>(.*?)</script>", html, re.S)
        self.assertGreaterEqual(len(blocks), 3, "gallery scripts not found")
        return "\n".join(blocks)

    def _run_gallery(self, first, second):
        """Execute the page with `first` resolving before `second`.

        Returns the engine's stdout. Ordering uses microtask ticks, not
        timers, so it is deterministic -- no sleeps, no flakiness.
        """
        shim = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "fixtures", "videoui_dom_shim.js")
        with open(shim, encoding="utf-8") as f:
            shim_src = f.read()
        payloads = {"/manifest.json": self._manifest(),
                    "/images.json": {"schema": "bmcam_images_v1",
                                     "count": 0, "images": []}}
        total = len(payloads["/manifest.json"]["clips"])
        driver = """
var PAYLOADS = %s, EXPECT_CARDS = %d, EXPECT_COUNT = "showing %d of %d";
check("both endpoints requested",
  FETCHED.indexOf("/manifest.json") >= 0 &&
  FETCHED.indexOf("/images.json") >= 0, FETCHED.join(","));
tick(4).then(function () {
  PENDING["%s"](PAYLOADS["%s"]);          /* this one wins the race */
  return tick(8);
}).then(function () {
  PENDING["%s"](PAYLOADS["%s"]);          /* the slow one lands late */
  return tick(8);
}).then(function () {
  check("clip cards present", cards().length === EXPECT_CARDS,
        "got " + cards().length);
  check("count line reports the whole list",
        document.getElementById("fcount").textContent.indexOf(
          EXPECT_COUNT) === 0,
        document.getElementById("fcount").textContent);
  check("empty placeholder hidden",
        document.getElementById("empty").classList.contains("hide") === true);
  done();
}).catch(function (e) {
  print("FAIL - threw: " + e); print("RESULT FAIL 1");
});
""" % (json.dumps(payloads), self.PAGE_SIZE, self.PAGE_SIZE, total,
       first, first, second, second)
        bundle = os.path.join(self.dir, "bundle.js")
        with open(bundle, "w", encoding="utf-8") as f:
            f.write(shim_src + "\n" + self._page_scripts() + "\n" + driver)
        proc = subprocess.run([self.engine, bundle], capture_output=True,
                              text=True, timeout=60)
        return proc.stdout + proc.stderr

    def _assert_gallery_renders(self, first, second):
        if not self.engine:
            self.skipTest("no JavaScript engine available (jsc/node/deno)")
        out = self._run_gallery(first, second)
        self.assertIn("RESULT PASS", out,
                      f"{first} first -> gallery did not render:\n{out}")

    # -- the regression ---------------------------------------------------
    def test_gallery_renders_when_images_json_resolves_first(self):
        """THE regression. With /images.json first and no stills on the
        unit, the date range used to be seeded from an empty list and the
        gallery stayed empty for the whole session."""
        self._assert_gallery_renders("/images.json", "/manifest.json")

    def test_gallery_renders_when_manifest_resolves_first(self):
        """The ordering that always worked -- kept so a fix for the race
        cannot break the common case."""
        self._assert_gallery_renders("/manifest.json", "/images.json")

    def test_date_range_init_tolerates_undefined(self):
        """Runs everywhere, including on the Pi where no JS engine exists.
        `min`/`max` are undefined whenever the other list has not landed,
        so a === null test leaves the range permanently undefined."""
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/", timeout=5) as resp:
            html = resp.read().decode("utf-8")
        self.assertIn("if(S.dFrom==null)S.dFrom=min;", html)
        self.assertIn("if(S.dTo==null)S.dTo=max;", html)
        self.assertNotIn("if(S.dFrom===null)", html)
        self.assertNotIn("if(S.dTo===null)", html)

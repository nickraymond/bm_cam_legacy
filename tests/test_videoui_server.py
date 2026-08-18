#!/usr/bin/env python3
# filename: test_videoui_server.py
# description: Sprint15 chunk 3 — gallery server: routes, Range, hardening.
"""
Sprint15 UI server tests against a live ThreadingHTTPServer on an
ephemeral port (stdlib urllib client, loopback only).

Covers: gallery page, manifest (present + fallback), full-file serving,
single-range 206s (start-end, open-ended, suffix), 416 on bad ranges,
and the serving rules (traversal, dotfiles, debris suffixes, unknown
extensions all 404).

Run: python3 -m unittest tests.test_videoui_server -v
"""

import contextlib
import io
import json
import os
import shutil
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
        self.assertIn(b"bmcam video", body)
        self.assertIn(b"/manifest.json", body)

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

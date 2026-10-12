#!/usr/bin/env python3
# filename: test_pool_preview.py
# description: Pool test 2026-10-13 — round-trip tests for tools/pool_preview.py (PVH/PV/PVE console reassembly).
"""
tools/pool_preview.py gate: a real progressive JPEG (Pillow) goes through the agreed wire
format as console log lines (with the monitor/Spotter prefixes seen in runs/*), and comes back:

  1. clean round trip -> OK, bytes identical, sha256 matches, Pillow opens it progressive;
  2. dropped lines -> PARTIAL, best-effort prefix opens (EOI appended), missing ranges +
     a <= 248 B resend command printed; the resent lines (PVH, subset, PVE) merge -> OK;
  3. corrupted lines (crc mismatch, console "..." truncation) -> counted missing, resend fixes;
  4. lines from two units interleaved line by line -> two independent OK images;
  5. a lost PVE is finalised by the idle timer; a lost PVH still rebuilds (PARTIAL, no sha);
  6. the tailer follows a growing log file (partial last line) and the web page renders.

Run (repo root):  python3 -m pytest -q tests/test_pool_preview.py
"""
import hashlib
import io
import json
import os
import random
import re
import sys
import tempfile
import time
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))

from PIL import Image  # noqa: E402

import pool_preview as pp  # noqa: E402


def make_pjpeg(seed=1, size=(320, 180), q=40):
    """A noisy-ish progressive JPEG so it spans many lines."""
    rnd = random.Random(seed)
    img = Image.new("RGB", size)
    px = img.load()
    for y in range(size[1]):
        for x in range(size[0]):
            px[x, y] = ((x * 3 + seed * 40) % 256, (y * 5) % 256, rnd.randrange(256))
    buf = io.BytesIO()
    img.save(buf, "JPEG", quality=q, progressive=True)
    return buf.getvalue()


def as_log(lines, t0=1791900000):
    """Wrap unit console lines the way the monitor + Spotter do (incl. junk prefixes)."""
    out = []
    for i, line in enumerate(lines):
        junk = ["", "Message: Message: ", "."][i % 3]
        out.append(f"2026-10-13T18:{(i // 60) % 60:02d}:{i % 60:02d}Z {junk}{t0 + i}.123 "
                   f"53171fa3d81a8e6f, {line}")
    return out


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = self.tmp.name
        self.logs = []
        self.store = pp.Store(self.out, idle_s=0.2, cmd_hint="pool_cmd.py", log=self.logs.append)

    def tearDown(self):
        self.tmp.cleanup()

    def feed(self, lines):
        for line in as_log(lines):
            self.store.feed(line)

    def result(self, key):
        with open(os.path.join(self.out, key + ".json")) as f:
            return json.load(f)

    def jpg(self, key):
        with open(os.path.join(self.out, key + ".jpg"), "rb") as f:
            return f.read()


class TestRoundTrip(Base):
    def test_clean_round_trip(self):
        jpeg = make_pjpeg()
        lines = pp.encode_preview_lines("bmcam003", "181500", jpeg, 320, 180, 40)
        self.assertGreater(len(lines), 10)
        for line in lines:  # every line must fit command_wire.MAX_CONSOLE_CHARS
            self.assertLessEqual(len(line), 240)
        self.feed(lines)
        r = self.result("bmcam003_181500")
        self.assertEqual(r["status"], "OK")
        self.assertEqual(self.jpg("bmcam003_181500"), jpeg)
        self.assertEqual(r["sha256"], hashlib.sha256(jpeg).hexdigest())
        im = Image.open(io.BytesIO(self.jpg("bmcam003_181500")))
        im.load()
        self.assertEqual(im.size, (320, 180))
        self.assertTrue(im.info.get("progressive") or im.info.get("progression"))
        self.assertEqual(r["resend_lines"], [])
        # times come from the monitor's log prefix (when the unit sent), not the parse time
        t18 = 1791914400   # 2026-10-13T18:00:00Z, as_log()'s first line
        self.assertGreaterEqual(r["first_seen_epoch"], t18)
        self.assertLess(r["received_epoch"], t18 + 3600)

    def test_unaligned_chunks_decode(self):
        jpeg = make_pjpeg(seed=2)
        lines = pp.encode_preview_lines("bmcam004", "181501", jpeg, 320, 180, 40, chunk_chars=157)
        self.feed(lines)
        self.assertEqual(self.result("bmcam004_181501")["status"], "OK")


class TestDropAndResend(Base):
    def test_dropped_lines_partial_then_resend_merges(self):
        jpeg = make_pjpeg(seed=3)
        lines = pp.encode_preview_lines("bmcam003", "182000", jpeg, 320, 180, 40)
        n = len(lines) - 2
        drop = {n // 2, n // 2 + 1, n // 2 + 2, n - 1}          # 1-based PV seqs
        first = [l for i, l in enumerate(lines) if not (1 <= i <= n and i in drop)]
        self.feed(first)
        key = "bmcam003_182000"
        r = self.result(key)
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["lines_received"], n - 4)
        self.assertEqual(r["contiguous_prefix"], n // 2 - 1)
        self.assertEqual(r["missing"], f"{n // 2}-{n // 2 + 2},{n - 1}")
        self.assertEqual(r["resend_lines"], [f"{n // 2}-{n // 2 + 2},{n - 1}"])
        self.assertTrue(r["eoi_appended"])
        best = self.jpg(key)
        self.assertTrue(best.endswith(b"\xff\xd9"))
        self.assertTrue(jpeg.startswith(best[:-2]))
        Image.open(io.BytesIO(best)).load()   # best-effort prefix must open
        self.assertTrue(any(f"preview --lines {n // 2}-{n // 2 + 2},{n - 1}" in m for m in self.logs))
        # resend: PVH, only the missing PV lines, PVE
        resend = [lines[0]] + [lines[s] for s in sorted(drop)] + [lines[-1]]
        self.feed(resend)
        r = self.result(key)
        self.assertEqual(r["status"], "OK")
        self.assertEqual(r["passes"], 2)
        self.assertEqual(self.jpg(key), jpeg)

    def test_resend_command_fits_pi_limits(self):
        # One number, both tools: 186 = the 256 B console line binds (JSON 248 B, Pi RE_LINES 200)
        import pool_cmd
        self.assertEqual(pp.MAX_LINES_CHARS, 186)
        self.assertEqual(pool_cmd.MAX_LINES_CHARS, pp.MAX_LINES_CHARS)
        pi_re = re.compile(r"[0-9,-]{1,200}\Z")      # #152 @ a23f4d6 rc_preview.py:60
        self.assertEqual(pp.RE_PI_LINES.pattern, pi_re.pattern)

        class Args:                                   # pool_cmd.build_json's argparse shape
            note = None; duration_s = interval_s = iterations = period_s = None; arg = []
        for missing in (list(range(1, 400, 2)),            # 200 single gaps: worst case
                        list(range(100, 260)),             # one long run
                        [5, 6, 7, 120, 121, 240, 241, 242, 243, 250]):
            parts = pp.resend_lines_args(missing)
            for p in parts:
                self.assertLessEqual(len(p), 186)
                self.assertTrue(pi_re.match(p), (len(p), p))
                Args.lines = p
                js, line = pool_cmd.build_json(99999, "preview", Args)   # dies (SystemExit) if over
                self.assertLessEqual(len(js), 248)
                self.assertLessEqual(len(line) + 1, 256)
        # the worst case actually uses the room (not a needlessly tiny split)
        self.assertGreater(max(len(p) for p in pp.resend_lines_args(list(range(1, 400, 2)))), 180)
        missing = list(range(1, 400, 2))
        parts = pp.resend_lines_args(missing)
        rebuilt = []
        for p in parts:
            for r in p.split(","):
                a, _, b = r.partition("-")
                rebuilt += list(range(int(a), int(b or a) + 1))
        self.assertEqual(rebuilt, missing)

    def test_ranges_str(self):
        self.assertEqual(pp.ranges_str([3, 4, 5, 7, 12, 13]), "3-5,7,12-13")
        self.assertEqual(pp.ranges_str([]), "")


class TestCorrupt(Base):
    def test_crc_mismatch_and_truncation_count_as_missing(self):
        jpeg = make_pjpeg(seed=4)
        lines = pp.encode_preview_lines("bmcam004", "183000", jpeg, 320, 180, 40)
        bad = list(lines)
        # seq 2: one flipped base64 char; seq 5: console_line truncation "..."
        h, sep, tail = bad[2].rpartition(" ")
        bad[2] = h + sep + ("A" if tail[0] != "A" else "B") + tail[1:]
        bad[5] = bad[5][:150] + "..."
        self.feed(bad)
        key = "bmcam004_183000"
        r = self.result(key)
        self.assertEqual(r["status"], "PARTIAL")
        self.assertEqual(r["missing"], "2,5")
        self.assertEqual(r["corrupt_seen"], "2,5")
        self.assertEqual(r["contiguous_prefix"], 1)
        self.assertTrue(any("CRC MISMATCH" in m for m in self.logs))
        self.feed([lines[0], lines[2], lines[5], lines[-1]])
        r = self.result(key)
        self.assertEqual(r["status"], "OK")
        self.assertEqual(r["corrupt_seen"], "")
        self.assertEqual(self.jpg(key), jpeg)

    def test_bad_sha_when_all_lines_present(self):
        jpeg = make_pjpeg(seed=5)
        lines = pp.encode_preview_lines("bmcam003", "183500", jpeg, 320, 180, 40)
        lines[0] = re.sub(r"sha256=[0-9a-f]{64}", "sha256=" + "0" * 64, lines[0])
        self.feed(lines)
        r = self.result("bmcam003_183500")
        self.assertEqual(r["status"], "BAD_SHA")
        self.assertEqual(r["resend_lines"], [f"1-{len(lines) - 2}"])


class TestInterleaved(Base):
    def test_two_units_interleaved(self):
        a = make_pjpeg(seed=6)
        b = make_pjpeg(seed=7, size=(240, 135))
        la = pp.encode_preview_lines("bmcam003", "184000", a, 320, 180, 40)
        lb = pp.encode_preview_lines("bmcam004", "184000", b, 240, 135, 40)
        mixed = []
        for i in range(max(len(la), len(lb))):
            if i < len(la):
                mixed.append(la[i])
            if i < len(lb):
                mixed.append(lb[i])
        self.feed(mixed)
        self.assertEqual(self.result("bmcam003_184000")["status"], "OK")
        self.assertEqual(self.result("bmcam004_184000")["status"], "OK")
        self.assertEqual(self.jpg("bmcam003_184000"), a)
        self.assertEqual(self.jpg("bmcam004_184000"), b)
        page = pp.render_page(self.store.snapshot(), ["bmcam003", "bmcam004"])
        self.assertIn("bmcam003", page)
        self.assertIn("/img/bmcam004_184000.jpg", page)
        self.assertEqual(page.count('class="OK"'), 4)    # 2 cards + 2 table rows


class TestLostFraming(Base):
    def test_lost_pve_finalised_by_idle(self):
        jpeg = make_pjpeg(seed=8)
        lines = pp.encode_preview_lines("bmcam003", "185000", jpeg, 320, 180, 40)
        self.feed(lines[:-1])
        self.assertFalse(os.path.exists(os.path.join(self.out, "bmcam003_185000.json")))
        time.sleep(0.3)
        self.store.tick()
        r = self.result("bmcam003_185000")
        self.assertEqual(r["status"], "OK")
        self.assertIn("idle", r["finalised_by"])

    def test_lost_pvh_still_rebuilds(self):
        jpeg = make_pjpeg(seed=9)
        lines = pp.encode_preview_lines("bmcam004", "185500", jpeg, 320, 180, 40)
        self.feed(lines[1:])
        r = self.result("bmcam004_185500")
        self.assertEqual(r["status"], "PARTIAL")       # bytes complete, sha unverifiable
        self.assertIn("no PVH", r["note"])
        self.assertEqual(self.jpg("bmcam004_185500"), jpeg)       # Pillow JPEGs already end in EOI

    def test_unrelated_lines_ignored(self):
        for line in ["2026-10-13T18:00:00Z 1791.1 abc, [bmcam003] OK id=90001 pool status",
                     '2026-10-13T18:00:01Z bm pub bmcam/cmd {"id":90002,"c":"pool","v":"preview"} 1 1',
                     "2026-10-13T18:00:02Z 1791.2 abc, power | 12.1V"]:
            self.assertIsNone(self.store.feed(line))


class TestTailer(Base):
    def test_tail_growing_file_with_partial_line(self):
        spot = os.path.join(self.out, "logs", "SPOT-33507C")
        os.makedirs(spot)
        path = os.path.join(spot, "console_20261013.log")
        open(path, "w").close()
        jpeg = make_pjpeg(seed=10)
        text = "\n".join(as_log(pp.encode_preview_lines("bmcam003", "190000", jpeg, 320, 180, 40))) + "\n"
        store = pp.Store(os.path.join(self.out, "pv"), log=self.logs.append)
        t = pp.Tailer(store, os.path.join(self.out, "logs"), log=self.logs.append)
        t.poll_once()
        cut = len(text) // 2 + 7          # mid-line
        with open(path, "a") as f:
            f.write(text[:cut])
        t.poll_once()
        self.assertEqual(store.snapshot(), [])
        with open(path, "a") as f:
            f.write(text[cut:])
        t.poll_once()
        snap = store.snapshot()
        self.assertEqual(len(snap), 1)
        self.assertEqual(snap[0]["status"], "OK")


class TestDisplayRotation(Base):
    """Per-unit display rotation: CSS only, the backend's CCW convention, live file re-read."""

    def feed_two(self):
        a, b = make_pjpeg(seed=20, size=(640, 360)), make_pjpeg(seed=21, size=(640, 360))
        self.feed(pp.encode_preview_lines("bmcam003", "200000", a, 640, 360, 40))
        self.feed(pp.encode_preview_lines("bmcam004", "200001", b, 640, 360, 40))
        return a, b

    def card(self, page, unit):
        start = page.index(f"<b>{unit}</b>")
        return page[start:page.index("</div>", page.index("<a href", start)) + 6]

    def test_args_parse(self):
        self.assertEqual(pp.parse_rotation_args(["bmcam004=90", "bmcam003=270"]), {"bmcam004": 90, "bmcam003": 270})
        self.assertEqual(pp.parse_rotation_args(["bmcam004=45", "x", "bmcam003=abc"], log=self.logs.append), {})
        self.assertEqual(sum("ignoring --rotate" in m for m in self.logs), 3)

    def test_css_only_bytes_and_sha_untouched(self):
        a, b = self.feed_two()
        before = {k: open(os.path.join(self.out, k + ".jpg"), "rb").read() for k in ("bmcam003_200000", "bmcam004_200001")}
        page = pp.render_page(self.store.snapshot(), ["bmcam003", "bmcam004"], rotations={"bmcam004": 90})
        c4, c3 = self.card(page, "bmcam004"), self.card(page, "bmcam003")
        self.assertIn('class="frame r90"', c4)
        self.assertIn("aspect-ratio:360 / 640", c4)            # the turned card is portrait
        self.assertIn("shown rotated 90&deg; CCW", c4)
        self.assertNotIn("frame r", c3)                        # default 0: unchanged markup
        self.assertIn(".frame.r90 img{transform:translate(-50%,-50%) rotate(-90deg)}", page)   # CCW, like gallery.html
        self.assertEqual(before["bmcam004_200001"], b)
        self.assertEqual(open(os.path.join(self.out, "bmcam004_200001.jpg"), "rb").read(), b)
        self.assertEqual(self.result("bmcam004_200001")["sha256"], hashlib.sha256(b).hexdigest())
        for deg in (180, 270):
            page = pp.render_page(self.store.snapshot(), ["bmcam004"], rotations={"bmcam004": deg})
            self.assertIn(f'class="frame r{deg}"', page)

    def test_file_overrides_flags_live_and_bad_file_kept(self):
        path = os.path.join(self.out, "display_rotation.json")
        cfg = pp.RotationConfig(path, {"bmcam004": 180, "bmcam003": 90}, log=self.logs.append)
        self.assertEqual(cfg.get(), {"bmcam004": 180, "bmcam003": 90})          # no file: flags
        with open(path, "w") as f:
            f.write('{"bmcam004": 90}')
        self.assertEqual(cfg.get(), {"bmcam004": 90, "bmcam003": 90})           # file wins per unit
        with open(path, "w") as f:
            f.write('{"bmcam004": 45}')
        os.utime(path, (time.time() + 5, time.time() + 5))
        self.assertEqual(cfg.get(), {"bmcam004": 90, "bmcam003": 90})           # bad file: keep last good
        self.assertIn("not 0/90/180/270", cfg.error)
        with open(path, "w") as f:
            f.write('{"bmcam004": 270}')
        os.utime(path, (time.time() + 10, time.time() + 10))
        self.assertEqual(cfg.get()["bmcam004"], 270)
        self.assertEqual(cfg.error, "")

    def test_non_int_rotations_rejected(self):
        """QE #156 nit: 90.0 (== 90 in Python), true, "90" and 45 are all refused; last good kept."""
        path = os.path.join(self.out, "display_rotation.json")
        cfg = pp.RotationConfig(path, {}, log=self.logs.append)
        with open(path, "w") as f:
            f.write('{"bmcam004": 90}')
        self.assertEqual(cfg.get(), {"bmcam004": 90})
        for i, bad in enumerate(('{"bmcam004": 90.0}', '{"bmcam004": true}', '{"bmcam004": "90"}',
                                 '{"bmcam004": 45}', '[90]'), 1):
            with open(path, "w") as f:
                f.write(bad)
            os.utime(path, (time.time() + 10 * i, time.time() + 10 * i))
            got = cfg.get()
            self.assertEqual(got, {"bmcam004": 90}, bad)
            self.assertIs(type(got["bmcam004"]), int)
            self.assertTrue(cfg.error, bad)
        self.assertEqual(pp.parse_rotation_args(["bmcam004=90.0"], log=self.logs.append), {})

    def test_http_page_follows_file_without_restart(self):
        import threading
        import urllib.request
        self.feed_two()
        path = os.path.join(self.out, "display_rotation.json")
        cfg = pp.RotationConfig(path, {}, log=self.logs.append)
        srv = pp.http.server.ThreadingHTTPServer(("127.0.0.1", 0), pp.make_handler(self.store, ["bmcam003", "bmcam004"], cfg))
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        url = f"http://127.0.0.1:{srv.server_address[1]}/"
        try:
            self.assertNotIn("frame r", urllib.request.urlopen(url, timeout=5).read().decode())
            with open(path, "w") as f:
                f.write('{"bmcam004": 90}')
            page = urllib.request.urlopen(url, timeout=5).read().decode()
            self.assertIn('class="frame r90"', self.card(page, "bmcam004"))
            img = urllib.request.urlopen(url + "img/bmcam004_200001.jpg", timeout=5).read()
            self.assertEqual(hashlib.sha256(img).hexdigest(), self.result("bmcam004_200001")["sha256"])
        finally:
            srv.shutdown()


class TestCli(unittest.TestCase):
    def test_file_once(self):
        import subprocess
        with tempfile.TemporaryDirectory() as d:
            jpeg = make_pjpeg(seed=11)
            log = os.path.join(d, "console.log")
            with open(log, "w") as f:
                f.write("\n".join(as_log(pp.encode_preview_lines("bmcam003", "191000", jpeg, 320, 180, 40))) + "\n")
            p = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "tools", "pool_preview.py"),
                                "--file", log, "--once", "--out", os.path.join(d, "out")],
                               capture_output=True, text=True, timeout=60)
            self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
            self.assertIn("bmcam003_191000 OK", p.stdout)
            with open(os.path.join(d, "out", "bmcam003_191000.jpg"), "rb") as f:
                self.assertEqual(f.read(), jpeg)

    def test_help(self):
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(REPO_ROOT, "tools", "pool_preview.py"), "--help"],
                           capture_output=True, text=True, timeout=30)
        self.assertEqual(p.returncode, 0)
        self.assertIn("PVH", p.stdout)


if __name__ == "__main__":
    unittest.main()

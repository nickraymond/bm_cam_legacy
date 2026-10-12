#!/usr/bin/env python3
# filename: pool_preview.py
# description: Pool test 2026-10-13 — rebuild PREVIEW JPEGs from the Spotter console logs on nereus000 and serve a viewer.
"""
pool_preview.py — rebuild the pool-test PREVIEW images that the camera Pis print to their
Spotter USB consoles, and serve them on a tiny auto-refreshing web page.

Purpose
  Tomorrow's pool test (2026-10-13): before the loop starts, Nick checks the card framing
  from his laptop. Each unit (bmcam003 / bmcam004) prints a small PROGRESSIVE JPEG to its
  Spotter console as base64 lines; spotter-monitor (tools/spotter_serial_monitor.py) logs
  them under <log-root>/<SPOT>/console_YYYYMMDD.log. This tool tails those logs, rebuilds
  each image, checks it and shows the latest per unit side by side.

Wire format (agreed with the Pi-side pool session, 2026-10-12; every line is a unit console
line "[bmcamNNN] ...", cut at 240 chars by command_wire.console_line):
  PVH <unit> <img_id> n=<total_lines> bytes=<jpeg_bytes> sha256=<hex> w=<w> h=<h> q=<q>
  PV  <unit> <img_id> <seq>/<total> <crc32 hex8 of the base64 chunk> <base64 chunk>
  PVE <unit> <img_id>
  seq is 1-based; crc32 = zlib.crc32(base64 ASCII); sha256 covers the decoded JPEG bytes;
  img_id = UTC HHMMSS (+ letter suffix on a same-second collision). A resend repeats PVH
  (same img_id, same n), then only the requested PV lines, then PVE.

Log line format (real, from runs/*/console evidence): the monitor prefixes "<UTC>Z ", the
Spotter adds "<epoch>.<ms> <node-id-hex16>, " and sometimes junk ("Message: Message: ", "."),
so the tokens are matched ANYWHERE in the line, never anchored at the start.

Outputs (in --out, default /home/pi/pool_preview):
  <unit>_<img_id>.jpg    complete image (sha256 OK), or best-effort from the contiguous
                         prefix with EOI (FF D9) appended so viewers open it (PARTIAL)
  <unit>_<img_id>.json   sidecar: status, lines received/total, missing ranges, sha, sizes
  pool_preview.log       every event this tool printed (append)
  Web page (http://<nereus000>:8090/): latest image per unit, unit, received time (console log
  timestamp, shown in the viewer's local time), lines
  received/total, OK/PARTIAL, size; refreshes every 5 s; older previews listed below.

On PARTIAL it prints the missing seq ranges and the exact resend command, e.g.
    python3 pool_cmd.py bmcam003 preview --lines 3-7,12
  which sends {"id":N,"c":"pool","v":"preview","lines":"3-7,12"}. Each `lines` value is <= 186
  chars (max_lines_chars(): the 256 B console line binds; JSON 248 B, Pi RE_LINES 200); more
  gaps -> several RESEND commands, run them in turn.
  Resent lines merge into the same <unit>_<img_id>.jpg.

Example (on nereus000)
  python3 /home/pi/pool_tools/pool_preview.py                      # tail + serve on :8090
  python3 pool_preview.py --port 8091 --bind 192.168.1.45           # other port / interface
  python3 pool_preview.py --file console_20261013.log --once --no-serve   # offline rebuild

Assumptions / limits
  - stdlib only (no Pillow on nereus000 needed); the browser decodes the JPEG.
  - A preview with no PVE is finalised after --idle-s (default 20 s) of silence.
  - A new PVH with the same img_id but a different sha256 starts a fresh image (old lines dropped).
  - Starts by re-reading the last --backfill-mb (default 4 MB) of each console log, so a
    restart rebuilds recent previews; older previews need --file.
  - The best-effort prefix is only as good as the first missing line: a progressive JPEG
    shows the whole frame at lower quality, a gap early in the file shows little.
"""
import argparse
import base64
import binascii
import glob
import hashlib
import html
import http.server
import json
import os
import re
import sys
import threading
import time
import zlib
from datetime import datetime, timezone

MAX_JSON_BYTES = 248     # command_wire.MAX_JSON_BYTES (whole command JSON)
MAX_CONSOLE_LINE = 256   # hil/tools/hil_console.sh: Spotter RX limit incl. newline (pool_cmd enforces it)
PI_LINES_MAX = 200       # the Pi's rc_preview.RE_LINES = [0-9,-]{1,200} (#152 @ a23f4d6)
RE_PI_LINES = re.compile(r"[0-9,-]{1,200}\Z")  # copy of the Pi regex; tests check every split against it


def max_lines_chars():
    """Same formula as tools/pool_cmd.max_lines_chars() (tested equal): the longest `lines`
    value whose command passes JSON <= 248 B, console line <= 256 B and the Pi's 200. = 186."""
    tpl = '{"id":99999,"c":"pool","v":"preview","lines":""}'
    by_console = MAX_CONSOLE_LINE - len("bm pub bmcam/cmd ") - len(" 1 1") - 1 - len(tpl)
    return min(PI_LINES_MAX, MAX_JSON_BYTES - len(tpl), by_console)


MAX_LINES_CHARS = max_lines_chars()
EOI = b"\xff\xd9"

# Tokens, matched anywhere in the line (see the docstring: junk prefixes are real).
RE_PVH = re.compile(
    r"(?<![A-Za-z0-9])PVH\s+(\S+)\s+(\S+)\s+n=(\d+)\s+bytes=(\d+)\s+sha256=([0-9a-fA-F]{64})"
    r"\s+w=(\d+)\s+h=(\d+)\s+q=(\d+)")
RE_PV = re.compile(
    r"(?<![A-Za-z0-9])PV\s+(\S+)\s+(\S+)\s+(\d+)/(\d+)\s+([0-9a-fA-F]{8})\s+(\S+)")
RE_PVE = re.compile(r"(?<![A-Za-z0-9])PVE\s+(\S+)\s+(\S+)")
RE_TS = re.compile(r"^(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ)\s")      # spotter-monitor's UTC prefix


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------- parsing

def parse_line(line):
    """One console log line -> event tuple or None.
    ("PVH", unit, img, {n, bytes, sha256, w, h, q}) | ("PV", unit, img, seq, total, crc, b64)
    | ("PVE", unit, img)."""
    m = RE_PVH.search(line)
    if m:
        return ("PVH", m.group(1), m.group(2), {
            "n": int(m.group(3)), "bytes": int(m.group(4)), "sha256": m.group(5).lower(),
            "w": int(m.group(6)), "h": int(m.group(7)), "q": int(m.group(8))})
    m = RE_PV.search(line)
    if m:
        return ("PV", m.group(1), m.group(2), int(m.group(3)), int(m.group(4)),
                m.group(5).lower(), m.group(6))
    m = RE_PVE.search(line)
    if m:
        return ("PVE", m.group(1), m.group(2))
    return None


def crc_hex(b64_text):
    return format(zlib.crc32(b64_text.encode("ascii", "replace")) & 0xFFFFFFFF, "08x")


def ranges_str(seqs):
    """[3,4,5,7,12,13] -> '3-5,7,12-13'."""
    out, seqs = [], sorted(seqs)
    i = 0
    while i < len(seqs):
        j = i
        while j + 1 < len(seqs) and seqs[j + 1] == seqs[j] + 1:
            j += 1
        out.append(str(seqs[i]) if i == j else f"{seqs[i]}-{seqs[j]}")
        i = j + 1
    return ",".join(out)


def resend_lines_args(missing, budget=MAX_JSON_BYTES, max_chars=MAX_LINES_CHARS):
    """Split missing seqs into 'lines' strings of <= MAX_LINES_CHARS (186) each, so every
    resend command passes JSON 248 B, the 256 B console line and the Pi's RE_LINES (200).
    More gaps than fit -> several resend commands, sent one after the other."""
    template = '{"id":99999,"c":"pool","v":"preview","lines":""}'
    room = min(max_chars, budget - len(template))
    parts, cur = [], ""
    for r in ranges_str(missing).split(","):
        if not r:
            continue
        cand = r if not cur else cur + "," + r
        if len(cand) > room:
            parts.append(cur)
            cur = r
        else:
            cur = cand
    if cur:
        parts.append(cur)
    return parts


def decode_chunks(chunks):
    """Decode an ordered list of base64 chunks. Chunks cut at multiples of 4 chars decode
    one by one; otherwise the text is joined first (whole-file base64 cut anywhere)."""
    if all(len(c) % 4 == 0 for c in chunks):
        return b"".join(base64.b64decode(c) for c in chunks)
    text = "".join(chunks)
    text = text[: len(text) - len(text) % 4]
    return base64.b64decode(text)


# ----------------------------------------------------------------------------- one image

class Preview:
    """All lines seen so far for one (unit, img_id)."""

    def __init__(self, unit, img_id):
        self.unit, self.img_id = unit, img_id
        self.header = None
        self.total = None
        self.chunks = {}          # seq -> base64 text (crc OK)
        self.corrupt = set()      # seqs seen only with a bad crc
        self.dupes = 0
        self.first_seen = time.time()
        self.last_line = time.time()
        self.ended = False        # PVE seen since the last PVH/PV
        self.dirty = False        # lines changed since the last write
        self.written = None       # last result dict
        self.passes = 0           # PVH count (1 = first send, >1 = resends)
        self.first_rx = None      # monitor log timestamps (epoch): when the unit SENT, not when we parsed
        self.last_rx = None

    @property
    def key(self):
        return f"{self.unit}_{self.img_id}"

    def on_header(self, h):
        if self.header and h["sha256"] != self.header["sha256"]:
            # same img_id, different image: start over (documented limit)
            self.chunks.clear()
            self.corrupt.clear()
        self.header = h
        self.total = h["n"]
        self.passes += 1
        self.ended = False
        self.dirty = True
        self.last_line = time.time()

    def on_line(self, seq, total, crc, b64):
        self.last_line = time.time()
        self.ended = False
        if self.total is None:
            self.total = total
        if crc_hex(b64) != crc:
            if seq not in self.chunks:
                self.corrupt.add(seq)
            return False
        if seq in self.chunks:
            self.dupes += 1
        self.chunks[seq] = b64
        self.corrupt.discard(seq)
        self.dirty = True
        return True

    def missing(self):
        if not self.total:
            return []
        return [s for s in range(1, self.total + 1) if s not in self.chunks]

    def contiguous_prefix(self):
        k = 0
        while (k + 1) in self.chunks:
            k += 1
        return k

    def build(self):
        """-> (jpeg bytes, result dict). Never raises on bad data."""
        total = self.total or 0
        missing = self.missing()
        prefix = self.contiguous_prefix()
        data, note = b"", ""
        try:
            data = decode_chunks([self.chunks[s] for s in range(1, prefix + 1)])
        except (binascii.Error, ValueError) as exc:
            note = f"base64 decode failed: {exc}"
        sha = hashlib.sha256(data).hexdigest()
        exp_sha = self.header["sha256"] if self.header else None
        exp_bytes = self.header["bytes"] if self.header else None
        complete = total > 0 and not missing
        if complete and exp_sha and sha == exp_sha:
            status = "OK"
        elif complete and not exp_sha:
            status = "PARTIAL"
            note = note or "all lines received but no PVH header: sha256 not checked"
        elif complete:
            status = "BAD_SHA"
            note = note or f"all lines received but sha256 {sha[:12]} != PVH {exp_sha[:12]}"
        else:
            status = "PARTIAL"
        out = data
        eoi_added = False
        if status != "OK" and out and not out.endswith(EOI):
            out = out + EOI
            eoi_added = True
        resend = missing if status != "BAD_SHA" else list(range(1, total + 1))
        res = {
            "unit": self.unit, "img_id": self.img_id, "status": status,
            "lines_received": len(self.chunks), "lines_total": total,
            "contiguous_prefix": prefix, "missing": ranges_str(missing),
            "corrupt_seen": ranges_str(sorted(self.corrupt)), "duplicate_lines": self.dupes,
            "bytes_decoded": len(data), "bytes_expected": exp_bytes,
            "file_bytes": len(out), "eoi_appended": eoi_added,
            "sha256": sha, "sha256_expected": exp_sha,
            "w": self.header["w"] if self.header else None,
            "h": self.header["h"] if self.header else None,
            "q": self.header["q"] if self.header else None,
            "header_seen": bool(self.header), "end_seen": self.ended, "passes": self.passes,
            "first_seen_epoch": self.first_rx or self.first_seen,
            "received_epoch": self.last_rx or self.last_line,
            "first_seen_utc": datetime.fromtimestamp(self.first_seen, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "updated_utc": utc_now(), "updated_epoch": time.time(),
            "resend_lines": resend_lines_args(resend) if resend else [],
            "note": note,
        }
        return out, res


# ----------------------------------------------------------------------------- the store

class Store:
    """Previews by key + writer. Thread-safe (tailer thread writes, web thread reads)."""

    def __init__(self, out_dir, idle_s=20.0, cmd_hint="python3 pool_cmd.py", log=print):
        self.out_dir = out_dir
        self.idle_s = idle_s
        self.cmd_hint = cmd_hint
        self.log = log
        self.previews = {}
        self.lock = threading.Lock()
        os.makedirs(out_dir, exist_ok=True)

    def _get(self, unit, img_id):
        k = f"{unit}_{img_id}"
        if k not in self.previews:
            self.previews[k] = Preview(unit, img_id)
        return self.previews[k]

    def feed(self, line, source=""):
        ev = parse_line(line)
        if not ev:
            return None
        m = RE_TS.match(line)
        rx = (datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()
              if m else time.time())
        with self.lock:
            kind, unit, img = ev[0], ev[1], ev[2]
            if not re.fullmatch(r"[A-Za-z0-9_.:-]+", unit + img):
                self.log(f"[preview] WARN unsafe unit/img_id token {unit!r} {img!r}; line ignored")
                return None
            p = self._get(unit, img)
            p.first_rx = p.first_rx or rx
            p.last_rx = rx
            if kind == "PVH":
                again = p.header is not None
                p.on_header(ev[3])
                h = ev[3]
                self.log(f"[preview] {p.key} PVH {'(resend) ' if again else ''}n={h['n']} "
                         f"bytes={h['bytes']} {h['w']}x{h['h']} q={h['q']} {source}")
            elif kind == "PV":
                ok = p.on_line(ev[3], ev[4], ev[5], ev[6])
                if not ok:
                    self.log(f"[preview] {p.key} line {ev[3]}/{ev[4]} CRC MISMATCH (kept as missing)")
            elif kind == "PVE":
                p.ended = True
                self._finalise(p, "PVE")
        return ev[0]

    def tick(self):
        """Finalise previews that went quiet without a PVE (lost end line)."""
        with self.lock:
            now = time.time()
            for p in self.previews.values():
                if not p.ended and p.dirty and now - p.last_line > self.idle_s:
                    self._finalise(p, f"idle {self.idle_s:.0f}s, no PVE")

    def flush_all(self, why="end of input"):
        with self.lock:
            for p in self.previews.values():
                if p.dirty or p.written is None:
                    self._finalise(p, why)

    def _finalise(self, p, why):
        data, res = p.build()
        res["finalised_by"] = why
        base = os.path.join(self.out_dir, p.key)
        if data:
            tmp = base + ".jpg.tmp"
            with open(tmp, "wb") as f:
                f.write(data)
            os.replace(tmp, base + ".jpg")
        res["file"] = os.path.basename(base + ".jpg") if data else None
        with open(base + ".json.tmp", "w") as f:
            json.dump(res, f, indent=1)
        os.replace(base + ".json.tmp", base + ".json")
        p.written, p.dirty = res, False
        self.log(f"[preview] {p.key} {res['status']} lines {res['lines_received']}/{res['lines_total']} "
                 f"bytes {res['bytes_decoded']}/{res['bytes_expected']} -> {res['file']} ({why})")
        if res["note"]:
            self.log(f"[preview] {p.key} note: {res['note']}")
        if res["status"] != "OK":
            if res["missing"]:
                self.log(f"[preview] {p.key} MISSING lines: {res['missing']} "
                         f"(contiguous prefix 1-{res['contiguous_prefix']} shown)")
            if not res["lines_total"]:
                self.log(f"[preview] {p.key} no line total known (no PVH/PV seen): "
                         f"re-request with: {self.cmd_hint} {p.unit} preview")
            for lines in res["resend_lines"]:
                self.log(f"[preview] {p.key} RESEND: {self.cmd_hint} {p.unit} preview --lines {lines}"
                         f"   # sends {{\"id\":N,\"c\":\"pool\",\"v\":\"preview\",\"lines\":\"{lines}\"}}")

    def snapshot(self):
        with self.lock:
            return [dict(p.written) for p in self.previews.values() if p.written]


# ----------------------------------------------------------------------------- tailing

class Tailer:
    """Follow the newest console_*.log in each <log-root>/<SPOT>/ dir (UTC-daily rotation)."""

    def __init__(self, store, log_root, spots=None, backfill_mb=4.0, poll_s=0.5, log=print):
        self.store, self.log_root, self.spots = store, log_root, spots
        self.backfill = int(backfill_mb * 1024 * 1024)
        self.poll_s, self.log = poll_s, log
        self.files = {}   # spot dir -> [path, fh, partial]

    def _dirs(self):
        if self.spots:
            return [os.path.join(self.log_root, s) for s in self.spots]
        return sorted(d for d in glob.glob(os.path.join(self.log_root, "*")) if os.path.isdir(d))

    def _open(self, d, path, backfill):
        fh = open(path, "rb")
        size = os.path.getsize(path)
        skip_partial = False
        if backfill and size > self.backfill:
            fh.seek(size - self.backfill)
            skip_partial = True
        elif not backfill:
            fh.seek(0)
        self.files[d] = [path, fh, b""]
        if skip_partial:
            fh.readline()  # drop the cut first line
        self.log(f"[preview] following {path} (from byte {fh.tell()} of {size})")

    def _drain(self, d):
        path, fh, partial = self.files[d]
        data = fh.read()
        if not data:
            return
        data = partial + data
        lines = data.split(b"\n")
        self.files[d][2] = lines.pop()       # incomplete tail waits for the next poll
        src = os.path.basename(d)
        for raw in lines:
            self.store.feed(raw.decode("utf-8", "replace"), source=src)

    def poll_once(self, first=False):
        for d in self._dirs():
            logs = sorted(glob.glob(os.path.join(d, "console_*.log")))
            if not logs:
                continue
            newest = logs[-1]
            cur = self.files.get(d)
            if cur is None:
                self._open(d, newest, backfill=True)
            elif cur[0] != newest:
                self._drain(d)              # finish yesterday's file, then switch
                cur[1].close()
                self._open(d, newest, backfill=False)
            self._drain(d)
        self.store.tick()

    def run_forever(self):
        if not self._dirs():
            self.log(f"[preview] WARN no Spotter log dirs under {self.log_root} yet (spotter-monitor running?)")
        while True:
            try:
                self.poll_once()
            except OSError as exc:
                self.log(f"[preview] WARN tail error: {exc}; retrying")
            time.sleep(self.poll_s)


# ----------------------------------------------------------------------------- web page

PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta http-equiv="refresh" content="{refresh}"><title>Pool previews</title>
<style>
body{{font:14px -apple-system,Helvetica,Arial,sans-serif;margin:16px;background:#111;color:#eee}}
h1{{font-size:18px;margin:0 0 4px}} .sub{{color:#999;margin-bottom:12px}}
.row{{display:flex;gap:16px;flex-wrap:wrap}} .card{{flex:1 1 420px;background:#1c1c1c;border-radius:8px;padding:10px}}
.card img{{width:100%;height:auto;display:block;background:#000;image-rendering:auto}}
.OK{{color:#5c5}} .PARTIAL,.BAD_SHA{{color:#fb3}} .none{{color:#888}}
table{{border-collapse:collapse;margin-top:16px;width:100%}} td,th{{padding:3px 8px;border-bottom:1px solid #333;text-align:left}}
code{{background:#000;padding:1px 4px;border-radius:3px;word-break:break-all}} a{{color:#8cf}}
</style></head><body>
<h1>Pool previews</h1><div class="sub">page {now} &middot; refresh {refresh} s &middot; image shown at browser width (scaled, not 1:1)</div>
<div class="row">{cards}</div>
<h2 style="font-size:15px;margin-top:20px">All previews ({count})</h2>
<table><tr><th>unit</th><th>img_id</th><th>status</th><th>lines</th><th>bytes</th><th>received</th><th>missing</th></tr>{rows}</table>
<script>
for (const el of document.querySelectorAll(".t")) {{
  el.textContent = new Date(1000 * +el.dataset.e).toLocaleTimeString([], {{hour: "numeric", minute: "2-digit", second: "2-digit", timeZoneName: "short"}});
}}
</script></body></html>"""


def _local(ts_epoch):
    """UTC text, rewritten to the VIEWER's local time (Nick: Pacific, AM/PM) by the page script."""
    utc = time.strftime("%H:%M:%SZ", time.gmtime(ts_epoch))
    return f'<span class="t" data-e="{ts_epoch:.0f}">{utc}</span>'


def render_page(snapshot, units=None, refresh=5):
    latest = {}
    for r in snapshot:
        u = r["unit"]
        if u not in latest or r["first_seen_epoch"] > latest[u]["first_seen_epoch"]:
            latest[u] = r
    unit_list = sorted(set(units or []) | set(latest))
    cards = []
    for u in unit_list:
        r = latest.get(u)
        if not r:
            cards.append(f'<div class="card"><b>{html.escape(u)}</b> <span class="none">no preview yet</span></div>')
            continue
        img = (f'<a href="/img/{html.escape(r["file"])}"><img src="/img/{html.escape(r["file"])}?t={int(r["updated_epoch"])}"></a>'
               if r.get("file") else '<div class="none">no decodable bytes yet</div>')
        resend = "".join(f'<div>resend: <code>pool_cmd.py {html.escape(u)} preview --lines {html.escape(x)}</code></div>'
                         for x in r["resend_lines"])
        cards.append(
            f'<div class="card"><b>{html.escape(u)}</b> &middot; img {html.escape(r["img_id"])} &middot; '
            f'<b class="{r["status"]}">{r["status"]}</b><br>'
            f'received {_local(r["received_epoch"])} &middot; lines {r["lines_received"]}/{r["lines_total"]}'
            f' &middot; {r["file_bytes"]} B'
            f'{" &middot; " + str(r["w"]) + "x" + str(r["h"]) + " q" + str(r["q"]) if r["w"] else ""}'
            f'{"<br>missing: " + html.escape(r["missing"]) if r["missing"] else ""}{resend}{img}</div>')
    rows = "".join(
        f'<tr><td>{html.escape(r["unit"])}</td><td>{html.escape(r["img_id"])}</td><td class="{r["status"]}">{r["status"]}</td>'
        f'<td>{r["lines_received"]}/{r["lines_total"]}</td><td>{r["file_bytes"]}</td><td>{_local(r["received_epoch"])}</td>'
        f'<td>{html.escape(r["missing"] or "")}</td></tr>'
        for r in sorted(snapshot, key=lambda r: -r["received_epoch"]))
    return PAGE.format(refresh=refresh, now=_local(time.time()), cards="".join(cards),
                       count=len(snapshot), rows=rows)


def make_handler(store, units):
    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, fmt, *args):   # keep the console for preview events
            pass

        def _send(self, code, ctype, body):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            path = self.path.split("?", 1)[0]
            if path in ("/", "/index.html"):
                self._send(200, "text/html; charset=utf-8", render_page(store.snapshot(), units).encode())
            elif path == "/previews.json":
                self._send(200, "application/json", json.dumps(store.snapshot(), indent=1).encode())
            elif path.startswith("/img/"):
                name = os.path.basename(path[5:])
                fp = os.path.join(store.out_dir, name)
                if name.endswith(".jpg") and os.path.isfile(fp):
                    with open(fp, "rb") as f:
                        self._send(200, "image/jpeg", f.read())
                else:
                    self._send(404, "text/plain", b"not found")
            else:
                self._send(404, "text/plain", b"not found")
    return Handler


# ----------------------------------------------------------------------------- encoder (tests / bench)

def encode_preview_lines(unit, img_id, jpeg, w, h, q, chunk_chars=160, host=None):
    """The Pi-side wire format, for tests and bench replays: list of console lines
    ("[host] PVH ...", "[host] PV ...", "[host] PVE ...")."""
    b64 = base64.b64encode(jpeg).decode("ascii")
    chunks = [b64[i:i + chunk_chars] for i in range(0, len(b64), chunk_chars)]
    n = len(chunks)
    pre = f"[{host or unit}] "
    lines = [pre + f"PVH {unit} {img_id} n={n} bytes={len(jpeg)} sha256={hashlib.sha256(jpeg).hexdigest()} "
             f"w={w} h={h} q={q}"]
    for i, c in enumerate(chunks, 1):
        lines.append(pre + f"PV {unit} {img_id} {i}/{n} {crc_hex(c)} {c}")
    lines.append(pre + f"PVE {unit} {img_id}")
    return lines


# ----------------------------------------------------------------------------- main

def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Rebuild pool PREVIEW JPEGs from Spotter console logs and serve a viewer page.",
        epilog=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log-root", default="/home/pi/spotter_logs", help="spotter-monitor log root")
    ap.add_argument("--spots", default="", help="comma list of SPOT dirs to follow (default: all under --log-root)")
    ap.add_argument("--file", action="append", default=[], help="parse this log file instead of tailing (repeatable)")
    ap.add_argument("--once", action="store_true", help="with --file: parse, write, exit (no tail)")
    ap.add_argument("--out", default="/home/pi/pool_preview", help="output folder for .jpg/.json/log")
    ap.add_argument("--bind", default="0.0.0.0", help="interface address for the web page (default all)")
    ap.add_argument("--port", type=int, default=8090,
                    help="web page port (default 8090; 8095 is the rig dashboard on nereus000)")
    ap.add_argument("--no-serve", action="store_true", help="do not start the web page")
    ap.add_argument("--units", default="bmcam003,bmcam004", help="units always shown on the page")
    ap.add_argument("--idle-s", type=float, default=20.0, help="finalise a preview with no PVE after this silence")
    ap.add_argument("--backfill-mb", type=float, default=4.0, help="re-read this much of each log at start")
    ap.add_argument("--cmd-hint", default="python3 /home/pi/pool_tools/pool_cmd.py",
                    help="command prefix printed in RESEND hints")
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    logf = open(os.path.join(a.out, "pool_preview.log"), "a")
    log_lock = threading.Lock()

    def log(msg):
        line = f"{utc_now()} {msg}"
        with log_lock:
            print(line, flush=True)
            logf.write(line + "\n")
            logf.flush()

    store = Store(a.out, idle_s=a.idle_s, cmd_hint=a.cmd_hint, log=log)
    units = [u for u in a.units.split(",") if u]
    log(f"[preview] start host={os.uname().nodename} out={a.out} "
        f"{'files=' + ','.join(a.file) if a.file else 'log-root=' + a.log_root}")

    if a.file:
        for path in a.file:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    store.feed(line.rstrip("\n"), source=os.path.basename(path))
        store.flush_all("end of --file input")
        bad = [r for r in store.snapshot() if r["status"] != "OK"]
        log(f"[preview] parsed {len(a.file)} file(s): {len(store.snapshot())} preview(s), {len(bad)} not OK")
        if a.once or a.no_serve:
            return 0 if store.snapshot() and not bad else 1
    else:
        spots = [s for s in a.spots.split(",") if s] or None
        tailer = Tailer(store, a.log_root, spots=spots, backfill_mb=a.backfill_mb, log=log)
        threading.Thread(target=tailer.run_forever, daemon=True).start()

    if a.no_serve:
        while True:
            time.sleep(3600)
    srv = http.server.ThreadingHTTPServer((a.bind, a.port), make_handler(store, units))
    log(f"[preview] serving http://{a.bind}:{a.port}/  (from the laptop: http://<nereus000 IP>:{a.port}/)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("[preview] stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())

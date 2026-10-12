#!/usr/bin/env python3
# filename: rc_preview.py
# description: Pool 2026-10-13 — preview still over the Spotter USB console (progressive JPEG, numbered base64 lines).
"""
A small preview of what the camera sees, printed on the Spotter USB console
(spotter/printf: zero cellular quota) as numbered base64 lines that a host on
the console (nereus000) reassembles. Built for the 2026-10-13 pool test;
Nick wants it as a product command later, so this module knows nothing about
the pool: it takes a native JPEG + the production crop and returns lines.

Production wiring (the supervisor / v9 daemon) is a POST-POOL PR: units stay
frozen in production mode Tue-Fri. Today only pool_sequence.py calls this.

Image: native JPEG -> the production crop (rc_jpeg_encoder.prepare_source, the
SAME crop + Lanczos the stills use, e.g. 1600x900) -> downscale to the preview
size (default 640x360) -> PROGRESSIVE JPEG (Nick: if lines are lost the coarse
scans still show something), quality stepped down until <= max_bytes.

WIRE FORMAT (frozen with the nereus000 reassembler session; change it only
through the EM). Every line goes through command_wire.console_line(host, text),
so on the console it reads `[<host>] <text>`, max MAX_CONSOLE_CHARS (240):

  PVH <unit> <img_id> n=<total_lines> bytes=<jpeg_bytes> sha256=<hex> w=<w> h=<h> q=<q>
  PV  <unit> <img_id> <seq>/<total> <crc32 of the base64 chunk, 8 hex> <base64 chunk>
  PVE <unit> <img_id>

  seq 1-based; crc32 = zlib.crc32 over the base64 ASCII, lowercase hex8;
  sha256 over the decoded JPEG bytes; <unit> = hostname; <img_id> =
  HHMMSS (UTC) plus a letter suffix on a same-second collision.
  A resend repeats PVH, then only the requested PV lines, then PVE.

Pacing: one line per `line_s` (default 0.031 s = the Sprint09 measured
Spotter-side arrival spacing, sprints/Sprint09_mote_throughput/DEV_LOG.md:179).
Sprint13 measured zero drops at 0.05 s over 123 console lines
(sprints/Sprint13_console_help_readback/SPEC.md:88-90); 0.031 s over ~250
lines is NOT yet measured -> the crc + resend path exists for that.

Known limitations: base64 is 4/3 overhead; one 30 KB preview ~ 250 lines ~
8 s of console at 0.031 s/line. No compression of the line headers.
"""

import base64
import hashlib
import io
import re
import time
import zlib

import command_wire

DEFAULT_PREVIEW_WH = (640, 360)
DEFAULT_QUALITY = 60
QUALITY_FLOOR = 20
QUALITY_STEP = 10
DEFAULT_MAX_BYTES = 30_000
DEFAULT_CHUNK_CHARS = 160
DEFAULT_LINE_S = 0.031          # Sprint09 DEV_LOG.md:179, see module doc
# Up to 200 chars: #151's viewer splits resend lists at ~199 chars; the 248 B JSON
# cap on the command (command_wire.MAX_JSON_BYTES) is the real bound.
RE_LINES = re.compile(r"[0-9,-]{1,200}\Z")


def render_preview_jpeg(source_img, out_wh=DEFAULT_PREVIEW_WH, quality=DEFAULT_QUALITY,
                        max_bytes=DEFAULT_MAX_BYTES, floor=QUALITY_FLOOR, step=QUALITY_STEP):
    """PIL image (the production crop, e.g. 1600x900) -> (jpeg_bytes, q, attempts).
    Progressive + optimize (the rc_jpeg_encoder Pillow call), stepping q down by
    `step` until <= max_bytes or the floor (then the floor's bytes, over target)."""
    from PIL import Image
    small = source_img.convert("RGB").resize(tuple(out_wh), Image.Resampling.LANCZOS)
    attempts = []
    q = int(quality)
    while True:
        buf = io.BytesIO()
        small.save(buf, format="JPEG", quality=q, progressive=True, optimize=True)
        data = buf.getvalue()
        attempts.append({"q": q, "bytes": len(data)})
        if len(data) <= int(max_bytes) or q - step < floor:
            return data, q, attempts
        q -= step


def preview_from_native(native_path, crop_xywh, output_width, **kw):
    """Native JPEG -> production crop (prepare_source) -> preview JPEG.
    -> (jpeg_bytes, q, attempts, (w, h))."""
    from rc_jpeg_encoder import prepare_source
    source = prepare_source(native_path, crop_xywh, output_width)
    data, q, attempts = render_preview_jpeg(source, **kw)
    out_wh = tuple(kw.get("out_wh", DEFAULT_PREVIEW_WH))
    return data, q, attempts, out_wh


def crc8hex(text):
    return format(zlib.crc32(text.encode("ascii")) & 0xFFFFFFFF, "08x")


def max_chunk_chars(host, unit, img_id, total):
    """Largest base64 chunk whose PV line still fits MAX_CONSOLE_CHARS after
    console_line adds `[host] `."""
    head = f"[{host}] PV {unit} {img_id} {total}/{total} 00000000 "
    return command_wire.MAX_CONSOLE_CHARS - len(head)


def build_lines(host, unit, img_id, jpeg, w, h, q, chunk_chars=DEFAULT_CHUNK_CHARS):
    """-> list of console TEXTS (console_line adds the [host] prefix):
    [PVH, PV 1..n, PVE]. Raises ValueError when a line would not fit."""
    b64 = base64.b64encode(jpeg).decode("ascii")
    chunk_chars = int(chunk_chars)
    if chunk_chars < 4:
        raise ValueError(f"chunk_chars must be >= 4, got {chunk_chars}")
    chunks = [b64[i:i + chunk_chars] for i in range(0, len(b64), chunk_chars)] or [""]
    total = len(chunks)
    limit = max_chunk_chars(host, unit, img_id, total)
    if chunk_chars > limit:
        raise ValueError(f"chunk_chars {chunk_chars} > {limit}: the PV line would exceed "
                         f"{command_wire.MAX_CONSOLE_CHARS} console chars")
    header = (f"PVH {unit} {img_id} n={total} bytes={len(jpeg)} "
              f"sha256={hashlib.sha256(jpeg).hexdigest()} w={w} h={h} q={q}")
    if len(f"[{host}] {header}") > command_wire.MAX_CONSOLE_CHARS:
        raise ValueError("PVH line exceeds the console limit (host/unit name too long)")
    lines = [header]
    for seq, chunk in enumerate(chunks, 1):
        lines.append(f"PV {unit} {img_id} {seq}/{total} {crc8hex(chunk)} {chunk}")
    lines.append(f"PVE {unit} {img_id}")
    return lines


def parse_line_spec(spec, total):
    """"3-7,12" -> sorted unique 1-based seqs. Raises ValueError (bad charset,
    reversed range, out of 1..total)."""
    if not isinstance(spec, str) or not RE_LINES.match(spec):
        raise ValueError("lines is digits, ',' and '-' (e.g. 3-7,12)")
    out = set()
    for part in spec.split(","):
        if not part:
            raise ValueError("empty item in lines")
        if "-" in part:
            a, _, b = part.partition("-")
            if not a.isdigit() or not b.isdigit():
                raise ValueError(f"bad range {part!r}")
            a, b = int(a), int(b)
            if a > b:
                raise ValueError(f"reversed range {part!r}")
        else:
            if not part.isdigit():
                raise ValueError(f"bad item {part!r}")
            a = b = int(part)
        if a < 1 or b > total:
            raise ValueError(f"{part} outside 1..{total}")
        out.update(range(a, b + 1))
    return sorted(out)


def resend_lines(lines, seqs):
    """The resend of `seqs` from a full build_lines() list: PVH, those PV, PVE."""
    return [lines[0]] + [lines[s] for s in seqs] + [lines[-1]]


def reassemble(console_lines):
    """Reference reassembler (tests; the nereus000 viewer is the real one).
    Accepts lines with or without the `[host] ` prefix, in any order, with
    duplicates and corrupt lines. -> dict(jpeg|None, missing[seq], bad[seq], header)."""
    header, chunks, bad = None, {}, set()
    for raw in console_lines:
        line = raw.split("] ", 1)[1] if raw.startswith("[") and "] " in raw else raw
        parts = line.split(" ")
        if parts[0] == "PVH":
            header = {"unit": parts[1], "img_id": parts[2]}
            for kv in parts[3:]:
                k, _, v = kv.partition("=")
                header[k] = v
        elif parts[0] == "PV" and len(parts) == 6:
            try:
                seq, total = (int(x) for x in parts[3].split("/"))
            except ValueError:
                continue
            if crc8hex(parts[5]) != parts[4]:
                bad.add(seq)
                continue
            chunks[seq] = parts[5]
            bad.discard(seq)
    if header is None:
        return {"jpeg": None, "missing": [], "bad": sorted(bad), "header": None}
    n = int(header["n"])
    missing = [s for s in range(1, n + 1) if s not in chunks]
    if missing:
        return {"jpeg": None, "missing": missing, "bad": sorted(bad), "header": header}
    jpeg = base64.b64decode("".join(chunks[s] for s in range(1, n + 1)))
    ok = hashlib.sha256(jpeg).hexdigest() == header.get("sha256")
    return {"jpeg": jpeg if ok else None, "missing": [], "bad": sorted(bad),
            "header": header, "sha_ok": ok}


def new_img_id(previous_ids, now_utc):
    """HHMMSS (UTC), with a/b/c... appended on a same-second collision."""
    base = now_utc.strftime("%H%M%S")
    if base not in previous_ids:
        return base
    for c in "bcdefghijklmnopqrstuvwxyz":
        if base + c not in previous_ids:
            return base + c
    raise RuntimeError("too many previews in one second")


def expected_seconds(n_lines, line_s=DEFAULT_LINE_S):
    """Console time for n lines at the pacing (the capture + encode come on top)."""
    return round(n_lines * float(line_s), 1)


def send_lines(print_fn, host, lines, line_s=DEFAULT_LINE_S, sleep_fn=time.sleep):
    """Print each text as console_line(host, text), paced. -> lines sent."""
    for i, text in enumerate(lines):
        print_fn(command_wire.console_line(host, text))
        if i + 1 < len(lines) and line_s > 0:
            sleep_fn(line_s)
    return len(lines)

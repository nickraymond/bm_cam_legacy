#!/usr/bin/env python3
# filename: sprint17_ab_rollup.py
# description: Sprint17 D-S17-5 — pull A/B clips from units, build the comparison CSV + cut sheet.
"""
Sprint17 A/B rollup (D-S17-5): the artifact Nick actually judges.

Pulls per-clip sidecars and poster frames from one or more units, joins them
with the geometry facts the sidecar now carries (preset, sensor mode,
available px, scale — Sprint17 sidecar v2), derives the field-viability
numbers, and renders a side-by-side cut sheet.

WHAT MAKES A COMPARISON VALID (learned the hard way):
  - ONE variable at a time. The tool labels every differing field so a sheet
    that accidentally varies two things is obvious at a glance.
  - A LIT scene. The 2026-08-18 overnight run showed a dark bench undershoots
    every bitrate cap, so dark clips compare nothing but noise. Clips whose
    achieved bitrate is far below their cap are flagged UNDERSHOOT.
  - Simultaneous beats sequential. Two units recording the same scene at the
    same moment removes lighting drift between takes; the 20 s bench probe
    showed a 60 % file-size swing between consecutive clips from scene change
    alone.

INPUTS
  --unit HOST[:LABEL]   repeatable; pulls from pi@HOST
  --since UTC           only clips at/after this ISO stamp (e.g. 2026-08-18T20:30Z)
  --limit N             newest N clips per unit (default 6)
  --out DIR             run folder (default runs/sprint17_ab_<UTC>)
  --no-pull             use what is already in --out (re-render the sheet only)

OUTPUTS (self-contained, manifesto rule 10)
  comparison.csv    one row per clip: every setting, size, encode_s,
                    boundary_s, temp, GB/day, ring window, dead-time %
  cut_sheet.png     poster frames side by side, each labelled with its
                    settings and PASS/WARN status
  <unit>/           the pulled sidecars + thumbs
  run_manifest.json

EXAMPLE
  python3 tools/sprint17_ab_rollup.py --unit bmcam003:high --unit bmcam004:lean \
      --since 2026-08-18T20:35Z

ASSUMPTIONS
  - Units run the Sprint17 runtime (sidecar v2 with preset/sensor_mode/
    avail_px/scale). v1 sidecars are read but their geometry columns are blank.
  - ssh key auth to pi@<host>.

KNOWN LIMITATIONS
  - Thumbnails on the sheet are SCALED to a common tile height. The sheet says
    so, per manifesto rule 13 — it is not a 1:1 pixel comparison, it is a
    framing/exposure/settings overview. Judge sharpness from the mp4s.
"""

import argparse
import csv
import json
import os
import subprocess
import sys
from datetime import datetime, timezone

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

try:
    from PIL import Image, ImageDraw, ImageFont
except ImportError:
    Image = None

REMOTE_VIDEO_DIR = "/home/pi/BM_Devel_Pi/videos"

# Dead time vs clip bytes, interpolated from the 2026-08-18 overnight run
# (75 MB -> 5 s, 150 MB -> 23 s, 300 MB -> 40 s). Three points do not fit a
# clean line, so this is an ESTIMATE for planning; boundary_s in the sidecar
# is the measurement and always wins where present.
DEAD_TIME_POINTS = [(75.0, 5.0), (150.0, 23.0), (300.0, 40.0)]


def estimate_dead_s(mb):
    if mb <= DEAD_TIME_POINTS[0][0]:
        return DEAD_TIME_POINTS[0][1] * mb / DEAD_TIME_POINTS[0][0]
    for (x0, y0), (x1, y1) in zip(DEAD_TIME_POINTS, DEAD_TIME_POINTS[1:]):
        if mb <= x1:
            return y0 + (y1 - y0) * (mb - x0) / (x1 - x0)
    (x0, y0), (x1, y1) = DEAD_TIME_POINTS[-2:]
    return y1 + (y1 - y0) * (mb - x1) / (x1 - x0)


def pull(host, dest, limit, since):
    """Mirror the sidecars + poster frames for one unit.

    ONE rsync of the directory with include/exclude patterns rather than a
    per-file argument list: it survives Apple's rsync 2.6.9 (no
    --ignore-missing-args), missing posters, and long file lists alike.
    Clips themselves are GB-scale and deliberately stay on the unit.
    """
    os.makedirs(dest, exist_ok=True)
    proc = subprocess.run(
        ["rsync", "-a", "-e", "ssh -o ConnectTimeout=10",
         "--include=*.json", "--include=*_thumb.jpg", "--exclude=*",
         f"pi@{host}:{REMOTE_VIDEO_DIR}/", dest + "/"],
        capture_output=True, text=True)
    if proc.returncode != 0:
        print(f"[AB][WARN] {host}: rsync rc={proc.returncode} "
              f"{proc.stderr.strip().splitlines()[:1]}")
    # manifest.json is DERIVED state (D-S15-9), not a clip sidecar.
    names = sorted(n for n in os.listdir(dest)
                   if n.endswith(".json") and n != "manifest.json")
    if since:
        # Sidecar names carry the UTC stamp with '-' where the clock has ':'.
        # Compare on the shared YYYY-MM-DDTHH-MM prefix so a caller can pass
        # either form without an off-by-one on the trailing 'Z'.
        key = since.replace(":", "-")[:16]
        names = [n for n in names if n[:16] >= key]
    names = names[-limit:]
    # Keep the run folder to exactly what the rollup used, so the CSV and the
    # folder can never disagree about which clips were compared.
    keep = set(names) | {n.replace(".json", "_thumb.jpg") for n in names}
    for n in os.listdir(dest):
        if n != "manifest.json" and n not in keep:
            os.remove(os.path.join(dest, n))
    print(f"[AB] {host}: {len(names)} sidecars"
          + (f" (since {since})" if since else ""))
    return names


def load_rows(unit_dirs):
    import video_geometry as vg
    rows = []
    for host, label, d in unit_dirs:
        for name in sorted(os.listdir(d)):
            # manifest.json is the UI's DERIVED index (D-S15-9), not a clip.
            if not name.endswith(".json") or name == "manifest.json":
                continue
            try:
                with open(os.path.join(d, name)) as f:
                    rec = json.load(f)
            except (OSError, ValueError):
                continue
            mb = (rec.get("sz") or 0) / 1e6
            dur = rec.get("dur") or 0
            achieved = (rec.get("sz", 0) * 8 / dur / 1e6) if dur else 0
            cap = rec.get("br") or 0
            sm = vg.storage_math(achieved) if achieved else {}
            boundary = rec.get("boundary_s")
            dead_s = boundary if boundary else estimate_dead_s(mb)
            thumb = name.replace(".json", "_thumb.jpg")
            rows.append({
                "unit": host, "label": label,
                "file": rec.get("fn", name),
                "utc": rec.get("utc", ""),
                "preset": rec.get("preset", ""),
                "sensor_mode": rec.get("sensor_mode", ""),
                "crop_native_xywh": ",".join(
                    str(v) for v in (rec.get("crop_native_xywh") or [])),
                "res": rec.get("res", ""),
                "avail_px": rec.get("avail_px", ""),
                "scale": rec.get("scale", ""),
                "fps": rec.get("fps", ""),
                "bitrate_cap_mbps": cap,
                "bitrate_achieved_mbps": round(achieved, 2),
                "hit_cap_pct": round(100 * achieved / cap, 0) if cap else "",
                "size_mb": round(mb, 1),
                "dur_s": dur,
                "encode_s": rec.get("encode_s", ""),
                "boundary_s": boundary if boundary is not None else "",
                "dead_time_pct": round(100 * dead_s / dur, 1) if dur else "",
                "temp_c": rec.get("tmp", ""),
                "gb_per_day": sm.get("gb_per_day", ""),
                "ring_days": sm.get("ring_days", ""),
                "denoise": (rec.get("encoder") or {}).get("denoise", ""),
                "sharpness": (rec.get("encoder") or {}).get("sharpness", ""),
                "thumb": os.path.join(d, thumb) if os.path.exists(
                    os.path.join(d, thumb)) else "",
                # A clip far under its cap tells you about the SCENE, not the
                # setting — the single biggest way to mis-read an A/B.
                "status": ("UNDERSHOOT" if cap and achieved < 0.6 * cap
                           else "OK"),
            })
    return rows


TILE_W, TILE_H, PAD, HEADER, CAPTION = 640, 360, 18, 92, 132


def cut_sheet(rows, path, title):
    """Side-by-side poster frames with their settings. Thumbnails are SCALED
    to a common tile size — stated on the sheet (manifesto rule 13)."""
    if Image is None:
        print("[AB][WARN] PIL not available; skipping cut sheet")
        return None
    tiles = [r for r in rows if r["thumb"]]
    if not tiles:
        print("[AB][WARN] no poster frames pulled; skipping cut sheet")
        return None
    cols = min(3, len(tiles))
    rows_n = (len(tiles) + cols - 1) // cols
    W = cols * TILE_W + (cols + 1) * PAD
    H = HEADER + rows_n * (TILE_H + CAPTION + PAD) + PAD
    sheet = Image.new("RGB", (W, H), (18, 18, 20))
    draw = ImageDraw.Draw(sheet)
    try:
        f_title = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 26)
        f_sub = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 15)
        f_cap = ImageFont.truetype("/System/Library/Fonts/Menlo.ttc", 13)
    except OSError:
        f_title = f_sub = f_cap = ImageFont.load_default()

    draw.text((PAD, 14), title, fill=(245, 245, 245), font=f_title)
    draw.text((PAD, 50),
              "Poster frames SCALED to a common tile — framing/exposure "
              "overview, NOT a 1:1 pixel comparison. Judge sharpness from the mp4s.",
              fill=(150, 150, 158), font=f_sub)

    for i, r in enumerate(tiles):
        cx, cy = i % cols, i // cols
        x = PAD + cx * (TILE_W + PAD)
        y = HEADER + cy * (TILE_H + CAPTION + PAD)
        try:
            with Image.open(r["thumb"]) as im:
                im = im.convert("RGB")
                im.thumbnail((TILE_W, TILE_H))
                sheet.paste(im, (x + (TILE_W - im.width) // 2,
                                 y + (TILE_H - im.height) // 2))
        except OSError:
            draw.rectangle([x, y, x + TILE_W, y + TILE_H], outline=(90, 90, 90))
        colour = (255, 176, 0) if r["status"] != "OK" else (120, 220, 150)
        lines = [
            f"{r['label'] or r['unit']}  {r['preset'] or '(pre-Sprint17)'}   [{r['status']}]",
            f"{r['res']} @{r['fps']}fps   mode {r['sensor_mode']}   "
            f"avail {r['avail_px']}   scale {r['scale']}x",
            f"cap {r['bitrate_cap_mbps']} Mbps -> achieved "
            f"{r['bitrate_achieved_mbps']} Mbps ({r['hit_cap_pct']}% of cap)",
            f"{r['size_mb']} MB   encode {r['encode_s']}s   "
            f"boundary {r['boundary_s']}s ({r['dead_time_pct']}% dead)   "
            f"{r['temp_c']}C",
            f"{r['gb_per_day']} GB/day   ring ~{r['ring_days']} d   {r['utc']}",
        ]
        for j, text in enumerate(lines):
            draw.text((x, y + TILE_H + 8 + j * 17), text,
                      fill=colour if j == 0 else (208, 208, 214), font=f_cap)

    sheet.save(path)
    print(f"[AB] cut sheet: {path} ({sheet.width}x{sheet.height})")
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--unit", action="append", default=[],
                    help="HOST[:LABEL], repeatable")
    ap.add_argument("--since", default="")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--out", default="")
    ap.add_argument("--no-pull", action="store_true")
    ap.add_argument("--title", default="Sprint17 video quality A/B")
    args = ap.parse_args()

    if not args.unit:
        print("[AB][ERROR] give at least one --unit HOST[:LABEL]")
        return 2

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = args.out or os.path.join(REPO, "runs", f"sprint17_ab_{stamp}")
    os.makedirs(out, exist_ok=True)

    unit_dirs = []
    for spec in args.unit:
        host, _, label = spec.partition(":")
        d = os.path.join(out, host)
        if not args.no_pull:
            pull(host, d, args.limit, args.since)
        if os.path.isdir(d):
            unit_dirs.append((host, label or host, d))

    rows = load_rows(unit_dirs)
    if not rows:
        print("[AB][ERROR] no clips found — check --since / --limit")
        return 1
    rows.sort(key=lambda r: (r["utc"], r["unit"]))

    csv_path = os.path.join(out, "comparison.csv")
    with open(csv_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"[AB] {len(rows)} clips -> {csv_path}")

    # One tile per unit's NEWEST clip keeps the sheet a comparison, not a dump.
    newest = {}
    for r in rows:
        newest[r["unit"]] = r
    cut_sheet(sorted(newest.values(), key=lambda r: r["unit"]),
              os.path.join(out, "cut_sheet.png"), args.title)

    with open(os.path.join(out, "run_manifest.json"), "w") as f:
        json.dump({"tool": "sprint17_ab_rollup.py", "sprint": "Sprint17",
                   "utc": datetime.now(timezone.utc).isoformat(),
                   "units": [{"host": h, "label": l} for h, l, _ in unit_dirs],
                   "since": args.since, "limit": args.limit,
                   "clips": len(rows)}, f, indent=2)

    under = [r for r in rows if r["status"] != "OK"]
    if under:
        print(f"[AB][WARN] {len(under)} clip(s) UNDERSHOT their bitrate cap — "
              "the scene was too dark/simple to fill it. Those clips compare "
              "the SCENE, not the setting. Re-shoot lit.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

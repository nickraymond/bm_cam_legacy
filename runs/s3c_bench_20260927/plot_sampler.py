#!/usr/bin/env python3
"""S3c bench: plot SD used % from pulled/sampler.csv against the configured caps.

Purpose: the gate's review artifact ("SD usage bounded"): one PNG, used % over time
(UTC), the cap of each half as a dashed line, phase labels, plus a PASS/FAIL line per
half = every sample after the cap was first reached stays <= cap + one action.
Inputs: pulled/sampler.csv (gate.sh pull) and phases given on the command line:
  plot_sampler.py 'video,2026-09-27T21:08:00Z,2026-09-27T21:38:00Z,41.30,0.012' \
                  'still,2026-09-27T21:40:00Z,2026-09-27T22:10:00Z,41.80,0.020'
  (name, start, end, cap %, one action as % of the SD)
Output: sd_usage.png + a summary on stdout. Needs Pillow only (the repo's .venv-dev).
Known limitations: a plain line chart; the y axis is zoomed to the data (labelled).
"""
import csv
import os
import sys
from datetime import datetime, timezone

from PIL import Image, ImageDraw

HERE = os.path.dirname(os.path.abspath(__file__))
W, H, L, R, T, BOT = 1400, 700, 90, 30, 60, 70


def ts(s):
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).timestamp()


def main():
    rows = list(csv.DictReader(open(os.path.join(HERE, "pulled", "sampler.csv"))))
    pts = [(ts(r["utc"]), float(r["used_pct"])) for r in rows]
    phases = []
    for arg in sys.argv[1:]:
        name, start, end, cap, one = arg.split(",")
        phases.append((name, ts(start), ts(end), float(cap), float(one)))
    t0, t1 = pts[0][0], pts[-1][0]
    ys = [p[1] for p in pts] + [ph[3] for ph in phases]
    y0, y1 = min(ys) - 0.05, max(ys) + 0.05

    def xy(t, y):
        return (L + (t - t0) / max(t1 - t0, 1) * (W - L - R),
                T + (y1 - y) / (y1 - y0) * (H - T - BOT))

    img = Image.new("RGB", (W, H), "white")
    d = ImageDraw.Draw(img)
    d.text((L, 15), "S3c gate bmcam003: SD used % (df /) every 15 s vs the video.storage cap "
           "(y axis zoomed)", fill="black")
    for i in range(6):
        y = y0 + (y1 - y0) * i / 5
        d.line([xy(t0, y), xy(t1, y)], fill=(230, 230, 230))
        d.text((5, xy(t0, y)[1] - 6), f"{y:.2f} %", fill="black")
    for i in range(7):
        t = t0 + (t1 - t0) * i / 6
        x = xy(t, y0)[0]
        d.text((x - 25, H - BOT + 10), datetime.fromtimestamp(t, timezone.utc).strftime("%H:%M:%SZ"),
               fill="black")
    lines = []
    for name, s, e, cap, one in phases:
        d.line([xy(s, cap), xy(e, cap)], fill=(200, 0, 0), width=2)
        d.line([xy(s, cap + one), xy(e, cap + one)], fill=(255, 150, 150), width=1)
        d.text((xy(s, cap)[0] + 5, xy(s, cap)[1] - 16), f"{name} cap {cap:.2f} %", fill=(200, 0, 0))
        inside = [(t, y) for t, y in pts if s <= t <= e]
        reached = [i for i, (_t, y) in enumerate(inside) if y >= cap]
        after = inside[reached[0]:] if reached else []
        worst = max((y for _t, y in after), default=None)
        ok = bool(after) and worst <= cap + one
        lines.append(f"{name}: {len(inside)} samples, cap reached={'yes' if reached else 'NO'}, "
                     f"max after cap={worst if worst is None else round(worst, 3)} "
                     f"(<= cap+one action {cap + one:.3f}) -> {'PASS' if ok else 'FAIL'}")
    d.line([xy(t, y) for t, y in pts], fill=(0, 70, 160), width=2)
    d.text((L, H - 25), " | ".join(lines), fill="black")
    out = os.path.join(HERE, "sd_usage.png")
    img.save(out)
    print("\n".join(lines))
    print(f"wrote {out} ({os.path.getsize(out)} B)")


if __name__ == "__main__":
    main()

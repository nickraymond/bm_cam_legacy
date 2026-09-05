#!/usr/bin/env python3
# filename: bm_reef_frame_intake.py
# description: Sprint21 day-0 intake — archive received reef frames with provenance,
#              join Sofar delivery records, verify registration, and build a quick-look composite.
"""
Sprint21 day-0 frame intake for fixed-mount reef cameras.

WHY THIS EXISTS
Received frames arrive as loose downloads in ~/Downloads with no provenance: the
same capture can appear two or three times under "(1)"/"(2)" names, and nothing on
disk records which of them was a complete delivery. Temporal fusion and any later
change detection need to know, per frame, exactly what arrived. This tool makes the
day's frames into a self-contained, re-runnable archive before any analysis touches
them.

WHAT IT DOES
  1. Hashes every input frame, groups by capture timestamp parsed from the filename,
     and picks ONE canonical file per capture (largest byte count wins -- a longer
     file is a longer delivery prefix, never a shorter one). Duplicates are recorded,
     not deleted.
  2. Joins the Sofar delivery reports written by tools/count_complete_images.py
     (planned / received / first gap / usable prefix / complete).
  3. Verifies registration: sub-pixel phase correlation against the reference frame.
     A fixed mount should hold to ~1 px. Larger drift means a new epoch, and the tool
     says so rather than silently letting a later fusion ghost the reef.
  4. Measures per-frame sharpness (Laplacian variance) as a delivery-state cross-check.
  5. Writes a quick-look MEDIAN composite per day, per hour-of-day, and over all frames,
     plus a contact sheet. The median is a deliberately dumb CONTROL, not the proposed
     fusion method -- it is the baseline every later method has to beat.

INPUTS
  --frames GLOB...      received JPEGs (repeatable; shell globs fine)
  --delivery-dir DIR    directory of SPOT-*_YYYY-MM-DD.json from count_complete_images.py
  --output-dir DIR      run folder (created; overwritten on re-run)
  --reference NAME      filename substring picking the registration reference
                        (default: the sharpest frame)
  --corrected-dir DIR   optional dir of <stem>/after_grvi.jpg colour-corrected frames;
                        when given, the quick-look composites are built from those too

OUTPUTS (under --output-dir)
  frames/                     canonical copy of each capture, named by UTC timestamp
  frames_manifest.json        per-frame provenance: sha256, bytes, dims, duplicates,
                              delivery record, shift vs reference, sharpness
  frames_manifest.csv         the same table, flat, for eyeballing
  quicklook/median_*.jpg      median composites (all / per day / per hour)
  cut_sheets/contact_sheet.jpg
  run_manifest.json           command, git commit, counts, verdicts

EXAMPLE
  python3 tools/bm_reef_frame_intake.py \
      --frames ~/Downloads/Day_03/*.jpg \
      --delivery-dir runs/sprint21_day0_20260905/delivery \
      --output-dir runs/sprint21_day0_20260905

ASSUMPTIONS / KNOWN LIMITATIONS
  - Filenames carry the capture time as ..._YYYY-MM-DDTHH-MM-SSZ.jpg (the Sofar
    download convention). Frames that do not match are skipped with a warning.
  - Inputs are the BACKEND DISPLAY DERIVATIVES (re-encoded baseline JPEG), not the
    Pi's progressive originals, so they carry no completeness information themselves.
    That is why the Sofar join exists.
  - Registration is translation-only. It reports rotation-sensitive residual but does
    not correct rotation; a mount that rotates needs the day-1 fusion tool.
  - The median composite is a control. It is expected to look SOFTER than the sharpest
    single frame; that is the finding, not a bug.
"""
from __future__ import annotations

import argparse
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
from collections import defaultdict
from datetime import datetime, timezone

import cv2
import numpy as np
from PIL import Image

TS_RE = re.compile(r"(\d{4}-\d{2}-\d{2})T(\d{2})-(\d{2})-(\d{2})Z")


def sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def parse_stamp(path: str):
    """-> (iso_utc, date, hour) or None when the filename carries no capture time."""
    m = TS_RE.search(os.path.basename(path))
    if not m:
        return None
    date, hh, mm, ss = m.groups()
    return f"{date}T{hh}:{mm}:{ss}Z", date, hh


def load_delivery(delivery_dir: str) -> dict:
    """filename-stem -> delivery record, from count_complete_images.py reports."""
    out = {}
    if not delivery_dir or not os.path.isdir(delivery_dir):
        return out
    for p in sorted(glob.glob(os.path.join(delivery_dir, "*.json"))):
        try:
            rep = json.load(open(p))
        except (json.JSONDecodeError, OSError):
            continue
        for dev in rep.get("devices", {}).values():
            for img in dev.get("images", []):
                m = TS_RE.search(img.get("filename", "").replace(":", "-"))
                if not m:
                    continue
                date, hh, mm, ss = m.groups()
                out[f"{date}T{hh}:{mm}:{ss}Z"] = {
                    "planned_chunks": img.get("planned"),
                    "received_chunks": img.get("received"),
                    "first_gap_index": img.get("first_gap_index"),
                    "usable_prefix_pct": img.get("usable_prefix_pct"),
                    "end_seen": img.get("end_seen"),
                    "complete": img.get("complete"),
                }
    return out


def sharpness(bgr: np.ndarray) -> float:
    return float(cv2.Laplacian(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY), cv2.CV_64F).var())


def median_stack(paths, size):
    """Median of registered frames, in uint8 BGR. Deliberately the dumb control."""
    if not paths:
        return None
    arrs = []
    for p in paths:
        a = cv2.imread(p, cv2.IMREAD_COLOR)
        if a is None:
            continue
        if (a.shape[1], a.shape[0]) != size:
            a = cv2.resize(a, size, interpolation=cv2.INTER_AREA)
        arrs.append(a.astype(np.float32))
    if not arrs:
        return None
    return np.median(np.stack(arrs), axis=0).astype(np.uint8)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--frames", nargs="+", required=True)
    ap.add_argument("--delivery-dir", default="")
    ap.add_argument("--output-dir", required=True)
    ap.add_argument("--reference", default="")
    ap.add_argument("--corrected-dir", default="")
    ap.add_argument("--drift-warn-px", type=float, default=1.0)
    args = ap.parse_args(argv)

    out = args.output_dir
    for sub in ("frames", "quicklook", "cut_sheets"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)

    # ---- 1. group inputs by capture timestamp, pick the canonical file -------
    inputs = [p for pat in args.frames for p in sorted(glob.glob(os.path.expanduser(pat)))]
    inputs = [p for p in inputs if os.path.isfile(p)]
    if not inputs:
        print("[ERROR] no input frames matched", file=sys.stderr)
        return 1
    groups = defaultdict(list)
    skipped = []
    for p in inputs:
        st = parse_stamp(p)
        if st is None:
            skipped.append(p)
            print(f"[warn] no capture time in filename, skipped: {os.path.basename(p)}")
            continue
        groups[st[0]].append(p)

    print(f"[intake] {len(inputs)} files -> {len(groups)} distinct captures"
          f"{f', {len(skipped)} skipped' if skipped else ''}")

    delivery = load_delivery(args.delivery_dir)
    print(f"[intake] delivery records loaded: {len(delivery)}")

    records = []
    for stamp in sorted(groups):
        cands = sorted(groups[stamp], key=lambda p: (-os.path.getsize(p), p))
        chosen = cands[0]
        dupes = []
        for c in cands[1:]:
            dupes.append({"path": c, "bytes": os.path.getsize(c), "sha256": sha256(c),
                          "identical_to_canonical": None})
        h = sha256(chosen)
        for d in dupes:
            d["identical_to_canonical"] = (d["sha256"] == h)
        dest = os.path.join(out, "frames", stamp.replace(":", "-") + ".jpg")
        shutil.copy2(chosen, dest)
        with Image.open(dest) as im:
            w, hgt = im.size
        records.append({
            "capture_utc": stamp,
            "date": stamp[:10],
            "hour_utc": stamp[11:13],
            "archived_as": os.path.relpath(dest, out),
            "source_path": chosen,
            "source_mtime_utc": datetime.fromtimestamp(os.path.getmtime(chosen),
                                                       timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "bytes": os.path.getsize(chosen),
            "sha256": h,
            "width": w, "height": hgt,
            "duplicate_copies": dupes,
            "delivery": delivery.get(stamp, {}),
        })

    # ---- 2. registration check + sharpness ----------------------------------
    imgs = {r["capture_utc"]: cv2.imread(os.path.join(out, r["archived_as"]), cv2.IMREAD_COLOR)
            for r in records}
    for r in records:
        r["sharpness_lapvar"] = round(sharpness(imgs[r["capture_utc"]]), 1)

    if args.reference:
        ref_key = next((r["capture_utc"] for r in records if args.reference in r["capture_utc"]
                        or args.reference in r["source_path"]), None)
        if ref_key is None:
            print(f"[warn] --reference {args.reference!r} matched nothing; using sharpest")
    else:
        ref_key = None
    if ref_key is None:
        ref_key = max(records, key=lambda r: r["sharpness_lapvar"])["capture_utc"]
    print(f"[intake] registration reference: {ref_key}")

    ref_gray = cv2.cvtColor(imgs[ref_key], cv2.COLOR_BGR2GRAY).astype(np.float32)
    max_drift = 0.0
    for r in records:
        g = cv2.cvtColor(imgs[r["capture_utc"]], cv2.COLOR_BGR2GRAY).astype(np.float32)
        if g.shape != ref_gray.shape:
            r["shift_px"] = None
            continue
        (dx, dy), resp = cv2.phaseCorrelate(ref_gray, g)
        mag = float(np.hypot(dx, dy))
        max_drift = max(max_drift, mag)
        r["shift_px"] = {"dx": round(dx, 3), "dy": round(dy, 3),
                         "magnitude": round(mag, 3), "response": round(float(resp), 3)}
    mount_ok = max_drift <= args.drift_warn_px
    print(f"[intake] max drift vs reference: {max_drift:.2f} px -> "
          f"{'STABLE, single epoch' if mount_ok else 'EXCEEDS THRESHOLD, check for a new epoch'}")

    # ---- 3. quick-look median composites (control, not the proposed method) --
    size = (records[0]["width"], records[0]["height"])
    sources = {"received": {r["capture_utc"]: os.path.join(out, r["archived_as"]) for r in records}}
    if args.corrected_dir:
        cor = {}
        for r in records:
            stem_glob = os.path.join(os.path.expanduser(args.corrected_dir),
                                     f"*{r['capture_utc'][:13].replace(':', '-')}*", "after_grvi.jpg")
            hit = sorted(glob.glob(stem_glob))
            if hit:
                cor[r["capture_utc"]] = hit[0]
        if cor:
            sources["corrected"] = cor
            print(f"[intake] colour-corrected frames matched: {len(cor)}/{len(records)}")
        else:
            print("[warn] --corrected-dir matched no after_grvi.jpg files")

    made = []
    for label, mapping in sources.items():
        keys = sorted(mapping)
        groupings = {"all": keys}
        by_day, by_hour = defaultdict(list), defaultdict(list)
        for k in keys:
            by_day[k[:10]].append(k)
            by_hour[k[11:13]].append(k)
        for d, ks in by_day.items():
            groupings[f"day_{d}"] = ks
        for hh, ks in by_hour.items():
            if len(ks) >= 2:
                groupings[f"hour_{hh}Z"] = ks
        for gname, ks in groupings.items():
            comp = median_stack([mapping[k] for k in ks], size)
            if comp is None:
                continue
            p = os.path.join(out, "quicklook", f"median_{label}_{gname}.jpg")
            cv2.imwrite(p, comp, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
            made.append({"file": os.path.relpath(p, out), "source": label,
                         "group": gname, "n_frames": len(ks),
                         "sharpness_lapvar": round(sharpness(comp), 1)})
    for m in made:
        print(f"  [quicklook] {m['group']:22} {m['source']:9} n={m['n_frames']:2} "
              f"lapvar={m['sharpness_lapvar']:7.1f}  {m['file']}")

    # ---- 4. contact sheet ----------------------------------------------------
    cols, tw = 4, 340
    th = int(tw * size[1] / size[0])
    rows_n = (len(records) + cols - 1) // cols
    sheet = np.full((rows_n * (th + 26) + 10, cols * (tw + 10) + 10, 3), 245, np.uint8)
    for i, r in enumerate(records):
        thumb = cv2.resize(imgs[r["capture_utc"]], (tw, th), interpolation=cv2.INTER_AREA)
        y = 10 + (i // cols) * (th + 26)
        x = 10 + (i % cols) * (tw + 10)
        sheet[y:y + th, x:x + tw] = thumb
        d = r["delivery"]
        pct = d.get("usable_prefix_pct")
        label = (f"{r['capture_utc'][5:16]}  prefix "
                 f"{pct if pct is not None else '?'}%  lap {r['sharpness_lapvar']:.0f}")
        cv2.putText(sheet, label, (x, y + th + 17), cv2.FONT_HERSHEY_SIMPLEX, 0.42,
                    (40, 40, 40), 1, cv2.LINE_AA)
    sheet_path = os.path.join(out, "cut_sheets", "contact_sheet.jpg")
    cv2.imwrite(sheet_path, sheet, [int(cv2.IMWRITE_JPEG_QUALITY), 88])
    print(f"[intake] contact sheet -> {sheet_path}")

    # ---- 5. manifests --------------------------------------------------------
    json.dump(records, open(os.path.join(out, "frames_manifest.json"), "w"), indent=2)
    flat_fields = ["capture_utc", "date", "hour_utc", "bytes", "sha256", "width", "height",
                   "sharpness_lapvar", "planned_chunks", "received_chunks",
                   "first_gap_index", "usable_prefix_pct", "end_seen", "complete",
                   "shift_dx", "shift_dy", "shift_px", "n_duplicate_copies", "source_path"]
    with open(os.path.join(out, "frames_manifest.csv"), "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=flat_fields)
        w.writeheader()
        for r in records:
            d, s = r["delivery"], r.get("shift_px") or {}
            w.writerow({
                "capture_utc": r["capture_utc"], "date": r["date"], "hour_utc": r["hour_utc"],
                "bytes": r["bytes"], "sha256": r["sha256"][:16], "width": r["width"],
                "height": r["height"], "sharpness_lapvar": r["sharpness_lapvar"],
                "planned_chunks": d.get("planned_chunks"), "received_chunks": d.get("received_chunks"),
                "first_gap_index": d.get("first_gap_index"),
                "usable_prefix_pct": d.get("usable_prefix_pct"), "end_seen": d.get("end_seen"),
                "complete": d.get("complete"), "shift_dx": s.get("dx"), "shift_dy": s.get("dy"),
                "shift_px": s.get("magnitude"), "n_duplicate_copies": len(r["duplicate_copies"]),
                "source_path": r["source_path"],
            })

    try:
        commit = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"],
                                         stderr=subprocess.DEVNULL).decode().strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        commit = "unknown"
    complete_n = sum(1 for r in records if r["delivery"].get("complete") is True)
    with_rec = sum(1 for r in records if r["delivery"])
    json.dump({
        "run_tag": f"reef_intake_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}",
        "git_commit": commit,
        "command": " ".join([sys.argv[0]] + (argv if argv is not None else sys.argv[1:])),
        "args": vars(args),
        "captures": len(records),
        "input_files": len(inputs),
        "skipped_files": skipped,
        "registration_reference": ref_key,
        "max_drift_px": round(max_drift, 3),
        "mount_stable_single_epoch": mount_ok,
        "delivery_records_matched": with_rec,
        "complete_deliveries": complete_n,
        "quicklook": made,
    }, open(os.path.join(out, "run_manifest.json"), "w"), indent=2)

    print(f"[intake] captures={len(records)}  delivery-matched={with_rec}  "
          f"COMPLETE deliveries={complete_n}/{with_rec}")
    print(f"[intake] wrote {out}/frames_manifest.json, frames_manifest.csv, run_manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""
bm_video_tx_budget_ladder.py -- quality ladder BY BYTE BUDGET (Sprint22 Phase 2, bite 1).

PURPOSE
    The first ladder (bm_video_tx_size_sweep.py) swept CRF and reported the size
    it happened to produce. The product needs the opposite: the link gives a
    MESSAGE BUDGET (bytes are messages: 288 raw bytes each) and the camera must
    fill it as well as it can, without trial-and-error encodes on a Pi Zero 2 W.

    For each budget this tool asks ONE question per rate-control mode:
      * does the payload FIT the budget (after the contract's SEI strip)?
      * how much of the budget does it USE (unused messages = wasted quality)?
      * what quality (SSIM vs the lossless reference) does it buy?
      * how many encodes did it cost (energy proxy), and how long did they take?

    One variable: the rate-control mode. Geometry, fps, duration, GOP (one
    keyframe) and the reference are fixed by --ref.

MODES (libx264, one keyframe, yuv420p; target kbps = budget_bytes*8/dur/1000)
    abr1      1-pass average bitrate          -b:v K
    abr1_vbv  1-pass ABR, hard VBV cap        -b:v K -maxrate K -bufsize K*dur
    abr2      2-pass average bitrate          (2 encodes)
    abr2_m96  2-pass aimed at 96 % of budget  (2 encodes; margin for the miss)
    crf_cap   constant quality under a cap    -crf 23 -maxrate K -bufsize K*dur
    crf_search  bisect CRF for the best fit   (N encodes; today's method)

INPUTS
    --ref       lossless reference .mkv from the size sweep (refs_lossless/...)
    --fps       frame rate of --ref
    --budgets   message budgets, comma separated (default 88,126,172)
    --presets   x264 presets, comma separated (default veryslow,veryfast)
    --run-dir   output folder (results.csv, ladder.log, payloads/)

OUTPUTS stay LOCAL: the reference is bench footage and this repo is public.
Only this tool is committed; the run folder has a .gitignore guard.

KNOWN LIMITATIONS
    * Encode seconds are measured on THIS machine; the Pi Zero 2 W is ~10-50x
      slower. Use them to RANK modes (and count encodes), not as Pi numbers.
    * SSIM is codec loss only, vs a lossless reference at the same geometry.
    * One scene. Rate control behaves differently on calm vs busy footage.

EXAMPLE
    python3 tools/bm_video_tx_budget_ladder.py \\
        --ref runs/<sweep>/refs_lossless/ref_d5s_w480_f10.mkv --fps 10 \\
        --run-dir runs/video_tx_budget_ladder_<ts>
"""

import argparse
import csv
import shutil
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import bm_video_tx_loopback as lb          # noqa: E402  strip_x264_sei, ssim_full, run, log

RAW_BYTES_PER_MSG = lb.RAW_BYTES_PER_MSG   # 288
CRF_CAP_QUALITY = 23                       # "as good as we would ever want" ceiling for crf_cap
CRF_SEARCH_RANGE = (18, 51)
ABR2_MARGIN = 0.96                         # abr2_m96 targets 96 % of the budget


def encode(ref, fps, n_frames, preset, rc_args, out_h264, passlog=None):
    """One libx264 encode -> SEI-stripped Annex-B payload. Returns (bytes, seconds)."""
    tmp = out_h264.with_suffix(".mp4")
    base = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ref),
            "-c:v", "libx264", "-preset", preset, "-pix_fmt", "yuv420p",
            "-g", str(n_frames + 1), "-keyint_min", str(n_frames + 1),
            "-sc_threshold", "0", "-an"]
    t0 = time.time()
    if passlog is not None:                                   # 2-pass
        lb.run(base + rc_args + ["-pass", "1", "-passlogfile", str(passlog),
                                 "-f", "null", "/dev/null"])
        lb.run(base + rc_args + ["-pass", "2", "-passlogfile", str(passlog), str(tmp)])
    else:
        lb.run(base + rc_args + [str(tmp)])
    seconds = time.time() - t0
    raw = out_h264.with_suffix(".annexb")
    lb.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(tmp),
            "-c", "copy", "-bsf:v", "h264_mp4toannexb", "-f", "h264", str(raw)])
    payload, _ = lb.strip_x264_sei(raw.read_bytes())
    out_h264.write_bytes(payload)
    tmp.unlink(missing_ok=True)
    raw.unlink(missing_ok=True)
    return len(payload), seconds


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--fps", required=True, type=int)
    ap.add_argument("--budgets", default="88,126,172")
    ap.add_argument("--presets", default="veryslow,veryfast")
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    if not args.ref.is_file():
        sys.exit(f"ref not found: {args.ref}")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not on PATH")

    rd = args.run_dir
    (rd / "payloads").mkdir(parents=True, exist_ok=True)
    (rd / ".gitignore").write_text("*\n")          # bench footage never leaves this folder
    lb._LOG = open(rd / "ladder.log", "a")
    probe = lb.run(["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
                    "-show_entries", "stream=nb_read_frames,width,height", "-of",
                    "csv=p=0", str(args.ref)]).stdout.strip().split(",")
    width, height, n_frames = int(probe[0]), int(probe[1]), int(probe[2])
    dur = n_frames / args.fps
    lb.log(f"=== budget ladder  ref={args.ref.name} {width}x{height} {n_frames} f @ "
           f"{args.fps} fps = {dur:g} s")

    rows = []
    for budget in (int(b) for b in args.budgets.split(",")):
        budget_bytes = budget * RAW_BYTES_PER_MSG
        kbps = budget_bytes * 8 / dur / 1000
        k, buf = f"{kbps:.1f}k", f"{kbps * dur:.1f}k"
        lb.log(f"\n--- budget {budget} msgs = {budget_bytes:,} B -> target {kbps:.1f} kbps")
        for preset in args.presets.split(","):
            modes = {
                "abr1": (["-b:v", k], False),
                "abr1_vbv": (["-b:v", k, "-maxrate", k, "-bufsize", buf], False),
                "abr2": (["-b:v", k], True),
                # 2-pass lands within a few % of target but on EITHER side; aim
                # low by ABR2_MARGIN so the miss stays inside the budget.
                "abr2_m96": (["-b:v", f"{kbps * ABR2_MARGIN:.1f}k"], True),
                "crf_cap": (["-crf", str(CRF_CAP_QUALITY), "-maxrate", k, "-bufsize", buf], False),
            }
            for mode, (rc_args, two_pass) in modes.items():
                out = rd / "payloads" / f"b{budget}_{preset}_{mode}.h264"
                size, secs = encode(args.ref, args.fps, n_frames, preset, rc_args, out,
                                    passlog=(rd / "_x264pass") if two_pass else None)
                rows.append(result(budget, budget_bytes, preset, mode, size, secs,
                                   2 if two_pass else 1, out, args, ""))

            # Today's method: bisect CRF for the highest quality that still fits.
            lo, hi = CRF_SEARCH_RANGE
            best, encodes, total_s = None, 0, 0.0
            out = rd / "payloads" / f"b{budget}_{preset}_crf_search.h264"
            while lo <= hi:
                crf = (lo + hi) // 2
                trial = rd / "payloads" / "_trial.h264"
                size, secs = encode(args.ref, args.fps, n_frames, preset,
                                    ["-crf", str(crf)], trial)
                encodes, total_s = encodes + 1, total_s + secs
                if size <= budget_bytes:
                    best = (crf, size)
                    shutil.copyfile(trial, out)
                    hi = crf - 1                      # fits: try better quality
                else:
                    lo = crf + 1
            (rd / "payloads" / "_trial.h264").unlink(missing_ok=True)
            if best:
                rows.append(result(budget, budget_bytes, preset, "crf_search", best[1],
                                   total_s, encodes, out, args, f"crf={best[0]}"))

    fields = list(rows[0])
    with open(rd / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)
    lb.log(f"\nrows: {len(rows)}  csv: {rd / 'results.csv'}")


def result(budget, budget_bytes, preset, mode, size, secs, encodes, payload, args, note):
    msgs = -(-size // RAW_BYTES_PER_MSG)
    frames, _ = lb.decode_probe(payload, True, args.fps)
    ssim = lb.ssim_full(payload, True, args.fps, args.ref)
    row = {"budget_msgs": budget, "preset": preset, "mode": mode, "bytes": size,
           "msgs": msgs, "fits": msgs <= budget, "budget_used_pct": round(100 * size / budget_bytes, 1),
           "over_msgs": max(0, msgs - budget), "ssim": round(ssim, 4), "frames": frames,
           "encodes": encodes, "encode_s_this_machine": round(secs, 2), "note": note}
    lb.log(f"  {preset:9s} {mode:10s} {size:6,d} B = {msgs:3d} msgs "
           f"({row['budget_used_pct']:5.1f}% of budget) {'FITS' if row['fits'] else 'OVER +' + str(row['over_msgs'])}"
           f"  ssim {ssim:.4f}  {encodes} enc {secs:5.1f}s {note}")
    return row


if __name__ == "__main__":
    main()

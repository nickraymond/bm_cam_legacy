#!/usr/bin/env python3
"""
bm_video_tx_size_sweep.py -- Mac-side size sweep for "short video over Spotter".

PURPOSE
    Answer ONE question before any transmit code is written: how small does a
    3 s / 5 s clip have to be to fit the Spotter cellular lane, and is a clip
    that small still worth watching?  (Step 1 of the video-transmit test ladder,
    2026-09-19.)

    This phase changes ONLY encode geometry/quality (duration, width, fps, CRF).
    It does NOT touch framing, chunking, loss handling, or the UART -- that is
    step 2 (loopback).

INPUTS
    --source   an H.264 .mp4 recorded by the unit (stream-copied excerpt is fine)
    --run-dir  existing/new run folder; outputs are written inside it

OUTPUTS (all inside --run-dir)
    encodes/<cell>.mp4       libx264 encode, for playback + metrics
    encodes/<cell>.h264      the SAME bits as raw Annex-B = the TRANSMIT payload
    refs_lossless/*.mkv      ffv1 reference per (duration, width, fps)
    results.csv              one row per cell (bytes, msgs, cycles, SSIM, status)
    cutsheet_<D>s_<F>fps.png width x CRF grid, same-ROI normalized display
    index.html               every cell playable side by side (open in a browser)
    index_selfcontained.html same page, mp4s inlined -- one file, sendable
    run_manifest.json        source, grid, constants (with provenance), commands
    sweep.log                everything printed

ASSUMPTIONS (labelled -- see BUDGET below for provenance)
    * Payload framing is the existing still-image framing: base64, 384 chars per
      message (rc_field_template bm_serial.image_buffer_size on development).
      384 b64 chars = 288 raw bytes.
    * One message per second (bm_serial.image_transmit_delay_seconds: 1.0).
    * Per-cycle message budgets:
        135 = interim mitigation suggested in TODO-SPOT-001 (safe under the
              observed ~145-message delivery wall on bmcam001 / SPOT-33361C)
        195 = progressive_jpeg.message_cap (configured cap; NOT what bmcam001
              actually delivers today)
      START/END/incomplete overhead messages are NOT counted here.
    * Encoder is Mac libx264. The Pi Zero 2 W encode TIME is not measured by
      this tool -- encode_s in the CSV is Mac time and is recorded only so
      nobody mistakes it for a Pi number. Bytes are what matter here; libx264
      output is deterministic for the same version/settings, but the Pi's
      ffmpeg/libx264 version may differ slightly.
    * SSIM is measured against a lossless (ffv1) copy of the source resampled
      to the same WxH and fps (refs_lossless/), so it measures codec loss
      only -- not the loss from downscaling or dropping frames. Judge those by eye on the cut sheet / index.html.

EXAMPLE
    python3 tools/bm_video_tx_size_sweep.py \
        --source runs/<run>/source/<clip>_first10s.mp4 \
        --run-dir runs/<run>

KNOWN LIMITATIONS
    * One source clip = one scene. Compressibility is scene dependent; a static
      reef and a moving hand are very different. Record which scene was used.
    * No audio (the units record none).
    * Single I-frame per clip (keyint = whole clip) minimises bytes but means a
      lost early chunk loses the whole clip. That trade is step 2's question.
"""

import argparse
import base64
import csv
import json
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

# ---- BUDGET constants (provenance in the module docstring) -----------------
B64_CHARS_PER_MSG = 384
RAW_BYTES_PER_MSG = B64_CHARS_PER_MSG * 3 // 4      # 288
SECONDS_PER_MSG = 1.0
MSG_BUDGET_SAFE = 135
MSG_BUDGET_CAP = 195

# ---- default grid -----------------------------------------------------------
DURATIONS_S = [3, 5]
WIDTHS_PX = [240, 320, 480, 640]
FPS_LIST = [5, 10, 15]
CRF_LIST = [28, 34, 40, 46]

DISPLAY_W = 400          # cut-sheet thumbnail width (all cells resized to this)

_LOG = None


def log(msg=""):
    print(msg, flush=True)
    if _LOG:
        _LOG.write(msg + "\n")
        _LOG.flush()


def run(cmd, capture=False):
    """Run a command, fail loudly. Returns stderr+stdout text when capture."""
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True)
    if res.returncode != 0:
        log(f"COMMAND FAILED ({res.returncode}): {' '.join(map(str, cmd))}")
        log(res.stdout[-2000:])
        sys.exit(2)
    return res.stdout if capture else None


def messages_for(n_bytes):
    """Messages needed for n raw bytes under base64 / 384-char framing."""
    b64_len = 4 * math.ceil(n_bytes / 3)
    return math.ceil(b64_len / B64_CHARS_PER_MSG)


def status_for(msgs):
    if msgs <= MSG_BUDGET_SAFE:
        return "PASS"          # one cycle, under the observed delivery wall
    if msgs <= MSG_BUDGET_CAP:
        return "WARN"          # one cycle only if the full configured cap lands
    return "FAIL"              # needs more than one wake cycle


def vf_chain(width, fps):
    # scale=W:-2 keeps aspect and forces an even height (H.264 4:2:0 needs it)
    return f"fps={fps},scale={width}:-2:flags=lanczos"


def build_reference(source, ref_mkv, duration, width, fps):
    """Lossless (ffv1) reference: the source after ONLY the fps+scale chain.
    Every CRF cell for this (duration, width, fps) is encoded FROM this file and
    scored AGAINST it, so SSIM isolates codec loss and frames pair 1:1."""
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(source), "-t", str(duration), "-an",
         "-vf", vf_chain(width, fps), "-c:v", "ffv1", str(ref_mkv)])


def encode_cell(ref_mkv, out_mp4, out_h264, duration, fps, crf):
    """libx264 -> .mp4 (real timestamps), then stream-copy the SAME bits out as
    raw Annex-B (.h264) = the transmit payload. One keyframe for the clip.

    Do NOT go the other way (raw .h264 -> mp4 with -c copy): with B-frames the
    raw stream carries no timestamps, and that mux dropped a frame and
    scrambled pts (found 2026-09-19 -- it silently broke SSIM and playback)."""
    keyint = int(duration * fps) + 1
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-i", str(ref_mkv),
           "-c:v", "libx264", "-preset", "veryslow", "-crf", str(crf),
           "-pix_fmt", "yuv420p",
           "-g", str(keyint), "-keyint_min", str(keyint), "-sc_threshold", "0",
           "-movflags", "+faststart", str(out_mp4)]
    t0 = time.time()
    run(cmd)
    enc_s = time.time() - t0
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-i", str(out_mp4), "-c", "copy", "-bsf:v", "h264_mp4toannexb",
         "-f", "h264", str(out_h264)])
    return enc_s, cmd


def ssim_vs_reference(mp4, ref_mkv):
    """SSIM of the encoded cell vs its lossless reference (codec loss only)."""
    out = run(["ffmpeg", "-hide_banner", "-i", str(mp4), "-i", str(ref_mkv),
               "-lavfi", "[0:v][1:v]ssim", "-f", "null", "-"], capture=True)
    for line in out.splitlines():
        if "SSIM" in line and "All:" in line:
            return float(line.split("All:")[1].split()[0])
    return float("nan")


def count_decoded_frames(h264, fps):
    """Decode the RAW payload alone (what a receiver gets) and count frames."""
    out = run(["ffmpeg", "-hide_banner", "-r", str(fps), "-i", str(h264),
               "-f", "null", "-"], capture=True)
    hits = [l for l in out.replace("\r", "\n").splitlines() if "frame=" in l]
    return int(hits[-1].split("frame=")[1].split()[0]) if hits else 0


def probe_size(mp4):
    out = run(["ffprobe", "-v", "error", "-select_streams", "v:0",
               "-show_entries", "stream=width,height,nb_frames",
               "-of", "csv=p=0", str(mp4)], capture=True)
    w, h, n = out.strip().split(",")[:3]
    return int(w), int(h), int(n)


def grab_frame(mp4, t_s, out_png):
    run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
         "-ss", f"{t_s:.3f}", "-i", str(mp4), "-frames:v", "1", str(out_png)])


def load_font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_cut_sheet(rows, duration, fps, run_dir, run_tag, frame_t):
    """Width (rows) x CRF (cols). Same-ROI normalized display: every thumbnail
    is RESIZED to DISPLAY_W, so low-res cells are upsampled -- NOT 1:1."""
    cells = [r for r in rows if r["duration_s"] == duration and r["fps"] == fps]
    widths = sorted({r["width"] for r in cells})
    crfs = sorted({r["crf"] for r in cells})
    sample = Image.open(cells[0]["_frame"])
    disp_h = round(DISPLAY_W * sample.height / sample.width)
    pad, cap_h, head_h = 10, 62, 92
    sheet_w = pad + len(crfs) * (DISPLAY_W + pad)
    sheet_h = head_h + len(widths) * (disp_h + cap_h + pad) + pad
    sheet = Image.new("RGB", (sheet_w, sheet_h), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    f_big, f_med, f_sm = load_font(22), load_font(15), load_font(13)
    d.text((pad, 8), f"Video TX size sweep -- {duration} s clip @ {fps} fps  "
           f"(libx264 veryslow, 1 keyframe)", fill="white", font=f_big)
    d.text((pad, 38), f"run: {run_tag}   frame shown: t={frame_t:.2f}s (late "
           f"P-frame)   budget: {RAW_BYTES_PER_MSG} B/msg, 1 msg/s, "
           f"PASS<= {MSG_BUDGET_SAFE} msgs, WARN<= {MSG_BUDGET_CAP}, else FAIL",
           fill=(200, 200, 200), font=f_med)
    d.text((pad, 62), f"SAME-ROI NORMALIZED DISPLAY: every thumbnail is resized "
           f"to {DISPLAY_W}px wide -- low-res cells are UPSAMPLED, not 1:1.",
           fill=(255, 200, 90), font=f_med)
    colors = {"PASS": (70, 190, 110), "WARN": (235, 180, 60),
              "FAIL": (225, 85, 85)}
    for ri, w in enumerate(widths):
        for ci, crf in enumerate(crfs):
            r = next(x for x in cells if x["width"] == w and x["crf"] == crf)
            x = pad + ci * (DISPLAY_W + pad)
            y = head_h + ri * (disp_h + cap_h + pad)
            im = Image.open(r["_frame"]).convert("RGB").resize(
                (DISPLAY_W, disp_h), Image.LANCZOS)
            sheet.paste(im, (x, y))
            col = colors[r["status"]]
            d.rectangle([x, y, x + DISPLAY_W - 1, y + disp_h - 1],
                        outline=col, width=3)
            d.text((x + 2, y + disp_h + 3),
                   f"{r['out_w']}x{r['out_h']}  crf {crf}   [{r['status']}]",
                   fill=col, font=f_med)
            d.text((x + 2, y + disp_h + 22),
                   f"{r['bytes']:,} B = {r['msgs']} msgs = {r['tx_s']:.0f} s tx"
                   f"  ({r['kbps']:.0f} kbps)", fill="white", font=f_sm)
            d.text((x + 2, y + disp_h + 40),
                   f"cycles@135: {r['cycles_safe']}  @195: {r['cycles_cap']}"
                   f"   SSIM {r['ssim']:.3f}", fill=(200, 200, 200), font=f_sm)
    out = run_dir / f"cutsheet_{duration}s_{fps}fps.png"
    sheet.save(out)
    return out


def build_index_html(rows, run_dir, run_tag, embed=False):
    """All cells playable side by side, grouped by duration and fps.
    embed=True inlines every mp4 as a data: URI -> one self-contained file that
    can be sent/opened anywhere (a few MB; the clips are tiny by design)."""
    colors = {"PASS": "#46be6e", "WARN": "#ebb43c", "FAIL": "#e15555"}
    h = ["<!doctype html><meta charset=utf-8>",
         f"<title>Video TX sweep {run_tag}</title>",
         "<style>body{background:#18181c;color:#ddd;font:13px system-ui;"
         "margin:16px}table{border-collapse:collapse}td{padding:6px;"
         "vertical-align:top}video{width:320px;display:block;"
         "image-rendering:auto}h2{margin:22px 0 4px}.c{font-size:12px}"
         "</style>",
         f"<h1>Video TX size sweep &mdash; {run_tag}</h1>",
         f"<p>Players are scaled to 320px wide (NOT 1:1). Budget: "
         f"{RAW_BYTES_PER_MSG} B/msg, 1 msg/s. PASS &le; {MSG_BUDGET_SAFE} "
         f"msgs (one cycle under the observed delivery wall), WARN &le; "
         f"{MSG_BUDGET_CAP} (configured cap), FAIL = multi-cycle.</p>"]
    for dur in sorted({r["duration_s"] for r in rows}):
        for fps in sorted({r["fps"] for r in rows}):
            cells = [r for r in rows
                     if r["duration_s"] == dur and r["fps"] == fps]
            h.append(f"<h2>{dur} s @ {fps} fps</h2><table>")
            for w in sorted({r["width"] for r in cells}):
                h.append("<tr>")
                for r in sorted((c for c in cells if c["width"] == w),
                                key=lambda c: c["crf"]):
                    col = colors[r["status"]]
                    src = f"encodes/{r['cell']}.mp4"
                    if embed:
                        src = "data:video/mp4;base64," + base64.b64encode(
                            (run_dir / src).read_bytes()).decode("ascii")
                    h.append(
                        f"<td><video src='{src}' loop muted "
                        f"autoplay playsinline style='border:3px solid {col}'>"
                        f"</video><div class=c><b style='color:{col}'>"
                        f"{r['status']}</b> {r['out_w']}x{r['out_h']} crf "
                        f"{r['crf']}<br>{r['bytes']:,} B &middot; {r['msgs']} "
                        f"msgs &middot; {r['tx_s']:.0f} s &middot; cycles@135:"
                        f" {r['cycles_safe']}<br>SSIM {r['ssim']:.3f}</div>"
                        f"</td>")
                h.append("</tr>")
            h.append("</table>")
    out = run_dir / ("index_selfcontained.html" if embed else "index.html")
    out.write_text("\n".join(h))
    return out


def main():
    global _LOG
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--source", required=True, type=Path)
    ap.add_argument("--run-dir", required=True, type=Path)
    ap.add_argument("--durations", type=float, nargs="+", default=DURATIONS_S)
    ap.add_argument("--widths", type=int, nargs="+", default=WIDTHS_PX)
    ap.add_argument("--fps", type=int, nargs="+", default=FPS_LIST)
    ap.add_argument("--crf", type=int, nargs="+", default=CRF_LIST)
    args = ap.parse_args()

    if not args.source.is_file():
        sys.exit(f"source not found: {args.source}")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not on PATH")
    run_dir = args.run_dir
    enc_dir, frame_dir = run_dir / "encodes", run_dir / "frames"
    ref_dir = run_dir / "refs_lossless"
    for d in (enc_dir, frame_dir, ref_dir):
        d.mkdir(parents=True, exist_ok=True)
    _LOG = open(run_dir / "sweep.log", "a")
    run_tag = run_dir.name
    durations = [int(d) if float(d).is_integer() else d for d in args.durations]

    n_cells = len(durations) * len(args.widths) * len(args.fps) * len(args.crf)
    log(f"=== bm_video_tx_size_sweep  run={run_tag}")
    log(f"source : {args.source} ({args.source.stat().st_size:,} B)")
    log(f"grid   : dur={durations} widths={args.widths} fps={args.fps} "
        f"crf={args.crf}  -> {n_cells} cells")
    log(f"budget : {RAW_BYTES_PER_MSG} raw B/msg, PASS<={MSG_BUDGET_SAFE} "
        f"({MSG_BUDGET_SAFE * RAW_BYTES_PER_MSG:,} B)  WARN<={MSG_BUDGET_CAP} "
        f"({MSG_BUDGET_CAP * RAW_BYTES_PER_MSG:,} B)")

    rows, example_cmd, i = [], None, 0
    for dur in durations:
        frame_t = max(0.0, dur - 0.4)          # late P-frame, the honest one
        for fps in args.fps:
            for width in args.widths:
                ref = ref_dir / f"ref_d{dur}s_w{width}_f{fps}.mkv"
                build_reference(args.source, ref, dur, width, fps)
                for crf in args.crf:
                    i += 1
                    cell = f"d{dur}s_w{width}_f{fps}_crf{crf}"
                    h264, mp4 = enc_dir / f"{cell}.h264", enc_dir / f"{cell}.mp4"
                    enc_s, cmd = encode_cell(ref, mp4, h264, dur, fps, crf)
                    example_cmd = example_cmd or " ".join(map(str, cmd))
                    out_w, out_h, n_frames = probe_size(mp4)
                    expect = int(round(dur * fps))
                    rx_frames = count_decoded_frames(h264, fps)
                    if n_frames != expect or rx_frames != expect:
                        log(f"FRAME COUNT MISMATCH {cell}: mp4={n_frames} "
                            f"raw_decode={rx_frames} expected={expect}")
                        sys.exit(3)
                    n_bytes = h264.stat().st_size
                    if n_bytes == 0 or n_frames == 0:
                        log(f"EMPTY OUTPUT for {cell}")
                        sys.exit(3)
                    msgs = messages_for(n_bytes)
                    frame = frame_dir / f"{cell}.png"
                    grab_frame(mp4, frame_t, frame)
                    row = {
                        "cell": cell, "duration_s": dur, "width": width,
                        "fps": fps, "crf": crf, "out_w": out_w, "out_h": out_h,
                        "frames": n_frames, "bytes": n_bytes,
                        "mp4_bytes": mp4.stat().st_size,
                        "kbps": n_bytes * 8 / dur / 1000.0,
                        "msgs": msgs, "tx_s": msgs * SECONDS_PER_MSG,
                        "cycles_safe": math.ceil(msgs / MSG_BUDGET_SAFE),
                        "cycles_cap": math.ceil(msgs / MSG_BUDGET_CAP),
                        "status": status_for(msgs),
                        "ssim": ssim_vs_reference(mp4, ref),
                        "mac_encode_s": round(enc_s, 2),
                        "_frame": frame,
                    }
                    rows.append(row)
                    log(f"[{i:3d}/{n_cells}] {cell:26s} {out_w}x{out_h} "
                        f"{n_bytes:8,d} B {msgs:4d} msgs "
                        f"cyc@135={row['cycles_safe']} {row['status']:4s} "
                        f"ssim={row['ssim']:.3f}")

    fields = [k for k in rows[0] if not k.startswith("_")]
    with open(run_dir / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in r.items() if k in fields})

    sheets = [build_cut_sheet(rows, d, f, run_dir, run_tag,
                              max(0.0, d - 0.4))
              for d in durations for f in args.fps]
    index = build_index_html(rows, run_dir, run_tag)
    build_index_html(rows, run_dir, run_tag, embed=True)

    manifest = {
        "tool": "tools/bm_video_tx_size_sweep.py",
        "run_tag": run_tag,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "source": str(args.source),
        "source_bytes": args.source.stat().st_size,
        "grid": {"durations_s": durations, "widths_px": args.widths,
                 "fps": args.fps, "crf": args.crf},
        "budget": {
            "b64_chars_per_msg": B64_CHARS_PER_MSG,
            "raw_bytes_per_msg": RAW_BYTES_PER_MSG,
            "seconds_per_msg": SECONDS_PER_MSG,
            "msg_budget_safe": MSG_BUDGET_SAFE,
            "msg_budget_cap": MSG_BUDGET_CAP,
            "provenance": "development rc_field_template bm_serial island "
                          "(384 / 1.0 s), progressive_jpeg.message_cap 195, "
                          "TODO-SPOT-001 interim ~135. Overhead msgs excluded.",
        },
        "encoder": "Mac libx264 -preset veryslow, single keyframe; "
                   "mac_encode_s is NOT a Pi number",
        "ffmpeg_version": run(["ffmpeg", "-version"],
                              capture=True).splitlines()[0],
        "example_encode_command": example_cmd,
        "argv": sys.argv,
        "counts": {s: sum(r["status"] == s for r in rows)
                   for s in ("PASS", "WARN", "FAIL")},
        "outputs": {"csv": "results.csv", "index": index.name,
                    "cut_sheets": [s.name for s in sheets]},
    }
    (run_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=1))

    log("")
    log(f"cells  : {len(rows)}  " + "  ".join(
        f"{k}={v}" for k, v in manifest["counts"].items()))
    log(f"csv    : {run_dir / 'results.csv'}")
    log(f"sheets : {len(sheets)} in {run_dir}")
    log(f"index  : {index}")
    if len(rows) != n_cells:
        log("ROW COUNT MISMATCH"); sys.exit(4)


if __name__ == "__main__":
    main()

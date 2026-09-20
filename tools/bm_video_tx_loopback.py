#!/usr/bin/env python3
"""
bm_video_tx_loopback.py -- Mac-side loopback + chunk-loss test for video payloads.

PURPOSE
    Step 2 of the video-transmit test ladder (2026-09-19). Two questions, no
    hardware, no cellular:
      1. Is the PRODUCTION chunk framing byte-exact for a binary video payload?
         (frame -> wire -> reassemble -> sha256 must match)
      2. What does message loss do to the clip, per container/GOP choice?
         A progressive JPEG survives a truncated burst; does video?

    The framing is NOT re-implemented here: it imports split_base64_chunks()
    from BM_Devel_Pi/rc_transmit.py and chunk_prefix() from rc_media_id.py, the
    same functions the unit's still-image path uses.

    One variable per axis: payload VARIANT (container / GOP) x LOSS SCENARIO.
    Geometry and quality are fixed (chosen in step 1).

INPUTS
    --ref       lossless reference .mkv from the step-1 sweep
                (refs_lossless/ref_d<D>s_w<W>_f<F>.mkv) -- fixes duration,
                width and fps
    --crf       x264 CRF chosen in step 1
    --fps       frame rate of --ref (raw .h264 carries no timestamps, so the
                RECEIVER MUST BE TOLD THE FPS -- found in step 1)
    --run-dir   output folder

OUTPUTS (inside --run-dir)
    payloads/<variant>.<ext>          what the Pi would send
    wire/<variant>.txt                the exact framed messages, one per line
    recovered/<variant>/<scenario>.*  what the receiver rebuilt
    results.csv                       one row per variant x scenario
    cutsheet_<variant>.png            filmstrip per scenario (resized, not 1:1)
    view_selfcontained.html           every recovered clip, playable, one file
    run_manifest.json, loopback.log

VARIANTS (all libx264 veryslow at --crf, from the same reference)
    h264_1key        raw Annex-B, ONE keyframe, B-frames   (= the step-1 cell)
    h264_1key_nobf   same, -bf 0            (no frame reordering)
    h264_key1s       raw Annex-B, keyframe every 1 s
    mp4_faststart    mp4, moov index at the FRONT, one keyframe
    mp4_moov_end     mp4, moov index at the END (ffmpeg default), one keyframe
    mp4_frag_key1s   fragmented mp4, keyframe + fragment every 1 s

LOSS SCENARIOS
    none             nothing lost                     -> must be byte-exact
    tail_at_145      burst dies after 145 messages    (TODO-SPOT-001 median)
    tail_at_135      burst dies after 135 messages
    drop_first       message 0 lost
    drop_header      the message carrying SPS/PPS (raw) or moov (mp4) lost
    drop_early/mid/late   one single message lost
    random_2pct / random_5pct   seeded random loss

REASSEMBLY POLICY (labelled assumption -- the backend does not exist yet)
    * Each 384-char chunk is a whole number of base64 quanta, so every message
      decodes on its own; a gap costs exactly its own 288 bytes.
    * raw .h264  -> gaps are SKIPPED (decoder resyncs on the next start code)
    * .mp4       -> gaps are ZERO-FILLED (keeps the index byte offsets valid)
    * The receiver knows the planned message count (the real START message
      carries `length`), so it can tell a gap from the end.

KNOWN LIMITATIONS
    * Decode = Mac ffmpeg with error concealment. A browser <video> element is
      far less forgiving than ffmpeg; "ffmpeg recovered N frames" is an UPPER
      bound on what a dashboard player would show.
    * START/END/ack messages are not simulated; only image-chunk messages.
    * SSIM is reported only when the full frame count decodes (otherwise frames
      cannot be paired 1:1 with the reference).

EXAMPLE
    python3 tools/bm_video_tx_loopback.py \
        --ref runs/<sweep>/refs_lossless/ref_d5s_w480_f10.mkv \
        --crf 34 --fps 10 --run-dir runs/video_tx_loopback_<ts>_bmcam003
"""

import argparse
import csv
import base64
import hashlib
import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "BM_Devel_Pi"))
from rc_media_id import chunk_prefix              # noqa: E402  production
from rc_transmit import split_base64_chunks       # noqa: E402  production

B64_CHARS_PER_MSG = 384        # bm_serial.image_buffer_size (development)
RAW_BYTES_PER_MSG = 288
SEED = 20260919
STRIP_W = 230                  # filmstrip thumbnail width (RESIZED, not 1:1)
N_STRIP = 5

WIRE_RE = re.compile(rb"^<I(?:([0-9A-Za-z]+)\.)?(\d+)>(.*)$")

_LOG = None


def log(msg=""):
    print(msg, flush=True)
    if _LOG:
        _LOG.write(msg + "\n")
        _LOG.flush()


def run(cmd, check=True):
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         text=True, errors="replace")
    if check and res.returncode != 0:
        log(f"COMMAND FAILED ({res.returncode}): {' '.join(map(str, cmd))}")
        log(res.stdout[-2000:])
        sys.exit(2)
    return res


# ---- payload variants -------------------------------------------------------
def x264_args(crf, keyint, extra=()):
    return ["-c:v", "libx264", "-preset", "veryslow", "-crf", str(crf),
            "-pix_fmt", "yuv420p", "-g", str(keyint), "-keyint_min",
            str(keyint), "-sc_threshold", "0", *extra]


def build_variants(ref, crf, fps, n_frames, out_dir):
    """Encode every payload variant from the same lossless reference.
    Raw .h264 is extracted FROM an mp4 encode (never the reverse -- step-1
    finding: raw->mp4 stream copy drops a frame when B-frames are present)."""
    one_key, key_1s = n_frames + 1, fps
    ff = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(ref)]
    tmp = out_dir / "_tmp.mp4"
    specs = {}

    def raw_from(name, args):
        run(ff + args + [str(tmp)])
        out = out_dir / f"{name}.h264"
        run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i",
             str(tmp), "-c", "copy", "-bsf:v", "h264_mp4toannexb", "-f",
             "h264", str(out)])
        specs[name] = out

    raw_from("h264_1key", x264_args(crf, one_key))
    raw_from("h264_1key_nobf", x264_args(crf, one_key, ["-bf", "0"]))
    raw_from("h264_key1s", x264_args(crf, key_1s))
    tmp.unlink(missing_ok=True)

    out = out_dir / "mp4_faststart.mp4"
    run(ff + x264_args(crf, one_key) + ["-movflags", "+faststart", str(out)])
    specs["mp4_faststart"] = out
    out = out_dir / "mp4_moov_end.mp4"
    run(ff + x264_args(crf, one_key) + [str(out)])
    specs["mp4_moov_end"] = out
    out = out_dir / "mp4_frag_key1s.mp4"
    run(ff + x264_args(crf, key_1s) + ["-movflags",
        "+frag_keyframe+empty_moov+default_base_moof", str(out)])
    specs["mp4_frag_key1s"] = out
    return specs


# ---- wire -------------------------------------------------------------------
def frame_messages(payload_bytes):
    """EXACTLY what rc_transmit puts on the UART per image chunk."""
    chunks = split_base64_chunks(payload_bytes, B64_CHARS_PER_MSG)
    return [f"{chunk_prefix(i, None)}{c}\n".encode("ascii")
            for i, c in enumerate(chunks)]


def header_message_index(payload, is_raw):
    """Message that carries the decoder's must-have header: SPS (NAL type 7)
    for raw Annex-B, the `moov` box for mp4. NOT message 0 for raw x264 output:
    messages 0-1 are x264's ~690 B version-string SEI (measured 2026-09-19)."""
    if is_raw:
        for hit in re.finditer(b"\x00\x00\x01", payload):
            if payload[hit.end()] & 0x1F == 7:
                return hit.start() // RAW_BYTES_PER_MSG
        return 0
    return max(0, payload.find(b"moov")) // RAW_BYTES_PER_MSG


def loss_scenarios(n_msgs, header_idx):
    rng = random.Random(SEED)
    every = list(range(n_msgs))
    sc = {
        "none": set(),
        "tail_at_145": set(every[145:]),
        "tail_at_135": set(every[135:]),
        "drop_first": {0},
        "drop_header": {header_idx},     # SPS/PPS (raw) or moov (mp4)
        "drop_early": {min(5, n_msgs - 1)},
        "drop_mid": {n_msgs // 2},
        "drop_late": {max(0, n_msgs - 5)},
        "random_2pct": set(rng.sample(every, max(1, round(n_msgs * 0.02)))),
        "random_5pct": set(rng.sample(every, max(1, round(n_msgs * 0.05)))),
    }
    return sc


def reassemble(messages, planned, zero_fill):
    """Receiver side. messages = surviving wire lines, any order."""
    got = {}
    for m in messages:
        hit = WIRE_RE.match(m.rstrip(b"\n"))
        if not hit:
            continue
        got[int(hit.group(2))] = base64.b64decode(hit.group(3))
    out = bytearray()
    for i in range(planned):
        if i in got:
            out += got[i]
        elif zero_fill and i < planned - 1:
            out += b"\x00" * RAW_BYTES_PER_MSG
        # raw h264: skip the gap entirely
    return bytes(out), len(got)


# ---- decode / score ---------------------------------------------------------
def decode_probe(path, is_raw, fps):
    """Decode with concealment; return (frames_decoded, error_lines)."""
    cmd = ["ffmpeg", "-hide_banner", "-err_detect", "ignore_err"]
    if is_raw:
        cmd += ["-r", str(fps)]
    cmd += ["-i", str(path), "-f", "null", "-"]
    res = run(cmd, check=False)
    text = res.stdout.replace("\r", "\n")
    hits = [l for l in text.splitlines() if l.startswith("frame=")]
    frames = int(hits[-1].split("frame=")[1].split()[0]) if hits else 0
    errs = sum(1 for l in text.splitlines()
               if any(k in l.lower() for k in ("error", "invalid", "corrupt",
                                               "concealing", "missing")))
    return frames, errs


def ssim_full(path, is_raw, fps, ref):
    cmd = ["ffmpeg", "-hide_banner", "-err_detect", "ignore_err"]
    if is_raw:
        cmd += ["-r", str(fps)]
    cmd += ["-i", str(path), "-i", str(ref), "-lavfi",
            # settb BEFORE setpts, same timebase on both legs -- otherwise N is
            # counted in different tick sizes and frames pair wrongly (this
            # gave SSIM 0.79 for a byte-exact clip on the first pass).
            f"[0:v]settb=1/{fps},setpts=N[a];[1:v]settb=1/{fps},setpts=N[b];"
            f"[a][b]ssim", "-f", "null", "-"]
    res = run(cmd, check=False)
    for line in res.stdout.splitlines():
        if "SSIM" in line and "All:" in line:
            return float(line.split("All:")[1].split()[0])
    return float("nan")


def filmstrip(path, is_raw, fps, n_frames, out_png):
    """N_STRIP evenly spaced decoded frames, tiled. Missing -> no file."""
    idx = [round((k + 0.5) * n_frames / N_STRIP) for k in range(N_STRIP)]
    sel = "+".join(f"eq(n\\,{i})" for i in idx)
    cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
           "-err_detect", "ignore_err"]
    if is_raw:
        cmd += ["-r", str(fps)]
    cmd += ["-i", str(path), "-vf",
            f"select='{sel}',scale={STRIP_W}:-2,tile={N_STRIP}x1",
            "-frames:v", "1", "-vsync", "0", str(out_png)]
    run(cmd, check=False)
    return out_png if out_png.is_file() and out_png.stat().st_size else None


def load_font(size):
    for p in ("/System/Library/Fonts/Supplemental/Arial.ttf",
              "/System/Library/Fonts/Helvetica.ttc"):
        try:
            return ImageFont.truetype(p, size)
        except OSError:
            continue
    return ImageFont.load_default()


def build_cut_sheet(variant, rows, run_dir, run_tag, strip_h):
    colors = {"PASS": (70, 190, 110), "WARN": (235, 180, 60),
              "FAIL": (225, 85, 85)}
    pad, cap_h, head_h = 10, 40, 92
    w = pad * 2 + STRIP_W * N_STRIP
    h = head_h + len(rows) * (strip_h + cap_h + pad)
    sheet = Image.new("RGB", (w, h), (24, 24, 28))
    d = ImageDraw.Draw(sheet)
    f_big, f_med, f_sm = load_font(20), load_font(14), load_font(13)
    r0 = rows[0]
    d.text((pad, 8), f"Video TX loopback -- {variant}  ({r0['payload_bytes']:,}"
           f" B = {r0['planned_msgs']} msgs)", fill="white", font=f_big)
    d.text((pad, 36), f"run: {run_tag}   {N_STRIP} evenly spaced frames per "
           f"row, decoded by Mac ffmpeg WITH error concealment",
           fill=(200, 200, 200), font=f_med)
    d.text((pad, 58), f"Thumbnails RESIZED to {STRIP_W}px -- not 1:1. A browser "
           f"player is less forgiving than ffmpeg: treat as an upper bound.",
           fill=(255, 200, 90), font=f_med)
    for ri, r in enumerate(rows):
        y = head_h + ri * (strip_h + cap_h + pad)
        col = colors[r["status"]]
        if r["_strip"]:
            im = Image.open(r["_strip"]).convert("RGB")
            sheet.paste(im.crop((0, 0, min(im.width, w - 2 * pad), strip_h)),
                        (pad, y))
        else:
            d.rectangle([pad, y, w - pad, y + strip_h], fill=(50, 20, 20))
            d.text((pad + 12, y + strip_h // 2 - 8), "NOTHING DECODABLE",
                   fill=col, font=f_big)
        d.rectangle([pad, y, w - pad - 1, y + strip_h - 1], outline=col,
                    width=3)
        ssim = "n/a" if r["ssim"] != r["ssim"] else f"{r['ssim']:.3f}"
        d.text((pad + 2, y + strip_h + 3),
               f"[{r['status']}] {r['scenario']}: lost {r['msgs_lost']} of "
               f"{r['planned_msgs']} msgs", fill=col, font=f_med)
        d.text((pad + 2, y + strip_h + 21),
               f"frames decoded {r['frames_decoded']}/{r['frames_expected']} "
               f"({r['playable_s']:.1f} s)   decoder complaints "
               f"{r['decode_errors']}   SSIM(full-count only) {ssim}   "
               f"byte-exact: {r['sha256_match']}",
               fill=(200, 200, 200), font=f_sm)
    out = run_dir / f"cutsheet_{variant}.png"
    sheet.save(out)
    return out


def build_view_page(rows, variants, fps, rd, run_tag):
    """Self-contained HTML of every recovered clip. Recovered files are
    RE-ENCODED (x264 crf 18) purely so a browser can play them -- including raw
    .h264 and damaged files. What you see = what ffmpeg's error concealment
    salvaged, i.e. an UPPER bound on what a dashboard player would show."""
    colors = {"PASS": "#46be6e", "WARN": "#ebb43c", "FAIL": "#e15555"}
    view = rd / "view"
    view.mkdir(exist_ok=True)
    h = ["<!doctype html><meta charset=utf-8>",
         f"<title>Video TX loopback {run_tag}</title>",
         "<style>body{background:#18181c;color:#ddd;font:13px system-ui;"
         "margin:16px}td{padding:6px;vertical-align:top}video,.dead{width:"
         "300px;aspect-ratio:16/9;display:block}.dead{background:#3a1616;"
         "color:#e15555;display:flex;align-items:center;justify-content:"
         "center;font-weight:600}.c{font-size:12px;max-width:300px}</style>",
         f"<h1>Video TX loopback &mdash; {run_tag}</h1>",
         "<p>Each clip went through the production chunk framing, had messages "
         "removed, and was rebuilt. Clips are re-encoded for browser playback "
         "and scaled to 300px (NOT 1:1). This is ffmpeg's error concealment "
         "&mdash; an upper bound on what a dashboard player would show.</p>"]
    for v in variants:
        vr = [r for r in rows if r["variant"] == v]
        h.append(f"<h2>{v} &mdash; {vr[0]['payload_bytes']:,} B = "
                 f"{vr[0]['planned_msgs']} msgs</h2><table><tr>")
        for k, r in enumerate(vr):
            if k and k % 5 == 0:
                h.append("</tr><tr>")
            rec = next((rd / "recovered" / v).glob(f"{r['scenario']}.*"))
            out = view / f"{v}__{r['scenario']}.mp4"
            cmd = ["ffmpeg", "-hide_banner", "-loglevel", "error", "-y",
                   "-err_detect", "ignore_err"]
            if rec.suffix == ".h264":
                cmd += ["-r", str(fps)]
            cmd += ["-i", str(rec), "-c:v", "libx264", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out)]
            run(cmd, check=False)
            col = colors[r["status"]]
            if out.is_file() and out.stat().st_size > 1000:
                uri = "data:video/mp4;base64," + base64.b64encode(
                    out.read_bytes()).decode("ascii")
                media = (f"<video src='{uri}' loop muted autoplay playsinline "
                         f"style='border:3px solid {col}'></video>")
            else:
                media = "<div class=dead>NOTHING DECODABLE</div>"
            h.append(f"<td>{media}<div class=c><b style='color:{col}'>"
                     f"{r['status']}</b> {r['scenario']}: lost "
                     f"{r['msgs_lost']}/{r['planned_msgs']} msgs<br>frames "
                     f"{r['frames_decoded']}/{r['frames_expected']} "
                     f"({r['playable_s']:.1f} s)</div></td>")
        h.append("</tr></table>")
    out = rd / "view_selfcontained.html"
    out.write_text("\n".join(h))
    return out


def main():
    global _LOG
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--ref", required=True, type=Path)
    ap.add_argument("--crf", required=True, type=int)
    ap.add_argument("--fps", required=True, type=int)
    ap.add_argument("--run-dir", required=True, type=Path)
    args = ap.parse_args()
    if not args.ref.is_file():
        sys.exit(f"ref not found: {args.ref}")
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            sys.exit(f"{tool} not on PATH")

    rd = args.run_dir
    for sub in ("payloads", "wire", "recovered", "strips"):
        (rd / sub).mkdir(parents=True, exist_ok=True)
    _LOG = open(rd / "loopback.log", "a")
    run_tag = rd.name

    probe = run(["ffprobe", "-v", "error", "-count_frames", "-select_streams",
                 "v:0", "-show_entries", "stream=nb_read_frames,width,height",
                 "-of", "csv=p=0", str(args.ref)]).stdout.strip().split(",")
    ref_w, ref_h, n_frames = int(probe[0]), int(probe[1]), int(probe[2])
    strip_h = round(STRIP_W * ref_h / ref_w / 2) * 2
    log(f"=== bm_video_tx_loopback  run={run_tag}")
    log(f"ref    : {args.ref}  {ref_w}x{ref_h}  {n_frames} frames @ "
        f"{args.fps} fps  crf {args.crf}")
    log(f"framing: production split_base64_chunks + chunk_prefix, "
        f"{B64_CHARS_PER_MSG} b64 chars/msg = {RAW_BYTES_PER_MSG} B")

    variants = build_variants(args.ref, args.crf, args.fps, n_frames,
                              rd / "payloads")
    rows = []
    for name, payload_path in variants.items():
        is_raw = payload_path.suffix == ".h264"
        payload = payload_path.read_bytes()
        sha = hashlib.sha256(payload).hexdigest()
        msgs = frame_messages(payload)
        (rd / "wire" / f"{name}.txt").write_bytes(b"".join(msgs))
        longest = max(len(m) for m in msgs)
        log(f"\n--- {name}: {len(payload):,} B -> {len(msgs)} msgs "
            f"(longest wire line {longest} B)  sha256 {sha[:16]}")
        (rd / "recovered" / name).mkdir(exist_ok=True)
        hdr = header_message_index(payload, is_raw)
        log(f"    header (SPS / moov) rides in message #{hdr}")
        for scen, lost in loss_scenarios(len(msgs), hdr).items():
            survivors = [m for i, m in enumerate(msgs) if i not in lost]
            random.Random(SEED).shuffle(survivors)   # arrival order != send
            data, n_got = reassemble(survivors, len(msgs),
                                     zero_fill=not is_raw)
            rec = rd / "recovered" / name / f"{scen}{payload_path.suffix}"
            rec.write_bytes(data)
            match = hashlib.sha256(data).hexdigest() == sha
            frames, errs = decode_probe(rec, is_raw, args.fps)
            ssim = (ssim_full(rec, is_raw, args.fps, args.ref)
                    if frames == n_frames else float("nan"))
            strip = filmstrip(rec, is_raw, args.fps, n_frames,
                              rd / "strips" / f"{name}__{scen}.png")
            if match:
                status = "PASS"
            elif frames >= n_frames / 2:
                status = "WARN"
            else:
                status = "FAIL"
            if scen == "none" and not match:
                log(f"FRAMING IS NOT BYTE-EXACT for {name} -- stop.")
                sys.exit(3)
            row = {"variant": name, "scenario": scen,
                   "payload_bytes": len(payload), "planned_msgs": len(msgs),
                   "msgs_lost": len(lost & set(range(len(msgs)))),
                   "msgs_received": n_got, "bytes_recovered": len(data),
                   "sha256_match": match, "frames_expected": n_frames,
                   "frames_decoded": frames,
                   "playable_s": frames / args.fps, "decode_errors": errs,
                   "ssim": ssim, "status": status, "_strip": strip}
            rows.append(row)
            s = "n/a" if ssim != ssim else f"{ssim:.3f}"
            log(f"  {scen:12s} lost {row['msgs_lost']:3d}  frames "
                f"{frames:3d}/{n_frames}  errs {errs:3d}  ssim {s:>5s}  "
                f"exact={match!s:5s} {status}")

    fields = [k for k in rows[0] if not k.startswith("_")]
    with open(rd / "results.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: (round(v, 4) if isinstance(v, float) else v)
                        for k, v in r.items() if k in fields})
    sheets = [build_cut_sheet(v, [r for r in rows if r["variant"] == v], rd,
                              run_tag, strip_h) for v in variants]

    page = build_view_page(rows, list(variants), args.fps, rd, run_tag)
    log(f"view   : {page} ({page.stat().st_size:,} B)")

    manifest = {
        "tool": "tools/bm_video_tx_loopback.py", "run_tag": run_tag,
        "utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ref": str(args.ref), "crf": args.crf, "fps": args.fps,
        "frames": n_frames, "geometry": f"{ref_w}x{ref_h}",
        "framing": {"source": "BM_Devel_Pi/rc_transmit.split_base64_chunks + "
                              "rc_media_id.chunk_prefix (imported, not copied)",
                    "b64_chars_per_msg": B64_CHARS_PER_MSG,
                    "raw_bytes_per_msg": RAW_BYTES_PER_MSG},
        "reassembly_policy": {"h264": "skip gaps", "mp4": "zero-fill gaps",
                              "planned_count": "assumed known (START length)"},
        "seed": SEED, "argv": sys.argv,
        "variants": {k: {"file": str(v.relative_to(rd)),
                         "bytes": v.stat().st_size} for k, v in variants.items()},
        "ffmpeg_version": run(["ffmpeg", "-version"]).stdout.splitlines()[0],
        "outputs": {"csv": "results.csv",
                    "cut_sheets": [s.name for s in sheets]},
    }
    (rd / "run_manifest.json").write_text(json.dumps(manifest, indent=1))
    log(f"\nrows   : {len(rows)}   csv: {rd / 'results.csv'}")
    log(f"sheets : {', '.join(s.name for s in sheets)}")


if __name__ == "__main__":
    main()

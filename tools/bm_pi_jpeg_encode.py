#!/usr/bin/env python3
"""
BM Pi-side JPEG encoder (Sprint 07 — Pi validation of the Sprint06 verdict)

Purpose
-------
Encode-only twin of tools/bm_reference_card_jpeg_partial_sweep.py, meant to
run ON THE PI (bmcam000, Pi Zero 2W). Division of labor per Sprint07:

    Pi  : image altering only — native load -> fixed crop -> lanczos
          downsample -> Pillow JPEG encode (baseline/progressive x quality).
    Mac : ALL image-quality analysis (metrics, AprilTag detection, cut
          sheets) on the pulled-back artifacts, with the unchanged
          Sprint06 tooling.

The crop/resize/encode path replicates the sweep tool exactly:
  - crop box in native 4608x2592 sensor-equivalent coords (x, y, w, h)
  - Image.Resampling.LANCZOS to (output_width, round(output_width * h / w))
  - save(format="JPEG", quality=q, progressive=(mode=="progressive"),
         optimize=True)   # identical Pillow call to the Mac DOE
  - base64_len = len(base64(jpeg_bytes)); message_count = ceil(b64/300);
    est_minutes = msgs * 5 / 60

Additions over the sweep tool (the Sprint07 variables):
  - per-encode wall time (repeatable via --timing-repeats; file written once)
  - peak RSS (resource.getrusage ru_maxrss) logged per encode
  - board/OS/meminfo snapshot embedded in run_manifest.json
  - sha256 of the JPEG bytes and of the raw RGB pixels of the working
    source, so the Mac can attribute any byte delta to encoder vs resize.

Dependencies: Pillow + stdlib only (NO cv2/numpy — analysis is Mac-side).

Inputs (defaults, repo-relative)
--------------------------------
  card  : reference_images/reference_card_native_imx708.jpg
  coral : reference_images/prepared/P7071008/synthetic_native_4608x2592.jpg

Outputs (timestamped, self-contained run folder)
------------------------------------------------
  <out>/run_manifest.json
  <out>/encode_log.txt
  <out>/source_<W>/<label>_source_<W>x<H>.png     (lossless working source)
  <out>/jpeg/<label>/<label>_<mode>_qNN.jpg       (same stems as the sweep)
  <out>/results/encode_results.csv

Example (P0 parity smoke, on the Pi)
------------------------------------
  python3 tools/bm_pi_jpeg_encode.py \
      --images card --crop-native 1467 1255 1600 900 --output-width 1000 \
      --modes baseline progressive --qualities 13 \
      --output ~/bm_sprint07_runs/p0_parity_<UTC>

Assumptions / known limitations
-------------------------------
  - One geometry per invocation (file stems do not encode geometry); run
    card and coral crops as separate invocations into the same parent.
  - ru_maxrss is process-lifetime peak (monotonic): only the FIRST encode's
    delta is a clean per-encode number; later rows report the running peak.
  - Message counts are payload-only (no BM framing/retransmits), matching
    the Mac DOE model.
"""
from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import math
import platform
import resource
import shutil
import sys
import time
import traceback
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent

# Sprint-fixed geometry defaults (native sensor-equivalent coords).
FIXED_CROP_NATIVE = (768, 432, 3072, 1728)  # x, y, w, h (Sprint02 fixed crop)
OUTPUT_SIZE = (1600, 900)                   # rebound in main() from --output-width
NATIVE_SIZE = (4608, 2592)

# Transmission model (Sprint06 spec section 2).
CHUNK_B64_CHARS = 300
SECONDS_PER_MESSAGE = 5.0
BAND_IDEAL_MAX = 75
BAND_FEASIBLE_MAX = 125
BAND_GATED_MAX = 180        # sweep-CSV band (kept for column parity)
BAND_CAP195_MAX = 195       # field-tested hard cap (P3 verdict bands)

DEFAULT_IMAGES = {
    "card": REPO_ROOT / "reference_images" / "reference_card_native_imx708.jpg",
    "coral": REPO_ROOT / "reference_images" / "prepared" / "P7071008" / "synthetic_native_4608x2592.jpg",
}


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def ensure_dir(p: Path) -> Path:
    p.mkdir(parents=True, exist_ok=True)
    return p


class RunLog:
    """Print progress lines and mirror them into the run folder."""

    def __init__(self) -> None:
        self.lines: List[str] = []
        self.path: Optional[Path] = None

    def attach(self, path: Path) -> None:
        self.path = path
        if self.lines:
            path.write_text("\n".join(self.lines) + "\n", encoding="utf-8")

    def __call__(self, msg: str) -> None:
        line = f"[pi-encode] {msg}"
        print(line, flush=True)
        self.lines.append(line)
        if self.path is not None:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")


log = RunLog()


def read_text_first_line(path: str) -> str:
    try:
        return Path(path).read_text(errors="replace").replace("\x00", "").strip().splitlines()[0]
    except Exception:
        return ""


def proc_meminfo_fields() -> Dict[str, str]:
    out: Dict[str, str] = {}
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            k = line.split(":")[0]
            if k in {"MemTotal", "MemAvailable", "CmaTotal", "CmaFree"}:
                out[k] = line.split(":", 1)[1].strip()
    except Exception:
        pass
    return out


def hardware_snapshot() -> Dict[str, object]:
    """Board/OS/memory snapshot for the manifest (Linux; degrades on Mac)."""
    return {
        "board_model": read_text_first_line("/proc/device-tree/model"),
        "os_pretty_name": next(
            (l.split("=", 1)[1].strip('"') for l in Path("/etc/os-release").read_text().splitlines()
             if l.startswith("PRETTY_NAME=")), "") if Path("/etc/os-release").exists() else "",
        "machine": platform.machine(),
        "long_bit": 64 if sys.maxsize > 2**32 else 32,
        "meminfo": proc_meminfo_fields(),
        "hostname": platform.node(),
    }


def peak_rss_kb() -> int:
    """Process peak RSS. Linux reports ru_maxrss in KB, macOS in bytes."""
    v = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return int(v // 1024) if sys.platform == "darwin" else int(v)


def duration_band(message_count: int, cap: int) -> str:
    if message_count <= BAND_IDEAL_MAX:
        return "ideal"
    if message_count <= BAND_FEASIBLE_MAX:
        return "feasible"
    if message_count <= cap:
        return "gated"
    return "over_cap"


def prepare_source(label: str, native_path: Path, out_dir: Path) -> Path:
    """Native 4608x2592 -> fixed crop (native coords) -> lanczos PNG.

    Identical transform to the sweep tool; PNG keeps the working source
    lossless. Also records sha256 of the raw RGB pixels so the Mac can
    verify resize parity independent of PNG encoding differences.
    """
    t0 = time.perf_counter()
    with Image.open(native_path) as im:
        im = im.convert("RGB")
        if im.size != NATIVE_SIZE:
            raise SystemExit(
                f"{label}: expected native {NATIVE_SIZE[0]}x{NATIVE_SIZE[1]}, got {im.size[0]}x{im.size[1]}: {native_path}"
            )
        x, y, w, h = FIXED_CROP_NATIVE
        cropped = im.crop((x, y, x + w, y + h))
        source = cropped.resize(OUTPUT_SIZE, Image.Resampling.LANCZOS)
    pixel_sha = hashlib.sha256(source.tobytes()).hexdigest()
    out = ensure_dir(out_dir / f"source_{OUTPUT_SIZE[0]}") / f"{label}_source_{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}.png"
    source.save(out, format="PNG")
    dt = time.perf_counter() - t0
    log(f"source ready: {label} native={native_path.name} crop_native={FIXED_CROP_NATIVE} -> "
        f"{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} ({out.stat().st_size/1024:.0f} KB PNG, {dt:.2f}s, "
        f"pixel_sha256={pixel_sha[:16]}...)")
    (out.with_suffix(".pixel_sha256.txt")).write_text(pixel_sha + "\n", encoding="utf-8")
    return out


@dataclass
class EncodeRow:
    image_label: str
    mode: str
    jpeg_quality: int
    jpeg_bytes: int
    jpeg_kb: float
    base64_len: int
    message_count: int
    est_minutes: float
    duration_band: str
    duration_band_cap195: str
    encode_wall_s_mean: float
    encode_wall_s_best: float
    timing_repeats: int
    peak_rss_kb_after: int
    jpeg_sha256: str
    source_pixel_sha256: str
    jpeg_path: str


def encode_jpeg(label: str, source_png: Path, mode: str, quality: int,
                out_dir: Path, timing_repeats: int) -> EncodeRow:
    """Pillow JPEG encode, identical call to the sweep tool, with timing.

    The source PNG is decoded once, outside the timed region, so the wall
    time measures crop-source -> JPEG encode only (the Sprint07 variable).
    """
    stem = f"{label}_{mode}_q{quality:02d}"
    jpeg_path = ensure_dir(out_dir / "jpeg" / label) / f"{stem}.jpg"
    with Image.open(source_png) as im:
        src = im.convert("RGB")
    times: List[float] = []
    for _ in range(max(1, timing_repeats)):
        t0 = time.perf_counter()
        src.save(
            jpeg_path,
            format="JPEG",
            quality=quality,
            progressive=(mode == "progressive"),
            optimize=True,  # matches the Mac DOE: progressive implies optimized tables
        )
        times.append(time.perf_counter() - t0)
    raw = jpeg_path.read_bytes()
    b64_len = len(base64.b64encode(raw))
    msgs = math.ceil(b64_len / CHUNK_B64_CHARS)
    sha_path = source_png.with_suffix(".pixel_sha256.txt")
    return EncodeRow(
        image_label=label,
        mode=mode,
        jpeg_quality=quality,
        jpeg_bytes=len(raw),
        jpeg_kb=round(len(raw) / 1024.0, 3),
        base64_len=b64_len,
        message_count=msgs,
        est_minutes=round(msgs * SECONDS_PER_MESSAGE / 60.0, 3),
        duration_band=duration_band(msgs, BAND_GATED_MAX),
        duration_band_cap195=duration_band(msgs, BAND_CAP195_MAX),
        encode_wall_s_mean=round(sum(times) / len(times), 4),
        encode_wall_s_best=round(min(times), 4),
        timing_repeats=len(times),
        peak_rss_kb_after=peak_rss_kb(),
        jpeg_sha256=hashlib.sha256(raw).hexdigest(),
        source_pixel_sha256=sha_path.read_text().strip() if sha_path.exists() else "",
        jpeg_path=str(jpeg_path),
    )


def write_csv(path: Path, rows: List[Dict[str, object]]) -> None:
    ensure_dir(path.parent)
    fields: List[str] = []
    for r in rows:
        for k in r.keys():
            if k not in fields:
                fields.append(k)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(description="Sprint07 Pi-side JPEG encode (crop/downsample/encode only; analysis is Mac-side).")
    ap.add_argument("--images", nargs="+", choices=sorted(DEFAULT_IMAGES), default=["card"],
                    help="Which fixed inputs to run. Default: card")
    ap.add_argument("--card-path", type=Path, default=DEFAULT_IMAGES["card"])
    ap.add_argument("--coral-path", type=Path, default=DEFAULT_IMAGES["coral"])
    ap.add_argument("--modes", nargs="+", choices=["baseline", "progressive"], default=["baseline", "progressive"])
    ap.add_argument("--qualities", nargs="+", type=int, default=[13])
    ap.add_argument("--timing-repeats", type=int, default=1,
                    help="Encode each cell N times for timing stability (file written each pass). Default: 1")
    ap.add_argument("--crop-native", nargs=4, type=int, metavar=("X", "Y", "W", "H"),
                    default=list(FIXED_CROP_NATIVE),
                    help="Native crop (ROI) in native 4608x2592 coords. "
                         f"Default: {' '.join(str(v) for v in FIXED_CROP_NATIVE)}")
    ap.add_argument("--output-width", type=int, default=OUTPUT_SIZE[0],
                    help="Output width in px; height follows the crop aspect ratio. Default: 1600")
    ap.add_argument("--output", type=Path, default=None,
                    help="Run folder. Default: ~/bm_sprint07_runs/pi_encode_<UTC>")
    ap.add_argument("--overwrite", action="store_true", help="Remove the output folder first if it exists")
    return ap


def main() -> int:
    args = build_parser().parse_args()

    global FIXED_CROP_NATIVE, OUTPUT_SIZE
    cx, cy, cw, ch = args.crop_native
    if not (0 <= cx and 0 <= cy and cx + cw <= NATIVE_SIZE[0] and cy + ch <= NATIVE_SIZE[1] and cw > 0 and ch > 0):
        raise SystemExit(f"--crop-native {args.crop_native} outside native {NATIVE_SIZE[0]}x{NATIVE_SIZE[1]}")
    if not 200 <= args.output_width <= cw:
        raise SystemExit(f"--output-width {args.output_width} must be 200..{cw} (no upsampling beyond the crop)")
    FIXED_CROP_NATIVE = (cx, cy, cw, ch)
    OUTPUT_SIZE = (args.output_width, round(args.output_width * ch / cw))  # same rounding as the sweep tool

    qualities = sorted(set(args.qualities))
    for q in qualities:
        if not 1 <= q <= 95:
            raise SystemExit(f"Invalid JPEG quality {q}; expected 1-95")

    run_tag = f"pi_encode_{utc_stamp()}"
    out_dir = (args.output or (Path.home() / "bm_sprint07_runs" / run_tag)).expanduser().resolve()
    if out_dir.exists() and args.overwrite:
        shutil.rmtree(out_dir)
    ensure_dir(out_dir)
    log.attach(out_dir / "encode_log.txt")
    log(f"run_tag={run_tag}")
    log(f"output={out_dir}")
    log(f"images={args.images} modes={args.modes} qualities={qualities} "
        f"crop_native={FIXED_CROP_NATIVE} output={OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]}")

    from PIL import features
    image_paths = {"card": args.card_path.resolve(), "coral": args.coral_path.resolve()}
    manifest: Dict[str, object] = {
        "tool": "bm_pi_jpeg_encode.py",
        "sprint": "Sprint07 Pi validation (encode-only; analysis is Mac-side)",
        "run_tag": run_tag,
        "created_utc": utc_stamp(),
        "platform": platform.platform(),
        "python": sys.version,
        "pillow": getattr(Image, "__version__", ""),
        "libjpeg_turbo": features.version_feature("libjpeg_turbo") if features.check_feature("libjpeg_turbo") else "",
        "jpeglib_api": features.version("jpg"),
        "hardware": hardware_snapshot(),
        "coordinate_systems": {
            "native": f"{NATIVE_SIZE[0]}x{NATIVE_SIZE[1]} sensor-equivalent px; fixed_crop_xywh={FIXED_CROP_NATIVE}",
            "output": f"{OUTPUT_SIZE[0]}x{OUTPUT_SIZE[1]} lanczos-downsampled px",
        },
        "transmission_model": {
            "chunk_base64_chars": CHUNK_B64_CHARS,
            "seconds_per_message": SECONDS_PER_MESSAGE,
            "message_count_formula": "ceil(len(base64(jpeg_bytes)) / 300)",
            "bands_messages": {"ideal_max": BAND_IDEAL_MAX, "feasible_max": BAND_FEASIBLE_MAX,
                               "gated_max": BAND_GATED_MAX, "hard_cap_cap195": BAND_CAP195_MAX},
        },
        "encode_settings": {
            "encoder": "Pillow Image.save JPEG (on-device)",
            "optimize": True,
            "subsampling": "Pillow default (4:2:0)",
        },
        "inputs": {k: str(v) for k, v in image_paths.items() if k in args.images},
        "args": {k: (str(v) if isinstance(v, Path) else v) for k, v in vars(args).items()},
        "restore_notes": "no system changes; no camera access; nothing to restore",
        "errors": [],
    }

    try:
        rows: List[Dict[str, object]] = []
        for label in args.images:
            source_png = prepare_source(label, image_paths[label], out_dir)
            for mode in args.modes:
                for q in qualities:
                    row = encode_jpeg(label, source_png, mode, q, out_dir, args.timing_repeats)
                    log(f"encode {label} {mode} q{q}: {row.jpeg_kb} KB -> b64={row.base64_len} "
                        f"msgs={row.message_count} ({row.est_minutes} min, {row.duration_band_cap195}) "
                        f"wall={row.encode_wall_s_mean}s peak_rss={row.peak_rss_kb_after}KB")
                    rows.append(asdict(row))

        write_csv(out_dir / "results" / "encode_results.csv", rows)
        log(f"results CSV: {out_dir / 'results' / 'encode_results.csv'} ({len(rows)} rows)")
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        log("complete")
        return 0
    except Exception as exc:
        manifest["errors"].append({"type": type(exc).__name__, "message": str(exc), "traceback": traceback.format_exc()})
        (out_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
        log(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

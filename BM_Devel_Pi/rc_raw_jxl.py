#!/usr/bin/env python3
# filename: rc_raw_jxl.py
# description: Sprint28 S1 — RAW-plane JPEG XL stills (fmt=nrjxl): DNG crop reader, 4 Bayer planes, sqrt 12-bit, capped cjxl rung walk, NR container v1.
"""
Sprint28 path [B]: a native-resolution RAW still as JPEG XL (SPEC r4 §3, CONTAINER.md v1).

What it does (one still action, only when `still.format = nrjxl`):
  1. read ONLY the crop rows of the rpicam DNG (`still.crop`, native sensor px) — 2.9 MB
     at 1600x900 instead of the 24 MB frame (read_dng_crop);
  2. split the Bayer mosaic into R, G1, G2, B planes (crop/2 each) and map each count
     through the study's integer sqrt LUT to 12-bit codes (sqrt_lut, split_planes);
  3. encode each plane with `cjxl -m 1 -e <effort> -d <distance> --num_threads=0` in its
     OWN process with RLIMIT_AS 250 MB and oom_score_adj 1000 (run_capped): an overrun
     kills the encoder, never this process;
  4. walk the distance rungs (still.raw.distances, best quality first) until the blob fits
     the SAME message budget the pjpg selector uses (message_cap and the CycleBudget,
     START/END and the heal slot reserved) — rung_walk;
  5. wrap the 4 streams in the study's `NR` header (CONTAINER.md §1-2, profile v1, with
     the capture's WB/CCM and a crc32 over header + payloads, §4 crc-v1b) — seal_container.
  Any failure raises RawFallback(code), and the caller sends today's JPEG with `rfb=<code>`
  in the same wake (SPEC §6). Nothing here touches the BM bus.

Inputs:  the DNG + the rpicam --metadata JSON of the SAME exposure, still.crop, the
         `still_raw:` island of the rendered camera_schedule.yaml (load_raw_config),
         the CycleBudget, the message cap and chunk size of this wake.
Outputs: {blob, distance, attempts, attempt_log, message_count, ...} or RawFallback.

Coordinate systems (CLAUDE.md §12):
  - still.crop and header params crop_x/crop_y: NATIVE sensor px (the 4608x2592 frame);
  - plane px = crop px / 2 (each plane is w/2 x h/2);
  - the DNG's stored image = native px plus an even ActiveArea offset when present.

Assumptions (labelled; R0 on bmcam003/004 measures them, SPEC §7.2):
  - rpicam-still --raw writes `<-o stem>.dng` next to the JPEG (rpicam-apps still app;
    seen on nereus002 with rpicam-apps v1.12.0, rig data/s4_20260930). Not yet seen on
    bmcam003/004 (trixie).
  - The IMX708 DNG: uncompressed 16-bit container, one strip, CFA BGGR, BlackLevel 64 (2x2
    repeat, all equal), WhiteLevel 1023 (rig study DNGs). The reader takes every value
    from the tags and refuses what it does not handle.
  - cjxl on trixie = libjxl 0.11.x like the study's v0.11.1 (R0.4 records the version);
    distances were calibrated on the Mac with v0.11.1 (SPEC §3.5, S0 calibration in
    sprints/Sprint28_raw_jxl/S0_CALIBRATION.md).
  - numpy is on the unit (R0.4 checks; missing numpy = rfb=err, never a lost image).

Example (Mac, a study DNG):
  .venv-dev/bin/python BM_Devel_Pi/rc_raw_jxl.py --dng stop_-1_r0.dng \\
      --metadata stop_-1_r0.json --crop 1504,846,1600,900 --distances 3.5,4.2 --out /tmp/x

Known limitations: hydrium (method 19) is Next sprint; no partial nrjxl send (a prefix of
4 concatenated planes renders nothing, SPEC §3.6); peak RSS is read from wait4 (Linux:
KiB, macOS: bytes) and is only meaningful on Linux.
"""

import json
import math
import os
import shutil
import signal
import struct
import sys
import time
import zlib

FORMAT = "nrjxl"
EXTENSION = ".nrjxl"
CONTENT_SUFFIX = "_compressed" + EXTENSION   # <stem>_compressed.nrjxl (CONTAINER.md §7)
RAW_CROP_SUFFIX = "_raw_crop.pgm"            # still.raw.keep_crop: the crop mosaic, kept
WORK_PREFIX = ".nrjxl_work_"                 # per-action temp dir under the images dir

# CONTAINER.md §1 (the study's Header, rig compression_study/common.py)
MAGIC = b"NR"
METHOD_D2 = 14
FLAGS_4PL_SQRT = 0x04                        # layout 4pl = 0 | curve sqrt = 1 << 2
CFA_PATTERNS = ("RGGB", "BGGR", "GRBG", "GBRG")
CODE_BITS = 12
CODE_MAX = (1 << CODE_BITS) - 1
PROFILE_VERSION = 1
N_PARAMS_V1 = 24
SENTINEL = -32768                            # an optional param that is absent

# SPEC §3.3: until R0.3 proves a larger crop on the Zero, nrjxl allows 1600x900 only.
# One number: config_validate refuses the config, this module refuses the encode.
from config_validate import RAW_MAX_PX  # noqa: E402

# SPEC §3.5 guards (the study's pi_bench.child values)
ENCODER_MEM_LIMIT_BYTES = 250 * 2 ** 20
ENCODER_OOM_SCORE_ADJ = 1000
RAW_CAPTURE_TIMEOUT_S = 30                   # one --raw attempt, no retries (SPEC §3.1)

# START rfb codes (SPEC §3.6 table)
RFB_CODES = ("cap", "dng", "enc", "mem", "time", "fit", "err")

def _registry_default(path):
    import config_registry
    value = config_registry.BY_PATH[path].default
    return list(value) if isinstance(value, list) else value


# The island's defaults ARE the registry's (one source; still.raw.distances comes from
# the S0 calibration, runs/s28_s0_calibration_20261002/rungs.json).
DEFAULT_CONFIG = {
    "format": _registry_default("still.format"),
    "distances": _registry_default("still.raw.distances"),
    "encode_max_s": _registry_default("still.raw.encode_max_s"),
    "keep_crop": _registry_default("still.raw.keep_crop"),
    "effort": _registry_default("still.raw.effort"),
    "source": "default",
}


class RawFallback(Exception):
    """Path [B] failed: send today's JPEG with START rfb=<code> (SPEC §6)."""

    def __init__(self, code, detail, attempt_log=None, timings=None):
        if code not in RFB_CODES:
            code = "err"
        super().__init__(f"rfb={code}: {detail}")
        self.code = code
        self.detail = str(detail)
        self.attempt_log = list(attempt_log or [])
        self.timings = dict(timings or {})


# ---------------------------------------------------------------------------
# config island (rendered from the v2 keys still.format / still.raw.*)
# ---------------------------------------------------------------------------

def parse_distances(text):
    """"3.0,3.6,4.3" -> [3.0, 3.6, 4.3]: 1..4 finite numbers in 0.1..15, strictly
    ascending (best quality first). Raises ValueError naming the problem."""
    parts = [p for chunk in str(text).split(",") for p in chunk.split()]
    if not 1 <= len(parts) <= 4:
        raise ValueError(f"still_raw.distances needs 1..4 values, got {text!r}")
    try:
        out = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"still_raw.distances must be numbers, got {text!r}")
    for d in out:
        if not math.isfinite(d) or not 0.1 <= d <= 15.0:
            raise ValueError(f"still_raw.distances values must be 0.1..15, got {d}")
    if any(b <= a for a, b in zip(out, out[1:])):
        raise ValueError(f"still_raw.distances must be strictly ascending, got {out}")
    return out


def load_raw_config(config_path):
    """Read the `still_raw:` island (flat line parser, the media_key pattern: no
    PyYAML). Absent island = pjpg with the registry defaults. Raises ValueError on a
    bad value, naming the key (the caller treats that as rfb=err, never a lost boot)."""
    cfg = dict(DEFAULT_CONFIG)
    cfg["distances"] = list(DEFAULT_CONFIG["distances"])
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            in_island = False
            for raw in f:
                line = raw.split("#", 1)[0].rstrip()
                if not line.strip():
                    continue
                if not line.startswith(" "):
                    in_island = line.strip() == "still_raw:"
                    if in_island:
                        cfg["source"] = "yaml"
                    continue
                if not in_island or ":" not in line:
                    continue
                k, v = (x.strip() for x in line.split(":", 1))
                v = v.strip('"')
                if k == "format":
                    if v not in ("pjpg", FORMAT):
                        raise ValueError(f"still_raw.format must be pjpg|nrjxl, got {v!r}")
                    cfg["format"] = v
                elif k == "distances":
                    cfg["distances"] = parse_distances(v)
                elif k == "encode_max_s":
                    if not v.isdigit() or not 5 <= int(v) <= 120:
                        raise ValueError(f"still_raw.encode_max_s must be 5..120, got {v!r}")
                    cfg["encode_max_s"] = int(v)
                elif k == "keep_crop":
                    if v.lower() not in ("true", "false"):
                        raise ValueError(f"still_raw.keep_crop must be true|false, got {v!r}")
                    cfg["keep_crop"] = v.lower() == "true"
                elif k == "effort":
                    if not v.isdigit() or not 1 <= int(v) <= 7:
                        raise ValueError(f"still_raw.effort must be 1..7, got {v!r}")
                    cfg["effort"] = int(v)
    except OSError:
        pass
    return cfg


def enabled(cfg):
    return bool(cfg) and cfg.get("format") == FORMAT


# ---------------------------------------------------------------------------
# DNG crop reader (SPEC §3.2): seek + read only the crop rows
# ---------------------------------------------------------------------------

_TIFF_TYPES = {1: ("B", 1), 2: ("c", 1), 3: ("H", 2), 4: ("I", 4), 5: ("II", 8),
               6: ("b", 1), 7: ("B", 1), 8: ("h", 2), 9: ("i", 4), 10: ("ii", 8),
               11: ("f", 4), 12: ("d", 8), 13: ("I", 4), 16: ("Q", 8)}

T_NEWSUBFILETYPE, T_WIDTH, T_HEIGHT, T_BPS, T_COMPRESSION = 254, 256, 257, 258, 259
T_PHOTOMETRIC, T_STRIP_OFFSETS, T_SPP, T_ROWS_PER_STRIP, T_STRIP_COUNTS = 262, 273, 277, 278, 279
T_PLANAR, T_TILE_OFFSETS, T_SUBIFDS = 284, 324, 330
T_CFA_REPEAT, T_CFA_PATTERN = 33421, 33422
T_LINEARIZATION, T_BLACK_REPEAT, T_BLACK, T_WHITE, T_ACTIVE_AREA = 50712, 50713, 50714, 50717, 50829
PHOTOMETRIC_CFA = 32803
_CFA_COLOURS = {0: "R", 1: "G", 2: "B"}


class DngError(ValueError):
    """The DNG layout is not one this reader handles (rfb=dng)."""


def _read_ifd(fh, offset, bo):
    """{tag: tuple(values)} of one IFD (values read eagerly; DNG IFDs are small) and the
    next-IFD offset. Rationals become floats."""
    fh.seek(offset)
    raw = fh.read(2)
    if len(raw) != 2:
        raise DngError(f"IFD at {offset}: truncated")
    (n,) = struct.unpack(bo + "H", raw)
    entries = fh.read(12 * n)
    tail = fh.read(4)
    if len(entries) != 12 * n:
        raise DngError(f"IFD at {offset}: truncated")
    (nxt,) = struct.unpack(bo + "I", tail) if len(tail) == 4 else (0,)
    tags = {}
    for i in range(n):
        tag, typ, count, value = struct.unpack(bo + "HHI4s", entries[12 * i:12 * i + 12])
        if typ not in _TIFF_TYPES:
            continue                           # unknown type: not a tag this reader needs
        fmt, size = _TIFF_TYPES[typ]
        total = size * count
        if total <= 4:
            data = value[:total]
        else:
            (ptr,) = struct.unpack(bo + "I", value)
            here = fh.tell()
            fh.seek(ptr)
            data = fh.read(total)
            fh.seek(here)
        if typ == 2:
            tags[tag] = (data.split(b"\0", 1)[0].decode("ascii", "replace"),)
        elif typ in (5, 10):
            nums = struct.unpack(bo + (fmt[0] * 2 * count), data)
            tags[tag] = tuple((a / b) if b else 0.0 for a, b in zip(nums[0::2], nums[1::2]))
        elif typ in (1, 7):
            tags[tag] = tuple(data)
        else:
            tags[tag] = struct.unpack(bo + fmt * count, data)
    return tags, nxt


def _find_cfa_ifd(fh, bo, first):
    """Walk IFD0 + its chain + SubIFDs for the full-resolution CFA image."""
    seen, todo, found = set(), [first], []
    while todo:
        off = todo.pop(0)
        if not off or off in seen:
            continue
        seen.add(off)
        tags, nxt = _read_ifd(fh, off, bo)
        todo.append(nxt)
        todo.extend(tags.get(T_SUBIFDS, ()))
        if tags.get(T_PHOTOMETRIC, (None,))[0] == PHOTOMETRIC_CFA and \
                tags.get(T_NEWSUBFILETYPE, (0,))[0] == 0:
            found.append(tags)
    if not found:
        raise DngError("no full-resolution CFA image (PhotometricInterpretation 32803)")
    return max(found, key=lambda t: t[T_WIDTH][0] * t[T_HEIGHT][0])


def read_dng_crop(path, crop_xywh):
    """See _read_dng_crop. Any malformed-file error (short reads, bad offsets) is a
    DngError (rfb=dng), never a generic exception (rfb=err)."""
    try:
        return _read_dng_crop(path, crop_xywh)
    except DngError:
        raise
    except (struct.error, IndexError, KeyError, ValueError, OverflowError) as exc:
        raise DngError(f"{path}: malformed DNG ({type(exc).__name__}: {exc})")


def _read_dng_crop(path, crop_xywh):
    """-> dict(mosaic=np.uint16 (h, w), cfa, black, white, bits, native_w, native_h).

    crop_xywh is in NATIVE sensor px (the active area). Reads only the crop rows by
    seek. Fails loudly (DngError) on: compressed or tiled data, a LinearizationTable, a
    non-2x2 or non-RGB CFA, a per-position black level, an odd ActiveArea origin, a
    sample size other than 16 bits, or a crop outside the frame (the rig reader's
    refusals, raw_io.read_dng, plus this reader's own)."""
    import numpy as np

    x, y, w, h = (int(v) for v in crop_xywh)
    if x % 2 or y % 2 or w % 2 or h % 2 or w <= 0 or h <= 0:
        raise DngError(f"crop {crop_xywh} must be even (keeps the CFA phase)")
    with open(path, "rb") as fh:
        head = fh.read(8)
        if head[:2] == b"II":
            bo = "<"
        elif head[:2] == b"MM":
            bo = ">"
        else:
            raise DngError(f"{path}: not a TIFF/DNG (magic {head[:2]!r})")
        magic, first = struct.unpack(bo + "HI", head[2:8])
        if magic != 42:
            raise DngError(f"{path}: TIFF magic {magic} (BigTIFF is not handled)")
        t = _find_cfa_ifd(fh, bo, first)

        if t.get(T_COMPRESSION, (1,))[0] != 1:
            raise DngError(f"compressed CFA data (Compression {t[T_COMPRESSION][0]})")
        if T_TILE_OFFSETS in t:
            raise DngError("tiled CFA data is not handled")
        if T_LINEARIZATION in t:
            raise DngError("LinearizationTable is not handled")
        if t.get(T_SPP, (1,))[0] != 1 or t.get(T_PLANAR, (1,))[0] != 1:
            raise DngError("CFA image must be 1 sample per pixel, contiguous")
        bits = t.get(T_BPS, (0,))[0]
        if bits != 16:
            raise DngError(f"BitsPerSample {bits}: only the 16-bit container is handled")
        if tuple(t.get(T_CFA_REPEAT, ())) != (2, 2):
            raise DngError(f"CFA repeat {t.get(T_CFA_REPEAT)} is not 2x2")
        codes = t.get(T_CFA_PATTERN, ())
        if len(codes) != 4 or any(c not in _CFA_COLOURS for c in codes):
            raise DngError(f"CFAPattern {list(codes)} is not an RGB Bayer pattern")
        cfa = "".join(_CFA_COLOURS[c] for c in codes)
        if cfa not in CFA_PATTERNS:
            raise DngError(f"CFAPattern {cfa} is not a Bayer pattern")

        blacks = [float(b) for b in t.get(T_BLACK, (0.0,))]
        repeat = tuple(t.get(T_BLACK_REPEAT, (1, 1)))
        if not ((repeat == (1, 1) and len(blacks) == 1) or (repeat == (2, 2) and len(blacks) == 4)):
            raise DngError(f"BlackLevel {blacks} with repeat {repeat} is not handled")
        if len(set(blacks)) != 1 or not float(blacks[0]).is_integer():
            raise DngError(f"per-position or fractional BlackLevel {blacks} is not handled")
        black = int(blacks[0])
        white = int(round(float(t.get(T_WHITE, (2 ** bits - 1,))[0])))
        if not 0 <= black < white <= 65535:
            raise DngError(f"black {black} / white {white} out of order")

        img_w, img_h = int(t[T_WIDTH][0]), int(t[T_HEIGHT][0])
        ox = oy = 0
        native_w, native_h = img_w, img_h
        if T_ACTIVE_AREA in t:
            top, left, bottom, right = (int(v) for v in t[T_ACTIVE_AREA])
            if top % 2 or left % 2:
                raise DngError(f"odd ActiveArea origin ({left}, {top}) is not handled")
            ox, oy, native_w, native_h = left, top, right - left, bottom - top
        if x + w > native_w or y + h > native_h:
            raise DngError(f"crop {crop_xywh} does not fit the native frame "
                           f"{native_w}x{native_h}")

        offsets = list(t.get(T_STRIP_OFFSETS, ()))
        counts = list(t.get(T_STRIP_COUNTS, ()))
        rps = int(t.get(T_ROWS_PER_STRIP, (img_h,))[0])
        row_bytes = img_w * 2
        if not offsets or len(offsets) != len(counts):
            raise DngError("CFA image has no strips")
        if any(c < min(rps, img_h - i * rps) * row_bytes for i, c in enumerate(counts)):
            raise DngError("a strip is shorter than its rows (truncated DNG?)")

        dtype = np.dtype(bo + "u2")
        out = np.empty((h, w), dtype=np.uint16)
        for r in range(h):
            row = oy + y + r
            strip, within = divmod(row, rps)
            fh.seek(offsets[strip] + within * row_bytes + (ox + x) * 2)
            data = fh.read(w * 2)
            if len(data) != w * 2:
                raise DngError(f"short read at row {row} (truncated DNG?)")
            out[r] = np.frombuffer(data, dtype=dtype)
    return {"mosaic": out, "cfa": cfa, "black": black, "white": white, "bits": bits,
            "native_w": native_w, "native_h": native_h}


# ---------------------------------------------------------------------------
# planes + curve (SPEC §3.4; study common.split / sqrt_lut)
# ---------------------------------------------------------------------------

def plane_offsets(cfa):
    """{R, G1, G2, B: (dy, dx)}; G1 is the first green in raster order."""
    out, greens = {}, []
    for i, c in enumerate(cfa):
        pos = (i // 2, i % 2)
        if c == "G":
            greens.append(pos)
        else:
            out[c] = pos
    out["G1"], out["G2"] = greens
    return {k: out[k] for k in ("R", "G1", "G2", "B")}


def split_planes(mosaic, cfa):
    import numpy as np
    return {k: np.ascontiguousarray(mosaic[dy::2, dx::2])
            for k, (dy, dx) in plane_offsets(cfa).items()}


def sqrt_lut(black, white, b=CODE_BITS):
    """Integer LUT raw count -> b-bit code: floor(sqrt(clip(raw - black)) * S + 0.5),
    S = (2^b - 1) / sqrt(white - black). Built in float64 once, applied as integers, so
    the backend / rig reproduce it exactly (study common.sqrt_lut, pedestal 0)."""
    import numpy as np
    raw = np.arange(white + 1, dtype=np.float64)
    v = np.clip(raw - black, 0, white - black)
    scale = (2 ** b - 1) / np.sqrt(white - black)
    return np.floor(np.sqrt(v) * scale + 0.5).astype(np.uint16)


def code_planes(crop):
    """{R, G1, G2, B: uint16 12-bit codes} for a read_dng_crop() result."""
    import numpy as np
    lut = sqrt_lut(crop["black"], crop["white"])
    mosaic = np.minimum(crop["mosaic"], crop["white"])
    return {k: lut[p] for k, p in split_planes(mosaic, crop["cfa"]).items()}


def write_pgm(path, plane, maxval=CODE_MAX):
    """Binary P5, 16-bit big-endian (netpbm), maxval 4095 -> cjxl reads 12-bit grey."""
    import numpy as np
    h, w = plane.shape
    with open(path, "wb") as fh:
        fh.write(f"P5\n{w} {h}\n{maxval}\n".encode("ascii"))
        fh.write(np.ascontiguousarray(plane, dtype=">u2").tobytes())


# ---------------------------------------------------------------------------
# NR container v1 (CONTAINER.md §1-4)
# ---------------------------------------------------------------------------

def _uvarint(n):
    if n < 0:
        raise ValueError(f"varint must be >= 0, got {n}")
    out = bytearray()
    while True:
        byte, n = n & 0x7F, n >> 7
        out.append(byte | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _read_uvarint(buf, pos):
    n = shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        n |= (byte & 0x7F) << shift
        shift += 7
        if not byte & 0x80:
            return n, pos


def _zz(v):
    return (v << 1) if v >= 0 else ((-v) << 1) - 1


def _unzz(u):
    return (u >> 1) if not u & 1 else -((u + 1) >> 1)


def scaled(x, s):
    """Round half away from zero (CONTAINER.md §2): sign(x) * floor(|x| * s + 0.5)."""
    mag = int(math.floor(abs(float(x)) * s + 0.5))
    return -mag if x < 0 else mag


def _num(meta, key):
    v = meta.get(key)
    return float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) \
        and math.isfinite(v) else None


def colour_params(meta):
    """params[6..19, 22, 23] from the rpicam --metadata dict of the SAME exposure.
    Required ones missing -> RawFallback('err') (CONTAINER.md §2: never a neutral
    substitute)."""
    exp, again = _num(meta, "ExposureTime"), _num(meta, "AnalogueGain")
    gains = meta.get("ColourGains")
    ccm = meta.get("ColourCorrectionMatrix")
    ok_gains = (isinstance(gains, (list, tuple)) and len(gains) == 2
                and all(isinstance(g, (int, float)) and not isinstance(g, bool)
                        and math.isfinite(g) and g > 0 for g in gains))
    ok_ccm = (isinstance(ccm, (list, tuple)) and len(ccm) == 9
              and all(isinstance(c, (int, float)) and not isinstance(c, bool)
                      and math.isfinite(c) for c in ccm))
    missing = [name for name, ok in (("ExposureTime", exp is not None),
                                     ("AnalogueGain", again is not None),
                                     ("ColourGains", ok_gains),
                                     ("ColourCorrectionMatrix", ok_ccm)) if not ok]
    if missing:
        raise RawFallback("err", f"capture metadata lacks {', '.join(missing)} "
                                 "(no WB/CCM params: CONTAINER.md §2)")
    temp, dgain, ct = (_num(meta, k) for k in ("SensorTemperature", "DigitalGain",
                                               "ColourTemperature"))
    return {
        "exposure_us": scaled(exp, 1), "again_x1000": scaled(again, 1000),
        "gains_x10000": [scaled(g, 10000) for g in gains],
        "ccm_x10000": [scaled(c, 10000) for c in ccm],
        "temp_x10": SENTINEL if temp is None else scaled(temp, 10),
        "dgain_x1000": SENTINEL if dgain is None else scaled(dgain, 1000),
        "ct_k": SENTINEL if ct is None else scaled(ct, 1),
    }


def build_params(*, crop_xywh, native_wh, crc, colour, distance, effort):
    """The 24 profile-v1 params, in CONTAINER.md §2 order (append-only)."""
    x, y = int(crop_xywh[0]), int(crop_xywh[1])
    params = [PROFILE_VERSION, x, y, int(native_wh[0]), int(native_wh[1]), int(crc),
              colour["exposure_us"], colour["again_x1000"], *colour["gains_x10000"],
              *colour["ccm_x10000"], colour["temp_x10"], scaled(distance, 100), int(effort),
              colour["dgain_x1000"], colour["ct_k"]]
    assert len(params) == N_PARAMS_V1, len(params)
    return params


def pack_container(*, w, h, cfa, black, white, params, payloads):
    """Header + R, G1, G2, B payloads (byte-identical to the study's Header.pack for
    method D2 / flags 0x04 / b=12 / pedestal 0)."""
    if len(payloads) != 4:
        raise ValueError("nrjxl carries exactly 4 planes")
    out = bytearray(MAGIC)
    out += bytes([METHOD_D2, FLAGS_4PL_SQRT, CFA_PATTERNS.index(cfa), CODE_BITS])
    for n in (int(w), int(h), int(black), int(white), 0, len(params)):
        out += _uvarint(n)
    for p in params:
        out += _uvarint(_zz(int(p)))
    out += _uvarint(len(payloads))
    for p in payloads:
        out += _uvarint(len(p))
    return bytes(out) + b"".join(payloads)


CRC_PARAM = 5


def container_crc(*, w, h, cfa, black, white, params, payloads):
    """CONTAINER.md §4 (crc-v1b, agreed with the backend 2026-10-02): crc32 over the
    header packed with params[5] = 0 (minimal LEB128) followed by the 4 payloads, so a
    flipped bit in the colour params fails as loudly as one in a plane."""
    zeroed = list(params)
    zeroed[CRC_PARAM] = 0
    return zlib.crc32(pack_container(w=w, h=h, cfa=cfa, black=black, white=white,
                                     params=zeroed, payloads=payloads)) & 0xFFFFFFFF


def seal_container(*, w, h, cfa, black, white, params, payloads):
    """-> (blob, crc): params[5] filled with container_crc, then packed."""
    crc = container_crc(w=w, h=h, cfa=cfa, black=black, white=white, params=params,
                        payloads=payloads)
    sealed = list(params)
    sealed[CRC_PARAM] = crc
    return pack_container(w=w, h=h, cfa=cfa, black=black, white=white, params=sealed,
                          payloads=payloads), crc


def unpack_container(blob):
    """-> (header dict, [payloads]). The camera's own check of what it built (and the
    tests' decoder); refuses trailing / missing bytes and a crc mismatch."""
    if blob[:2] != MAGIC:
        raise ValueError(f"bad magic {blob[:2]!r}")
    method, flags, cfa, b = blob[2], blob[3], blob[4], blob[5]
    pos, vals = 6, []
    for _ in range(6):
        n, pos = _read_uvarint(blob, pos)
        vals.append(n)
    w, h, black, white, pedestal, n_params = vals
    params = []
    for _ in range(n_params):
        u, pos = _read_uvarint(blob, pos)
        params.append(_unzz(u))
    n_len, pos = _read_uvarint(blob, pos)
    lengths = []
    for _ in range(n_len):
        n, pos = _read_uvarint(blob, pos)
        lengths.append(n)
    payloads = []
    for n in lengths:
        payloads.append(blob[pos:pos + n])
        pos += n
    if pos != len(blob):
        raise ValueError(f"{len(blob) - pos} trailing/missing bytes")
    head = {"method": method, "flags": flags, "cfa": CFA_PATTERNS[cfa], "b": b, "w": w, "h": h,
            "black": black, "white": white, "pedestal": pedestal, "params": params,
            "lengths": lengths}
    if len(params) > CRC_PARAM:
        want = container_crc(w=w, h=h, cfa=CFA_PATTERNS[cfa], black=black, white=white,
                             params=params, payloads=payloads)
        if params[CRC_PARAM] != want or pedestal != 0 or n_len != 4:
            raise ValueError("crc32 mismatch (header or payload damaged)")
    return head, payloads


# ---------------------------------------------------------------------------
# the capped encoder child (SPEC §3.5 guards)
# ---------------------------------------------------------------------------

# The guards run in a /bin/sh wrapper that then `exec`s cjxl (same pid, so wait4 reports
# cjxl's own rusage). NOT a Popen preexec_fn: the supervisor has live threads (the command
# reader), and Python code between fork and exec can deadlock there.
#   - oom_score_adj 1000: first in line for the OOM killer (Linux; silently skipped elsewhere)
#   - ulimit -v (RLIMIT_AS, KiB): an overrun fails inside cjxl instead of swapping the Zero
# R0.4 checks on the unit that the cap really kills a 400 MB allocation (hil_s28_r0_probe.sh).
GUARD_SH = ('echo {adj} > /proc/self/oom_score_adj 2>/dev/null; '
            'ulimit -v {kib} 2>/dev/null; exec "$0" "$@"').format(
    adj=ENCODER_OOM_SCORE_ADJ, kib=ENCODER_MEM_LIMIT_BYTES // 1024)


def guarded(cmd):
    """argv -> the same argv under the encoder guards (GUARD_SH)."""
    return ["/bin/sh", "-c", GUARD_SH] + list(cmd)


_MEM_SIGNALS = {signal.SIGKILL, signal.SIGABRT, signal.SIGSEGV, getattr(signal, "SIGBUS", 7)}
_MEM_WORDS = (b"bad_alloc", b"out of memory", b"memory", b"alloc")


def run_capped(cmd, *, timeout_s, stdout_path, stderr_path, poll_s=0.05):
    """Run one encoder child under the guards. -> {rc, kind, seconds, peak_rss_kb}

    kind: "ok" (rc 0), "time" (killed at timeout_s), "mem" (killed by a signal the
    RLIMIT/OOM path raises, or stderr names an allocation failure), "enc" (any other
    non-zero exit, incl. 127 = binary not found). Peak RSS comes from wait4 (ru_maxrss:
    KiB on Linux, bytes on macOS; normalised to KiB). Never raises for a child failure;
    raises OSError only if /bin/sh itself cannot start."""
    import subprocess
    t0 = time.monotonic()
    with open(stdout_path, "wb") as out, open(stderr_path, "wb") as err:
        p = subprocess.Popen(guarded(cmd), stdout=out, stderr=err, stdin=subprocess.DEVNULL,
                             close_fds=True)
        status = rusage = None
        killed = False
        while True:
            pid, st, ru = os.wait4(p.pid, os.WNOHANG)
            if pid:
                status, rusage = st, ru
                break
            if time.monotonic() - t0 >= timeout_s:
                try:
                    p.kill()
                except OSError:
                    pass
                killed = True
                _, status, rusage = os.wait4(p.pid, 0)
                break
            time.sleep(poll_s)
        p.returncode = os.waitstatus_to_exitcode(status)
    seconds = time.monotonic() - t0
    rss = int(rusage.ru_maxrss) if rusage is not None else 0
    peak_kb = rss // 1024 if sys.platform == "darwin" else rss
    rc = p.returncode
    if killed:
        kind = "time"
    elif rc == 0:
        kind = "ok"
    elif rc < 0 and -rc in _MEM_SIGNALS:
        kind = "mem"
    else:
        try:
            with open(stderr_path, "rb") as fh:
                tail = fh.read()[-2000:].lower()
        except OSError:
            tail = b""
        kind = "mem" if any(word in tail for word in _MEM_WORDS) else "enc"
    return {"rc": rc, "kind": kind, "seconds": round(seconds, 3), "peak_rss_kb": peak_kb}


def cjxl_command(cjxl, src, dst, distance, effort):
    """The measured invocation (SPEC §3.5; rig pi_bench.py:196-197): modular, one thread."""
    return [cjxl, str(src), str(dst), "-m", "1", "-e", str(int(effort)),
            "-d", f"{float(distance):.4f}", "--num_threads=0"]


# ---------------------------------------------------------------------------
# rung walk (SPEC §3.5 rate control)
# ---------------------------------------------------------------------------

def message_count(n_bytes, chunk_b64_chars):
    """Chunks a payload of n_bytes needs: base64 length / chunk chars, rounded up
    (the pjpg encoder's own arithmetic, rc_jpeg_encoder.encode_progressive)."""
    b64 = 4 * ((int(n_bytes) + 2) // 3)
    return -(-b64 // int(chunk_b64_chars))


def encode_rung(codes, distance, effort, work_dir, *, cjxl, runner, timeout_s):
    """All 4 planes at one distance. -> (payloads, per-plane runs). Raises RawFallback
    for a child failure (enc / mem / time)."""
    payloads, runs = [], []
    for name in ("R", "G1", "G2", "B"):
        left = timeout_s - sum(r["seconds"] for r in runs)
        if left <= 0:
            raise RawFallback("time", f"encode cap reached before plane {name} at d={distance}")
        src = os.path.join(work_dir, f"{name}.pgm")
        dst = os.path.join(work_dir, f"{name}.jxl")
        if not os.path.exists(src):
            write_pgm(src, codes[name])
        if os.path.exists(dst):
            os.remove(dst)
        try:
            r = runner(cjxl_command(cjxl, src, dst, distance, effort), timeout_s=left,
                       stdout_path=os.path.join(work_dir, f"{name}.cjxl.out"),
                       stderr_path=os.path.join(work_dir, f"{name}.cjxl.err"))
        except OSError as exc:
            raise RawFallback("enc", f"cjxl could not start: {exc}")
        r = dict(r, plane=name)
        runs.append(r)
        if r["kind"] != "ok":
            raise RawFallback(r["kind"], f"cjxl plane {name} d={distance}: {r['kind']} "
                                         f"rc={r['rc']} after {r['seconds']} s")
        try:
            with open(dst, "rb") as fh:
                data = fh.read()
        except OSError:
            data = b""
        if not data:
            raise RawFallback("enc", f"cjxl plane {name} d={distance}: rc 0 but no output")
        payloads.append(data)
    return payloads, runs


def rung_walk(crop, codes, meta_params, cfg, *, crop_xywh, budget, message_cap,
              chunk_b64_chars, reserve_msgs, fallback_msgs, work_dir, cjxl, runner,
              clock=time.monotonic, log=print):
    """Walk still.raw.distances until the blob fits this wake. -> result dict.

    A rung FITS iff (the pjpg selector's rule, rc_quality_selector):
        message_count <= message_cap
        AND budget.messages_fit(message_count + 2 + reserve_msgs)  (START/END + heals)
    Time: each rung's encoders get min(encode_max_s left, the cycle budget left after
    the pjpg fallback's own send: fallback_msgs + 2 + reserve_msgs paced messages), so a
    RAW attempt can never eat the time the fallback JPEG needs (SPEC §6)."""
    t_start = clock()
    attempt_log = []
    pace = float(budget.seconds_per_message)
    fallback_s = (int(fallback_msgs) + 2 + int(reserve_msgs)) * pace
    for distance in cfg["distances"]:
        cap_left = float(cfg["encode_max_s"]) - (clock() - t_start)
        budget_left = budget.remaining_s() - fallback_s
        timeout_s = min(cap_left, budget_left)
        if timeout_s <= 0:
            raise RawFallback("time", f"no time for rung d={distance} (encode cap left "
                                      f"{cap_left:.1f} s, budget left after the fallback "
                                      f"send {budget_left:.1f} s)", attempt_log)
        t_rung = clock()
        try:
            payloads, runs = encode_rung(codes, distance, cfg["effort"], work_dir,
                                         cjxl=cjxl, runner=runner, timeout_s=timeout_s)
        except RawFallback as exc:
            if exc.code == "time" and cap_left > budget_left:
                exc.detail += " (cycle budget)"
            exc.attempt_log = attempt_log
            raise
        params = build_params(crop_xywh=crop_xywh, native_wh=(crop["native_w"], crop["native_h"]),
                              crc=0, colour=meta_params, distance=distance,
                              effort=cfg["effort"])
        blob, crc = seal_container(w=crop["mosaic"].shape[1], h=crop["mosaic"].shape[0],
                                   cfa=crop["cfa"], black=crop["black"], white=crop["white"],
                                   params=params, payloads=payloads)
        params[CRC_PARAM] = crc
        msgs = message_count(len(blob), chunk_b64_chars)
        over_cap = msgs > int(message_cap)
        budget_fit = budget.messages_fit(msgs + 2 + int(reserve_msgs))
        attempt_log.append({
            "distance": distance, "bytes": len(blob), "message_count": msgs,
            "plane_bytes": [len(p) for p in payloads], "over_cap": over_cap,
            "budget_fit": budget_fit, "seconds": round(clock() - t_rung, 3),
            "cjxl_seconds": [r["seconds"] for r in runs],
            "peak_rss_kb": max(r["peak_rss_kb"] for r in runs),
        })
        log(f"[RAW] rung d={distance}: {len(blob)} B, {msgs} msgs, over_cap={over_cap}, "
            f"budget_fit={budget_fit}, {attempt_log[-1]['seconds']:.1f} s, "
            f"peak_rss={attempt_log[-1]['peak_rss_kb']} KiB")
        if not over_cap and budget_fit:
            return {"blob": blob, "distance": distance, "attempts": len(attempt_log),
                    "attempt_log": attempt_log, "message_count": msgs, "crc32": crc,
                    "params": params, "encode_s": round(clock() - t_start, 3)}
    raise RawFallback("fit", f"no rung of {cfg['distances']} fits (cap {message_cap}, "
                             f"reserve {reserve_msgs})", attempt_log)


def encode_still(dng_path, metadata, cfg, *, crop_xywh, budget, message_cap, chunk_b64_chars,
                 reserve_msgs=0, fallback_msgs=0, work_dir, cjxl=None, runner=run_capped,
                 keep_crop_path=None, clock=time.monotonic, log=print):
    """Path [B] for one wake: DNG crop -> planes -> rung walk -> NR blob.
    -> result dict (rung_walk + timings). Raises RawFallback(code) for EVERY failure
    (an unexpected exception becomes rfb=err), never anything else."""
    timings = {}
    try:
        if cjxl is None:
            cjxl = shutil.which("cjxl")
        if not cjxl:
            raise RawFallback("enc", "cjxl not found on PATH")
        x, y, w, h = (int(v) for v in crop_xywh)
        if w * h > RAW_MAX_PX:
            raise RawFallback("err", f"crop {w}x{h} exceeds RAW_MAX_PX {RAW_MAX_PX} "
                                     "(config_validate should have refused it)")
        meta_params = colour_params(metadata or {})
        t0 = clock()
        try:
            crop = read_dng_crop(dng_path, crop_xywh)
        except DngError as exc:
            raise RawFallback("dng", str(exc))
        timings["dng_read_s"] = round(clock() - t0, 3)
        t1 = clock()
        codes = code_planes(crop)
        timings["planes_s"] = round(clock() - t1, 3)
        os.makedirs(work_dir, exist_ok=True)
        if keep_crop_path:
            # still.raw.keep_crop (O1 paired analysis): the crop mosaic itself, 16-bit
            # PGM at the sensor's white level, native px.
            write_pgm(keep_crop_path, crop["mosaic"], maxval=crop["white"])
        result = rung_walk(crop, codes, meta_params, cfg, crop_xywh=crop_xywh, budget=budget,
                           message_cap=message_cap, chunk_b64_chars=chunk_b64_chars,
                           reserve_msgs=reserve_msgs, fallback_msgs=fallback_msgs,
                           work_dir=work_dir, cjxl=cjxl, runner=runner, clock=clock, log=log)
        unpack_container(result["blob"])        # our own bytes must read back (crc too)
        result["timings"] = timings
        result["cfa"], result["black"], result["white"] = crop["cfa"], crop["black"], crop["white"]
        return result
    except RawFallback as exc:
        exc.timings.update(timings)
        raise
    except Exception as exc:                    # numpy missing, ENOSPC, anything: rfb=err
        raise RawFallback("err", f"{type(exc).__name__}: {exc}", timings=timings)


# ---------------------------------------------------------------------------
# files: work dir, orphan sweep (SPEC §3.1)
# ---------------------------------------------------------------------------

def dng_path_for(native_jpeg_path):
    """rpicam-still --raw names the DNG after -o with the extension replaced."""
    return os.path.splitext(native_jpeg_path)[0] + ".dng"


def work_dir_for(output_dir, image_stem):
    return os.path.join(output_dir, WORK_PREFIX + image_stem)


def remove_quietly(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path, ignore_errors=True)
        elif os.path.exists(path):
            os.remove(path)
    except OSError:
        pass


def sweep_orphans(output_dir, log=print):
    """Delete RAW work files a crash or power cut left behind: every `*.dng` and every
    `.nrjxl_work_*` dir in the images dir (a live action creates its own AFTER this
    runs). Kept crops (`*_raw_crop.pgm`, still.raw.keep_crop) are products, not work
    files: the storage guard prunes them. Silent when there is nothing to remove (a
    pjpg unit's log is unchanged). -> count removed."""
    try:
        names = os.listdir(output_dir)
    except OSError:
        return 0
    n, freed = 0, 0
    for name in sorted(names):
        path = os.path.join(output_dir, name)
        if name.endswith(".dng") or (name.startswith(WORK_PREFIX) and os.path.isdir(path)):
            try:
                if os.path.isdir(path):
                    freed += sum(os.path.getsize(os.path.join(path, f)) for f in os.listdir(path))
                else:
                    freed += os.path.getsize(path)
            except OSError:
                pass
            remove_quietly(path)
            n += 1
    if n:
        log(f"[RAW] orphan sweep: removed {n} RAW work file(s) ({freed} B) from {output_dir}")
    return n


# ---------------------------------------------------------------------------
# CLI: run path [B] on a DNG on the desk or the unit (R0.3 uses it)
# ---------------------------------------------------------------------------

class _FixedBudget:
    """A CycleBudget stand-in for the CLI: lots of time, the given pacing."""

    def __init__(self, seconds=3600.0, pace=1.3):
        self.seconds_per_message = pace
        self._end = time.monotonic() + seconds

    def remaining_s(self):
        return max(0.0, self._end - time.monotonic())

    def messages_fit(self, n):
        return self.remaining_s() >= n * self.seconds_per_message


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description="Sprint28 path [B] on one DNG (no BM bus).")
    ap.add_argument("--dng", required=True)
    ap.add_argument("--metadata", required=True, help="rpicam --metadata JSON of the exposure")
    ap.add_argument("--crop", default="1504,846,1600,900", help="x,y,w,h native px (even)")
    ap.add_argument("--distances", default=",".join(str(d) for d in DEFAULT_CONFIG["distances"]))
    ap.add_argument("--effort", type=int, default=DEFAULT_CONFIG["effort"])
    ap.add_argument("--encode-max-s", type=int, default=120)
    ap.add_argument("--message-cap", type=int, default=195)
    ap.add_argument("--chunk-chars", type=int, default=384)
    ap.add_argument("--allow-any-crop", action="store_true",
                    help="R0.3 only: lift RAW_MAX_PX for the opt-in preset measurements")
    ap.add_argument("--out", required=True)
    args = ap.parse_args(argv)
    global RAW_MAX_PX
    if args.allow_any_crop:
        RAW_MAX_PX = 4608 * 2592
    crop = [int(v) for v in args.crop.split(",")]
    cfg = dict(DEFAULT_CONFIG, distances=parse_distances(args.distances), effort=args.effort,
               encode_max_s=args.encode_max_s)
    with open(args.metadata, "r", encoding="utf-8") as fh:
        meta = json.load(fh)
    os.makedirs(args.out, exist_ok=True)
    work = os.path.join(args.out, "work")
    print(f"[RAW] host={os.uname().nodename} dng={args.dng} crop={crop} cfg={cfg}")
    try:
        res = encode_still(args.dng, meta, cfg, crop_xywh=crop, budget=_FixedBudget(),
                           message_cap=args.message_cap, chunk_b64_chars=args.chunk_chars,
                           work_dir=work)
    except RawFallback as exc:
        print(f"[RAW][FALLBACK] {exc}")
        with open(os.path.join(args.out, "result.json"), "w") as fh:
            json.dump({"rfb": exc.code, "detail": exc.detail, "attempt_log": exc.attempt_log,
                       "timings": exc.timings}, fh, indent=1)
        return 1
    blob_path = os.path.join(args.out, "image" + CONTENT_SUFFIX)
    with open(blob_path, "wb") as fh:
        fh.write(res["blob"])
    summary = {k: v for k, v in res.items() if k != "blob"}
    with open(os.path.join(args.out, "result.json"), "w") as fh:
        json.dump(summary, fh, indent=1)
    print(f"[RAW] wrote {blob_path} ({len(res['blob'])} B, d={res['distance']}, "
          f"att={res['attempts']}, {res['encode_s']} s)")
    remove_quietly(work)
    return 0


if __name__ == "__main__":
    sys.exit(main())

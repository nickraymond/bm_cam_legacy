#!/usr/bin/env python3
# filename: make_fixtures.py
# description: Sprint28 — build the committed test fixtures: a small real-data DNG (tifffile writer) and 3 production nrjxl blobs from the study DNGs.
"""
Builds everything under tests/fixtures/s28/ (run once on the Mac; outputs are committed):

  mini.dng / mini.json   a 160x96 crop of a real IMX708 study DNG, re-written with
                         tifffile in rpicam's layout (IFD0 = 8-bit RGB thumbnail,
                         SubIFD = 16-bit CFA, BGGR, BlackLevel 64 x4 at a 2x2 repeat,
                         WhiteLevel 1023), plus the expected crops of 3 windows read by
                         tifffile (an INDEPENDENT reader): the parity oracle for
                         rc_raw_jxl.read_dng_crop.
  mini_meta.json         the rpicam --metadata JSON of the same exposure.
  blob_<frame>.nrjxl     production blobs: rc_raw_jxl.encode_still on the study DNGs at
                         still.crop [1504, 846, 1600, 900], default rungs, cap 195,
                         384 chars/chunk (the deployed units), Mac libjxl.
  blobs.json             per blob: sha256, bytes, distance, source DNG sha256, crop,
                         sha256 of the source crop mosaic (uint16 LE), cjxl version.
                         The backend copies the blobs by hash (CONTAINER.md §8).

Inputs:  --data  rig data/s4_20260930 (study DNGs + their rpicam JSONs; read-only).
Example: .venv-dev/bin/python tests/fixtures/s28/make_fixtures.py \\
             --data ~/Documents/GitHub/nereus-camera-test-rig/data/s4_20260930
Known limitations: blob bytes depend on the libjxl build (recorded); tests decode and
check them, they never re-encode and compare bytes.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
sys.path.insert(0, os.path.join(REPO, "BM_Devel_Pi"))

import numpy as np  # noqa: E402
import tifffile  # noqa: E402

import rc_raw_jxl as R  # noqa: E402

MINI_SRC = ("cool_imx708", "stop_-1_r0")
MINI_ORIGIN = (2400, 1300)          # native px of the mini frame's top-left in the source
MINI_WH = (160, 96)
MINI_WINDOWS = {"full": [0, 0, 160, 96], "inner": [10, 6, 64, 40], "corner": [96, 56, 64, 40]}
BLOB_FRAMES = (("cool_imx708", "stop_-1_r0"), ("warm_imx708", "stop_-1_r0"),
               ("cool_imx708", "stop_+0_r0"))
CROP = [1504, 846, 1600, 900]


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_cfa(path):
    with tifffile.TiffFile(path) as tf:
        return tf.pages[0].pages[0].asarray()


def write_mini_dng(path, mosaic):
    """rpicam's DNG layout, written by tifffile (not by the reader under test)."""
    thumb = np.zeros((8, 8, 3), dtype=np.uint8)
    with tifffile.TiffWriter(path, byteorder="<") as tw:
        tw.write(thumb, photometric="rgb", subfiletype=1, subifds=1, metadata=None,
                 extratags=[(50706, 1, 4, b"\x01\x01\x00\x00", True),
                            (271, 2, 0, "Raspberry Pi", True),
                            (272, 2, 0, "imx708_wide", True)])
        tw.write(mosaic.astype("<u2"), photometric=32803, compression=None, metadata=None,
                 extratags=[(33421, 3, 2, (2, 2), True),
                            (33422, 1, 4, b"\x02\x01\x01\x00", True),
                            (50713, 3, 2, (2, 2), True),
                            (50714, 5, 4, (64, 1, 64, 1, 64, 1, 64, 1), True),
                            (50717, 4, 1, 1023, True)])


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--data", required=True)
    args = ap.parse_args(argv)
    cjxl = shutil.which("cjxl")
    ver = subprocess.run([cjxl, "--version"], capture_output=True, text=True).stdout.splitlines()[0]

    # ---- mini DNG + tifffile oracle --------------------------------------
    src = os.path.join(args.data, MINI_SRC[0], MINI_SRC[1] + ".dng")
    full = read_cfa(src)
    x0, y0 = MINI_ORIGIN
    w, h = MINI_WH
    mini = np.ascontiguousarray(full[y0:y0 + h, x0:x0 + w])
    dng = os.path.join(HERE, "mini.dng")
    write_mini_dng(dng, mini)
    back = read_cfa(dng)
    assert np.array_equal(back, mini)
    oracle = {}
    for name, (x, y, cw, ch) in MINI_WINDOWS.items():
        crop = np.ascontiguousarray(back[y:y + ch, x:x + cw]).astype("<u2")
        oracle[name] = {"crop": [x, y, cw, ch], "sha256_u16le": sha(crop.tobytes()),
                        "mean": float(crop.mean())}
    shutil.copyfile(os.path.join(args.data, MINI_SRC[0], MINI_SRC[1] + ".json"),
                    os.path.join(HERE, "mini_meta.json"))
    with open(os.path.join(HERE, "mini.json"), "w") as fh:
        json.dump({"source": f"{MINI_SRC[0]}/{MINI_SRC[1]}.dng", "source_sha256": sha(open(src, "rb").read()),
                   "origin_native_px": MINI_ORIGIN, "wh": MINI_WH, "cfa": "BGGR", "black": 64,
                   "white": 1023, "windows": oracle, "writer": f"tifffile {tifffile.__version__}"},
                  fh, indent=1)
    print(f"[FIX] mini.dng {os.path.getsize(dng)} B, windows {list(oracle)}")

    # ---- production blobs ------------------------------------------------
    # The committed blobs were built with the fixed rungs (pre byte-target search); pin that
    # so a re-run reproduces them byte for byte (the backend copied them by sha256).
    cfg = dict(R.DEFAULT_CONFIG, format="nrjxl", distances=[3.8, 4.6, 5.95, 8.25],
               target_fill=0.0)
    blobs = {}
    for folder, stem in BLOB_FRAMES:
        dpath = os.path.join(args.data, folder, stem + ".dng")
        with open(os.path.join(args.data, folder, stem + ".json")) as fh:
            meta = json.load(fh)
        with tempfile.TemporaryDirectory(prefix="s28fix_") as work:
            res = R.encode_still(dpath, meta, cfg, crop_xywh=CROP, budget=R._FixedBudget(),
                                 message_cap=195, chunk_b64_chars=384, work_dir=work, log=print)
        name = f"blob_{folder.split('_')[0]}_{stem}.nrjxl"
        with open(os.path.join(HERE, name), "wb") as fh:
            fh.write(res["blob"])
        crop = np.ascontiguousarray(read_cfa(dpath)[CROP[1]:CROP[1] + CROP[3],
                                                    CROP[0]:CROP[0] + CROP[2]]).astype("<u2")
        blobs[name] = {"sha256": sha(res["blob"]), "bytes": len(res["blob"]),
                       "distance": res["distance"], "attempts": res["attempts"],
                       "message_count_384": res["message_count"], "crop": CROP,
                       "source": f"{folder}/{stem}.dng", "source_sha256": sha(open(dpath, "rb").read()),
                       "source_metadata": f"{folder}/{stem}.json",
                       "source_crop_sha256_u16le": sha(crop.tobytes()), "params": res["params"]}
        print(f"[FIX] {name}: {len(res['blob'])} B d={res['distance']}")
    with open(os.path.join(HERE, "blobs.json"), "w") as fh:
        json.dump({"cjxl": ver, "effort": cfg["effort"], "distances": cfg["distances"],
                   "container": "CONTAINER.md v1 (crc-v1b)", "blobs": blobs}, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())

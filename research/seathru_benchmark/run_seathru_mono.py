#!/usr/bin/env python3
"""
Sea-thru single-image benchmark runner — RESEARCH ONLY (see README.md).

Orchestrates: clone hainh/sea-thru (MIT) into a scratch bench dir, build an
isolated venv, compute depth with Depth Anything V2 Small (Apache-2.0), map it
to meters with a crude linear ramp, run seathru.run_pipeline, save outputs.

Two-stage design: this script is run with the SYSTEM python for --setup (it
only needs stdlib), then re-executes itself inside the bench venv for the
actual processing (which needs torch/transformers/scipy/skimage).

Outputs per image: <stem>_seathru.png, <stem>_depth.png, <stem>_params.json

Known limitations: relative depth ramp (meters are not real); sea-thru's
LSAC illumination step is slow (minutes per megapixel image).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

SEATHRU_GIT = "https://github.com/hainh/sea-thru"
BENCH_DEPS = ["numpy", "scipy", "scikit-learn", "scikit-image", "pillow",
              "matplotlib", "rawpy", "opencv-python", "torch", "transformers"]
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


def setup(bench: Path) -> None:
    bench.mkdir(parents=True, exist_ok=True)
    st = bench / "sea-thru"
    if not st.exists():
        print(f"cloning {SEATHRU_GIT} -> {st}")
        subprocess.run(["git", "clone", "--depth", "1", SEATHRU_GIT, str(st)], check=True)
    venv = bench / "venv"
    if not venv.exists():
        print(f"creating venv -> {venv}")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    print("installing deps (torch is large; this can take a few minutes)...")
    subprocess.run([str(venv / "bin" / "pip"), "install", "-q", *BENCH_DEPS], check=True)
    print("setup complete")


def run_in_venv(args) -> None:
    """Re-exec this script inside the bench venv for the heavy lifting."""
    py = Path(args.bench_dir).expanduser() / "venv" / "bin" / "python"
    if not py.exists():
        raise SystemExit(f"bench venv missing ({py}); run --setup first")
    cmd = [str(py), __file__, "--stage2", "--bench-dir", args.bench_dir,
           "--out-dir", args.out_dir, "--z-near", str(args.z_near),
           "--z-far", str(args.z_far), "--p", str(args.p), "--f", str(args.f),
           "--l", str(args.l)]
    for img in args.image:
        cmd += ["--image", img]
    raise SystemExit(subprocess.run(cmd).returncode)


def stage2(args) -> None:
    import numpy as np
    from PIL import Image

    bench = Path(args.bench_dir).expanduser()
    sys.path.insert(0, str(bench / "sea-thru"))
    # seathru.py hard-codes matplotlib.use('TkAgg'); neutralize for headless runs.
    import matplotlib
    _orig_use = matplotlib.use
    matplotlib.use = lambda *a, **k: None
    import seathru  # noqa: E402  (external MIT checkout, not repo code)
    matplotlib.use = _orig_use

    print(f"loading depth model {DEPTH_MODEL}...")
    from transformers import pipeline as hf_pipeline
    depth_pipe = hf_pipeline("depth-estimation", model=DEPTH_MODEL)

    out_dir = Path(args.out_dir).expanduser()
    out_dir.mkdir(parents=True, exist_ok=True)

    for img_path in args.image:
        img_path = Path(img_path).expanduser()
        print(f"\n=== {img_path.name} ===")
        pil_img = Image.open(img_path).convert("RGB")
        img01 = np.asarray(pil_img, dtype=np.float64) / 255.0

        print("  estimating depth (Depth Anything V2 Small)...")
        pred = depth_pipe(pil_img)["predicted_depth"].squeeze().numpy()
        # DA-V2 outputs disparity-like relative depth: larger = closer.
        disp = np.asarray(Image.fromarray(pred).resize(pil_img.size, Image.BILINEAR))
        disp_n = (disp - disp.min()) / max(disp.max() - disp.min(), 1e-9)
        depths = args.z_near + (args.z_far - args.z_near) * (1.0 - disp_n)
        print(f"  depth ramp: z in [{depths.min():.2f}, {depths.max():.2f}] m "
              f"(--z-near/--z-far assumption, NOT calibrated)")

        Image.fromarray((disp_n * 255).astype(np.uint8)).save(
            out_dir / f"{img_path.stem}_depth.png")

        ns = argparse.Namespace(p=args.p, f=args.f, l=args.l, min_depth=0.0,
                                max_depth=1.0, spread_data_fraction=0.05,
                                output_graphs=False)
        print("  running sea-thru pipeline (slow: LSAC illumination)...")
        recovered = seathru.run_pipeline(img01, depths.astype(np.float64), ns)
        out_png = out_dir / f"{img_path.stem}_seathru.png"
        Image.fromarray(np.clip(np.rint(recovered * 255.0), 0, 255)
                        .astype(np.uint8)).save(out_png)
        (out_dir / f"{img_path.stem}_params.json").write_text(json.dumps({
            "method": "seathru_mono (hainh/sea-thru + Depth Anything V2 Small)",
            "research_only": True,
            "image": str(img_path), "output": str(out_png),
            "depth_model": DEPTH_MODEL,
            "z_near": args.z_near, "z_far": args.z_far,
            "p": args.p, "f": args.f, "l": args.l,
        }, indent=2), encoding="utf-8")
        print(f"  saved {out_png}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--bench-dir", required=True,
                    help="scratch dir for the sea-thru checkout + venv (outside the repo)")
    ap.add_argument("--setup", action="store_true", help="clone + build venv only")
    ap.add_argument("--stage2", action="store_true", help=argparse.SUPPRESS)
    ap.add_argument("--image", action="append", default=[])
    ap.add_argument("--out-dir", default="seathru_bench_out")
    ap.add_argument("--z-near", type=float, default=0.5)
    ap.add_argument("--z-far", type=float, default=4.0)
    ap.add_argument("--p", type=float, default=0.01)
    ap.add_argument("--f", type=float, default=2.0)
    ap.add_argument("--l", type=float, default=0.5)
    args = ap.parse_args()

    if args.setup:
        setup(Path(args.bench_dir).expanduser())
    elif args.stage2:
        stage2(args)
    elif args.image:
        run_in_venv(args)
    else:
        ap.error("nothing to do: pass --setup and/or --image")


if __name__ == "__main__":
    main()

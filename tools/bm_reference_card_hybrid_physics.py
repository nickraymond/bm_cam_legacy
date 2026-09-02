#!/usr/bin/env python3
"""
Card-anchored physics color correction (hybrid, research brief Option C).

Purpose:
  Range-aware underwater correction where EVERY parameter of the image
  formation model is estimated from things we can see and trust: the Reef
  Reference Card V2 (known reflectances at one known image location) and a
  monocular relative depth map (Depth Anything V2 Small, Apache-2.0). This is
  the commercial-path candidate: our own code implementing the published
  Akkaynak-Treibitz model, with the card — not Sea-thru's dark-pixel
  statistics — supplying the coefficients. (Patent review before productizing
  is still noted in docs/underwater_color_correction_research_202609.md §4b.)

Model (per channel c, linear RGB):
  I_c(x) = J_c(x) * exp(-beta_D_c * z(x))  +  B_inf_c * (1 - exp(-beta_B_c * z(x)))

Estimation (all card/scene anchored):
  z map     : DA-V2 Small relative disparity -> z = a + b*(1 - disp_n),
              anchored so z(card) = --z-card and z(nearest pixel) =
              --near-ratio * z-card. beta*z products make the correction
              invariant to the absolute scale of z, so uncalibrated units are
              fine as long as the card sits correctly in the map.
  B_inf     : median color of the farthest --far-quantile of pixels
              (open water column).
  beta_B    : from the card's black patch (its direct signal ~0, so what we
              see there is backscatter at z_card).
  beta_D    : from the gray patches (known reflectance at z_card) after
              backscatter subtraction. Assumed z-constant (a single card
              distance cannot measure beta_D(z); known limitation).
  recovery  : J = (I - B) * exp(beta_D * z), channel boost capped at
              --max-boost to bound far-field red noise.
  polish    : optional root_poly2 fit on the RECOVERED card patches
              (default on) to absorb residual error.

Install (beyond the card-analysis stack):
  python3 -m pip install torch transformers   # depth model, ~2 GB
  (guarded import; everything else is the existing opencv/numpy/Pillow stack)

Example:
  <bench venv python> tools/bm_reference_card_hybrid_physics.py \
    --images ~/Downloads/SPOT-...Z.jpg --output-dir runs/hybrid_physics_20260901

Outputs per image: <stem>_hybrid.png (+ _nopolish.png), depth png,
params.json with every fitted coefficient; summary.json for the run.
Score results with tools/bm_reference_card_score_external.py.

Known limitations:
  - Relative depth from a land-trained model; shape errors propagate.
  - beta_D assumed constant in z (needs card at 2+ distances to do better).
  - 8-bit JPEG input: below ~5% white-patch red the recovery is noise-bound.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

TOOLS_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS_DIR))

import reference_card_color_utils as ccu  # noqa: E402

DEFAULT_TEMPLATE_DIR = TOOLS_DIR / "reference_card_color_correction" / "reference_card_template_v2"
CANONICAL_W, CANONICAL_H = 3000, 1000
DEPTH_MODEL = "depth-anything/Depth-Anything-V2-Small-hf"


def load_quality_module():
    path = TOOLS_DIR / "bm_reference_card_quality_v2.py"
    spec = importlib.util.spec_from_file_location("bm_reference_card_quality_v2", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def get_depth_pipe():
    try:
        import torch
        from transformers import AutoImageProcessor, AutoModelForDepthEstimation
    except ImportError:
        raise SystemExit("transformers/torch missing — install with:\n"
                         "  python3 -m pip install torch transformers")
    print(f"loading depth model {DEPTH_MODEL}...")
    proc = AutoImageProcessor.from_pretrained(DEPTH_MODEL)
    model = AutoModelForDepthEstimation.from_pretrained(DEPTH_MODEL).eval()
    return (proc, model, torch)


def relative_depth(depth_pipe, pil_img: Image.Image, scale: float = 2.0,
                   refine: bool = True) -> np.ndarray:
    """Normalized disparity in [0,1]; larger = closer to the camera.

    scale: internal inference resolution as a multiple of the image size
    (DA-V2's processor otherwise shrinks everything to ~518px — the ceiling
    test in runs/depth_ceiling_20260901 showed 2x resolves individual coral
    lobes). refine: image-guided filter snaps depth edges to image edges.
    """
    proc, model, torch = depth_pipe
    w = min(int(pil_img.width * scale), 2048)
    h = int(pil_img.height * w / pil_img.width)
    proc.size = {"height": (h // 14) * 14, "width": (w // 14) * 14}
    with torch.no_grad():
        pred = model(**proc(images=pil_img, return_tensors="pt")
                     ).predicted_depth.squeeze().numpy()
    disp = cv2.resize(pred, pil_img.size, interpolation=cv2.INTER_LINEAR)
    disp = (disp - disp.min()) / max(disp.max() - disp.min(), 1e-9)
    if refine:
        lum = ccu.rel_luminance_linear(
            ccu.srgb_to_linear(np.asarray(pil_img, dtype=np.float64) / 255.0))
        disp = np.clip(guided_filter(lum, disp[..., None], radius=6,
                                     eps=1e-4)[..., 0], 0.0, 1.0)
    return disp


def apply_ground_plane(z: np.ndarray, card_quad: np.ndarray, z_card: float,
                       camera_height_m: float, hfov_deg: float) -> tuple[np.ndarray, dict]:
    """Cap depths with the measured sand plane (camera height above seafloor).

    Geometry: a flat seafloor seen from height H puts the ground at range
    r(y) = H / sin(theta(y)) for each image row below the horizon. Camera
    pitch is solved from one known plane point — the card's base row at
    z_card. Anything rendered at a row below the horizon occludes the ground
    there, so z(x, y) <= r(y): a per-row CAP that overrides the depth model's
    known failure (sunlit sand read as far). f comes from the assumed
    in-water HFOV; both assumptions are recorded in the run params.
    """
    h, w = z.shape
    f_px = (w / 2.0) / np.tan(np.radians(hfov_deg) / 2.0)
    cy = h / 2.0
    y_card = float(np.max(np.asarray(card_quad)[:, 1]))       # card base row
    theta_card = np.arcsin(np.clip(camera_height_m / z_card, -1, 1))
    pitch = theta_card - np.arctan((y_card - cy) / f_px)
    rows = np.arange(h, dtype=np.float64)
    theta = pitch + np.arctan((rows - cy) / f_px)
    z_ground = np.full(h, np.inf)
    below = theta > np.radians(2.0)                            # avoid horizon blowup
    z_ground[below] = camera_height_m / np.sin(theta[below])
    z_capped = np.minimum(z, z_ground[:, None])
    return z_capped, {
        "pitch_deg": round(float(np.degrees(pitch)), 2),
        "f_px_assumed": round(float(f_px), 1),
        "hfov_deg_assumed": hfov_deg,
        "ground_rows_capped": int(np.sum(z > z_ground[:, None])),
        "z_ground_bottom_row": round(float(z_ground[below][-1]), 3) if below.any() else None,
    }


def anchor_depth(disp_n: np.ndarray, card_quad: np.ndarray, z_card: float,
                 near_ratio: float) -> tuple[np.ndarray, dict]:
    """z = a + b*(1-disp_n), with z at the card = z_card and z at the nearest
    pixel = near_ratio * z_card. Returns (z map, info)."""
    mask = np.zeros(disp_n.shape, dtype=np.uint8)
    cv2.fillPoly(mask, [np.asarray(card_quad, dtype=np.int32)], 1)
    d_card = float(np.median(disp_n[mask == 1]))
    z_near = near_ratio * z_card
    denom = max(1.0 - d_card, 1e-3)
    b = (z_card - z_near) / denom
    z = z_near + b * (1.0 - disp_n)
    return z, {"disp_at_card": round(d_card, 4), "z_near": z_near,
               "z_at_card": z_card, "z_max": round(float(z.max()), 3)}


def solve_physics(img_lin: np.ndarray, z: np.ndarray, samples, z_card: float,
                  far_quantile: float) -> dict:
    """Estimate B_inf, beta_B, beta_D from the scene + card patches (linear RGB)."""
    flat = img_lin.reshape(-1, 3)
    far = z.reshape(-1) >= np.quantile(z, far_quantile)
    B_inf = np.median(flat[far], axis=0)                       # open-water color

    by_id = {s.patch_id: s for s in samples}
    black = ccu.srgb_to_linear(by_id["gray_black"].median_srgb / 255.0)
    # Black patch: direct signal ~0, so observed = B_inf*(1-exp(-beta_B*z_card)).
    ratio = np.clip(black / np.maximum(B_inf, 1e-6), 0.0, 0.95)
    beta_B = -np.log(1.0 - ratio) / z_card

    grays = [s for s in samples if s.use_for_gray_balance]
    obs = ccu.srgb_to_linear(np.array([s.median_srgb for s in grays]) / 255.0)
    tgt = ccu.srgb_to_linear(np.array([s.target_srgb for s in grays]) / 255.0)
    direct = np.maximum(obs - B_inf * (1.0 - np.exp(-beta_B * z_card)), 1e-6)
    # direct = J * exp(-beta_D*z_card)  ->  beta_D from the mean ratio.
    with np.errstate(divide="ignore"):
        beta_D = -np.log(np.clip((direct / tgt).mean(axis=0), 1e-6, 1.0)) / z_card

    return {"B_inf": B_inf, "beta_B": beta_B, "beta_D": beta_D,
            "far_pixel_count": int(far.sum())}


def guided_filter(guide: np.ndarray, src: np.ndarray, radius: int,
                  eps: float) -> np.ndarray:
    """He et al. 2010 guided filter (in-house, numpy/cv2 box filters).

    Smooths src while following edges in guide. With the DEPTH MAP as guide
    the result is piecewise-smooth per depth region — an illumination map
    that does not bleed water-column haze across coral silhouettes.
    """
    ksize = (2 * radius + 1, 2 * radius + 1)

    def box(x):
        return cv2.boxFilter(x.astype(np.float32), -1, ksize).astype(np.float64)

    out = np.empty_like(src)
    mean_I = box(guide)
    var_I = box(guide * guide) - mean_I ** 2
    for c in range(src.shape[-1]):
        p = src[..., c]
        mean_p = box(p)
        cov_Ip = box(guide * p) - mean_I * mean_p
        a = cov_Ip / (var_I + eps)
        b = mean_p - a * mean_I
        out[..., c] = box(a) * guide + box(b)
    return out


def lsac_recover(img_lin: np.ndarray, z: np.ndarray, phys: dict,
                 sigma_frac: float, lsac_filter: str = "gaussian",
                 bs_guard: float = 0.8, lsac_mode: str = "chroma") -> np.ndarray:
    """Backscatter subtraction + LSAC illumination normalization.

    LSAC (Local Space Average Color, Ebner; the illuminant estimate Sea-thru
    uses) approximated by a wide Gaussian of the direct signal: dividing each
    channel by its own local average is a spatially varying gray-world, so
    every channel — including the nearly-dead red — is normalized to O(1)
    locally instead of being globally exponentiated. This is what makes
    sea-thru renders look natural; published method, implemented in-house.
    """
    z3 = z[..., None]
    B_model = phys["B_inf"] * (1.0 - np.exp(-phys["beta_B"] * z3))
    # Never subtract more than 80% of a pixel's own signal: the black-patch
    # beta_B overshoots (print black reflects ~5% and camera AWB inflates it),
    # and unguarded subtraction annihilates G/B on every shaded coral, leaving
    # red-only pixels. Physically, observed >= backscatter always.
    direct = img_lin - np.minimum(B_model, bs_guard * img_lin)
    sigma = max(sigma_frac * img_lin.shape[1], 8.0)
    if lsac_filter in ("guided", "guided_luma"):
        if lsac_filter == "guided":
            # Depth guide: honest depth-aware neighborhoods, but DA-V2's map
            # is blocky at this resolution and the blocks become halos.
            guide = (z - z.min()) / max(z.max() - z.min(), 1e-6)
            eps = 1e-3
        else:
            # Luminance guide: edges from the image itself (clean), still
            # stops illumination bleeding across coral silhouettes.
            guide = ccu.rel_luminance_linear(direct)
            eps = float(np.var(guide)) * 0.5 + 1e-6
        illum = np.maximum(guided_filter(guide, direct, radius=int(sigma), eps=eps), 0.0)
    else:
        illum = cv2.GaussianBlur(direct.astype(np.float32), (0, 0), sigma).astype(np.float64)
    if lsac_mode == "chroma":
        # Chroma normalization (default): remove only the SPATIAL variation of
        # the color cast; keep global balance for the card WB/stretch to
        # handle exactly once. out = direct * m_c * L_illum / illum, where m_c
        # is the illumination's global mean chroma. BUG HISTORY: without m_c
        # this forces every neighborhood to average NEUTRAL — a local
        # gray-world hiding a ~19x red gain (v6: yellows/browns destroyed,
        # Nick 2026-09-01). With m_c, regions matching the global cast pass
        # through untouched; shadows keep their captured luminance.
        illum_lum = ccu.rel_luminance_linear(illum)[..., None]
        m = illum.reshape(-1, 3).mean(axis=0)
        m = m / max(float(ccu.rel_luminance_linear(m)), 1e-6)
        return np.clip(direct * m * illum_lum / np.maximum(illum, 1e-4), 0.0, 4.0)
    ratio = direct / np.maximum(illum, 1e-4)
    # Flatten mode (v2-v5 behavior): restore each channel's GLOBAL level —
    # pure LSAC lifts even the dead red channel to mid-gray locally; the
    # global channel mean keeps red at its true low weight.
    chan_scale = direct.reshape(-1, 3).mean(axis=0)
    return np.clip(ratio * chan_scale, 0.0, 4.0)


def finish(recovered_lin: np.ndarray, card_quad, qm, layout, patch_inset: float,
           red_wb_cap: float = 1.5, stretch: str = "perchannel",
           sharpen: float = 0.6, black_point: float = 0.02,
           card_wb: bool = True) -> tuple[np.ndarray, dict]:
    """Finishing, card-anchored (all BSD/our code, standard photography ops):

    1. White balance from the card's WHITE patch measured in the recovered
       image — per-channel gains toward its design value, red capped at
       red_wb_cap (forcing dead red to target floods the scene).
    2. Percentile contrast stretch on luminance (p1 -> 0.02, p99 -> 0.95 in
       linear, color ratios preserved) — the "faded" fix.
    3. TV-Chambolle denoise (scikit-image, BSD) with sigma-estimated weight.
    4. Mild unsharp mask (amount=sharpen, ~1.5px) — the crispness fix.
    """
    from skimage.restoration import denoise_tv_chambolle, estimate_sigma

    if card_wb:
        rect = qm.rectify_quad(recovered_lin, card_quad, CANONICAL_W, CANONICAL_H)
        p_w = next(p for p in layout["patches"] if p["id"] == "gray_white")
        sx = CANONICAL_W / layout["template_width_px"]
        sy = CANONICAL_H / layout["template_height_px"]
        box = rect[int((p_w["y"] + 0.3 * p_w["h"]) * sy):int((p_w["y"] + 0.7 * p_w["h"]) * sy),
                   int((p_w["x"] + 0.3 * p_w["w"]) * sx):int((p_w["x"] + 0.7 * p_w["w"]) * sx)]
        obs_white = np.median(box.reshape(-1, 3), axis=0)
        tgt_white = ccu.srgb_to_linear(np.asarray(p_w["target_srgb"]) / 255.0)
        gains = tgt_white / np.maximum(obs_white, 1e-4)
    else:
        # Cardless (sea-thru philosophy): make the top of each channel's
        # histogram neutral — p95 per channel mapped to a common level.
        p95 = np.percentile(recovered_lin.reshape(-1, 3), 95.0, axis=0)
        gains = float(p95.mean()) / np.maximum(p95, 1e-4)
    gains[0] = min(gains[0], red_wb_cap)
    out = np.clip(recovered_lin * gains, 0.0, 1.0)

    info = {"wb_gains": np.round(gains, 3).tolist()}
    if stretch == "luma":
        lum = np.maximum(ccu.rel_luminance_linear(out), 1e-6)
        lo, hi = np.percentile(lum, [1.0, 99.0])
        new_lum = np.clip(0.02 + (lum - lo) / max(hi - lo, 1e-6) * (0.95 - 0.02),
                          0.0, 1.0)
        out = np.clip(out * (new_lum / lum)[..., None], 0.0, 1.0)
        info["stretch_p1_p99"] = [round(float(lo), 4), round(float(hi), 4)]
    elif stretch == "perchannel":
        # Per-channel p1->0.02 / p99->0.95: pulling each channel's black level
        # to the floor removes the residual veiling cast (the "fade"), which a
        # luminance-preserving stretch faithfully keeps.
        los = np.percentile(out.reshape(-1, 3), 1.0, axis=0)
        his = np.percentile(out.reshape(-1, 3), 99.0, axis=0)
        out = np.clip(black_point + (out - los) / np.maximum(his - los, 1e-6)
                      * (0.95 - black_point), 0.0, 1.0)
        info["stretch_perchannel_p1_p99"] = [np.round(los, 4).tolist(),
                                             np.round(his, 4).tolist()]

    sigma = float(np.mean(estimate_sigma(out, channel_axis=-1))) / 10.0
    out = denoise_tv_chambolle(out, weight=max(sigma, 0.005), channel_axis=-1)
    info["tv_weight"] = round(max(sigma, 0.005), 5)

    if sharpen > 0:
        blur = cv2.GaussianBlur(out.astype(np.float32), (0, 0), 1.5).astype(np.float64)
        out = out + sharpen * (out - blur)
        info["unsharp_amount"] = sharpen
    return np.clip(out, 0.0, 1.0), info


def recover(img_lin: np.ndarray, z: np.ndarray, phys: dict,
            max_boost: float, z_cap: float) -> np.ndarray:
    z3 = z[..., None]
    backscatter = phys["B_inf"] * (1.0 - np.exp(-phys["beta_B"] * z3))
    direct = np.clip(img_lin - backscatter, 0.0, None)
    # Attenuation compensation saturates at z_cap: beyond ~2x the card's
    # distance the red boost exp(beta_D*z) amplifies pure sensor noise, so we
    # correct fully out to z_cap and hold that gain constant farther away.
    z_att = np.minimum(z3, z_cap)
    boost = np.minimum(np.exp(phys["beta_D"] * z_att), max_boost)
    if phys.get("red_boost_cap") is not None:
        # Red is mostly sensor noise at these ranges: per-channel inversion
        # amplifies it into a red glow. Cap the physics-stage red gain and let
        # the cross-channel polish reconstruct red from healthy G/B instead.
        boost[..., 0] = np.minimum(boost[..., 0], phys["red_boost_cap"])
    return np.clip(direct * boost, 0.0, 1.0)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--images", nargs="+", required=True)
    ap.add_argument("--output-dir", default="")
    ap.add_argument("--template-dir", default=str(DEFAULT_TEMPLATE_DIR))
    ap.add_argument("--tag-family", default="DICT_APRILTAG_36h11")
    ap.add_argument("--scales", nargs="+", type=float, default=[1, 2, 3, 4, 6, 8])
    ap.add_argument("--corner-map", default="tl:0,tr:1,bl:2,br:3")
    ap.add_argument("--card-expand-x", type=float, default=1.25)
    ap.add_argument("--card-expand-y", type=float, default=2.0)
    ap.add_argument("--patch-inset", type=float, default=0.30)
    ap.add_argument("--z-card", type=float, default=1.0,
                    help="card distance; meters when measured (AOML: 1.5), "
                         "else arbitrary units (correction is invariant to "
                         "the absolute scale)")
    ap.add_argument("--near-ratio", type=float, default=0.4,
                    help="nearest-pixel distance as a fraction of z-card "
                         "(with the camera ~25cm off the sand and the card at "
                         "1.5m, the frame-bottom sand sits at ~0.45)")
    ap.add_argument("--camera-height-m", type=float, default=None,
                    help="camera height above the seafloor (metadata + future "
                         "ground-plane depth anchoring)")
    ap.add_argument("--water-depth-m", type=float, default=None,
                    help="deployment water depth (metadata + future ambient-"
                         "illuminant modeling)")
    ap.add_argument("--far-quantile", type=float, default=0.98,
                    help="pixels at/above this z quantile define open water (B_inf)")
    ap.add_argument("--max-boost", type=float, default=32.0,
                    help="cap on per-channel attenuation gain exp(beta_D*z)")
    ap.add_argument("--z-cap", type=float, default=2.0,
                    help="attenuation compensation saturates at this z (in "
                         "z-card units); beyond it the gain is held constant")
    ap.add_argument("--illumination", default="global", choices=["global", "lsac"],
                    help="'global': exponential attenuation compensation (v1); "
                         "'lsac': local space-average-color normalization after "
                         "backscatter removal (sea-thru-style, natural render)")
    ap.add_argument("--depth-npy", default="",
                    help="load a precomputed normalized disparity map (.npy, "
                         "image-sized, larger=nearer) instead of running the "
                         "depth model — e.g. a multi-frame fused site map")
    ap.add_argument("--depth-scale", type=float, default=2.0,
                    help="depth-model internal resolution as multiple of image "
                         "size (ceiling test: 2x resolves coral lobes)")
    ap.add_argument("--no-depth-refine", action="store_true",
                    help="skip image-guided edge refinement of the depth map")
    ap.add_argument("--hfov-deg", type=float, default=52.0,
                    help="assumed in-water horizontal FOV for the ground-plane "
                         "constraint (IMX708 behind a flat port)")
    ap.add_argument("--lsac-sigma-frac", type=float, default=0.12,
                    help="LSAC Gaussian sigma as a fraction of image width")
    ap.add_argument("--lsac-mode", default="chroma",
                    choices=["chroma", "flatten"],
                    help="chroma: remove local color cast, keep captured "
                         "luminance (shadows stay dark); flatten: v2-v5 "
                         "behavior (lifts shadows toward local mean)")
    ap.add_argument("--lsac-filter", default="gaussian",
                    choices=["gaussian", "guided", "guided_luma"],
                    help="'guided' = depth-guided filter (He 2010): the "
                         "illumination map follows depth edges instead of "
                         "bleeding haze across coral silhouettes")
    ap.add_argument("--red-wb-cap", type=float, default=1.5,
                    help="max red gain in the --finish white balance")
    ap.add_argument("--no-card-color", action="store_true",
                    help="EXPERIMENT: drop every card COLOR anchor (beta_B, "
                         "exposure, finish WB become scene-statistics; card "
                         "still anchors depth geometry)")
    ap.add_argument("--fixed-beta-b", type=float, default=1.0,
                    help="per-meter backscatter growth used with "
                         "--no-card-color (no black patch to fit from)")
    ap.add_argument("--stretch-black", type=float, default=0.02,
                    help="black point of the --finish stretch (0.0 = crushed "
                         "sea-thru-style shadows; costs shadow detail)")
    ap.add_argument("--bs-guard", type=float, default=0.8,
                    help="max fraction of a pixel's own signal the backscatter "
                         "subtraction may remove (higher = darker shadows)")
    ap.add_argument("--sharpen", type=float, default=0.6,
                    help="unsharp amount in --finish (0 disables)")
    ap.add_argument("--stretch-mode", default="perchannel",
                    choices=["perchannel", "luma", "none"],
                    help="--finish contrast stretch: perchannel also removes "
                         "residual veiling cast; luma preserves color balance")
    ap.add_argument("--red-boost-cap", type=float, default=None,
                    help="cap the physics-stage RED gain (e.g. 2.0) and let "
                         "the cross-channel polish reconstruct red from G/B; "
                         "default: same cap as other channels")
    ap.add_argument("--finish", action="store_true",
                    help="card-anchored white balance (red-capped) + TV "
                         "denoise as the final step (sea-thru-style finish)")
    ap.add_argument("--no-polish", action="store_true",
                    help="skip the final fit on recovered card patches")
    ap.add_argument("--polish-method", default="root_poly2",
                    choices=["root_poly2", "gray_balance", "white_patch", "ccm3x3"],
                    help="registry method for the final polish (root_poly2's "
                         "cross-channel terms can flood scenes with "
                         "reconstructed red when the physics stage leaves "
                         "card red crushed — gray_balance is the safe pick)")
    args = ap.parse_args()

    run_tag = f"hybrid_physics_{datetime.now(timezone.utc).strftime('%Y%m%d')}"
    out_dir = (Path(args.output_dir).expanduser().resolve() if args.output_dir
               else TOOLS_DIR.parent / "runs" / run_tag)
    out_dir.mkdir(parents=True, exist_ok=True)
    layout = ccu.load_template(Path(args.template_dir) / "template_layout.json")
    qm = load_quality_module()
    corner_map = qm.parse_corner_map(args.corner_map)
    depth_pipe = None if args.depth_npy else get_depth_pipe()

    print(f"run_tag={run_tag}\noutput={out_dir}")
    rows = []
    for img_path in [Path(p).expanduser().resolve() for p in args.images]:
        print(f"\n=== {img_path.name} ===")
        row = {"image": img_path.name}
        try:
            img_bgr = qm.load_image_bgr(img_path)
            img_rgb = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB)
            pil_img = Image.fromarray(img_rgb)

            _, corners_by_id, _, _ = qm.detect_tags(img_bgr, args.tag_family, args.scales)
            fid_quad, _, _ = qm.infer_card_corners_from_tags(corners_by_id, corner_map)
            if fid_quad is None:
                raise RuntimeError("card not detected")
            card_quad = qm.expand_quad(fid_quad, args.card_expand_x, args.card_expand_y)
            rect_rgb = cv2.cvtColor(
                qm.rectify_quad(img_bgr, card_quad, CANONICAL_W, CANONICAL_H),
                cv2.COLOR_BGR2RGB)
            samples = ccu.sample_patches(rect_rgb, layout, args.patch_inset)

            print("  estimating depth...")
            if args.depth_npy:
                disp_n = np.load(Path(args.depth_npy).expanduser())
                if disp_n.shape != img_rgb.shape[:2]:
                    raise RuntimeError(f"--depth-npy shape {disp_n.shape} != image")
            else:
                disp_n = relative_depth(depth_pipe, pil_img, scale=args.depth_scale,
                                        refine=not args.no_depth_refine)
            z, zinfo = anchor_depth(disp_n, card_quad, args.z_card, args.near_ratio)
            if args.camera_height_m:
                z, ginfo = apply_ground_plane(z, card_quad, args.z_card,
                                              args.camera_height_m, args.hfov_deg)
                zinfo["ground_plane"] = ginfo
                print(f"  ground plane: pitch={ginfo['pitch_deg']}deg "
                      f"capped {ginfo['ground_rows_capped']} px "
                      f"(z_ground at bottom row {ginfo['z_ground_bottom_row']}m)")
            Image.fromarray((disp_n * 255).astype(np.uint8)).save(
                out_dir / f"{img_path.stem}_depth.png")

            img_lin = ccu.srgb_to_linear(img_rgb.astype(np.float64) / 255.0)
            phys = solve_physics(img_lin, z, samples, args.z_card, args.far_quantile)
            phys["red_boost_cap"] = args.red_boost_cap
            if args.no_card_color:
                phys["beta_B"] = np.full(3, args.fixed_beta_b)
            print(f"  B_inf(lin)={np.round(phys['B_inf'], 4).tolist()} "
                  f"beta_B={np.round(phys['beta_B'], 3).tolist()} "
                  f"beta_D={np.round(phys['beta_D'], 3).tolist()} (per unit z)")

            if args.illumination == "lsac":
                recovered_lin = lsac_recover(img_lin, z, phys, args.lsac_sigma_frac,
                                             args.lsac_filter, args.bs_guard,
                                             args.lsac_mode)
                # Card-anchored exposure: scale so the mid-gray patch lands on
                # its design luminance, then clip.
                rect_lin = qm.rectify_quad(recovered_lin, card_quad,
                                           CANONICAL_W, CANONICAL_H)
                p_mid = next(p for p in layout["patches"] if p["id"] == "gray_mid")
                sx = CANONICAL_W / layout["template_width_px"]
                sy = CANONICAL_H / layout["template_height_px"]
                box = rect_lin[int((p_mid["y"] + 0.3 * p_mid["h"]) * sy):
                               int((p_mid["y"] + 0.7 * p_mid["h"]) * sy),
                               int((p_mid["x"] + 0.3 * p_mid["w"]) * sx):
                               int((p_mid["x"] + 0.7 * p_mid["w"]) * sx)]
                if args.no_card_color:
                    lum = float(np.median(ccu.rel_luminance_linear(recovered_lin)))
                    target_lum = 0.18  # scene median to mid-gray (gray-world)
                else:
                    lum = float(ccu.rel_luminance_linear(
                        np.median(box.reshape(-1, 3), axis=0)))
                    target_lum = float(ccu.rel_luminance_linear(
                        ccu.srgb_to_linear(np.asarray(p_mid["target_srgb"]) / 255.0)))
                recovered_lin = np.clip(recovered_lin * target_lum / max(lum, 1e-6),
                                        0.0, 1.0)
                print(f"  LSAC exposure anchor: gray_mid lum {lum:.3f} -> {target_lum:.3f}")
            else:
                recovered_lin = recover(img_lin, z, phys, args.max_boost,
                                        args.z_cap * args.z_card)
            finish_info = None
            if args.finish:
                recovered_lin, finish_info = finish(
                    recovered_lin, card_quad, qm, layout, args.patch_inset,
                    red_wb_cap=args.red_wb_cap, stretch=args.stretch_mode,
                    sharpen=args.sharpen, black_point=args.stretch_black,
                    card_wb=not args.no_card_color)
                print(f"  finish: wb_gains={finish_info['wb_gains']} "
                      f"tv_weight={finish_info['tv_weight']}")
            recovered = np.clip(np.rint(ccu.linear_to_srgb(recovered_lin) * 255.0),
                                0, 255).astype(np.uint8)
            nopolish_path = out_dir / f"{img_path.stem}_hybrid_nopolish.png"
            Image.fromarray(recovered).save(nopolish_path)

            polish_model = None
            final = recovered
            if not args.no_polish:
                # Re-sample the card from the RECOVERED image (same quad) and fit
                # root_poly2 on what physics left over.
                rect_rec = cv2.cvtColor(
                    qm.rectify_quad(cv2.cvtColor(recovered, cv2.COLOR_RGB2BGR),
                                    card_quad, CANONICAL_W, CANONICAL_H),
                    cv2.COLOR_BGR2RGB)
                samples_rec = ccu.sample_patches(rect_rec, layout, args.patch_inset)
                polish_model = ccu.METHOD_REGISTRY[args.polish_method](
                    samples_rec, ccu.srgb_to_linear(
                        recovered.astype(np.float64) / 255.0))
                final = np.clip(np.rint(polish_model.apply_srgb255(recovered)),
                                0, 255).astype(np.uint8)

            out_png = out_dir / f"{img_path.stem}_hybrid.png"
            Image.fromarray(final).save(out_png)
            row.update({
                "output": str(out_png), "nopolish": str(nopolish_path),
                "depth_anchor": zinfo,
                "B_inf_linear": np.round(phys["B_inf"], 5).tolist(),
                "beta_B_per_z": np.round(phys["beta_B"], 4).tolist(),
                "beta_D_per_z": np.round(phys["beta_D"], 4).tolist(),
                "far_pixel_count": phys["far_pixel_count"],
                "max_boost": args.max_boost, "z_cap": args.z_cap,
                "finish": finish_info,
                "polish": None if polish_model is None else polish_model.to_dict(),
            })
            print(f"  saved {out_png}")
        except Exception as exc:
            row["error"] = f"{type(exc).__name__}: {exc}"
            print(f"  ERROR: {row['error']}")
        (out_dir / f"{img_path.stem}_params.json").write_text(
            json.dumps(row, indent=2), encoding="utf-8")
        rows.append(row)

    (out_dir / "summary.json").write_text(json.dumps({
        "run_tag": run_tag, "depth_model": DEPTH_MODEL,
        "args": {k: v for k, v in vars(args).items()},
        "generated_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "images": rows,
    }, indent=2), encoding="utf-8")
    print(f"\nsummary={out_dir / 'summary.json'}")
    if all("error" in r for r in rows):
        sys.exit(1)


if __name__ == "__main__":
    main()

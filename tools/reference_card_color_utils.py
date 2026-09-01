#!/usr/bin/env python3
"""
Reference-card color utilities: patch sampling, color math, correction methods.

Purpose:
  Shared math for tools/bm_reference_card_color_smoke.py (Sprint05 revival).
  Samples the Reef Reference Card V2 patches from a rectified card crop and
  solves/applies a set of color-correction methods against the card's design
  target colors.

Inputs:
  - Rectified card crop (numpy RGB uint8) in the canonical 3000x1000 frame
    produced by tools/bm_reference_card_quality_v2.py rectify_quad().
  - template_layout.json from
    tools/reference_card_color_correction/reference_card_template_v2/
    (patch x/y/w/h in the canonical frame + target_srgb design values).

Assumptions:
  - target_srgb are nominal design values from the V2 SVG, not measured print
    values. Absolute print/illumination error is common to all methods, so
    method-vs-method comparison remains fair.
  - All correction methods operate in LINEAR RGB (sRGB decoded), then results
    are re-encoded to sRGB. Underwater images have almost no red signal, so
    large red gains / matrix coefficients and amplified red noise are expected
    findings, not bugs.

Adding a new correction method (e.g. from ongoing underwater color research):
  Write solve_<name>(samples, img_lin) -> CorrectionModel and register it in
  METHOD_REGISTRY at the bottom. The smoke-test tool picks it up automatically
  (it iterates METHOD_REGISTRY and exposes names via --methods).

Known limitations:
  - CIE76 delta-E (adequate for MVP ranking; not perceptually uniform in blue).
  - No chromatic-adaptation transform; matrices are plain least squares.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import numpy as np

# ---------------------------------------------------------------------------
# sRGB <-> linear and Lab conversions (vectorized, D65)
# ---------------------------------------------------------------------------

def srgb_to_linear(srgb: np.ndarray) -> np.ndarray:
    """sRGB in 0..1 -> linear RGB in 0..1."""
    s = np.asarray(srgb, dtype=np.float64)
    return np.where(s <= 0.04045, s / 12.92, ((s + 0.055) / 1.055) ** 2.4)


def linear_to_srgb(lin: np.ndarray) -> np.ndarray:
    """Linear RGB in 0..1 -> sRGB in 0..1 (input clipped to 0..1)."""
    l = np.clip(np.asarray(lin, dtype=np.float64), 0.0, 1.0)
    return np.where(l <= 0.0031308, l * 12.92, 1.055 * l ** (1 / 2.4) - 0.055)


_M_RGB2XYZ = np.array([
    [0.4124564, 0.3575761, 0.1804375],
    [0.2126729, 0.7151522, 0.0721750],
    [0.0193339, 0.1191920, 0.9503041],
])
_WHITE_D65 = np.array([0.95047, 1.0, 1.08883])


def srgb_to_lab(srgb_255: np.ndarray) -> np.ndarray:
    """sRGB 0..255 (..., 3) -> CIELAB (..., 3), D65."""
    lin = srgb_to_linear(np.asarray(srgb_255, dtype=np.float64) / 255.0)
    xyz = lin @ _M_RGB2XYZ.T / _WHITE_D65
    f = np.where(xyz > 0.008856, np.cbrt(xyz), 7.787 * xyz + 16.0 / 116.0)
    fx, fy, fz = f[..., 0], f[..., 1], f[..., 2]
    return np.stack([116.0 * fy - 16.0, 500.0 * (fx - fy), 200.0 * (fy - fz)], axis=-1)


def delta_e76(rgb_a_255: np.ndarray, rgb_b_255: np.ndarray) -> np.ndarray:
    """CIE76 delta-E between two sRGB 0..255 arrays of shape (..., 3)."""
    return np.linalg.norm(srgb_to_lab(rgb_a_255) - srgb_to_lab(rgb_b_255), axis=-1)


def rel_luminance_linear(lin: np.ndarray) -> np.ndarray:
    """Relative luminance of linear RGB (..., 3)."""
    return lin @ np.array([0.2126, 0.7152, 0.0722])


# ---------------------------------------------------------------------------
# Template + patch sampling
# ---------------------------------------------------------------------------

@dataclass
class PatchSample:
    patch_id: str
    patch_type: str            # "gray" | "color"
    label: str
    target_srgb: np.ndarray    # (3,) 0..255 design value
    median_srgb: np.ndarray    # (3,) 0..255 observed median
    mean_srgb: np.ndarray      # (3,) 0..255 observed mean
    std_srgb: np.ndarray       # (3,) observed std (texture / glint indicator)
    clip_low_frac: float       # fraction of pixels with any channel <= 2
    clip_high_frac: float      # fraction of pixels with any channel >= 253
    use_for_gray_balance: bool
    use_for_matrix: bool

    def to_dict(self) -> dict:
        return {
            "id": self.patch_id,
            "type": self.patch_type,
            "label": self.label,
            "target_srgb": [int(v) for v in self.target_srgb],
            "median_srgb": [round(float(v), 2) for v in self.median_srgb],
            "mean_srgb": [round(float(v), 2) for v in self.mean_srgb],
            "std_srgb": [round(float(v), 2) for v in self.std_srgb],
            "clip_low_frac": round(self.clip_low_frac, 4),
            "clip_high_frac": round(self.clip_high_frac, 4),
            "use_for_gray_balance": self.use_for_gray_balance,
            "use_for_matrix": self.use_for_matrix,
        }


def load_template(template_json: Path) -> dict:
    layout = json.loads(Path(template_json).read_text(encoding="utf-8"))
    for key in ("template_width_px", "template_height_px", "patches"):
        if key not in layout:
            raise ValueError(f"template_layout.json missing key {key!r}")
    return layout


def sample_patches(rect_rgb: np.ndarray, layout: dict,
                   inset_frac: float = 0.30) -> List[PatchSample]:
    """Sample every template patch from a rectified card crop.

    rect_rgb: RGB uint8 array; any size (coords scaled from canonical frame).
    inset_frac: fraction shaved off each side of the patch box so border
      bleed / slight warp misalignment does not contaminate the sample.
    """
    h, w = rect_rgb.shape[:2]
    sx = w / layout["template_width_px"]
    sy = h / layout["template_height_px"]
    samples: List[PatchSample] = []
    for p in layout["patches"]:
        x0 = int((p["x"] + p["w"] * inset_frac) * sx)
        x1 = int((p["x"] + p["w"] * (1 - inset_frac)) * sx)
        y0 = int((p["y"] + p["h"] * inset_frac) * sy)
        y1 = int((p["y"] + p["h"] * (1 - inset_frac)) * sy)
        x1, y1 = max(x1, x0 + 2), max(y1, y0 + 2)  # never collapse to nothing
        box = rect_rgb[y0:y1, x0:x1].reshape(-1, 3).astype(np.float64)
        samples.append(PatchSample(
            patch_id=p["id"],
            patch_type=p["type"],
            label=p.get("label", p["id"]),
            target_srgb=np.asarray(p["target_srgb"], dtype=np.float64),
            median_srgb=np.median(box, axis=0),
            mean_srgb=box.mean(axis=0),
            std_srgb=box.std(axis=0),
            clip_low_frac=float(np.mean((box <= 2).any(axis=1))),
            clip_high_frac=float(np.mean((box >= 253).any(axis=1))),
            use_for_gray_balance=bool(p.get("use_for_gray_balance", False)),
            use_for_matrix=bool(p.get("use_for_matrix", p["type"] == "color")),
        ))
    return samples


# ---------------------------------------------------------------------------
# Correction models
# ---------------------------------------------------------------------------

@dataclass
class CorrectionModel:
    """A solved correction, applied in linear RGB as out = lin @ matrix + offset."""
    method: str
    matrix: np.ndarray               # (3,3) linear-RGB matrix (diag for gains)
    offset: np.ndarray = field(default_factory=lambda: np.zeros(3))
    notes: List[str] = field(default_factory=list)

    def apply_linear(self, lin: np.ndarray) -> np.ndarray:
        return lin @ self.matrix.T + self.offset

    def apply_srgb255(self, srgb_255: np.ndarray) -> np.ndarray:
        """sRGB uint8/float 0..255 (..., 3) -> corrected sRGB float 0..255."""
        lin = srgb_to_linear(np.asarray(srgb_255, dtype=np.float64) / 255.0)
        return linear_to_srgb(self.apply_linear(lin)) * 255.0

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "matrix": [[round(float(v), 6) for v in row] for row in self.matrix],
            "offset": [round(float(v), 6) for v in self.offset],
            "applied_in": "linear_rgb",
            "notes": self.notes,
        }


def _patch_lin(samples: List[PatchSample]):
    """Observed and target patch colors in linear RGB, shape (N, 3)."""
    obs = srgb_to_linear(np.array([s.median_srgb for s in samples]) / 255.0)
    tgt = srgb_to_linear(np.array([s.target_srgb for s in samples]) / 255.0)
    return obs, tgt


def _safe_gains(target: np.ndarray, observed: np.ndarray, notes: List[str]) -> np.ndarray:
    eps = 1e-6
    if np.any(observed < eps):
        notes.append("observed channel near zero; gain floored at eps=1e-6")
    return target / np.maximum(observed, eps)


def solve_gray_world(samples: List[PatchSample], img_lin: np.ndarray) -> CorrectionModel:
    """No-card baseline: scale each channel so the whole-image mean is neutral."""
    notes: List[str] = ["baseline; uses whole image, ignores the card"]
    mean = img_lin.reshape(-1, 3).mean(axis=0)
    gains = _safe_gains(np.full(3, float(mean.mean())), mean, notes)
    return CorrectionModel("gray_world", np.diag(gains), notes=notes)


def solve_white_patch(samples: List[PatchSample], img_lin: np.ndarray) -> CorrectionModel:
    """Gains from the card's white patch only."""
    notes: List[str] = []
    white = next(s for s in samples if s.patch_id == "gray_white")
    if white.clip_high_frac > 0.05:
        notes.append(f"white patch {white.clip_high_frac:.0%} highlight-clipped; gains may be biased")
    obs = srgb_to_linear(white.median_srgb / 255.0)
    tgt = srgb_to_linear(white.target_srgb / 255.0)
    return CorrectionModel("white_patch", np.diag(_safe_gains(tgt, obs, notes)), notes=notes)


def solve_gray_balance(samples: List[PatchSample], img_lin: np.ndarray) -> CorrectionModel:
    """Sprint05 §9B: per-channel gains from the mid-gray patches (light/mid/dark)."""
    notes: List[str] = []
    grays = [s for s in samples if s.use_for_gray_balance]
    if not grays:
        raise ValueError("no gray patches flagged use_for_gray_balance in template")
    obs, tgt = _patch_lin(grays)
    target_luma = float(rel_luminance_linear(tgt).mean())
    gains = _safe_gains(np.full(3, target_luma), obs.mean(axis=0), notes)
    notes.append(f"grays used: {', '.join(s.patch_id for s in grays)}")
    return CorrectionModel("gray_balance", np.diag(gains), notes=notes)


def _matrix_lstsq(samples: List[PatchSample], affine: bool):
    """Least-squares matrix over all patches (grays + colors) in linear RGB."""
    obs, tgt = _patch_lin(samples)
    notes = [f"solved on {len(samples)} patches (grays + colors), plain least squares"]
    clipped = [s.patch_id for s in samples if s.clip_high_frac > 0.25 or s.clip_low_frac > 0.25]
    if clipped:
        notes.append(f"heavily clipped patches included in fit: {', '.join(clipped)}")
    A = np.hstack([obs, np.ones((len(obs), 1))]) if affine else obs
    coef, *_ = np.linalg.lstsq(A, tgt, rcond=None)  # (3or4, 3): tgt ≈ A @ coef
    if affine:
        return coef[:3].T, coef[3], notes
    return coef.T, np.zeros(3), notes


def solve_ccm3x3(samples: List[PatchSample], img_lin: np.ndarray) -> CorrectionModel:
    """Least-squares 3x3 matrix, observed -> target, all patches, linear RGB."""
    matrix, offset, notes = _matrix_lstsq(samples, affine=False)
    return CorrectionModel("ccm3x3", matrix, offset, notes)


def solve_ccm_affine(samples: List[PatchSample], img_lin: np.ndarray) -> CorrectionModel:
    """3x3 matrix + offset; the offset term absorbs underwater veiling light."""
    matrix, offset, notes = _matrix_lstsq(samples, affine=True)
    return CorrectionModel("ccm_affine", matrix, offset, notes)


# Registry the smoke-test tool iterates. Order = order on the cut sheet.
# To experiment with a new method, add: "name": solve_name
METHOD_REGISTRY: Dict[str, Callable[[List[PatchSample], np.ndarray], CorrectionModel]] = {
    "gray_world": solve_gray_world,
    "white_patch": solve_white_patch,
    "gray_balance": solve_gray_balance,
    "ccm3x3": solve_ccm3x3,
    "ccm_affine": solve_ccm_affine,
}


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------

def gray_neutrality(samples: List[PatchSample],
                    model: Optional[CorrectionModel] = None) -> float:
    """Mean channel imbalance on the gray-balance patches (0 = perfectly neutral).

    Per patch: mean absolute deviation of R/G/B from the patch mean, divided by
    the patch mean (in sRGB 0..255). Optionally after applying a correction.
    """
    grays = [s for s in samples if s.use_for_gray_balance]
    vals = []
    for s in grays:
        rgb = model.apply_srgb255(s.median_srgb) if model else s.median_srgb
        m = float(np.mean(rgb))
        if m < 1e-6:
            continue
        vals.append(float(np.mean(np.abs(rgb - m))) / m)
    return float(np.mean(vals)) if vals else float("nan")


def patch_delta_e(samples: List[PatchSample],
                  model: Optional[CorrectionModel] = None) -> Dict[str, float]:
    """Per-patch CIE76 delta-E vs target, optionally after correction."""
    out = {}
    for s in samples:
        rgb = model.apply_srgb255(s.median_srgb) if model else s.median_srgb
        out[s.patch_id] = float(delta_e76(rgb, s.target_srgb))
    return out


def clip_stats_srgb255(img_255: np.ndarray) -> dict:
    """Percent of pixels near black / white in an sRGB 0..255 image."""
    flat = np.asarray(img_255).reshape(-1, 3)
    return {
        "clip_percent_low": round(100.0 * float(np.mean((flat <= 2).any(axis=1))), 3),
        "clip_percent_high": round(100.0 * float(np.mean((flat >= 253).any(axis=1))), 3),
    }

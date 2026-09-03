"""Tests for tools/reference_card_color_utils.py color math.

CIEDE2000 is validated against published test pairs from Sharma, Wu & Dalal,
"The CIEDE2000 Color-Difference Formula: Implementation Notes..." (2005).
"""
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import reference_card_color_utils as ccu


# --- CIEDE2000 --------------------------------------------------------------

# (Lab1, Lab2, expected dE00) — Sharma et al. 2005 test data, pairs 1-4.
SHARMA_PAIRS = [
    ((50.0, 2.6772, -79.7751), (50.0, 0.0, -82.7485), 2.0425),
    ((50.0, 3.1571, -77.2803), (50.0, 0.0, -82.7485), 2.8615),
    ((50.0, 2.8361, -74.0200), (50.0, 0.0, -82.7485), 3.4412),
    ((50.0, -1.3802, -84.2814), (50.0, 0.0, -82.7485), 1.0000),
]


@pytest.mark.parametrize("lab1,lab2,expected", SHARMA_PAIRS)
def test_ciede2000_sharma_pairs(lab1, lab2, expected):
    got = ccu._ciede2000_lab(np.array(lab1), np.array(lab2))
    assert got == pytest.approx(expected, abs=1e-4)


def test_ciede2000_identical_is_zero():
    rgb = np.array([120.0, 80.0, 40.0])
    assert ccu.delta_e2000(rgb, rgb) == pytest.approx(0.0, abs=1e-9)


# --- sRGB/linear round trip -------------------------------------------------

def test_srgb_linear_round_trip():
    v = np.linspace(0.0, 1.0, 32)
    assert np.allclose(ccu.linear_to_srgb(ccu.srgb_to_linear(v)), v, atol=1e-9)


# --- synthetic patch fixtures ----------------------------------------------

def _make_samples(distort):
    """PatchSamples whose observed values are distort(target) in linear RGB."""
    targets = [
        ("gray_light", "gray", [200, 200, 200], True),
        ("gray_mid", "gray", [128, 128, 128], True),
        ("gray_dark", "gray", [74, 74, 74], True),
        ("gray_white", "gray", [255, 255, 255], False),
        ("red_orange", "color", [240, 74, 24], False),
        ("green", "color", [108, 176, 80], False),
        ("blue", "color", [12, 102, 199], False),
        ("yellow", "color", [247, 185, 30], False),
        ("cyan", "color", [92, 200, 208], False),
        ("magenta", "color", [180, 70, 163], False),
    ]
    samples = []
    for pid, ptype, tgt, gb in targets:
        tgt = np.asarray(tgt, dtype=np.float64)
        obs_lin = distort(ccu.srgb_to_linear(tgt / 255.0))
        obs = ccu.linear_to_srgb(obs_lin) * 255.0
        samples.append(ccu.PatchSample(
            patch_id=pid, patch_type=ptype, label=pid, target_srgb=tgt,
            median_srgb=obs, mean_srgb=obs, std_srgb=np.zeros(3),
            clip_low_frac=0.0, clip_high_frac=0.0,
            use_for_gray_balance=gb, use_for_matrix=(ptype == "color")))
    return samples


def test_root_poly2_recovers_channel_gain_distortion():
    gains = np.array([0.15, 0.85, 0.70])  # underwater-ish red crush
    samples = _make_samples(lambda lin: lin * gains)
    model = ccu.solve_root_poly2(samples, np.zeros((2, 2, 3)))
    de = ccu.patch_delta_e(samples, model)
    assert max(de.values()) < 1.0  # linear distortion is exactly invertible


def test_root_poly_is_exposure_invariant():
    gains = np.array([0.15, 0.85, 0.70])
    samples = _make_samples(lambda lin: lin * gains)
    model = ccu.solve_root_poly2(samples, np.zeros((2, 2, 3)))
    lin = np.array([[0.2, 0.4, 0.3]])
    out1 = model.apply_linear(lin)
    out2 = model.apply_linear(0.5 * lin)
    assert np.allclose(out2, 0.5 * out1, atol=1e-12)


def test_ccm3x3_recovers_matrix_distortion():
    M = np.array([[0.2, 0.05, 0.0], [0.02, 0.9, 0.05], [0.0, 0.1, 0.8]])
    samples = _make_samples(lambda lin: lin @ M.T)
    model = ccu.solve_ccm3x3(samples, np.zeros((2, 2, 3)))
    de = ccu.patch_delta_e(samples, model)
    assert max(de.values()) < 1.0


def test_gray_angular_error_zero_for_neutral():
    samples = _make_samples(lambda lin: lin)  # undistorted
    assert ccu.gray_angular_error_deg(samples) == pytest.approx(0.0, abs=1e-6)


def test_card_red_health_flags_dead_red():
    samples = _make_samples(lambda lin: lin * np.array([0.001, 0.8, 0.7]))
    health = ccu.card_red_health(samples)
    assert not health["red_signal_ok"]


def test_veil_ramp_recovers_gain_and_veil():
    gains = np.array([0.06, 0.70, 0.45])
    veil = np.array([0.02, 0.15, 0.10])
    samples = _make_samples(lambda lin: lin * gains + veil)
    model = ccu.solve_veil_ramp(samples, np.zeros((2, 2, 3)))
    # per-channel affine distortion is exactly invertible for ALL patches,
    # colors included, even though only grays were used to solve
    de = ccu.patch_delta_e(samples, model)
    assert max(de.values()) < 1.0
    # the solved inverse encodes the true physical parameters
    assert np.allclose(np.diag(model.matrix), 1.0 / gains, rtol=1e-6)
    assert np.allclose(model.offset, -veil / gains, rtol=1e-6)


def test_veil_poly2_corrects_affine_water_distortion():
    gains = np.array([0.08, 0.65, 0.50])
    veil = np.array([0.01, 0.12, 0.08])
    samples = _make_samples(lambda lin: lin * gains + veil)
    model = ccu.solve_veil_poly2(samples, np.zeros((2, 2, 3)))
    de = ccu.patch_delta_e(samples, model)
    assert float(np.mean(list(de.values()))) < 2.0


def test_veil_poly2_red_guard_locks_red_row_when_red_dead():
    gains = np.array([0.002, 0.65, 0.50])  # red effectively extinct
    veil = np.array([0.0, 0.12, 0.08])
    samples = _make_samples(lambda lin: lin * gains + veil)
    model = ccu.solve_veil_poly2(samples, np.zeros((2, 2, 3)))
    stage2 = model.stages[1]
    identity_row = np.zeros(stage2.matrix.shape[1])
    identity_row[0] = 1.0
    # heavy ridge shrinkage: red output row must stay near identity so red
    # noise is not amplified into a warm cast
    assert np.linalg.norm(stage2.matrix[0] - identity_row) < 0.2


def test_grvi_neutralizes_grays_and_bounds_red():
    gains = np.array([0.02, 0.65, 0.50])   # red nearly dead (2%)
    veil = np.array([0.005, 0.15, 0.08])
    samples = _make_samples(lambda lin: lin * gains + veil)
    model = ccu.solve_grvi(samples, np.zeros((2, 2, 3)))
    # red amplification must be capped at 2x the green amp, not 50x
    assert model.amp[0] <= 2.0 * model.amp[1] + 1e-9
    out = model.apply_srgb255(np.array([s.median_srgb for s in samples]))
    assert np.all(np.isfinite(out))
    # with red nearly dead, GRVI keeps red conservative: the corrected gray
    # may be red-deficient (honest) but must never be red-dominant
    # (hallucinated warmth); G/B must still balance
    mid = next(i for i, s in enumerate(samples) if s.patch_id == "gray_mid")
    r, g, b = out[mid]
    assert r <= g * 1.05
    assert abs(b - g) / max(g, 1e-6) < 0.15


def test_grvi_output_in_display_range():
    gains = np.array([0.06, 0.70, 0.45])
    veil = np.array([0.02, 0.15, 0.10])
    samples = _make_samples(lambda lin: lin * gains + veil)
    model = ccu.solve_grvi(samples, np.zeros((2, 2, 3)))
    rng = np.random.default_rng(7)
    img = rng.uniform(0, 255, size=(8, 8, 3))
    out = model.apply_srgb255(img)
    assert np.all(np.isfinite(out))
    assert out.min() >= 0.0 and out.max() <= 255.0 + 1e-6

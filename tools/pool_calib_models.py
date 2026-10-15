#!/usr/bin/env python3
# filename: pool_calib_models.py
# description: Underwater calibration maths for tools/pool_calib_pipeline.py: OpenCV-compatible distortion models (standard 5, rational 8, fisheye 4), robust fitting, held-out CV, bootstrap, ray uncertainty.
"""
pool_calib_models.py — camera-model maths for the pool calibration pipeline (no I/O, no plots).

Purpose
  Everything that turns ChArUco corner observations into intrinsics and into the numbers the
  pre-registered bars use (docs/pool_calib_prereg_20261014.md):
    - forward + inverse distortion for the three models, in OpenCV's conventions, written out in
      numpy so the inverse can be checked (cv2.undistortPointsIter is gone in OpenCV 5.0 and the
      default cv2.undistortPoints does only 5 fixed-point steps: not accurate at a wide-lens edge);
    - fit()            one model on a set of views (cv2.calibrateCameraExtended / cv2.fisheye.calibrate)
    - fit_robust()     + per-view outlier rejection (the pre-registered rule)
    - cross_validate() k-fold by view: train on k-1 folds, pose-only fit on each held-out view
    - bootstrap()      resample views with replacement, refit, keep K and D
    - rays()           pixel -> undistorted ray (normalised x/z, y/z), NaN where the model cannot
                       be inverted (non-convergent or folded): that is itself a red flag.

Coordinate systems
  Pixels are native sensor pixels of the frame the views came from (4608x2592 for the pool data).
  "Normalised" = (X/Z, Y/Z) of the camera-frame ray. Distortion maps undistorted normalised ->
  distorted normalised; K maps distorted normalised -> pixels (skew fixed at 0 for all models).

Model conventions (OpenCV, checked against cv2.projectPoints / cv2.fisheye.projectPoints in
tests/test_pool_calib_pipeline.py)
  standard  D = k1 k2 p1 p2 k3               radial (1 + k1 r^2 + k2 r^4 + k3 r^6) + tangential
  rational  D = k1 k2 p1 p2 k3 k4 k5 k6      radial (1 + k1 r^2 + k2 r^4 + k3 r^6) / (1 + k4 r^2 + k5 r^4 + k6 r^6)
  fisheye   D = k1 k2 k3 k4                  theta_d = theta (1 + k1 theta^2 + k2 theta^4 + k3 theta^6 + k4 theta^8)

Limitations
  - Pinhole + distortion is an approximation behind a flat port (refraction is non-central). It is
    the standard practice at a fixed working range; the fit residuals say how good it is here.
  - Thin-prism and tilt terms are not fitted (OpenCV flags off), so they are not modelled here.
"""
import math

import numpy as np

MODELS = ("standard", "rational", "fisheye")
MODEL_PARAMS = {"standard": 5, "rational": 8, "fisheye": 4}
# Tie-break order when held-out RMS is within the tie margin (pre-registered): standard is what
# cv2.undistort / initUndistortRectifyMap and our own tools use.
MODEL_PREFERENCE = ("standard", "fisheye", "rational")


def _cv2():
    import cv2
    return cv2


# ----------------------------------------------------------------------------- forward model

def distort_norm(model, D, x, y):
    """Undistorted normalised (x, y) -> distorted normalised (xd, yd). Arrays of any shape."""
    D = np.asarray(D, dtype=float).ravel()
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if model == "fisheye":
        k1, k2, k3, k4 = (list(D) + [0.0] * 4)[:4]
        r = np.hypot(x, y)
        th = np.arctan(r)
        t2 = th * th
        thd = th * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4))))
        scale = np.where(r > 1e-12, thd / np.maximum(r, 1e-12), 1.0)
        return x * scale, y * scale
    k = np.zeros(8)
    k[:min(8, D.size)] = D[:8]
    k1, k2, p1, p2, k3, k4, k5, k6 = k
    r2 = x * x + y * y
    rad = (1 + r2 * (k1 + r2 * (k2 + r2 * k3))) / (1 + r2 * (k4 + r2 * (k5 + r2 * k6)))
    xd = x * rad + 2 * p1 * x * y + p2 * (r2 + 2 * x * x)
    yd = y * rad + p1 * (r2 + 2 * y * y) + 2 * p2 * x * y
    return xd, yd


def norm_to_px(K, xd, yd):
    K = np.asarray(K, dtype=float)
    return K[0, 0] * xd + K[0, 1] * yd + K[0, 2], K[1, 1] * yd + K[1, 2]


def px_to_norm(K, u, v):
    K = np.asarray(K, dtype=float)
    yd = (np.asarray(v, float) - K[1, 2]) / K[1, 1]
    xd = (np.asarray(u, float) - K[0, 2] - K[0, 1] * yd) / K[0, 0]
    return xd, yd


def project(model, K, D, obj, rvec, tvec):
    """Board points (N,3) mm -> pixels (N,2) for one view."""
    cv2 = _cv2()
    R, _ = cv2.Rodrigues(np.asarray(rvec, dtype=float).reshape(3, 1))
    P = np.asarray(obj, dtype=float).reshape(-1, 3) @ R.T + np.asarray(tvec, dtype=float).reshape(1, 3)
    xd, yd = distort_norm(model, D, P[:, 0] / P[:, 2], P[:, 1] / P[:, 2])
    u, v = norm_to_px(K, xd, yd)
    return np.stack([u, v], axis=1)


# ----------------------------------------------------------------------------- inverse model

def undistort_norm(model, D, xd, yd, iters=60, tol=1e-11):
    """Distorted normalised -> undistorted normalised. NaN where the model has no valid inverse
    (did not converge, or the radial map folds back between the centre and this radius)."""
    xd = np.asarray(xd, dtype=float)
    yd = np.asarray(yd, dtype=float)
    shape = xd.shape
    xd, yd = xd.ravel(), yd.ravel()
    D = np.asarray(D, dtype=float).ravel()
    if model == "fisheye":
        k1, k2, k3, k4 = (list(D) + [0.0] * 4)[:4]
        rd = np.hypot(xd, yd)
        th = np.minimum(rd, 1.5)
        ok = np.ones_like(rd, dtype=bool)
        for _ in range(iters):
            t2 = th * th
            f = th * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4)))) - rd
            df = 1 + t2 * (3 * k1 + t2 * (5 * k2 + t2 * (7 * k3 + t2 * 9 * k4)))
            step = f / np.where(np.abs(df) > 1e-12, df, 1e-12)
            th = th - step
            if np.nanmax(np.abs(step)) < tol:
                break
        t2 = th * th
        resid = np.abs(th * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4)))) - rd)
        ok &= (resid < 1e-9) & (th >= 0) & (th < math.pi / 2)
        ok &= _monotonic_fisheye(D, th)
        r = np.tan(np.clip(th, 0, math.pi / 2 - 1e-9))
        scale = np.where(rd > 1e-12, r / np.maximum(rd, 1e-12), 1.0)
        x, y = xd * scale, yd * scale
    else:
        x, y = xd.copy(), yd.copy()
        h = 1e-7
        for _ in range(iters):
            fx_, fy_ = distort_norm(model, D, x, y)
            ex, ey = fx_ - xd, fy_ - yd
            ax, ay = distort_norm(model, D, x + h, y)
            bx, by = distort_norm(model, D, x, y + h)
            a, c = (ax - fx_) / h, (ay - fy_) / h
            b, d = (bx - fx_) / h, (by - fy_) / h
            det = a * d - b * c
            det = np.where(np.abs(det) > 1e-14, det, 1e-14)
            dx = (d * ex - b * ey) / det
            dy = (-c * ex + a * ey) / det
            x, y = x - dx, y - dy
            if np.nanmax(np.abs(np.concatenate([dx, dy]))) < tol:
                break
        fx_, fy_ = distort_norm(model, D, x, y)
        ok = (np.hypot(fx_ - xd, fy_ - yd) < 1e-9) & np.isfinite(x) & np.isfinite(y)
        ok &= _monotonic_radial(model, D, x, y)
    x = np.where(ok, x, np.nan)
    y = np.where(ok, y, np.nan)
    return x.reshape(shape), y.reshape(shape)


def _monotonic_radial(model, D, x, y, steps=32):
    """The distorted radius must grow monotonically from the centre out to (x, y) along the ray;
    a fold means two different rays land on the same pixel = the model is extrapolating badly."""
    s = np.linspace(0.0, 1.0, steps + 1)[None, :]
    xs, ys = x[:, None] * s, y[:, None] * s
    xd, yd = distort_norm(model, D, xs, ys)
    rd = np.hypot(xd, yd)
    return np.all(np.diff(rd, axis=1) > -1e-12, axis=1)


def _monotonic_fisheye(D, th, steps=32):
    k1, k2, k3, k4 = (list(np.asarray(D, float).ravel()) + [0.0] * 4)[:4]
    s = np.linspace(0.0, 1.0, steps + 1)[None, :]
    t = np.clip(th, 0, math.pi / 2)[:, None] * s
    t2 = t * t
    thd = t * (1 + t2 * (k1 + t2 * (k2 + t2 * (k3 + t2 * k4))))
    return np.all(np.diff(thd, axis=1) > -1e-12, axis=1)


def rays(model, K, D, pts_px):
    """Pixels (N,2) -> undistorted normalised rays (N,2); NaN rows = no valid inverse."""
    pts = np.asarray(pts_px, dtype=float).reshape(-1, 2)
    xd, yd = px_to_norm(K, pts[:, 0], pts[:, 1])
    x, y = undistort_norm(model, D, xd, yd)
    return np.stack([x, y], axis=1)


def pinhole_px(K, ray_xy):
    """Undistorted ray -> where an ideal pinhole camera with the same K puts it (the 'after
    undistortion' pixel for newK = K)."""
    K = np.asarray(K, dtype=float)
    r = np.asarray(ray_xy, dtype=float).reshape(-1, 2)
    return np.stack([K[0, 0] * r[:, 0] + K[0, 2], K[1, 1] * r[:, 1] + K[1, 2]], axis=1)


# ----------------------------------------------------------------------------- fitting

def view_errors(model, K, D, obj, img, rvecs, tvecs):
    """-> per-view RMS (V,), and the list of per-point error vectors."""
    per, errs = [], []
    for o, p, rv, tv in zip(obj, img, rvecs, tvecs):
        e = project(model, K, D, o, rv, tv) - np.asarray(p, float).reshape(-1, 2)
        errs.append(e)
        per.append(float(np.sqrt(np.mean(np.sum(e * e, axis=1)))))
    return np.array(per), errs


def overall_rms(errs):
    e = np.concatenate([x for x in errs], axis=0)
    return float(np.sqrt(np.mean(np.sum(e * e, axis=1))))


def fit(model, obj, img, size, K0=None):
    """Fit one model on views (obj: list of (N,3) mm, img: list of (N,2) px). -> dict or raises."""
    cv2 = _cv2()
    obj = [np.asarray(o, dtype=np.float64).reshape(-1, 3) for o in obj]
    img = [np.asarray(p, dtype=np.float64).reshape(-1, 2) for p in img]
    std_int = None
    if model == "fisheye":
        K = np.array(K0 if K0 is not None else _guess_K(size), dtype=np.float64)
        D = np.zeros((4, 1))
        flags = (_fisheye_flag(cv2, "CALIB_RECOMPUTE_EXTRINSIC") | _fisheye_flag(cv2, "CALIB_FIX_SKEW")
                 | _fisheye_flag(cv2, "CALIB_USE_INTRINSIC_GUESS"))
        crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-10)
        _, K, D, rvecs, tvecs = cv2.fisheye.calibrate(
            # (1, N, 3) / (1, N, 2): OpenCV 5.0's fisheye rejects the (N, 1, 3) layout 4.x took
            [o.reshape(1, -1, 3) for o in obj], [p.reshape(1, -1, 2) for p in img], tuple(size), K, D,
            flags=flags, criteria=crit)
    else:
        flags = cv2.CALIB_RATIONAL_MODEL if model == "rational" else 0
        crit = (cv2.TERM_CRITERIA_COUNT + cv2.TERM_CRITERIA_EPS, 200, 1e-10)
        out = cv2.calibrateCameraExtended([o.astype(np.float32) for o in obj], [p.astype(np.float32) for p in img],
                                          tuple(size), None, None, flags=flags, criteria=crit)
        _, K, D, rvecs, tvecs, std_i = out[0], out[1], out[2], out[3], out[4], out[5]
        std_int = np.asarray(std_i, float).ravel()
        D = np.asarray(D, float).ravel()[:MODEL_PARAMS[model]]
    K = np.asarray(K, float)
    D = np.asarray(D, float).ravel()
    rvecs = [np.asarray(r, float).reshape(3) for r in rvecs]
    tvecs = [np.asarray(t, float).reshape(3) for t in tvecs]
    per, errs = view_errors(model, K, D, obj, img, rvecs, tvecs)
    return {"model": model, "K": K, "D": D, "rvecs": rvecs, "tvecs": tvecs, "per_view": per,
            "rms": overall_rms(errs), "n_views": len(obj), "n_points": int(sum(len(o) for o in obj)),
            "std_intrinsics": std_int}


def _fisheye_flag(cv2, name):
    """OpenCV 4.x keeps the fisheye flags in cv2.fisheye; 5.0 moved them to the top level (with
    different values from 4.x, so never mix the two)."""
    v = getattr(cv2.fisheye, name, None)
    return v if v is not None else getattr(cv2, name)


def _guess_K(size):
    w, h = size
    f = 0.55 * w
    return np.array([[f, 0, (w - 1) / 2.0], [0, f, (h - 1) / 2.0], [0, 0, 1.0]])


def outlier_threshold(per_view):
    """Pre-registered rule: max(3 x median, median + 5 x 1.4826 x MAD)."""
    e = np.asarray(per_view, float)
    med = float(np.median(e))
    mad = float(np.median(np.abs(e - med)))
    return max(3 * med, med + 5 * 1.4826 * mad)


def fit_robust(model, obj, img, size, K0=None, rounds=3, min_views=4):
    """fit() + drop per-view outliers and refit, up to `rounds` times. Returns (fit, kept_idx, dropped)."""
    keep = list(range(len(obj)))
    dropped = []
    res = None
    for rnd in range(rounds + 1):
        res = fit(model, [obj[i] for i in keep], [img[i] for i in keep], size, K0)
        thr = outlier_threshold(res["per_view"])
        bad = [keep[j] for j, e in enumerate(res["per_view"]) if e > thr]
        res["outlier_threshold_px"] = thr
        if not bad or rnd == rounds or len(keep) - len(bad) < min_views:
            break
        for j, e in enumerate(res["per_view"]):
            if e > thr:
                dropped.append({"index": keep[j], "round": rnd + 1, "rms_px": float(e), "threshold_px": thr})
        keep = [i for i in keep if i not in bad]
    return res, keep, dropped


def fit_pose(model, K, D, obj, img):
    """Pose-only fit of one view with fixed intrinsics: IPPE on undistorted rays, then LM in pixels."""
    cv2 = _cv2()
    from scipy.optimize import least_squares
    obj = np.asarray(obj, float).reshape(-1, 3)
    img = np.asarray(img, float).reshape(-1, 2)
    r = rays(model, K, D, img)
    good = np.all(np.isfinite(r), axis=1)
    rv = tv = None
    if good.sum() >= 4:
        ok, rv, tv = cv2.solvePnP(obj[good].astype(np.float64), r[good].reshape(-1, 1, 2).astype(np.float64),
                                  np.eye(3), None, flags=cv2.SOLVEPNP_IPPE)
        if not ok:
            rv = tv = None
    if rv is None:
        ok, rv, tv = cv2.solvePnP(obj, img, np.asarray(K, float), None, flags=cv2.SOLVEPNP_ITERATIVE)
    x0 = np.concatenate([np.asarray(rv, float).ravel(), np.asarray(tv, float).ravel()])

    def resid(p):
        return (project(model, K, D, obj, p[:3], p[3:]) - img).ravel()

    sol = least_squares(resid, x0, method="lm", xtol=1e-12, ftol=1e-12, max_nfev=400)
    e = sol.fun.reshape(-1, 2)
    return sol.x[:3], sol.x[3:], e


def fold_assignment(n_views, folds, seed=0):
    rng = np.random.default_rng(seed)
    perm = rng.permutation(n_views)
    fold_of = np.empty(n_views, dtype=int)
    fold_of[perm] = np.arange(n_views) % folds
    return fold_of


def cross_validate(model, obj, img, size, folds=5, seed=0, K0=None, keep_fold_models=False):
    """k-fold by view. Outlier rejection runs on the TRAINING views only; every held-out view is
    scored (pose-only fit with the trained intrinsics). -> dict."""
    n = len(obj)
    fold_of = fold_assignment(n, folds, seed)
    sq_sum, n_pts = 0.0, 0
    per_view = np.full(n, np.nan)
    fold_models = {}
    for f in range(folds):
        train = [i for i in range(n) if fold_of[i] != f]
        test = [i for i in range(n) if fold_of[i] == f]
        if not test or len(train) < 4:
            continue
        res, _, _ = fit_robust(model, [obj[i] for i in train], [img[i] for i in train], size, K0)
        if keep_fold_models:
            fold_models[f] = {"K": res["K"], "D": res["D"]}
        for i in test:
            _, _, e = fit_pose(model, res["K"], res["D"], obj[i], img[i])
            s = float(np.sum(e * e))
            sq_sum += s
            n_pts += len(e)
            per_view[i] = math.sqrt(s / len(e))
    out = {"model": model, "folds": folds, "seed": seed, "fold_of": fold_of.tolist(),
           "held_out_rms": math.sqrt(sq_sum / n_pts) if n_pts else float("nan"),
           "held_out_per_view": per_view,
           "held_out_median_view": float(np.nanmedian(per_view)) if n_pts else float("nan")}
    if keep_fold_models:
        out["fold_models"] = fold_models
    return out


def choose_model(cv_by_model, tie_frac=0.02):
    """Lowest held-out RMS; within tie_frac of the best, the pre-registered preference order wins."""
    ok = {m: r["held_out_rms"] for m, r in cv_by_model.items() if r and np.isfinite(r["held_out_rms"])}
    if not ok:
        return None, "no model produced a finite held-out RMS"
    best = min(ok.values())
    tied = [m for m in MODEL_PREFERENCE if m in ok and ok[m] <= best * (1 + tie_frac)]
    pick = tied[0]
    why = (f"lowest held-out RMS {ok[pick]:.3f} px" if ok[pick] == best else
           f"held-out RMS {ok[pick]:.3f} px within {100 * tie_frac:.0f}% of the best ({best:.3f} px): "
           f"preference order {' > '.join(MODEL_PREFERENCE)}")
    return pick, why


# ----------------------------------------------------------------------------- bootstrap

def _boot_one(args):
    model, obj, img, size, K0, idx = args
    try:
        import cv2
        cv2.setNumThreads(1)
        r = fit(model, [obj[i] for i in idx], [img[i] for i in idx], size, K0)
        return {"K": r["K"], "D": r["D"], "rms": r["rms"]}
    except Exception as exc:          # a degenerate resample must not kill the run
        return {"error": str(exc)}


def bootstrap(model, obj, img, size, B=100, seed=0, K0=None, workers=1):
    """Resample views with replacement B times and refit (no outlier rejection inside). -> list."""
    rng = np.random.default_rng(seed)
    n = len(obj)
    jobs = [(model, obj, img, size, K0, rng.integers(0, n, n).tolist()) for _ in range(B)]
    if workers <= 1:
        return [_boot_one(j) for j in jobs]
    from concurrent.futures import ProcessPoolExecutor
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_boot_one, jobs, chunksize=max(1, B // (4 * workers))))


def ray_spread_px(model, Ks, Ds, pts_px, f_ref, pct=95):
    """Bootstrap ray uncertainty at each pixel: the pct-th percentile distance of the replicate
    rays from their median ray, in px at focal f_ref. A replicate that cannot invert a pixel makes
    that pixel's spread inf (the model does not know where that ray goes)."""
    R = np.stack([rays(model, K, D, pts_px) for K, D in zip(Ks, Ds)])   # (B, P, 2)
    bad = ~np.all(np.isfinite(R), axis=2)                                # (B, P)
    with np.errstate(invalid="ignore"):
        med = np.nanmedian(R, axis=0)
    d = f_ref * np.linalg.norm(R - med[None], axis=2)
    d = np.where(bad | ~np.isfinite(d), np.inf, d)
    return _pct_inf(d, pct)


def _pct_inf(d, pct):
    """Percentile over axis 0 that treats inf as 'worse than anything' (np.percentile turns
    inf - inf into nan)."""
    big = 1e12
    p = np.percentile(np.where(np.isfinite(d), d, big), pct, axis=0)
    return np.where(p >= big / 10, np.inf, p)


# Amendment A1 (docs/pool_calib_prereg_20261014.md): the unaligned ray spread above includes the
# principal-point <-> camera-rotation trade-off (a cx shift of d px moves EVERY ray by d px, centre
# included), so it measures cx/cy, which B5 already bounds. Undistortion is only defined up to a
# homography (newK + rectifying rotation), so the distortion SHAPE is judged after aligning each ray
# field to the reference with a homography fitted where the data are (inside the observed hull).

def fit_homography(src, dst):
    cv2 = _cv2()
    H, _ = cv2.findHomography(np.asarray(src, np.float64).reshape(-1, 1, 2),
                              np.asarray(dst, np.float64).reshape(-1, 1, 2), 0)
    return H


def apply_homography(H, pts):
    p = np.c_[np.asarray(pts, float).reshape(-1, 2), np.ones(len(np.asarray(pts).reshape(-1, 2)))] @ np.asarray(H).T
    with np.errstate(divide="ignore", invalid="ignore"):
        return p[:, :2] / p[:, 2:3]


def _aligned_dist(src_fit, dst_fit, src_probe, dst_probe, f_ref):
    ok = np.all(np.isfinite(src_fit), axis=1) & np.all(np.isfinite(dst_fit), axis=1)
    if ok.sum() < 8:
        return np.full(len(src_probe), np.inf)
    H = fit_homography(src_fit[ok], dst_fit[ok])
    if H is None:
        return np.full(len(src_probe), np.inf)
    d = f_ref * np.linalg.norm(apply_homography(H, src_probe) - dst_probe, axis=1)
    return np.where(np.isfinite(d), d, np.inf)


def aligned_ray_spread_px(model, Ks, Ds, K_ref, D_ref, fit_px, probe_px, f_ref, pct=95):
    """Bootstrap spread of the distortion SHAPE: each replicate's rays are homography-aligned to the
    reference model's rays over fit_px (the observed region), then compared at probe_px."""
    ref_fit = rays(model, K_ref, D_ref, fit_px)
    ref_probe = rays(model, K_ref, D_ref, probe_px)
    d = np.stack([_aligned_dist(rays(model, K, D, fit_px), ref_fit, rays(model, K, D, probe_px), ref_probe, f_ref)
                  for K, D in zip(Ks, Ds)])
    return _pct_inf(d, pct)


def aligned_model_disagreement_px(ref, others, fit_px, probe_px, f_ref):
    """Max over the other models of the homography-aligned ray distance to the reference model."""
    ref_fit = rays(ref["model"], ref["K"], ref["D"], fit_px)
    ref_probe = rays(ref["model"], ref["K"], ref["D"], probe_px)
    out = np.zeros(len(np.asarray(probe_px).reshape(-1, 2)))
    for f in others:
        d = _aligned_dist(rays(f["model"], f["K"], f["D"], fit_px), ref_fit,
                          rays(f["model"], f["K"], f["D"], probe_px), ref_probe, f_ref)
        out = np.maximum(out, d)
    return out


def model_disagreement_px(fits, pts_px, f_ref):
    """Max pairwise distance between the models' rays at each pixel, in px at f_ref (inf if any
    model cannot invert that pixel)."""
    R = [rays(f["model"], f["K"], f["D"], pts_px) for f in fits]
    out = np.zeros(len(np.asarray(pts_px).reshape(-1, 2)))
    for i in range(len(R)):
        for j in range(i + 1, len(R)):
            d = f_ref * np.linalg.norm(R[i] - R[j], axis=1)
            d = np.where(np.isfinite(d), d, np.inf)
            out = np.maximum(out, d)
    return out


def fov_deg(model, K, D, size):
    """Horizontal / vertical field of view from the rays of the edge midpoints (NaN if not invertible)."""
    w, h = size
    pts = np.array([[0, (h - 1) / 2], [w - 1, (h - 1) / 2], [(w - 1) / 2, 0], [(w - 1) / 2, h - 1]], float)
    r = rays(model, K, D, pts)
    hfov = math.degrees(math.atan(abs(r[0, 0])) + math.atan(abs(r[1, 0])))
    vfov = math.degrees(math.atan(abs(r[2, 1])) + math.atan(abs(r[3, 1])))
    return (hfov if math.isfinite(hfov) else None), (vfov if math.isfinite(vfov) else None)


# ----------------------------------------------------------------------------- straight lines

def line_straightness(pts):
    """Total-least-squares line through pts (N,2) -> (rms_px, max_px) perpendicular residual."""
    p = np.asarray(pts, float).reshape(-1, 2)
    p = p[np.all(np.isfinite(p), axis=1)]
    if len(p) < 3:
        return None
    c = p.mean(axis=0)
    _, _, vt = np.linalg.svd(p - c)
    n = vt[1]
    r = (p - c) @ n
    return float(np.sqrt(np.mean(r * r))), float(np.max(np.abs(r)))


def board_lines(ids, pts, inner_cols, inner_rows, min_pts=4):
    """Group ChArUco corners into board rows and columns (corner id = row * inner_cols + col).
    -> list of (kind, index, (N,2) points ordered along the line)."""
    ids = np.asarray(ids).ravel()
    pts = np.asarray(pts, float).reshape(-1, 2)
    rows, cols = ids // inner_cols, ids % inner_cols
    out = []
    for r in range(inner_rows):
        m = rows == r
        if m.sum() >= min_pts:
            o = np.argsort(cols[m])
            out.append(("row", r, pts[m][o]))
    for c in range(inner_cols):
        m = cols == c
        if m.sum() >= min_pts:
            o = np.argsort(rows[m])
            out.append(("col", c, pts[m][o]))
    return out

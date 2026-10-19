#!/usr/bin/env python3
# filename: pool_calib_figures.py
# description: Review artifacts for tools/pool_calib_pipeline.py: screen cut sheet, coverage heatmap, model comparison, bootstrap, ray-uncertainty map, one-page verdict, before/after composites.
"""
pool_calib_figures.py — pictures for the pool calibration pipeline (matplotlib Agg + OpenCV).

Every picture says what it is, which camera, and whether it is SCALED or 1:1. Colours: one blue
ramp for counts (coverage), one orange ramp for uncertainty, status colours only next to a
PASS / FAIL word (never colour alone). Coordinates on every axis are native sensor pixels.
"""
import math
import os

import numpy as np

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK2 = "#52514e"
MUTED = "#898781"
GRID = "#e1e0d9"
AXIS = "#c3c2b7"
BLUE = "#2a78d6"
ORANGE = "#eb6834"
STATUS = {"PASS": "#0ca30c", "WARN": "#fab219", "FAIL": "#d03b3b", "N/A": MUTED}   # N/A = bar not scored
BLUE_RAMP = ["#fcfcfb", "#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"]
ORANGE_RAMP = ["#fdf1ea", "#f9d2bd", "#f4ad8b", "#ec835a", "#d95926", "#a8441c", "#733013"]


def _plt():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
                         "axes.edgecolor": AXIS, "axes.labelcolor": INK2, "xtick.color": MUTED, "ytick.color": MUTED,
                         "text.color": INK, "axes.titlesize": 11, "axes.titleweight": "bold", "font.size": 9,
                         "font.family": "sans-serif", "axes.spines.top": False, "axes.spines.right": False})
    return plt


def _cmap(colors, name):
    from matplotlib.colors import LinearSegmentedColormap
    return LinearSegmentedColormap.from_list(name, colors)


# ----------------------------------------------------------------------------- screen sheet

def screen_sheet(frames, path, name, thumb_w=480, cols=6):
    """Every frame as a SCALED thumbnail: status bar (word + colour), corners drawn, first reason."""
    import cv2
    col = {"PASS": (12, 163, 12), "WARN": (25, 178, 250), "FAIL": (59, 59, 208)}
    tiles = []
    for f in frames:
        t = f.get("_thumb")
        s = f.get("_scale", 1.0)
        if t is None:
            tile = np.full((int(thumb_w * 9 / 16), thumb_w, 3), 40, np.uint8)
        else:
            tile = cv2.cvtColor(t, cv2.COLOR_GRAY2BGR)
            if f.get("_corners") is not None:
                c = (0, 220, 0) if f.get("in_set") and not f.get("outlier") else (0, 200, 255)
                for p in f["_corners"] * s:
                    cv2.circle(tile, (int(p[0]), int(p[1])), 2, c, -1)
        st = f.get("status", "FAIL")
        tag = st + (" in set" if f.get("in_set") and not f.get("outlier") else (" outlier" if f.get("outlier") else ""))
        cv2.rectangle(tile, (0, 0), (tile.shape[1], 44), (0, 0, 0), -1)
        cv2.rectangle(tile, (0, 0), (8, 44), col.get(st, (128, 128, 128)), -1)
        cv2.putText(tile, f"{f['stem'][-22:]}  {tag}", (14, 17), cv2.FONT_HERSHEY_SIMPLEX, 0.45,
                    col.get(st, (200, 200, 200)), 1, cv2.LINE_AA)
        reason = (f.get("reasons") or [""])[0]
        info = f"c{f.get('corners', 0)} " + (f"{f['distance_mm'] / 1000:.2f}m " if f.get("distance_mm") else "") + reason
        cv2.putText(tile, info[:62], (14, 36), cv2.FONT_HERSHEY_SIMPLEX, 0.38, (230, 230, 230), 1, cv2.LINE_AA)
        tiles.append(tile)
    if not tiles:
        return
    th = max(t.shape[0] for t in tiles)
    tiles = [np.vstack([t, np.zeros((th - t.shape[0], thumb_w, 3), np.uint8)]) if t.shape[0] < th else t for t in tiles]
    while len(tiles) % cols:
        tiles.append(np.zeros((th, thumb_w, 3), np.uint8))
    grid = np.vstack([np.hstack(tiles[i:i + cols]) for i in range(0, len(tiles), cols)])
    n = {k: sum(1 for f in frames if f.get("status") == k) for k in ("PASS", "WARN", "FAIL")}
    used = sum(1 for f in frames if f.get("in_set") and not f.get("outlier"))
    head = np.zeros((70, grid.shape[1], 3), np.uint8)
    cv2.putText(head, f"{name}: per-frame screen  PASS {n['PASS']}  WARN {n['WARN']}  FAIL {n['FAIL']}  "
                      f"-> {used} views calibrate", (12, 28), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (240, 240, 240), 1, cv2.LINE_AA)
    cv2.putText(head, f"thumbnails SCALED to {thumb_w} px wide (not 1:1); green dots = corners used, "
                      f"yellow = detected but not used (FAIL / duplicate / outlier); c = ChArUco corners",
                (12, 56), cv2.FONT_HERSHEY_SIMPLEX, 0.55, (200, 200, 200), 1, cv2.LINE_AA)
    cv2.imwrite(path, np.vstack([head, grid]), [cv2.IMWRITE_JPEG_QUALITY, 85])


# ----------------------------------------------------------------------------- coverage

def _draw_coverage(ax, pts, cov, size, crop=None, title=None):
    from matplotlib.patches import Rectangle
    w, h = size
    gx, gy = cov["grid"]
    H, _, _ = np.histogram2d(pts[:, 1], pts[:, 0], bins=[36, 64], range=[[0, h], [0, w]])
    vmax = max(1.0, float(H.max()))
    im = ax.imshow(H, extent=[0, w, h, 0], cmap=_cmap(BLUE_RAMP, "cov"), vmin=0, vmax=vmax,
                   interpolation="nearest", aspect="equal")
    for i in range(1, gx):
        ax.axvline(i * w / gx, color=AXIS, lw=0.8)
    for j in range(1, gy):
        ax.axhline(j * h / gy, color=AXIS, lw=0.8)
    counts = np.array(cov["counts"])
    covered = np.array(cov["covered"])
    for r in range(gy):
        for c in range(gx):
            x0, y0 = c * w / gx, r * h / gy
            corner = (r in (0, gy - 1)) and (c in (0, gx - 1))
            if not covered[r, c]:
                ax.add_patch(Rectangle((x0, y0), w / gx, h / gy, fill=False, hatch="///", ec=AXIS, lw=0))
            if corner and not covered[r, c]:
                ax.add_patch(Rectangle((x0 + 6, y0 + 6), w / gx - 12, h / gy - 12, fill=False, ec=STATUS["FAIL"], lw=2))
            label = f"{counts[r, c]}"
            ax.text(x0 + 25, y0 + 95, label, fontsize=7, color=INK2 if covered[r, c] else STATUS["FAIL"])
    if crop:
        x, y, cw, ch = crop
        ax.add_patch(Rectangle((x, y), cw, ch, fill=False, ec=INK, lw=1.2, ls="--"))
        ax.text(x + 10, y + ch - 30, f"production crop {cw}×{ch}", fontsize=7, color=INK)
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_xlabel("x (native px)")
    ax.set_ylabel("y (native px)")
    ax.set_title(title or "")
    return im


def coverage_figure(pts, cov, size, path, name):
    plt = _plt()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(15, 5.2), gridspec_kw={"width_ratios": [2.4, 1]})
    nc = sum(cov["corner_cells_covered"].values())
    im = _draw_coverage(a1, pts, cov, size, cov.get("crop", {}).get("xywh"),
                        f"{name}: where the board corners landed ({cov['n_obs']} corners, used views)\n"
                        f"{100 * cov['covered_frac']:.0f}% of {cov['grid'][0]}×{cov['grid'][1]} cells covered "
                        f"(≥{cov['min_obs_per_cell']} corners); corner cells covered {nc}/4")
    cb = fig.colorbar(im, ax=a1, fraction=0.025, pad=0.01)
    cb.set_label("corners per bin (64×36 bins)")
    w, h = size
    c0 = np.array([(w - 1) / 2.0, (h - 1) / 2.0])
    rn = np.linalg.norm(pts - c0, axis=1) / math.hypot(w / 2.0, h / 2.0)
    a2.hist(rn, bins=np.linspace(0, 1, 26), color=BLUE, edgecolor=SURFACE, lw=2)
    a2.axvline(1.0, color=INK2, lw=1)
    a2.text(0.99, a2.get_ylim()[1] * 0.95, "frame corner", ha="right", va="top", fontsize=8, color=INK2)
    if cov.get("crop"):
        cr = cov["crop"]["corner_radial_norm"]
        a2.axvline(cr, color=INK2, lw=1, ls="--")
        a2.text(cr + 0.01, a2.get_ylim()[1] * 0.85, "crop corner", fontsize=8, color=INK2)
    a2.set_xlim(0, 1.02)
    a2.set_xlabel("distance from frame centre / half-diagonal")
    a2.set_ylabel("corner observations")
    a2.set_title(f"radial reach: max {cov['radial_norm']['max']:.2f}, "
                 f"{100 * cov['radial_norm']['frac_gt_0.6']:.0f}% beyond 0.6")
    a2.grid(axis="y", color=GRID, lw=0.6)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- models

def models_figure(cal, in_set, path, name):
    plt = _plt()
    fig, (a1, a2) = plt.subplots(1, 2, figsize=(13, 4.6))
    models = [m for m in ("standard", "rational", "fisheye") if m in cal["fits"]]
    tr = [cal["fits"][m]["rms"] for m in models]
    ho = [cal["cv"][m]["held_out_rms"] if m in cal["cv"] else np.nan for m in models]
    x = np.arange(len(models))
    b1 = a1.bar(x - 0.2, tr, 0.38, color=BLUE, label="training RMS")
    b2 = a1.bar(x + 0.2, ho, 0.38, color=ORANGE, label="held-out RMS (5-fold)")
    for bars, vals in ((b1, tr), (b2, ho)):
        for b, v in zip(bars, vals):
            if np.isfinite(v):
                a1.text(b.get_x() + b.get_width() / 2, v, f"{v:.3f}", ha="center", va="bottom", fontsize=8, color=INK2)
    a1.axhline(1.0, color=INK2, lw=1, ls="--")
    a1.text(len(models) - 0.5, 1.0, "bar B2: held-out < 1 px", ha="right", va="bottom", fontsize=8, color=INK2)
    a1.set_xticks(x, [m + (" ← chosen" if m == cal["chosen"] else "") for m in models])
    a1.set_ylabel("reprojection RMS (native px)")
    a1.set_title(f"{name}: models compared on views they did NOT see")
    a1.legend(frameon=False, loc="upper left")
    a1.grid(axis="y", color=GRID, lw=0.6)
    ch = cal["chosen"]
    hv = np.asarray(cal["cv"][ch]["held_out_per_view"], float)
    dist = np.array([f.get("distance_mm") or np.nan for f in in_set]) / 1000
    out = np.array([bool(f.get("outlier")) for f in in_set])
    a2.scatter(dist[~out], hv[~out], s=18, color=BLUE, label="used view")
    if out.any():
        a2.scatter(dist[out], hv[out], s=30, marker="x", color=STATUS["FAIL"], label="outlier view (dropped)")
    a2.set_xlabel("board distance (m, from the fitted pose)")
    a2.set_ylabel(f"held-out RMS per view, {ch} (px)")
    a2.set_title("per-view held-out error")
    a2.legend(frameon=False)
    a2.grid(color=GRID, lw=0.6)
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


def bootstrap_figure(cal, path, name):
    plt = _plt()
    good = [b for b in cal["bootstrap"] if "error" not in b]
    fig, axs = plt.subplots(1, 4, figsize=(15, 3.4))
    vals = {"fx": [b["K"][0, 0] for b in good], "fy": [b["K"][1, 1] for b in good],
            "cx": [b["K"][0, 2] for b in good], "cy": [b["K"][1, 2] for b in good]}
    for ax, (k, v) in zip(axs, vals.items()):
        v = np.asarray(v)
        if len(v):
            ax.hist(v, bins=20, color=BLUE, edgecolor=SURFACE, lw=1.5)
            ax.axvline(v.mean(), color=INK, lw=1)
            sd = v.std(ddof=1) if len(v) > 1 else 0.0
            rel = f" ({100 * sd / v.mean():.2f}%)" if k in ("fx", "fy") else ""
            ax.set_title(f"{k}: {v.mean():.1f} ± {sd:.1f} px{rel}")
        ax.set_xlabel(f"{k} (native px)")
        ax.grid(axis="y", color=GRID, lw=0.6)
    fig.suptitle(f"{name}: bootstrap of the {cal['chosen']} model ({len(good)} replicates, views resampled)",
                 fontweight="bold")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- uncertainty

def _draw_uncertainty(ax, cal, pts, size, crop, bars):
    from matplotlib.colors import LogNorm
    from matplotlib.patches import Rectangle
    w, h = size
    um = cal["uncertainty_map"]
    S = np.asarray(um["spread_px"], float) if um["spread_px"] is not None else None
    im = None
    if S is not None:
        Sc = np.clip(np.where(np.isfinite(S), S, 100.0), 0.05, 100.0)
        im = ax.imshow(Sc, extent=[0, w, h, 0], cmap=_cmap(ORANGE_RAMP, "unc"), norm=LogNorm(0.05, 100),
                       interpolation="bilinear", aspect="equal")
        X, Y = np.meshgrid(um["xs"], um["ys"])
        cs = ax.contour(X, Y, Sc, levels=[bars["B5c_crop_ray_px"], bars["B4_edge_ray_px"], 10.0],
                        colors=[INK2, INK, INK], linewidths=[0.8, 1.2, 0.8], linestyles=["--", "-", ":"])
        ax.clabel(cs, fmt=lambda v: f"{v:g} px", fontsize=7)
    sub = pts[:: max(1, len(pts) // 3000)]
    ax.scatter(sub[:, 0], sub[:, 1], s=1.5, color=BLUE, alpha=0.6, lw=0, label="corner observations")
    if crop:
        x, y, cw, ch = crop
        ax.add_patch(Rectangle((x, y), cw, ch, fill=False, ec=INK, lw=1.2, ls="--"))
        ax.text(x + 10, y + ch - 30, "production crop", fontsize=7, color=INK)
    for p in cal["probes"]:
        if p["group"] == "frame" and p["name"] in ("TL", "TR", "BL", "BR"):
            v = p["ray_uncertainty_px"]
            txt = "∞" if not np.isfinite(v) else f"{v:.1f}"
            ha = "left" if p["x"] < w / 2 else "right"
            va = "top" if p["y"] < h / 2 else "bottom"
            ax.text(p["x"] + (40 if ha == "left" else -40), p["y"] + (40 if va == "top" else -40),
                    f"±{txt} px", ha=ha, va=va, fontsize=8, color=INK, fontweight="bold")
    ax.set_xlim(0, w)
    ax.set_ylim(h, 0)
    ax.set_xlabel("x (native px)")
    ax.set_ylabel("y (native px)")
    return im


def uncertainty_figure(cal, pts, size, crop, path, name, bars):
    plt = _plt()
    fig, ax = plt.subplots(figsize=(12, 6.6))
    im = _draw_uncertainty(ax, cal, pts, size, crop, bars)
    if im is not None:
        cb = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.01)
        cb.set_label("distortion-shape uncertainty, bootstrap 95% (px, homography-aligned)")
    ax.set_title(f"{name}: how well the {cal['chosen']} model knows the distortion at each pixel (amendment A1: aligned)\n"
                 f"dark = extrapolating (no data); contours at {bars['B5c_crop_ray_px']:g} px (crop bar), "
                 f"{bars['B4_edge_ray_px']:g} px (edge bar), 10 px; blue dots = observed corners")
    fig.tight_layout()
    fig.savefig(path, dpi=110)
    plt.close(fig)


# ----------------------------------------------------------------------------- verdict sheet

def verdict_sheet(name, pts, cov, cal, bars_out, v_full, v_crop, size, crop, path, bars):
    """One page for review: coverage | distortion-shape uncertainty, then the bars table."""
    plt = _plt()
    import textwrap
    fig = plt.figure(figsize=(17, 11))
    gs = fig.add_gridspec(2, 2, height_ratios=[1, 0.62], hspace=0.22, wspace=0.12)
    a1 = fig.add_subplot(gs[0, 0])
    nc = sum(cov["corner_cells_covered"].values())
    _draw_coverage(a1, pts, cov, size, crop, f"Where the board corners landed: {100 * cov['covered_frac']:.0f}% of 8×5 "
                                              f"cells, corner cells {nc}/4 (hatched = empty)")
    a2 = fig.add_subplot(gs[0, 1])
    im = _draw_uncertainty(a2, cal, pts, size, crop, bars)
    if im is not None:
        cb = fig.colorbar(im, ax=a2, fraction=0.03, pad=0.01)
        cb.set_label("distortion uncertainty, 95% (px)")
    a2.set_title("How well the distortion is known (dark = no data, model guessing)")
    a3 = fig.add_subplot(gs[1, :])
    a3.axis("off")
    rows = [[b["status"], b["bar"], textwrap.fill(_short(b["value"]), 46), textwrap.fill(b["rule"], 70)]
            for b in bars_out]
    t = a3.table(cellText=rows, colLabels=["status", "bar", "value", "pre-registered rule"], loc="upper left",
                 cellLoc="left", colWidths=[0.06, 0.14, 0.33, 0.47])
    t.auto_set_font_size(False)
    t.set_fontsize(9)
    for (r, c), cell in t.get_celld().items():
        cell.set_edgecolor(GRID)
        cell.set_height(0.13 if r else 0.09)
        if r == 0:
            cell.set_text_props(fontweight="bold", color=INK2)
        elif c == 0:
            cell.set_text_props(color=STATUS.get(cell.get_text().get_text(), INK), fontweight="bold")
    K = cal["final"]["K"]
    ho = cal["cv"][cal["chosen"]]["held_out_rms"]
    bars_src = ("Pre-registered bars (docs/pool_calib_prereg_20261014.md)" if crop else    # crop None = not native
                "IMX708-pixel bars, not pre-registered for this sensor")     # same length: fits the page
    a3.set_title(f"{bars_src} — model {cal['chosen']} chosen by held-out "
                 f"error ({ho:.3f} px): fx {K[0, 0]:.0f}  fy {K[1, 1]:.0f}  cx {K[0, 2]:.0f}  cy {K[1, 2]:.0f}",
                 loc="left")
    title, subtitle = verdict_titles(name, v_full, v_crop, size, crop)
    fig.suptitle(title, fontsize=15, fontweight="bold", color=INK, x=0.01, ha="left")
    fig.text(0.01, 0.945, subtitle, fontsize=9, color=INK2)
    fig.savefig(path, dpi=100)
    plt.close(fig)


def verdict_titles(name, v_full, v_crop, size, crop):
    """Verdict-sheet title + subtitle from the ACTUAL frame size. crop None = frames are not
    --native-size (e.g. the OpenMV N6 1280x800): no crop is derived or drawn, so it reads n/a."""
    w, h = size
    if crop:
        crop_txt = f"production {crop[2]}×{crop[3]} crop: {v_crop}"
        sub = f"All axes in native {w}×{h} sensor pixels. Scaled figure, not 1:1. Dashed box = production crop."
    else:
        crop_txt = "production crop: n/a"
        sub = (f"All axes in {w}×{h} frame pixels (not --native-size). Scaled figure, not 1:1. "
               f"No production crop derived or drawn (B5c N/A). IMX708-pixel bars, not pre-registered for "
               f"this sensor.")
    return f"{name} underwater calibration — full-frame intrinsics: {v_full}   ·   {crop_txt}", sub


def _short(v):
    if isinstance(v, dict) and "worst_ray_uncertainty_px" in v:          # B4 / B5c
        f = lambda x: "∞" if x is None or not np.isfinite(x) else f"{x:.2g}"  # noqa: E731
        miss = f"; models missing: {', '.join(v['models_missing'])}" if v.get("models_missing") else ""
        if v.get("models_reading") == "literal":     # d33: lead with the values that decide the bar
            return (f"ray {f(v['worst_ray_uncertainty_px'])} px, models {f(v.get('literal_models_px'))} px literal "
                    f"(aligned models {f(v['worst_model_disagreement_px'])}){miss}")
        return (f"ray {f(v['worst_ray_uncertainty_px'])} px, models {f(v['worst_model_disagreement_px'])} px "
                f"(literal {f(v.get('literal_ray_px'))} / {f(v.get('literal_models_px'))}){miss}")
    if isinstance(v, dict):
        parts = []
        for k, x in v.items():
            if isinstance(x, dict):
                parts.append(f"{sum(bool(y) for y in x.values())}/{len(x)} {k.replace('_', ' ')}")
            elif isinstance(x, float):
                parts.append(f"{k.replace('_', ' ')} {'∞' if not np.isfinite(x) else f'{x:.3g}'}")
            else:
                parts.append(f"{k.replace('_', ' ')} {x}")
        return "; ".join(parts)
    if isinstance(v, float):
        return "∞" if not np.isfinite(v) else f"{v:.3g}"
    return str(v)


# ----------------------------------------------------------------------------- before / after

def _overlay(img, rows, which, crop, grid_step=None, hull=None):
    """Draw a straight reference grid, the crop box, and each board row/column (yellow polyline
    through the corners, magenta straight chord end to end: any gap = a bent line)."""
    import cv2
    out = img.copy()
    h, w = out.shape[:2]
    th = max(2, int(round(w / 1500)))
    step_x, step_y = (grid_step or (w / 8, h / 5))
    ov = out.copy()
    x = step_x
    while x < w - 1:
        cv2.line(ov, (int(x), 0), (int(x), h - 1), (235, 235, 235), th)
        x += step_x
    y = step_y
    while y < h - 1:
        cv2.line(ov, (0, int(y)), (w - 1, int(y)), (235, 235, 235), th)
        y += step_y
    out = cv2.addWeighted(ov, 0.45, out, 0.55, 0)
    if crop:
        x0, y0, cw, ch = crop
        cv2.rectangle(out, (x0, y0), (x0 + cw - 1, y0 + ch - 1), (255, 255, 0), th)
    if hull is not None and len(hull) >= 3:
        # A hull point the model cannot map (it folds there) is NaN after undistortion; casting NaN to int
        # drew stray lines to the frame corner. Draw only the segments between two mappable neighbours.
        hp = np.asarray(hull, float).reshape(-1, 2)
        ok = np.all(np.isfinite(hp), axis=1)
        for i in range(len(hp)):
            j = (i + 1) % len(hp)
            if ok[i] and ok[j]:
                cv2.line(out, (int(hp[i, 0]), int(hp[i, 1])), (int(hp[j, 0]), int(hp[j, 1])), (80, 220, 80), th)
    for r in rows:
        p = np.asarray(r["_before_pts" if which == "before" else "_after_pts"], float)
        p = p[np.all(np.isfinite(p), axis=1)]
        if len(p) < 2:
            continue
        cv2.line(out, tuple(int(v) for v in p[0]), tuple(int(v) for v in p[-1]), (255, 0, 255), th)
        cv2.polylines(out, [p.astype(np.int32).reshape(-1, 1, 2)], False, (0, 230, 255), th)
    return out


def _label(img, text_lines, scale=1.0):
    import cv2
    h = int(34 * scale) * len(text_lines) + 10
    bar = np.zeros((h, img.shape[1], 3), np.uint8)
    for i, t in enumerate(text_lines):
        cv2.putText(bar, t, (12, int(28 * scale) + i * int(34 * scale)), cv2.FONT_HERSHEY_SIMPLEX, 0.8 * scale,
                    (240, 240, 240), max(1, int(round(1.5 * scale))), cv2.LINE_AA)
    return np.vstack([bar, img])


def before_after_images(before, after, rows, crop, out_dir, prefix, title, straight=None, hull=None, hull_after=None):
    """-> dict of written files. Full frame side by side SCALED to 1/3; production crop side by
    side at 1:1; plus single images for a slider (full at 1/2 scale, crop 1:1)."""
    import cv2
    files = {}
    h, w = before.shape[:2]
    ob = _overlay(before, rows, "before", crop, hull=hull)
    oa = _overlay(after, rows, "after", crop, hull=hull_after)
    s = 1 / 3.0
    sz = (int(round(w * s)), int(round(h * s)))
    sb = cv2.resize(ob, sz, interpolation=cv2.INTER_AREA)
    sa = cv2.resize(oa, sz, interpolation=cv2.INTER_AREA)
    st = ""
    if straight and straight.get("before_rms_px"):
        st = (f"board-line straightness RMS (median over rows/cols): {straight['before_rms_px']['median']:.2f} px raw -> "
              f"{straight['after_rms_px']['median']:.2f} px undistorted; "
              f"worst max {straight['before_max_px']['max']:.1f} -> "
              f"{straight['after_max_px']['max']:.1f} px")
    lb, la = _label(sb, ["BEFORE: raw JPEG"]), _label(sa, ["AFTER: undistorted (newK = K)"])
    gap = np.full((lb.shape[0], 12, 3), 255, np.uint8)
    sbs = _label(np.hstack([lb, gap, la]),
                 [title, f"FULL FRAME {w}x{h} SCALED to {100 * s:.0f}% (not 1:1). Grey grid = straight reference "
                         f"(8x5 coverage cells); cyan box = production crop; green outline = where calibration "
                         f"corners landed (outside it the model extrapolates); yellow = board row through the "
                         f"corners, magenta = straight chord",
                  st] if st else
                 [title, f"FULL FRAME {w}x{h} SCALED to {100 * s:.0f}% (not 1:1). Grey grid = straight reference; "
                         f"cyan box = production crop; green outline = where calibration corners landed (outside it the "
                         f"model extrapolates)"], scale=0.9)
    p = os.path.join(out_dir, f"{prefix}_full_sbs.jpg")
    cv2.imwrite(p, sbs, [cv2.IMWRITE_JPEG_QUALITY, 88])
    files["full_side_by_side"] = p
    # single images for a slider page: LOSSLESS WebP so the page adds no artefacts of its own;
    # full frame at 50 % (a lossless 12 MP frame is ~20 MB), crop at 1:1
    half = (w // 2, h // 2)
    for tag, im in (("before", ob), ("after", oa)):
        p = os.path.join(out_dir, f"{prefix}_full_{tag}_50pct.webp")
        cv2.imwrite(p, cv2.resize(im, half, interpolation=cv2.INTER_AREA), [cv2.IMWRITE_WEBP_QUALITY, 101])
        files[f"full_{tag}_50pct"] = p
    if crop:
        x0, y0, cw, ch = crop
        local = []
        for r in rows:
            local.append({"_before_pts": np.asarray(r["_before_pts"]) - [x0, y0],
                          "_after_pts": np.asarray(r["_after_pts"]) - [x0, y0]})
        hb = None if hull is None else np.asarray(hull, float) - [x0, y0]
        ha = None if hull_after is None else np.asarray(hull_after, float) - [x0, y0]
        cb = _overlay(before[y0:y0 + ch, x0:x0 + cw], local, "before", None, grid_step=(cw / 8, ch / 4.5), hull=hb)
        ca = _overlay(after[y0:y0 + ch, x0:x0 + cw], local, "after", None, grid_step=(cw / 8, ch / 4.5), hull=ha)
        lb, la = _label(cb, ["BEFORE: raw"]), _label(ca, ["AFTER: undistorted"])
        gap = np.full((lb.shape[0], 12, 3), 255, np.uint8)
        sbs = _label(np.hstack([lb, gap, la]),
                     [title, f"PRODUCTION CROP [{x0},{y0},{cw},{ch}] at 1:1 native px (crop of the undistorted full frame)"],
                     scale=0.9)
        p = os.path.join(out_dir, f"{prefix}_crop_sbs_1to1.jpg")
        cv2.imwrite(p, sbs, [cv2.IMWRITE_JPEG_QUALITY, 90])
        files["crop_side_by_side_1to1"] = p
        for tag, im in (("before", cb), ("after", ca)):
            p = os.path.join(out_dir, f"{prefix}_crop_{tag}_1to1.webp")
            cv2.imwrite(p, im, [cv2.IMWRITE_WEBP_QUALITY, 101])
            files[f"crop_{tag}_1to1"] = p
    return files

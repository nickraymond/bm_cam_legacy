#!/usr/bin/env python3
"""
BM Reference Card Quality Analyzer v2

Detects AprilTags, estimates/rectifies the Nereus reference card, computes
repeatable quality metrics, and builds a card-crop contact sheet.

Install:
  python3 -m pip install opencv-contrib-python pillow numpy

Example:
  python3 bm_reference_card_quality_v2.py \
    --input ~/Downloads/bm_fixed_crop_resolution_sweep/<run>/ \
    --output ~/Downloads/card_quality_v2
"""
from __future__ import annotations
import argparse, csv, json, math, os, sys, traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple
import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

DEFAULT_CORNER_MAP = {"tl": None, "tr": 1, "bl": 2, "br": 3}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".bmp", ".webp"}

@dataclass
class TagMetric:
    tag_id: int
    center_x: float
    center_y: float
    side_px_mean: float
    side_px_min: float
    area_px: float
    laplacian_var: float
    tenengrad: float
    contrast_p95_p05: float

def parse_corner_map(s: str) -> Dict[str, Optional[int]]:
    out = dict(DEFAULT_CORNER_MAP)
    if not s:
        return out
    for part in s.split(','):
        if not part.strip():
            continue
        k, v = part.split(':', 1)
        k, v = k.strip().lower(), v.strip().lower()
        if k not in {"tl", "tr", "bl", "br"}:
            raise ValueError(f"Invalid corner key {k!r}")
        out[k] = None if v in {"none", "null", ""} else int(v)
    return out

def load_image_bgr(path: Path) -> np.ndarray:
    data = np.fromfile(str(path), dtype=np.uint8)
    img = cv2.imdecode(data, cv2.IMREAD_COLOR)
    if img is None:
        raise ValueError(f"Unable to read image: {path}")
    return img

def pil_font(size: int, bold: bool = False):
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf" if bold else "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf" if bold else "/Library/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for c in candidates:
        if Path(c).exists():
            return ImageFont.truetype(c, size)
    return ImageFont.load_default()

def variance_laplacian(gray: np.ndarray) -> float:
    return 0.0 if gray.size == 0 else float(cv2.Laplacian(gray, cv2.CV_64F).var())

def tenengrad(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    gx = cv2.Sobel(gray, cv2.CV_64F, 1, 0, ksize=3)
    gy = cv2.Sobel(gray, cv2.CV_64F, 0, 1, ksize=3)
    return float(np.mean(gx * gx + gy * gy))

def contrast_p95_p05(gray: np.ndarray) -> float:
    if gray.size == 0:
        return 0.0
    p05, p95 = np.percentile(gray, [5, 95])
    return float(p95 - p05)

def polygon_area(pts: np.ndarray) -> float:
    return float(abs(cv2.contourArea(np.asarray(pts, dtype=np.float32).reshape(-1, 2))))

def tag_side_lengths(pts: np.ndarray) -> List[float]:
    pts = np.asarray(pts, dtype=np.float32).reshape(4, 2)
    return [float(np.linalg.norm(pts[i] - pts[(i + 1) % 4])) for i in range(4)]

def safe_crop(gray: np.ndarray, pts: np.ndarray, pad: int = 4) -> np.ndarray:
    h, w = gray.shape[:2]
    pts = np.asarray(pts).reshape(-1, 2)
    x0 = max(0, int(np.floor(np.min(pts[:, 0]))) - pad)
    y0 = max(0, int(np.floor(np.min(pts[:, 1]))) - pad)
    x1 = min(w, int(np.ceil(np.max(pts[:, 0]))) + pad)
    y1 = min(h, int(np.ceil(np.max(pts[:, 1]))) + pad)
    return gray[y0:y1, x0:x1] if x1 > x0 and y1 > y0 else gray[0:0, 0:0]

def detect_tags(img_bgr: np.ndarray, tag_family: str, scales: Iterable[float]):
    if not hasattr(cv2, 'aruco'):
        raise RuntimeError('OpenCV aruco module unavailable; install opencv-contrib-python')
    if not hasattr(cv2.aruco, tag_family):
        candidates = [x for x in dir(cv2.aruco) if x.lower() == tag_family.lower()]
        if not candidates:
            raise RuntimeError(f'No OpenCV aruco dictionary named {tag_family}')
        tag_family = candidates[0]
    dictionary = cv2.aruco.getPredefinedDictionary(getattr(cv2.aruco, tag_family))
    gray0 = cv2.cvtColor(img_bgr, cv2.COLOR_BGR2GRAY)
    best = (0, 1.0, [], None, [])
    for scale in scales:
        gray = gray0 if scale == 1 else cv2.resize(gray0, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        params = cv2.aruco.DetectorParameters()
        params.cornerRefinementMethod = cv2.aruco.CORNER_REFINE_APRILTAG
        detector = cv2.aruco.ArucoDetector(dictionary, params)
        corners, ids, rejected = detector.detectMarkers(gray)
        n = 0 if ids is None else len(ids)
        if n > best[0] or (n == best[0] and scale < best[1]):
            best = (n, scale, corners, ids, rejected)
    n, scale, corners, ids, rejected = best
    metrics, corners_by_id = [], {}
    if ids is None or len(ids) == 0:
        return metrics, corners_by_id, scale, len(rejected or [])
    for tag_id, corner in zip(ids.flatten().tolist(), corners):
        pts = np.asarray(corner, dtype=np.float32).reshape(4, 2) / float(scale)
        sides = tag_side_lengths(pts)
        center = pts.mean(axis=0)
        local = safe_crop(gray0, pts, pad=4)
        metrics.append(TagMetric(int(tag_id), float(center[0]), float(center[1]), float(np.mean(sides)), float(np.min(sides)), polygon_area(pts), variance_laplacian(local), tenengrad(local), contrast_p95_p05(local)))
        corners_by_id[int(tag_id)] = pts
    return metrics, corners_by_id, scale, len(rejected or [])

def infer_card_corners_from_tags(corners_by_id, corner_map):
    centers, status = {}, {}
    for name, tag_id in corner_map.items():
        if tag_id is None:
            status[name] = 'not_configured'
        elif tag_id in corners_by_id:
            centers[name] = np.asarray(corners_by_id[tag_id]).reshape(4, 2).mean(axis=0)
            status[name] = 'detected'
        else:
            status[name] = 'missing'
    if len(centers) < 3:
        return None, status, float('nan')
    if 'tl' not in centers and {'tr','bl','br'} <= set(centers):
        centers['tl'] = centers['tr'] + centers['bl'] - centers['br']; status['tl'] = 'inferred'
    if 'tr' not in centers and {'tl','br','bl'} <= set(centers):
        centers['tr'] = centers['tl'] + centers['br'] - centers['bl']; status['tr'] = 'inferred'
    if 'bl' not in centers and {'tl','br','tr'} <= set(centers):
        centers['bl'] = centers['tl'] + centers['br'] - centers['tr']; status['bl'] = 'inferred'
    if 'br' not in centers and {'tr','bl','tl'} <= set(centers):
        centers['br'] = centers['tr'] + centers['bl'] - centers['tl']; status['br'] = 'inferred'
    if not {'tl','tr','br','bl'} <= set(centers):
        return None, status, float('nan')
    quad = np.vstack([centers['tl'], centers['tr'], centers['br'], centers['bl']]).astype(np.float32)
    residual = float(np.linalg.norm((quad[0] + quad[2]) / 2.0 - (quad[1] + quad[3]) / 2.0))
    return quad, status, residual

def expand_quad(quad: np.ndarray, scale_x=1.25, scale_y=2.0) -> np.ndarray:
    q = np.asarray(quad, dtype=np.float32).reshape(4, 2)
    center = q.mean(axis=0)
    tl, tr, br, bl = q
    x_axis = ((tr - tl) + (br - bl)) / 2.0
    y_axis = ((bl - tl) + (br - tr)) / 2.0
    hx, hy = x_axis / 2.0 * scale_x, y_axis / 2.0 * scale_y
    return np.vstack([center - hx - hy, center + hx - hy, center + hx + hy, center - hx + hy]).astype(np.float32)

def rectify_quad(img_bgr, quad, out_w, out_h):
    dst = np.array([[0,0], [out_w-1,0], [out_w-1,out_h-1], [0,out_h-1]], dtype=np.float32)
    H = cv2.getPerspectiveTransform(np.asarray(quad, dtype=np.float32), dst)
    return cv2.warpPerspective(img_bgr, H, (out_w, out_h), flags=cv2.INTER_CUBIC)

def compute_card_metrics(rect_bgr):
    if rect_bgr is None or rect_bgr.size == 0:
        return {}
    gray = cv2.cvtColor(rect_bgr, cv2.COLOR_BGR2GRAY)
    return {
        'card_rect_width': int(gray.shape[1]),
        'card_rect_height': int(gray.shape[0]),
        'card_laplacian_var': round(variance_laplacian(gray), 4),
        'card_tenengrad': round(tenengrad(gray), 4),
        'card_contrast_p95_p05': round(contrast_p95_p05(gray), 4),
        'card_clipped_dark_frac': round(float(np.mean(gray <= 3)), 6),
        'card_clipped_bright_frac': round(float(np.mean(gray >= 252)), 6),
    }

def compare_to_reference(rect_bgr, ref_bgr):
    if rect_bgr is None or ref_bgr is None:
        return {}
    h, w = ref_bgr.shape[:2]
    test = cv2.resize(rect_bgr, (w, h), interpolation=cv2.INTER_CUBIC)
    ref, tst = ref_bgr.astype(np.float32), test.astype(np.float32)
    mse = float(np.mean((ref - tst) ** 2))
    psnr = 99.0 if mse <= 1e-9 else float(20 * math.log10(255.0 / math.sqrt(mse)))
    ref_g = cv2.cvtColor(ref_bgr, cv2.COLOR_BGR2GRAY).astype(np.float32)
    tst_g = cv2.cvtColor(test, cv2.COLOR_BGR2GRAY).astype(np.float32)
    ref_lap = cv2.Laplacian(ref_g, cv2.CV_32F).reshape(-1)
    tst_lap = cv2.Laplacian(tst_g, cv2.CV_32F).reshape(-1)
    denom = float(np.linalg.norm(ref_lap) * np.linalg.norm(tst_lap))
    corr = float(np.dot(ref_lap, tst_lap) / denom) if denom > 1e-9 else 0.0
    return {'ref_mse_rgb': round(mse, 4), 'ref_psnr_rgb': round(psnr, 4), 'ref_laplacian_corr': round(corr, 6)}

def find_images(input_path: Path) -> List[Path]:
    if input_path.is_file():
        return [input_path]
    imgs = []
    for p in input_path.rglob('*'):
        if p.is_file() and p.suffix.lower() in IMAGE_EXTS:
            if any(part in {'cut_sheets','annotated','rectified_cards'} for part in p.parts):
                continue
            imgs.append(p)
    return sorted(imgs)

def quality_status(tag_count, min_side, geom, has_card):
    if tag_count < 3 or not has_card: return 'FAIL_NO_CARD'
    if min_side < 10: return 'FAIL_TAG_TOO_SMALL'
    if min_side < 18: return 'WARN_TAG_SMALL'
    if not math.isnan(geom) and geom > max(3.0, 0.15 * min_side): return 'WARN_GEOMETRY'
    return 'PASS'

def draw_annotation(img_bgr, tag_metrics, corners_by_id, fid_quad, card_quad, out_path):
    pil = Image.fromarray(cv2.cvtColor(img_bgr, cv2.COLOR_BGR2RGB))
    d = ImageDraw.Draw(pil); f = pil_font(16, True); fs = pil_font(13)
    for tm in tag_metrics:
        pts = corners_by_id.get(tm.tag_id)
        if pts is not None:
            poly = [tuple(map(float, p)) for p in pts]
            d.line(poly + [poly[0]], fill=(0,255,0), width=3)
        d.text((tm.center_x+6, tm.center_y-10), f'ID {tm.tag_id}', fill=(0,255,0), font=fs)
    if fid_quad is not None:
        q = [tuple(map(float, p)) for p in fid_quad]
        d.line(q + [q[0]], fill=(255,215,0), width=3)
    if card_quad is not None:
        q = [tuple(map(float, p)) for p in card_quad]
        d.line(q + [q[0]], fill=(255,0,0), width=4)
        d.text((q[0][0], max(0, q[0][1]-22)), 'estimated card crop', fill=(255,0,0), font=f)
    pil.save(out_path, quality=92)

def build_contact_sheet(rows, output_dir, rectified_dir, annotated_dir):
    cut_dir = output_dir / 'cut_sheets'; cut_dir.mkdir(exist_ok=True)
    f_title, f_small = pil_font(28, True), pil_font(13)
    margin, display_w, display_h, label_h, cols = 24, 520, 260, 150, 2
    tile_w, tile_h = display_w + 24, display_h + label_h
    sheet = Image.new('RGB', (margin*2+cols*tile_w, margin*2+95+max(1, math.ceil(len(rows)/cols))*tile_h), (245,247,250))
    d = ImageDraw.Draw(sheet)
    d.text((margin, margin), 'Reference card quality analysis', font=f_title, fill=(30,50,70))
    d.text((margin, margin+38), 'Rectified card crops, normalized display.', font=f_small, fill=(110,80,50))
    for i, r in enumerate(rows):
        x, y = margin + (i % cols) * tile_w, margin + 95 + (i // cols) * tile_h
        d.rectangle((x,y,x+tile_w-10,y+tile_h-8), fill=(255,255,255), outline=(200,210,220))
        img_path = rectified_dir / f"{r['stem']}_card_rectified.jpg"
        if not img_path.exists(): img_path = annotated_dir / f"{r['stem']}_annotated.jpg"
        if img_path.exists():
            with Image.open(img_path) as im:
                im = im.convert('RGB'); im.thumbnail((display_w, display_h), Image.Resampling.LANCZOS)
                canvas = Image.new('RGB', (display_w, display_h), (230,235,240))
                canvas.paste(im, ((display_w-im.width)//2, (display_h-im.height)//2))
                sheet.paste(canvas, (x+8,y+8))
        ly = y + display_h + 16
        lines = [
            f"{r.get('source_name','')[:48]}",
            f"status={r.get('quality_status')} tags={r.get('tag_count')} min_tag_px={r.get('tag_side_px_min')}",
            f"tag sharp={r.get('tag_laplacian_var_mean')} contrast={r.get('tag_contrast_mean')}",
            f"card sharp={r.get('card_laplacian_var')} PSNR={r.get('ref_psnr_rgb','')}",
            f"file={r.get('image_width')}×{r.get('image_height')} {r.get('image_size_kb')} KB",
        ]
        for j, line in enumerate(lines):
            d.text((x+12, ly+j*22), line, font=f_small, fill=(40,60,80))
    out = cut_dir / 'reference_card_quality_sheet.jpg'
    sheet.save(out, quality=94)
    return out

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--input', required=True)
    ap.add_argument('--output', required=True)
    ap.add_argument('--tag-family', default='DICT_APRILTAG_36h11')
    ap.add_argument('--scales', nargs='+', type=float, default=[1,2,3,4,6,8])
    ap.add_argument('--corner-map', default='tl:none,tr:1,bl:2,br:3')
    ap.add_argument('--rectified-width', type=int, default=1000)
    ap.add_argument('--rectified-height', type=int, default=420)
    ap.add_argument('--card-expand-x', type=float, default=1.25)
    ap.add_argument('--card-expand-y', type=float, default=2.0)
    ap.add_argument('--reference', default='')
    args = ap.parse_args()
    out_dir = Path(args.output).expanduser().resolve(); out_dir.mkdir(parents=True, exist_ok=True)
    annotated_dir, rectified_dir, json_dir = out_dir/'annotated', out_dir/'rectified_cards', out_dir/'json'
    annotated_dir.mkdir(exist_ok=True); rectified_dir.mkdir(exist_ok=True); json_dir.mkdir(exist_ok=True)
    corner_map = parse_corner_map(args.corner_map)
    images = find_images(Path(args.input).expanduser().resolve())
    if not images: raise SystemExit('No images found')
    rows, rectified_by_stem = [], {}
    for img_path in images:
        stem = img_path.stem
        try:
            img = load_image_bgr(img_path); h,w = img.shape[:2]
            tag_metrics, corners_by_id, best_scale, rejected_count = detect_tags(img, args.tag_family, args.scales)
            fid_quad, corner_status, geom_resid = infer_card_corners_from_tags(corners_by_id, corner_map)
            card_quad = rect = None
            if fid_quad is not None:
                card_quad = expand_quad(fid_quad, args.card_expand_x, args.card_expand_y)
                rect = rectify_quad(img, card_quad, args.rectified_width, args.rectified_height)
                cv2.imwrite(str(rectified_dir / f'{stem}_card_rectified.jpg'), rect, [int(cv2.IMWRITE_JPEG_QUALITY),94])
                rectified_by_stem[stem] = rect
            draw_annotation(img, tag_metrics, corners_by_id, fid_quad, card_quad, annotated_dir / f'{stem}_annotated.jpg')
            sides = [tm.side_px_min for tm in tag_metrics]
            row = {
                'source_path': str(img_path), 'source_name': img_path.name, 'stem': stem,
                'image_width': w, 'image_height': h, 'image_size_kb': round(img_path.stat().st_size/1024, 3),
                'tag_count': len(tag_metrics), 'tag_ids': ' '.join(str(tm.tag_id) for tm in sorted(tag_metrics, key=lambda x:x.tag_id)),
                'detector_best_scale': best_scale, 'rejected_candidates': rejected_count,
                'tag_side_px_min': round(min(sides),3) if sides else '',
                'tag_side_px_mean': round(float(np.mean([tm.side_px_mean for tm in tag_metrics])),3) if tag_metrics else '',
                'tag_laplacian_var_mean': round(float(np.mean([tm.laplacian_var for tm in tag_metrics])),4) if tag_metrics else '',
                'tag_tenengrad_mean': round(float(np.mean([tm.tenengrad for tm in tag_metrics])),4) if tag_metrics else '',
                'tag_contrast_mean': round(float(np.mean([tm.contrast_p95_p05 for tm in tag_metrics])),4) if tag_metrics else '',
                'fiducial_geometry_residual_px': round(geom_resid,4) if not math.isnan(geom_resid) else '',
                'corner_status': json.dumps(corner_status, sort_keys=True), 'has_card_rectified': rect is not None,
            }
            row.update(compute_card_metrics(rect))
            row['quality_status'] = quality_status(row['tag_count'], float(row['tag_side_px_min'] or 0), geom_resid, rect is not None)
        except Exception as exc:
            row = {'source_path': str(img_path), 'source_name': img_path.name, 'stem': stem, 'quality_status': 'ERROR', 'error': f'{type(exc).__name__}: {exc}'}
        (json_dir / f'{stem}.json').write_text(json.dumps(row, indent=2, sort_keys=True), encoding='utf-8')
        rows.append(row)
    ref_rect, ref_name = None, ''
    if args.reference:
        ref_path = Path(args.reference).expanduser().resolve(); ref_img = load_image_bgr(ref_path)
        _, ref_corners, _, _ = detect_tags(ref_img, args.tag_family, args.scales)
        ref_fid, _, _ = infer_card_corners_from_tags(ref_corners, corner_map)
        if ref_fid is not None:
            ref_rect = rectify_quad(ref_img, expand_quad(ref_fid, args.card_expand_x, args.card_expand_y), args.rectified_width, args.rectified_height)
            ref_name = ref_path.name
    else:
        candidates = [r for r in rows if r.get('has_card_rectified')]
        candidates.sort(key=lambda r: int(r.get('image_width',0))*int(r.get('image_height',0)), reverse=True)
        if candidates:
            ref_name = candidates[0]['source_name']; ref_rect = rectified_by_stem.get(candidates[0]['stem'])
    if ref_rect is not None:
        cv2.imwrite(str(out_dir/'reference_card_rectified.jpg'), ref_rect, [int(cv2.IMWRITE_JPEG_QUALITY),96])
        for r in rows:
            r.update(compare_to_reference(rectified_by_stem.get(r.get('stem','')), ref_rect)); r['reference_name'] = ref_name
    fieldnames = ['source_name','quality_status','image_width','image_height','image_size_kb','tag_count','tag_ids','tag_side_px_min','tag_side_px_mean','tag_laplacian_var_mean','tag_tenengrad_mean','tag_contrast_mean','fiducial_geometry_residual_px','card_laplacian_var','card_tenengrad','card_contrast_p95_p05','card_clipped_dark_frac','card_clipped_bright_frac','ref_psnr_rgb','ref_laplacian_corr','ref_mse_rgb','detector_best_scale','rejected_candidates','corner_status','has_card_rectified','reference_name','source_path','error']
    csv_path = out_dir/'reference_card_quality_results.csv'
    with csv_path.open('w', newline='', encoding='utf-8') as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore'); w.writeheader(); w.writerows(rows)
    sheet_path = build_contact_sheet(rows, out_dir, rectified_dir, annotated_dir)
    print('images=', len(images)); print('results_csv=', csv_path); print('contact_sheet=', sheet_path); print('annotated_dir=', annotated_dir); print('rectified_dir=', rectified_dir); print('reference=', ref_name or '(none)')
if __name__ == '__main__': main()

#!/usr/bin/env python3
"""7-frame fused site depth map for the fixed bmcam001 AOML view.

Runs in the sea-thru BENCH venv (torch). Static scene + fixed camera =>
each frame's DA-V2 Small disparity is a noisy measurement of one geometry;
the per-pixel MEDIAN across 7 frames (14:00-18:00 sun angles, two days)
cancels lighting-driven depth errors (sunlit sand read far, shadows near,
caustic ripple). Output: fused_disp_7frames.npy (+ per-frame pngs).
"""
import sys, numpy as np
from pathlib import Path
from PIL import Image
import torch
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

IN = Path("runs/day02_validation_20260902/inputs")
OUT = Path("runs/day02_validation_20260902/depth_fusion")
OUT.mkdir(exist_ok=True)
proc = AutoImageProcessor.from_pretrained("depth-anything/Depth-Anything-V2-Small-hf")
model = AutoModelForDepthEstimation.from_pretrained("depth-anything/Depth-Anything-V2-Small-hf").eval()
disps = []
for p in sorted(IN.glob("*.jpg")):
    img = Image.open(p).convert("RGB")
    w = min(int(img.width*2.0), 2048); h = int(img.height*w/img.width)
    proc.size = {"height": (h//14)*14, "width": (w//14)*14}
    with torch.no_grad():
        pred = model(**proc(images=img, return_tensors="pt")).predicted_depth.squeeze().numpy()
    import cv2
    disp = cv2.resize(pred, img.size, interpolation=cv2.INTER_LINEAR)
    disp = (disp-disp.min())/max(disp.max()-disp.min(),1e-9)
    disps.append(disp)
    Image.fromarray((disp*255).astype(np.uint8)).save(OUT/f"disp_{p.stem[-9:-1]}.png")
    print("depth", p.name)
fused = np.median(np.stack(disps), axis=0)
fused = (fused-fused.min())/max(fused.max()-fused.min(),1e-9)
np.save(OUT/"fused_disp_7frames.npy", fused)
Image.fromarray((fused*255).astype(np.uint8)).save(OUT/"fused_disp_7frames.png")
spread = np.median(np.abs(np.stack(disps)-fused), axis=0)
Image.fromarray((np.clip(spread*4,0,1)*255).astype(np.uint8)).save(OUT/"disp_spread_x4.png")
print("fused saved; per-pixel median abs spread:", round(float(spread.mean()),4))

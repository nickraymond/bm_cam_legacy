#!/bin/bash
# Order-of-operations test matrix — color_v2_orderops_20260902
#
# Question: how much of the v1 red shift and halo artifacting is caused by
# ORDERING (stacked red gains, second WB after card WB, non-edge-aware
# illumination, late linear-light unsharp) rather than the physics model?
# Every variant below uses EXISTING flags on the locked v1 tool — no code
# changes — so each toggles exactly one suspected ordering defect.
#
# Variants:
#   v1_baseline    locked v1 defaults (repro; bit-exact vs nereus_color_v1_20260901)
#   nored          red WB cap 1.0 + red stretch cap 1.0 (kill stacked red gains)
#   singlewb       luma-only stretch (kill the 2nd per-channel WB after card WB)
#   nored_singlewb both of the above (card WB becomes the ONLY color balance)
#   guidedluma     edge-aware LSAC illumination (halo suspect #1)
#   refined        guidedluma + singlewb + nored + sharpen 0.4 (candidate v2)
set -euo pipefail
cd "$(dirname "$0")/../.."
PY=/Users/nickbuemond/Documents/GitHub/bm_cam_legacy/.venv/bin/python
IMGS="$HOME/Downloads/SPOT-33361C_BMCAM_001_2026-09-01T17-00-25Z.jpg $HOME/Downloads/SPOT-33361C_BMCAM_001_2026-09-01T18-00-24Z.jpg"
GEO="--depth-npy runs/depth_fusion_20260901/fused_disp_2frames.npy --z-card 1.5 --near-ratio 0.45 --camera-height-m 0.25 --water-depth-m 4.57"
OUT=runs/color_v2_orderops_20260902

run() { name=$1; shift; echo "=== $name ==="; \
  $PY tools/bm_reference_card_hybrid_physics.py --images $IMGS $GEO \
    --output-dir "$OUT/$name" "$@" 2>&1 | grep -E "===|wb_gains|ERROR|saved" ; }

run nored          --red-wb-cap 1.0 --red-stretch-cap 1.0
run singlewb       --stretch-mode luma
run nored_singlewb --red-wb-cap 1.0 --red-stretch-cap 1.0 --stretch-mode luma
run guidedluma     --lsac-filter guided_luma
run refined        --lsac-filter guided_luma --stretch-mode luma \
                   --red-wb-cap 1.0 --red-stretch-cap 1.0 --sharpen 0.4
echo "MATRIX DONE"

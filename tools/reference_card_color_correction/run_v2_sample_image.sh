#!/usr/bin/env bash
set -Eeuo pipefail

# Run from repo root:
#   ./tools/reference_card_color_correction/run_v2_sample_image.sh \
#     "$HOME/Downloads/bm_color_smoke_input" \
#     "$HOME/Downloads/color_smoke_v2_$(date -u +%Y%m%dT%H%M%SZ)"

INPUT_DIR="${1:-$HOME/Downloads/bm_color_smoke_input}"
OUTPUT_DIR="${2:-$HOME/Downloads/color_smoke_v2_$(date -u +%Y%m%dT%H%M%SZ)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/bm_reference_card_color_smoke.py" \
  --input-dir "$INPUT_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --reference-card "$SCRIPT_DIR/reference_card_template_v2/reference_card_template_3000x1000.png" \
  --template-json "$SCRIPT_DIR/reference_card_template_v2/template_layout.json" \
  --quality-script "$SCRIPT_DIR/bm_reference_card_quality_v2.py" \
  --method gray_chroma \
  --max-images 10 \
  --make-highres-cutsheets true \
  --make-card-detail-sheet true

echo "OUTPUT_DIR=$OUTPUT_DIR"
echo "Open: $OUTPUT_DIR/cutsheets/color_correction_contact_sheet.jpg"

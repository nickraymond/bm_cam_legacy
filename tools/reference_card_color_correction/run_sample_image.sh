#!/usr/bin/env bash
set -euo pipefail

# Usage:
#   ./run_sample_image.sh /path/to/folder/with/bm/images /path/to/output_dir
# Example:
#   ./run_sample_image.sh "$HOME/Downloads/bm_color_input" "$HOME/Downloads/bm_color_output_$(date -u +%Y%m%dT%H%M%SZ)"

INPUT_DIR="${1:-$HOME/Downloads/bm_color_smoke_input}"
OUTPUT_DIR="${2:-$HOME/Downloads/bm_color_smoke_$(date -u +%Y%m%dT%H%M%SZ)}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

python3 "$SCRIPT_DIR/tools/bm_reference_card_color_smoke.py" \
  --input-dir "$INPUT_DIR" \
  --output-dir "$OUTPUT_DIR" \
  --reference-card "$SCRIPT_DIR/reference_card_template_v1/reference_card_template_2000x840.png" \
  --template-json "$SCRIPT_DIR/reference_card_template_v1/template_layout.json" \
  --quality-script "$SCRIPT_DIR/tools/bm_reference_card_quality_v2.py" \
  --method gray_chroma \
  --scales 1 2 3 4 6 8

echo ""
echo "Output: $OUTPUT_DIR"
echo "Contact sheet: $OUTPUT_DIR/cutsheets/color_correction_contact_sheet.jpg"

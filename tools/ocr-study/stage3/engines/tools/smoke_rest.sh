#!/usr/bin/env bash
# Sequential smoke runs (CPU timings need the machine otherwise idle).
#
# Settings: OCR_STUDY_DIR (default ../../.work from the engines folder),
# OCR_STUDY_ENGINES_PYTHON (default $OCR_STUDY_DIR/stage3/engines/venv/Scripts/python.exe).
set -u
E="$(cd "$(dirname "$0")/.." && pwd)"
cd "$E"
WORK=${OCR_STUDY_DIR:-../../.work}
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
EW=$OCR_STUDY_DIR/stage3/engines
W() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
SMOKE=$(W "$EW/crops/smoke")
PY="${OCR_STUDY_ENGINES_PYTHON:-$EW/venv/Scripts/python.exe}"
for m in paddleocr-vl-1.6 glm-ocr granite-docling; do
  echo "=== vlm $m cpu"
  "$PY" -I vlm.py --model $m --device cpu "$SMOKE" 2>&1 | grep -v '^   "\|^  \],\|server_args'
done
for e in pp-formulanet-plus-m pp-formulanet-plus-s pix2text-mfr-1.5 rapid-latex-ocr texteller; do
  echo "=== rec $e"
  "$PY" -I recognize.py --engine $e "$SMOKE" 2>&1 | grep -v '^\s*$' | grep -v '^  "\|^{$\|^}$' | tail -14
done
echo "=== vlm granite-docling gpu page"
"$PY" -I vlm.py --model granite-docling --device gpu --page lounis-p8 2>&1 | tail -3
echo "=== done"

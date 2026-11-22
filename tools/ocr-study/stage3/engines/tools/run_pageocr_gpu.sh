#!/usr/bin/env bash
# Stage 2 page OCR on the GPU, one engine at a time, all 15 truth pages
# -> stage2/vlm_raw/<engine>/<page>.json (engines/pageocr.py).
# Logs: stage3/engines/logs/run_pageocr_<engine>_gpu.txt.
#
# Settings: OCR_STUDY_DIR (default ../../.work from the engines folder),
# OCR_STUDY_ENGINES_PYTHON (default $OCR_STUDY_DIR/stage3/engines/venv/Scripts/python.exe).
set -u
E="$(cd "$(dirname "$0")/.." && pwd)"
cd "$E"
WORK=${OCR_STUDY_DIR:-../../.work}
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
EW=$OCR_STUDY_DIR/stage3/engines
PY="${OCR_STUDY_ENGINES_PYTHON:-$EW/venv/Scripts/python.exe} -I"
L="$EW/logs"
mkdir -p "$L"
for e in paddle-page glm-page lighton-page granite-page dots-ocr-page dots-layout glm-regions paddle-regions; do
  echo "== $e"
  $PY pageocr.py --engine $e --device gpu > "$L/run_pageocr_${e}_gpu.txt" 2>&1
  tail -16 "$L/run_pageocr_${e}_gpu.txt"
done
echo done

#!/usr/bin/env bash
# The five VLMs on the GPU: real crops -> stage3/rec/<model>-gpu, synthetic scans -> stage3/rec_synth/<model>-gpu.
# Logs: stage3/logs_<model>_<set>.txt.
#
# Settings: OCR_STUDY_DIR (default ../.work), OCR_STUDY_ENGINES_PYTHON (the
# engines' venv; default $OCR_STUDY_DIR/stage3/engines/venv/Scripts/python.exe).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
WORK=${OCR_STUDY_DIR:-../.work}
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
S3=$OCR_STUDY_DIR/stage3
W() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
cd "$HERE/engines"
PY="${OCR_STUDY_ENGINES_PYTHON:-$S3/engines/venv/Scripts/python.exe} -I"
REAL=$(W "$S3/crops_real")
SYN=$(W "$S3/synth/scan")
OUT_SYN=$(W "$S3/rec_synth")
for m in paddleocr-vl-1.6 glm-ocr granite-docling lightonocr-2 dots-ocr; do
  echo "== $m real"; $PY vlm.py --model $m --device gpu "$REAL" > "$S3/logs_${m}_real.txt" 2>&1; tail -3 "$S3/logs_${m}_real.txt"
  echo "== $m synth"; $PY vlm.py --model $m --device gpu "$SYN" --out "$OUT_SYN" > "$S3/logs_${m}_synth.txt" 2>&1; tail -3 "$S3/logs_${m}_synth.txt"
done
echo done

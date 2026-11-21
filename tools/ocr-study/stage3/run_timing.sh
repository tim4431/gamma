#!/usr/bin/env bash
# Clean CPU timings on an idle machine, one engine at a time, with peak memory:
# the readers on the 30 Lounis displays, and GLM-OCR, LightOnOCR-2 and dots.ocr on the 53
# inline crops (one thread and the default), the detector fp32/fp16 on the 13 Lounis pages,
# PP-FormulaNet plus-M.
# Outputs: stage3/timing/<what>/... (the runners' _timing.json), stage3/timing/peaks.json.
#
# Settings: OCR_STUDY_DIR (default ../.work), OCR_STUDY_ENGINES_PYTHON (the
# engines' venv; default $OCR_STUDY_DIR/stage3/engines/venv/Scripts/python.exe).
# The memory sampler is mem_sampler.ps1 (Windows PowerShell).
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
WORK=${OCR_STUDY_DIR:-../.work}
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
S3=$OCR_STUDY_DIR/stage3
cd "$HERE/engines"
PY="${OCR_STUDY_ENGINES_PYTHON:-$S3/engines/venv/Scripts/python.exe} -I"
W() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
T="$S3/timing"
mkdir -p "$T"
rm -f "$T/stop.flag"
powershell -NoProfile -ExecutionPolicy Bypass -File "$(W "$HERE/mem_sampler.ps1")" -Out "$(W "$T/peaks.json")" -StopFile "$(W "$T/stop.flag")" &
SAMPLER=$!
LOUNIS=$(ls "$S3/img" | grep lounis | sed 's/.png//' | tr '\n' ' ')
for m in pp-doclayoutv3 pp-doclayoutv3-fp16; do
  echo "== detect $m"; $PY detect.py --model $m $LOUNIS --threads both --out "$(W "$T/det")" > "$T/log_det_$m.txt" 2>&1
done
echo "== plus-m"; $PY recognize.py --engine pp-formulanet-plus-m "$(W "$S3/crops_lounis")" --threads both --out "$(W "$T/rec")" > "$T/log_plusm.txt" 2>&1
for m in glm-ocr paddleocr-vl-1.6 lightonocr-2 dots-ocr; do
  echo "== $m displays"; $PY vlm.py --model $m --device cpu "$(W "$S3/crops_lounis")" --threads both --out "$(W "$T/rec")" > "$T/log_$m.txt" 2>&1
done
for m in glm-ocr lightonocr-2 dots-ocr; do
  echo "== $m inline"; $PY vlm.py --model $m --device cpu "$(W "$S3/crops_inline")" --threads both --out "$(W "$T/rec_inline")" > "$T/log_${m}_inline.txt" 2>&1
done
touch "$T/stop.flag"; wait $SAMPLER
cat "$T/peaks.json"
echo done

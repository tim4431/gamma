#!/usr/bin/env bash
# Dedicated recognizers on the real crops (-> stage3/rec) and the synthetic scans
# (-> stage3/rec_synth); rapid-latex-ocr last (slow, loops on wide crops), real only.
# Logs: stage3/logs_<engine>_<set>.txt.
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
for e in pp-formulanet-plus-s pp-formulanet-plus-m pix2text-mfr-1.5 texteller; do
  echo "== $e real"; $PY recognize.py --engine $e "$REAL" --threads default > "$S3/logs_${e}_real.txt" 2>&1; tail -3 "$S3/logs_${e}_real.txt"
  echo "== $e synth"; $PY recognize.py --engine $e "$SYN" --threads default --out "$OUT_SYN" > "$S3/logs_${e}_synth.txt" 2>&1; tail -3 "$S3/logs_${e}_synth.txt"
done
echo "== rapid-latex-ocr real"; $PY recognize.py --engine rapid-latex-ocr "$REAL" --threads default > "$S3/logs_rapid_real.txt" 2>&1; tail -3 "$S3/logs_rapid_real.txt"
echo done

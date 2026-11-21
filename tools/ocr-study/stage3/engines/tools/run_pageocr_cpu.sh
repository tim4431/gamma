#!/usr/bin/env bash
# CPU timings for the small models' page OCR, one run at a time on an otherwise idle machine,
# a fresh llama-server per page: default threads (12) on lounis-p2 and holstein-p2; one thread
# on lounis-p2 for the two leaders.
# -> stage2/vlm_timing/<engine>-t<threads>-<page>.json (+ .raw.json) (engines/pageocr.py --timing-out).
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
PY="${OCR_STUDY_ENGINES_PYTHON:-$EW/venv/Scripts/python.exe} -I"
T=$(W "$OCR_STUDY_DIR/stage2/vlm_timing")
for e in paddle-regions lighton-page paddle-page glm-regions; do
  echo "== $e default $(date +%T)"; $PY pageocr.py --engine $e --device cpu --threads default lounis-p2 holstein-p2 --timing-out "$T" 2>&1 | tail -3
done
for e in paddle-regions lighton-page; do
  echo "== $e t1 $(date +%T)"; $PY pageocr.py --engine $e --device cpu --threads 1 lounis-p2 --timing-out "$T" 2>&1 | tail -3
done
echo "done $(date +%T)"

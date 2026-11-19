#!/usr/bin/env bash
# Every engine configuration over the 15 study pages, one after another (so
# timings do not compete). Logs to $OCR_STUDY_DIR/stage2/logs/<engine>.log.
#
# Settings: OCR_STUDY_DIR, OCR_STUDY_PYTHON (the Python with rapidocr, onnxtr,
# tesserocr; default python), OCR_STUDY_MUPDF_PYTHON (a Python with PyMuPDF
# 1.28.2; default OCR_STUDY_PYTHON), and the ones the runners read
# (OCR_STUDY_TESSERACT, OCR_STUDY_TESSDATA_FAST/BEST, OCR_STUDY_TJS).
set -u
cd "$(dirname "$0")"
WORK=${OCR_STUDY_DIR:-../.work}
mkdir -p "$WORK/stage2/logs"
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
LOGS=$OCR_STUDY_DIR/stage2/logs
export PYTHONIOENCODING=utf-8
PY="${OCR_STUDY_PYTHON:-python} -I"
BPY="${OCR_STUDY_MUPDF_PYTHON:-${OCR_STUDY_PYTHON:-python}} -I"
run() { local name=$1; shift; echo "== $name"; "$@" > "$LOGS/$name.log" 2>&1; tail -1 "$LOGS/$name.log"; }
run tess-best   $PY run_tess.py --data best
run tess-fast   $PY run_tess.py --data fast
run mupdf-best  $BPY run_mupdf.py --data best
run mupdf-fast  $BPY run_mupdf.py --data fast
run rapid-v6-small        $PY run_rapid.py --size small
run rapid-v6-small-s4000  $PY run_rapid.py --size small --side 4000
run rapid-v6-medium-s4000 $PY run_rapid.py --size medium --side 4000
run onnxtr-db_resnet50-parseq  $PY run_onnxtr.py --det db_resnet50 --reco parseq
run onnxtr-fast_base-crnn_vgg16_bn $PY run_onnxtr.py --det fast_base --reco crnn_vgg16_bn
if command -v fnm >/dev/null; then eval "$(fnm env --shell bash)"; fnm use 24 >/dev/null; fi
run tjs node run_tjs.mjs
echo done

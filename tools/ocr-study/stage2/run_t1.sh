#!/usr/bin/env bash
# Single-thread timings (a NAS core is closer to one thread than to 24).
# Settings as in run_all.sh.
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
run tess-best-t1 $PY run_tess.py --data best --threads 1
run tess-fast-t1 $PY run_tess.py --data fast --threads 1
export OMP_THREAD_LIMIT=1 OMP_NUM_THREADS=1
run mupdf-fast-omp1 $BPY run_mupdf.py --data fast --name-suffix=-omp1
unset OMP_THREAD_LIMIT OMP_NUM_THREADS
run rapid-v6-small-s4000-t1 $PY run_rapid.py --size small --side 4000 --threads 1
run onnxtr-fast_base-crnn_vgg16_bn-t1 $PY run_onnxtr.py --det fast_base --reco crnn_vgg16_bn --threads 1
echo done

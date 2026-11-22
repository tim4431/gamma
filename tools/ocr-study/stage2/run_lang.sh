#!/usr/bin/env bash
# What not knowing the language costs: every page read as English only, and
# with English, French and German loaded together. Settings as in run_all.sh.
set -u
cd "$(dirname "$0")"
WORK=${OCR_STUDY_DIR:-../.work}
mkdir -p "$WORK/stage2/logs"
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
LOGS=$OCR_STUDY_DIR/stage2/logs
export PYTHONIOENCODING=utf-8
PY="${OCR_STUDY_PYTHON:-python} -I"
run() { local name=$1; shift; echo "== $name"; "$@" > "$LOGS/$name.log" 2>&1; tail -1 "$LOGS/$name.log"; }
run tess-fast-eng   $PY run_tess.py --data fast --lang eng --name tess-fast-eng
run tess-fast-multi $PY run_tess.py --data fast --lang eng+fra+deu --name tess-fast-multi
run tess-best-multi $PY run_tess.py --data best --lang eng+fra+deu --name tess-best-multi
echo done

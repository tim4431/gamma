#!/usr/bin/env bash
# Score every finished reader on the real crops (gt_latex, 152) and the
# synthetic scans (gt_synth, 191): stage3/scores/<set>-<engine>.json, one line each.
#
# Settings: OCR_STUDY_DIR (default ../.work), OCR_STUDY_PYTHON (the plain
# Python with numpy, pillow, pymupdf; default python), and OCR_STUDY_PDFLATEX
# for the renders.
set -u
HERE=$(cd "$(dirname "$0")" && pwd)
cd "$HERE"
WORK=${OCR_STUDY_DIR:-../.work}
export OCR_STUDY_DIR=$(cd "$WORK" && pwd)
W() { if command -v cygpath >/dev/null; then cygpath -w "$1"; else printf '%s\n' "$1"; fi; }
PY="${OCR_STUDY_PYTHON:-python} -I"
SCORE=$(W "$HERE/score_latex.py")
cd "$OCR_STUDY_DIR/stage3"
mkdir -p scores
for set in real synth; do
  if [ $set = real ]; then GT=gt_latex; REC=rec; N=152; else GT=gt_synth; REC=rec_synth; N=191; fi
  for d in $REC/*/; do
    e=$(basename $d)
    case $e in *-page|*-cpu) continue;; esac
    have=$(for f in $GT/*.tex; do [ -f "$d$(basename $f .tex).json" ] && echo; done | wc -l)
    [ "$have" -lt $N ] && { echo "$set $e: only $have of $N, skipped"; continue; }
    echo -n "$set $e: "; $PY "$SCORE" --gt "$(W $GT)" --pred "$(W $d)" --render "$(W render_cache)" --json "$(W scores/$set-$e.json)"
  done
done

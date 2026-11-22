"""Shared paths and settings for the Stage 3 OCR study (formulas on scanned pages).

Every script reads and writes under $OCR_STUDY_DIR/stage3 (ROOT): pages.json,
img/, lines/, grid/, marks/, eq/, gt_latex/, gtA/, gtB/, adj/, synth/,
crops_real/, crops_inline/, crops_lounis/, det/, det_inline/, rec/,
rec_synth/, rec_inline/, rec_passes/, render_cache/, scores/, timing/ and
engines/ (the engines' downloads, converted models, logs and venvs).

Settings (environment variables):
  OCR_STUDY_DIR       the working directory (default tools/ocr-study/.work);
                      the corpus is corpus/<id>.pdf in it
  OCR_STUDY_PDFLATEX  the pdflatex of TeX Live 2024 for render_latex.py and
                      synth/render.py (default pdflatex on PATH)

Standard library only: the engines' venvs load it too (engines/common.py).
"""

import os
import pathlib

HERE = pathlib.Path(__file__).resolve().parent
WORK = pathlib.Path(os.environ.get("OCR_STUDY_DIR") or HERE.parent / ".work").resolve()
CORPUS = WORK / "corpus"
ROOT = WORK / "stage3"
SYNTH = ROOT / "synth"
ENGINES = ROOT / "engines"
PDFLATEX = os.environ.get("OCR_STUDY_PDFLATEX") or "pdflatex"

"""Compile one display equation alone with pdflatex and render its ink crop
at 300 dpi grey. pdflatex is OCR_STUDY_PDFLATEX (default pdflatex on PATH)."""
import re, subprocess, shutil, sys
from pathlib import Path
import numpy as np
import fitz
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from common import PDFLATEX  # noqa: E402
DPI = 300
MARGIN = 10
TEXTWIDTH_IN = 3.4

PREAMBLE = r"""\documentclass[10pt]{article}
\usepackage{amsmath,amssymb}
\usepackage{newtxtext,newtxmath}
\usepackage[paperwidth=8.5in,paperheight=11in,textwidth=3.4in,top=1in]{geometry}
\pagestyle{empty}
\begin{document}
"""


def document(latex):
    return PREAMBLE + "\\begin{equation*}\n" + latex + "\n\\end{equation*}\n\\end{document}\n"


def compile_eq(latex, workdir):
    """-> (pdf_path or None, error string)"""
    workdir = Path(workdir)
    if workdir.exists():
        shutil.rmtree(workdir)
    workdir.mkdir(parents=True)
    (workdir / "eq.tex").write_text(document(latex), encoding="utf-8")
    try:
        p = subprocess.run([PDFLATEX, "-interaction=nonstopmode", "-halt-on-error",
                            "-no-shell-escape", "eq.tex"], cwd=workdir,
                           capture_output=True, timeout=60)
    except subprocess.TimeoutExpired:
        return None, "timeout"
    log = (workdir / "eq.log").read_text(encoding="latin-1") if (workdir / "eq.log").exists() else ""
    pdf = workdir / "eq.pdf"
    if p.returncode != 0 or not pdf.exists():
        m = re.search(r"^! (.*)$", log, re.M)
        err = m.group(1) if m else "pdflatex exit %d" % p.returncode
        m2 = re.search(r"^l\.\d+ (.*)$", log, re.M)
        if m2 and "Undefined control sequence" in err:
            cs = re.findall(r"\\[A-Za-z]+", m2.group(1))
            err += " " + (cs[-1] if cs else "")
        return None, err
    return pdf, ""


def rasterize(pdf):
    """-> uint8 grey array cropped to ink + MARGIN, or None if blank"""
    with fitz.open(pdf) as doc:
        if doc.page_count != 1:
            return None
        pix = doc[0].get_pixmap(dpi=DPI, colorspace=fitz.csGRAY, alpha=False)
        a = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width).copy()
    ys, xs = np.nonzero(a < 250)
    if len(ys) == 0:
        return None
    y0 = max(ys.min() - MARGIN, 0); y1 = min(ys.max() + MARGIN + 1, a.shape[0])
    x0 = max(xs.min() - MARGIN, 0); x1 = min(xs.max() + MARGIN + 1, a.shape[1])
    return a[y0:y1, x0:x1]


def render(latex, workdir):
    pdf, err = compile_eq(latex, workdir)
    if pdf is None:
        return None, err
    a = rasterize(pdf)
    if a is None:
        return None, "blank or multi-page output"
    return a, ""


def save_png(a, path):
    Image.fromarray(a, mode="L").save(path, optimize=True)

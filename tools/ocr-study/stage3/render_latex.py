"""Render LaTeX display math to a PNG with pdflatex (TeX Live) and PyMuPDF.

  python -I render_latex.py <out.png> < latex.txt          (one equation)
  import: render(latex, png_path, dpi=300) -> bool

A standalone page, Times-like math (newtx) as the scans' print, the
equation set as display math; several rows separated by \\\\ go in a
gathered environment. Returns False (and leaves no PNG) when it does not
compile. Builds in a temporary folder. pdflatex is OCR_STUDY_PDFLATEX
(default pdflatex on PATH)."""

import os
import pathlib
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from common import PDFLATEX  # noqa: E402

TEMPLATE = r"""\documentclass[varwidth=60cm,border=6pt]{standalone}
\usepackage{amsmath,amssymb}
\usepackage{newtxtext,newtxmath}
\begin{document}
\[ %s \]
\end{document}
"""


def wrap(latex: str) -> str:
    body = latex.strip()
    for opener, closer in (("\\[", "\\]"), ("$$", "$$")):
        if body.startswith(opener) and body.endswith(closer):
            body = body[len(opener):-len(closer)].strip()
    if "\\\\" in body and "\\begin{" not in body:
        body = "\\begin{gathered}" + body + "\\end{gathered}"
    return body


def render(latex: str, png: str, dpi: int = 300) -> bool:
    import pymupdf
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tex = pathlib.Path(tmp) / "eq.tex"
        tex.write_text(TEMPLATE % wrap(latex), encoding="utf-8")
        try:
            r = subprocess.run([PDFLATEX, "-interaction=nonstopmode", "-halt-on-error", "eq.tex"], cwd=tmp,
                               capture_output=True, timeout=60)
        except subprocess.TimeoutExpired:
            return False
        pdf = pathlib.Path(tmp) / "eq.pdf"
        if r.returncode or not pdf.exists():
            return False
        doc = pymupdf.open(str(pdf))
        try:
            doc[0].get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY).save(png)
        finally:
            doc.close()
        return True


if __name__ == "__main__":
    ok = render(sys.stdin.read(), sys.argv[1])
    print("ok" if ok else "failed")
    sys.exit(0 if ok else 1)

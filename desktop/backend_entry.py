"""Entry point for the frozen (PyInstaller) Gamma server the desktop shell
bundles. Sets the env config BEFORE importing gamma (gamma/config.py reads the
environment at import time), then runs uvicorn programmatically.

Dev mode never uses this file — the shell runs `python -m uvicorn app:app`
from the repo's backend/ directly.

`gamma-server --draw-svg <side> <cap> [<x0> <y0> <x1> <y1>]` is the
server's own child process that draws an SVG picture under a time limit
(gamma/svg_draw.py, gamma/pdf_image.py `_svg_draw_command`): a frozen
server has no Python to run the module with, so it runs itself.

`gamma-server --check-ocr` reads a printed line back with the bundled
Tesseract and its models (gamma/ocr_engine.py) and exits 0, or says why it
cannot and exits 1. The release's smoke step runs it (desktop.yml):
`/api/server-config`'s `ocr` only finds the module and the model files,
while a freeze that left out one of Tesseract's libraries fails only once
the module loads.
"""

import argparse
import os
import sys

# The words --check-ocr prints and reads back.
CHECK_WORDS = "Gamma reads scanned pages"


def _bundle_env():
    """Point gamma at what the freeze carries, before gamma is imported."""
    # The built frontend and the OCR models are bundled as data files next
    # to the frozen code (PyInstaller keeps them under sys._MEIPASS in
    # onedir mode).
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    static = os.path.join(bundle_root, "frontend_dist")
    if os.path.isdir(static):
        os.environ.setdefault("GAMMA_STATIC_DIR", static)
    tessdata = os.path.join(bundle_root, "tessdata")
    if os.path.isdir(tessdata):
        os.environ.setdefault("GAMMA_TESSDATA_DIR", tessdata)
    # One thread per recognized page, as the Docker image sets it: the OCR
    # lane (GAMMA_OCR_WORKERS) decides how many pages are read at once, and
    # a Tesseract built with OpenMP would otherwise start a thread a core,
    # which measured slower (docs/research/ocr.md). Read when the library
    # loads, so it is set before anything imports it.
    os.environ.setdefault("OMP_THREAD_LIMIT", "1")


def check_ocr() -> int:
    """``--check-ocr``: 0 when the bundled engine reads ``CHECK_WORDS``
    back from a page drawn as a run draws one (gamma/pdf_ocr.py), else 1
    with the reason on stderr."""
    import tempfile

    # A check of the engine, not of a server's settings: OCR is on unless
    # the environment says otherwise, so nothing opens a data directory's
    # users.db to read whether the server is a demo (config.ocr_enabled).
    # gamma.config still places a data directory at import, beside the
    # bundle when unset: a throwaway one, should anything open it.
    os.environ.setdefault("GAMMA_OCR", "on")
    with tempfile.TemporaryDirectory(prefix="gamma-check-ocr-", ignore_cleanup_errors=True) as data:
        os.environ.setdefault("GAMMA_DATA_DIR", data)
        return _read_back()


def _read_back() -> int:
    from gamma import ocr_engine, pdf_text

    reason = ocr_engine.check()
    if reason:
        print(f"OCR unavailable: {reason}", file=sys.stderr)
        return 1
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=400, height=80)
    page.insert_text((20, 50), CHECK_WORDS, fontsize=24)
    data = doc.tobytes()
    doc.close()
    raster, _ = pdf_text.page_raster(data, 1, ocr_engine.render_zoom, grey=True, annots=False)
    lines = ocr_engine.recognize(raster)
    read = " ".join(word[0] for line in lines or () for word in line)
    if read != CHECK_WORDS:
        print(f"OCR read {read!r}, not {CHECK_WORDS!r}", file=sys.stderr)
        return 1
    print(f"OCR reads {read!r}")
    return 0


def main():
    if sys.argv[1:2] == ["--draw-svg"]:
        from gamma import svg_draw
        sys.exit(svg_draw.main(sys.argv[2:]))
    _bundle_env()
    if sys.argv[1:2] == ["--check-ocr"]:
        sys.exit(check_ocr())
    parser = argparse.ArgumentParser(description="Gamma desktop local server")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.environ["GAMMA_DATA_DIR"] = args.data_dir

    import uvicorn
    from gamma.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

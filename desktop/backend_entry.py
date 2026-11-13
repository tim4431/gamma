"""Entry point for the frozen (PyInstaller) Gamma server the desktop shell
bundles. Sets the env config BEFORE importing gamma (gamma/config.py reads the
environment at import time), then runs uvicorn programmatically.

Dev mode never uses this file — the shell runs `python -m uvicorn app:app`
from the repo's backend/ directly.

`gamma-server --draw-svg <side> <cap> [<x0> <y0> <x1> <y1>]` is the
server's own child process that draws an SVG picture under a time limit
(gamma/svg_draw.py, gamma/pdf_image.py `_svg_draw_command`): a frozen
server has no Python to run the module with, so it runs itself.
"""

import argparse
import os
import sys


def main():
    if sys.argv[1:2] == ["--draw-svg"]:
        from gamma import svg_draw
        sys.exit(svg_draw.main(sys.argv[2:]))
    parser = argparse.ArgumentParser(description="Gamma desktop local server")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--data-dir", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()

    os.environ["GAMMA_DATA_DIR"] = args.data_dir
    # The built frontend is bundled as data files next to the frozen code
    # (PyInstaller extracts/keeps them under sys._MEIPASS in onedir mode).
    bundle_root = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    static = os.path.join(bundle_root, "frontend_dist")
    if os.path.isdir(static):
        os.environ.setdefault("GAMMA_STATIC_DIR", static)

    import uvicorn
    from gamma.app import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()

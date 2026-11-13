"""The child process that draws an SVG picture for ``pdf_image._svg_drawing``.

MuPDF can take minutes to draw an SVG inside ``pdf_image._check_svg``'s
bounds (a path of a million points, many translucent layers, a mask, a page
of text), and no call into it can be stopped. So the server draws one in a
process of this module's, killed when it passes ``pdf_image.SVG_DRAW_SECONDS``,
under an address-space cap where the system holds a process to one.

It reads the SVG, already sized (``picture.svg_sized``) and checked, on
stdin, converts it to a one-page PDF and draws that page as RGBA, its longer
side ``side`` pixels: the whole page, or the region ``x0 y0 x1 y1``
(fractions of the page, top-left origin) when four more arguments give one.
It writes the PDF's length (8 bytes, big-endian), the PDF and the PNG to
stdout and exits 0. It exits 1 with MuPDF's reason on stderr, and 3 when
PyMuPDF does not load.

It imports nothing of the server's, so a fresh interpreter runs it as a
script (``python -P svg_draw.py <side> <cap> [<x0> <y0> <x1> <y1>]``) and the
frozen desktop server as ``gamma-server --draw-svg <side> <cap> [...]``
(desktop/backend_entry.py).
"""

import struct
import sys

NO_PYMUPDF = 3


def main(argv: list[str]) -> int:
    side, cap = int(argv[0]), int(argv[1])
    box = [float(v) for v in argv[2:6]]
    try:
        import resource
        resource.setrlimit(resource.RLIMIT_AS, (cap, cap))
    except (ImportError, ValueError, OSError):  # Windows has no resource; a system may refuse the cap
        pass
    data = sys.stdin.buffer.read()
    try:
        import pymupdf
    except ImportError:
        return NO_PYMUPDF
    pymupdf.TOOLS.mupdf_display_errors(False)
    try:
        pdf = pymupdf.open(stream=data, filetype="svg").convert_to_pdf()
        page = pymupdf.open(stream=pdf, filetype="pdf")[0]
        # a region as pdf_text._draw takes one: fractions of page.rect
        w, h = page.rect.width, page.rect.height
        clip, shown = None, (w, h)
        if box:
            x0, y0, x1, y1 = box
            clip, shown = pymupdf.Rect(x0 * w, y0 * h, x1 * w, y1 * h), ((x1 - x0) * w, (y1 - y0) * h)
        zoom = side / max(shown)
        png = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip, alpha=True).tobytes("png")
    except Exception as e:  # noqa: BLE001 — every failure is the parent's ValueError
        warnings = (pymupdf.TOOLS.mupdf_warnings() or "").strip()  # as pdf_text._mupdf_warnings puts them
        sys.stderr.write(f"{e}; MuPDF: {' | '.join(warnings.splitlines())}" if warnings else str(e))
        return 1
    out = sys.stdout.buffer
    out.write(struct.pack(">Q", len(pdf)))
    out.write(pdf)
    out.write(png)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))

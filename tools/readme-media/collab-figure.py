"""The collaboration demo's pasted screenshot: panels c and d of Fig. 1 on
page 2 of the curated atom-arrays paper (Bluvstein et al. 2022, Nature,
CC BY 4.0), cut from the paper's own PDF.

    python collab-figure.py paper.pdf fig1cd.png 400

renders page 2 with PyMuPDF (the backend's PDF library), crops the two panels (PDF points below),
trims the white margin to a small even border and scales the result to the
given pixel width.
"""
import sys

import pymupdf
from PIL import Image, ImageOps

# Panels c and d, with their axis labels and legend, in PDF points of page 2.
PANELS = (309, 216, 568, 352)
SCALE = 8          # render at 8x (576 dpi) so the downscale stays sharp


def main(source, target, width):
    pixmap = pymupdf.open(source)[1].get_pixmap(matrix=pymupdf.Matrix(SCALE, SCALE), clip=pymupdf.Rect(*PANELS), alpha=False)
    image = Image.frombytes('RGB', (pixmap.width, pixmap.height), pixmap.samples)
    ink = ImageOps.invert(image.convert('L')).point(lambda v: 255 if v > 40 else 0).getbbox()
    pad = 5 * SCALE
    image = image.crop((max(0, ink[0] - pad), max(0, ink[1] - pad), min(image.width, ink[2] + pad), min(image.height, ink[3] + pad)))
    image.resize((width, round(image.height * width / image.width)), Image.LANCZOS).save(target, optimize=True)
    print(f'{target} {width}x{round(image.height * width / image.width)}')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]))

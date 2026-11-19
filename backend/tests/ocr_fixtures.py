"""Synthetic scanned pages for the OCR-layer tests (gamma/pdf_scan.py) and
for text recognition (gamma/ocr_engine.py, gamma/pdf_ocr.py).

No real scan can be committed, so ``scan_pdf`` draws one: a picture over
the whole page and, over it, an invisible OCR layer written the way the APS
and HAL scans write theirs, one text call per word, each word in a size of
its own (the size follows the word's ink, not the line's type) on a
baseline of its own. The words stand wide apart, as an OCR font's narrow
boxes do over the printed words, so MuPDF reads every word as a line of
its own. The layout has a header row that crosses the column gutter, then
two columns of three rows; the stream gives the left column before the
right one, as the scans do. Its picture is blank: nothing to recognize.

``printed_scan_pdf`` is a scan a recognition can read: ``PRINTED`` drawn
visibly, the page drawn as a picture at 300 dpi, and that picture the only
content of a fresh page, with no layer, the printed words as an invisible
layer, or a garbled one (``GARBLED``) as a poor OCR leaves."""

import pymupdf

W, H = 612, 792
# Each word's size, in turn: the spread of one visual row on the Lounis scan.
SIZES = (7.5, 13.0, 9.0, 14.5, 8.0, 12.0, 10.5, 7.0, 13.5, 9.5)
WORD_GAP = 12  # points between two words' font boxes

# (x, baseline, words) in stream order; y down.
ROWS = (
    (50, 60, ("J.", "Phys.", "II", "France", "2")),
    (400, 60, ("APRIL", "1992,", "PAGE", "579")),
    (50, 120, ("The", "entangle-")),
    (50, 140, ("ment", "of", "a", "non-", "linear", "pair")),
    (50, 160, ("(Received", "15", "January", "1992)")),
    (330, 120, ("Cohen", "and", "Lounis")),
    (330, 140, ("show", "a", "dark")),
    (330, 160, ("state", "here.")),
)
# The page text with the rows put back together.
ROW_TEXT = "".join(" ".join(words) + "\n" for _x, _y, words in ROWS)
# MuPDF's plain text of the page: a line a word.
WORD_TEXT = "".join(word + "\n" for _x, _y, words in ROWS for word in words)


def _picture() -> bytes:
    pix = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 64, 64), False)
    pix.set_rect(pix.irect, (235,))
    return pix.tobytes("png")


def scan_pdf(*, invisible: bool = True, image=(0, 0, W, H), size=None) -> bytes:
    """A one-page PDF of ``ROWS``: the words invisible (render mode 3) or
    drawn, over a picture filling ``image`` (a rect, y down; None for no
    picture), each word in its size from ``SIZES`` unless ``size`` sets one
    for all."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    if image:
        page.insert_image(pymupdf.Rect(*image), stream=_picture())
    k = 0
    for x, y, words in ROWS:
        for word in words:
            s = SIZES[k % len(SIZES)]
            baseline = y + (k % 3 - 1) * 0.15 * s
            k += 1
            page.insert_text((x, baseline), word, fontname="tiro", fontsize=s if size is None else size,
                             render_mode=3 if invisible else 0)
            x += pymupdf.get_text_length(word, fontname="tiro", fontsize=s) + WORD_GAP
    return doc.tobytes()


def picture_pdf(*, image=(0, 0, W, H), footer: str = "", text: str = "", note: str = "") -> bytes:
    """A one-page PDF painting the blank picture over ``image`` and no
    text layer: with ``footer`` a line drawn visibly under it (a library's
    stamp), ``text`` drawn in a column over it, ``note`` a FreeText
    annotation."""
    doc = pymupdf.open()
    page = doc.new_page(width=W, height=H)
    page.insert_image(pymupdf.Rect(*image), stream=_picture())
    if footer:
        page.insert_text((40, H - 20), footer, fontsize=7)
    if text:
        page.insert_textbox(pymupdf.Rect(60, 60, W - 60, H - 60), text, fontsize=10)
    if note:
        page.add_freetext_annot(pymupdf.Rect(30, 30, 500, 300), note, fontsize=9)
    return doc.tobytes()


# (x, baseline, size, text) of the printed page, y down: a title in large
# type over two lines, then body text. "resonance" is a word only a
# recognition reads: the garbled layer spells it otherwise.
PRINTED = (
    (72, 110, 24, "Quantum Zeno dynamics"),
    (72, 142, 24, "of entangled atoms"),
    (72, 200, 12, "Recognized text replaces a garbled layer on every"),
    (72, 218, 12, "scanned page, and each highlight keeps its place."),
    (72, 236, 12, "Lounis and Cohen measured the resonance twice."),
)
PRINTED_TEXT = "".join(text + "\n" for _x, _y, _size, text in PRINTED)
# How the garbled layer spells the printed letters (a poor OCR's misreadings).
GARBLE = str.maketrans("aeiosn", "4c105m")
GARBLED = "".join(text.translate(GARBLE) + "\n" for _x, _y, _size, text in PRINTED)


def printed_scan_pdf(*, layer: str | None = None, dpi: int = 300, rotate: int = 0) -> bytes:
    """A one-page scan of ``PRINTED``: the printed page drawn grey at
    ``dpi`` and placed as the only content of a fresh page of the same
    size; with ``layer`` "true" the printed words over it in an invisible
    layer (render mode 3, where they are printed), with "garbled" the same
    words as a poor OCR spelled them (``GARBLED``), with None no layer. The
    page turned by ``rotate`` (/Rotate) when given."""
    printed = pymupdf.open()
    page = printed.new_page(width=W, height=H)
    for x, y, size, text in PRINTED:
        page.insert_text((x, y), text, fontname="helv", fontsize=size)
    pix = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csGRAY)
    doc = pymupdf.open()
    scan = doc.new_page(width=W, height=H)
    scan.insert_image(scan.rect, stream=pix.tobytes("png"))
    if layer:
        for x, y, size, text in PRINTED:
            scan.insert_text((x, y), text if layer == "true" else text.translate(GARBLE), fontname="helv",
                             fontsize=size, render_mode=3)
    if rotate:
        scan.set_rotation(rotate)
    return doc.tobytes(deflate=True)


def mupdf_glyphs(page, flags) -> list:
    """The glyph walk as it was before scan rows (gamma/pdf_text._glyphs): a
    line break after every one of MuPDF's lines."""
    with_rotation = page.rotation
    page.set_rotation(0)
    to_user = ~page.transformation_matrix
    blocks = page.get_text("rawdict", flags=flags)["blocks"]
    page.set_rotation(with_rotation)
    out = []
    for block in blocks:
        if block.get("type") != 0:
            continue
        for line in block["lines"]:
            for span in line["spans"]:
                for char in span["chars"]:
                    box = pymupdf.Rect(char["bbox"]) * to_user
                    out.append((char["c"], (box.x0, box.y0, box.x1, box.y1)))
            out.append(("\n", (0.0, 0.0, 0.0, 0.0)))
    return out

"""The quote of an imported highlight is the text under its quads: pdfium's
glyph boxes (pdf_text.GlyphPages), a glyph counted when its centre is
covered (pdf_text.quote_under). Guards the two failures of the reader this
replaced, which matched one point per line: a highlight from a line's start
quoting the line above it (PyPDF2's text visitor reports a line at the next
line's position), and one starting mid-line quoting nothing."""

import io

from PyPDF2 import PdfReader

from gamma.pdf_text import GlyphPages, quote_under
from gamma.routers.imports import _extract_pdf_annotations

LEFT, TOP, LEAD, SIZE = 72, 700, 12, 10  # Courier: every glyph 6 pt wide at 10 pt
LINES = ["Line one of the paragraph is here.",
         "averaged across five atom pairs on line two.",
         "Line three is the one we highlight.",
         "Line four closes the paragraph."]


def _x(col):
    return LEFT + 6 * col


def _quad(x1, x2, line, below=2, above=8):
    y1, y2 = TOP - LEAD * line - below, TOP - LEAD * line + above
    return (x1, y2, x2, y2, x1, y1, x2, y1)


def _pdf(highlights):
    """One page of LINES in Courier, with a /Highlight per entry of
    ``highlights``: ``(contents, [quad, ...])``."""
    stream = b"BT /F1 %d Tf %d %d Td " % (SIZE, LEFT, TOP)
    stream += b" ".join(b"(%s) Tj 0 -%d Td" % (line.encode(), LEAD) for line in LINES) + b" ET"
    annots = []
    for contents, quads in highlights:
        xs = [q[i] for q in quads for i in (0, 2, 4, 6)]
        ys = [q[i] for q in quads for i in (1, 3, 5, 7)]
        nums = " ".join("%g" % v for q in quads for v in q).encode()
        annots.append(b"<< /Type /Annot /Subtype /Highlight /Rect [%g %g %g %g] /QuadPoints [%s]"
                      b" /C [1 1 0] /Contents (%s) >>"
                      % (min(xs), min(ys), max(xs), max(ys), nums, contents.encode()))
    refs = b" ".join(b"%d 0 R" % (6 + i) for i in range(len(annots)))
    objs = [b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R"
            b" /Resources << /Font << /F1 5 0 R >> >> /Annots [" + refs + b"] >>",
            b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream),
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Courier >>", *annots]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, obj in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n%s\nendobj\n" % (i, obj))
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objs) + 1))
    for off in offsets:
        out.write(b"%010d 00000 n \n" % off)
    out.write(b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objs) + 1, xref))
    return out.getvalue()


def _quotes(pdf_bytes):
    reader = PdfReader(io.BytesIO(pdf_bytes))
    with GlyphPages(pdf_bytes) as glyphs:
        return {r["content"]: r["quote"] for r in _extract_pdf_annotations(reader, glyphs)}


def test_a_highlight_quotes_the_text_under_its_quads():
    end = _x(len(LINES[2]))
    quotes = _quotes(_pdf([
        ("whole line", [_quad(LEFT, end, 2)]),
        ("mid-line", [_quad(_x(11), end, 2)]),
        ("across lines", [_quad(_x(16), _x(len(LINES[1])), 1), _quad(LEFT, end, 2)]),
        # The line's full height and the gap above it, as a viewer's loose
        # quad may reach: the line above stays out.
        ("tall", [_quad(LEFT, end, 2, below=3, above=11)]),
        ("mid-word", [_quad(_x(2), _x(8), 2)]),
    ]))
    assert quotes == {
        "whole line": "Line three is the one we highlight.",
        "mid-line": "is the one we highlight.",
        "across lines": "five atom pairs on line two. Line three is the one we highlight.",
        "tall": "Line three is the one we highlight.",
        "mid-word": "ne thr",
    }


def test_without_glyphs_or_a_readable_file_the_highlight_imports_without_a_quote():
    pdf_bytes = _pdf([("plain", [_quad(LEFT, _x(10), 0)])])
    found = _extract_pdf_annotations(PdfReader(io.BytesIO(pdf_bytes)))
    assert [(r["content"], r["quote"]) for r in found] == [("plain", "")]
    with GlyphPages(b"not a pdf") as glyphs:
        assert glyphs.page(1) == []
        found = _extract_pdf_annotations(PdfReader(io.BytesIO(pdf_bytes)), glyphs)
    assert [(r["content"], r["quote"]) for r in found] == [("plain", "")]


def test_quote_under_counts_a_glyph_by_its_centre_and_skips_read_as_one_space():
    def line(text, y):  # 10-wide glyphs on baseline y, boxes y-2..y+8
        return [(ch, (10 * i, y - 2, 10 * i + 10, y + 8)) for i, ch in enumerate(text)]
    glyphs = (line("ab cd", 100) + [("\r", (50, 100, 50, 100)), ("\n", (50, 100, 50, 100))]  # pdfium's line break
              + line("ef gh", 88) + [("e", (0, 76, 10, 86)), ("́", (10, 76, 10, 86))])
    # The whole first line and the second line's "gh": the line break and
    # the skipped "ef " read as one space.
    assert quote_under(glyphs, [(0, 97, 50, 109), (30, 85, 50, 97)]) == "ab cd gh"
    # Edges on glyph boundaries take neither neighbour (centres at 5, 15, ...).
    assert quote_under(glyphs, [(10, 97, 30, 109)]) == "b"
    # A combining mark (no advance) rides on the glyph before it.
    assert quote_under(glyphs, [(0, 73, 10, 89)]) == "é"
    assert quote_under(glyphs, []) == ""
    assert quote_under(glyphs, [(0, 97, 50, 109)], limit=2) == "ab"

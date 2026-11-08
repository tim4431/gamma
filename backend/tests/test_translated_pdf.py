"""The translated view written into an exported PDF (gamma/pdf_translated.py,
docs/dev/translation.md "Exporting the translation"): the original lines go
and only they, the translation is laid out in their place and shrunk to
fit, upright on a turned page, a formula comes back as a picture of itself
on the baseline with its text hidden under it, the original text stays out
of the file even behind a clip, and one subset font serves every page. Then the export job that
carries it (POST /api/jobs/export with ``translation``): the annotated PDF
of the page with its translation, and what it refuses; and a folder's, with
``translations``, each paper's into its own PDF."""

import io
import struct
import zipfile

import pytest

from conftest import login, make_folder, make_page, make_user

PAGE_W, PAGE_H = 300, 400


def _mupdf():
    import pymupdf

    return pymupdf


def _band(line):
    from gamma.pdf_translated import _band

    return _band(line)


def _paper(rotation=0, pages=1, extra=None):
    """A PDF of ``pages`` pages, each turned by ``rotation`` and written so
    its text reads upright on screen: two lines of a paragraph, a line with
    a formula ("x" in Times-Italic) inside it, a kept paragraph, a drawn
    frame and a link. ``extra(page, put)`` writes more on each page."""
    pymupdf = _mupdf()
    doc = pymupdf.open()
    for n in range(pages):
        # shown PAGE_W by PAGE_H once turned
        turned = rotation in (90, 270)
        page = doc.new_page(width=PAGE_H if turned else PAGE_W, height=PAGE_W if turned else PAGE_H)
        page.set_rotation(rotation)

        def put(x, y, text, size=10, font="helv"):
            page.insert_text(pymupdf.Point(x, y) * page.derotation_matrix, text, fontname=font, fontsize=size,
                             rotate=rotation)

        put(40, 60, f"Alpha line one on page {n + 1}")
        put(40, 74, "Alpha line two")
        put(40, 120, "Beta speaks of")
        put(112, 120, "x", font="tiit")
        put(120, 120, "and more")
        put(40, 200, "Kept paragraph stays")
        page.draw_rect(pymupdf.Rect(40, 250, 200, 330) * page.derotation_matrix, color=(0, 0, 1))
        page.insert_link({"kind": pymupdf.LINK_URI, "uri": "https://example.org",
                          "from": pymupdf.Rect(40, 50, 120, 62) * page.derotation_matrix})
        if extra:
            extra(page, put)
    return doc.tobytes()


def _shown(page):
    """The page's text as the viewer sees it: ``[(text, box, direction,
    font, baseline)]`` per span, in the display frame."""
    m = page.rotation_matrix
    out = []
    for block in page.get_text("dict", flags=0)["blocks"]:
        for line in block.get("lines", []):
            dx, dy = line["dir"]
            direction = (round(dx * m.a + dy * m.c, 3), round(dx * m.b + dy * m.d, 3))
            for span in line["spans"]:
                box = _mupdf().Rect(span["bbox"]) * m
                origin = _mupdf().Point(span["origin"]) * m
                out.append((span["text"], box, direction, span["font"], origin.y))
    return out


def _line_box(page, startswith):
    """The display box of the line whose text starts so: what the viewer
    sends as that line's box."""
    boxes = [box for text, box, *_ in _shown(page) if text.startswith(startswith)]
    assert boxes, startswith
    return list(boxes[0])


def _within(area, box, baseline, slack=2):
    """Whether a span set at ``baseline`` with ``box`` (its font's full
    height, taller than its line) sits in ``area``: its sides inside, its
    baseline between the top and the bottom."""
    return (area.x0 - slack <= box.x0 and box.x1 <= area.x1 + slack
            and area.y0 < baseline <= area.y1 + slack)


def _formula(page, pn=1):
    """The formula span ("x" in Times-Italic) as the viewer sends it."""
    (box, base), = [(box, base) for text, box, _, font, base in _shown(page) if text == "x"]
    return {"page": pn, "box": list(box), "base": base, "text": "x"}


def _write(data, pages, lang="en"):
    from gamma import pdf_translated

    return pdf_translated.write(data, pages, lang)


def _text(data, page=0, clip=True):
    pymupdf = _mupdf()
    flags = pymupdf.TEXTFLAGS_TEXT if clip else pymupdf.TEXTFLAGS_TEXT & ~pymupdf.TEXT_MEDIABOX_CLIP
    return pymupdf.open(stream=data, filetype="pdf")[page].get_text("text", flags=flags)


def _alpha_block(page, parts):
    return {"lines": [_line_box(page, "Alpha line one"), _line_box(page, "Alpha line two")], "size": 10,
            "parts": parts}


def test_the_translation_takes_the_place_of_the_original_lines_and_only_theirs():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Die übersetzten Worte des Absatzes"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    text = page.get_text("text")
    assert "Alpha" not in text and "Die übersetzten Worte des Absatzes" in text.replace("\n", " ")
    assert "Beta speaks of" in text and "Kept paragraph stays" in text
    # the translation sits in the paragraph's box, upright
    (box, direction, base), = [(b, d, y) for t, b, d, _, y in _shown(page) if "übersetzten" in t]
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert direction == (1.0, 0.0) and _within(alpha, box, base)
    # the drawing and the link are the file's still; the lines are masked
    # white, as the overlay masks them
    drawn = page.get_drawings()
    assert [d["rect"] for d in drawn if d["color"] == (0.0, 0.0, 1.0)] == [d["rect"] for d in src.get_drawings()]
    masks = [d["rect"] for d in drawn if d.get("fill") == (1.0, 1.0, 1.0)]
    assert all(any(m.contains(pymupdf.Rect(_band(line))) for m in masks)
               for line in (_line_box(src, "Alpha line one"), _line_box(src, "Alpha line two")))
    assert [link["uri"] for link in page.get_links()] == ["https://example.org"]


@pytest.mark.parametrize("rotation", [90, 180, 270])
def test_on_a_turned_page_the_translation_reads_upright_where_the_lines_were(rotation):
    pymupdf = _mupdf()
    data = _paper(rotation)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    assert src.rotation == rotation and all(d == (1.0, 0.0) for _, _, d, *_ in _shown(src))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Translated words"])]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    shown = _shown(page)
    assert not any("Alpha" in t for t, *_ in shown)
    (box, direction, base), = [(b, d, y) for t, b, d, _, y in shown if "Translated" in t]
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert direction == (1.0, 0.0) and _within(alpha, box, base) and abs(box.x0 - alpha.x0) < 3
    assert any(t.startswith("Kept paragraph") for t, *_ in shown)


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_the_files_annotations_stay_as_they_were_at_every_turn(rotation):
    """A redaction drops the links and FreeText boxes it meets; they come
    back as they were stored, wherever the page turns, and so does a link
    MuPDF cannot follow (no action), which once hid the others from a
    restore by link."""
    pymupdf = _mupdf()

    def annotate(page, put):
        line = pymupdf.Rect(40, 64, 100, 76) * page.derotation_matrix
        page.add_freetext_annot(line, "typed in another app")
        doc = page.parent
        bare = doc.get_new_xref()
        x0, y0, x1, y1 = line * page.transformation_matrix
        doc.update_object(bare, f"<< /Type /Annot /Subtype /Link /Rect [{min(x0, x1)} {min(y0, y1)} {max(x0, x1)} {max(y0, y1)}] >>")
        annots = doc.xref_get_key(page.xref, "Annots")[1].strip("[]")
        doc.xref_set_key(page.xref, "Annots", f"[{annots} {bare} 0 R]")

    data = _paper(rotation, extra=annotate)

    def stored(raw):
        doc = pymupdf.open(stream=raw, filetype="pdf")
        page = doc[0]
        return sorted((doc.xref_get_key(x, "Subtype")[1], doc.xref_get_key(x, "Rect")[1], doc.xref_get_key(x, "A")[1],
                       doc.xref_get_key(x, "Contents")[1]) for x, *_ in page.annot_xrefs())

    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Übersetzt"])]}], "de")
    before, after = stored(data), stored(out)
    assert len(before) == 3 and after == before


def _one_line_block(page, parts, **setting):
    return {"lines": [_line_box(page, "Alpha line two")], "size": 10, "parts": parts, **setting}


def _spans(data):
    """The output page's spans as ``(text, x0, x1, size, font)``."""
    page = _mupdf().open(stream=data, filetype="pdf")[0]
    return [(s["text"], s["bbox"][0], s["bbox"][2], s["size"], s["font"])
            for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]]


def test_a_one_line_block_takes_the_room_the_viewer_gave_it():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    x0, y0, x1, y1 = _line_box(src, "Alpha line two")
    long = "Eine viel laengere Zeile als das Original"
    tight = _spans(_write(data, [{"page": 1, "blocks": [_one_line_block(src, [long])]}], "de"))
    roomy = _spans(_write(data, [{"page": 1, "blocks": [_one_line_block(src, [long], box=[x0, y0, x0 + 300, y1])]}], "de"))
    size = next(s for t, _, _, s, _ in tight if t.startswith("Eine"))
    (_, left, right, grown, _), = [s for s in roomy if s[0].startswith("Eine")]
    assert grown == pytest.approx(10, abs=0.2) and size < grown / 1.5, "it needs no shrinking in the room"
    assert left == pytest.approx(x0, abs=2) and right > x1 + 50


def test_a_centred_block_is_centred_in_its_room():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    x0, y0, x1, y1 = _line_box(src, "Alpha line two")
    block = _one_line_block(src, ["Mitte"], box=[x0, y0, x0 + 200, y1], centred=True)
    (_, left, right, _, _), = [s for s in _spans(_write(data, [{"page": 1, "blocks": [block]}], "de")) if s[0] == "Mitte"]
    assert (left + right) / 2 == pytest.approx(x0 + 100, abs=2)


def test_bold_and_italic_go_in_the_type_and_cjk_has_no_italic():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]

    def font(lang, text, **setting):
        out = _write(data, [{"page": 1, "blocks": [_one_line_block(src, [text], **setting)]}], lang)
        return next(f for t, _, _, _, f in _spans(out) if t.startswith(text[0]))

    assert "Bold" not in font("de", "Xyz") and "Italic" not in font("de", "Xyz")
    assert "Bold" in font("de", "Xyz", bold=True)
    assert "Italic" in font("de", "Xyz", italic=True)
    assert "Italic" not in font("zh-CN", "译文", italic=True)


def test_a_block_takes_the_overlays_leading():
    """gamma/pdf_translated._leading against the cases of transBlock.test.mjs."""
    from gamma.pdf_translated import _leading

    def lines(*tops):
        return [(0, y, 100, y + 9) for y in tops]

    assert _leading({"size": 10, "lines": lines(0, 11.8, 23.6, 35.4)}, "de") == pytest.approx(1.18)
    assert _leading({"size": 10, "lines": lines(0, 11.8, 30, 41.8)}, "de") == pytest.approx(1.18), "the median step"
    assert _leading({"size": 10, "lines": lines(0, 10, 20)}, "ru") == 1.12
    assert _leading({"size": 10, "lines": lines(0, 20, 40)}, "en") == 1.3
    assert _leading({"size": 10, "lines": lines(0)}, "de") == 1.15
    for lang in ("zh-CN", "zh-TW", "ja", "ko"):
        assert _leading({"size": 10, "lines": lines(0, 11.8)}, lang) == 1.28


def test_the_lines_go_at_that_leading():
    pymupdf = _mupdf()
    data = _paper()

    def pitches(lines, lang):
        words = "译文每行都一样" * 60 if lang == "zh-CN" else " ".join(["Worte"] * 60)
        block = {"lines": lines, "size": 10, "parts": [words]}
        page = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [block]}], lang), filetype="pdf")[0]
        spans = [s for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                 if "Worte" in s["text"] or "译" in s["text"]]
        bases = sorted({round(s["origin"][1], 1) for s in spans})
        return (bases[1] - bases[0]) / spans[0]["size"]

    tight = [[40, y, 260, y + 9] for y in (60, 70, 80, 90)]
    wide = [[40, y, 260, y + 9] for y in (60, 73, 86, 99)]
    assert pitches(tight, "de") == pytest.approx(1.12, abs=0.02)
    assert pitches(wide, "de") == pytest.approx(1.3, abs=0.02)
    assert pitches(tight, "zh-CN") == pytest.approx(1.28, abs=0.02)


def test_an_openers_indent_goes_with_its_first_line():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    lines = [_line_box(src, "Alpha line one"), [40, 74, 260, 84]]
    words = " ".join(["Worte"] * 12)

    def rows(**setting):
        out = _write(data, [{"page": 1, "blocks": [{"lines": lines, "size": 10, "parts": [words], **setting}]}], "de")
        page = pymupdf.open(stream=out, filetype="pdf")[0]
        return [ln["bbox"][0] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
                if ln["spans"][0]["text"].startswith("Worte")]

    flat, indented = rows(), rows(indent=12)
    assert flat[0] == pytest.approx(flat[1], abs=0.1)
    assert indented[0] - indented[1] > 8 and indented[1] == pytest.approx(flat[1], abs=0.1)


def _line_ends(data, top, bottom):
    """The right end of each output line with its baseline from ``top`` to
    ``bottom``, top to bottom."""
    page = _mupdf().open(stream=data, filetype="pdf")[0]
    ends = {}
    for b in page.get_text("dict")["blocks"]:
        for ln in b.get("lines", []):
            spans = [s for s in ln["spans"] if s["text"].strip()]
            base = round(spans[0]["origin"][1], 1) if spans else None
            if base is not None and top <= base <= bottom:
                ends[base] = max(ends.get(base, 0), ln["bbox"][2])
    return [ends[k] for k in sorted(ends)]


def test_a_block_justified_in_the_original_is_justified_unless_its_box_is_narrow():
    data = _paper()
    words = ("Die Übersetzung eines Absatzes hat andere Wörter als das Original und so andere Zeilen, manche "
             "lang und manche kurz, wie es eben kommt, wenn ein Text in einer anderen Sprache steht. ") * 2

    def ends(right, **setting):
        # inside the page's frame, where it has no text
        lines = [[40, y, right, y + 9] for y in (255, 268, 281, 294, 307, 320)]
        out = _write(data, [{"page": 1, "blocks": [{"lines": lines, "size": 10, "parts": [words], **setting}]}], "de")
        return _line_ends(out, 250, 335)[:-1]  # the last line ends where its words do

    ragged, justified = ends(260), ends(260, justified=True)
    assert len(justified) >= 3 and max(justified) - min(justified) < 0.5, justified
    assert max(ragged) - min(ragged) > 3, "ragged where the original is"
    narrow = ends(200, justified=True)  # 16 em
    assert max(narrow) - min(narrow) > 3, "a box under 18 em stays ragged"


def _font_paper():
    """A page whose paragraph is set in an embedded font (MuPDF's Nimbus
    Roman) subset to the letters it uses, as a paper's fonts are: MuPDF's
    subsetter keeps the other letters' places and empties their outlines.
    Its other text is in Helvetica, which the file does not hold."""
    pymupdf = _mupdf()
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_font(fontname="F9", fontbuffer=pymupdf.Font("tiro").buffer)
    page.insert_text((40, 60), "Alpha line one: the quick brown fox", fontname="F9", fontsize=10)
    page.insert_text((40, 74), "Alpha line two jumps over a lazy dog", fontname="F9", fontsize=10)
    page.insert_text((40, 200), "Kept paragraph stays", fontname="helv", fontsize=10)
    doc.subset_fonts()
    return doc.tobytes(garbage=1, deflate=True)


def test_a_block_is_set_in_the_papers_own_font_when_it_draws_every_letter():
    pymupdf = _mupdf()
    data = _font_paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    (face,) = {f for t, _, _, f, _ in _shown(src) if t.startswith("Alpha")}
    kept = next(f for t, _, _, f, _ in _shown(src) if t.startswith("Kept"))

    def fonts(text, lang="en", **setting):
        out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, [text]) | setting]}], lang)
        assert text.split()[0] in _text(out)
        return {f for t, _, _, _, f in _spans(out) if t.strip() and t.strip() in text}, out

    used, _ = fonts("the lazy brown dog jumps", face=face, bold=True)
    assert used == {face}, "its own face carries its weight: no bold over it"
    serif = fonts("the lazy brown dog jumps")[0]
    assert face not in serif and all("Charis" in f for f in serif), "no face sent: the serif"
    for text, sent, why in (("Die übersetzten Worte", face, "a letter the subset emptied"),
                            ("翻译", face, "no CJK in it"),
                            ("the lazy dog", kept, "a font the file does not hold"),
                            ("the lazy dog", "Nimbus Sans", "a font the page does not have")):
        used = fonts(text, "zh-CN" if text == "翻译" else "de", face=sent)[0]
        assert used and face not in used, why
    # two blocks in one font: the file holds it once
    two = _write(data, [{"page": 1, "blocks": [
        {"lines": [_line_box(src, "Alpha line one")], "size": 10, "parts": ["the brown fox"], "face": face},
        {"lines": [_line_box(src, "Alpha line two")], "size": 10, "parts": ["a lazy dog"], "face": face}]}], "en")
    held = pymupdf.open(stream=two, filetype="pdf")[0].get_fonts()
    assert len({f[0] for f in held if face in f[3]}) == 1, held  # (xref, ext, type, basefont, …)


def test_the_blanks_a_papers_font_lacks_go_in_the_serif():
    """TeX's fonts have no space: the spaces are set in the serif the file
    has anyway, not in MuPDF's fallback (Noto Serif, 60 KB for the spaces)."""
    from gamma.pdf_translated import _blanks

    class Spaceless:
        def draws_blank(self, c):
            return False

    class Spaced:
        def draws_blank(self, c):
            return c == " "

    gap = '<span class="gs"> </span>'
    assert _blanks("a b\n c", None) == "a b\n c"
    assert _blanks("a b\n c", Spaceless()) == f"a{gap}b{gap}c"
    assert _blanks("a\xa0b c", Spaced()) == 'a<span class="gs">\xa0</span>b c'


_COMPOSITE_LINES = ("Alpha line one: the quick brown fox", "Alpha line two jumps over a lazy dog")


def _cid_keyed(cff: bytes, shift: int) -> bytes:
    """A name-keyed CFF program (MuPDF's Nimbus Roman) written as a CID-keyed
    one, as LuaLaTeX and XeLaTeX embed an OpenType font: one Font DICT, glyph
    k at CID k + ``shift``, so CID and GID differ, and no glyph names, so
    the program maps no character."""
    from gamma.pdf_font_cmap import _dict, _index, _op, _write_index

    _, at = _index(cff, cff[2])
    names = cff[cff[2]:at]
    tops, at = _index(cff, at)
    top = _dict(tops[0])
    strings, after_strings = _index(cff, at)
    _, after_gsubrs = _index(cff, after_strings)
    gsubrs = cff[after_strings:after_gsubrs]
    charstrings, _ = _index(cff, top[17][0][0])
    size, offset = top[18][0]
    end = offset + size
    subrs = _dict(cff[offset:end]).get(19)
    if subrs:
        end = max(end, _index(cff, offset + subrs[0][0])[1])
    private, sid, count = cff[offset:end], 391 + len(strings), len(charstrings)
    kept = b"".join(raw for op, (_, raw) in top.items() if op in (5, 1207))  # FontBBox, FontMatrix

    def top_dict(charset, select, glyphs, fds):
        return (_op(1230, sid, sid + 1, 0) + kept + _op(1234, count + shift) + _op(15, charset)
                + _op(1237, select) + _op(17, glyphs) + _op(1236, fds))

    head, string_index = b"\x01\x00\x04\x04", _write_index(strings + [b"Adobe", b"Identity"])
    charset = b"\x02" + struct.pack(">HH", 1 + shift, count - 2)
    select = b"\x03" + struct.pack(">HHBH", 1, 0, 0, count)
    glyphs = _write_index(charstrings)
    at_charset = len(head) + len(names) + len(_write_index([top_dict(0, 0, 0, 0)])) + len(string_index) + len(gsubrs)
    at_select = at_charset + len(charset)
    at_glyphs = at_select + len(select)
    at_fds = at_glyphs + len(glyphs)
    at_private = at_fds + len(_write_index([_op(18, size, 0)]))
    return (head + names + _write_index([top_dict(at_charset, at_select, at_glyphs, at_fds)]) + string_index
            + gsubrs + charset + select + glyphs + _write_index([_op(18, size, at_private)]) + private)


def _composite_paper(kind: str):
    """A page whose paragraph is set in a composite font (Identity-H, a
    ToUnicode) whose program maps no character to a glyph: ``cid``, a bare
    CID-keyed CFF (Nimbus Roman, its CIDs not its GIDs), or ``ttf``, a
    TrueType program without a cmap (Droid Sans Fallback as MuPDF's
    subsetter leaves it, named GammaTrue here), its CIDs mapped to its
    GIDs by a ``/CIDToGIDMap``. → (the file, the font's name)."""
    pymupdf = _mupdf()
    from gamma.pdf_font_cmap import _names, _sfnt, _sfnt_tables

    letters = sorted(set("".join(_COMPOSITE_LINES)))
    if kind == "cid":
        own, name = pymupdf.Font("tiro"), "GammaCID"
        program = _cid_keyed(own.buffer, 100)
        cid = {c: own.has_glyph(ord(c)) + 100 for c in letters}
    else:
        own, name = pymupdf.Font("cjk"), "GammaTrue"
        subset = pymupdf.open()
        sheet = subset.new_page()
        sheet.insert_font(fontname="D", fontbuffer=own.buffer)
        sheet.insert_text((40, 60), "".join(letters), fontname="D")
        subset.subset_fonts()  # which drops the cmap
        subset = pymupdf.open(stream=subset.tobytes(garbage=1), filetype="pdf")
        tables = _sfnt_tables(subset.extract_font(subset.get_page_fonts(0)[0][0])[3])
        tables.pop(b"cmap", None)
        tables[b"name"] = _names(name)
        program = _sfnt(b"\x00\x01\x00\x00", tables)
        cid = {c: k + 1 for k, c in enumerate(letters)}
    doc = pymupdf.open()
    page = doc.new_page(width=PAGE_W, height=PAGE_H)
    page.insert_text((40, 200), "Kept paragraph stays", fontname="helv", fontsize=10)

    def obj(text, stream=None):
        xref = doc.get_new_xref()
        doc.update_object(xref, text)
        if stream is not None:
            doc.update_stream(xref, stream)
        return xref

    pairs = "".join(f"<{cid[c]:04X}> <{ord(c):04X}>\n" for c in letters)
    to_unicode = obj("<<>>", (
        "/CIDInit /ProcSet findresource begin 12 dict begin begincmap /CMapName /Gamma def /CMapType 2 def\n"
        "1 begincodespacerange <0000> <FFFF> endcodespacerange\n"
        f"{len(letters)} beginbfchar\n{pairs}endbfchar\nendcmap CMapName currentdict /CMap defineresource pop end end"
    ).encode())
    if kind == "cid":
        file_key, subtype, extra = "FontFile3", "CIDFontType0", ""
        program_xref = obj("<< /Subtype /CIDFontType0C >>", program)
    else:
        file_key, subtype = "FontFile2", "CIDFontType2"
        program_xref = obj(f"<< /Length1 {len(program)} >>", program)
        to_gid = [0] * (max(cid.values()) + 1)
        for c, k in cid.items():
            to_gid[k] = own.has_glyph(ord(c))
        extra = f"/CIDToGIDMap {obj('<<>>', struct.pack(f'>{len(to_gid)}H', *to_gid))} 0 R"
    widths = " ".join(f"{cid[c]} [{round(own.glyph_advance(ord(c)) * 1000)}]" for c in letters)
    descriptor = obj(f"<< /Type /FontDescriptor /FontName /ABCDEF+{name} /Flags 6 /FontBBox [-200 -250 1100 950] "
                     f"/ItalicAngle 0 /Ascent 900 /Descent -250 /CapHeight 680 /StemV 80 "
                     f"/{file_key} {program_xref} 0 R >>")
    descendant = obj(f"<< /Type /Font /Subtype /{subtype} /BaseFont /ABCDEF+{name} /CIDSystemInfo "
                     f"<< /Registry (Adobe) /Ordering (Identity) /Supplement 0 >> "
                     f"/FontDescriptor {descriptor} 0 R /W [{widths}] {extra} >>")
    font = obj(f"<< /Type /Font /Subtype /Type0 /BaseFont /ABCDEF+{name} /Encoding /Identity-H "
               f"/DescendantFonts [{descendant} 0 R] /ToUnicode {to_unicode} 0 R >>")
    held, resources = doc.xref_get_key(page.xref, "Resources")
    if held == "xref":
        doc.xref_set_key(int(resources.split()[0]), "Font/FC", f"{font} 0 R")
    else:
        doc.xref_set_key(page.xref, "Resources/Font/FC", f"{font} 0 R")
    text = "".join(f"BT /FC 10 Tf 40 {PAGE_H - y} Td <{''.join(f'{cid[c]:04X}' for c in line)}> Tj ET\n"
                   for y, line in zip((60, 74), _COMPOSITE_LINES))
    contents = page.get_contents()[-1]
    doc.update_stream(contents, doc.xref_stream(contents) + b"\n" + text.encode())
    return doc.tobytes(garbage=1, deflate=True), name


@pytest.mark.parametrize("kind", ["cid", "ttf"])
def test_a_composite_font_whose_program_maps_no_character_is_used_through_its_tounicode(kind):
    """LuaLaTeX and XeLaTeX embed every OpenType font as a bare CID-keyed
    CFF, which maps no character; the file's ToUnicode does
    (gamma/pdf_font_cmap.py), as pdf.js reads it for the overlay."""
    pymupdf = _mupdf()
    data, name = _composite_paper(kind)
    src = pymupdf.open(stream=data, filetype="pdf")
    assert "the quick brown fox" in src[0].get_text()
    (xref,) = [f[0] for f in src.get_page_fonts(0) if name in f[3]]
    assert not pymupdf.Font(fontbuffer=src.extract_font(xref)[3]).has_glyph(ord("t")), "its program names no letter"
    (face,) = {f for t, _, _, f, _ in _shown(src[0]) if t.startswith("Alpha")}
    block = _alpha_block(src[0], ["the lazy brown dog jumps"]) | {"face": face}
    out = _write(data, [{"page": 1, "blocks": [block]}], "en")
    assert "the lazy brown dog jumps" in _text(out).replace("\n", " "), "its ToUnicode in the export reads back"
    assert {f for t, _, _, _, f in _spans(out) if t.strip() and t.strip() in "the lazy brown dog jumps"} == {name}
    # each glyph drawn is the letter's, not an empty one at another index
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    for word in ("lazy", "jumps"):
        (area,) = page.search_for(word)
        assert min(page.get_pixmap(dpi=200, clip=area).samples) < 128, word
    assert len([f for f in page.get_fonts() if name in f[3]]) == 1
    other = _write(data, [{"page": 1, "blocks": [_alpha_block(src[0], ["Die Wörter"]) | {"face": face}]}], "de")
    used = {f for t, _, _, _, f in _spans(other) if t.strip() and t.strip() in "Die Wörter"}
    assert used and name not in used, "a letter it lacks: the serif"


def test_a_composite_fonts_program_is_kept_when_it_maps_its_characters():
    """MuPDF's own Type0 font holds a name-keyed CFF, whose glyph names
    FreeType maps: no cmap is made for it."""
    from gamma import pdf_font_cmap

    pymupdf = _mupdf()
    src = pymupdf.open(stream=_font_paper(), filetype="pdf")
    fonts = [(xref, ext) for xref, ext, *_ in src.get_page_fonts(0, full=True) if ext == "cid"]
    assert fonts
    for xref, ext in fonts:
        assert pdf_font_cmap.unicode_program(pymupdf, src, xref, ext, src.extract_font(xref)[3]) is None


def test_a_mask_stops_at_the_next_lines_top_or_at_its_own_bottom():
    """gamma/pdf_translated._masks against the overlay's blockBoxes."""
    from gamma.pdf_translated import PAD, _masks

    def bottoms(*lines):
        return [m[3] - PAD for m in _masks({"size": 10, "lines": list(lines)})]

    assert bottoms((0, 0, 100, 9), (0, 12, 100, 21)) == [12, 21], "the leading is covered"
    assert bottoms((0, 0, 100, 15), (0, 12, 60, 21)) == [15, 21], "glyph boxes that overlap keep the line's own bottom"
    assert bottoms((0, 0, 100, 9), (0, 40, 100, 49)) == [9, 49], "a gap of more than a line is no leading"


def _panel_paper(rotation=0, fill=(0.93, 0.87, 0.74), ink=(0, 0, 0.8)):
    """A page with a tinted panel under the Alpha paragraph, and a heading in
    ``ink`` on plain paper. As ``_paper``, drawn under its text."""
    pymupdf = _mupdf()

    def paint(page, put):
        page.draw_rect(pymupdf.Rect(30, 48, 270, 92) * page.derotation_matrix, color=None, fill=fill, overlay=False)
        at = pymupdf.Point(40, 160) * page.derotation_matrix
        page.insert_text(at, "Heading in colour", fontname="hebo", fontsize=14, rotate=rotation, color=ink)

    return _paper(rotation, extra=paint)


def _colours(data, rect, zoom=4, page=0):
    """The share of each colour in a part of the display frame of a page."""
    from collections import Counter

    pymupdf = _mupdf()
    pixmap = pymupdf.open(stream=data, filetype="pdf")[page].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=rect, alpha=False)
    s = pixmap.samples
    return Counter(zip(s[0::3], s[1::3], s[2::3]))


def _heading_block(page):
    return {"lines": [_line_box(page, "Heading in colour")], "size": 14, "parts": ["Farbe"]}


def _span_colour(data, starts):
    page = _mupdf().open(stream=data, filetype="pdf")[0]
    return next(s["color"] for b in page.get_text("dict")["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
                if s["text"].startswith(starts))


@pytest.mark.parametrize("rotation", [0, 90])
def test_a_mask_takes_the_ground_it_stands_on_and_nothing_the_viewer_sent(rotation):
    """A page exported may never have been shown: the block carries no colour."""
    pymupdf = _mupdf()
    data = _panel_paper(rotation)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    assert {"lines", "size", "parts"} == set(_alpha_block(src, []))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Kurz"])]}], "de")
    panel = pymupdf.Rect(32, 50, 268, 90)
    (ground, n), = _colours(data, pymupdf.Rect(32, 50, 36, 52)).most_common(1)
    found = _colours(out, panel)
    assert found.most_common(1)[0][0] == ground
    assert found[(255, 255, 255)] == 0, "no white slab"
    near = [c for c in found if c != ground and max(abs(a - b) for a, b in zip(c, ground)) < 12]
    assert sum(found[c] for c in near) < sum(found.values()) * 0.01, "and no second ground"


def test_a_coloured_ink_goes_with_its_words_and_the_usual_one_stays():
    pymupdf = _mupdf()
    data = _panel_paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    blocks = [_heading_block(src), _alpha_block(src, ["Absatz"])]
    out = _write(data, [{"page": 1, "blocks": blocks}], "de")
    assert _span_colour(out, "Farbe") == 0x0000cc
    assert _span_colour(out, "Absatz") == 0x1c1c1c


def test_white_ink_on_a_dark_band_stays_white():
    pymupdf = _mupdf()
    data = _panel_paper(fill=(0.05, 0.1, 0.3), ink=(0, 0, 0))

    def white(page, put):
        page.draw_rect(pymupdf.Rect(30, 140, 270, 170), color=None, fill=(0.05, 0.1, 0.3), overlay=True)
        page.insert_text(pymupdf.Point(40, 160), "White on a band of colour", fontname="hebo", fontsize=14, color=(1, 1, 1))

    doc = pymupdf.open(stream=data, filetype="pdf")
    white(doc[0], None)
    data = doc.tobytes()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    block = {"lines": [_line_box(src, "White on a band")], "size": 14, "parts": ["Weiss"]}
    assert _span_colour(_write(data, [{"page": 1, "blocks": [block]}], "de"), "Weiss") == 0xffffff


def test_text_over_a_gradient_takes_paper_not_its_own_colour_for_ground():
    """A gradient's many colours leave the text's own the commonest: that is no
    ground, or the mask would be black under black words."""
    pymupdf = _mupdf()

    def gradient(page, put):
        for k in range(100):
            v = k / 99
            page.draw_rect(pymupdf.Rect(30 + 2.4 * k, 48, 32.4 + 2.4 * k, 92), color=None, fill=(v, 1 - v, 0.5),
                           overlay=False)

    data = _paper(extra=gradient)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Kurz"])]}], "de"), filetype="pdf")
    fills = [d.get("fill") for d in out[0].get_drawings() if d.get("fill") in ((1.0, 1.0, 1.0), (0.0, 0.0, 0.0))]
    assert (1.0, 1.0, 1.0) in fills and (0.0, 0.0, 0.0) not in fills
    assert _span_colour(out.tobytes(), "Kurz") == 0x1c1c1c


def test_a_band_no_taller_than_its_line_is_its_ground_though_paper_rings_it():
    """White on a band inside its line's box, as hard.pdf p3's banner: the ring
    above and below is paper, and with the white text it outvotes the band."""
    pymupdf = _mupdf()
    green = (0.2, 0.6, 0.3)

    def band(page, put):
        page.insert_text(pymupdf.Point(40, 160), "White on a tight band of green", fontname="hebo", fontsize=11, color=(1, 1, 1))

    doc = pymupdf.open(stream=_paper(extra=band), filetype="pdf")
    line = pymupdf.Rect(_line_box(doc[0], "White on a tight band"))
    doc[0].draw_rect(pymupdf.Rect(0, line.y0 + 0.5, 300, line.y1 - 0.5), color=None, fill=green, overlay=False)
    data = doc.tobytes()
    block = {"lines": [list(line)], "size": 11, "parts": ["Weiss"]}
    out = _write(data, [{"page": 1, "blocks": [block]}], "de")
    assert _span_colour(out, "Weiss") == 0xffffff
    fills = [d.get("fill") for d in pymupdf.open(stream=out, filetype="pdf")[0].get_drawings()]
    assert (1.0, 1.0, 1.0) not in fills, "no white stripe over the band"


def test_an_ink_too_faint_to_read_on_its_ground_is_no_ink():
    """Yellow on paper ties black for farthest from white, and outweighs it."""
    pymupdf = _mupdf()
    data = _panel_paper(ink=(1, 0.83, 0))
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    assert _span_colour(_write(data, [{"page": 1, "blocks": [_heading_block(src)]}], "de"), "Farbe") == 0x1c1c1c


def test_a_block_stops_where_the_next_one_starts_inside_its_box():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    long = " ".join(f"word{n}" for n in range(30))
    one = pymupdf.Rect(_line_box(src, "Alpha line one"))
    foot = one.y1 + 2
    block = {**_alpha_block(src, [long]), "foot": foot}
    page = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [block]}]), filetype="pdf")[0]
    spans = [(b, y) for t, b, _, _, y in _shown(page) if "word" in t]
    assert spans and all(y < foot - 0.75 for _, y in spans), "every baseline above the next block's text"


def test_a_highlight_annotation_is_no_ground():
    pymupdf = _mupdf()
    doc = pymupdf.open(stream=_paper(), filetype="pdf")
    page = doc[0]
    for box in (_line_box(page, "Alpha line one"), _line_box(page, "Alpha line two")):
        annot = page.add_highlight_annot(pymupdf.Rect(box))
        annot.set_colors(stroke=(1, 1, 0))
        annot.update()
    data = doc.tobytes()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Kurz"])]}], "de"), filetype="pdf")[0]
    assert any(d.get("fill") == (1.0, 1.0, 1.0) for d in out.get_drawings())


def test_the_sample_leaves_mupdfs_smoothing_as_it_was_and_a_big_page_coarser():
    pymupdf = _mupdf()
    from gamma import pdf_translated

    data = _panel_paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    pymupdf.TOOLS.set_aa_level(4)
    try:
        _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Kurz"])]}], "de")
        assert pymupdf.TOOLS.show_aa_level()["graphics"] == 4
    finally:
        pymupdf.TOOLS.set_aa_level(8)

    doc = pymupdf.open()
    doc.new_page(width=5000, height=5000)
    sample = pdf_translated._Sample(doc[0], pdf_translated._Frame(doc[0]), pymupdf)
    assert sample.width * sample.height <= pdf_translated.SAMPLE_PIXELS * 1.01
    assert sample.scale == pytest.approx(sample.width / 5000, rel=0.01)


def test_a_pages_default_colour_spaces_outlive_the_redaction():
    """MuPDF's rewritten page keeps only the resources its content names, so
    a page's /DefaultCMYK, which ``k`` reads through, went, and with it the
    colour of every CMYK fill on the page."""
    pymupdf = _mupdf()
    doc = pymupdf.open(stream=_paper(), filetype="pdf")
    page = doc[0]
    space = doc.get_new_xref()
    doc.update_object(space, "[/CalRGB << /WhitePoint [0.95 1 1.09] /Gamma [2.2 2.2 2.2] >>]")
    resources = int(doc.xref_get_key(page.xref, "Resources")[1].split()[0])
    doc.xref_set_key(resources, "ColorSpace", f"<< /DefaultRGB {space} 0 R >>")
    data = doc.tobytes()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    assert src.parent.xref_get_key(src.xref, "Resources/ColorSpace/DefaultRGB")[0] == "xref"
    out = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Kurz"])]}], "de"), filetype="pdf")
    kind, value = out.xref_get_key(out[0].xref, "Resources/ColorSpace/DefaultRGB")
    assert kind == "xref"
    assert "CalRGB" in out.xref_object(int(value.split()[0]))


def test_a_translation_past_the_floor_stays_whole_and_runs_out_of_its_box():
    """As on screen, 5% is as small as it gets and nothing is cut."""
    pymupdf = _mupdf()
    from gamma import pdf_translated

    assert pdf_translated.MIN_SCALE == 0.05
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    words = [f"wort{n}" for n in range(9000)]
    block = {"lines": [_line_box(src, "Alpha line two")], "size": 10, "parts": [" ".join(words)]}
    out = _write(data, [{"page": 1, "blocks": [block]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    spans = [s for b in page.get_text("dict", clip=pymupdf.INFINITE_RECT())["blocks"] for ln in b.get("lines", []) for s in ln["spans"]
             if "wort" in s["text"]]
    assert {round(s["size"], 2) for s in spans} == {0.5}


def test_a_long_translation_shrinks_until_it_fits_its_box():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    long = " ".join(f"word{n}" for n in range(60))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, [long])]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    spans = [(t, b, y) for t, b, _, _, y in _shown(page) if "word" in t]
    assert " ".join(t for t, _, _ in spans).split() == long.split()  # every word, in order
    alpha = pymupdf.Rect(_line_box(src, "Alpha line one")) | pymupdf.Rect(_line_box(src, "Alpha line two"))
    assert all(_within(alpha, b, y) for _, b, y in spans)
    sizes = {round(s["size"], 2) for blk in page.get_text("dict")["blocks"] for ln in blk.get("lines", [])
             for s in ln["spans"] if "word" in s["text"]}
    assert max(sizes) < 10


@pytest.fixture
def pictures(monkeypatch):
    """Every formula drawn as a picture, as the writer draws one it cannot
    draw as itself (``pdf_translated._vectors``)."""
    from gamma import pdf_translated

    monkeypatch.setattr(pdf_translated, "_vectors", lambda *args: {})


def _beta_block(page, parts):
    return {"lines": [_line_box(page, "Beta"), _line_box(page, "and more")], "size": 10, "parts": parts}


def _in_line(page, starts="Vor", text="x"):
    """The spans of ``text`` on the line of the span that starts with
    ``starts``, the translation's: ``[(box, direction, font, baseline)]``.
    The fixture's own "x" is in no line the blocks send, so it stays where
    it was, off that line."""
    spans = _shown(page)
    (line,) = {round(base, 1) for t, *_, base in spans if t.startswith(starts)}
    return [(box, direction, font, base) for t, box, direction, font, base in spans
            if t.strip() == text and abs(base - line) < 0.5]


def test_a_formula_comes_back_as_itself_in_the_papers_own_glyph_on_the_baseline():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    formula = _formula(src)
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", formula, " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    text = page.get_text("text")
    assert "Beta" not in text and "Vor" in text and "und nach" in text
    # no picture and no hidden text: the page's own glyph, in its own font,
    # at its size (the block was not shrunk), on the translation's baseline
    assert not page.get_images() and not _hidden(page)
    (box, direction, font, base), = _in_line(page)
    (vor,) = {base for t, *_, base in _shown(page) if t.startswith("Vor")}
    x0, y0, x1, y1 = formula["box"]
    assert font == "Times-Italic" and direction == (1.0, 0.0) and abs(base - vor) < 0.05
    assert abs(box.width - (x1 - x0)) < 0.05 and abs(box.height - (y1 - y0)) < 0.05
    # between the words around it
    words = {t.strip(): b for t, b, *_ in _shown(page) if t.strip() in ("Vor", "und nach")}
    assert words["Vor"].x1 <= box.x0 + 0.5 and box.x1 <= words["und nach"].x0 + 0.5


def test_a_formula_drawn_as_itself_keeps_the_pages_black_not_the_translations_ink():
    """The formula's content runs from a page's usual state, not from what
    the sheet set before it: the translation's ink is the overlay's dark
    grey, the formula's own black (it sets none)."""
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    colours = {"".join(chr(c[0]) for c in span["chars"]).strip(): span["color"] for span in page.get_texttrace()}
    assert max(colours["Vor"]) > 0.05 and max(colours["x"]) == 0


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_formula_drawn_as_itself_reads_in_its_place_at_every_turn(rotation):
    from pypdf import PdfReader

    pymupdf = _mupdf()
    data = _paper(rotation=rotation)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    # its glyph is its text, copied with the words around it, in their order
    for got in (page.get_text("text"), PdfReader(io.BytesIO(out)).pages[0].extract_text()):
        assert "Vor x und nach" in " ".join(got.split())
    # upright on screen, as the words are
    (_, direction, font, _), = _in_line(page)
    (vor,) = {d for t, _, d, *_ in _shown(page) if t.startswith("Vor")}
    assert font == "Times-Italic" and direction == vor
    assert not page.get_images() and not _hidden(page)


def test_no_original_text_rides_along_with_a_formula_even_behind_a_clip():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    everything = _text(out, clip=False)
    assert "Beta" not in everything and "speaks" not in everything
    assert everything.count("Alpha line one") == 1 and everything.count("Kept paragraph") == 1


def test_a_formula_takes_from_a_form_on_its_page_what_lies_in_its_box():
    """A page whose content is a form XObject (a page placed in another, a
    figure, an export exported again): MuPDF keeps every form a content
    draws, so the formula's is filtered as its own copy, not taken whole."""
    pymupdf = _mupdf()
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H).show_pdf_page(pymupdf.Rect(0, 0, PAGE_W, PAGE_H),
                                                             pymupdf.open(stream=_paper(), filetype="pdf"), 0)
    data = doc.tobytes()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    everything = _text(out, clip=False)
    assert "Vor x und nach" in " ".join(everything.split())
    # the formula's "x" and the page's own: nothing else of the form twice
    assert everything.count("x") == 2 and everything.count("Kept paragraph") == 1
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert not page.get_images() and not _hidden(page)


def test_a_formula_over_a_picture_of_its_page_is_a_picture():
    """A scan under its text layer, an equation set as an image: what shows
    in the box is a picture, so the formula is drawn as one, its text hidden
    under it."""
    pymupdf = _mupdf()

    def scanned(page, _put):
        grey = pymupdf.Pixmap(pymupdf.csGRAY, pymupdf.IRect(0, 0, 4, 4), False)
        grey.clear_with(128)
        page.insert_image(pymupdf.Rect(110, 108, 118, 124), pixmap=grey)

    data = _paper(extra=scanned)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert len(page.get_images()) == 2  # the page's own, and the formula's
    assert [text for text, _ in _hidden(page)] == ["x"]
    assert all(font != "Times-Italic" for _, _, font, _ in _in_line(page))


def test_an_overstruck_glyph_is_drawn_where_the_page_drew_it():
    """MuPDF 1.28's content filter drops a ``Tm`` that repeats the line's
    after text was shown, so a glyph drawn over the one before it (an
    accent, the bar of an ħ, where a producer places every run by ``Tm``, as
    LuaTeX does) would move: the writer checks every glyph's place, and a
    formula whose glyphs moved is a picture."""
    pymupdf = _mupdf()

    def overstruck(page, put):
        put(200, 120, "h")  # its font, helv, among the page's resources
        doc = page.parent
        xref = page.get_contents()[-1]
        doc.update_stream(xref, doc.xref_stream(xref)
                          + b"\nBT /helv 10 Tf 1 0 0 1 200 280 Tm (h)Tj 1 0 0 1 200 280 Tm (-)Tj ET\n")

    data = _paper(extra=overstruck)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    boxes = [box for text, box, *_ in _shown(src) if box.x0 >= 199 and box.y1 > 110]
    box = boxes[0]
    for other in boxes[1:]:
        box |= other
    formula = {"page": 1, "box": list(box), "base": 120, "text": "ħ"}
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", formula, " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    if page.get_images():
        assert [text for text, _ in _hidden(page)] == ["ħ"]
    else:  # a MuPDF that keeps the Tm: both glyphs at one point, as drawn
        spans = _shown(page)
        (line,) = {round(base, 1) for t, *_, base in spans if t.startswith("Vor")}
        starts = [b.x0 for t, b, *_, base in spans if t in ("h", "-", "h-") and abs(base - line) < 0.5]
        assert starts and max(starts) - min(starts) < 0.05


def _hidden(page):
    """The text a page writes but does not paint (render mode 3), and where:
    ``[(text, box)]`` per span."""
    return [("".join(chr(c[0]) for c in span["chars"]), _mupdf().Rect(span["bbox"]))
            for span in page.get_texttrace() if span["type"] == 3]


def test_a_formula_drawn_as_a_picture_comes_back_on_the_baseline(pictures):
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    formula = _formula(src)
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", formula, " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    text = page.get_text("text")
    assert "Beta" not in text and "Vor" in text and "und nach" in text
    (image,) = page.get_image_info()
    picture = pymupdf.Rect(image["bbox"])
    (baseline,) = {round(base, 2) for t, _, _, _, base in _shown(page) if t.startswith("Vor")}
    # the picture's share below the baseline is the formula's own descent
    x0, y0, x1, y1 = formula["box"]
    below = (y1 - formula["base"]) / (y1 - y0)
    assert abs((picture.y1 - baseline) - below * picture.height) < 0.3
    assert abs(picture.width / picture.height - (x1 - x0) / (y1 - y0)) < 0.05
    # it is the formula, drawn on a clear ground: black ink whose shape is
    # in the picture's mask, at the print resolution
    doc = pymupdf.open(stream=out, filetype="pdf")
    (xref, smask, width, *_), = doc[0].get_images(full=True)
    shape = pymupdf.Pixmap(doc.extract_image(smask)["image"])
    assert smask and width >= (x1 - x0) * 600 / 72 - 1
    assert min(shape.samples) == 0 and max(shape.samples) == 255


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_a_formulas_text_lies_hidden_under_its_picture_where_it_reads(rotation, pictures):
    from pypdf import PdfReader

    pymupdf = _mupdf()
    data = _paper(rotation=rotation)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    out = _write(data, [{"page": 1, "blocks": [_beta_block(src, ["Vor ", _formula(src), " und nach"])]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    # copied with the words around it, in their order: the text stands where
    # the placeholder was drawn, not after the paragraph
    assert "Vor x und nach" in page.get_text("text").replace("\n", " ")
    assert "Vor x und nach" in PdfReader(io.BytesIO(out)).pages[0].extract_text().replace("\n", " ")
    # painted nowhere, under the picture and across it, turned with the page
    (text, box), = _hidden(page)
    picture = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    assert text == "x" and (picture + (-0.1, -0.1, 0.1, 0.1)).contains(box)
    assert min(box.width / picture.width, box.height / picture.height) > 0.6
    # the placeholders are gone: the page draws the formula's picture alone
    assert len(page.get_images()) == 1


def test_a_formulas_math_copies_as_itself_past_the_first_plane_too(pictures):
    from pypdf import PdfReader

    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    math = "∑ᵢ 𝑥ᵢ² ⊗ ℝ → 𝒜"  # Noto Sans Math's, and math italic and script past U+FFFF
    block = _beta_block(src, ["Vor ", {**_formula(src), "text": math}, " und 𝑓 nach"])
    out = _write(data, [{"page": 1, "blocks": [block]}], "de")
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert "".join(text for text, _ in _hidden(page)) == math
    for got in (page.get_text("text"), PdfReader(io.BytesIO(out)).pages[0].extract_text()):
        assert f"Vor {math} und 𝑓 nach" in got.replace("\n", " ")
    assert any("Noto Sans Math" in font[3] for font in page.get_fonts(full=True))


def test_a_formulas_latin_letters_take_the_translations_face_not_a_third_one(pictures):
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    block = _beta_block(src, ["Vor ", {**_formula(src), "text": "µ·é × α"}, " und nach"])
    page = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [block]}], "de"), filetype="pdf")[0]
    assert "".join(text for text, _ in _hidden(page)) == "µ·é × α"
    faces = {font[3].split("+")[-1] for font in page.get_fonts(full=True)}
    assert faces == {"Charis SIL Regular", "Noto Sans Math Regular", "Helvetica", "Times-Italic"}, faces


def test_a_formula_of_blanks_leaves_no_hidden_text(pictures):
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    block = _beta_block(src, ["Vor ", {**_formula(src), "text": " "}, " und nach"])
    page = pymupdf.open(stream=_write(data, [{"page": 1, "blocks": [block]}], "de"), filetype="pdf")[0]
    assert not _hidden(page) and len(page.get_images()) == 1


def test_a_formula_from_another_page_is_drawn_from_that_page():
    pymupdf = _mupdf()
    data = _paper(pages=2)
    doc = pymupdf.open(stream=data, filetype="pdf")
    block = _alpha_block(doc[0], ["joined ", _formula(doc[1], pn=2), " here"])
    out = _write(data, [{"page": 1, "blocks": [block]}])
    result = pymupdf.open(stream=out, filetype="pdf")
    (_, _, font, _), = _in_line(result[0], "joined")
    assert font == "Times-Italic" and not result[0].get_images()
    assert "joined x here" in " ".join(result[0].get_text("text").split())
    assert "Beta speaks of" in result[1].get_text("text")  # page 2 itself untouched


def test_a_formulas_content_names_its_resources_anew_and_nothing_else():
    """``_renamed``: a resource an operator names takes its new name; the
    same name in a string, a dictionary or an array stays as it is; a name
    the formula's resources lack, or an inline image, gives up."""
    from gamma.pdf_translated import _renamed

    names = {("Font", "F1"): "Gv12", ("XObject", "Fm0"): "Gv13", ("Properties", "MC0"): "Gv14",
             ("Pattern", "P0"): "Gv15", ("ColorSpace", "CS0"): "Gv16"}
    content = (b"q BT/F1 9 Tf[(/F1 9 Tf\\) \\(x)-3(y)]TJ ET /Fm0 Do /Span<</ActualText(/F1)/F1 2>>BDC EMC"
               b" /OC /MC0 BDC EMC /CS0 cs .5 /P0 scn [/F1] 0 d % /F1 9 Tf\n/F#31 8 Tf Q")
    assert _renamed(content, names) == (
        b"q BT/Gv12 9 Tf[(/F1 9 Tf\\) \\(x)-3(y)]TJ ET /Gv13 Do /Span<</ActualText(/F1)/F1 2>>BDC EMC"
        b" /OC /Gv14 BDC EMC /Gv16 cs .5 /Gv15 scn [/F1] 0 d % /F1 9 Tf\n/Gv12 8 Tf Q")
    assert _renamed(b"/F2 9 Tf", names) is None
    assert _renamed(b"q BI /W 1 /H 1 /BPC 8 /CS /G ID \x00 EI Q", names) is None


def test_a_formula_from_a_page_turned_otherwise_is_written_as_its_text():
    pymupdf = _mupdf()
    flat, turned = _paper(), _paper(rotation=90)
    doc = pymupdf.open(stream=flat, filetype="pdf")
    doc.insert_pdf(pymupdf.open(stream=turned, filetype="pdf"))
    data = doc.tobytes()
    doc = pymupdf.open(stream=data, filetype="pdf")
    block = _alpha_block(doc[0], ["joined ", _formula(doc[1], pn=2), " here"])
    out = _write(data, [{"page": 1, "blocks": [block]}])
    page = pymupdf.open(stream=out, filetype="pdf")[0]
    assert not page.get_image_info() and "joined x here" in page.get_text("text").replace("\n", " ")


def test_a_formula_box_off_its_page_is_written_as_its_text():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    for box in ([1000, 1000, 1010, 1012], [-50, -50, -40, -38], [290, 390, 310, 410]):
        formula = {"page": 1, "box": box, "base": box[3] - 2, "text": "y"}
        out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["vor ", formula, " nach"])]}], "de")
        page = pymupdf.open(stream=out, filetype="pdf")[0]
        assert not page.get_image_info() and "vor y nach" in page.get_text("text").replace("\n", " "), box


def test_a_block_without_parts_loses_its_lines_and_gets_nothing():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    for parts in ([], ["   "]):
        out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, parts)]}])
        text = _text(out)
        assert "Alpha" not in text and "Beta speaks of" in text and "Kept paragraph" in text


def test_what_is_left_out_and_nothing_to_write():
    pymupdf = _mupdf()
    data = _paper()
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    # a page the file has not, a block whose boxes have no area
    assert _write(data, [{"page": 9, "blocks": [_alpha_block(src, ["far away"])]}]) == data
    flat = {"lines": [[40, 60, 40, 70], [50, 60, 90, 60]], "size": 10, "parts": ["nothing"]}
    assert _write(data, [{"page": 1, "blocks": [flat]}]) == data
    # a page sent twice writes both lists
    beta = {"lines": [_line_box(src, "Beta"), _line_box(src, "and more")], "size": 10, "parts": ["Zwei"]}
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Eins"])]}, {"page": 1, "blocks": [beta]}])
    text = _text(out)
    assert "Eins" in text and "Zwei" in text and "Alpha" not in text and "Beta" not in text


def test_a_tall_glyph_of_the_next_line_stays():
    pymupdf = _mupdf()
    def operator(page, put):
        put(60, 107, "Q", size=30)  # a display formula's tall glyph, reaching up into the line above

    data = _paper(extra=operator)
    src = pymupdf.open(stream=data, filetype="pdf")[0]
    (tall,) = [b for t, b, *_ in _shown(src) if t == "Q"]
    line = _line_box(src, "Alpha line two")
    assert tall.intersects(pymupdf.Rect(line)) and not tall.intersects(pymupdf.Rect(_band(line)))
    out = _write(data, [{"page": 1, "blocks": [_alpha_block(src, ["Übersetzt"])]}], "de")
    assert "Q" in _text(out) and "Alpha" not in _text(out)


def test_cjk_and_cyrillic_share_one_subset_font_across_pages():
    pymupdf = _mupdf()
    data = _paper(pages=2)
    doc = pymupdf.open(stream=data, filetype="pdf")
    pages = [{"page": 1, "blocks": [_alpha_block(doc[0], ["译文：原子在光镊中排列整齐。"])]},
             {"page": 2, "blocks": [_alpha_block(doc[1], ["译文：Привет, мир — 第二页。"])]}]
    out = _write(data, pages, "zh-CN")
    result = pymupdf.open(stream=out, filetype="pdf")
    first, second = (page.get_text("text").replace("\n", "") for page in result)
    assert "原子在光镊中排列整齐" in first and "Привет, мир" in second
    fonts = {(xref, name) for page in result for xref, _, _, name, *_ in page.get_fonts()}
    cjk = {xref for xref, name in fonts if "Droid" in name}
    assert len(cjk) == 1  # embedded once, shared by both pages
    assert len(out) < len(data) + 150_000  # subset: the full font is 4 MB


def test_a_file_that_needs_a_password_is_refused():
    pymupdf = _mupdf()
    doc = pymupdf.open(stream=_paper(), filetype="pdf")
    locked = doc.tobytes(encryption=pymupdf.PDF_ENCRYPT_AES_256, user_pw="secret", owner_pw="owner")
    with pytest.raises(ValueError):
        _write(locked, [{"page": 1, "blocks": [{"lines": [[40, 50, 200, 64]], "size": 10, "parts": ["x"]}]}])


@pytest.mark.parametrize("size,height", [(7, 0.3), (10, 1.0), (20, 2.5)])
def test_mupdf_draws_a_picture_in_a_line_where_the_writer_expects_it(size, height):
    """The rule ``pdf_translated._IMAGE_DROP`` stands on: MuPDF reports a
    picture in a line 0.8 of its height above where it draws it, its bottom
    on the baseline. A PyMuPDF that moves it fails here, not in a paper."""
    pymupdf = _mupdf()
    from gamma import pdf_translated

    red = b'<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"><rect width="10" height="10" fill="red"/></svg>'
    archive = pymupdf.Archive()
    archive.add((red, "r.svg"))
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    device = writer.begin_page(pymupdf.Rect(0, 0, 300, 300))
    story = pymupdf.Story(f'<p>Some words <img id="f0" src="r.svg" style="width:{size}px;height:{height * size}px"> after</p>',
                          user_css=pdf_translated._css(size), archive=archive)
    story.place(pymupdf.Rect(0, 0, 280, 280))
    seen = []
    story.element_positions(lambda p: seen.append(p.rect) if p.id == "f0" and p.rect[2] > p.rect[0] else None)
    story.draw(device)
    writer.end_page()
    writer.close()
    page = pymupdf.open(stream=out.getvalue(), filetype="pdf")[0]
    drawn = pymupdf.Rect(page.get_image_info()[0]["bbox"])
    (reported,) = seen
    h = reported[3] - reported[1]
    assert abs(drawn.y1 - (reported[3] + pdf_translated._IMAGE_DROP * h)) < 0.01
    baseline = {round(s["origin"][1], 2) for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])
                for s in ln["spans"] if "words" in s["text"]}
    assert baseline == {round(drawn.y1, 2)}


def test_mupdf_reports_a_picture_in_a_justified_line_where_it_draws_it():
    """A justified line's gaps grow after the line is broken: the place
    MuPDF reports for a picture in it is still where it draws it."""
    pymupdf = _mupdf()
    from gamma import pdf_translated

    archive = pymupdf.Archive()
    archive.add((pdf_translated._PLACEHOLDER, "r.svg"))
    words = "the green bird is a good friend of the old man who sits by the bridge and reads".split()
    html = " ".join(w + (f' <img id="f{k}" src="r.svg" style="width:14px;height:7px">' if k in (3, 9, 20) else "")
                    for k, w in enumerate(words * 2))
    out = io.BytesIO()
    writer = pymupdf.DocumentWriter(out)
    device = writer.begin_page(pymupdf.Rect(0, 0, 300, 300))
    story = pymupdf.Story(f"<p>{html}</p>", user_css=pdf_translated._css(10, setting="text-align:justify;"), archive=archive)
    story.place(pymupdf.Rect(0, 0, 200, 280))
    seen = {}
    story.element_positions(lambda p: seen.setdefault(p.id, p.rect) if p.id and p.rect[2] > p.rect[0] else None)
    story.draw(device)
    writer.end_page()
    writer.close()
    page = pymupdf.open(stream=out.getvalue(), filetype="pdf")[0]
    drawn = sorted(round(i["bbox"][0], 2) for i in page.get_image_info())
    assert drawn == sorted(round(r[0], 2) for r in seen.values())
    ends = {round(ln["bbox"][2]) for b in page.get_text("dict")["blocks"] for ln in b.get("lines", [])[:-1]}
    assert ends == {200}, "the lines but the last are justified"


# --- The export job ----------------------------------------------------------

@pytest.fixture(scope="module")
def user(client):
    make_user("tpdf_user", "tpdf-user-pw")
    return login("tpdf_user", "tpdf-user-pw")


def _pdf_page(c, title, data, **props):
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", data, "application/pdf")})
    assert up.status_code == 200, up.text
    page = make_page(c, title, {"doc_id": up.json()["doc_id"], "source_url": up.json()["source_url"], **props})
    return page, up.json()["doc_id"]


def _highlight(c, page_id):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": [
        {"id": f"hl-{page_id}", "content": "a note", "properties": {
            "quote": "Kept paragraph", "color": "rgba(255, 226, 143, 0.65)",
            "pdf_position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H,
                             "boundingRect": {"x1": 40, "y1": 190, "x2": 160, "y2": 203},
                             "rects": [{"x1": 40, "y1": 190, "x2": 160, "y2": 203}]}},
         "children": []}]})
    assert r.status_code == 200, r.text


def _translation(doc_id, data, text="Übersetzt bleibt übersetzt", lang="de"):
    src = _mupdf().open(stream=data, filetype="pdf")[0]
    return {"doc_id": doc_id, "lang": lang, "pages": [{"page": 1, "blocks": [_alpha_block(src, [text])]}]}


def _start(c, **body):
    return c.post("/api/jobs/export", json=body)


def _finished(c, **body):
    from gamma import jobs

    started = _start(c, **body)
    assert started.status_code == 200, started.text
    return jobs.wait(started.json()["id"])


def _file(c, job):
    assert job["state"] == "done", job["error"]
    r = c.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200, r.text
    return r.content


@pytest.fixture(scope="module")
def paper(user):
    data = _paper(extra=lambda page, put: put(40, 380, "The export job's paper"))
    page, doc_id = _pdf_page(user, "Translated paper", data)
    _highlight(user, page["id"])
    return {"id": page["id"], "doc_id": doc_id, "data": data}


def test_a_page_exports_as_its_translated_annotated_pdf(user, paper):
    from pypdf import PdfReader

    job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                    translation=_translation(paper["doc_id"], paper["data"]))
    out = _file(user, job)
    text = _text(out)
    assert "Übersetzt bleibt übersetzt" in text.replace("\n", " ") and "Alpha" not in text
    assert "Kept paragraph stays" in text
    annots = PdfReader(io.BytesIO(out)).pages[0]["/Annots"]
    assert any(a.get_object()["/Subtype"] == "/Highlight" for a in annots)
    assert job["params"]["translation"] == "de"
    # without one: the original, and no language in the params
    plain = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False)
    assert "Alpha line one" in _text(_file(user, plain)) and plain["params"]["translation"] == ""


def test_the_translation_goes_with_the_page_not_the_pages_inside_it(user, paper):
    inner, inner_doc = _pdf_page(user, "Inner paper", _paper(extra=lambda page, put: put(40, 380, "Inner")),
                                 parent_page=paper["id"])
    try:
        job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=False, notes=False,
                        sub_pages=True, translation=_translation(paper["doc_id"], paper["data"]))
        z = zipfile.ZipFile(io.BytesIO(_file(user, job)))
        names = z.namelist()
        top = next(n for n in names if n.endswith("Translated paper.pdf"))
        below = next(n for n in names if n.endswith("Inner paper.pdf"))
        assert "Übersetzt" in _text(z.read(top)) and "Alpha" not in _text(z.read(top))
        assert "Alpha line one" in _text(z.read(below))
    finally:
        user.delete(f"/api/blocks/{inner['id']}")


def test_a_translation_of_another_file_fails_the_export(user, paper):
    job = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                    translation=_translation("0" * 64, paper["data"]))
    assert job["state"] == "failed" and "changed after it was translated" in job["error"]


def test_what_a_translated_export_refuses(user, paper, monkeypatch):
    translation = _translation(paper["doc_id"], paper["data"])
    for body, status in [
        ({"page_id": paper["id"], "mode": "readable"}, 400),                         # another format
        ({"folder": "anything", "mode": "annotated-pdf"}, 400),                      # a folder
        ({"page_id": paper["id"], "mode": "annotated-pdf",
          "translation": {**translation, "lang": "xx"}}, 422),                       # no such language
        ({"page_id": paper["id"], "mode": "annotated-pdf",
          "translation": {**translation, "pages": []}}, 422),                        # nothing in it
    ]:
        r = _start(user, **body, **({} if "translation" in body else {"translation": translation}))
        assert r.status_code == status, (body, r.text)
    from gamma.routers import export

    # the paper's one block has two line boxes, one part and 26 characters;
    # a formula's box counts its area
    formula = {"page": 1, "box": [40, 50, 140, 70], "base": 66, "text": "x"}
    with_formula = {**translation, "pages": [{"page": 1, "blocks": [
        {**translation["pages"][0]["blocks"][0], "parts": ["a ", formula]}]}]}
    for cap, value, sent in (("TRANSLATION_BLOCKS_MAX", 0, translation), ("TRANSLATION_ITEMS_MAX", 1, translation),
                             ("TRANSLATION_CHARS_MAX", 25, translation),
                             ("TRANSLATION_FORMULA_AREA_MAX", 1999, with_formula)):
        with monkeypatch.context() as m:
            m.setattr(export, cap, value)
            r = _start(user, page_id=paper["id"], mode="annotated-pdf", translation=sent)
            assert r.status_code == 413 and r.json()["detail"].startswith("a translation holds at most"), (cap, r.text)


def test_a_translated_export_starts_again_with_the_translation_it_kept(user, paper):
    """Background tasks' retry names the job (``translation_of``): the
    server kept the translation it was sent for as long as the job is
    listed, a failed or retried one too, and writes it again."""
    first = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                      translation=_translation(paper["doc_id"], paper["data"], text="Wieder übersetzt"))
    again = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                      translation_of=first["id"])
    text = _text(_file(user, again)).replace("\n", " ")
    assert "Wieder übersetzt" in text and "Alpha" not in text
    assert again["params"]["translation"] == "de"
    # the retry keeps it in turn, so it starts again too
    third = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=False, notes=False,
                      translation_of=again["id"])
    assert "Wieder übersetzt" in _text(_file(user, third)).replace("\n", " ")
    # a failed one keeps it, and its retry fails the same way
    failed = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                       translation=_translation("0" * 64, paper["data"]))
    retried = _finished(user, page_id=paper["id"], mode="annotated-pdf", translation_of=failed["id"])
    assert retried["state"] == "failed" and "changed after it was translated" in retried["error"]


def test_what_a_translated_exports_retry_refuses(user, paper):
    first = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False,
                      translation=_translation(paper["doc_id"], paper["data"]))
    plain = _finished(user, page_id=paper["id"], mode="annotated-pdf", highlights=True, notes=False)
    other, _ = _pdf_page(user, "Another paper", _paper())
    make_user("tpdf_other", "tpdf-other-pw")
    stranger = login("tpdf_other", "tpdf-other-pw")
    for who, body, status in [
        (user, {"page_id": paper["id"], "mode": "annotated-pdf", "translation_of": first["id"],
                "translation": _translation(paper["doc_id"], paper["data"])}, 400),     # both
        (user, {"page_id": paper["id"], "mode": "readable", "translation_of": first["id"]}, 400),
        (user, {"folder": "anything", "mode": "annotated-pdf", "translation_of": first["id"]}, 404),  # a page's export
        (user, {"page_id": paper["id"], "mode": "annotated-pdf", "translation_of": plain["id"]}, 404),  # kept none
        (user, {"page_id": other["id"], "mode": "annotated-pdf", "translation_of": first["id"]}, 404),  # another page
        (user, {"page_id": paper["id"], "mode": "annotated-pdf", "translation_of": "0" * 24}, 404),
        (user, {"page_id": paper["id"], "mode": "annotated-pdf", "translation_of": "../x"}, 404),
        (stranger, {"page_id": paper["id"], "mode": "annotated-pdf", "translation_of": first["id"]}, 404),
    ]:
        r = _start(who, **body)
        assert r.status_code == status, (body, r.text)
    # removed from Background tasks: the translation goes with it
    assert user.delete(f"/api/jobs/{first['id']}").status_code == 200
    r = _start(user, page_id=paper["id"], mode="annotated-pdf", translation_of=first["id"])
    assert r.status_code == 404 and "no longer kept" in r.json()["detail"]


def test_a_translated_exports_retry_is_its_own_accounts_only(client):
    """In a shared workspace a co-member cannot start another member's
    translated export again by its job's id: same page, same workspace,
    and still 404, since ``translation_of`` names the account's own export."""
    make_user("tpdf_boss", "tpdf-boss-pw", is_admin=1)
    make_user("tpdf_owner", "tpdf-owner-pw")
    make_user("tpdf_peer", "tpdf-peer-pw")
    ws = login("tpdf_boss", "tpdf-boss-pw").post(
        "/api/workspaces", json={"name": "Translated lab", "kind": "shared", "owner": "tpdf_owner"}).json()["id"]
    owner, peer = login("tpdf_owner", "tpdf-owner-pw"), login("tpdf_peer", "tpdf-peer-pw")
    assert owner.put(f"/api/workspaces/{ws}/members/tpdf_peer", json={"role": "editor"}).status_code == 200
    owner.headers["X-Gamma-Workspace"] = peer.headers["X-Gamma-Workspace"] = ws
    data = _paper()
    page, doc_id = _pdf_page(owner, "Shared paper", data)
    first = _finished(owner, page_id=page["id"], mode="annotated-pdf", highlights=False, notes=False,
                      translation=_translation(doc_id, data))
    assert first["state"] == "done" and first["workspace"] == ws
    # the peer exports the page with a translation of their own...
    assert _finished(peer, page_id=page["id"], mode="annotated-pdf", highlights=False, notes=False,
                     translation=_translation(doc_id, data))["state"] == "done"
    # ... but not with the one the owner's export kept
    r = _start(peer, page_id=page["id"], mode="annotated-pdf", translation_of=first["id"])
    assert r.status_code == 404 and "no longer kept" in r.json()["detail"]
    assert _finished(owner, page_id=page["id"], mode="annotated-pdf", translation_of=first["id"])["state"] == "done"


# --- A folder's export -------------------------------------------------------

def _folder_of_papers(c, name):
    """A folder of two papers and a third in a subfolder:
    ``(folder id, [{id, doc_id, data, title}])``."""
    folder = make_folder(c, name)
    sub = make_folder(c, f"{name}/Sub")
    papers = []
    for title, where in ((f"{name} one", folder), (f"{name} two", folder), (f"{name} three", sub)):
        data = _paper(extra=lambda page, put, title=title: put(40, 380, title))
        page, doc_id = _pdf_page(c, title, data, folders=[where])
        papers.append({"id": page["id"], "doc_id": doc_id, "data": data, "title": title})
    return folder, papers


def _paper_translation(paper, text):
    return {"page_id": paper["id"], **_translation(paper["doc_id"], paper["data"], text=text)}


def _zip_texts(data):
    z = zipfile.ZipFile(io.BytesIO(data))
    return {name.rsplit("/", 1)[-1][:-4]: _text(z.read(name)).replace("\n", " ") for name in z.namelist()}


def test_a_folders_annotated_pdfs_carry_the_translations_of_its_papers(user):
    """Each paper's translation goes into its own PDF, a paper without one
    goes in the original, and one for a page the folder does not hold is
    passed by."""
    folder, (one, two, three) = _folder_of_papers(user, "Translated folder")
    outside_data = _paper()
    outside, outside_doc = _pdf_page(user, "Not in the folder", outside_data)
    job = _finished(user, folder=folder, mode="annotated-pdf", highlights=True, notes=False, translations=[
        _paper_translation(one, "Erstes Papier übersetzt"), _paper_translation(three, "Drittes Papier übersetzt"),
        {"page_id": outside["id"], **_translation(outside_doc, outside_data, text="Nicht im Ordner")}])
    texts = _zip_texts(_file(user, job))
    assert set(texts) == {one["title"], two["title"], three["title"]}
    assert "Erstes Papier übersetzt" in texts[one["title"]] and "Alpha" not in texts[one["title"]]
    assert "Drittes Papier übersetzt" in texts[three["title"]] and "Alpha" not in texts[three["title"]]
    assert "Alpha line one" in texts[two["title"]]
    assert not any("Nicht im Ordner" in t for t in texts.values())
    assert job["params"]["translation"] == "de" and job["params"]["folder"] == folder
    plain = _finished(user, folder=folder, mode="annotated-pdf", highlights=True, notes=False)
    assert plain["params"]["translation"] == ""
    assert all("Alpha line one" in t for t in _zip_texts(_file(user, plain)).values())


def test_a_folders_paper_whose_pdf_changed_is_left_out_and_the_rest_go(user):
    folder, (one, two, _) = _folder_of_papers(user, "Changed folder")
    job = _finished(user, folder=folder, mode="annotated-pdf", highlights=False, notes=False, translations=[
        {**_paper_translation(one, "Veraltet"), "doc_id": "0" * 64}, _paper_translation(two, "Zweites übersetzt")])
    assert job["state"] == "done", job["error"]
    texts = _zip_texts(_file(user, job))
    assert one["title"] not in texts and "Zweites übersetzt" in texts[two["title"]]
    assert any(s["reason"] == "the page's PDF changed after it was translated" for s in job["result"]["skipped"])


def test_a_translated_folder_export_starts_again_with_the_translations_it_kept(user):
    folder, (one, two, _) = _folder_of_papers(user, "Retried folder")
    first = _finished(user, folder=folder, mode="annotated-pdf", highlights=True, notes=False, translations=[
        _paper_translation(one, "Wieder das erste"), _paper_translation(two, "Wieder das zweite")])
    again = _finished(user, folder=folder, mode="annotated-pdf", highlights=True, notes=False,
                      translation_of=first["id"])
    texts = _zip_texts(_file(user, again))
    assert "Wieder das erste" in texts[one["title"]] and "Wieder das zweite" in texts[two["title"]]
    assert again["params"]["translation"] == "de"
    other_folder, _ = _folder_of_papers(user, "Another folder")
    page_job = _finished(user, page_id=one["id"], mode="annotated-pdf", highlights=False, notes=False,
                         translation=_translation(one["doc_id"], one["data"]))
    for body, status in [
        ({"folder": other_folder, "mode": "annotated-pdf", "translation_of": first["id"]}, 404),   # another folder
        ({"page_id": one["id"], "mode": "annotated-pdf", "translation_of": first["id"]}, 404),     # a page
        ({"folder": folder, "mode": "annotated-pdf", "translation_of": page_job["id"]}, 404),      # a page's export
        ({"folder": folder, "mode": "readable", "translation_of": first["id"]}, 400),
    ]:
        r = _start(user, **body)
        assert r.status_code == status, (body, r.text)
    r = _start(user, folder=folder, mode="annotated-pdf", translation_of=page_job["id"])
    assert "export the folder again" in r.json()["detail"]


def test_what_a_translated_folder_export_refuses(user, paper, monkeypatch):
    folder, (one, two, _) = _folder_of_papers(user, "Refusing folder")
    sent = [_paper_translation(one, "Eins"), _paper_translation(two, "Zwei")]
    for body, status in [
        ({"folder": folder, "mode": "notes-pdf", "translations": sent}, 400),                   # another format
        ({"page_id": paper["id"], "mode": "annotated-pdf", "translations": sent}, 400),         # a page
        ({"folder": folder, "mode": "annotated-pdf", "translations": sent,
          "translation": _translation(one["doc_id"], one["data"])}, 400),                         # a page's too
        ({"folder": folder, "mode": "annotated-pdf", "translations": sent, "translation_of": "0" * 24}, 400),
        ({"folder": folder, "mode": "annotated-pdf", "translations": [{**sent[0], "page_id": ""}]}, 422),
    ]:
        r = _start(user, **body)
        assert r.status_code == status, (body, r.text)
    from gamma.routers import export

    # each paper's one block holds "Eins" or "Zwei": four characters, eight together
    with monkeypatch.context() as m:
        m.setattr(export, "TRANSLATION_CHARS_MAX", 7)
        assert _start(user, folder=folder, mode="annotated-pdf", translations=sent[:1]).status_code == 200
        r = _start(user, folder=folder, mode="annotated-pdf", translations=sent)
        assert r.status_code == 413 and r.json()["detail"].startswith("a translation holds at most"), r.text

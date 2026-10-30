"""Zotero annotation items and Gamma's annotation blocks (gamma/zotero_annotations.py):
the reader's sort index and page labels, comments both ways, every kind out and
in on plain, turned, cropped and offset pages, ink stroke ownership, splitting,
field-level applies, and the stability a two-base sync rests on: an item shaped
like the reader's comes back as itself, a block as its geometry, and an apply of
what the block already reads as changes nothing."""

import hashlib
import json
import re

import pymupdf
import pytest

from gamma import highlights, ink, text_box, zotero_local, zotero_sync_blocks
from gamma import zotero_annotations as za
from gamma.pdf_export import annotation_key, popup_text

W, H = 612, 792
LINES = [(72, 100, "Attention is all you need"), (72, 130, "fast high fidelity readout"),
         (300, 400, "middle of the page")]
# Page frames: (view box as written, /Rotate). Boxes are PDF arrays in user
# space, so an offset media box really moves the page's origin.
FRAMES = {
    "plain": {},
    "rot90": {"rotate": 90},
    "rot180": {"rotate": 180},
    "rot270": {"rotate": 270},
    "crop": {"crop": (30, 40, 580, 760)},
    "offset": {"media": (50, 100, 662, 892)},
    "crop-rot90": {"crop": (30, 40, 580, 760), "rotate": 90},
    "offset-crop-rot270": {"media": (50, 100, 662, 892), "crop": (60, 110, 600, 800), "rotate": 270},
}
SORT_RE = re.compile(r"^\d{5}\|\d{6}\|\d{5}$")
COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")


def _pdf(*, rotate=0, crop=None, media=None, labels=None, pages=1, lines=LINES) -> bytes:
    doc = pymupdf.open()
    for _ in range(pages):
        page = doc.new_page(width=W, height=H)
        for x, y, text in lines:
            page.insert_text((x, y), text, fontname="helv", fontsize=11)
        if media:
            doc.xref_set_key(page.xref, "MediaBox", "[%g %g %g %g]" % media)
        if crop:
            doc.xref_set_key(page.xref, "CropBox", "[%g %g %g %g]" % crop)
        if rotate:
            doc.xref_set_key(page.xref, "Rotate", str(rotate))
    if labels:
        doc.set_page_labels(labels)
    return doc.tobytes()


@pytest.fixture(scope="module", params=list(FRAMES))
def framed(request):
    """(frame name, page 1 of a PDF in that frame)."""
    with za.Pages(_pdf(**FRAMES[request.param])) as pages:
        yield request.param, pages.page(1)


def _r(*values):
    return [round(v, 3) for v in values]


def _reader_items(page: za.Page) -> dict:
    """One item of each kind as the reader writes them on ``page``: user
    space, three decimals, inside the view box."""
    x0, y0 = page.view[0], page.view[1]

    def item(key, kind, position, **fields):
        return {"key": key, "version": 3, "itemType": "annotation", "parentItem": "PDFPDFPD",
                "annotationType": kind, "annotationComment": "", "annotationColor": "#ffd400",
                "annotationPageLabel": "1", "annotationSortIndex": "00000|000000|00000",
                "annotationPosition": json.dumps({"pageIndex": 0, **position}, separators=(",", ":")), **fields}

    path1 = _r(*(v for i in range(12) for v in (x0 + 205.5 + 3.137 * i, y0 + 270.25 + 1.069 * (i % 4))))
    path2 = _r(x0 + 250.25, y0 + 300.125, x0 + 260.375, y0 + 310.5, x0 + 270.875, y0 + 305.25)
    return {
        "highlight": item("HHHHHHH2", "highlight",
                          {"rects": [_r(x0 + 120.317, y0 + 410.733, x0 + 299.958, y0 + 421.733),
                                     _r(x0 + 72.004, y0 + 396.62, x0 + 180.31, y0 + 407.62)]},
                          annotationText="a passage  over two\nlines", annotationColor="#ff6666",
                          annotationComment="a <b>bold</b> remark"),
        "underline": item("UUUUUUU2", "underline",
                          {"rects": [_r(x0 + 111.402, y0 + 526.975, x0 + 182.391, y0 + 539.545)]},
                          annotationText="under a line", annotationColor="#2ea8e5"),
        "image": item("IIIIIII2", "image", {"rects": [_r(x0 + 72, y0 + 192.25, x0 + 300.5, y0 + 342.75)]},
                      annotationColor="#a28ae5", annotationComment="an area"),
        "ink": item("KKKKKKK2", "ink", {"width": 2, "paths": [path1, path2]}, annotationColor="#aaaaaa"),
        "note": item("NNNNNNN2", "note", {"rects": [_r(x0 + 200, y0 + 342, x0 + 222, y0 + 364)]},
                     annotationColor="#5fb236", annotationComment="a sticky <i>note</i>"),
        "text": item("TTTTTTT2", "text", {"fontSize": 14, "rotation": page.rotation,
                                          "rects": [_r(x0 + 171, y0 + 351.6, x0 + 243, y0 + 370.2)]},
                     annotationColor="#e56eee", annotationComment="typed text"),
    }


def _block(made: dict, bid="b1") -> dict:
    return {"id": bid, "content": made["content"], "properties": made["props"]}


def _items_of(made: dict, page) -> list:
    """``to_items`` of the block ``from_item`` made, with the row the engine
    keeps for an ink item."""
    rows = ()
    if made["ink"] is not None:
        rows = [(made["slot"], made["stroke_ids"], za.ink_look(made["ink"].strokes[0]))]
    return za.to_items(_block(made), page, ink_file=made["ink"], rows=rows, zotero_type=made["zotero_type"])


def _accepted(fields: dict) -> None:
    """The rules the dataserver holds an annotation to (the research note's
    "What an annotation item is"; tests/zotero_fake.py)."""
    assert fields["annotationType"] in za.TYPES
    assert COLOR_RE.match(fields["annotationColor"])
    assert SORT_RE.match(fields["annotationSortIndex"])
    assert len(fields["annotationPageLabel"]) <= za.LABEL_MAX
    assert isinstance(fields["annotationPosition"], str)
    assert len(fields["annotationPosition"].encode("utf-8")) <= 65535
    assert ("annotationText" in fields) == (fields["annotationType"] in za.TEXT_KINDS)
    pos = json.loads(fields["annotationPosition"])
    for x1, y1, x2, y2 in pos.get("rects") or ():
        assert x1 < x2 and y1 < y2


# --- Pages -------------------------------------------------------------------------------

def test_pages_reads_frames_labels_and_characters(tmp_path):
    data = _pdf(crop=(30, 40, 580, 760), rotate=90, pages=2,
                labels=[{"startpage": 0, "prefix": "A-", "style": "r", "firstpagenum": 4}])
    path = tmp_path / "doc.pdf"
    path.write_bytes(data)
    with za.Pages(path) as pages:
        assert pages.count() == 2
        page = pages.page(1)
        assert page.view == (30.0, 40.0, 580.0, 760.0) and page.rotation == 90
        assert page.size == (720.0, 550.0) and page.frame == (page.view, 90)
        assert (page.label, pages.page(2).label) == ("A-iv", "A-v")
        text = "".join(c for c, _box in page.chars)
        assert text == "".join("".join(line[2].split()) for line in LINES)
        assert all(box[2] > box[0] and box[3] > box[1] for _c, box in page.chars)
        assert any(c == "\n" for c, _box in page.glyphs)
        assert pages.page(3) is None and pages.page(0) is None
        assert pages.md5() == hashlib.md5(data).hexdigest() == za.file_md5(data)
    assert za.Pages(_pdf()).page(1).label == ""


def test_file_md5_follows_the_file_not_its_name(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF one")
    first = za.file_md5(path)
    assert first == hashlib.md5(b"%PDF one").hexdigest() == za.file_md5(str(path))
    path.write_bytes(b"%PDF two, rewritten in place")
    assert za.file_md5(path) == hashlib.md5(b"%PDF two, rewritten in place").hexdigest()
    assert za.file_md5(tmp_path / "missing.pdf") == ""


def test_unreadable_file_has_no_pages():
    with za.Pages(b"not a pdf") as pages:
        assert pages.count() == 0 and pages.page(1) is None


def test_pages_keeps_the_pages_last_asked_for(monkeypatch):
    monkeypatch.setattr(za, "PAGES_KEPT", 2)
    with za.Pages(_pdf(pages=3)) as pages:
        one = pages.page(1)
        assert pages.page(1) is one
        pages.page(2)
        pages.page(3)
        again = pages.page(1)
        assert again is not one and again == one


def test_page_chars_keeps_a_ligature_mupdf_spelled_out_as_one():
    # MuPDF's "fi" ligature: the "i" starts where the next glyph starts.
    glyphs = [("f", (360.75, 648.15, 366.29, 658.11)), ("i", (366.29, 648.15, 369.06, 658.11)),
              ("d", (366.29, 648.15, 371.27, 658.11)), ("e", (371.27, 648.15, 375.7, 658.11)),
              (" ", (375.7, 648.15, 378.0, 658.11)), ("\n", (0, 0, 0, 0)),
              ("1", (50, 100, 55, 108)), ("1", (50, 108, 55, 116))]   # a column of digits stays two
    assert za.page_chars(glyphs) == [("fi", glyphs[0][1]), ("d", glyphs[2][1]), ("e", glyphs[3][1]),
                                     ("1", glyphs[6][1]), ("1", glyphs[7][1])]


# --- the sort index --------------------------------------------------------------------

def _page(chars=(), view=(0, 0, 612, 792), rotation=0, label="", number=1):
    return za.Page(number, view, rotation, list(chars), label)


def test_sort_index_takes_the_rect_with_the_greatest_x2():
    chars = [("a", (10, 700, 15, 710)), ("b", (300, 600, 305, 610)), ("c", (500, 500, 505, 510))]
    # The second rect has the greatest x2, though the first is higher on the page.
    position = {"pageIndex": 0, "rects": [[10, 700, 100, 710], [290, 600, 320, 610], [200, 500, 320, 510]]}
    assert za.sort_index(_page(chars), position) == "00000|000001|00182"


def test_sort_index_of_ink_uses_the_paths_bounding_box_without_width():
    chars = [("a", (10, 700, 15, 710)), ("b", (100, 400, 105, 410))]
    position = {"pageIndex": 2, "width": 40, "paths": [[100, 380, 120, 390], [110, 395, 101, 385]]}
    assert za.sort_index(_page(chars, number=3), position) == "00002|000001|00397"


def test_sort_index_top_is_the_view_height_less_the_top_unoffset_and_never_negative():
    view = (50, 100, 662, 892)
    position = {"pageIndex": 0, "rects": [[60, 500.7, 80, 650.2]]}
    assert za.sort_index(_page(view=view), position) == "00000|000000|00141"   # 792 - 650.2, floored
    high = {"pageIndex": 0, "rects": [[60, 800, 80, 880]]}
    assert za.sort_index(_page(view=view), high).endswith("|00000")


def test_sort_index_formats_as_the_reader_cuts_and_pads():
    position = {"pageIndex": 123456, "rects": [[0, 0, 1, 1]]}
    assert za.sort_index(_page(), position) == "12345|000000|00791"


def test_sort_index_offset_is_the_first_nearest_character():
    with za.Pages(_pdf()) as pages:
        page = pages.page(1)
    text = "".join(c for c, _ in page.chars)
    word = text.index("fidelity")
    box = page.chars[word][1]
    rect = [box[0] + 0.5, box[1] + 1, page.chars[word + 7][1][2], box[3] - 1]
    assert za.sort_index(page, {"pageIndex": 0, "rects": [rect]}).split("|")[1] == f"{word:06d}"
    # Distances: diagonal by the nearest corners, one-sided by the gap, 0 when touching.
    assert za._rects_dist((0, 0, 1, 1), (4, 5, 6, 7)) == 5.0
    assert za._rects_dist((0, 0, 10, 10), (12, 2, 13, 3)) == 2
    assert za._rects_dist((0, 0, 10, 10), (5, 5, 20, 20)) == 0


def test_sort_index_of_reader_shaped_items_on_every_frame(framed):
    _name, page = framed
    for kind, item in _reader_items(page).items():
        sort = za.sort_index(page, json.loads(item["annotationPosition"]))
        assert SORT_RE.match(sort), kind


# --- comments ------------------------------------------------------------------------------

@pytest.mark.parametrize("comment, markdown", [
    ("plain words", "plain words"),
    ("a <b>bold</b> and <i>italic</i> word", "a **bold** and *italic* word"),
    ("<B>loud</B> <I>soft</I>", "**loud** *soft*"),
    ("H<sub>2</sub>O and x<SUP>2</SUP>", "H2O and x2"),
    ("<b>open but never closed", "\\<b>open but never closed"),
    ("<i>a <b>b</b>", "\\<i>a **b**"),
    ("<b>across\nlines</b>", "\\<b>across\nlines\\</b>"),
    ("2 * 3 * 4 and [x](y) and `c`", "2 \\* 3 \\* 4 and \\[x\\](y) and \\`c\\`"),
    ("- not a list\n# not a heading", "\\- not a list\n\\# not a heading"),
    ("a < b & c > d", "a < b & c > d"),
    ("<b> spaced </b>out", "**spaced** out"),
    ("", ""),
])
def test_comment_in_reads_the_readers_marks(comment, markdown):
    assert za.comment_in(comment) == markdown


CORPUS = ["plain", "a <b>bold</b> b", "<i>it</i>alic", "<b><i>both</i></b>", "<i><b>both</b></i>",
          "H<sub>2</sub>O", "<B>case</B>", "x * y * z", "under_score and *stars*", "$5 and $6", "a\\b",
          "[[ref]] ![[embed]] [l](http://x.org)", "see http://x.org/a_b now", "(http://x.org)",
          "~~strike~~ ==mark== <u>u</u> <span style=\"color:red\">s</span>", "1. one\n2. two",
          "- a\n  - b\n\nlast", "  indented\ttabs  ", "<i>a*b</i>", "a<i>b</i>c", "<b>unclosed", "&amp; &lt;",
          "line one\nline two", "\u00a0nbsp", "<b> </b>", "émoji 🎉 <b>ok</b>"]


@pytest.mark.parametrize("comment", CORPUS)
def test_comment_in_and_out_round_trip(comment):
    md = za.comment_in(comment)
    assert za.comment_in(za.comment_out(md)) == md
    # The text Gamma would send back is stable too: a pull then a push changes nothing more.
    assert za.comment_out(za.comment_in(za.comment_out(md))) == za.comment_out(md)


@pytest.mark.parametrize("comment", ["plain words", "a <b>bold</b> and <i>italic</i>", "x * y and [x](y)",
                                     "- dash\n# hash\n1. one", "a < b & c > d", "line\n\nafter a blank",
                                     "see http://x.org/a_b now", "<b><i>both</i></b>"])
def test_a_comment_gamma_can_hold_comes_back_as_it_was(comment):
    assert za.comment_out(za.comment_in(comment)) == comment


def test_comment_out_writes_markdown_as_the_readers_text():
    assert za.comment_out("**bold** and *it* and _it_") == "<b>bold</b> and <i>it</i> and <i>it</i>"
    assert za.comment_out("\\*literal\\* \\# x") == "*literal* # x"
    assert za.comment_out("**two\nlines**") == "<b>two</b>\n<b>lines</b>"
    assert za.comment_out("[http://x.org](http://x.org) [label](http://y.org)") == \
        "http://x.org [label](http://y.org)"
    assert za.comment_out("`**code**` $x_1$") == "`**code**` $x_1$"
    assert za.comment_out("  padded  ") == "padded"


def test_comment_carries_the_nested_notes_as_the_popup_text():
    nested = [(0, "own, skipped", "b1"), (1, "child **one**", "c1"), (2, "grandchild", "c2"), (1, "child\ntwo", "c3")]
    text = za.comment_out("my *note*", nested)
    assert text == "my <i>note</i>\n- child <b>one</b>\n  - grandchild\n- child\ntwo"
    assert text.replace("<i>", "*").replace("</i>", "*").replace("<b>", "**").replace("</b>", "**") == \
        popup_text([(0, "my *note*"), *[(d, md) for d, md, _ in nested[1:]]])
    assert za.comment_out("", nested[1:2]) == "- child <b>one</b>"


def test_comment_parts_splits_off_the_nested_notes_gamma_holds():
    nested = [(1, "child", "c1"), (2, "grand **child**", "c2")]
    suffix = "- child\n  - grand <b>child</b>"
    own = "intro\n- a list in my own note\n- that looks nested"
    assert za.comment_parts(za.comment_out(own, nested), nested) == (za.comment_out(own), suffix)
    assert za.comment_parts("edited in Zotero\n" + suffix, nested) == ("edited in Zotero", suffix)
    assert za.comment_parts(suffix, nested) == ("", suffix)
    assert za.comment_parts("intro\r\n" + suffix.replace("\n", "\r\n"), nested) == ("intro", suffix)
    assert za.comment_parts("intro\n- child changed in Zotero\n  - grand <b>child</b>", nested) is None
    assert za.comment_parts("the nested part deleted", nested) is None
    assert za.comment_parts("- anything\n- at all", ()) == ("- anything\n- at all", "")


# --- colours -------------------------------------------------------------------------------

def test_colours_out_drop_alpha_and_in_take_one():
    assert za.color_out("#9BCDFFA6") == "#9bcdff"
    assert za.color_out("rgba(255, 226, 143, 0.65)") == "#ffe28f"
    assert za.color_out("#abc") == "#aabbcc"
    assert za.color_out(None) == za.color_out("nonsense") == "#ffd400"
    assert za.color_in("#ff6666") == "#ff666673"
    assert za.color_in("#ff6666", keep_alpha=0.65) == "#ff6666a6"
    assert za.color_in("bad") == "#ffd40073"


# --- out: every kind ---------------------------------------------------------------------------

def _highlight_block(page, *, scale=1.5, area=False, **props):
    w, h = page.size
    rects = [(w * 0.2, h * 0.3, w * 0.6, h * 0.32), (w * 0.15, h * 0.33, w * 0.4, h * 0.35)]
    rects = [tuple(round(v * scale, 2) for v in r) for r in rects]
    pos = highlights.position(page.number, round(w * scale, 2), round(h * scale, 2), rects, area=area)
    return {"id": "h1", "content": "my **own** note",
            "properties": {"color": "#9bcdffa6", "quote": "a quote   kept\nas stored", "pdf_position": pos, **props}}


def _ink_file(page, strokes):
    w, h = page.size
    return ink.parse_ink({"format": "gamma-ink", "version": 2,
                          "space": {"kind": "pdf-page", "page": page.number, "width": w, "height": h},
                          "strokes": strokes})


def _stroke(sid, points, color="#1f1f1f", size=2.0, **extra):
    return {"id": sid, "tool": "pen", "color": color, "size": size, "ch": "xy",
            "pts": ink.encode_points([{"x": x, "y": y} for x, y in points]), **extra}


def _ink_block(page, file):
    return {"id": "k1", "content": "ink *note*",
            "properties": {"ink_url": "/api/uploads/" + "a" * 24 + ".ink", "pdf_position": ink.pdf_position(file),
                           "ink_strokes": len(file.strokes)}}


def _box_block(page, **box):
    return {"id": "t1", "content": "Hello *box*\nsecond line",
            "properties": {"text_box": text_box.normalize_text_box({"x": 100, "y": 120, "w": 150, "h": 40,
                                                                    "auto": False, "size": 16, "color": "#d32f2f",
                                                                    "bg": "#fff4b8", **box}),
                           "pdf_page": page.number}}


def test_to_items_writes_a_highlight_as_the_reader_would(framed):
    _name, page = framed
    (item,) = za.to_items(_highlight_block(page), page, nested=[(1, "child", "c1")], author="Tim")
    f = item.fields
    _accepted(f)
    assert item.slot == "" and item.stroke_ids == ()
    assert (f["annotationType"], f["annotationColor"]) == ("highlight", "#9bcdff")
    assert f["annotationText"] == "a quote   kept\nas stored"      # never collapsed
    assert f["annotationComment"] == "my <b>own</b> note\n- child"
    assert f["annotationPageLabel"] == "1" and f["annotationAuthorName"] == "Tim"
    pos = json.loads(f["annotationPosition"])
    assert pos["pageIndex"] == 0 and len(pos["rects"]) == 2
    assert f["annotationPosition"] == json.dumps(pos, separators=(",", ":"))
    assert f["annotationSortIndex"] == za.sort_index(page, pos)
    view = page.view
    for x1, y1, x2, y2 in pos["rects"]:     # on the page as shown, whatever its frame
        assert view[0] <= x1 < x2 <= view[2] and view[1] <= y1 < y2 <= view[3]
    (under,) = za.to_items(_highlight_block(page), page, zotero_type="underline")
    assert under.fields["annotationType"] == "underline"
    assert "annotationAuthorName" not in under.fields


def test_to_items_writes_an_area_as_an_image_without_text(framed):
    _name, page = framed
    (item,) = za.to_items(_highlight_block(page, area=True), page, zotero_type="underline")
    _accepted(item.fields)
    assert item.fields["annotationType"] == "image" and "annotationText" not in item.fields
    assert len(json.loads(item.fields["annotationPosition"])["rects"]) == 1


def test_to_items_writes_a_text_box_as_text_or_note(framed):
    _name, page = framed
    (text,) = za.to_items(_box_block(page), page, nested=[(1, "ignored", "c")])
    _accepted(text.fields)
    pos = json.loads(text.fields["annotationPosition"])
    assert text.fields["annotationType"] == "text" and text.fields["annotationColor"] == "#d32f2f"
    assert text.fields["annotationComment"] == "Hello box\nsecond line"          # plain text, no tags
    assert pos["fontSize"] == 16 and pos["rotation"] == page.rotation
    (x1, y1, x2, y2), = pos["rects"]
    assert (round(x2 - x1, 2), round(y2 - y1, 2)) == (150, 40)                  # the box as shown, unturned
    (note,) = za.to_items(_box_block(page), page, zotero_type="note", nested=[(1, "child", "c")])
    _accepted(note.fields)
    (x1, y1, x2, y2), = json.loads(note.fields["annotationPosition"])["rects"]
    assert note.fields["annotationType"] == "note" and note.fields["annotationColor"] == za.DEFAULT_COLOR
    assert note.fields["annotationComment"] == "Hello <i>box</i>\nsecond line\n- child"
    assert (round(x2 - x1, 2), round(y2 - y1, 2)) == (22, 22)


def test_to_items_writes_ink_one_item_per_look(framed):
    _name, page = framed
    file = _ink_file(page, [_stroke("a", [(100, 100), (150, 120), (160, 140)]),
                            _stroke("b", [(200, 200)], color="#e53935", size=4),
                            _stroke("c", [(110, 130), (115, 135)])])
    items = za.to_items(_ink_block(page, file), page, ink_file=file)
    assert [(i.slot, i.stroke_ids, i.look) for i in items] == [
        ("0", ("a", "c"), "pen:#1f1f1f:2:1"), ("1", ("b",), "pen:#e53935:4:1")]
    for item in items:
        _accepted(item.fields)
    first, second = (json.loads(i.fields["annotationPosition"]) for i in items)
    assert first["width"] == 2 and second["width"] == 4
    assert len(first["paths"]) == 2 and len(second["paths"][0]) == 4           # a dot: a zero-length segment
    assert items[0].fields["annotationComment"] == "ink <i>note</i>" and items[1].fields["annotationComment"] == ""
    assert items[1].fields["annotationColor"] == "#e53935"


def test_to_items_labels_by_the_pdfs_page_labels():
    with za.Pages(_pdf(pages=3, labels=[{"startpage": 0, "prefix": "", "style": "D", "firstpagenum": 1043}])) as pages:
        page = pages.page(3)
    assert page.label == "1045"
    (item,) = za.to_items(_highlight_block(page), page)
    assert item.fields["annotationPageLabel"] == "1045"
    long = page._replace(label="x" * 80)
    assert za.to_items(_highlight_block(long), long)[0].fields["annotationPageLabel"] == "x" * 50


def _hex_labelled_pdf(pages=2) -> bytes:
    """A PDF whose /PageLabels prefixes are UTF-16 hex strings, as hyperref
    writes them: pdf.js reads "1", "2", PyMuPDF "<FEFF0031>", "<FEFF0032>"."""
    doc = pymupdf.open(stream=_pdf(pages=pages), filetype="pdf")
    doc.xref_set_key(doc.pdf_catalog(), "PageLabels", "<</Nums[0<</P<FEFF0031>>>1<</P<FEFF0032>>>]>>")
    return doc.tobytes()


def test_to_items_labels_a_utf16_prefix_as_pdfjs_decodes_it():
    with za.Pages(_hex_labelled_pdf()) as pages:
        page = pages.page(2)
        assert (pages.label(1), pages.label(2), pages.label(3)) == ("1", "2", "")
        assert (pages.mupdf_label(1), pages.mupdf_label(2), pages.mupdf_label(0)) == ("<FEFF0031>", "<FEFF0032>", "")
    assert page.label == "2"
    (item,) = za.to_items(_highlight_block(page), page)
    assert item.fields["annotationPageLabel"] == "2"


def test_a_page_without_a_label_is_labelled_by_its_number_as_the_reader_labels_it():
    # the reader's _getPageLabel: pageLabels[pageIndex] || (pageIndex + 1).toString()
    assert za.item_label("", 7) == "7" and za.item_label("iv", 7) == "iv" and za.item_label("x" * 80, 7) == "x" * 50
    with za.Pages(_pdf(pages=2, labels=[{"startpage": 1, "prefix": "", "style": "r", "firstpagenum": 1}])) as pages:
        assert (pages.label(1), pages.label(2)) == ("1", "i")
    with za.Pages(_pdf(pages=2)) as pages:
        assert (pages.label(1), pages.label(2), pages.mupdf_label(2)) == ("1", "2", "2")


@pytest.mark.parametrize("theirs, ours, sent, send", [
    ("<FEFF0031>", "1", "<FEFF0031>", "1"),   # Zotero holds the label the annotation's create carried: Gamma's
    ("1", "1", "<FEFF0031>", ""),             # already right
    ("523", "1", "<FEFF0031>", ""),           # Zotero's own (its reader's numbering, or a person's): stays
    ("3", "3", "3", ""),                      # both readings agree: nothing to send
])
def test_a_label_goes_only_over_the_one_the_annotations_create_carried(theirs, ours, sent, send):
    assert za.label_correction(theirs, ours, sent) == send


def test_to_items_skips_what_is_no_annotation_or_not_on_the_page():
    page = _page()
    block = _highlight_block(page)
    assert za.to_items(block, page._replace(number=2)) == []
    assert za.to_items({"id": "n", "content": "a note", "properties": {}}, page) == []
    unplaced = {**block, "properties": {**block["properties"], "pdf_position": {"pageNumber": 1}}}
    assert za.to_items(unplaced, page) == []
    assert za.to_items(_ink_block(page, _ink_file(page, [_stroke("a", [(1, 1)])])), page) == []   # no file given


# --- in: every kind -------------------------------------------------------------------------------

def test_from_item_reads_every_kind(framed):
    _name, page = framed
    items = _reader_items(page)
    made = {kind: za.from_item(item, page) for kind, item in items.items()}
    for kind, m in made.items():
        assert m["zotero_type"] == kind
        assert m["props"]["zotero_annotation"] == items[kind]["key"] and m["props"]["annot_stripped"] is True
    hl = made["highlight"]
    assert hl["kind"] == "highlight" and hl["content"] == "a **bold** remark"
    assert hl["props"]["quote"] == "a passage  over two\nlines"
    assert hl["props"]["color"] == "#ff666673"
    w, h = page.size
    pos = hl["props"]["pdf_position"]
    assert (pos["width"], pos["height"], pos["pageNumber"]) == (round(w, 2), round(h, 2), 1)
    rect0 = json.loads(items["highlight"]["annotationPosition"])["rects"][0]
    assert hl["props"]["imported_annot"] == annotation_key(1, "/Highlight", rect0)
    assert made["underline"]["kind"] == "highlight"
    assert made["image"]["props"]["pdf_position"]["area"] is True and made["image"]["props"]["quote"] == ""
    k = made["ink"]
    assert k["kind"] == "ink" and k["slot"] == "0" and k["stroke_ids"] == ("zKKKKKKK2-0", "zKKKKKKK2-1")
    assert {s.color for s in k["ink"].strokes} == {"#aaaaaa"} and k["ink"].strokes[0].size == 2
    assert k["props"]["ink_url"] == "" and k["props"]["ink_strokes"] == 2
    paths = json.loads(items["ink"]["annotationPosition"])["paths"]
    assert k["props"]["imported_annot"] == f"1:/Ink:{round(paths[0][0])}:{round(paths[0][1])}:2"
    note, text = made["note"], made["text"]
    assert note["kind"] == text["kind"] == "text_box"
    assert note["content"] == "a sticky *note*" and note["props"]["text_box"]["bg"] == za.NOTE_FILL
    assert text["content"] == "typed text" and text["props"]["text_box"]["bg"] is None
    assert text["props"]["text_box"]["size"] == 14 and text["props"]["text_box"]["color"] == "#e56eee"
    assert text["props"]["pdf_page"] == note["props"]["pdf_page"] == 1


def test_from_item_takes_a_quote_from_the_page_when_zotero_sent_none():
    with za.Pages(_pdf()) as pages:
        page = pages.page(1)
    word = "".join(c for c, _ in page.chars).index("fidelity")
    first, last = page.chars[word][1], page.chars[word + 7][1]
    item = {"key": "QQQQQQQ2", "annotationType": "highlight", "annotationColor": "#ffd400",
            "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[first[0], first[1], last[2], last[3]]]})}
    assert za.from_item(item, page)["props"]["quote"] == "fidelity"


def test_from_item_text_is_plain_and_turned_text_stands_upright():
    page = _page()
    item = {"key": "TTTTTTT3", "annotationType": "text", "annotationColor": "#000000",
            "annotationComment": "<b>literal</b> * tags",
            "annotationPosition": json.dumps({"pageIndex": 0, "fontSize": 12, "rotation": 30,
                                              "rects": [[100, 300, 200, 330]]})}
    made = za.from_item(item, page)
    assert made["content"] == "\\<b>literal\\</b> \\* tags"
    assert text_box.plain_text(made["content"]) == "<b>literal</b> * tags"
    box = made["props"]["text_box"]
    assert (box["x"], box["y"], box["w"], box["h"]) == (100, 792 - 330, 100, 30)   # same centre, upright
    turned = za._turned_box((100, 300, 200, 330), 30)
    assert made["props"]["imported_annot"] == annotation_key(1, "/FreeText", turned)
    (back,) = za.to_items(_block(made), page, zotero_type="text")
    assert json.loads(back.fields["annotationPosition"])["rotation"] == 0
    odd = {**item, "annotationPosition": json.dumps({"pageIndex": 0, "fontSize": "big", "rotation": "x",
                                                     "rects": [[100, 300, 200, 330]]})}
    assert za.from_item(odd, page)["props"]["text_box"]["size"] == text_box.DEFAULT_SIZE


@pytest.mark.parametrize("change", [
    {"annotationType": "comment"},
    {"annotationPosition": "{bad json"},
    {"annotationPosition": json.dumps({"pageIndex": 1, "rects": [[1, 1, 2, 2]]})},
    {"annotationPosition": json.dumps({"pageIndex": 0, "rects": []})},
    {"annotationPosition": json.dumps({"pageIndex": 0})},
])
def test_from_item_refuses_an_item_that_makes_no_block(change):
    item = {"key": "XXXXXXX2", "annotationType": "highlight", "annotationColor": "#ffd400",
            "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[1, 1, 20, 20]]}), **change}
    assert za.from_item(item, _page()) is None


def test_from_item_refuses_empty_notes_texts_and_ink():
    page = _page()
    for kind, position in (("note", {"rects": [[1, 1, 23, 23]]}), ("text", {"rects": [[1, 1, 50, 23]], "fontSize": 12}),
                           ("ink", {"width": 1, "paths": [[]]}), ("ink", {"width": 1, "paths": [[1e12, 5]]})):
        item = {"key": "EEEEEEE2", "annotationType": kind, "annotationComment": "  ",
                "annotationPosition": json.dumps({"pageIndex": 0, **position})}
        assert za.from_item(item, page) is None, kind


# --- stability ---------------------------------------------------------------------------------------

ONE_UNIT = 0.011   # the canonical rounding: Gamma stores hundredths of a point


def test_a_reader_item_comes_back_as_itself(framed):
    name, page = framed
    for kind, item in _reader_items(page).items():
        made = za.from_item(item, page)
        (back,) = _items_of(made, page)
        _accepted(back.fields)
        want, got = za.projection(item), za.projection(back.fields)
        if kind == "note":       # a box has no place for a note's colour: never compared, Zotero's stays
            assert back.fields["annotationColor"] == za.DEFAULT_COLOR and got["color"] == want["color"] == ""
        assert za.same(got, want, tol=ONE_UNIT), (name, kind, got, want)
        assert {k: got[k] for k in ("type", "color", "comment", "text")} == \
            {k: want[k] for k in ("type", "color", "comment", "text")}, (name, kind)


def test_one_trip_reaches_a_fixed_point(framed):
    """What Gamma sends of a pulled item, pulled again, is what it sends again:
    exactly, so a round after a round writes nothing."""
    name, page = framed
    for kind, item in _reader_items(page).items():
        made = za.from_item(item, page)
        (first,) = _items_of(made, page)
        again = za.from_item({**first.fields, "key": item["key"]}, page)
        (second,) = _items_of(again, page)
        assert za.projection(second.fields) == za.projection(first.fields), (name, kind)
        assert second.fields["annotationSortIndex"] == first.fields["annotationSortIndex"]
        assert again["props"] == made["props"], (name, kind)
        assert again["content"] == made["content"], (name, kind)


def test_a_block_comes_back_as_its_geometry(framed):
    name, page = framed
    w, h = page.size
    for area in (False, True):
        block = _highlight_block(page, area=area)
        (item,) = za.to_items(block, page)
        made = za.from_item({**item.fields, "key": "BBBBBBB2"}, page)
        old, new = block["properties"]["pdf_position"], made["props"]["pdf_position"]
        k = new["width"] / old["width"]
        olds = old["rects"] if not area else [old["boundingRect"]]
        assert len(new["rects"]) == len(olds)
        for a, b in zip(olds, new["rects"]):
            assert all(abs(a[c] * k - b[c]) <= ONE_UNIT for c in highlights.COORDS), (name, a, b)
        assert made["props"]["quote"] == ("" if area else block["properties"]["quote"])
        assert made["content"] == "my **own** note"
        assert made["props"]["color"] == "#9bcdff73"          # Zotero keeps no alpha: a new block takes 0.45
    box_block = _box_block(page)
    (item,) = za.to_items(box_block, page)
    made = za.from_item({**item.fields, "key": "BBBBBBB3"}, page)
    old, new = box_block["properties"]["text_box"], made["props"]["text_box"]
    assert all(abs(old[c] - new[c]) <= ONE_UNIT for c in ("x", "y", "w", "h")), (name, old, new)
    assert (new["size"], new["color"], new["bg"]) == (16, "#d32f2f", None)   # Zotero has no fill
    assert made["content"] == "Hello box\nsecond line"
    file = _ink_file(page, [_stroke("a", [(100.25, 100.5), (150, 120.75), (160, 140)]), _stroke("c", [(110, 130)])])
    (item,) = za.to_items(_ink_block(page, file), page, ink_file=file)
    made = za.from_item({**item.fields, "key": "BBBBBBB4"}, page)
    for old_s, new_s in zip(file.strokes, made["ink"].strokes):
        old_pts = [(x, y) for x, y, _w in ink.stroke_polyline(old_s)]
        new_pts = [(x, y) for x, y, _w in ink.stroke_polyline(new_s)]
        assert len(new_pts) in (len(old_pts), 2 * len(old_pts))
        assert all(abs(a - b) <= ONE_UNIT for p, q in zip(old_pts, new_pts) for a, b in zip(p, q)), name
    assert made["ink"].strokes[0].size == 2


def test_an_apply_of_what_the_block_reads_as_is_a_no_op(framed):
    name, page = framed
    nested = [(1, "a child", "c1")]
    cases = []
    for kind, item in _reader_items(page).items():
        made = za.from_item(item, page)
        cases.append((_block(made), made["ink"], made["stroke_ids"], made["zotero_type"], ()))
    file = _ink_file(page, [_stroke("a", [(100, 100), (150, 120)]), _stroke("b", [(200, 200)], color="#e53935")])
    cases += [(_highlight_block(page), None, (), "highlight", nested),
              (_highlight_block(page, area=True), None, (), "image", nested),
              (_box_block(page), None, (), "text", ()), (_box_block(page), None, (), "note", nested),
              (_ink_block(page, file), file, ("b",), "ink", nested)]
    for block, file_, ids, ztype, nest in cases:
        items = za.to_items(block, page, ink_file=file_, zotero_type=ztype, nested=nest,
                            rows=[("x", ids, "")] if ids else ())
        n = next(n for n, i in enumerate(items) if set(i.stroke_ids) == set(ids)) if ids else 0
        assert za.apply_fields(block, page, za.projection(items[n].fields), ink_file=file_, stroke_ids=ids,
                               nested=nest, zotero_type=ztype, first=n == 0) == {"props": {}}, (name, ztype)
        assert za.apply_fields(block, page, {}, ink_file=file_, stroke_ids=ids, zotero_type=ztype) == {"props": {}}


def test_a_comment_on_an_item_that_carries_none_is_a_conflict():
    page = _page()
    file = _ink_file(page, [_stroke("a", [(1, 1)], color="#e53935"), _stroke("b", [(2, 2)], color="#1e88e5")])
    block = _ink_block(page, file)
    out = za.apply_fields(block, page, {"comment": "written on the blue one"}, ink_file=file, stroke_ids=("b",),
                          first=False)
    assert out == {"props": {}, "conflicts": ["comment"]}
    first = za.apply_fields(block, page, {"comment": "on the red one"}, ink_file=file, stroke_ids=("a",))
    assert first == {"props": {}, "content": "on the red one"}


@pytest.fixture(scope="module")
def fixture_library():
    """tests/zotero_fixtures.py's standard library in the fake, and its PDF's pages."""
    from zotero_fake import FakeZotero
    from zotero_fixtures import pdf_bytes, standard_library

    fake = FakeZotero()
    made = standard_library(fake)
    with za.Pages(pdf_bytes()) as pages:
        yield fake, made, pages


def test_the_fixture_annotations_sort_and_come_back(fixture_library):
    fake, made, pages = fixture_library
    annotations = [d for lib in (made["library"], made["group"]) for d in fake.items(lib)
                   if d["itemType"] == "annotation"]
    assert len(annotations) == 9
    for data in annotations:
        position = json.loads(data["annotationPosition"])
        page = pages.page(position["pageIndex"] + 1)
        assert za.sort_index(page, position) == data["annotationSortIndex"], data["annotationType"]
        made_ = za.from_item(data, page)
        (back,) = _items_of(made_, page)
        got, want = za.projection(back.fields), za.projection(data)
        if data["annotationType"] == "note":
            got["color"] = want["color"]
        assert za.same(got, want, tol=ONE_UNIT), data["annotationType"]
        assert back.fields["annotationSortIndex"] == data["annotationSortIndex"], data["annotationType"]


def test_zotero_takes_every_item_gamma_makes(fixture_library):
    fake, made, pages = fixture_library
    lib, pdf = made["library"], made["keys"]["pdf"]
    page = pages.page(1)
    file = _ink_file(page, _long_strokes(12, 500) + [_stroke("dot", [(300, 300)], color="#e53935")])
    blocks = [(_highlight_block(page), {}), (_highlight_block(page), {"zotero_type": "underline"}),
              (_highlight_block(page, area=True), {}), (_box_block(page), {}),
              (_box_block(page), {"zotero_type": "note"}), (_ink_block(page, file), {"ink_file": file})]
    for block, kw in blocks:
        items = za.to_items(block, page, nested=[(1, "a <child>", "c")], author="Tim", **kw)
        for item in items:
            key = fake.add_item(lib, {**item.fields, "parentItem": pdf})   # AssertionError for a refusal
            stored = next(d for d in fake.items(lib) if d["key"] == key)
            assert za.projection(stored) == za.projection(item.fields)


# --- applying one field ------------------------------------------------------------------------------

def test_apply_colour_keeps_the_blocks_alpha():
    page = _page()
    block = _highlight_block(page)
    out = za.apply_fields(block, page, {"color": "#ff6666"}, zotero_type="highlight")
    assert out == {"props": {"color": "#ff6666a6"}}
    box = _box_block(page)
    recoloured = za.apply_fields(box, page, {"color": "#00ff00"}, zotero_type="text")
    assert recoloured["props"]["text_box"]["color"] == "#00ff00"
    assert za.apply_fields(box, page, {"color": "#00ff00"}, zotero_type="note") == {"props": {}}


def test_apply_text_sets_the_quote_as_sent():
    page = _page()
    out = za.apply_fields(_highlight_block(page), page, {"text": "a  new\nquote"}, zotero_type="underline")
    assert out == {"props": {"quote": "a  new\nquote"}}
    assert za.apply_fields(_highlight_block(page, area=True), page, {"text": "x"}, zotero_type="image") == {"props": {}}


def test_apply_comment_changes_the_own_note_unless_the_nested_part_changed():
    page = _page()
    block = _highlight_block(page)
    nested = [(1, "child", "c1")]
    out = za.apply_fields(block, page, {"comment": "new <i>own</i>\n- child"}, nested=nested, zotero_type="highlight")
    assert out == {"props": {}, "content": "new *own*"}
    out = za.apply_fields(block, page, {"comment": "new own\n- child edited"}, nested=nested, zotero_type="highlight")
    assert out == {"props": {}, "conflicts": ["comment"]}
    text = za.apply_fields(_box_block(page), page, {"comment": "plain *text*"}, zotero_type="text")
    assert text == {"props": {}, "content": "plain \\*text\\*"}


def test_apply_position_moves_a_highlight_and_drops_its_relocation_marks(framed):
    _name, page = framed
    block = _highlight_block(page)
    block["properties"]["pdf_position"].update(doc_id="old", approx=True, match=0.8)
    x0, y0 = page.view[0], page.view[1]
    position = {"pageIndex": 0, "rects": [[x0 + 100, y0 + 200, x0 + 220, y0 + 212]]}
    out = za.apply_fields(block, page, {"position": position}, zotero_type="highlight")
    pos = out["props"]["pdf_position"]
    assert set(out["props"]) == {"pdf_position"} and not ({"doc_id", "approx", "match"} & set(pos))
    (item,) = za.to_items({**block, "properties": {**block["properties"], **out["props"]}}, page)
    got = za.projection(item.fields)
    assert za.same(got, {**got, "position": za.projection({"annotationPosition": position})["position"]},
                   tol=ONE_UNIT)
    area = _highlight_block(page, area=True)
    moved = za.apply_fields(area, page, {"position": position}, zotero_type="image")["props"]["pdf_position"]
    assert moved["area"] is True
    other_page = za.apply_fields(block, page, {"position": {**position, "pageIndex": 4}}, zotero_type="highlight")
    assert other_page == {"props": {}, "conflicts": ["position"]}


def test_apply_position_moves_a_box_and_keeps_what_zotero_has_no_place_for(framed):
    _name, page = framed
    block = _box_block(page)
    (item,) = za.to_items(block, page)
    pos = json.loads(item.fields["annotationPosition"])
    x1, y1, x2, y2 = pos["rects"][0]
    pos["rects"] = [[x1 + 10, y1 + 20, x2 + 10, y2 + 20]]
    pos["fontSize"] = 24
    out = za.apply_fields(block, page, {"position": pos}, zotero_type="text")
    box = out["props"]["text_box"]
    old = block["properties"]["text_box"]
    assert (box["bg"], box["auto"], box["color"], box["size"]) == (old["bg"], old["auto"], old["color"], 24)
    assert (box["w"], box["h"]) == (old["w"], old["h"]) and (box["x"], box["y"]) != (old["x"], old["y"])
    note = za.apply_fields(block, page, {"position": {"pageIndex": 0, "rects": [[x1 + 5, y1 + 5, x1 + 27, y1 + 27]]}},
                           zotero_type="note")["props"]["text_box"]
    assert (note["w"], note["h"], note["size"]) == (old["w"], old["h"], old["size"])


def test_apply_to_ink_restyles_or_replaces_only_the_items_strokes():
    page = _page()
    file = _ink_file(page, [_stroke("a", [(100, 100), (150, 120)]),
                            _stroke("b", [(200, 200), (210, 220)], color="#e53935"),
                            _stroke("c", [(120, 140), (125, 150)])])
    block = _ink_block(page, file)
    items = za.to_items(block, page, ink_file=file)
    mine = next(i for i in items if i.stroke_ids == ("a", "c"))
    recolor = za.apply_fields(block, page, {"color": "#2ea8e5"}, ink_file=file, stroke_ids=mine.stroke_ids)
    assert recolor["stroke_ids"] == ("a", "c")
    assert [s.color for s in recolor["ink"].strokes] == ["#2ea8e5", "#e53935", "#2ea8e5"]
    assert recolor["props"]["ink_strokes"] == 3 and "pdf_position" in recolor["props"]
    pos = json.loads(mine.fields["annotationPosition"])
    wider = za.apply_fields(block, page, {"position": {**pos, "width": 4}}, ink_file=file, stroke_ids=mine.stroke_ids)
    assert [s.size for s in wider["ink"].strokes] == [4, 2, 4] and wider["stroke_ids"] == ("a", "c")
    moved = {**pos, "paths": [[v + 5 for v in path] for path in pos["paths"]]}
    out = za.apply_fields(block, page, {"position": moved, "color": "#5fb236"}, ink_file=file,
                          stroke_ids=mine.stroke_ids, key="ZKEY2345")
    assert out["stroke_ids"] == ("zZKEY2345-0", "zZKEY2345-1")
    assert [s.id for s in out["ink"].strokes] == ["zZKEY2345-0", "zZKEY2345-1", "b"]
    assert {s.color for s in out["ink"].strokes if s.id != "b"} == {"#5fb236"}
    assert out["ink"].strokes[2] == file.strokes[1]
    (again,) = [i for i in za.to_items(block, page, ink_file=out["ink"], rows=[("0", out["stroke_ids"], "")])
                if i.slot == "0"]
    got = za.projection(again.fields)
    assert za.same(got, {**got, "position": za.projection({"annotationPosition": moved})["position"]}, tol=ONE_UNIT)
    # Moved to another page in Zotero: a block of its own, never strokes of this one.
    other = page._replace(number=2)
    assert za.apply_fields(block, other, {"position": {**pos, "pageIndex": 1}}, ink_file=file,
                           stroke_ids=mine.stroke_ids) == {"props": {}, "conflicts": ["position"]}


def test_replace_strokes_keeps_the_order_and_validates():
    page = _page()
    file = _ink_file(page, [_stroke("a", [(1, 1)]), _stroke("b", [(2, 2)]), _stroke("c", [(3, 3)])])
    out = za.replace_strokes(file, {"b", "c"}, [_stroke("n1", [(4, 4)]), _stroke("n2", [(5, 5)])])
    assert [s.id for s in out.strokes] == ["a", "n1", "n2"]
    assert [s.id for s in za.replace_strokes(file, {"zz"}, [_stroke("n", [(4, 4)])]).strokes] == ["a", "b", "c", "n"]
    with pytest.raises(ink.InkError):
        za.replace_strokes(file, {"c"}, [_stroke("a", [(4, 4)])])


# --- ink rows ---------------------------------------------------------------------------------------

def _looks(items):
    return [(i.slot, i.stroke_ids, i.look) for i in items]


def test_ink_rows_keep_their_strokes_through_a_change_of_look():
    page = _page()
    red, blue = "pen:#e53935:2:1", "pen:#1e88e5:2:1"
    file = _ink_file(page, [_stroke("a", [(1, 1)], color="#e53935"), _stroke("b", [(2, 2)], color="#1e88e5")])
    rows = [("0", ("a",), red), ("1", ("b",), blue)]
    assert _looks(za.to_items(_ink_block(page, file), page, ink_file=file, rows=rows)) == [
        ("0", ("a",), red), ("1", ("b",), blue)]
    # Zotero (or Gamma) made the blue one red: it keeps its row, a PATCH of its colour.
    recoloured = _ink_file(page, [_stroke("a", [(1, 1)], color="#e53935"), _stroke("b", [(2, 2)], color="#e53935")])
    assert _looks(za.to_items(_ink_block(page, recoloured), page, ink_file=recoloured, rows=rows)) == [
        ("0", ("a",), red), ("1", ("b",), red)]


def test_ink_strokes_no_row_owns_join_their_look_or_a_new_slot():
    page = _page()
    red, blue, green = "pen:#e53935:2:1", "pen:#1e88e5:2:1", "pen:#43a047:2:1"
    file = _ink_file(page, [_stroke("a", [(1, 1)], color="#e53935"), _stroke("b", [(2, 2)], color="#1e88e5"),
                            _stroke("n1", [(3, 3)], color="#e53935"), _stroke("n2", [(4, 4)], color="#43a047"),
                            _stroke("n3", [(5, 5)], color="#1e88e5")])
    rows = [("1", ("b",), blue, True), ("0", ("a", "gone"), red)]     # "1" is another member's: locked
    assert _looks(za.to_items(_ink_block(page, file), page, ink_file=file, rows=rows)) == [
        ("0", ("a", "n1"), red), ("1", ("b",), blue), ("2", ("n2",), green), ("3", ("n3",), blue)]


def test_a_row_of_mixed_looks_keeps_its_own_and_lets_the_rest_go():
    page = _page()
    red, blue = "pen:#e53935:2:1", "pen:#1e88e5:2:1"
    file = _ink_file(page, [_stroke("a", [(1, 1)], color="#1e88e5"), _stroke("b", [(2, 2)], color="#e53935"),
                            _stroke("c", [(3, 3)], color="#e53935")])
    rows = [("0", ("a", "b", "c"), red)]
    assert _looks(za.to_items(_ink_block(page, file), page, ink_file=file, rows=rows)) == [
        ("0", ("b", "c"), red), ("1", ("a",), blue)]
    gone = _ink_file(page, [_stroke("z", [(9, 9)], color="#e53935")])
    assert _looks(za.to_items(_ink_block(page, gone), page, ink_file=gone, rows=rows)) == [("1", ("z",), red)]


def test_the_ink_comment_rides_on_the_lowest_slot():
    page = _page()
    file = _ink_file(page, [_stroke("a", [(1, 1)], color="#e53935"), _stroke("b", [(2, 2)], color="#1e88e5")])
    items = za.to_items(_ink_block(page, file), page, ink_file=file,
                        rows=[("4", ("a",), ""), ("2", ("b",), "")])
    assert [(i.slot, i.fields["annotationComment"]) for i in items] == [("2", "ink <i>note</i>"), ("4", "")]


# --- splitting --------------------------------------------------------------------------------------

def _long_strokes(n_strokes, points):
    return [_stroke(f"s{n}", [(50 + (i % 400) * 1.237 + n * 0.01, 60 + (i // 400) * 3.11 + n * 1.5)
                              for i in range(points)]) for n in range(n_strokes)]


def test_a_long_drawing_splits_into_pieces_of_whole_strokes():
    page = _page()
    file = _ink_file(page, _long_strokes(12, 500))
    items = za.to_items(_ink_block(page, file), page, ink_file=file)
    assert len(items) > 1 and [i.slot for i in items] == ["0"] + [f"0.{n}" for n in range(1, len(items))]
    seen = []
    for item in items:
        _accepted(item.fields)
        assert len(item.fields["annotationPosition"]) <= za.POSITION_MAX
        pos = json.loads(item.fields["annotationPosition"])
        assert len(pos["paths"]) == len(item.stroke_ids)
        assert item.fields["annotationSortIndex"] == za.sort_index(page, pos)
        seen += item.stroke_ids
    assert seen == [s.id for s in file.strokes]
    assert [bool(i.fields["annotationComment"]) for i in items] == [True] + [False] * (len(items) - 1)
    # Slots taken by other rows are skipped.
    rows = [("0", tuple(s.id for s in file.strokes), "pen:#1f1f1f:2:1"), ("0.1", ("none",), "")]
    assert "0.1" not in [i.slot for i in za.to_items(_ink_block(page, file), page, ink_file=file, rows=rows)]


def test_a_stroke_too_long_for_a_piece_is_cut_by_points():
    page = _page()
    file = _ink_file(page, _long_strokes(1, 9000))
    items = za.to_items(_ink_block(page, file), page, ink_file=file)
    assert len(items) > 1 and all(i.stroke_ids == ("s0",) for i in items)
    points = [v for i in items for v in json.loads(i.fields["annotationPosition"])["paths"][0]]
    (whole,) = za._ink_position(file, file.strokes, page)["paths"]
    assert len(points) == 2 * 9000 and points == whole


def test_split_follows_zoteros_rule_for_rects():
    page = _page()
    rects = [[10 + i * 0.001, 20, 30 + i * 0.001, 40.123] for i in range(3000)]
    fields = {"annotationType": "highlight", "annotationText": "t", "annotationComment": "c",
              "annotationColor": "#ffd400", "annotationPosition": {"pageIndex": 0, "rects": rects}}
    pieces = [item.fields for item in za._items(fields, page, "", "", set())]  # (as ``to_items`` finishes them)
    assert len(pieces) > 1
    assert [r for p in pieces for r in json.loads(p["annotationPosition"])["rects"]] == rects
    assert all(len(p["annotationPosition"]) <= za.POSITION_MAX and p["annotationText"] == "t" for p in pieces)
    assert all(p["annotationSortIndex"] == za.sort_index(page, json.loads(p["annotationPosition"])) for p in pieces)
    assert [p["annotationComment"] for p in pieces] == ["c"] + [""] * (len(pieces) - 1)
    small = {**fields, "annotationPosition": {"pageIndex": 0, "rects": rects[:3]}}
    assert len(za._items(small, page, "", "", set())) == 1


# --- projections and signatures --------------------------------------------------------------------

def test_projection_keeps_what_places_the_annotation():
    fields = {"annotationType": "highlight", "annotationColor": "#FFD400", "annotationText": "t",
              "annotationComment": "c", "annotationSortIndex": "00000|000001|00002", "annotationPageLabel": "iv",
              "annotationPosition": json.dumps({"pageIndex": 0, "rects": [[154.4739990234375, 663.8139953613281,
                                                                           289.09796142578125, 683.05]],
                                                "width": 612, "height": 792, "gamma": {"id": "b1"}})}
    assert za.projection(fields) == {"type": "highlight", "color": "#ffd400", "comment": "c", "text": "t",
                                     "position": {"pageIndex": 0, "rects": [[154.47, 663.81, 289.1, 683.05]]}}
    ink_fields = {"annotationType": "ink", "annotationText": "ignored",
                  "annotationPosition": {"pageIndex": 1, "width": 2.0, "paths": [[1.004, 2, 3.333, 4]]}}
    assert za.projection(ink_fields) == {"type": "ink", "color": "", "comment": "", "text": "",
                                         "position": {"pageIndex": 1, "width": 2, "paths": [[1, 2, 3.33, 4]]}}


def test_same_allows_a_position_tolerance_only():
    a = {"type": "ink", "color": "#000000", "comment": "", "text": "", "position": {"pageIndex": 0, "paths": [[1, 2]]}}
    assert za.same(a, {**a, "position": {"pageIndex": 0, "paths": [[1.4, 2.5]]}})
    assert not za.same(a, {**a, "position": {"pageIndex": 0, "paths": [[1.6, 2]]}})
    assert not za.same(a, {**a, "position": {"pageIndex": 1, "paths": [[1, 2]]}})
    assert not za.same(a, {**a, "comment": "x"})


def _two_places(x1, x2):
    a = za.projection({"annotationType": "highlight", "annotationPosition": json.dumps(
        {"pageIndex": 0, "rects": [[x1, 1.0, 2.0, 3.0]]})})
    b = za.projection({"annotationType": "highlight", "annotationPosition": json.dumps(
        {"pageIndex": 0, "rects": [[x2, 1.0, 2.0, 3.0]]})})
    return a, b


def test_positions_half_a_point_apart_are_one_place_by_one_rule():
    a, b = _two_places(0.57, 1.07)
    assert za.positions_close(a["position"], b["position"]) and za.same(a, b)
    assert za.positions_close((0.57, (1, 2)), [1.07, [1, 2]])  # lists or tuples alike
    far_a, far_b = _two_places(0.57, 1.08)
    assert not za.positions_close(far_a["position"], far_b["position"]) and not za.same(far_a, far_b)


def test_block_sig_follows_what_to_items_reads():
    page = _page()
    block = _highlight_block(page)
    sig = za.block_sig(block, [(1, "child", "c1")])
    assert sig == za.block_sig(block, [(1, "child", "c2")])
    assert sig != za.block_sig(block, [(1, "child edited", "c1")])
    assert sig != za.block_sig({**block, "content": "other"}, [(1, "child", "c1")])
    for key, value in (("color", "#000000ff"), ("quote", "q"), ("pdf_position", {"pageNumber": 2}),
                       ("ink_url", "/x"), ("text_box", {}), ("pdf_page", 3)):
        changed = {**block, "properties": {**block["properties"], key: value}}
        assert za.block_sig(changed, [(1, "child", "c1")]) != sig, key
    assert za.block_sig({**block, "properties": {**block["properties"], "t0": 5}}, [(1, "child", "c1")]) == sig


def test_patch_fields_sends_only_what_gamma_changed():
    page = _page()
    (item,) = za.to_items(_highlight_block(page), page, zotero_type="highlight")
    f = item.fields
    assert za.patch_fields(f, ["color"]) == {"annotationColor": f["annotationColor"]}
    assert za.patch_fields(f, ["type", "text"]) == {"annotationText": f["annotationText"]}
    base = za.projection(f)
    moved = za.patch_fields(f, ["position"], base)
    assert moved == {"annotationPosition": f["annotationPosition"], "annotationSortIndex": f["annotationSortIndex"]}
    elsewhere = za.patch_fields(f, ["position"], {**base, "position": {**base["position"], "pageIndex": 3}})
    assert elsewhere["annotationPageLabel"] == f["annotationPageLabel"]
    (area,) = za.to_items(_highlight_block(page, area=True), page)
    assert za.patch_fields(area.fields, ["text"]) == {}


# --- which blocks -------------------------------------------------------------------------------------

MD5 = "0" * 32


PLACED = {"pageNumber": 1, "width": 612, "height": 792, "rects": [{"x1": 1, "y1": 1, "x2": 9, "y2": 9}]}


@pytest.mark.parametrize("props, annotation, may", [
    ({"pdf_position": PLACED}, True, True),
    ({"pdf_position": {**PLACED, "doc_id": "old"}}, True, False),          # measured on another file: held
    ({"pdf_position": {**PLACED, "lost": True}}, True, False),
    ({"pdf_position": {**PLACED, "approx": True}}, True, False),
    ({"pdf_position": {"pageNumber": 1}}, True, False),                     # no place known
    ({"pdf_position": {k: v for k, v in PLACED.items() if k != "width"}}, True, False),
    ({"pdf_position": PLACED, "imported_annot": "1:/Highlight:1:1:9"}, True, False),   # still in the file
    ({"pdf_position": PLACED, "imported_annot": "1:/Highlight:1:1:9", "annot_stripped": True}, True, True),
    ({"pdf_position": PLACED, "link_url": "https://x.org"}, False, False),
    ({"ink_url": "/api/uploads/x.ink", "pdf_position": {"pageNumber": 1}}, True, True),
    ({"ink_url": "", "pdf_position": {"pageNumber": 1}}, True, False),     # not uploaded yet
    ({"ink_url": "/api/uploads/x.ink"}, False, False),                      # on a canvas
    ({"text_box": {"x": 1}, "pdf_page": 2}, True, True),
    ({"text_box": {"x": 1}}, False, False),
    ({"sheet": {"w": 1}, "pdf_position": {"pageNumber": 1}}, False, False),
    ({"picture": {"url": "/api/uploads/p.png", "x": 1, "y": 1, "w": 9, "h": 9}, "pdf_page": 1}, False, False),
    ({"picture": {"url": "/api/uploads/p.png"}, "pdf_position": PLACED}, False, False),  # a picture, never a highlight
    ({"recording": {}}, False, False),
    ({}, False, False),
])
def test_which_blocks_are_annotations_and_which_may_go_now(props, annotation, may):
    block = {"id": "b", "content": "", "properties": props}
    assert za.is_annotation_block(block) is annotation
    assert za.eligible(block, MD5, MD5.upper()) is may
    assert za.eligible(block, MD5, "1" * 32) is False and za.eligible(block, "", "") is False


@pytest.mark.parametrize("props, reason", [
    ({"pdf_position": PLACED}, ""),
    ({"pdf_position": {**PLACED, "doc_id": "old"}}, za.WAIT_PLACING),
    ({"pdf_position": {**PLACED, "lost": True}}, za.WAIT_LOST),
    ({"pdf_position": {**PLACED, "approx": True}}, za.WAIT_DASHED),
    ({"pdf_position": {"pageNumber": 1}}, za.WAIT_NO_PLACE),
    ({"pdf_position": {k: v for k, v in PLACED.items() if k != "width"}}, za.WAIT_NO_PLACE),
    ({"pdf_position": {k: v for k, v in PLACED.items() if k not in ("width", "height")}}, za.WAIT_NO_PLACE),
    ({"pdf_position": PLACED, "imported_annot": "1:/Highlight:1:1:9"}, za.WAIT_EMBEDDED),
    ({"pdf_position": PLACED, "imported_annot": "1:/Highlight:1:1:9", "annot_stripped": True}, ""),
    ({"ink_url": "/api/uploads/x.ink", "pdf_position": {"pageNumber": 1}}, ""),
    ({"ink_url": "", "pdf_position": {"pageNumber": 1}}, za.WAIT_PLACING),
    ({"text_box": {"x": 1}, "pdf_page": 2}, ""),
])
def test_one_rule_says_why_an_annotation_block_is_held_and_eligible_reads_it(props, reason):
    block = {"id": "b", "content": "", "properties": props}
    assert za.is_annotation_block(block)
    assert za.hold_reason(block) == reason
    assert za.eligible(block, "0" * 32, "0" * 32) is (reason == "")
    view = type("View", (), {"annotations": [zotero_local.Annotation(block, [], "p", 1, False, "p")]})()
    assert zotero_sync_blocks.held_blocks(view) == ({"b"} if reason else set())


def test_on_a_sheet_ink_and_boxes_are_no_annotations():
    for props in ({"ink_url": "/x", "pdf_position": {"pageNumber": 1}}, {"text_box": {"x": 1}, "pdf_page": 2}):
        block = {"id": "b", "properties": props}
        assert not za.is_annotation_block(block, on_sheet=True) and not za.eligible(block, MD5, MD5, on_sheet=True)
        assert za.page_number(props, on_sheet=True) is None


def test_page_number_of_each_kind():
    page = _page(number=3)
    file = _ink_file(page, [_stroke("a", [(1, 1)])])
    assert za.page_number({"ink_url": "/x", "pdf_position": {"pageNumber": 2}}, file) == 3
    assert za.page_number({"ink_url": "/x", "pdf_position": {"pageNumber": 2}}) == 2
    assert za.page_number({"text_box": {}, "pdf_page": 4}) == 4
    assert za.page_number({"pdf_position": {"pageNumber": 5}}) == 5
    assert za.page_number({"pdf_position": {"pageNumber": 5}, "link_page_id": "p"}) is None
    assert za.page_number({}) is None

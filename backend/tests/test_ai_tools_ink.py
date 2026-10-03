"""The agent's eyes on handwriting: view_ink (a group on its PDF page or
sheet of paper, cropped or whole), the pictures of attached handwriting
blocks riding with a message, and the renderer under both
(gamma/ink_view.py, pdf_export.page_with_ink)."""

from types import SimpleNamespace

import pypdfium2 as pdfium
import pytest

from gamma import ink as inkmod
from gamma.ai_context import notes_focus_section
from gamma.ai_tools import run_agent_tool
from gamma.ink_view import CROP_MIN_SIDE, crop_box
from gamma.pdf_export import page_with_ink

from ai_fixtures import folder, org  # noqa: F401  (org is a fixture)

PAGE_W, PAGE_H = 300.0, 400.0


def _ink(kind="pdf-page", color="#ff0000", width=PAGE_W, height=PAGE_H,
         points=((40, 60), (260, 340))):
    space = {"kind": kind, "width": width, "height": height, **({"page": 1} if kind == "pdf-page" else {})}
    return {"format": "gamma-ink", "version": 1, "space": space,
            "strokes": [{"id": "s1", "color": color, "size": 6, "ch": "xy",
                         "pts": inkmod.encode_points([{"x": x, "y": y} for x, y in points], "xy")}]}


def _pdf(path, pages=1):
    from PyPDF2 import PdfWriter
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=PAGE_W, height=PAGE_H)
    with open(path, "wb") as f:
        w.write(f)
    return path


def _pixel(pdf_bytes, x, y):
    page = pdfium.PdfDocument(pdf_bytes)[0]
    bitmap = page.render(scale=1, rev_byteorder=True)
    i = y * bitmap.stride + x * bitmap.n_channels
    return tuple(bytes(bitmap.buffer)[i:i + 3])


@pytest.fixture(scope="module")
def inked(org, tmp_path_factory):
    """A PDF page carrying a captioned handwriting group and an unrelated
    note, plus a notebook: a sheet of ruled paper with a group on it."""
    c, ids = org

    def block(parent, content, props=None):
        r = c.post("/api/blocks", json={"parent_id": parent, "content": content, "properties": props or {}})
        assert r.status_code == 200, r.text
        return r.json()["id"]

    def upload(data):
        r = c.post("/api/upload-ink", json=data)
        assert r.status_code == 200, r.text
        return r.json()

    paper = block("root", "inked paper", {"folders": [ids["readout"]], "doc_id": "e" * 24})
    drawn = upload(_ink())
    group = block(paper, "circled the key result",
                  {"ink_url": drawn["url"], "pdf_position": drawn["pdf_position"], "ink_strokes": 1})
    plain = block(paper, "a typed note")
    book = block("root", "lecture notebook", {"folders": [ids["readout"]]})
    sheet = block(book, "Page 1", {"sheet": {"pattern": "ruled"}})
    scrawl = block(sheet, "", {"ink_url": upload(_ink("canvas", "#0000ff", 595.28, 841.89))["url"],
                               "ink_strokes": 1})
    pdf = _pdf(tmp_path_factory.mktemp("ink") / "inked.pdf")
    return c, {**ids, "paper": paper, "group": group, "plain": plain, "book": book,
               "sheet": sheet, "scrawl": scrawl, "pdf": pdf}


@pytest.fixture
def stored_pdf(inked, monkeypatch):
    _, ids = inked
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: ids["pdf"])
    return ids


def test_page_with_ink_draws_the_strokes_on_that_page(tmp_path):
    path = _pdf(tmp_path / "two.pdf", pages=2)
    data, pages = page_with_ink(path, 2, [inkmod.parse_ink(_ink())])
    assert pages == 2 and data
    assert len(pdfium.PdfDocument(data)) == 1  # the page alone
    # The stroke runs corner to corner through the middle; the corner is paper.
    assert _pixel(data, 150, 200) == (255, 0, 0)
    assert _pixel(data, 290, 10) == (255, 255, 255)
    assert page_with_ink(path, 3, []) == (b"", 2)


def test_crop_box_pads_grows_and_stays_on_the_page():
    ink = inkmod.parse_ink(_ink(points=((100, 200), (110, 205))))
    x0, y0, x1, y1 = crop_box([ink], PAGE_W, PAGE_H)
    assert (x1 - x0) * PAGE_W == pytest.approx(CROP_MIN_SIDE)  # a tiny scribble shows 2 inches
    assert (y1 - y0) * PAGE_H == pytest.approx(CROP_MIN_SIDE)
    assert x0 * PAGE_W < 100 and x1 * PAGE_W > 110
    corner = inkmod.parse_ink(_ink(points=((2, 2), (5, 5))))
    x0, y0, x1, y1 = crop_box([corner], PAGE_W, PAGE_H)
    assert x0 == 0 and y0 == 0 and x1 * PAGE_W == pytest.approx(CROP_MIN_SIDE)
    assert crop_box([], PAGE_W, PAGE_H) is None


def test_view_ink_shows_a_group_on_its_pdf_page(stored_pdf):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]})
    assert text.startswith(f"Handwriting block [{ids['group']}] on PDF page 1"), text
    assert "cropped" in text and '"circled the key result"' in text and "[illegible]" in text
    assert action["kind"] == "ink" and action["block_id"] == ids["group"] and action["pdf_page"] == 1
    (media_type, data), = action["images"]
    assert media_type in ("image/png", "image/jpeg") and len(data) > 100
    # The whole page, with every group on it.
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink",
                                  {"block_id": ids["group"], "area": "page"})
    assert text.startswith('All the handwriting on PDF page 1 of "inked paper"') and action["images"]


def test_view_ink_shows_a_sheet_of_paper(stored_pdf):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["scrawl"]})
    assert f"Handwriting block [{ids['scrawl']}] on a page of paper" in text and "no caption yet" in text
    assert "pdf_page" not in action and action["images"]
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["sheet"]})
    assert text.startswith(f"The page of paper [{ids['sheet']}]") and "with all the handwriting" in text


def test_view_ink_refusals(stored_pdf, monkeypatch):
    ids = stored_pdf
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["plain"]})
    assert "holds no handwriting" in text and action["error"] and "images" not in action
    # Outside the chat's folder, and with the permission off.
    text, _ = run_agent_tool(ids["ws"], folder(ids["cooling"]), "view_ink", {"block_id": ids["group"]})
    assert text.startswith("error") and "outside" in text
    text, _ = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]},
                             allowed_tools={"read_block"})
    assert "not enabled" in text
    # A PDF this server doesn't hold: the strokes are still drawn, on blank paper.
    monkeypatch.setattr("gamma.ai_context.pdf_path", lambda ws, doc: None)
    text, action = run_agent_tool(ids["ws"], folder(ids["readout"]), "view_ink", {"block_id": ids["group"]})
    assert "drawn on blank paper" in text and action["images"]


def test_attached_handwriting_rides_with_the_message_as_a_picture(stored_pdf):
    ids = stored_pdf
    crops = []
    payload = SimpleNamespace(focus_block_id="", context_blocks=[ids["group"], ids["plain"]],
                              note_selections=[], pages=[], page_id=ids["paper"])
    section = notes_focus_section(ids["ws"], payload, crops=crops)
    assert f"[{ids['group']}] (handwriting on p. 1, 1 strokes; the text is its caption)" in section
    assert "a picture of this handwriting is attached" in section
    assert len(crops) == 1 and crops[0][0] in ("image/png", "image/jpeg")
    # The cursor block rides with every message: labelled, never pictured.
    crops = []
    payload = SimpleNamespace(focus_block_id=ids["group"], context_blocks=[], note_selections=[],
                              pages=[], page_id=ids["paper"])
    assert "handwriting on p. 1" in notes_focus_section(ids["ws"], payload, crops=crops)
    assert crops == []

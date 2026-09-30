"""Sheets of paper (gamma/notebook.py, docs/dev/notebooks.md): a page's
sheets are blocks carrying ``sheet`` — a notebook is just a page that has
some — so these tests go through the ordinary page and op endpoints; the
export draws the paper and the ink."""

import io
import json

from fractional_indexing import generate_key_between

from gamma import ink as inkmod
from gamma import notebook

A4 = notebook.normalize_paper(None)


def _page(client, title="Notebook"):
    r = client.post("/api/pages", json={"title": title})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _ops(client, page, ops):
    r = client.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": ops})
    assert r.status_code == 200, r.text
    return r.json()


def _sheet(sid, parent, position, paper=None):
    return {"op": "insert", "id": sid, "parent": parent, "position": position, "content": "",
            "props": {"sheet": paper or A4}}


def _canvas_ink(client, strokes, width=A4["width"], height=A4["height"]):
    data = {"format": "gamma-ink", "version": 1, "space": {"kind": "canvas", "width": width, "height": height},
            "strokes": strokes}
    body = json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    r = client.post("/api/upload-ink", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["pdf_position"] is None  # a sheet has no PDF page to point at
    return out["url"]


def _stroke(sid, x, y):
    return {"id": sid, "tool": "pen", "color": "#1d4ed8", "size": 2, "opacity": 1, "pen": True, "ch": "xyp",
            "pts": inkmod.encode_points([{"x": x, "y": y, "p": 0.5}, {"x": x + 40, "y": y + 10, "p": 0.6}], "xyp")}


def test_paper_is_read_through_normalize_and_sheets_come_in_block_order(guest):
    page = _page(guest)
    b = generate_key_between(None, None)
    _ops(guest, page, [_sheet("nbA", page, b),
                       _sheet("nbB", page, generate_key_between(b, None), {"width": "x", "pattern": "ruled", "height": 792}),
                       {"op": "insert", "id": "nbNote", "parent": page, "position": generate_key_between(None, b),
                        "content": "an intro note", "props": {}}])
    rows = guest.get(f"/api/blocks/{page}/subtree").json()["block"]
    flat = [{"id": rows["id"], "parent_id": "root", "position": "", "properties": rows["properties"]}]
    for c in rows["children"]:
        flat.append({"id": c["id"], "parent_id": page, "position": c["position"], "properties": c["properties"]})
    sheets = notebook.sheets_of(flat, page)
    assert [s["id"] for s in sheets] == ["nbA", "nbB"]
    # a bad key falls back to the built-in paper; the good ones stay
    assert sheets[1]["paper"]["width"] == A4["width"] and sheets[1]["paper"]["pattern"] == "ruled"
    assert sheets[1]["paper"]["height"] == 792


def test_pages_added_on_two_devices_at_once_both_stay(guest):
    """Adding a page is inserting a block: two writers appending after the
    same last sheet (each from the state it saw) both keep their page."""
    page = _page(guest)
    first = generate_key_between(None, None)
    _ops(guest, page, [_sheet("nb1", page, first)])
    after = generate_key_between(first, None)
    one = _ops(guest, page, [_sheet("nb2a", page, after)])
    two = _ops(guest, page, [_sheet("nb2b", page, after)])  # the same key: re-keyed, not refused
    assert one["ops"][0]["position"] != two["ops"][0]["position"]
    kids = guest.get(f"/api/blocks/{page}/subtree").json()["block"]["children"]
    assert [c["id"] for c in sorted(kids, key=lambda c: c["position"])][0] == "nb1"
    assert {c["id"] for c in kids} == {"nb1", "nb2a", "nb2b"}


def test_a_page_with_sheets_exports_as_a_pdf_of_them(guest):
    from PyPDF2 import PdfReader
    page = _page(guest, "Lecture 3")
    k1 = generate_key_between(None, None)
    k2 = generate_key_between(k1, None)
    letter = notebook.normalize_paper({"width": 612, "height": 792, "pattern": "grid", "spacing": 24})
    _ops(guest, page, [_sheet("nbX1", page, k1), _sheet("nbX2", page, k2, letter)])
    url = _canvas_ink(guest, [_stroke("s1", 100, 120), _stroke("s2", 100, 200)])
    _ops(guest, page, [{"op": "insert", "id": "nbInk1", "parent": "nbX1", "content": "a derivation",
                        "props": {"ink_url": url, "ink_strokes": 2}}])
    r = guest.get(f"/api/pages/{page}/export-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["X-Annotations-Written"] == "1"
    assert "Lecture 3" in r.headers["content-disposition"]
    reader = PdfReader(io.BytesIO(r.content))
    assert len(reader.pages) == 2
    sizes = [(round(float(p.mediabox.width), 2), round(float(p.mediabox.height), 2)) for p in reader.pages]
    assert sizes == [(A4["width"], A4["height"]), (612, 792)]
    first = reader.pages[0].get_contents().get_data()
    assert b"re f" in first and b" RG" in first            # the paper, then the ink's strokes
    assert first.count(b" l S") >= 2
    second = reader.pages[1].get_contents().get_data()
    assert second.count(b" l S") > 40                        # the grid's lines


def test_a_page_with_neither_a_pdf_nor_sheets_has_no_pdf_to_export(guest):
    page = _page(guest, "Plain notes")
    _ops(guest, page, [{"op": "insert", "id": "nbPlain", "parent": page, "position": generate_key_between(None, None),
                        "content": "just text", "props": {}}])
    r = guest.get(f"/api/pages/{page}/export-pdf")
    assert r.status_code == 400 and "no PDF" in r.text


def test_paper_ops_draw_the_pattern():
    ruled = notebook.normalize_paper({"width": 200, "height": 150, "pattern": "ruled", "spacing": 50})
    ops = notebook.paper_ops(ruled)
    assert ops.count(b" l S") == 2 and b"1.00 1.00 1.00 rg" in ops
    dots = notebook.normalize_paper({"width": 160, "height": 150, "pattern": "dots", "spacing": 50})
    assert notebook.paper_ops(dots).count(b" l S") == 6 and b"1 J" in notebook.paper_ops(dots)
    assert notebook.paper_ops(notebook.normalize_paper(None)).count(b" l S") == 0


def test_the_agent_reads_a_sheet_as_a_page_of_paper(guest):
    from gamma import ai_tools
    from gamma.db import connect_pages_db
    from gamma.workspaces import default_workspace
    from conftest import guest_name
    page = _page(guest, "Agent notebook")
    _ops(guest, page, [_sheet("nbAg", page, generate_key_between(None, None))])
    url = _canvas_ink(guest, [_stroke("s1", 50, 60)])
    _ops(guest, page, [{"op": "insert", "id": "nbAgInk", "parent": "nbAg", "content": "", "props": {"ink_url": url, "ink_strokes": 1}}])
    ws = default_workspace(guest_name())
    with connect_pages_db(ws) as conn:
        text, _ = ai_tools._run_read_block(conn, ws, {"pages": None}, {"block_id": page})
    assert "(a page of paper: the handwriting under it is written on it)" in text
    assert "(handwriting on the page of paper above, 1 strokes" in text

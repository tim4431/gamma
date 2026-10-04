"""The highlight shape (gamma/highlights.py, migration step 30): one
pdf_position — the page and its size once, the rects bare — written by
every writer that places a block on a PDF page (the annotation and Logseq
importers, ink), the block id as the highlight's id, and a page's PDF URL
derived from its doc_id rather than stored (attach, by-doc, the op path,
page creation, the clip and its lookup)."""

import io

import pytest

from conftest import login, make_page, make_user
from gamma.highlights import from_scaled, is_highlight, page_of, position
from gamma.pdf_export import annotate_pdf
from test_pdf_export import PAGE_H, PAGE_W, _blank_pdf

USER, PASSWORD = "hs_owner", "pw-hs-1"


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


# --- the shape -----------------------------------------------------------------------

def test_a_position_is_the_page_its_size_once_and_bare_rects():
    pos = position(3, 612, 792, [(10, 20, 30, 40), (5, 50, 25, 60)], area=True)
    assert pos == {"pageNumber": 3, "width": 612, "height": 792,
                   "boundingRect": {"x1": 5, "y1": 20, "x2": 30, "y2": 60},
                   "rects": [{"x1": 10, "y1": 20, "x2": 30, "y2": 40}, {"x1": 5, "y1": 50, "x2": 25, "y2": 60}],
                   "area": True}
    assert page_of({"pdf_position": pos}) == 3 and page_of({"pdf_position": {"pageNumber": True}}) is None
    assert page_of({"pdf_page": 3}) is None  # the position's page, nothing else
    assert is_highlight({"pdf_position": {"pageNumber": 1}}) and is_highlight({"pdf_position": pos, "link_url": "x"})
    assert not any(is_highlight(p) for p in (
        {}, {"pdf_position": None}, {"ink_url": "", "pdf_position": pos},
        {"text_box": {}, "pdf_position": pos}, {"sheet": {}, "pdf_position": pos}))


def test_from_scaled_keeps_every_rect_in_one_frame():
    """The scaled form (Logseq's EDN, Gamma before step 30): the frame is
    the position's size, else the first measured rect's, and a rect
    measured at another size is scaled into it; a stored position comes
    back as it is."""
    old = {"boundingRect": {"x1": 10, "y1": 10, "x2": 90, "y2": 40, "width": 100, "height": 200, "pageNumber": 2},
           "rects": [{"x1": 10, "y1": 10, "x2": 90, "y2": 20, "width": 100, "height": 200, "pageNumber": 2},
                     {"x1": 20, "y1": 60, "x2": 100, "y2": 80, "width": 200, "height": 400}]}
    new = from_scaled(old)
    assert new == {"pageNumber": 2, "width": 100, "height": 200,
                   "boundingRect": {"x1": 10, "y1": 10, "x2": 90, "y2": 40},
                   "rects": [{"x1": 10, "y1": 10, "x2": 90, "y2": 20}, {"x1": 10.0, "y1": 30.0, "x2": 50.0, "y2": 40.0}]}
    assert from_scaled(new) == new
    # the page from the caller when the position names none; no rect, no place
    assert from_scaled({"boundingRect": {"x1": 1, "y1": 1, "x2": 2, "y2": 2}}, 5)["pageNumber"] == 5
    assert from_scaled({"boundingRect": {}, "rects": []}, 4) == {"pageNumber": 4}
    assert from_scaled({"rects": [{"x1": 1, "y1": 1, "x2": 2, "y2": 2}]}) is None  # no page anywhere
    # an area flag inside the bounding rect, where Logseq-era positions kept it
    assert from_scaled({"pageNumber": 1, "boundingRect": {"x1": 0, "y1": 0, "x2": 1, "y2": 1, "area": True}})["area"]


# --- the writers -------------------------------------------------------------------------

def _upload(c, data, name="p.pdf"):
    r = c.post("/api/uploads", files={"file": (name, io.BytesIO(data), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()


def test_the_annotation_importer_writes_the_shape_under_the_block_id(owner):
    rect = {"x1": 100, "y1": 72, "x2": 300, "y2": 92}
    pdf, _ = annotate_pdf(_blank_pdf(), [
        {"position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H, "boundingRect": rect, "rects": [rect]},
         "color": "rgba(170, 235, 170, 0.65)", "note": "a note"},
        {"position": {"pageNumber": 1, "width": PAGE_W, "height": PAGE_H, "boundingRect": rect, "rects": [rect],
                      "area": True}, "color": None, "note": "", "id": "area"}])
    up = _upload(owner, pdf)
    page = owner.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "Imported"}).json()
    r = owner.post("/api/import/pdf-annotations", json={"block_id": page["id"], "doc_id": up["doc_id"]})
    assert r.status_code == 200 and r.json()["imported"] == 2, r.text
    kids = owner.get(f"/api/blocks/{page['id']}/children").json()["children"]
    assert [k["kind"] for k in kids] == ["highlight", "highlight"]
    for k in kids:
        props = k["properties"]
        assert "highlight_id" not in props and "pdf_page" not in props
        pos = props["pdf_position"]
        assert (pos["pageNumber"], pos["width"], pos["height"]) == (1, PAGE_W, PAGE_H)
        assert all(set(r) == {"x1", "y1", "x2", "y2"} for r in (pos["boundingRect"], *pos["rects"]))
        assert abs(pos["boundingRect"]["x1"] - 100) < 0.01 and abs(pos["boundingRect"]["y2"] - 92) < 0.01
    assert [bool(k["properties"]["pdf_position"].get("area")) for k in kids] == [False, True]


EDN = ('{:highlights [{:id #uuid "6500e1f4-0000-4000-8000-0000000000aa" :page 2 :position '
       '{:bounding {:x1 10 :y1 20 :x2 110 :y2 60 :width 600 :height 800} '
       ':rects [{:x1 10 :y1 20 :x2 110 :y2 40 :width 600 :height 800}] :page 2} '
       ':content {:text "an edn quote"} :properties {:color "green"}}]}')
MD = ("- an edn quote\n  ls-type:: annotation\n  hl-page:: 2\n  id:: 6500e1f4-0000-4000-8000-0000000000aa\n"
      "- a quote the EDN lost\n  ls-type:: annotation\n  hl-page:: 7\n")


def test_the_logseq_importer_writes_the_shape(owner):
    from test_writer_races import _pdf

    pdf = _pdf("hs-logseq")
    r = owner.post("/api/import/logseq", files={
        "pdf": ("paper.pdf", pdf, "application/pdf"),
        "edn": ("hls.edn", EDN.encode(), "application/octet-stream"),
        "md": ("hls.md", MD.encode(), "text/markdown")})
    assert r.status_code == 200, r.text
    kids = {k["properties"]["quote"]: k for k in
            owner.get(f"/api/blocks/{r.json()['block_id']}/children").json()["children"]}
    placed = kids["an edn quote"]["properties"]
    assert placed["pdf_position"] == {"pageNumber": 2, "width": 600, "height": 800,
                                      "boundingRect": {"x1": 10, "y1": 20, "x2": 110, "y2": 60},
                                      "rects": [{"x1": 10, "y1": 20, "x2": 110, "y2": 40}]}
    # no box in the EDN: its page alone, still a highlight
    lost = kids["a quote the EDN lost"]
    assert lost["properties"]["pdf_position"] == {"pageNumber": 7} and lost["kind"] == "highlight"
    assert not any("highlight_id" in k["properties"] or "pdf_page" in k["properties"] for k in kids.values())


# --- the PDF's URL ---------------------------------------------------------------------

def test_a_pages_pdf_url_is_derived_from_its_doc_id(owner):
    """No writer stores the stored copy's URL: by-doc and the op path drop
    it (an older client may send it), a page created with it too; a URL of
    its own stays."""
    up = _upload(owner, b"%PDF-1.4 the derived url\n")
    doc, url = up["doc_id"], up["source_url"]
    assert url == f"/api/uploads/{doc}.pdf"  # the upload still answers where its file is
    page = owner.post(f"/api/blocks/by-doc/{doc}", json={"default_title": "By doc", "source_url": url}).json()
    assert "source_url" not in page["properties"] and page["content"] == "By doc"
    r = owner.post(f"/api/pages/{page['id']}/ops", json={"client": "old", "ops": [
        {"op": "set", "id": page["id"], "props": {"source_url": url}}]})
    assert r.status_code == 200 and r.json()["ops"][0]["props"] == {"source_url": None}, r.text
    assert "source_url" not in owner.get(f"/api/blocks/{page['id']}").json()["properties"]
    made = owner.post("/api/pages", json={"title": "Made", "properties": {"doc_id": "a" * 24,
                                                                         "source_url": f"/api/uploads/{'a' * 24}.pdf"}})
    assert made.status_code == 200 and made.json()["properties"] == {"doc_id": "a" * 24}, made.text
    own = make_page(owner, "Proxied", properties={"doc_id": "b" * 24, "source_url": "https://x.test/p.pdf"})
    assert owner.get(f"/api/blocks/{own['id']}").json()["properties"]["source_url"] == "https://x.test/p.pdf"


def test_a_lookup_by_the_stored_copys_url_still_finds_the_page(owner):
    up = _upload(owner, b"%PDF-1.4 looked up by its url\n")
    page = owner.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "Looked up"}).json()
    r = owner.get("/api/library/lookup", params={"url": up["source_url"]})
    assert r.status_code == 200 and r.json()["block_id"] == page["id"], r.text

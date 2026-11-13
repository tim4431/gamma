"""The feedback reader (gamma/feedback/entries.py, docs/dev/feedback.md "The
entry and the document") and its Markdown (gamma/feedback/markdown.py): the
threads of a work folder, a directory or a file as entries — every kind's
place and how it stands after a rebuild (exact, rough, estimate, lost,
pending, earlier), a picture's regions and the whole picture, the file's
name from the vault's rows and from the folder tree, the order, a Send
waiting, a tombstone, the cut past ``MAX_ENTRIES``, the state filters, the
scopes and their refusals, a root from before step 54 read from its block,
the source live or as commented (the SyncTeX map stood in for:
gamma/feedback/sources.py has its own tests) and the pictures' keys; then
the Markdown of a fixed document byte for byte, its empty form and its
picture modes."""

import io
import json
import sys
from types import SimpleNamespace

import pytest

import gamma.feedback
from conftest import (account_of, login, make_folder, make_page, make_user, mark_work_folder, thread_on, work_props,
                      workspace_of)
from gamma import storage
from gamma.vault import store as vault_store
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db
from gamma.documents.model import document
from gamma.feedback import anchors, entries, kept, markdown, pictures, requests
from gamma.feedback.entries import FeedbackError, Scope
from gamma.ops import commit_ops

USER, PASSWORD = "fbe_own", "pw-fbe-1"
FRAME = {"width": 600, "height": 800}
COORDS = ("x1", "y1", "x2", "y2")


def _pdf(pages: int, text: str) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=600, height=800).insert_text((72, 72), f"{text}, page {n + 1}", fontsize=11)
    return doc.tobytes()


def _png(width: int, height: int, color="white") -> bytes:
    from PIL import Image

    out = io.BytesIO()
    Image.new("RGB", (width, height), color).save(out, "PNG")
    return out.getvalue()


def _picture(hash: str, **more) -> dict:
    return document(hash, name="fig.png", format="image", ext=".png", **more)


def _region(*rect, hash: str, **more) -> dict:
    """An ``image_position`` over ``rect`` of a 1200 × 800 picture, drawn on
    its version ``hash``."""
    box = dict(zip(COORDS, rect))
    return {"width": 1200, "height": 800, "boundingRect": box, "rects": [box], "hash": hash, **more}


def _pos(page, *rects, area=False, **more) -> dict:
    out = {"pageNumber": page, **FRAME, "boundingRect": {
        "x1": min(r[0] for r in rects), "y1": min(r[1] for r in rects),
        "x2": max(r[2] for r in rects), "y2": max(r[3] for r in rects)},
        "rects": [dict(zip(("x1", "y1", "x2", "y2"), r)) for r in rects], **more}
    if area:
        out["area"] = True
    return out


def _ops(client, page_id, ops):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": ops})
    assert r.status_code == 200, r.text


def _insert(block_id, parent, content="", **props):
    return {"op": "insert", "id": block_id, "parent": parent, "content": content, "props": props}


@pytest.fixture(autouse=True)
def synctex(monkeypatch):
    """The SyncTeX map stood in for: ``found`` is what ``source_for``
    answers, ``asked`` the places it was asked about."""
    fake = SimpleNamespace(found=None, asked=[])

    def source_for(ws, conn, page_id, props, place):
        fake.asked.append((page_id, place))
        return fake.found

    fake.source_for = source_for
    monkeypatch.setitem(sys.modules, "gamma.feedback.sources", fake)
    monkeypatch.setattr(gamma.feedback, "sources", fake, raising=False)
    return fake


@pytest.fixture(scope="module")
def paper():
    """A work folder ``fbe-paper`` (W) with a directory ``figures`` (D) and
    a library folder beside it. In W: ``main.pdf`` (five pages) with a
    passage H on page 3, a note N under it, an area A on page 5, handwriting
    I on page 2 and a note T on the page; ``figures/fig1.pdf``; the note
    file ``notes.md``. A thread on each of them and on each file, and one on
    a library page. A second work folder M takes the files a test makes."""
    make_user(USER, PASSWORD)
    own = login(USER, PASSWORD)
    ws, account = workspace_of(USER), account_of(USER)
    W, D, L = make_folder(own, "fbe-paper"), make_folder(own, "fbe-paper/figures"), make_folder(own, "fbe-library")
    M = make_folder(own, "fbe-more")
    mark_work_folder(own, W)
    mark_work_folder(own, M)
    v1, _ = storage.store_pdf(ws, _pdf(5, "build one"))
    fig, _ = storage.store_pdf(ws, _pdf(1, "a figure"))
    with connect_pages_db(ws) as conn:
        def vault(title, props):
            return create_page(conn, title, props, actor=account, client=DISK_CLIENT)["id"]

        main = vault("main", work_props(W, "main.pdf", doc=document(v1, name="main.pdf")))
        figure = vault("fig1", work_props(W, "fig1.pdf", doc=document(fig, name="fig1.pdf"), directory=D))
        notes = vault("notes", work_props(W, "notes.md"))
    library = make_page(own, "a library paper", properties={"folders": [L]})["id"]
    _ops(own, main, [_insert("fbeH", main, pdf_position=_pos(3, (60, 80, 300, 96)), quote="the loss is defined as",
                             color="#ff0000"),
                     _insert("fbeA", main, pdf_position=_pos(5, (60, 400, 540, 720), area=True)),
                     _insert("fbeI", main, ink_url="", pdf_position=_pos(2, (100, 100, 160, 140))),
                     _insert("fbeT", main, "check the abstract")])
    _ops(own, main, [_insert("fbeN", "fbeH", "why this name?")])
    t = {}
    for key, page, block, body in (("file", main, main, "the title is too long"), ("H", main, "fbeH", "define it"),
                                   ("A", main, "fbeA", "move the legend inside the plot"),
                                   ("I", main, "fbeI", "arrow: here"), ("N", main, "fbeN", "rename it"),
                                   ("T", main, "fbeT", "the abstract"), ("fig", figure, figure, "use viridis"),
                                   ("notes", notes, notes, "a todo"), ("library", library, library, "not feedback")):
        t[key] = thread_on(own, page, block, body)["id"]
    return SimpleNamespace(own=own, ws=ws, account=account, W=W, D=D, L=L, M=M, v1=v1, fig=fig, main=main,
                           figure=figure, notes=notes, library=library, t=t)


def _document(p, scope_id=None, **kw):
    with connect_pages_db(p.ws) as conn:
        return entries.document(p.ws, conn, entries.resolve_scope(conn, scope_id or p.W), **kw)


def _by_id(doc) -> dict:
    return {e["id"]: e for e in doc["entries"]}


# ---- the entries ------------------------------------------------------------------------

def test_a_work_folders_entries_in_their_order(paper):
    p = paper
    doc = _document(p, owner="tim")
    assert [e["id"] for e in doc["entries"]] == [p.t[k] for k in ("fig", "file", "I", "H", "N", "A", "T", "notes")]
    assert [e["file"] for e in doc["entries"]] == ["figures/fig1.pdf", *["main.pdf"] * 6, "notes.md"]
    assert {k: doc[k] for k in ("format", "workspace", "work_folder", "scope", "owner", "state", "count")} == {
        "format": "gamma-feedback-1", "workspace": p.ws, "work_folder": {"id": p.W, "name": "fbe-paper"},
        "scope": {"kind": "work_folder", "id": p.W, "name": "fbe-paper"}, "owner": "tim", "state": "open", "count": 8}
    assert "truncated" not in doc and doc["answer"] == entries.ANSWER
    assert list(doc) == ["format", "workspace", "work_folder", "scope", "owner", "state", "updated_at", "count",
                         "answer", "entries"]
    with connect_pages_db(p.ws) as conn:
        newest = max(t["last_at"] for t in entries.comments.threads_in_work_folder(conn, p.W, state="open"))
    assert doc["updated_at"] == newest


def test_every_kind_reads_its_place(paper):
    p = paper
    got = _by_id(_document(p))
    with connect_pages_db(p.ws) as conn:
        box = {k: anchors.anchor_of(conn, p.main, f"fbe{k}")["box"] for k in ("A", "I")}
    H, A, I, N, T = (got[p.t[k]] for k in ("H", "A", "I", "N", "T"))
    assert (H["kind"], H["where"]) == ("passage", {"page": 3, "quote": "the loss is defined as", "placed": "exact"})
    assert (A["kind"], A["where"]) == ("area", {"page": 5, "box": box["A"], "placed": "exact"})
    assert (I["kind"], I["where"]) == ("ink", {"page": 2, "box": box["I"], "placed": "exact"})
    assert (N["kind"], N["block_id"], N["where"]) == (
        "passage", "fbeN", {"page": 3, "quote": "the loss is defined as", "placed": "exact", "note": "why this name?"})
    assert (T["kind"], T["where"]) == ("note", {"note": "check the abstract"})
    for key in ("file", "fig", "notes"):
        assert (got[p.t[key]]["kind"], got[p.t[key]]["where"]) == ("file", {})
    assert got[p.t["notes"]]["format"] == "markdown" and H["format"] == "pdf"
    assert list(H) == ["id", "file", "format", "page_id", "block_id", "kind", "where", "source", "picture", "before",
                       "state", "sent", "link", "comments"]
    assert H["link"] == f"?ws={p.ws}&page={p.main}&block=fbeH&thread={p.t['H']}"
    assert H["comments"] == [{"id": p.t["H"], "by": USER, "agent": "", "at": H["comments"][0]["at"],
                              "body": "define it", "deleted": False}]
    assert (H["state"], H["sent"], H["source"], H["before"]) == ("open", None, None, None)


def test_pictures_are_named_by_their_regions_key(paper):
    p = paper
    regions: dict = {}
    got = _by_id(_document(p, regions=regions))
    with connect_pages_db(p.ws) as conn:
        props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (p.main,)).fetchone()[0])
        for key in ("H", "A", "I", "N"):
            anchor = anchors.parse(conn.execute("SELECT anchor FROM comments WHERE id = ?", (p.t[key],)).fetchone()[0])
            region = pictures.now(conn, p.main, props, anchor)
            assert got[p.t[key]]["picture"] == f"img/{pictures.key(region)}.jpg"
            assert regions[pictures.key(region)] == (p.main, region)
    assert got[p.t["N"]]["picture"] == got[p.t["H"]]["picture"]  # a note under a passage: the passage's region
    for key in ("T", "file", "fig", "notes"):
        assert got[p.t[key]]["picture"] is None and got[p.t[key]]["before"] is None
    assert len(regions) == 3


def test_placed_after_a_rebuild(paper):
    """A rebuild replaces the file: each highlight stands as the run left it
    (roughly, by estimate, lost, not placed yet), ink is drawn on an earlier
    build, and the version commented on, while stored, gives ``before``."""
    p = paper
    old, _ = storage.store_pdf(p.ws, _pdf(5, "build zero"))
    new = "c" * 24  # the new build: not stored here, read in place on the person's computer
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "rebuilt", work_props(p.M, "rebuilt.pdf", doc=document(old, name="rebuilt.pdf")),
                           actor=p.account, client=DISK_CLIENT)["id"]
    rects, areas = (60, 80, 300, 96), ("E", "S")
    _ops(p.own, page, [_insert(f"fbr{k}", page, pdf_position=_pos(1, rects, area=k in areas), quote="" if k in areas
                               else f"passage {k}") for k in ("R", "E", "S", "L", "P", "X")]
         + [_insert("fbrI", page, ink_url="", pdf_position=_pos(1, (100, 100, 160, 140)))])
    t = {k: thread_on(p.own, page, f"fbr{k}", f"on {k}")["id"] for k in ("R", "E", "S", "L", "P", "X", "I")}
    commit_ops(p.ws, page, [{"op": "set", "id": page, "props": {"doc": document(new, name="rebuilt.pdf", at="path")}}],
               actor=p.account, client=DISK_CLIENT)
    # E: an area the run could not place keeps its rectangles on the old build, approx without a match
    flags = {"R": {"approx": True, "match": 0.82}, "E": {"approx": True, "doc_id": old}, "S": {"approx": True},
             "L": {"lost": True, "doc_id": old}, "P": {"doc_id": old}, "X": {}}
    _ops(p.own, page, [{"op": "set", "id": f"fbr{k}", "props": {"pdf_position": _pos(1, rects, area=k in areas, **more)}}
                       for k, more in flags.items()])
    regions: dict = {}
    with connect_pages_db(p.ws) as conn:
        got = {e["id"]: e for e in entries.entries(p.ws, conn, entries.resolve_scope(conn, page), regions=regions)}
    placed = {k: got[t[k]]["where"]["placed"] for k in t}
    assert placed == {"R": "rough", "E": "pending", "S": "estimate", "L": "lost", "P": "pending", "X": "exact",
                      "I": "earlier"}
    assert got[t["R"]]["where"]["match"] == 0.82 and "match" not in got[t["S"]]["where"]
    for k in ("E", "L", "P", "I"):
        assert got[t[k]]["picture"] is None  # not on this build
    for k in t:  # every thread was opened on the stored earlier build
        assert got[t[k]]["before"] and regions[entries.key_of(got[t[k]]["before"])][1].file == f"{old}.pdf"
    assert got[t["E"]]["where"]["box"] == anchors.parse(_anchor_of(p.ws, t["E"]))["box"]  # still measured on it
    # the picture of a rough placement is the new build's (no file here to draw it from: a key all the same)
    assert regions[entries.key_of(got[t["R"]]["picture"])][1].file == f"{new}.pdf"


def _anchor_of(ws, thread_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT anchor FROM comments WHERE id = ?", (thread_id,)).fetchone()[0]


def test_an_area_dragged_on_the_same_build_keeps_the_threads_place(paper, synctex):
    """An area's or ink's thread stays where it was opened while the file is
    the anchor's: its page, box, picture and source line agree, the block
    dragged or not. A passage follows its highlight."""
    p = paper
    steady, _ = storage.store_pdf(p.ws, _pdf(3, "a steady build"))
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "steady", work_props(p.M, "steady.pdf", doc=document(steady, name="steady.pdf")),
                           actor=p.account, client=DISK_CLIENT)["id"]
    _ops(p.own, page, [_insert("fbdA", page, pdf_position=_pos(1, (60, 80, 300, 96), area=True)),
                       _insert("fbdH", page, pdf_position=_pos(1, (60, 80, 300, 96)), quote="steady words")])
    ta, th = (thread_on(p.own, page, block, "here")["id"] for block in ("fbdA", "fbdH"))
    anchor = anchors.parse(_anchor_of(p.ws, ta))
    _ops(p.own, page, [{"op": "set", "id": "fbdA", "props": {"pdf_position": _pos(2, (60, 400, 300, 416), area=True)}},
                       {"op": "set", "id": "fbdH", "props": {"pdf_position": _pos(2, (60, 400, 300, 416)),
                                                             "quote": "moved words"}}])
    assert kept.wait()        # the kept version's worker asked for the source at the threads' open
    synctex.asked.clear()
    synctex.found = {"file": "main.tex", "line": 7}
    regions: dict = {}
    with connect_pages_db(p.ws) as conn:
        got = {e["id"]: e for e in entries.entries(p.ws, conn, entries.resolve_scope(conn, page), regions=regions)}
    area, passage = got[ta], got[th]
    assert (area["where"]["page"], area["where"]["box"], area["where"]["placed"]) == (1, anchor["box"], "exact")
    assert regions[entries.key_of(area["picture"])][1][1:3] == (1, tuple(anchor["box"]))
    assert [place["rects"] for pid, place in synctex.asked if pid == page and place["kind"] == "area"] == [anchor["rects"]]
    assert area["source"] == {"file": "main.tex", "line": 7, "version": "current"}
    assert (passage["where"]["page"], passage["where"]["quote"], passage["where"]["placed"]) == (2, "moved words", "exact")


def test_a_pictures_regions_the_whole_picture_and_their_order(paper):
    """A region of a picture: its place in the picture's own pixels and as
    fractions, exact while the picture is the version it was drawn on, with
    the current version's crop and the page's regions drawn; a note under
    one takes its place; the page's thread gets the whole picture; the
    order in the file: the whole picture, the regions by top, the notes.
    A rebuild leaves the regions where they were, drawn on an earlier
    version, with the held version's crop as ``before``; a file that is no
    longer a picture loses them."""
    p = paper
    name, _ = storage.store_file(p.ws, _png(1200, 800), ".png")
    png = name[:-4]
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "fig.png", work_props(p.M, "fig.png", doc=_picture(png)), actor=p.account,
                           client=DISK_CLIENT)["id"]
    _ops(p.own, page, [_insert("fbgR", page, image_position=_region(120, 80, 360, 240, hash=png), color="#ff0000"),
                       _insert("fbgS", page, image_position=_region(600, 400, 900, 600, hash=png)),
                       _insert("fbgT", page, "a note on the page")])
    _ops(p.own, page, [_insert("fbgN", "fbgR", "why red?")])
    t = {k: thread_on(p.own, page, block, body)["id"] for k, block, body in (
        ("T", "fbgT", "the note"), ("S", "fbgS", "the lower region"), ("R", "fbgR", "the region"),
        ("N", "fbgN", "under the region"), ("file", page, "the whole picture"))}

    def read():
        regions: dict = {}
        with connect_pages_db(p.ws) as conn:
            got = entries.entries(p.ws, conn, entries.resolve_scope(conn, page), regions=regions)
        return [e["id"] for e in got], {e["id"]: e for e in got}, regions

    order, by, regions = read()
    assert order == [t[k] for k in ("file", "R", "N", "S", "T")]
    R, N, S, F = (by[t[k]] for k in ("R", "N", "S", "file"))
    assert (R["format"], R["kind"], R["where"]) == ("image", "region", {
        "box": [0.095, 0.095, 0.305, 0.305], "rect": [120, 80, 360, 240], "size": [1200, 800], "placed": "exact"})
    assert list(R["where"]) == ["box", "rect", "size", "placed"]
    assert (N["kind"], N["block_id"], N["where"]) == ("region", "fbgN", {**R["where"], "note": "why red?"})
    assert (R["source"], R["before"], S["where"]["rect"]) == (None, None, [600, 400, 900, 600])
    assert R["picture"] == N["picture"] and regions[entries.key_of(R["picture"])][1] == pictures.Region(
        name, 0, (0.095, 0.095, 0.305, 0.305),
        (("region", "fbgR", "#ff0000", ((0.1, 0.1, 0.3, 0.3),), True), ("region", "fbgS", "", ((0.5, 0.5, 0.75, 0.75),), True)))
    assert (F["kind"], F["where"], F["before"]) == ("file", {}, None)
    assert regions[entries.key_of(F["picture"])][1].box == (0.0, 0.0, 1.0, 1.0)
    assert by[t["T"]]["picture"] is None and by[t["T"]]["kind"] == "note"

    new = "d" * 24   # the picture rebuilt: not stored here, read in place on the person's computer
    commit_ops(p.ws, page, [{"op": "set", "id": page, "props": {"doc": _picture(new, at="path")}}], actor=p.account,
               client=DISK_CLIENT)
    order, by, regions = read()
    R, F = by[t["R"]], by[t["file"]]
    assert R["where"]["placed"] == "earlier" and R["where"]["rect"] == [120, 80, 360, 240]
    now, before = (regions[entries.key_of(R[k])][1] for k in ("picture", "before"))
    assert (now.file, now.page, now.box, now.drawn[0][4]) == (f"{new}.png", 0, (0.095, 0.095, 0.305, 0.305), False)
    assert before == pictures.Region(name, 0, (0.095, 0.095, 0.305, 0.305),
                                     (("anchor", "region", "#ff0000", ((0.1, 0.1, 0.3, 0.3),)),))
    whole = regions[entries.key_of(F["before"])][1]
    assert (whole.file, whole.box, whole.drawn) == (name, (0.0, 0.0, 1.0, 1.0), (("anchor", "file", "", ()),))
    assert regions[entries.key_of(F["picture"])][1].file == f"{new}.png"

    commit_ops(p.ws, page, [{"op": "set", "id": page, "props": {
        "doc": document(new, name="fig.tex", format="text", ext=".tex", at="path")}}], actor=p.account, client=DISK_CLIENT)
    order, by, regions = read()
    R, F = by[t["R"]], by[t["file"]]
    assert (R["format"], R["where"]["placed"], R["picture"]) == ("text", "lost", None) and R["before"]
    assert (F["picture"], F["before"]) == (None, None)


def test_a_root_from_before_step_54_is_read_from_its_block(paper):
    p = paper
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "older", work_props(p.M, "older.pdf", doc=document(p.v1, name="older.pdf")),
                           actor=p.account, client=DISK_CLIENT)["id"]
    _ops(p.own, page, [_insert("fboH", page, pdf_position=_pos(2, (60, 80, 300, 96)), quote="an older passage")])
    _ops(p.own, page, [_insert("fboN", "fboH", "a note under it")])
    thread = thread_on(p.own, page, "fboN", "from before")["id"]
    before = _entry(p, thread)
    with connect_pages_db(p.ws) as conn:
        conn.execute("UPDATE comments SET anchor = NULL WHERE id = ?", (thread,))
        conn.commit()
    after = _entry(p, thread)
    assert after["kind"] == "passage" and after["where"] == before["where"] == {
        "page": 2, "quote": "an older passage", "placed": "exact", "note": "a note under it"}
    assert after["picture"] == before["picture"] and after["before"] is None


def _entry(p, thread_id, **kw):
    with connect_pages_db(p.ws) as conn:
        return entries.entry(p.ws, conn, thread_id, **kw)


def test_a_place_whose_block_went_keeps_the_anchors(paper):
    """A note moved out from under its passage, the passage deleted: the
    thread still names the place it was opened on."""
    p = paper
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "moved", work_props(p.M, "moved.pdf", doc=document(p.v1, name="moved.pdf")),
                           actor=p.account, client=DISK_CLIENT)["id"]
    _ops(p.own, page, [_insert("fbmH", page, pdf_position=_pos(4, (60, 80, 300, 96)), quote="a passage that goes")])
    _ops(p.own, page, [_insert("fbmN", "fbmH", "a note that stays")])
    thread = thread_on(p.own, page, "fbmN", "on the note")["id"]
    _ops(p.own, page, [{"op": "move", "id": "fbmN", "parent": page}])
    _ops(p.own, page, [{"op": "delete", "id": "fbmH"}])
    got = _entry(p, thread)
    assert (got["kind"], got["where"]) == ("passage", {"page": 4, "quote": "a passage that goes", "placed": "exact",
                                                       "note": "a note that stays"})


def test_the_source_live_or_as_commented(paper, synctex):
    p = paper
    synctex.found = {"file": "sections/method.tex", "line": 118, "end": 120}
    assert _entry(p, p.t["H"])["source"] == {"file": "sections/method.tex", "line": 118, "end": 120,
                                             "version": "current"}
    page_id, place = synctex.asked[-1]
    assert page_id == p.main and place == {"kind": "passage", "page": 3, "rects": [[0.1, 0.1, 0.5, 0.12]]}
    synctex.found = {"file": "main.tex", "line": 18, "end": 18}
    assert _entry(p, p.t["A"])["source"] == {"file": "main.tex", "line": 18, "version": "current"}
    asked = len(synctex.asked)
    assert _entry(p, p.t["T"])["source"] is None and _entry(p, p.t["file"])["source"] is None
    assert len(synctex.asked) == asked  # a note alone and the whole file have no place to map
    synctex.found = None
    assert _entry(p, p.t["H"])["source"] is None
    with connect_pages_db(p.ws) as conn:
        conn.execute("UPDATE comments SET anchor = json_set(anchor, '$.source', json(?)) WHERE id = ?",
                     (json.dumps({"file": "sections/method.tex", "line": 112}), p.t["H"]))
        conn.commit()
    try:
        assert _entry(p, p.t["H"])["source"] == {"file": "sections/method.tex", "line": 112, "version": "commented"}
        synctex.found = {"file": "sections/method.tex", "line": 130}
        assert _entry(p, p.t["H"], sources=False)["source"]["version"] == "commented"
        assert _entry(p, p.t["H"])["source"] == {"file": "sections/method.tex", "line": 130, "version": "current"}
    finally:
        with connect_pages_db(p.ws) as conn:
            conn.execute("UPDATE comments SET anchor = json_remove(anchor, '$.source') WHERE id = ?", (p.t["H"],))
            conn.commit()


def test_states_a_tombstone_and_a_send(paper):
    p = paper
    with connect_pages_db(p.ws) as conn:
        page = create_page(conn, "states", work_props(p.M, "states.pdf"), actor=p.account, client=DISK_CLIENT)["id"]
    done = thread_on(p.own, page, page, "done already")["id"]
    tomb = thread_on(p.own, page, page, "deleted, its reply stays")["id"]
    assert p.own.post(f"/api/pages/{page}/comments", json={"block_id": page, "body": "the reply",
                                                          "thread_id": tomb}).status_code == 201
    assert p.own.delete(f"/api/comments/{tomb}").status_code == 200
    assert p.own.post(f"/api/comments/{done}/resolve", json={"resolved": True}).status_code == 200

    def ids(state):
        with connect_pages_db(p.ws) as conn:
            return [e["id"] for e in entries.entries(p.ws, conn, entries.resolve_scope(conn, page), state=state)]

    assert ids("open") == [tomb] and ids("resolved") == [done] and sorted(ids("all")) == sorted([done, tomb])
    with pytest.raises(FeedbackError) as e:
        ids("later")
    assert e.value.status == 400
    got = _entry(p, tomb)
    assert [(c["body"], c["deleted"]) for c in got["comments"]] == [("", True), ("the reply", False)]
    assert _entry(p, done)["state"] == "resolved"
    with connect_pages_db(p.ws) as conn:
        sent = requests.create(p.ws, conn, entries.resolve_scope(conn, page), by=p.account, by_name=USER)["request"]
    assert _entry(p, tomb)["sent"] == {"request_id": sent["id"], "state": "sent", "at": sent["at"]}


def test_a_document_is_cut_past_its_entries(paper, monkeypatch):
    monkeypatch.setattr(entries, "MAX_ENTRIES", 3)
    doc = _document(paper)
    assert doc["count"] == 3 and len(doc["entries"]) == 3 and doc["truncated"] >= 5
    assert [e["id"] for e in doc["entries"]] == [paper.t[k] for k in ("fig", "file", "I")]


def test_scopes(paper):
    p = paper
    with connect_pages_db(p.ws) as conn:
        assert entries.resolve_scope(conn, p.W) == Scope("work_folder", p.W, p.W, "fbe-paper")
        assert entries.resolve_scope(conn, p.D) == Scope("folder", p.D, p.W, "figures")
        assert entries.resolve_scope(conn, p.figure) == Scope("file", p.figure, p.W, "figures/fig1.pdf")
        for scope_id, status in ((p.L, 400), (p.library, 400), ("noSuchThing", 404), ("", 404), ("folders", 404)):
            with pytest.raises(FeedbackError) as e:
                entries.resolve_scope(conn, scope_id)
            assert e.value.status == status, scope_id
        assert "work folder's files" in str(_refusal(lambda: entries.resolve_scope(conn, p.library)))
        assert entries.work_folder_named(conn, "FBE-Paper") == entries.work_folder_named(conn, p.W)
        assert _refusal(lambda: entries.work_folder_named(conn, "no such folder")).status == 404
    assert [e["id"] for e in _document(p, p.D)["entries"]] == [p.t["fig"]]
    assert _document(p, p.D)["scope"] == {"kind": "folder", "id": p.D, "name": "figures"}
    assert [e["id"] for e in _document(p, p.main)["entries"]] == [p.t[k] for k in ("file", "I", "H", "N", "A", "T")]
    assert p.t["library"] not in {e["id"] for e in _document(p)["entries"]}
    with connect_pages_db(p.ws) as conn:
        assert _entry(p, p.t["library"]) is None and _entry(p, "noSuchThread") is None


def test_two_work_folders_of_one_name(paper):
    p = paper
    twins = [make_folder(p.own, f"fbe-twins/{side}/fbe-twin") for side in ("a", "b")]
    for twin in twins:
        mark_work_folder(p.own, twin)
    with connect_pages_db(p.ws) as conn:
        assert _refusal(lambda: entries.work_folder_named(conn, "fbe-twin")).status == 400
        assert entries.work_folder_named(conn, twins[1]).id == twins[1]
        assert entries.thread_ids(conn, entries.resolve_scope(conn, twins[0])) == []


def _refusal(fn) -> FeedbackError:
    with pytest.raises(FeedbackError) as e:
        fn()
    return e.value


def test_a_files_name_from_the_vaults_rows_or_the_tree(paper):
    p = paper
    rows = {path: {"path": path, "page_id": page_id, "kind": kind, "was": was} for path, page_id, kind, was in (
        ("figures/fig1.md", p.figure, "notes", ""), ("figures/fig1.pdf", p.figure, "pdf", ""),
        ("paper/notes.md", p.notes, "notes", ""), (vault_store.KEPT + "old.pdf", "gone", "pdf", "old.pdf"),
        (".trash/x.pdf", "trashed", "pdf", "x.pdf"), ("untracked.txt", "", "pdf", ""))}
    paths = entries.paths_of(rows)
    assert paths == {p.figure: "figures/fig1.pdf", p.notes: "paper/notes.md"}
    with connect_pages_db(p.ws) as conn:
        got = {e["id"]: e["file"] for e in entries.entries(p.ws, conn, entries.resolve_scope(conn, p.W), paths=paths)}
        tree = entries.Tree.read(conn)
    assert got[p.t["notes"]] == "paper/notes.md" and got[p.t["H"]] == "main.pdf"
    assert entries.file_path(tree, {"work_folder": p.W, "folders": [p.D]}, title="a title") == "a title"
    assert entries.file_path(tree, work_props(p.W, "x.pdf", directory=p.D)) == "figures/x.pdf"


# ---- the Markdown ------------------------------------------------------------------------

def _comment(cid, by, at, body, agent=""):
    return {"id": cid, "by": by, "agent": agent, "at": at, "body": body, "deleted": False}


FIXED = {"format": "gamma-feedback-1", "workspace": "ws1", "work_folder": {"id": "W", "name": "paper"},
         "scope": {"kind": "work_folder", "id": "W", "name": "paper"}, "owner": "tim", "state": "open",
         "updated_at": "2026-11-10T13:58:41.120000Z", "count": 3, "answer": entries.ANSWER, "entries": [
             {"id": "0d7c5e2a9f1b", "file": "main.pdf", "format": "pdf", "page_id": "P1", "block_id": "H1",
              "kind": "passage", "where": {"page": 3, "quote": "the loss is defined as", "placed": "exact"},
              "source": {"file": "sections/method.tex", "line": 118, "end": 120, "version": "current"},
              "picture": "img/3f9a2c1b7d4e6a01.jpg", "before": None, "state": "open",
              "sent": {"request_id": "R1", "state": "sent", "at": "2026-11-10T13:59:02.000000Z"},
              "link": "?ws=ws1&page=P1&block=H1&thread=0d7c5e2a9f1b",
              "comments": [_comment("0d7c5e2a9f1b", "tim", "2026-11-10T13:58:10.000000Z", "lambda is never introduced."),
                           _comment("c2", "tim", "2026-11-10T14:06:31.000000Z",
                                    "Defined in 3.1 (sections/method.tex:112).", "Claude Code")]},
             {"id": "a81e44b07c3d", "file": "main.pdf", "format": "pdf", "page_id": "P1", "block_id": "A1",
              "kind": "area", "where": {"page": 5, "box": [0.1, 0.5, 0.9, 0.9], "placed": "estimate"},
              "source": {"file": "main.tex", "line": 18, "version": "commented"},
              "picture": "img/5b2e7a90c4d1f386.jpg", "before": "img/9c1d0e6f2a4b7358.jpg", "state": "open",
              "sent": None, "link": "?ws=ws1&page=P1&block=A1&thread=a81e44b07c3d",
              "comments": [_comment("a81e44b07c3d", "tim", "2026-11-10T13:59:00.000000Z",
                                    "move the legend inside the plot.")]},
             {"id": "f3e19b2c6d70", "file": "figures/fig1.pdf", "format": "pdf", "page_id": "P2", "block_id": "P2",
              "kind": "file", "where": {}, "source": None, "picture": None, "before": None, "state": "open",
              "sent": None, "link": "?ws=ws1&page=P2&block=P2&thread=f3e19b2c6d70",
              "comments": [_comment("f3e19b2c6d70", "tim", "2026-11-10T13:57:00.000000Z", "use the viridis palette.")]}]}

EXPECTED = """# Feedback on paper

3 open comments · owner tim · updated 2026-11-10 13:58

Each item is a comment thread on a file of this work folder. Answer it in Gamma: reply with what you
changed and where, resolve it when it is done (see "How to answer" at the end).

## main.pdf

### 1. Page 3 · “the loss is defined as”

- id: `0d7c5e2a9f1b` · passage · placed exactly
- source: `sections/method.tex`, lines 118–120
- sent: waiting since 2026-11-10 13:59

![Page 3, the comment's region](img/3f9a2c1b7d4e6a01.jpg)

> **tim** · 2026-11-10 13:58
> lambda is never introduced.

> **Claude Code** (tim's connection) · 2026-11-10 14:06
> Defined in 3.1 (sections/method.tex:112).

### 2. Page 5 · area

- id: `a81e44b07c3d` · area · placed by estimate · the figure was rebuilt
- source: `main.tex`, line 18 (as commented)

![Page 5, the comment's region now](img/5b2e7a90c4d1f386.jpg)
![Page 5, the region as it was commented on](img/9c1d0e6f2a4b7358.jpg)

> **tim** · 2026-11-10 13:59
> move the legend inside the plot.

## figures/fig1.pdf

### 3. The whole file

- id: `f3e19b2c6d70` · file

> **tim** · 2026-11-10 13:57
> use the viridis palette.

---

## How to answer

- Reply: `POST /api/pages/{page_id}/comments` with `{"block_id", "thread_id", "body"}`, or `reply_feedback`.
- Resolve: `POST /api/comments/{id}/resolve` with `{"resolved": true}`, or `resolve_feedback`.
- The JSON form with every id is `open.json` beside this file.
"""


def test_the_markdown_of_a_fixed_document():
    assert markdown.render(FIXED) == EXPECTED
    assert markdown.render(json.loads(json.dumps(FIXED))) == EXPECTED  # what open.json reads back gives the same


def test_the_markdown_with_nothing_open():
    doc = {**FIXED, "entries": [], "count": 0, "updated_at": None}
    assert markdown.render(doc) == ("# Feedback on paper\n\nNo open comments.\n\n---\n\n## How to answer\n\n"
                                    + EXPECTED.split("## How to answer\n\n")[1])
    assert markdown.render({**doc, "state": "resolved"}).split("\n")[2] == "No resolved comments."


def test_the_markdowns_picture_modes():
    tool = markdown.render(FIXED, pictures="tool")
    assert "![" not in tool and 'Picture: read_feedback(thread_id="0d7c5e2a9f1b")' in tool
    assert tool.count("Picture: read_feedback") == 2  # the whole file has none
    assert tool.endswith("- `read_feedback(thread_id)` reads one item with its pictures and its JSON form.\n")
    urls = markdown.render({**FIXED, "entries": [entries.with_urls(e) for e in FIXED["entries"]]}, pictures="urls")
    assert ("![Page 3, the comment's region](/api/feedback/threads/0d7c5e2a9f1b/picture?at=now&v=3f9a2c1b7d4e6a01)"
            in urls)
    assert "/api/feedback/threads/a81e44b07c3d/picture?at=before&v=9c1d0e6f2a4b7358" in urls
    assert urls.endswith("- The JSON form with every id is `GET /api/feedback?scope=W&state=open`.\n")
    with pytest.raises(ValueError):
        markdown.render(FIXED, pictures="inline")


def test_the_markdowns_headings_and_facts():
    def item(kind, where, **more):
        return {"id": "x", "kind": kind, "where": where, "comments": [], **more}

    assert markdown.heading(item("ink", {"page": 2})) == "Page 2 · handwriting"
    assert markdown.heading(item("passage", {"page": 3, "quote": "q", "note": "n"})) == "Page 3 · note on “q”"
    assert markdown.heading(item("area", {"page": 5, "note": "n"})) == "Page 5 · note on an area"
    assert markdown.heading(item("note", {"note": "n"})) == "A note"
    long = markdown.heading(item("passage", {"page": 1, "quote": "word " * 40}))
    assert len(long) == len("Page 1 · “”") + 80 and long.endswith("…”")
    text = markdown.render_entry(item("passage", {"page": 1, "quote": "q", "placed": "rough", "match": 0.82,
                                                  "note": "a\nnote"},
                                      sent={"request_id": "R", "state": "failed", "at": "2026-01-02T03:04:05Z",
                                            "detail": "no build"}, state="resolved",
                                      comments=[{"by": "", "agent": "", "at": "", "body": "", "deleted": True},
                                                {"by": "tim", "agent": "", "at": "", "body": "two\n\nlines"}]))
    assert "- id: `x` · passage · placed roughly (0.82) · resolved\n- note: “a note”\n" in text
    assert "- sent: failed: no build (sent 2026-01-02 03:04)" in text
    assert "> **someone** · \n> *(deleted)*\n\n> **tim** · \n> two\n>\n> lines\n" in text
    # a picture's regions and the whole picture
    region = {"box": [0.095, 0.095, 0.305, 0.305], "rect": [40, 60, 360, 240], "size": [1200, 800], "placed": "exact"}
    assert markdown.heading(item("region", region)) == "Region · 320×180 px at (40, 60)"
    assert markdown.heading(item("region", {**region, "note": "n"})) == "Note on a region · 320×180 px at (40, 60)"
    assert markdown.heading(item("region", {"placed": "lost"})) == "Region"
    text = markdown.render_entry(item("region", {**region, "placed": "earlier"}, picture="img/a.jpg", before="img/b.jpg"),
                                 pictures="files")
    assert "- id: `x` · region · drawn on an earlier version of the picture\n" in text
    assert "![The comment's region now](img/a.jpg)\n![The region as it was commented on](img/b.jpg)\n" in text
    assert "the file is no longer a picture" in markdown.render_entry(item("region", {"placed": "lost"}))
    text = markdown.render_entry(item("file", {}, picture="img/a.jpg", before="img/b.jpg"), pictures="files")
    assert "- id: `x` · file · the picture was rebuilt\n" in text
    assert "![The picture now](img/a.jpg)\n![The picture as it was commented on](img/b.jpg)\n" in text
    assert "![The picture](img/a.jpg)\n\n" in markdown.render_entry(item("file", {}, picture="img/a.jpg"), pictures="files")

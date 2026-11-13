"""The feedback routes' reading side (routers/feedback.py, docs/dev/feedback.md
"The routes"): a work folder's feedback as JSON with its pictures' routes
and as Markdown, one thread's entry, a region's picture as a JPEG (404
without one); who reads — any member and any token of the workspace, never
a share visitor — and the refusals of a scope outside a work folder."""

from types import SimpleNamespace

import pytest

from conftest import (account_of, fresh_client, login, make_folder, make_page, make_user, mark_work_folder, thread_on,
                      token_for, work_props, workspace_of)
from gamma import storage, workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db
from gamma.documents.model import document

OWNER, VIEWER, OUTSIDER = "fbr_own", "fbr_vie", "fbr_out"
FRAME = {"width": 600, "height": 800}


def _pdf(pages: int) -> bytes:
    import pymupdf

    doc = pymupdf.open()
    for n in range(pages):
        doc.new_page(width=600, height=800).insert_text((72, 100), f"The loss is defined as follows, page {n + 1}.",
                                                       fontsize=14)
    return doc.tobytes()


def _bearer(token: str):
    c = fresh_client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c


@pytest.fixture(scope="module")
def lab():
    """A shared workspace owned by fbr_own, fbr_vie a viewer there: a work
    folder ``fbr-paper`` with ``main.pdf`` (a stored two-page PDF) and a
    passage on its first page, a thread on the passage and one on the
    file; a library page beside it with a thread."""
    for name in (OWNER, VIEWER, OUTSIDER):
        make_user(name, f"pw-{name}")
    owner, viewer = login(OWNER, f"pw-{OWNER}"), login(VIEWER, f"pw-{VIEWER}")
    ws = workspaces.create("Feedback lab", account_of(OWNER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(VIEWER), "viewer", by=account_of(OWNER))
    for c in (owner, viewer):
        c.headers["X-Gamma-Workspace"] = ws
    W, L = make_folder(owner, "fbr-paper"), make_folder(owner, "fbr-library")
    mark_work_folder(owner, W)
    pdf, _ = storage.store_pdf(ws, _pdf(2))
    with connect_pages_db(ws) as conn:
        main = create_page(conn, "main", work_props(W, "main.pdf", doc=document(pdf, name="main.pdf")),
                           actor=account_of(OWNER), client=DISK_CLIENT)["id"]
    pos = {"pageNumber": 1, **FRAME, "boundingRect": {"x1": 72, "y1": 86, "x2": 400, "y2": 104},
           "rects": [{"x1": 72, "y1": 86, "x2": 400, "y2": 104}]}
    r = owner.post(f"/api/pages/{main}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "fbrH", "parent": main, "content": "",
         "props": {"pdf_position": pos, "quote": "The loss is defined as follows"}}]})
    assert r.status_code == 200, r.text
    library = make_page(owner, "a library paper", properties={"folders": [L]})["id"]
    t = {"H": thread_on(owner, main, "fbrH", "lambda is never introduced")["id"],
         "file": thread_on(owner, main, main, "use the viridis palette")["id"],
         "library": thread_on(owner, library, library, "not feedback")["id"]}
    return SimpleNamespace(ws=ws, owner=owner, viewer=viewer, W=W, L=L, main=main, library=library, t=t)


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r


def test_a_work_folders_feedback_as_json(lab):
    doc = _ok(lab.owner.get("/api/feedback", params={"scope": lab.W})).json()
    assert (doc["owner"], doc["scope"], doc["count"]) == (OWNER, {"kind": "work_folder", "id": lab.W,
                                                                  "name": "fbr-paper"}, 2)
    file, passage = doc["entries"]
    assert (file["id"], passage["id"]) == (lab.t["file"], lab.t["H"])
    assert passage["where"] == {"page": 1, "quote": "The loss is defined as follows", "placed": "exact"}
    key = passage["picture"].removeprefix("img/").removesuffix(".jpg")
    assert passage["picture_url"] == f"/api/feedback/threads/{lab.t['H']}/picture?at=now&v={key}"
    assert passage["before_url"] is None and file["picture_url"] is None
    assert list(passage).index("picture_url") == list(passage).index("before") + 1
    assert lab.owner.get("/api/feedback", params={"scope": lab.W, "state": "resolved"}).json()["entries"] == []


def test_the_markdown(lab):
    r = _ok(lab.owner.get("/api/feedback", params={"scope": lab.W, "format": "md"}))
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    text = r.text
    assert text.startswith(f"# Feedback on fbr-paper\n\n2 open comments · owner {OWNER} · updated ")
    assert "## main.pdf\n\n### 1. The whole file\n" in text and "### 2. Page 1 · “The loss is defined as follows”" in text
    assert f"](/api/feedback/threads/{lab.t['H']}/picture?at=now&v=" in text
    one = _ok(lab.owner.get(f"/api/feedback/threads/{lab.t['H']}", params={"format": "md"})).text
    assert one.startswith("## main.pdf\n\n### Page 1 · “The loss is defined as follows”\n")


def test_one_thread(lab):
    entry = _ok(lab.owner.get(f"/api/feedback/threads/{lab.t['H']}")).json()["entry"]
    assert entry["id"] == lab.t["H"] and entry["kind"] == "passage" and entry["picture_url"]
    for missing in (lab.t["library"], "noSuchThread"):
        assert lab.owner.get(f"/api/feedback/threads/{missing}").status_code == 404
    assert _ok(lab.owner.post(f"/api/comments/{lab.t['file']}/resolve", json={"resolved": True})).json()
    try:
        assert lab.owner.get(f"/api/feedback/threads/{lab.t['file']}").json()["entry"]["state"] == "resolved"
    finally:
        _ok(lab.owner.post(f"/api/comments/{lab.t['file']}/resolve", json={"resolved": False}))


def test_the_picture_of_a_region(lab):
    entry = lab.owner.get(f"/api/feedback/threads/{lab.t['H']}").json()["entry"]
    r = _ok(lab.owner.get(entry["picture_url"]))
    assert r.headers["content-type"] == "image/jpeg" and r.content[:3] == b"\xff\xd8\xff"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert _ok(lab.owner.get(entry["picture_url"])).content == r.content  # the cache's
    assert lab.owner.get(f"/api/feedback/threads/{lab.t['H']}/picture", params={"at": "before"}).status_code == 404
    assert lab.owner.get(f"/api/feedback/threads/{lab.t['file']}/picture").status_code == 404
    assert lab.owner.get(f"/api/feedback/threads/{lab.t['H']}/picture", params={"at": "later"}).status_code == 400


def test_who_reads(lab, anon):
    assert _ok(lab.viewer.get("/api/feedback", params={"scope": lab.W})).json()["owner"] == VIEWER
    reader = _bearer(token_for(lab.owner, "read"))
    assert _ok(reader.get("/api/feedback", params={"scope": lab.W})).json()["count"] == 2
    assert _ok(reader.get(f"/api/feedback/threads/{lab.t['H']}"))
    assert reader.get("/api/feedback", params={"scope": lab.W, "ws": workspace_of(OWNER)}).status_code == 403
    outsider = login(OUTSIDER, f"pw-{OUTSIDER}")
    outsider.headers["X-Gamma-Workspace"] = lab.ws
    assert outsider.get("/api/feedback", params={"scope": lab.W}).status_code == 403
    share = _ok(lab.owner.post(f"/api/share/folder/{lab.W}", json={"audience": "anyone", "role": "view"})).json()["token"]
    for visitor in (anon, login(OUTSIDER, f"pw-{OUTSIDER}")):
        for path in ("/api/feedback", f"/api/feedback/threads/{lab.t['H']}", "/api/feedback/requests"):
            r = visitor.get(path, params={"share": share, "scope": lab.W})
            assert r.status_code == 403 and "members" in r.text, (path, r.text)


def test_the_refusals(lab):
    for params, status in (({"scope": lab.L}, 400), ({"scope": lab.library}, 400), ({"scope": "noSuchThing"}, 404),
                           ({}, 404), ({"scope": lab.W, "state": "later"}, 400),
                           ({"scope": lab.W, "format": "html"}, 400)):
        assert lab.owner.get("/api/feedback", params=params).status_code == status, params

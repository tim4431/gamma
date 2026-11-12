"""Step 2's gate (docs/research/file-explorer.md section 7, docs/dev/feedback.md):
the loop end to end on the paper (samples/synctex/paper) in a work folder
of this computer. The person comments on a passage and on the figure; the
files below ``.gamma/feedback/`` say where each is in the sources, with a
picture; a Send; a feedback token takes the request, rebuilds (v2 over the
build, which the round takes and the kept version places the highlights
on), answers each thread, resolves them and marks the request done; the
person's Inbox names the agent, the token's writes made no one follow the
page, ``open.json`` is empty, the request file says done; a reopen sends a
thread back without its Sent chip; the removal takes the files."""

import json
import os
from pathlib import Path

import pytest

from conftest import account_of, fresh_client, login, make_user, thread_on, token_for
from gamma import folder_links, gamma_sync, jobs, pdf_meta, storage, vault_store, vault_sync
from gamma.blocks_store import new_block_id
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.feedback import files, kept
from gamma.ops import commit_ops
from test_highlight_relocate import H, W, _position
from test_vault_routes import _page_of, _put, _sync, _vault
from test_vault_sync import insert

OWNER = "fbl_owner"
PAPER = Path(__file__).parent / "samples" / "synctex" / "paper"
PLACES = json.loads((PAPER / "places.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it (test_feedback_files' fixture)."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(folder_links, "due", lambda link: "")
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    with files._guard:
        files._pages.clear()
        files._folders.clear()
    yield tmp_path / "disk"
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    below = str(tmp_path).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE substr(path, 1, ?) = ?", (len(below), below))
    with folder_links._dirty_guard:
        folder_links._dirty.clear()


def _paper(root, version: str) -> None:
    for rel in ("main.pdf", "main.synctex.gz", "main.tex", "sections/method.tex", "figures/fig1.pdf"):
        _put(root / rel, (PAPER / version / rel).read_bytes())


def _rects(fractions) -> list:
    return [{"x1": x0 * W, "y1": y0 * H, "x2": x1 * W, "y2": y1 * H} for x0, y0, x1, y1 in fractions]


def _bearer(token: str):
    c = fresh_client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _pass():
    """The hot loop's feedback pass (``folder_links._feedback_pass`` for
    each work folder due), once the kept version's worker is done."""
    assert kept.wait()
    links = {link["id"]: link for link in folder_links.all_links() if folder_links.engine(link) and not link["remote_url"]}
    for ws, w in files.due(0):
        folder_links._feedback_pass(ws, w, links, files)


def _open(root) -> dict:
    return json.loads((root / files.DIR / "open.json").read_text(encoding="utf-8"))


def _follows(ws, page) -> list:
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT user_id, level FROM page_follows WHERE page_id = ? ORDER BY user_id",
                            (page,)).fetchall()


def _position_of(owner, block_id) -> dict:
    r = owner.get(f"/api/blocks/{block_id}")
    assert r.status_code == 200, r.text
    return r.json()["properties"]["pdf_position"]


def test_the_loop(owner, disk):
    root = disk / "paper"
    _paper(root, "v1")
    link = _vault(owner, root)
    ws, w, user = link["workspace_id"], link["folder_id"], account_of(OWNER)
    page, here = _page_of(link, "main.pdf"), root / files.DIR
    assert page not in {b["id"] for b in owner.get("/api/blocks/root/children").json()["children"]}

    # the person comments on the method's passage and on the figure
    v1 = PLACES["v1"]
    passage, area = new_block_id(), new_block_id()
    commit_ops(ws, page, [
        insert(page, "", {"pdf_position": _position(1, _rects(v1["passage"]["rects"])), "quote": v1["passage"]["quote"]},
               passage),
        insert(page, "", {"pdf_position": _position(1, _rects([v1["figure"]["rect"]]), area=True), "color": "#ffe28fa6"},
               area)], actor=user)
    tp = thread_on(owner, page, passage, "lambda is never introduced")
    ta = thread_on(owner, page, area, "move the legend inside the plot")
    _pass()
    doc = _open(root)
    by = {e["id"]: e for e in doc["entries"]}
    assert [e["file"] for e in doc["entries"]] == ["main.pdf", "main.pdf"] and doc["owner"] == OWNER
    p, a = by[tp["id"]], by[ta["id"]]
    assert (p["kind"], p["where"]["page"], p["where"]["quote"], p["where"]["placed"]) == (
        "passage", 1, v1["passage"]["quote"], "exact")
    assert p["source"] == {"file": "sections/method.tex", "line": 3, "end": 5, "version": "current"}
    assert (a["kind"], a["where"]["page"], a["where"]["placed"]) == ("area", 1, "exact")
    assert a["source"] == {"file": "main.tex", "line": 22, "version": "current"}
    for entry in (p, a):
        assert entry["picture"] and (here / entry["picture"]).is_file() and entry["before"] is None
    text = (here / "open.md").read_text(encoding="utf-8")
    assert "## main.pdf" in text and "- source: `sections/method.tex`, lines 3–5" in text
    assert (here / ".gitignore").read_bytes() == b"*\n"

    # a Send
    r = owner.post("/api/feedback/requests", json={"scope": w, "note": "fix these"})
    assert r.status_code == 201, r.text
    request = r.json()["request"]
    assert {t["id"] for t in request["threads"]} == {tp["id"], ta["id"]}
    file = here / "requests" / f"{request['id']}.json"
    sent = json.loads(file.read_text(encoding="utf-8"))
    assert sent["state"] == "sent" and sent["note"] == "fix these" and len(sent["entries"]) == 2
    chips = {t["id"]: t["sent"] for t in owner.get(f"/api/pages/{page}/comments").json()["threads"]}
    assert chips[tp["id"]]["state"] == "sent" and chips[ta["id"]]["request_id"] == request["id"]

    # the agent: a script holding a feedback token takes the request and rebuilds
    agent = _bearer(token_for(owner, "feedback", "Claude Code"))
    polled = agent.get("/api/feedback/requests", params={"after": 0}).json()
    assert [q["id"] for q in polled["requests"]] == [request["id"]] and polled["cursor"] == request["rev"]
    r = agent.post(f"/api/feedback/requests/{request['id']}/state", json={"state": "running"})
    assert r.status_code == 200, r.text
    follows = _follows(ws, page)
    assert follows
    before = _position_of(owner, passage)
    _paper(root, "v2")
    assert _sync(owner, link["id"])["last_error"] == ""
    v2_hash = storage.content_digest((PAPER / "v2" / "main.pdf").read_bytes())
    job = jobs.latest(user, "relocate-highlights", f"{page}:{v2_hash}")
    assert job is not None and jobs.wait(job["id"])["state"] == "done"
    after = _position_of(owner, passage)
    assert after["rects"][0]["y1"] > before["rects"][0]["y1"] + 20 and not {"approx", "doc_id", "lost"} & set(after)
    assert not {"doc_id", "lost"} & set(_position_of(owner, area))
    v2 = PLACES["v2"]
    seen = {e["id"]: e for e in agent.get("/api/feedback", params={"scope": w}).json()["entries"]}
    assert seen[tp["id"]]["source"] == {"file": "sections/method.tex", "line": 6, "end": 8, "version": "current"}
    assert seen[tp["id"]]["where"]["placed"] == "exact" and seen[tp["id"]]["picture_url"]
    assert (seen[ta["id"]]["source"]["file"], seen[ta["id"]]["source"]["line"]) == ("main.tex", v2["figure"]["line"])
    # what the token may not do
    assert agent.post(f"/api/pages/{page}/comments", json={"block_id": page, "body": "a new thread"}).status_code == 403
    assert agent.put(f"/api/blocks/{passage}", json={"properties": {"color": "#000000"}}).status_code == 403
    assert agent.patch(f"/api/comments/{tp['id']}", json={"body": "edited"}).status_code == 403
    assert agent.post(f"/api/comments/{tp['id']}/resolve", json={"resolved": False}).status_code == 403
    # it answers each thread, resolves them, and the request is done
    for thread, body in ((tp, "Defined in 3.1 (sections/method.tex:6)."), (ta, "Legend moved inside the axes (main.tex:22).")):
        r = agent.post(f"/api/pages/{page}/comments", json={"block_id": thread["block_id"], "thread_id": thread["id"],
                                                             "body": body})
        assert r.status_code == 201, r.text
        assert r.json()["thread"]["comments"][-1]["agent"] == "Claude Code"
        r = agent.post(f"/api/comments/{thread['id']}/resolve", json={"resolved": True})
        assert r.status_code == 200, r.text
    r = agent.post(f"/api/feedback/requests/{request['id']}/state", json={"state": "done"})
    assert r.status_code == 200, r.text

    # the person sees it: the Inbox names the agent, and the token's writes made no one follow the page
    items = owner.get("/api/inbox", params={"filter": "all", "limit": 100}).json()["items"]
    mine = sorted((i["kind"], i["actor"], i["agent"]) for i in items if i["page_id"] == page)
    assert mine == sorted([("reply", OWNER, "Claude Code")] * 2 + [("resolved", OWNER, "Claude Code")] * 2)
    assert _follows(ws, page) == follows
    _pass()
    doc = _open(root)
    assert doc["count"] == 0 and doc["entries"] == []
    done = json.loads(file.read_text(encoding="utf-8"))
    assert (done["state"], done["state_by"]) == ("done", "Claude Code")
    assert {e["state"] for e in done["entries"]} == {"resolved"}
    assert {c["agent"] for e in done["entries"] for c in e["comments"]} == {"", "Claude Code"}

    # a reopen sends the thread back, its request answered: no Sent chip
    r = owner.post(f"/api/comments/{tp['id']}/resolve", json={"resolved": False})
    assert r.status_code == 200, r.text
    _pass()
    [entry] = _open(root)["entries"]
    assert entry["id"] == tp["id"] and entry["sent"] is None and entry["state"] == "open"

    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not here.exists()

"""The feedback files below a work folder's ``.gamma/feedback/``
(gamma/feedback/files.py, docs/dev/feedback.md "The files"): written by
every whole round and within one hot pass of a comment, byte for byte the
same on a quiet pass, rewritten on a reply, a moved place and a resolve,
emptied by the last resolve, gone with the vault and when it stops being a
work folder; nothing written where the directory is not the vault's, below
a junction, or while the vault is paused or choosing its files; Gamma's
stale temporary files swept; a file a reader holds tried again and left for
the next pass; a request's file written at once and rewritten by an answer,
its pictures kept while the rest are pruned, and old done requests' files
gone past the newest."""

import json
import os
import shutil
import sys
import time

import pymupdf
import pytest

from conftest import account_of, fresh_client, login, make_page, make_user, thread_on, token_for
from gamma import folder_links, gamma_sync, pdf_meta, storage
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.blocks_store import new_block_id
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.feedback import files, kept
from gamma.ops import commit_ops
from test_highlight_relocate import _position
from test_vault_routes import _create, _page_of, _put, _sync, _vault
from test_vault_sync import insert

OWNER = "fbf_owner"
WINDOWS = sys.platform == "win32"
AREA = {"x1": 66, "y1": 60, "x2": 260, "y2": 110}      # over the line of text
MOVED = {"x1": 66, "y1": 300, "x2": 260, "y2": 350}    # where a rebuild put it
JPEG = b"\xff\xd8\xff"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it, a place on "this computer",
    files settled at once, and no round or pass but the test's own: none in
    the background, none from the app's own tick (``due``) or hot loop
    (``HOT_S``: it runs beside the tests and must find nothing due), the
    feedback pass run by hand (``_pass``). The links and the marks a test
    leaves go with it."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(folder_links, "due", lambda link: "")
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    _clear_marks()
    yield tmp_path / "disk"
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    below = str(tmp_path).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE substr(path, 1, ?) = ?", (len(below), below))
    with folder_links._dirty_guard:
        folder_links._dirty.clear()
    _clear_marks()


def _clear_marks():
    with files._guard:
        files._pages.clear()
        files._folders.clear()


def _pass():
    """What the hot loop's tick does for the feedback files
    (``folder_links._feedback_pass`` for each work folder due, with no
    wait), once the kept version's worker is done (it stores a commented
    page's bytes, which the pictures read)."""
    assert kept.wait()
    links = {link["id"]: link for link in folder_links.all_links() if folder_links.engine(link) and not link["remote_url"]}
    for ws, w in files.due(0):
        folder_links._feedback_pass(ws, w, links, files)


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _paper(owner, root, **body) -> dict:
    """A work folder of ``root`` holding ``main.pdf``, after its first
    round, which pushes and never pulls."""
    return _vault(owner, root, {"main.pdf": _pdf("the method defines the loss")}, pull=False, **body)


def _area(ws, page, rect, block_id=None) -> str:
    """An area highlight over ``rect`` of the page's first PDF page."""
    block_id = block_id or new_block_id()
    commit_ops(ws, page, [insert(page, "", {"pdf_position": _position(1, rect, area=True), "color": "#ffe28fa6"},
                                 block_id)], actor=account_of(OWNER))
    return block_id


def _here(root):
    return root / files.DIR


def _open(root) -> dict:
    return json.loads((_here(root) / "open.json").read_text(encoding="utf-8"))


def _footprint(root) -> dict:
    """Every file below ``.gamma/feedback/`` with its time and bytes."""
    out = {}
    for top, _dirs, names in os.walk(_here(root)):
        for name in names:
            path = os.path.join(top, name)
            out[os.path.relpath(path, root)] = (os.stat(path).st_mtime_ns, open(path, "rb").read())
    return out


def _leftovers(root) -> list:
    return sorted(p.name for p in _here(root).rglob(f"{vault_disk.TEMP}*"))


def _link_dir(target, link):
    """``link`` standing for the directory ``target``: a junction on
    Windows, a symlink elsewhere (skipped where the account may make none)."""
    if WINDOWS:
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
        return
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError:
        pytest.skip("this account may not make symlinks")


def _bearer(token: str):
    c = fresh_client()
    c.headers["Authorization"] = f"Bearer {token}"
    return c


def _resolve(owner, thread_id: str, resolved: bool = True) -> None:
    r = owner.post(f"/api/comments/{thread_id}/resolve", json={"resolved": resolved})
    assert r.status_code == 200, r.text


def test_the_files_are_written_by_every_round_within_a_pass_of_a_comment_and_left_alone_while_nothing_changes(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    assert folder_links.get_link(link["id"])["status"]["feedback"] == {"open": 0}
    assert (here / ".gitignore").read_bytes() == b"*\n" and _open(root)["entries"] == []
    assert "No open comments." in (here / "open.md").read_text(encoding="utf-8")

    area = _area(ws, page, AREA)
    thread = thread_on(owner, page, area, "move the legend inside the plot")
    assert files.due(0) == [(ws, w)]
    _pass()
    assert files.due(0) == []
    doc = _open(root)
    assert (doc["owner"], doc["work_folder"]["id"], doc["count"]) == (OWNER, w, 1)
    [entry] = doc["entries"]
    assert (entry["id"], entry["file"], entry["kind"], entry["where"]["page"], entry["where"]["placed"]) == (
        thread["id"], "main.pdf", "area", 1, "exact")
    assert entry["picture"].startswith("img/") and (here / entry["picture"]).read_bytes()[:3] == JPEG
    assert [(c["by"], c["body"]) for c in entry["comments"]] == [(OWNER, "move the legend inside the plot")]
    text = (here / "open.md").read_text(encoding="utf-8")
    assert "## main.pdf" in text and f"![Page 1, the comment's region]({entry['picture']})" in text
    assert "> move the legend inside the plot" in text

    # nothing marked, then a whole round: every byte and every time stay as they are
    before = _footprint(root)
    _pass()
    assert _sync(owner, link["id"])["feedback"] == {"open": 1}
    assert _footprint(root) == before and _leftovers(root) == []


def test_a_reply_a_moved_place_and_a_resolve_rewrite_the_files_and_the_last_resolve_empties_them(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    area = _area(ws, page, AREA)
    thread = thread_on(owner, page, area, "move the legend")
    _pass()
    first = _open(root)["entries"][0]["picture"]

    r = owner.post(f"/api/pages/{page}/comments", json={"block_id": area, "thread_id": thread["id"], "body": "done in 3.1"})
    assert r.status_code == 201, r.text
    _pass()
    [entry] = _open(root)["entries"]
    assert [c["body"] for c in entry["comments"]] == ["move the legend", "done in 3.1"]
    assert "> done in 3.1" in (here / "open.md").read_text(encoding="utf-8")

    # the place moved (what a relocation does): the region is drawn again; the old picture stays one
    # pass for the file it replaced, then goes
    commit_ops(ws, page, [{"op": "set", "id": area, "props": {"pdf_position": _position(1, MOVED, area=True)}}],
               actor=account_of(OWNER))
    _pass()
    [entry] = _open(root)["entries"]
    assert entry["picture"] != first and (here / entry["picture"]).is_file() and (here / first).is_file()
    files.mark_folder(ws, w)
    _pass()
    assert not (here / first).exists() and (here / entry["picture"]).is_file()

    _resolve(owner, thread["id"])
    _pass()
    doc = _open(root)
    assert doc["count"] == 0 and doc["entries"] == [] and doc["updated_at"] is None
    assert "No open comments." in (here / "open.md").read_text(encoding="utf-8")
    files.mark_folder(ws, w)
    _pass()
    assert list((here / "img").iterdir()) == []

    _resolve(owner, thread["id"], False)
    _pass()
    assert [e["id"] for e in _open(root)["entries"]] == [thread["id"]] and (here / entry["picture"]).is_file()


def test_the_files_go_with_the_vault_and_when_it_stops_being_a_work_folder(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    assert (_here(root) / "open.json").is_file()
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not _here(root).exists() and (root / vault_disk.MARKER_DIR).is_dir() and not (root / vault_disk.MARKER).exists()

    # a work folder whose push came down, its files in the library: let go at its next round, its files with it
    other = disk / "library"
    link = _paper(owner, other, library=True)
    assert (_here(other) / "open.json").is_file()
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": "pdfs"})
    assert r.status_code == 200, r.text
    status = _sync(owner, link["id"])
    assert "feedback" not in status and not _here(other).exists() and (other / vault_disk.MARKER).is_file()


def test_removing_the_files_never_enters_a_link_or_a_junction_below_them(tmp_path):
    root = tmp_path / "vault"
    here = root / files.DIR
    (here / "img").mkdir(parents=True)
    (here / "open.json").write_bytes(b"{}")
    (here / "img" / f"{'a' * 16}.jpg").write_bytes(JPEG)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "precious.txt").write_bytes(b"keep me")
    _link_dir(elsewhere, here / "pictures")
    files.remove(root)
    assert not here.exists() and (root / vault_disk.MARKER_DIR).is_dir()
    assert (elsewhere / "precious.txt").read_bytes() == b"keep me"


def test_a_picture_that_cannot_be_drawn_takes_no_slot_of_the_cap_and_holds_no_mark(owner, disk, monkeypatch):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, page = link["workspace_id"], _page_of(link, "main.pdf")
    monkeypatch.setattr(files, "PICTURES_PER_WRITE", 1)
    monkeypatch.setattr(files.pictures, "cached",
                        lambda ws, conn, page_id, region: None if region == "broken" else JPEG + region.encode())
    broken, fine, a, b = "0" * 16, "1" * 16, "2" * 16, "3" * 16
    with connect_pages_db(ws) as conn:
        # the broken one first in the order: the one behind it is drawn all the same, and nothing waits
        assert files._pictures(ws, conn, root, {broken: (page, "broken"), fine: (page, "fine")}) == ({fine}, 0)
        # one drawn is the cap: the next waits for the next pass, the broken one not
        assert files._pictures(ws, conn, root, {broken: (page, "broken"), a: (page, "a"), b: (page, "b")}) == ({fine, a}, 1)
    assert sorted(p.name for p in (_here(root) / "img").iterdir()) == [f"{fine}.jpg", f"{a}.jpg"]


def test_a_request_answered_before_a_pass_drew_its_pictures_gets_them_at_the_next(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    thread = thread_on(owner, page, _area(ws, page, AREA), "legend")
    r = owner.post("/api/feedback/requests", json={"scope": w, "threads": [thread["id"]]})
    assert r.status_code == 201, r.text
    request = r.json()["request"]
    agent = _bearer(token_for(owner, "feedback", "Claude Code"))
    r = agent.post(f"/api/feedback/requests/{request['id']}/state", json={"state": "done"})
    assert r.status_code == 200, r.text
    file = here / "requests" / f"{request['id']}.json"
    done = json.loads(file.read_text(encoding="utf-8"))
    assert done["state"] == "done" and [e["picture"] for e in done["entries"]] == [None]   # no pass drew it yet
    _pass()
    done = json.loads(file.read_text(encoding="utf-8"))
    [picture] = [e["picture"] for e in done["entries"]]
    assert done["state"] == "done" and picture and (file.parent / picture).is_file()
    before = _footprint(root)
    files.mark_folder(ws, w)
    _pass()
    assert _footprint(root) == before     # the file stands now: a pass reads it no more


def test_nothing_is_written_where_the_directory_is_not_the_vaults_below_a_junction_or_while_the_vault_rests(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    row, here = folder_links.get_link(link["id"]), _here(root)
    marker = (root / vault_disk.MARKER).read_bytes()
    before = _footprint(root)
    thread_on(owner, page, page, "the whole file")

    # the marker gone (a copy of the directory, an emptied one) or another vault's
    (root / vault_disk.MARKER).unlink()
    assert files.write(row) == {"open": 0, "error": "the directory has no vault marker: it is not the vault's any more"}
    (root / vault_disk.MARKER).write_bytes(json.dumps({"format": vault_disk.FORMAT, "id": "f" * 32, "notes_folder": ""}).encode())
    assert files.write(row) == {"open": 0, "error": "the directory is another vault's"}
    assert _footprint(root) == before
    (root / vault_disk.MARKER).write_bytes(marker)

    # below a junction: nothing is read or written through it
    elsewhere = disk / "elsewhere"
    elsewhere.mkdir()
    shutil.rmtree(here)
    _link_dir(elsewhere, here)
    written = files.write(row)
    assert written["open"] == 1 and "below a link" in written["error"]
    assert list(elsewhere.iterdir()) == []
    # rmdir takes a junction away; a symlink is a file to rmdir (ENOTDIR)
    os.rmdir(here) if WINDOWS else os.unlink(here)
    _pass()
    assert _open(root)["count"] == 1

    # paused: the mark is dropped, the files stay as they are
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True}).raise_for_status()
    thread_on(owner, page, page, "while paused")
    assert files.due(0) == [(ws, w)]
    _pass()
    assert files.due(0) == [] and _open(root)["count"] == 1
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False}).raise_for_status()

    # a work folder choosing its files: no files, the mark dropped
    chosen = disk / "choosing"
    _put(chosen / "a.md", "a\n")
    choosing = _create(owner, path=str(chosen), new_folder=True, push="everything", choose=True)
    files.mark_folder(ws, choosing["folder_id"])
    _pass()
    assert files.due(0) == [] and not _here(chosen).exists()

    # a page of no work folder marks nothing
    library = make_page(owner, "fbf library paper")["id"]
    thread_on(owner, library, library, "not feedback")
    assert files.due(0) == []


def test_gammas_stale_temporary_files_are_swept_and_a_write_going_on_stays(owner, disk):
    root = disk / "paper"
    link = _paper(owner, root)
    here = _here(root)
    then = time.time() - 3600
    old = [here / f"{vault_disk.TEMP}old.tmp", here / "img" / f"{vault_disk.TEMP}old.tmp",
           here / "requests" / f"{vault_disk.TEMP}old.tmp"]
    for path in old:
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(b"x")
        os.utime(path, (then, then))
    young = here / f"{vault_disk.TEMP}young.tmp"
    young.write_bytes(b"y")
    theirs = here / "mine.txt"
    theirs.write_bytes(b"z")
    os.utime(theirs, (then, then))
    assert _sync(owner, link["id"])["feedback"] == {"open": 0}
    assert not any(path.exists() for path in old) and young.exists() and theirs.exists()


def test_a_mark_resolved_beside_another_caller_is_found_by_both(owner, disk, monkeypatch):
    """The hot loop's tick runs ``due`` beside a caller of its own (the
    tests' pass, here): a mark taken by one is still there for the other
    until it is resolved, and a page marked again during the resolve keeps
    its newer mark."""
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    real, seen, inner = files.connect_pages_db, [], []

    def connect(workspace):
        if not seen:
            seen.append(True)
            inner.append(files.due(0))      # the tick, between this call's snapshot and its resolve
            files.mark(ws, page)            # and a change that came meanwhile
        return real(workspace)
    monkeypatch.setattr(files, "connect_pages_db", connect)
    files.mark(ws, page)
    assert files.due(0) == [(ws, w)] and inner == [[(ws, w)]]
    files.settle(ws, w, time.monotonic())
    assert files.due(0) == [(ws, w)]        # the mark made during the resolve stands
    files.settle(ws, w, time.monotonic())
    assert files.due(0) == []


def test_a_file_a_reader_holds_is_tried_again_then_left_for_the_next_pass(owner, disk, monkeypatch):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    monkeypatch.setattr(vault_disk, "REPLACE_RETRY_S", 0)
    real, refused = os.replace, []

    def held(src, dst):
        if os.path.basename(dst) == "open.json":
            refused.append(dst)
            raise PermissionError(13, "held by a reader")
        return real(src, dst)
    monkeypatch.setattr(os, "replace", held)
    thread_on(owner, page, page, "the whole file")
    _pass()
    assert len(refused) == vault_disk.REPLACE_ATTEMPTS and _open(root)["count"] == 0   # the old file stands
    assert files.due(0) == [(ws, w)] and _leftovers(root) == []                      # kept for the next pass
    assert "1 open comment" in (here / "open.md").read_text(encoding="utf-8")        # the rest was written
    assert _sync(owner, link["id"])["feedback"] == {"open": 1, "left": 1}            # no error of the vault's
    monkeypatch.setattr(os, "replace", real)
    _pass()
    assert _open(root)["count"] == 1 and files.due(0) == [] and _leftovers(root) == []


def test_a_requests_file_is_written_at_once_keeps_its_pictures_and_goes_once_done_past_the_newest(owner, disk,
                                                                                                    monkeypatch):
    root = disk / "paper"
    link = _paper(owner, root)
    ws, w, page = link["workspace_id"], link["folder_id"], _page_of(link, "main.pdf")
    here = _here(root)
    a, b = _area(ws, page, AREA), _area(ws, page, MOVED)
    ta, tb = thread_on(owner, page, a, "legend"), thread_on(owner, page, b, "axis")
    _pass()
    pictures = {e["id"]: e["picture"] for e in _open(root)["entries"]}
    assert len(set(pictures.values())) == 2 and all((here / p).is_file() for p in pictures.values())

    # a Send writes its file at once, its pictures named from requests/
    r = owner.post("/api/feedback/requests", json={"scope": w, "threads": [ta["id"]], "note": "fix the legend"})
    assert r.status_code == 201, r.text
    request = r.json()["request"]
    file = here / "requests" / f"{request['id']}.json"
    sent = json.loads(file.read_text(encoding="utf-8"))
    assert (sent["format"], sent["state"], sent["note"], sent["rev"]) == ("gamma-feedback-request-1", "sent",
                                                                         "fix the legend", request["rev"])
    assert sent["answer"]["state"] == f"POST /api/feedback/requests/{request['id']}/state {{state, detail}}"
    [entry] = sent["entries"]
    assert entry["id"] == ta["id"] and entry["picture"] == f"../{pictures[ta['id']]}"
    assert (file.parent / entry["picture"]).resolve() == (here / pictures[ta["id"]]).resolve()

    # an answer rewrites the request file: its entries as they are now
    agent = _bearer(token_for(owner, "feedback", "Claude Code"))
    r = agent.post(f"/api/pages/{page}/comments", json={"block_id": a, "thread_id": ta["id"], "body": "moved it"})
    assert r.status_code == 201, r.text
    _pass()
    sent = json.loads(file.read_text(encoding="utf-8"))
    assert [(c["agent"], c["body"]) for c in sent["entries"][0]["comments"]] == [("", "legend"), ("Claude Code", "moved it")]

    # both resolved: open.json names no picture; the request's stays, the other goes
    _resolve(owner, ta["id"])
    _resolve(owner, tb["id"])
    _pass()
    files.mark_folder(ws, w)
    _pass()
    assert (here / pictures[ta["id"]]).is_file() and not (here / pictures[tb["id"]]).exists()

    # a done request's file stays while it is among the newest, goes past them, and a live one never
    monkeypatch.setattr(files, "REQUEST_FILES", 1)
    r = agent.post(f"/api/feedback/requests/{request['id']}/state", json={"state": "done"})
    assert r.status_code == 200, r.text
    _pass()
    assert json.loads(file.read_text(encoding="utf-8"))["state"] == "done"
    _resolve(owner, tb["id"], False)
    second = owner.post("/api/feedback/requests", json={"scope": w, "threads": [tb["id"]]}).json()["request"]
    _pass()
    assert not file.exists() and (here / "requests" / f"{second['id']}.json").is_file()
    assert not (here / pictures[ta["id"]]).exists()     # nothing names it any more
    third = owner.post("/api/feedback/requests", json={"scope": w, "threads": [tb["id"]]}).json()["request"]
    _pass()
    assert (here / "requests" / f"{second['id']}.json").is_file() and (here / "requests" / f"{third['id']}.json").is_file()

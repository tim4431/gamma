"""Synced vaults through their routes (routers/folder_links.py, gamma/folder_links.py
wired to gamma/vault/sync.py): a vault made from a directory, what /preview
finds and refuses, the refusals of ``check_vault`` on making, changing and
each round, the conflicts the file won kept apart from a mirror's and
Zotero's, the hot loop and its marks, removing a vault both ways, the page's
own routes (its vault, its notes file read and written), a file inside a
vault opened through /open, the vaults of one file a directory vault takes
in, who may use each route and the shell's secret."""

import hashlib
import json
import os
import shutil
import stat
import sys
import threading
import time

import pymupdf
import pytest

from conftest import account_of, doc_props, fresh_client, login, make_folder, make_page, make_user, workspace_of
from gamma import (collab, config, folder_links, gamma_sync, inbox, pdf_meta, publish, storage, sync_engine, workspaces,
                   zotero_sync)
from gamma.vault import disk as vault_disk, kinds, store as vault_store, sync as vault_sync
from gamma.vault.local_remote import LocalRemote
from gamma.blocks_store import FOLDERS, block_kind, new_block_id
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.integrations import create_token
from gamma.ops import MAX_CONTENT, SYNC_CLIENT, commit_ops, delete_page, trash_page
from gamma.publisher_sessions import seal
from test_folder_links import over_http  # noqa: F401: the over_http fixture

OWNER = "vr_owner"
WINDOWS = sys.platform == "win32"
REAL_DUE = folder_links.due


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it, a place on "this computer",
    files settled at once and a missing file removed at its second scan, and
    no round but the test's own: none in the background, none from the app's
    tick (``due``) or its hot loop (``HOT_S``). The links a test leaves go
    with their rows."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(folder_links, "due", lambda link: False)
    monkeypatch.setattr(folder_links, "HOT_S", 3600)
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})
    monkeypatch.setattr(storage, "_offline", {})
    yield tmp_path / "disk"
    for link in folder_links.all_links():
        if folder_links.inside(link["path"], str(tmp_path)):
            folder_links._forget(link)
    below = str(tmp_path).rstrip("\\/") + os.sep
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE substr(path, 1, ?) = ?", (len(below), below))
    with folder_links._dirty_guard:
        folder_links._dirty.clear()


def _put(path, data, age: float = 10):
    """A file written ``age`` seconds ago: settled, and not racy."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _create(c, **body):
    r = c.post("/api/folder-links", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sync(c, link_id, **params):
    r = c.post(f"/api/folder-links/{link_id}/sync", params={"wait": 1, **params})
    assert r.status_code == 200, r.text
    return r.json()["status"]


def _vault(c, root, files=None, **body):
    """A vault made from the directory ``root`` holding ``files`` ({path:
    text or bytes}), after its first round: the link as the API answers it."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in (files or {}).items():
        _put(root / rel, data)
    link = _create(c, path=str(root), new_folder=True, **{"push": "everything", **body})
    status = _sync(c, link["id"])
    assert status["last_error"] == "", status
    return link


def _page_of(link, rel) -> str:
    return vault_store.files(link["id"])[rel]["page_id"]


def _made_in(c, title, folder) -> str:
    """A page made in Gamma filed in ``folder``, a directory of a work folder
    that pulls: the server stamps it as the work folder's note (a person may
    not file an existing page there)."""
    r = c.post("/api/blocks", json={"parent_id": "root", "content": title, "properties": {"folders": [folder]}})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _blocks(link, page_id) -> dict:
    return LocalRemote(link["workspace_id"], link["folder_id"], link["created_by"]).page(page_id).blocks


def _texts(link, page_id) -> list:
    blocks = _blocks(link, page_id)
    return sorted(b["content"] for i, b in blocks.items() if i != page_id
                  and block_kind(b["parent"], page_id, b["props"]) == "note")


def _find(link, page_id, content) -> str:
    return next(i for i, b in _blocks(link, page_id).items() if b["content"] == content and i != page_id)


def _edit(link, page_id, block_id, content):
    commit_ops(link["workspace_id"], page_id, [{"op": "set", "id": block_id, "content": content}],
               actor=link["created_by"])


def _state(c, page_id) -> dict:
    r = c.get(f"/api/folder-links/page/{page_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _props(link, page_id) -> dict:
    return _blocks(link, page_id)[page_id]["props"]


def _sha(path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


# --- a vault made from a directory -----------------------------------------------------------

def test_a_vault_made_from_a_directory_writes_only_its_marker_into_a_new_folder(owner, disk):
    root = disk / "Lab notes"
    _put(root / "Ideas.md", "# Ideas\n\na first idea\n")
    _put(root / "Sub" / "Deep.md", "deep text\n")
    _put(root / "picture.png", b"\x89PNG not really")
    before = sorted(p.relative_to(root).as_posix() for p in root.rglob("*"))

    r = owner.post("/api/folder-links/preview", json={"path": str(root)})
    assert r.status_code == 200, r.text
    seen = r.json()
    assert seen == {"path": str(root.resolve()), "notes": 2, "pdfs": 0, "pictures": 1, "untracked": 0, "empty": False,
                    "marker": None, "refusal": "", "pages": 0, "will_write": [".gamma/"],
                    "gammaignore": {"exists": False, "ours": False}}
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*")) == before      # nothing written
    (disk / "Empty").mkdir(parents=True)
    assert owner.post("/api/folder-links/preview", json={"path": str(disk / "Empty")}).json()["empty"] is True
    _put(disk / "Sources" / "main.tex", "\\documentclass{article}\n")   # the default reads no source
    sources = owner.post("/api/folder-links/preview", json={"path": str(disk / "Sources")}).json()
    assert (sources["untracked"], sources["empty"]) == (1, False)

    same_name = make_folder(owner, "Lab notes")       # a folder of the name: never the one a directory fills
    link = _create(owner, path=str(root), new_folder=True, push="everything")
    assert link["folder_id"] not in ("", same_name) and link["push"] == "everything" and link["pull"] is True
    assert "state" not in link and folder_links.engine(folder_links.get_link(link["id"]))
    with connect_pages_db(link["workspace_id"]) as conn:
        assert conn.execute("SELECT content FROM unified_blocks WHERE id = ? AND page_id = ?",
                            (link["folder_id"], FOLDERS)).fetchone() == ("Lab notes",)
    assert vault_disk.read_marker(root)["id"] == folder_links.get_link(link["id"])["state"]["vault"]
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if ".gamma" not in p.parts) == before

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["running"] is False and status["folder_path"] == ["Lab notes"]
    assert set(status["counts"]) >= {"added", "updated", "renamed", "removed", "kept", "unchanged", "sent", "waiting",
                                     "read", "conflicts"}
    assert status["untracked"] == 0 and status["kept"] == [] and status["started_at"]
    ideas = _page_of(link, "Ideas.md")
    assert _texts(link, ideas) == ["# Ideas", "a first idea"]
    assert _state(owner, ideas) == {"vault": {"id": link["id"], "name": "Lab notes", "scope": "folder",
                                              "path": str(root.resolve() / "Ideas.md"), "pull": True,
                                              "push": "everything", "problem": "", "conflicts": 0, "paused": False,
                                              "work_folder": True, "choosing": False, "library": False, "mine": True,
                                              "page": {"library": False, "file_name": "Ideas.md"}},
                                    "reader": None}
    assert owner.get(f"/api/folder-links/{link['id']}").json()["status"]["folder_path"] == ["Lab notes"]

    # a second directory vault of the same name gets a folder of its own too
    other = _vault(owner, disk / "elsewhere" / "Lab notes", {"x.md": "x\n"})
    assert other["folder_id"] not in (link["folder_id"], same_name)


def test_preview_and_making_refuse_what_a_vault_may_not_be(owner, disk):
    outer = _vault(owner, disk / "Outer", {"a.md": "a\n"})
    inner = disk / "Outer" / "inner"
    _put(inner / "b.md", "b\n")
    seen = owner.post("/api/folder-links/preview", json={"path": str(inner)}).json()
    assert "do not overlap" in seen["refusal"]
    for path in (inner, disk):                                       # inside, and around
        r = owner.post("/api/folder-links", json={"path": str(path), "new_folder": True, "push": "everything"})
        assert r.status_code == 400 and "do not overlap" in r.json()["detail"], path

    # a copied directory: its marker is a live link's
    copy = disk / "Copy"
    shutil.copytree(disk / "Outer", copy)
    seen = owner.post("/api/folder-links/preview", json={"path": str(copy)}).json()
    assert seen["marker"]["link_id"] == outer["id"] and "copied" in seen["refusal"] and seen["will_write"] == []
    r = owner.post("/api/folder-links", json={"path": str(copy), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and "copied" in r.json()["detail"]

    # a marker no link holds: adopted only when asked
    gone = disk / "Was a vault"
    _put(gone / "c.md", "c\n")
    marker = vault_disk.make_marker(gone)
    assert owner.post("/api/folder-links/preview", json={"path": str(gone)}).json()["marker"] == {"id": marker["id"],
                                                                                               "link_id": ""}
    r = owner.post("/api/folder-links", json={"path": str(gone), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and "was a synced vault before" in r.json()["detail"]
    adopted = _create(owner, path=str(gone), new_folder=True, push="everything", adopt=True)
    assert folder_links.get_link(adopted["id"])["state"]["vault"] == marker["id"]

    # a folder an earlier link kept on disk (made from a directory: a new folder is never that one), a gamma-sync of
    # another version, a missing directory, nothing to sync, the data directory
    old, other = disk / "Kept before", disk / "Other version"
    _put(old / gamma_sync.STATE, json.dumps({"format": gamma_sync.FORMAT, "server": "local", "folder": "f",
                                             "workspace": workspace_of(OWNER), "files": {}, "dirs": []}))
    _put(other / gamma_sync.STATE, "{}")
    seen = owner.post("/api/folder-links/preview", json={"path": str(old)}).json()
    assert "another folder's files" in seen["refusal"]
    for body, why in (({"path": str(old)}, "another folder's files"), ({"path": str(other)}, "another version"),
                      ({"path": str(disk / "nowhere")}, "There is no directory"),
                      ({"path": str(disk / "x"), "pull": False, "push": "off"}, "pulls, pushes, or both"),
                      ({"path": str(config.DATA_DIR / "x")}, "data directory")):
        r = owner.post("/api/folder-links", json={"new_folder": True, "push": "everything", **body})
        assert r.status_code == 400 and why in r.json()["detail"], body
    r = owner.post("/api/folder-links", json={"path": str(disk / "far"), "new_folder": True, "remote_url": "http://x"})
    assert r.status_code == 400 and "another server" in r.json()["detail"]

    # from a folder with pages into a directory with notes: pushing everything would mix them
    folder = make_folder(owner, "VR full folder")
    make_page(owner, "VR filed", {"folders": [folder]})
    _put(disk / "Full dir" / "mine.md", "mine\n")
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "Full dir"), "push": "everything"})
    assert r.status_code == 400 and "Both the folder and the directory" in r.json()["detail"]
    made = _create(owner, folder=folder, path=str(disk / "New dir"), push="everything")   # a new directory is fine
    assert folder_links.engine(folder_links.get_link(made["id"])) and (disk / "New dir" / vault_disk.MARKER).is_file()


def test_pushing_everything_is_refused_in_a_clone_and_a_zotero_workspace_and_pulling_is_not(disk):
    make_user("vr_clone", "pw")
    c, ws, user = login("vr_clone", "pw"), workspace_of("vr_clone"), account_of("vr_clone")
    for table, insert in (("mirrors", "INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, mode, "
                                      "created_at) VALUES (?, 'http://nas', 'r', ?, ?, 'off', '2026-01-01T00:00:00Z')"),
                          ("zotero_links", "INSERT INTO zotero_links (workspace_id, owner, library, mode, created_at) "
                                           "VALUES (?, ?, ?, 'off', '2026-01-01T00:00:00Z')")):
        with connect_users_db() as conn:
            conn.execute(insert, (ws, seal("gamma_x"), user) if table == "mirrors" else (ws, user, "users/vr"))
        try:
            root = disk / table
            _put(root / "n.md", "n\n")
            r = c.post("/api/folder-links", json={"path": str(root), "new_folder": True, "push": "everything"})
            assert r.status_code == 400 and "cannot push everything" in r.json()["detail"], table
            pulled = _create(c, path=str(root), new_folder=True, push="pdfs")
            r = c.patch(f"/api/folder-links/{pulled['id']}", json={"push": "everything"})
            assert r.status_code == 400 and "cannot push everything" in r.json()["detail"]
            c.delete(f"/api/folder-links/{pulled['id']}").raise_for_status()
        finally:
            with connect_users_db() as conn:
                conn.execute(f"DELETE FROM {table} WHERE workspace_id = ?", (ws,))


def test_a_clone_or_a_zotero_link_is_refused_a_workspace_a_vault_pushes_everything_into(owner, disk, monkeypatch):
    ws, user = workspace_of(OWNER), account_of(OWNER)
    _vault(owner, disk / "Pushing", {"p.md": "p\n"})
    assert folder_links.pushing_vault(ws) == str((disk / "Pushing").resolve()) and folder_links.pushing_vault("none") == ""
    monkeypatch.setattr(sync_engine, "_check_remote", lambda url, token, mode, fetch: (
        url, {"workspace": {"id": "r", "name": "R"}, "user": "u", "role": "owner"}, mode))
    with pytest.raises(ValueError, match="synced vault"):
        sync_engine.create_mirror(user, "http://nas", "gamma_x", workspace_id=ws)
    assert sync_engine.get_mirror(ws) is None
    monkeypatch.setattr(zotero_sync, "_library_entry", lambda client, library: {"write": True, "kind": "user"})
    monkeypatch.setattr(zotero_sync.zotero_account, "client_for", lambda owner_id: None)
    monkeypatch.setattr(zotero_sync.zotero_account, "status", lambda owner_id: {})
    with pytest.raises(ValueError, match="synced vault"):
        zotero_sync.create_link(user, "users/12345", workspace_id=ws)
    with connect_users_db() as conn:
        assert conn.execute("SELECT 1 FROM zotero_links WHERE workspace_id = ?", (ws,)).fetchone() is None


def test_vaults_that_push_do_not_share_a_folder_and_the_newer_of_two_that_came_to_stops(owner, disk):
    top = make_folder(owner, "VR top")
    below = make_folder(owner, "VR top/below")
    side = make_folder(owner, "VR side")
    first = _create(owner, folder=below, path=str(disk / "First"), push="pdfs")     # a folder put on disk, pushing PDFs
    r = owner.post("/api/folder-links", json={"folder": top, "path": str(disk / "Top"), "push": "everything"})
    assert r.status_code == 400 and "pushes into too" in r.json()["detail"]
    pulled = _create(owner, folder=top, path=str(disk / "Pulled"))                  # pulling from above is allowed
    assert _sync(owner, pulled["id"])["last_error"] == ""
    second = _create(owner, folder=side, path=str(disk / "Side"), push="everything")
    r = owner.patch(f"/api/folder-links/{pulled['id']}", json={"push": "pdfs"})
    assert r.status_code == 400 and "pushes into too" in r.json()["detail"]

    # the folder of the older one moved below the newer one's (a mirror's merge: no person files a folder in a work
    # folder): the newer stops, the older goes on
    commit_ops(workspace_of(OWNER), FOLDERS, [{"op": "move", "id": below, "parent": side}], actor=account_of(OWNER),
               client=SYNC_CLIENT)
    status = _sync(owner, second["id"])
    assert "pushes into too" in status["stopped"] and status["last_error"] == status["stopped"] and status["failed_at"]
    assert _sync(owner, first["id"])["last_error"] == ""


def test_a_change_of_direction_passes_the_same_refusals(owner, disk):
    link = _vault(owner, disk / "Turning", {"t.md": "t\n"}, library=True)    # its files in the library: its push may come down
    url = f"/api/folder-links/{link['id']}"
    assert owner.patch(url, json={"pull": False}).json()["pull"] is False             # a vault that only pushes
    r = owner.patch(url, json={"push": "off"})
    assert r.status_code == 400 and "pulls, pushes, or both" in r.json()["detail"]
    assert owner.patch(url, json={"pull": True, "push": "pdfs"}).json()["push"] == "pdfs"
    r = owner.patch(url, json={"notes": False})
    assert r.status_code == 400 and "keeps its notes" in r.json()["detail"]
    assert owner.patch(url, json={"push": "everything"}).json()["push"] == "everything"
    pulled = _create(owner, folder=make_folder(owner, "VR pulled"), path=str(disk / "Pulled"), notes=False)
    assert pulled["notes"] is False                                               # its PDFs alone
    r = owner.patch(f"/api/folder-links/{pulled['id']}", json={"push": "everything"})
    assert r.status_code == 200 and r.json()["push"] == "everything" and r.json()["notes"] is True
    assert owner.patch(f"/api/folder-links/{pulled['id']}", json={"pull": False}).json()["pull"] is False
    file_link = _create(owner, kind="file", page=make_page(owner, "VR followed")["id"],
                        path=str(_put(disk / "followed.pdf", _pdf("followed"))))
    r = owner.patch(f"/api/folder-links/{file_link['id']}", json={"pull": True})
    assert r.status_code == 400 and "only pushes" in r.json()["detail"]


def test_a_place_taken_and_the_limits_are_told_in_the_words_of_vaults(owner, disk, monkeypatch):
    """The shell shows these refusals as they come: no *link*, no *follows*,
    no *keeps on disk*."""
    f = _put(disk / "Taken" / "main.pdf", _pdf("taken"))
    _create(owner, kind="file", page=make_page(owner, "VR taken A")["id"], path=str(f))
    r = owner.post("/api/folder-links", json={"kind": "file", "page": make_page(owner, "VR taken B")["id"], "path": str(f)})
    assert r.status_code == 400 and r.json()["detail"] == f"{f.resolve()} is already pushed to another page by a synced vault."
    monkeypatch.setattr(folder_links, "MAX_FILE_LINKS", 0)
    r = owner.post("/api/folder-links", json={"kind": "file", "page": make_page(owner, "VR taken C")["id"],
                                              "path": str(_put(disk / "Taken" / "other.pdf", _pdf("other")))})
    assert r.status_code == 400 and r.json()["detail"] == "A workspace has at most 0 synced vaults of files."


# --- the sync route ----------------------------------------------------------------------------

def test_a_sync_goes_one_way_forces_either_way_and_goes_on_past_the_guard_when_confirmed(owner, disk):
    root = disk / "Ways"
    link = _vault(owner, root, {"w.md": "from disk\n", **{f"n{k}.md": f"note {k}\n" for k in range(11)}})
    page = _page_of(link, "w.md")

    # push only: the file's change goes in, Gamma's stays out of the file
    _put(root / "w.md", "from disk, edited\n")
    _sync(owner, link["id"], direction="push")
    assert _texts(link, page) == ["from disk, edited"]
    _edit(link, page, _find(link, page, "from disk, edited"), "from gamma")
    _sync(owner, link["id"], direction="push")
    assert (root / "w.md").read_text(encoding="utf-8") == "from disk, edited\n"
    _sync(owner, link["id"], direction="pull")
    assert (root / "w.md").read_text(encoding="utf-8") == "from gamma\n"

    # force push: the file is the page, what it replaced recorded resolved; force pull: the page is the file
    _edit(link, page, _find(link, page, "from gamma"), "typed in gamma")
    _put(root / "w.md", "the file says\n", age=5)
    _sync(owner, link["id"], force="push")
    assert _texts(link, page) == ["the file says"]
    with connect_pages_db(link["workspace_id"]) as conn:
        assert conn.execute("SELECT kind, mine, resolved FROM sync_conflicts WHERE page_id = ?", (page,)).fetchall() == [
            ("file_won", "typed in gamma", 1)]
    _edit(link, page, _find(link, page, "the file says"), "gamma's version")
    _put(root / "w.md", "a local edit\n", age=3)
    _sync(owner, link["id"], force="pull")
    assert (root / "w.md").read_text(encoding="utf-8") == "gamma's version\n" and _texts(link, page) == ["gamma's version"]

    # most files gone: held until confirmed
    pages = [_page_of(link, f"n{k}.md") for k in range(11)]
    for k in range(8):
        os.remove(root / f"n{k}.md")
    _sync(owner, link["id"])
    status = _sync(owner, link["id"])
    assert status["held"] == {"removals": 8, "total": 12} and status["last_error"] == ""
    assert owner.get(f"/api/folder-links/{link['id']}").json()["status"]["held"] == status["held"]
    remote = LocalRemote(link["workspace_id"], link["folder_id"], link["created_by"])
    assert all(remote.state(p) == "live" for p in pages)
    _sync(owner, link["id"], confirm=1)
    assert [remote.state(p) for p in pages] == ["trashed"] * 8 + ["live"] * 3


def test_a_round_asked_for_while_one_runs_follows_it(owner, disk, monkeypatch):
    link = _vault(owner, disk / "Busy", {"b.md": "b\n"})
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append((link_id, kw)))
    lock = folder_links._lock(link["id"])
    with lock:
        assert folder_links.run_link(link["id"], force="pull") == folder_links.get_link(link["id"])["status"]
    assert started == []                                    # kept, not lost
    folder_links.run_link(link["id"])                      # the round that ran first runs it after itself
    assert started == [(link["id"], {"force": "pull", "direction": "both", "confirm": False})]
    folder_links.run_link(link["id"])
    assert len(started) == 1


def test_a_hint_runs_the_tick_in_the_background(owner, disk, monkeypatch):
    ran = threading.Event()
    monkeypatch.setattr(folder_links, "tick", ran.set)
    r = owner.post("/api/folder-links/hint")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert ran.wait(5)


def test_the_tick_scans_a_vault_each_time(owner, disk, monkeypatch):
    root = disk / "Ticked"
    link = _vault(owner, root, {"t.md": "t\n"})
    monkeypatch.setattr(folder_links, "due", REAL_DUE)
    assert REAL_DUE(folder_links.get_link(link["id"])) == "both"             # nothing in Gamma moved: it scans anyway
    _put(root / "new.md", "dropped in\n")
    for _ in range(50):          # the app's own tick may be in the middle of one
        folder_links.tick()
        if "new.md" in vault_store.files(link["id"]):
            break
        time.sleep(0.1)
    assert _texts(link, _page_of(link, "new.md")) == ["dropped in"]
    folder_links.set_paused(link["id"], True)
    assert REAL_DUE(folder_links.get_link(link["id"])) == ""


# --- conflicts ----------------------------------------------------------------------------------

def _clash(owner, disk, name):
    """A vault whose file won a clash on its page: ``(link, page, block)``."""
    root = disk / name
    link = _vault(owner, root, {"C.md": "The quick brown fox jumps over the dog\n"})
    page = _page_of(link, "C.md")
    _edit(link, page, _find(link, page, "The quick brown fox jumps over the dog"),
          "The quick brown fox jumps over the lazy dog today")
    _put(root / "C.md", "A slow red fox leaps across a cat\n", age=5)
    assert _sync(owner, link["id"])["counts"]["conflicts"] == 1
    return link, page, _find(link, page, "A slow red fox leaps across a cat")


def test_a_vaults_conflicts_and_a_mirrors_are_kept_apart(owner, disk):
    link, page, block = _clash(owner, disk, "Clash")
    ws = link["workspace_id"]
    with connect_pages_db(ws) as conn:
        merged = conn.execute("INSERT INTO sync_conflicts (page_id, block_id, kind, mine, theirs, result, base, at, resolved) "
                              "VALUES (?, ?, 'merged', 'm', 't', 'r', 'b', '2026-01-01', 0)", (page, block)).lastrowid
        conn.commit()
    listed = owner.get(f"/api/folder-links/{link['id']}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"], c["mine"], c["theirs"]) for c in listed] == [
        ("file_won", block, "The quick brown fox jumps over the lazy dog today", "A slow red fox leaps across a cat")]
    assert owner.get(f"/api/folder-links/{link['id']}/conflicts", params={"page": "other"}).json()["conflicts"] == []
    assert _state(owner, page)["vault"]["conflicts"] == 1
    won = listed[0]["id"]
    # the mirror's and Zotero's side never see or settle the vault's
    assert [c["id"] for c in sync_engine.list_conflicts(ws, page_id=page)] == [merged]
    assert sync_engine.open_conflicts(ws) == 1 and sync_engine.open_conflict_mark(ws) == (1, merged)
    assert sync_engine.resolve_conflict(ws, won, "keep") is None and zotero_sync.resolve_conflict(ws, won, "keep") is None
    r = owner.post(f"/api/folder-links/{link['id']}/conflicts/{merged}", json={"choice": "keep"})
    assert r.status_code == 404

    # Gamma's side taken back: the next pull writes it to the file
    r = owner.post(f"/api/folder-links/{link['id']}/conflicts/{won}", json={"choice": "mine"})
    assert r.status_code == 200 and r.json() == {"id": won, "resolved": True}
    assert _texts(link, page) == ["The quick brown fox jumps over the lazy dog today"]
    _sync(owner, link["id"])
    assert (disk / "Clash" / "C.md").read_text(encoding="utf-8") == "The quick brown fox jumps over the lazy dog today\n"
    assert owner.get(f"/api/folder-links/{link['id']}/conflicts").json()["conflicts"] == []
    with connect_pages_db(ws) as conn:
        actor = conn.execute("SELECT actor FROM page_ops WHERE page_id = ? AND client = '' ORDER BY seq DESC",
                             (page,)).fetchone()[0]
    assert actor == link["created_by"]

    # forgetting a mirror takes its rows and leaves the vault's
    sync_engine.remove_mirror(ws)
    with connect_pages_db(ws) as conn:
        assert [r[0] for r in conn.execute("SELECT kind FROM sync_conflicts WHERE page_id = ?", (page,))] == ["file_won"]


def test_a_vaults_write_names_no_one_in_the_inbox(monkeypatch):
    emitted = []
    monkeypatch.setattr(inbox, "emit", lambda *a, **kw: emitted.append(a))
    monkeypatch.setattr(inbox, "_ids_of", lambda names: {n: n for n in names})
    found = [{"block_id": "b", "users": ["bob"], "text": "hi @bob"}]
    inbox.from_ops("ws", {"mentions_added": found, "actor": "maker", "client": folder_links.DISK_CLIENT, "page_id": "p"})
    assert emitted == []
    inbox.from_ops("ws", {"mentions_added": found, "actor": "maker", "client": "", "page_id": "p"})
    assert len(emitted) == 1


# --- the hot loop ----------------------------------------------------------------------------------

def test_a_page_written_in_gamma_has_a_round_of_its_own_once_its_writes_rest(owner, disk, monkeypatch):
    root = disk / "Hot"
    link = _vault(owner, root, {"h.md": "first\n", "other.md": "other\n"})
    page, ws = _page_of(link, "h.md"), link["workspace_id"]
    with folder_links._dirty_guard:
        folder_links._dirty.clear()
    _edit(link, page, _find(link, page, "first"), "typed in gamma")
    assert (ws, page) in folder_links._dirty
    folder_links.hot_tick()                                     # not rested yet (HOT_S)
    assert (root / "h.md").read_text(encoding="utf-8") == "first\n" and (ws, page) in folder_links._dirty

    real_scan = vault_disk.scan
    monkeypatch.setattr(vault_disk, "scan", lambda *a: pytest.fail("a hot round scanned"))
    monkeypatch.setattr(folder_links, "HOT_S", 0)
    with folder_links._lock(link["id"]):                        # a round of the vault runs: the mark waits
        folder_links.hot_tick()
    assert (root / "h.md").read_text(encoding="utf-8") == "first\n" and (ws, page) in folder_links._dirty
    for _ in range(50):          # the app's hot loop runs too now
        folder_links.hot_tick()
        if (root / "h.md").read_text(encoding="utf-8") == "typed in gamma\n":
            break
        time.sleep(0.05)
    assert (root / "h.md").read_text(encoding="utf-8") == "typed in gamma\n" and (ws, page) not in folder_links._dirty
    assert (root / "other.md").read_text(encoding="utf-8") == "other\n"
    # the vault's own writes mark nothing; a page of no vault's loses its mark
    _put(root / "h.md", "edited on disk\n", age=4)
    monkeypatch.setattr(vault_disk, "scan", real_scan)
    _sync(owner, link["id"])
    assert _texts(link, page) == ["edited on disk"] and (ws, page) not in folder_links._dirty
    loose = make_page(owner, "VR loose")["id"]
    commit_ops(ws, loose, [{"op": "insert", "id": new_block_id(), "parent": loose, "content": "x", "props": {}}],
               actor=account_of(OWNER))
    folder_links.hot_tick()
    assert (ws, loose) not in folder_links._dirty


def test_a_page_open_in_a_window_follows_its_file_without_a_scan(owner, disk, monkeypatch):
    root = disk / "Open"
    link = _vault(owner, root, {"o.md": "on screen\n", "gone.md": "soon gone\n"})
    page, gone = _page_of(link, "o.md"), _page_of(link, "gone.md")
    monkeypatch.setattr(collab, "open_pages", lambda: [(link["workspace_id"], page), (link["workspace_id"], gone)])
    folder_links.hot_tick()
    assert _texts(link, page) == ["on screen"]
    _put(root / "o.md", "saved in an editor\n", age=4)
    os.remove(root / "gone.md")
    for _ in range(50):
        folder_links.hot_tick()
        if _texts(link, page) == ["saved in an editor"]:
            break
        time.sleep(0.05)
    assert _texts(link, page) == ["saved in an editor"]
    remote = LocalRemote(link["workspace_id"], link["folder_id"], link["created_by"])
    assert remote.state(gone) == "live"                        # a hot round never removes
    monkeypatch.setattr(folder_links, "run_page", lambda *a: pytest.fail("a quiet page had a round"))
    folder_links.hot_tick()


def test_the_collab_rooms_say_which_pages_are_open_and_where_the_carets_are():
    room = collab.Room(("vr-ws", "vr-page"))
    room.peers["c1"] = collab.Peer(ws=None, client="c1", user="u", name="u", can_edit=True, block="b1")
    room.peers["c2"] = collab.Peer(ws=None, client="c2", user="v", name="v", can_edit=True)
    collab._rooms[room.key] = room
    collab._rooms[("vr-ws", "folders")] = collab.Room(("vr-ws", "folders"), {"c3": room.peers["c2"]})
    try:
        assert ("vr-ws", "vr-page") in collab.open_pages() and ("vr-ws", "folders") not in collab.open_pages()
        assert collab.editing("vr-ws", "vr-page") == {"b1"} and collab.editing("vr-ws", "none") == set()
    finally:
        collab._rooms.pop(room.key, None)
        collab._rooms.pop(("vr-ws", "folders"), None)


# --- removing a vault -------------------------------------------------------------------------------

def _mixed(owner, disk, name):
    """A vault with a note of the person's, a page made in Gamma (its file
    the vault's), and a PDF read where it is, all in the library (the vault's
    switch on): ``(link, root, pages)``."""
    root = disk / name
    link = _vault(owner, root, {"Mine.md": "my own note\n", "Paper.pdf": _pdf(f"{name} paper")}, library=True)
    page = _made_in(owner, f"{name} from Gamma", link["folder_id"])
    commit_ops(link["workspace_id"], page, [{"op": "insert", "id": new_block_id(), "parent": page,
                                             "content": "written in gamma", "props": {}}], actor=link["created_by"])
    _sync(owner, link["id"])
    assert (root / f"{name} from Gamma.md").read_text(encoding="utf-8") == "written in gamma\n"
    return link, root, {"mine": _page_of(link, "Mine.md"), "pdf": _page_of(link, "Paper.pdf"), "gamma": page}


def test_removing_a_vault_keeps_its_files_and_pages_and_stores_its_pdfs_first(owner, disk):
    link, root, pages = _mixed(owner, disk, "Keep")
    ws, size = link["workspace_id"], (root / "Paper.pdf").stat().st_size
    assert _props(link, pages["pdf"])["doc"]["at"] == "path"
    r = owner.get(f"/api/folder-links/{link['id']}/removal")
    assert r.status_code == 200 and r.json() == {"pages": 3, "in_place": 1, "bytes": size, "files": 0, "in_library": 3}
    doc = _props(link, pages["pdf"])["doc"]["hash"]
    assert not storage.exists(ws, f"{doc}.pdf")

    assert owner.delete(f"/api/folder-links/{link['id']}", params={"files": "keep"}).json() == {"ok": True}
    assert folder_links.get_link(link["id"]) is None and vault_store.files(link["id"]) == {}
    assert sorted(p.name for p in root.iterdir() if p.is_file()) == ["Keep from Gamma.md", "Mine.md", "Paper.pdf"]
    assert not (root / vault_disk.MARKER).exists()
    assert storage.exists(ws, f"{doc}.pdf") and _props(link, pages["pdf"])["doc"]["at"] == "store"
    with connect_vaults_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM linked_files WHERE ws = ? AND path LIKE ?",
                            (ws, f"{root}%")).fetchone()[0] == 0
    remote = LocalRemote(ws, link["folder_id"], link["created_by"])
    assert all(remote.state(p) == "live" for p in pages.values())
    assert not any(key in _props(link, p) for p in pages.values() for key in ("work_folder", "library", "file_name"))
    assert remote.mark() is None                                                   # library pages, filed as they were


def test_removing_a_work_folder_sends_its_files_out_of_the_library_to_recently_deleted(owner, disk):
    """Its files not in the library go to Recently deleted, those in it stay
    library pages, and the directory folders that leaves empty go: they stood
    for directories, and nothing is filed there now."""
    root = disk / "Leaving"
    link = _vault(owner, root, {"a.md": "a\n", "deep/b.md": "b\n", "kept/c.md": "c\n", "Paper.pdf": _pdf("leaving")})
    ws, pages = link["workspace_id"], {rel: _page_of(link, rel) for rel in ("a.md", "deep/b.md", "kept/c.md", "Paper.pdf")}
    kept = pages["kept/c.md"]
    commit_ops(ws, kept, [{"op": "set", "id": kept, "props": {"library": True}}], actor=link["created_by"])
    folders = {d: row["folder_id"] for d, row in vault_store.dirs(link["id"]).items()}
    r = owner.get(f"/api/folder-links/{link['id']}/removal")
    assert r.json() | {"bytes": 0} == {"pages": 4, "in_place": 1, "bytes": 0, "files": 3, "in_library": 1}

    assert owner.delete(f"/api/folder-links/{link['id']}").status_code == 200
    remote = LocalRemote(ws, link["folder_id"], link["created_by"])
    assert {rel: remote.state(p) for rel, p in pages.items()} == {"a.md": "trashed", "deep/b.md": "trashed",
                                                                  "kept/c.md": "live", "Paper.pdf": "trashed"}
    assert not any(key in _props(link, kept) for key in ("work_folder", "library", "file_name"))
    with connect_pages_db(ws) as conn:
        left = {r[0] for r in conn.execute("SELECT id FROM unified_blocks WHERE page_id = ?", (FOLDERS,))}
    assert folders["kept"] in left and link["folder_id"] in left and folders["deep"] not in left
    assert sorted(p.relative_to(root).as_posix() for p in root.rglob("*.*") if ".gamma" not in p.parts) == [
        "Paper.pdf", "a.md", "deep/b.md", "kept/c.md"]                             # the disk as it was


def test_removing_a_vault_with_its_files_trashes_only_those_its_pulls_made(owner, disk):
    link, root, pages = _mixed(owner, disk, "Trash")
    (root / ".gamma" / "trash").mkdir(parents=True, exist_ok=True)
    _put(root / ".gamma" / "trash" / "old" / "kept.md", "an earlier round's\n")
    assert owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).status_code == 200
    assert sorted(p.name for p in root.iterdir() if p.is_file()) == ["Mine.md", "Paper.pdf"]
    trashed = sorted(p.relative_to(root / ".gamma" / "trash").as_posix() for p in (root / ".gamma" / "trash").rglob("*.md"))
    assert len(trashed) == 2 and "old/kept.md" in trashed and any(t.endswith("/Trash from Gamma.md") for t in trashed)
    assert not (root / vault_disk.MARKER).exists()
    assert LocalRemote(link["workspace_id"], "", "").state(pages["gamma"]) == "live"


def test_a_removal_that_cannot_store_a_pdf_removes_nothing(owner, disk, monkeypatch):
    link, root, pages = _mixed(owner, disk, "Stuck")

    def refuse(ws, path, doc):
        raise folder_links.LinkError(f"{path} could not be stored, so the vault was not removed: the disk is full")
    monkeypatch.setattr(folder_links, "_store_copy", refuse)
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"})
    assert r.status_code == 409 and "was not removed" in r.json()["detail"]
    assert folder_links.get_link(link["id"]) is not None and (root / vault_disk.MARKER).is_file()
    assert (root / "Stuck from Gamma.md").is_file() and _props(link, pages["pdf"])["doc"]["at"] == "path"
    assert vault_store.files(link["id"])


def test_a_vault_whose_disk_is_gone_is_removed_with_its_pages_reading_their_files(owner, disk):
    link, root, pages = _mixed(owner, disk, "Unplugged")
    away = disk / "Unplugged elsewhere"
    os.rename(root, away)
    assert owner.delete(f"/api/folder-links/{link['id']}").status_code == 200
    assert folder_links.get_link(link["id"]) is None
    assert _props(link, pages["pdf"])["doc"]["at"] == "path"                    # missing, to be located
    assert (away / vault_disk.MARKER).is_file()                                # nothing written where it went


# --- the page's own routes -----------------------------------------------------------------------------

def test_a_notes_file_is_read_and_written_from_gamma_through_the_write_path(owner, disk):
    root = disk / "Edited"
    link = _vault(owner, root, {"E.md": "# Title\n\nline one\n", "Paper.pdf": _pdf("edited paper")}, library=True)
    page = _page_of(link, "E.md")
    url = f"/api/folder-links/page/{page}/file"
    got = owner.get(url).json()
    assert got == {"text": "# Title\n\nline one\n", "hash": _sha(root / "E.md"), "path": str(root.resolve() / "E.md")}

    r = owner.put(url, json={"text": "# Title\n\nline two\n", "hash": "0" * 64})
    assert r.status_code == 409 and r.json()["hash"] == got["hash"]
    r = owner.put(url, json={"text": "x" * (MAX_CONTENT + 1), "hash": got["hash"]})
    assert r.status_code == 400 and "longer than Gamma takes" in r.json()["detail"]
    r = owner.put(url, json={"text": "# Title\n\nline two\n", "hash": got["hash"]})
    assert r.status_code == 200 and r.json() == {"hash": _sha(root / "E.md"), "path": got["path"]}
    assert (root / "E.md").read_text(encoding="utf-8") == "# Title\n\nline two\n"
    assert [h["data"] for h in vault_store.history(link["id"], "E.md")] == [b"# Title\n\nline one\n"]
    for _ in range(50):          # its page's round comes at the next hot tick (the app's own may take it)
        folder_links.hot_tick()
        if _texts(link, page) == ["# Title", "line two"]:
            break
        time.sleep(0.05)
    assert _texts(link, page) == ["# Title", "line two"]

    assert owner.get(f"/api/folder-links/page/{_page_of(link, 'Paper.pdf')}/file").status_code == 404
    _put(root / "E.md", b"\xff\xfe not text")
    assert owner.get(url).status_code == 400
    owner.patch(f"/api/folder-links/{link['id']}", json={"push": "pdfs"}).raise_for_status()
    r = owner.put(url, json={"text": "q\n", "hash": _sha(root / "E.md")})
    assert r.status_code == 400 and "does not push notes" in r.json()["detail"]


def test_a_save_between_the_file_editors_check_and_its_write_is_kept_in_the_history(owner, disk, monkeypatch):
    """The history keeps one entry of a path per few minutes, for Gamma's
    own writes; a person's save that lands between the editor's check and its
    rename is no write of Gamma's, and was dropped when an entry was young."""
    root = disk / "Raced"
    link = _vault(owner, root, {"R.md": "first\n"})
    url = f"/api/folder-links/page/{_page_of(link, 'R.md')}/file"
    first = owner.get(url).json()
    owner.put(url, json={"text": "second\n", "hash": first["hash"]}).raise_for_status()
    replace = vault_disk.replace_note

    def saved_meanwhile(root_, rel, data, *a, **kw):
        _put(root_ / rel, "the person's save\n")
        return replace(root_, rel, data, *a, **kw)
    monkeypatch.setattr(vault_disk, "replace_note", saved_meanwhile)
    owner.put(url, json={"text": "third\n", "hash": _sha(root / "R.md")}).raise_for_status()
    assert b"the person's save\n" in [h["data"] for h in vault_store.history(link["id"], "R.md")]


def test_the_file_editor_never_reads_or_writes_over_a_cloud_placeholder(owner, disk, monkeypatch):
    """The rounds never read a placeholder (a recall from the cloud) or write
    over one; the file editor read it, and wrote new bytes over it. It says
    the file is in the cloud, and touches nothing."""
    root = disk / "Clouded"
    link = _vault(owner, root, {"C.md": "kept in the cloud\n"})
    url = f"/api/folder-links/page/{_page_of(link, 'C.md')}/file"
    hash = owner.get(url).json()["hash"]
    cloud = str(vault_disk.path(root.resolve(), "C.md"))
    placeholder = vault_disk._placeholder
    monkeypatch.setattr(vault_disk, "_placeholder", lambda st: st.st_ino == os.stat(cloud).st_ino or placeholder(st))
    read = []
    monkeypatch.setattr(vault_disk, "read", lambda root_, rel: read.append(rel) or b"")
    r = owner.get(url)
    assert r.status_code == 400 and "in the cloud" in r.json()["detail"]
    r = owner.put(url, json={"text": "written over it\n", "hash": hash})
    assert r.status_code == 400 and "in the cloud" in r.json()["detail"]
    assert read == [] and (root / "C.md").read_text(encoding="utf-8") == "kept in the cloud\n"


def test_a_page_of_no_vault_and_a_page_of_a_followed_file(owner, disk):
    plain = make_page(owner, "VR plain")["id"]
    assert _state(owner, plain) == {"vault": None, "reader": None}
    assert owner.get(f"/api/folder-links/page/{plain}/file").status_code == 404
    followed = make_page(owner, "VR follows")["id"]
    f = _put(disk / "follows.pdf", _pdf("follows"))
    link = _create(owner, kind="file", page=followed, path=str(f))
    vault = _state(owner, followed)["vault"]
    assert vault == {"id": link["id"], "name": "follows.pdf", "scope": "file", "path": str(f.resolve()), "pull": False,
                     "push": "pdfs", "problem": "", "conflicts": 0, "paused": False, "work_folder": False,
                     "choosing": False, "library": False, "mine": True, "page": {"library": False, "file_name": ""}}
    folder = make_folder(owner, "VR kept")
    kept = make_page(owner, "VR kept page", {"folders": [folder]})["id"]
    older = _create(owner, folder=folder, path=str(disk / "Kept"))
    _sync(owner, older["id"])
    assert _state(owner, kept)["vault"]["path"] == str((disk / "Kept" / "VR kept page.md").resolve())


def test_a_pages_chip_says_its_vault_reaches_no_file_while_it_is_stopped_offline_held_or_its_file_gone(owner, disk):
    """The chip said a plain *Synced* for a vault that stopped, whose
    directory or followed file was gone, or that its guard held, while the
    sync panel said otherwise: an edit there seemed to reach the file. Its
    page's problem is the vault's then, in words of its own."""
    root = disk / "Halted"
    link = _vault(owner, root, {f"n{k}.md": f"note {k}\n" for k in range(10)})
    page = _page_of(link, "n0.md")
    for k in range(1, 7):
        (root / f"n{k}.md").unlink()
    for _ in range(2):
        held = _sync(owner, link["id"])
    assert held.get("held") and "held" in _state(owner, page)["vault"]["problem"]
    _sync(owner, link["id"], confirm=1)
    assert _state(owner, page)["vault"]["problem"] == ""
    shutil.rmtree(root / vault_disk.MARKER_DIR)
    assert _sync(owner, link["id"]).get("stopped")
    assert "stopped" in _state(owner, page)["vault"]["problem"]
    away = disk / "Halted away"
    root.rename(away)
    assert _sync(owner, link["id"]).get("offline")
    assert "not there" in _state(owner, page)["vault"]["problem"]
    followed = make_page(owner, "VR followed, then gone")["id"]
    f = _put(disk / "gone.pdf", _pdf("gone"))
    file_link = _create(owner, kind="file", page=followed, path=str(f))
    f.unlink()
    folder_links.run_link(file_link["id"])
    assert _state(owner, followed)["vault"]["problem"] == "the file is not there"


def test_a_page_of_a_vault_is_not_published(owner, disk):
    link = _vault(owner, disk / "Unpublished", {"U.md": "u\n"})
    page = _page_of(link, "U.md")
    assert publish.state(account_of(OWNER), link["workspace_id"], page)["reason"] == publish.IN_VAULT
    r = owner.post(f"/api/pages/{page}/publish")
    assert r.status_code == 409 and r.json()["detail"] == publish.IN_VAULT
    assert publish.state(account_of(OWNER), link["workspace_id"], make_page(owner, "VR free")["id"]).get("reason") \
        != publish.IN_VAULT


def test_a_pdf_inside_a_vault_opens_the_vaults_page(owner, disk):
    root = disk / "Opened"
    link = _vault(owner, root, {"In.pdf": _pdf("in the vault")})
    page = _page_of(link, "In.pdf")
    r = owner.post("/api/folder-links/open", json={"path": str(root / "In.pdf")})
    assert r.status_code == 200 and r.json()["page"] == page and r.json()["created"] is False
    # one dropped in since the last round: the vault makes it a page first
    _put(root / "deep" / "Later.pdf", _pdf("dropped in later"))
    r = owner.post("/api/folder-links/open", json={"path": str(root / "deep" / "Later.pdf")})
    assert r.status_code == 200 and r.json()["page"] == _page_of(link, "deep/Later.pdf") and r.json()["created"] is False
    # a vault that makes no page of a file Gamma did not read: the file opens as any file does
    pulled = _vault(owner, disk / "Pulled", push="off")
    _put(disk / "Pulled" / "loose.pdf", _pdf("loose in a pulled vault"))
    r = owner.post("/api/folder-links/open", json={"path": str(disk / "Pulled" / "loose.pdf")})
    assert r.status_code == 200 and r.json()["created"] is True and vault_store.files(pulled["id"]) == {}
    # a file vault of a file inside a vault is refused
    r = owner.post("/api/folder-links", json={"kind": "file", "page": page, "path": str(root / "In.pdf")})
    assert r.status_code == 400 and "open its page" in r.json()["detail"]


def test_a_directory_vault_takes_in_the_file_vaults_inside_it(owner, disk):
    root = disk / "Taking"
    f = _put(root / "papers" / "Followed.pdf", _pdf("followed then taken in"))
    up = owner.post("/api/uploads", files={"file": ("f.pdf", f.read_bytes(), "application/pdf")}).json()
    page = make_page(owner, "VR taken in", doc_props(up["doc_id"], name="Followed.pdf"))["id"]
    file_link = _create(owner, kind="file", page=page, path=str(f))
    link = _create(owner, path=str(root), new_folder=True, push="everything")
    assert folder_links.get_link(file_link["id"]) is None
    row = vault_store.files(link["id"])["papers/Followed.pdf"]
    assert row["page_id"] == page and row["kind"] == "pdf" and row["hash"] == up["doc_id"]
    with connect_pages_db(link["workspace_id"]) as conn:
        papers = conn.execute("SELECT id FROM unified_blocks WHERE page_id = ? AND parent_id = ? AND content = 'papers'",
                              (FOLDERS, link["folder_id"])).fetchone()[0]
    props = _props(link, page)
    assert props[FOLDERS][0] == papers                              # a work folder's file now, in the library as it was
    assert (props["work_folder"], props["library"], props["file_name"]) == (link["folder_id"], True, "Followed.pdf")
    status = _sync(owner, link["id"])
    assert status["last_error"] == ""
    assert [r["page_id"] for p, r in vault_store.files(link["id"]).items() if r["kind"] == "pdf"] == [page]
    assert _state(owner, page)["vault"]["id"] == link["id"]


def test_a_vault_found_elsewhere_is_relinked_by_its_marker(owner, disk):
    root = disk / "Moving"
    link = _vault(owner, root, {"m.md": "moving\n", "M.pdf": _pdf("moving paper")})
    pdf_page = _page_of(link, "M.pdf")
    moved = disk / "Moved"
    os.rename(root, moved)
    status = _sync(owner, link["id"])
    assert status["last_error"] and status["offline"]
    stranger = disk / "Stranger"
    stranger.mkdir()
    vault_disk.make_marker(stranger)
    r = owner.post(f"/api/folder-links/{link['id']}/relink", json={"path": str(stranger)})
    assert r.status_code == 400 and "not this vault" in r.json()["detail"]
    r = owner.post(f"/api/folder-links/{link['id']}/relink", json={"path": str(moved)})
    assert r.status_code == 200 and r.json()["path"] == str(moved.resolve()) and "last_error" not in r.json()["status"]
    assert _sync(owner, link["id"])["last_error"] == ""
    with connect_vaults_db() as conn:
        paths = [p for (p,) in conn.execute("SELECT path FROM linked_files WHERE page_id = ?", (pdf_page,))]
    assert paths == [str(moved.resolve() / "M.pdf")]
    assert storage.open_path(link["workspace_id"], f"{_props(link, pdf_page)['doc']['hash']}.pdf") == moved.resolve() / "M.pdf"


# --- counter-examples, a first set -----------------------------------------------------------------------

def test_a_round_the_process_died_in_does_not_rest_the_vault(owner, disk, monkeypatch):
    """No restart cleared ``running``: the tick and the hot loop left the vault
    alone for ten minutes."""
    link = _vault(owner, disk / "Died", {"d.md": "d\n"})
    real = vault_sync.sync

    def dying(*args, **kwargs):
        raise KeyboardInterrupt("the process died")
    monkeypatch.setattr(vault_sync, "sync", dying)
    with pytest.raises(KeyboardInterrupt):
        folder_links.run_link(link["id"], wait=True)
    monkeypatch.setattr(vault_sync, "sync", real)
    stored = folder_links.get_link(link["id"])                 # its mark stays, read as no round: no lock holds it
    assert stored["status"]["running"] is False and not folder_links._resting(stored) and REAL_DUE(stored)
    with folder_links._lock(link["id"]):                     # a round of this process runs: it rests
        assert folder_links._resting(stored)


def test_requests_made_while_a_round_runs_add_up(owner, disk, monkeypatch):
    """The last request replaced a force or a confirmation asked before it, and
    one that waited past ``ROUND_WAIT_S`` was dropped."""
    link = _vault(owner, disk / "Asked", {"a.md": "a\n"})
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(kw))
    monkeypatch.setattr(folder_links, "ROUND_WAIT_S", 0.05)
    with folder_links._lock(link["id"]):
        folder_links.run_link(link["id"], force="pull")
        folder_links.run_link(link["id"], confirm=True, direction="push")
        folder_links.run_link(link["id"], wait=True)
    folder_links.run_link(link["id"])
    assert started == [{"force": "pull", "direction": "both", "confirm": True}]


def test_a_page_round_that_finds_its_file_being_written_keeps_its_mark(owner, disk, monkeypatch):
    """The write from Gamma's file editor marks the page; the next hot tick
    came within ``SETTLE_NOTE_S``, waited, and dropped the mark all the same."""
    root = disk / "Settling"
    link = _vault(owner, root, {"s.md": "s\n"})
    page, ws = _page_of(link, "s.md"), link["workspace_id"]
    url = f"/api/folder-links/page/{page}/file"
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 3600)
    owner.put(url, json={"text": "s, from the editor\n", "hash": owner.get(url).json()["hash"]}).raise_for_status()
    folder_links.hot_tick()
    assert (ws, page) in folder_links._dirty and _texts(link, page) == ["s"]
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    for _ in range(50):
        folder_links.hot_tick()
        if (ws, page) not in folder_links._dirty:
            break
        time.sleep(0.05)
    assert _texts(link, page) == ["s, from the editor"]


def test_a_tick_asked_for_while_one_runs_runs_again_after_it(monkeypatch):
    """A window's focus during a tick was dropped, for a vault that tick had
    passed already."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    runs = []

    def once():
        runs.append(1)
        if len(runs) == 1:
            folder_links.tick()        # the hint: the tick is running, so it is asked for and returns
    monkeypatch.setattr(folder_links, "_tick_once", once)
    folder_links.tick()
    for _ in range(100):               # the app's own tick may be the one running
        if len(runs) >= 2:
            break
        time.sleep(0.05)
    assert len(runs) >= 2


def test_a_page_round_is_refused_as_a_whole_round_is(owner, disk, monkeypatch):
    root = disk / "Refused page"
    link = _vault(owner, root, {"r.md": "r\n"})
    page = _page_of(link, "r.md")

    def refuse(link, **kw):
        raise folder_links.LinkError("refused since")
    monkeypatch.setattr(folder_links, "check_vault", refuse)
    _edit(link, page, _find(link, page, "r"), "r, typed")
    assert folder_links.run_page(link["id"], page)
    assert (root / "r.md").read_text(encoding="utf-8") == "r\n"
    assert folder_links.get_link(link["id"])["status"]["stopped"] == "refused since"


def test_a_viewer_is_told_no_path_in_a_link_its_status_or_a_files_problem(lab, disk):
    ws, alice, bob, vera = lab
    root = disk / "Paths"
    link = _vault(alice, root, {"L.md": "locked text\n"})
    page = _page_of(link, "L.md")
    # a note is replaced through a temp file beside it and a rename: Windows refuses that over a read-only
    # file, where POSIX goes by the directory's mode instead, so each locks what stops the write there
    locked, shut, open_again = ((root / "L.md", stat.S_IREAD, stat.S_IWRITE | stat.S_IREAD) if WINDOWS
                                else (root, stat.S_IRUSR | stat.S_IXUSR, stat.S_IRWXU))
    os.chmod(locked, shut)
    try:
        _edit(link, page, _find(link, page, "locked text"), "gamma wants this")
        status = _sync(alice, link["id"])
    finally:
        os.chmod(locked, open_again)
    assert status["kept"] and "another program" in status["kept"][0]["why"]
    folder_links._write_status(link["id"], {**folder_links.get_link(link["id"])["status"],
                                            "last_error": f"Cannot write {root}: no", "stopped": f"{root} moved",
                                            "push_off": f"push turned off: the synced vault {root} pushes into too"})
    seen = _state(vera, page)["vault"]
    got = vera.get(f"/api/folder-links/{link['id']}").json()
    listed = next(l for l in vera.get("/api/folder-links").json()["links"] if l["id"] == link["id"])
    assert seen["problem"] and repr(str(disk))[1:-1] not in repr((seen, got, listed))     # as repr writes a path
    assert got["path"] == got["dest"] == "" and got["status"]["stopped"] == "The vault stopped."
    assert alice.get(f"/api/folder-links/{link['id']}").json()["path"] == str(root.resolve())


def test_removing_a_vault_with_its_files_keeps_a_pulled_file_the_person_wrote_in(owner, disk):
    link, root, pages = _mixed(owner, disk, "Written in")
    pushed = root / "Written in from Gamma.md"
    _put(pushed, "written in gamma\n\nand a chapter the person wrote\n")
    _sync(owner, link["id"])                                  # pushed: the person's file now
    later = _made_in(owner, "Changed later", link["folder_id"])
    commit_ops(link["workspace_id"], later, [{"op": "insert", "id": new_block_id(), "parent": later,
                                              "content": "from gamma", "props": {}}], actor=link["created_by"])
    _sync(owner, link["id"])
    changed = _put(root / "Changed later.md", "from gamma, and edited, not synced yet\n", age=3)
    assert owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).status_code == 200
    assert pushed.is_file() and changed.is_file()


def test_removing_a_vault_whose_marker_went_stores_its_pdfs_first(owner, disk):
    """``git clean -xdf`` took ``.gamma``: the directory answers, so the PDFs
    read in place are stored, and nothing is written there."""
    link, root, pages = _mixed(owner, disk, "Cleaned")
    ws, doc = link["workspace_id"], _props(link, pages["pdf"])["doc"]["hash"]
    shutil.rmtree(root / ".gamma")
    assert owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).status_code == 200
    assert storage.exists(ws, f"{doc}.pdf") and _props(link, pages["pdf"])["doc"]["at"] == "store"
    assert not (root / ".gamma").exists() and (root / "Cleaned from Gamma.md").is_file()


def test_a_vault_whose_trash_was_emptied_can_be_removed(owner, disk):
    link, root, pages = _mixed(owner, disk, "Emptied")
    ws = link["workspace_id"]
    with connect_pages_db(ws) as conn:            # a note follows Gamma where its work folder pulls: to the vault's trash
        trash_page(ws, conn, pages["mine"], actor=link["created_by"])
    _sync(owner, link["id"])
    assert not (root / "Mine.md").exists()
    shutil.rmtree(root / ".gamma" / "trash")
    assert owner.delete(f"/api/folder-links/{link['id']}", params={"files": "keep"}).status_code == 200


def test_removing_a_vault_leaves_a_pdf_opened_from_there_reading_in_place(owner, disk):
    root = disk / "Readers"
    f = _put(root / "x.pdf", _pdf("opened before the vault"))
    opened = owner.post("/api/folder-links/open", json={"path": str(f)}).json()
    link = _vault(owner, root, {"n.md": "note\n"}, push="off")
    assert owner.delete(f"/api/folder-links/{link['id']}").status_code == 200
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT page_id FROM linked_files WHERE path = ?", (str(f.resolve()),)).fetchall()
    assert rows == [(opened["page"],)]


def test_the_file_routes_of_a_vault_whose_marker_went_read_and_write_nothing(owner, disk):
    root = disk / "Stopped edit"
    link = _vault(owner, root, {"S.md": "stopped\n"})
    url = f"/api/folder-links/page/{_page_of(link, 'S.md')}/file"
    got = owner.get(url).json()
    shutil.rmtree(root / ".gamma")
    assert owner.get(url).status_code == 400
    r = owner.put(url, json={"text": "replaced from gamma\n", "hash": got["hash"]})
    assert r.status_code == 400 and (root / "S.md").read_text(encoding="utf-8") == "stopped\n"


@pytest.mark.skipif(os.name != "nt", reason="device paths and administrative shares are Windows'")
@pytest.mark.parametrize("spell", ["device", "share"])
def test_a_place_inside_a_vault_spelled_another_way_is_refused(owner, disk, spell):
    """Two vaults over one directory: one file two pages, each pushing into it."""
    outer = disk / f"Outer {spell}"
    _vault(owner, outer, {"o.md": "outer\n", "x.pdf": _pdf(f"x {spell}")})
    _put(outer / "inner" / "i.md", "inner\n")

    def spelled(path):
        real = str(path.resolve())
        return "\\\\?\\" + real if spell == "device" else "\\\\localhost\\" + real[0] + "$" + real[2:]
    if not os.path.isdir(spelled(outer)):
        pytest.skip(f"{spelled(outer)} cannot be reached here")
    r = owner.post("/api/folder-links", json={"path": spelled(outer / "inner"), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and "do not overlap" in r.json()["detail"]
    page = make_page(owner, f"x {spell}")["id"]
    r = owner.post("/api/folder-links", json={"kind": "file", "page": page, "path": spelled(outer / "x.pdf")})
    assert r.status_code == 400 and "open its page" in r.json()["detail"]


@pytest.mark.parametrize("where", ["relative", "absolute"])
def test_an_adopted_markers_notes_folder_outside_the_vault_is_none(owner, disk, where):
    """A marker that came with the directory names the notes folder it likes:
    pull wrote outside the vault."""
    root, outside = disk / f"Adopted {where}", disk / f"Outside {where}"
    outside.mkdir(parents=True)
    _put(root / "paper.pdf", _pdf(f"adopted paper {where}"))
    (root / ".gamma").mkdir(parents=True)
    folder = f"../Outside {where}" if where == "relative" else str(outside).replace("\\", "/")
    (root / ".gamma" / "vault.json").write_text(json.dumps({"format": "gamma-vault-1", "id": "f" * 31 + where[0],
                                                            "notes_folder": folder, "ignore": []}))
    link = _create(owner, path=str(root), new_folder=True, push="everything", adopt=True)
    _sync(owner, link["id"])
    page = _page_of(link, "paper.pdf")
    commit_ops(link["workspace_id"], page, [{"op": "insert", "id": new_block_id(), "parent": page,
                                             "content": "a note typed in gamma", "props": {}}], actor=link["created_by"])
    _sync(owner, link["id"])
    assert list(outside.iterdir()) == [] and (root / "paper.md").read_text(encoding="utf-8") == "a note typed in gamma\n"


# --- who may use the routes ----------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lab(owner):
    """A shared workspace of vr_owner's with an editor and a viewer:
    ``(workspace, owner, editor, viewer)``, each asking in it."""
    info = workspaces.create("VR lab", account_of(OWNER), kind="shared")

    def member(name, role):
        make_user(name, "pw")
        workspaces.set_member(info["id"], account_of(name), role, by=account_of(OWNER))
        return login(name, "pw")
    clients = [login(OWNER, "pw"), member("vr_bob", "editor"), member("vr_vera", "viewer")]
    for c in clients:
        c.params = {"ws": info["id"]}
    return info["id"], *clients


def test_who_may_use_the_vault_routes(lab, guest, disk, over_http):
    ws, alice, bob, vera = lab
    root = disk / "Shared"
    link = _vault(alice, root, {"S.md": "shared\n"})
    page, url = _page_of(link, "S.md"), f"/api/folder-links/{link['id']}"
    clash = _clash(alice, disk, "Shared clash")[0]["id"]

    # a viewer reads, never writes, and is told no path
    assert vera.get(url).status_code == 200 and vera.get(f"{url}/conflicts").status_code == 200
    assert _state(vera, page)["vault"]["path"] == "" and _state(bob, page)["vault"]["path"]
    for method, path, body in (("PATCH", url, {"paused": True}), ("POST", f"{url}/sync", None), ("DELETE", url, None),
                               ("GET", f"{url}/removal", None), ("POST", f"/api/folder-links/{clash}/conflicts/1",
                                                                  {"choice": "keep"}),
                               ("GET", f"/api/folder-links/page/{page}/file", None),
                               ("PUT", f"/api/folder-links/page/{page}/file", {"text": "x", "hash": "y"}),
                               ("POST", "/api/folder-links/preview", {"path": str(disk / "v")}),
                               ("POST", f"{url}/relink", {"path": str(root)})):
        assert vera.request(method, path, json=body).status_code == 403, (method, path)
    assert bob.get(f"{url}/removal").status_code == 200 and bob.get(f"/api/folder-links/page/{page}/file").status_code == 200

    # a guest, a token, another account, nobody
    token = create_token(account_of(OWNER), ws, "vr", 1)["token"]
    assert guest.get(url).status_code == 403 and guest.get(f"/api/folder-links/page/{page}").status_code == 403
    tokened = fresh_client()
    for path in (url, f"/api/folder-links/page/{page}", f"{url}/conflicts"):
        assert tokened.get(path, headers={"Authorization": f"Bearer {token}"}).status_code == 403, path
    make_user("vr_stranger", "pw")
    stranger = login("vr_stranger", "pw")
    assert stranger.get(url).status_code == 404 and stranger.get(f"{url}/conflicts").status_code == 404
    assert stranger.delete(url).status_code == 404 and stranger.get(f"{url}/removal").status_code == 404
    assert stranger.get(f"/api/folder-links/page/{page}", params={"ws": ws}).status_code == 403
    assert fresh_client().get(url).status_code == 401 and fresh_client().post("/api/folder-links/hint").status_code == 401

    # a link to another server is its maker's alone
    far_token = create_token(account_of(OWNER), workspace_of(OWNER), "vr-far", 1)
    far = _create(alice, folder="root", path=str(disk / "Far"), remote_url=over_http, token=far_token["token"],
                  token_id=far_token["id"])
    assert alice.get(f"/api/folder-links/{far['id']}").status_code == 200
    assert alice.get(f"/api/folder-links/{far['id']}/conflicts").json() == {"conflicts": []}
    assert alice.get(f"/api/folder-links/{far['id']}/removal").json()["in_place"] == 0
    assert bob.get(f"/api/folder-links/{far['id']}").status_code == 404
    assert far["id"] not in [l["id"] for l in bob.get("/api/folder-links").json()["links"]]
    r = alice.post("/api/folder-links", json={"folder": "root", "path": str(disk / "Far 2"), "remote_url": over_http,
                                              "token": far_token["token"], "push": "everything"})
    assert r.status_code == 400 and "token that may write" in r.json()["detail"]


@pytest.mark.parametrize("role", ["viewer", "none"])
def test_the_account_that_made_a_vault_sees_pauses_and_removes_it_whatever_its_role_now(lab, disk, role):
    """The maker made a viewer saw its own vault with no path and could
    neither pause it nor remove it, and no one else could; removed from the
    workspace it saw nothing of it, and the vault held its directory against
    any vault made there again. The directory is on that account's computer;
    what the vault holds of the workspace is still the members'."""
    ws, alice, bob, _vera = lab
    root = disk / f"Bobs {role}"
    link = _vault(bob, root, {"B.md": "bob's note\n"})
    url, bob_id = f"/api/folder-links/{link['id']}", account_of("vr_bob")
    if role == "viewer":
        workspaces.set_member(ws, bob_id, "viewer", by=account_of(OWNER))
    else:
        workspaces.remove_member(ws, bob_id)
    try:
        listed = {l["id"]: l for l in bob.get("/api/folder-links").json()["links"]}
        assert listed[link["id"]]["path"] == link["path"] and bob.get(url).json()["path"] == link["path"]
        if role == "viewer":
            assert _state(bob, _page_of(link, "B.md"))["vault"]["path"].endswith("B.md")
        assert bob.patch(url, json={"paused": True}).status_code == 200
        assert bob.get(f"{url}/removal").status_code == 200
        if role == "none":
            assert bob.get(f"{url}/conflicts").status_code == 403
        assert alice.delete(url).status_code == 403
        assert bob.delete(url).status_code == 200 and folder_links.get_link(link["id"]) is None
    finally:
        workspaces.set_member(ws, bob_id, "editor", by=account_of(OWNER))
    r = bob.post("/api/folder-links", json={"path": link["path"], "new_folder": True, "push": "off"},
                 params={"ws": workspace_of("vr_bob")})
    assert r.status_code == 201, r.text


def test_the_routes_that_take_a_path_take_the_shells_secret(owner, disk, monkeypatch):
    link = _vault(owner, disk / "Secret", {"s.md": "s\n"})
    page = _page_of(link, "s.md")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-secret")
    for path, body in (("/api/folder-links", {"path": str(disk / "x"), "new_folder": True}),
                       ("/api/folder-links/preview", {"path": str(disk / "x")}),
                       (f"/api/folder-links/{link['id']}/relink", {"path": str(disk / "Secret")}),
                       ("/api/folder-links/open", {"path": str(disk / "x.pdf")}),
                       ("/api/folder-links/opened/p/locate", {"path": str(disk / "x.pdf")})):
        assert owner.post(path, json=body).status_code == 403, path
        assert owner.post(path, json=body, headers={"X-Gamma-Shell": "wrong"}).status_code == 403, path
    r = owner.post("/api/folder-links/preview", json={"path": str(disk / "x")}, headers={"X-Gamma-Shell": "the-secret"})
    assert r.status_code == 200
    r = owner.post(f"/api/folder-links/{link['id']}/relink", json={"path": str(disk / "Secret")},
                   headers={"X-Gamma-Shell": "the-secret"})
    assert r.status_code == 200
    # no path, no secret
    assert owner.get(f"/api/folder-links/page/{page}").status_code == 200
    assert owner.get(f"/api/folder-links/{link['id']}/removal").status_code == 200
    assert owner.post("/api/folder-links/hint").status_code == 200
    got = owner.get(f"/api/folder-links/page/{page}/file").json()
    assert owner.put(f"/api/folder-links/page/{page}/file", json={"text": "t\n", "hash": got["hash"]}).status_code == 200


def test_no_other_server_answers_the_vault_routes(owner, disk, monkeypatch):
    link = _vault(owner, disk / "Here", {"h.md": "h\n"})
    page = _page_of(link, "h.md")
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    for method, url in (("POST", "/api/folder-links/preview"), ("POST", "/api/folder-links/hint"),
                        ("GET", f"/api/folder-links/{link['id']}/removal"), ("GET", f"/api/folder-links/{link['id']}/conflicts"),
                        ("POST", f"/api/folder-links/{link['id']}/relink"), ("GET", f"/api/folder-links/page/{page}/file")):
        r = owner.request(method, url, json={"path": str(disk)} if method == "POST" else None)
        assert r.status_code == 404 and "desktop app" in r.json()["detail"], url
    assert publish.state(account_of(OWNER), link["workspace_id"], page).get("reason") != publish.IN_VAULT
    with folder_links._dirty_guard:
        folder_links._dirty.clear()
    commit_ops(link["workspace_id"], page, [{"op": "set", "id": page, "content": "renamed"}], actor=account_of(OWNER))
    assert (link["workspace_id"], page) not in folder_links._dirty


# --- counter-examples, a second set ----------------------------------------------------------------------

def _insert(link, page, content):
    commit_ops(link["workspace_id"], page, [{"op": "insert", "id": new_block_id(), "parent": page, "content": content,
                                             "props": {}}], actor=link["created_by"])


def test_a_force_push_is_refused_where_pushing_everything_is(owner, disk):
    """A vault that pushes PDFs in a workspace a Zotero link keeps wrote its
    notes into Gamma when forced to push: a force push is pushing everything."""
    root = disk / "Zot"
    root.mkdir(parents=True)
    link = _create(owner, path=str(root), new_folder=True, push="pdfs")
    page = make_page(owner, "Gamma page", {"folders": [link["folder_id"]]})["id"]
    _insert(link, page, "gamma text")
    _sync(owner, link["id"])
    with connect_users_db() as conn:
        conn.execute("INSERT INTO zotero_links (workspace_id, owner, library, mode, created_at) "
                     "VALUES (?, ?, 'users/vr', 'off', '2026-01-01T00:00:00Z')", (link["workspace_id"], account_of(OWNER)))
    try:
        _put(root / "Gamma page.md", "text from disk\n", age=5)
        r = owner.post(f"/api/folder-links/{link['id']}/sync", params={"wait": 1, "force": "push"})
        assert r.status_code == 400 and "cannot push everything" in r.json()["detail"]
        status = folder_links.run_link(link["id"], force="push")       # one asked before the link came: stopped
        assert "cannot push everything" in status["stopped"] and _texts(link, page) == ["gamma text"]
        assert _sync(owner, link["id"])["last_error"] == ""            # pulling and PDFs go on
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM zotero_links WHERE workspace_id = ?", (link["workspace_id"],))


def test_a_folder_that_only_pulls_takes_no_page_from_a_vault(owner, disk):
    """A pull-only "Sync with this computer" of a subfolder of a pushing
    vault, or of a folder a page of it is filed in too, took those pages out
    of the vault: the person's notes went to its trash, new ones every round."""
    reading = make_folder(owner, "VR reading list")
    _create(owner, folder=reading, path=str(disk / "Reading mirror"))                 # pulls only, made first
    root = disk / "Pushing"
    link = _vault(owner, root, {"top.md": "top\n", "Projects/plan.md": "the person's plan\n", "idea.md": "an idea\n"},
                  library=True)                       # its files in the library, filed in library folders too
    _create(owner, folder=vault_store.dirs(link["id"])["Projects"]["folder_id"], path=str(disk / "Mirror"))  # below
    idea = _page_of(link, "idea.md")
    commit_ops(link["workspace_id"], idea, [{"op": "set", "id": idea, "props": {
        "folders": [*_props(link, idea)[FOLDERS], reading]}}], actor=link["created_by"])
    _sync(owner, link["id"])
    _put(root / "Projects" / "plan.md", "the person's plan\n\nand a second line\n", age=5)
    _put(root / "Projects" / "new.md", "a note written after the mirror\n", age=5)
    for _ in range(2):
        assert _sync(owner, link["id"])["counts"]["removed"] == 0
    notes = sorted(p.relative_to(root).as_posix() for p in root.rglob("*.md") if ".gamma" not in p.parts)
    assert notes == ["Projects/new.md", "Projects/plan.md", "idea.md", "top.md"]
    assert "and a second line" in _texts(link, _page_of(link, "Projects/plan.md"))
    assert _state(owner, idea)["vault"]["id"] == link["id"]


def test_a_member_removed_from_the_workspace_receives_nothing_more_of_it(lab, disk):
    """A pull-only folder link of a member who was then removed kept writing
    the workspace into that member's directory, on every tick, and the
    status of each round it kept told that member the folder's names as they
    are now, a rename of a folder above it too."""
    ws, alice, _bob, _vera = lab
    make_user("vr_leaver", "pw")
    workspaces.set_member(ws, account_of("vr_leaver"), "editor", by=account_of(OWNER))
    leaver = login("vr_leaver", "pw")
    leaver.params = {"ws": ws}
    folder = make_folder(alice, "VR team/VR notes")
    page = make_page(alice, "VR plan", {"folders": [folder]})["id"]
    commit_ops(ws, page, [{"op": "insert", "id": new_block_id(), "parent": page, "content": "first", "props": {}}],
               actor=account_of(OWNER))
    root = disk / "Leaver's copy"
    link = _create(leaver, folder=folder, path=str(root))
    assert _sync(leaver, link["id"])["folder_path"] == ["VR team", "VR notes"]
    workspaces.remove_member(ws, account_of("vr_leaver"))
    commit_ops(ws, page, [{"op": "insert", "id": new_block_id(), "parent": page, "content": "after the member left",
                           "props": {}}], actor=account_of(OWNER))
    commit_ops(ws, FOLDERS, [{"op": "set", "id": make_folder(alice, "VR team"), "content": "VR merger"}],
               actor=account_of(OWNER))
    status = folder_links.run_link(link["id"])
    texts = " ".join(p.read_text(encoding="utf-8") for p in root.rglob("*.md"))
    assert "first" in texts and "after the member left" not in texts and "no access" in status["last_error"]
    assert status["folder_path"] == ["VR team", "VR notes"]
    assert leaver.get(f"/api/folder-links/{link['id']}").json()["status"]["folder_path"] == ["VR team", "VR notes"]


def _member(ws, name):
    """A member made an editor of ``ws``, asking in it."""
    make_user(name, "pw")
    workspaces.set_member(ws, account_of(name), "editor", by=account_of(OWNER))
    c = login(name, "pw")
    c.params = {"ws": ws}
    return c


def test_a_member_removed_is_told_nothing_of_the_workspace_by_its_vault_s_refusals(lab, disk):
    """The round of a vault whose maker had left the workspace asked
    ``check_vault`` first, whose reasons read the workspace as it is now and
    name other vaults' places: the leaver read in its status that its folder
    had been moved under another vault's, and the directory that vault was
    found at after the leaver left. It is told it has no access, and nothing
    else."""
    ws, alice, bob, _vera = lab
    leaver = _member(ws, "vr_leaver2")
    top, side = make_folder(alice, "VR top"), make_folder(alice, "VR side")
    bobs = _create(bob, folder=top, path=str(disk / "Bob's notes"), push="pdfs")
    mine = _create(leaver, folder=side, path=str(disk / "Leaver's papers"), push="pdfs")
    assert _sync(leaver, mine["id"])["last_error"] == ""
    workspaces.remove_member(ws, account_of("vr_leaver2"))
    os.rename(disk / "Bob's notes", disk / "Bob's new place")
    folder_links.relink(bobs["id"], str(disk / "Bob's new place"))
    commit_ops(ws, FOLDERS, [{"op": "move", "id": side, "parent": top}], actor=account_of(OWNER))
    status = _sync(leaver, mine["id"])
    seen = json.dumps(leaver.get(f"/api/folder-links/{mine['id']}").json())
    assert "no access" in status["last_error"] and not status.get("stopped"), status
    assert "Bob's" not in seen and "pushes into" not in seen and status["folder_path"] == ["VR side"]


def test_a_maker_who_only_views_now_is_told_no_other_vault_s_place(lab, disk):
    """A vault whose maker was made a viewer of its workspace: ``check_vault``'s
    reasons, in its round's status and in the answers to the maker's own
    changes, named the place of a vault another member keeps, which the API
    keeps from a viewer. Such a maker is told only that the vault is
    refused; an editor is still told why."""
    ws, alice, bob, _vera = lab
    maker = _member(ws, "vr_demoted")
    top, side = make_folder(alice, "VR demoted top"), make_folder(alice, "VR demoted side")
    _create(bob, folder=top, path=str(disk / "Bob's shelf"), push="pdfs")
    mine = _create(maker, folder=side, path=str(disk / "Demoted papers"), push="pdfs")
    assert _sync(maker, mine["id"])["last_error"] == ""
    commit_ops(ws, FOLDERS, [{"op": "move", "id": side, "parent": top}], actor=account_of(OWNER))
    assert "Bob's shelf" in _sync(maker, mine["id"])["stopped"]                 # an editor is told why
    workspaces.set_member(ws, account_of("vr_demoted"), "viewer", by=account_of(OWNER))
    status = _sync(maker, mine["id"])
    assert status["stopped"] == status["last_error"] == folder_links.REFUSED_UNSAID, status
    answers = [maker.patch(f"/api/folder-links/{mine['id']}", json={"push": "everything"}),
               maker.post(f"/api/folder-links/{mine['id']}/sync", params={"force": "push"}),
               maker.post(f"/api/folder-links/{mine['id']}/relink", json={"path": str(disk / "Demoted papers")})]
    for r in answers:
        assert r.status_code == 400 and r.json()["detail"] == folder_links.REFUSED_UNSAID, r.text
    listed = maker.get(f"/api/folder-links/{mine['id']}").json()
    seen = json.dumps([listed, maker.get("/api/folder-links").json()])
    assert "Bob's" not in seen and "pushes into" not in seen
    assert listed["path"] == mine["path"]                                         # its own place is its maker's


def test_a_member_removed_learns_nothing_of_a_page_from_its_file_vault(lab, disk):
    """A file vault of a member removed from the workspace said, at the next
    change of its file, that the page was no longer in Gamma for a page
    deleted since, that it might no longer edit for one still there, and
    nothing for a file of the bytes the page holds now: it told whether a
    page stood, and what it held. Every file reads the same."""
    ws, alice, _bob, _vera = lab
    leaver = _member(ws, "vr_leaver3")
    links = {}
    for name in ("kept", "gone", "same"):
        data = _pdf(f"VR the lab's {name} paper")
        doc = storage.content_digest(data)
        storage.put_upload(ws, f"{doc}.pdf", data)
        page = make_page(alice, f"VR followed {name}", doc_props(doc))["id"]
        held = data if name == "same" else _pdf(f"VR the leaver's {name} file")
        links[name] = (page, _create(leaver, kind="file", page=page, path=str(_put(disk / f"{name}.pdf", held))))
    workspaces.remove_member(ws, account_of("vr_leaver3"))
    with connect_pages_db(ws) as conn:
        delete_page(ws, conn, links["gone"][0], actor=account_of(OWNER))
    for name, (_, link) in links.items():
        status = _sync(leaver, link["id"])
        assert status["last_error"] == "you may no longer edit this workspace", (name, status)
        assert leaver.get(f"/api/folder-links/{link['id']}").json()["status"]["last_error"] == status["last_error"]


def test_a_request_that_misses_the_lock_as_the_round_lets_it_go_still_runs(owner, disk, monkeypatch):
    """A request whose try for the lock failed just before the round let it
    go, and that kept itself just after the round looked at ``_again``, was
    left with nobody to run it. Here the round finishes inside that moment
    whenever nothing stops it."""
    link = _vault(owner, disk / "Racing", {"r.md": "r\n"})
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(kw))
    lock, asked, done = folder_links._lock(link["id"]), threading.Event(), threading.Event()

    class Racing:
        """The link's lock: a try that fails lets the round finish first."""

        def acquire(self, blocking=True, timeout=-1):
            if threading.current_thread().name == "vr-round":
                return lock.acquire(blocking, timeout)
            got = lock.acquire(blocking=False)
            if not got:
                asked.set()
                done.wait(1)
            return got

        def release(self):
            lock.release()

        def locked(self):
            return lock.locked()
    real_round = folder_links._vault_round

    def round_waits(*args, **kwargs):
        asked.wait(5)
        return real_round(*args, **kwargs)
    monkeypatch.setattr(folder_links, "_lock", lambda link_id: Racing())
    monkeypatch.setattr(folder_links, "_vault_round", round_waits)
    first = threading.Thread(target=lambda: (folder_links.run_link(link["id"]), done.set()), name="vr-round")
    first.start()
    for _ in range(200):
        if lock.locked():
            break
        time.sleep(0.01)
    folder_links.run_link(link["id"], force="pull")
    first.join(10)
    assert started == [{"force": "pull", "direction": "both", "confirm": False}]


def test_a_tick_while_a_new_vault_takes_in_its_file_vaults_runs_no_round(owner, disk, monkeypatch):
    """The take-in ran outside the link's lock: a tick in between read the
    file vault's PDF as new and made it a second page."""
    root = disk / "Taking busy"
    f = _put(root / "papers" / "Followed.pdf", _pdf("followed, then taken in"))
    up = owner.post("/api/uploads", files={"file": ("f.pdf", f.read_bytes(), "application/pdf")}).json()
    page = make_page(owner, "VR taken in busy", doc_props(up["doc_id"], name="Followed.pdf"))["id"]
    _create(owner, kind="file", page=page, path=str(f))
    due, real = [], folder_links._take_in_one

    def taking(link, remote, other):
        due.append(REAL_DUE(folder_links.get_link(link["id"])))
        real(link, remote, other)
    monkeypatch.setattr(folder_links, "_take_in_one", taking)
    link = _create(owner, path=str(root), new_folder=True, push="everything")
    assert due == [""] and _sync(owner, link["id"])["last_error"] == ""
    assert [r["page_id"] for r in vault_store.files(link["id"]).values() if r["kind"] == "pdf"] == [page]


def test_an_open_page_whose_kept_note_moved_has_a_round_once_not_every_tick(owner, disk, monkeypatch):
    """A note kept (changed elsewhere, not pushed) keeps its row's stat, so its
    open page had a round every 2 s for as long as it stayed open."""
    root = disk / "Kept open"
    link = _vault(owner, root, {"k.md": "k\n", "other.md": "o\n"}, library=True)
    owner.patch(f"/api/folder-links/{link['id']}", json={"push": "pdfs"}).raise_for_status()
    page = _page_of(link, "k.md")
    _put(root / "k.md", "k, changed in another editor\n", age=5)
    assert _sync(owner, link["id"])["kept"]
    monkeypatch.setattr(collab, "open_pages", lambda: [(link["workspace_id"], page)])
    rounds, real = [], folder_links.run_page
    monkeypatch.setattr(folder_links, "run_page", lambda *a: rounds.append(a) or real(*a))
    for _ in range(5):
        folder_links.hot_tick()
    assert len(rounds) == 1
    _put(root / "k.md", "k, changed again\n", age=4)
    folder_links.hot_tick()
    assert len(rounds) == 2


# --- counter-examples, a third set -----------------------------------------------------------------------

def test_only_the_vaults_maker_changes_what_its_disk_sends_forces_it_or_removes_it(lab, disk):
    """Any editor of the workspace could turn a vault of the maker's
    computer to push everything (its private notes into the shared
    workspace), force a pull over what the maker kept on disk, or remove it."""
    ws, alice, bob, _vera = lab
    root = disk / "Alice private"
    _put(root / "diary.md", "alice's private diary\n")
    link = _create(alice, path=str(root), new_folder=True, push="pdfs")
    _sync(alice, link["id"])
    url = f"/api/folder-links/{link['id']}"
    for method, path, body, params in (("PATCH", url, {"push": "everything"}, {}), ("PATCH", url, {"pull": False}, {}),
                                       ("PATCH", url, {"notes": True}, {}), ("POST", f"{url}/sync", None, {"force": "pull"}),
                                       ("POST", f"{url}/sync", None, {"confirm": 1}), ("DELETE", url, None, {}),
                                       ("POST", f"{url}/relink", {"path": str(root)}, {})):
        r = bob.request(method, path, json=body, params={"ws": ws, **params})
        assert r.status_code == 403, (method, path, params)
    assert bob.patch(url, json={"paused": True}).status_code == 200 and bob.post(f"{url}/sync").status_code == 200
    assert alice.patch(url, json={"paused": False, "push": "off"}).json()["push"] == "off"
    pages = LocalRemote(ws, link["folder_id"], link["created_by"]).layout().pages
    assert folder_links.get_link(link["id"]) and not any("private diary" in t for p in pages for t in _texts(link, p))


def test_a_directory_vault_takes_in_no_file_vault_of_another_account(lab, disk):
    """Alice's directory vault around a file Bob keeps pushing took his file
    vault in: his link deleted without his say, which only he may remove,
    and his page and file her vault's. It is refused, and his stays his."""
    ws, alice, bob, _vera = lab
    root = disk / "Shared take"
    f = _put(root / "paper.pdf", _pdf("bob's followed paper"))
    up = bob.post("/api/uploads", files={"file": ("p.pdf", f.read_bytes(), "application/pdf")}).json()
    page = make_page(bob, "Bob paper", doc_props(up["doc_id"], name="paper.pdf"))["id"]
    file_link = _create(bob, kind="file", page=page, path=str(f))
    r = alice.post("/api/folder-links", json={"path": str(root), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and "another account" in r.json()["detail"]
    assert folder_links.get_link(file_link["id"])["created_by"] == account_of("vr_bob")
    assert bob.get(f"/api/folder-links/{file_link['id']}").status_code == 200
    link = _create(bob, path=str(root), new_folder=True, push="everything")       # his own takes it in
    assert folder_links.get_link(file_link["id"]) is None and vault_store.files(link["id"])["paper.pdf"]["page_id"] == page


def test_the_file_of_a_vault_that_pushes_no_notes_is_read_on_its_makers_computer_only(lab, disk):
    """``GET /page/{id}/file`` gave another editor what the maker kept off
    Gamma: a note of a vault that pushes PDFs only, changed on the maker's
    disk, with the path it is at."""
    ws, alice, bob, _vera = lab
    root = disk / "Alice copy"
    root.mkdir(parents=True)
    link = _create(alice, path=str(root), new_folder=True, push="pdfs")
    page = make_page(alice, "Plan b", {"folders": [link["folder_id"]]})["id"]
    commit_ops(ws, page, [{"op": "insert", "id": new_block_id(), "parent": page, "content": "the team's plan",
                           "props": {}}], actor=account_of(OWNER))
    _sync(alice, link["id"])
    _put(root / "Plan b.md", "the team's plan\n\nalice's own aside, kept on her computer\n", age=5)
    assert _sync(alice, link["id"])["kept"]
    r = bob.get(f"/api/folder-links/page/{page}/file")
    assert r.status_code == 403 and "own aside" not in r.text
    assert "own aside" in alice.get(f"/api/folder-links/page/{page}/file").json()["text"]


def test_a_vault_that_purges_a_pdf_another_page_reads_in_place_leaves_it_there(owner, disk, monkeypatch):
    """A PDF opened with *Open with* (a reader page, never stored) and later
    inside a vault: the vault's page of it went to Recently deleted and was
    purged, and the sweep took the reader page's only copy from the disk. A
    work folder's PDF is the disk's: its page in Recently deleted (a mirror
    sent it there) keeps the file where it is, and the purge forgets it."""
    from gamma import trash

    root = disk / "Papers"
    f = _put(root / "x.pdf", _pdf("the person's only copy, opened with Open with"))
    reader = owner.post("/api/folder-links/open", json={"path": str(f)}).json()["page"]
    link = _vault(owner, root, {"n.md": "note\n"})
    ws, page = link["workspace_id"], _page_of(link, "x.pdf")
    assert page != reader
    with connect_pages_db(ws) as conn:
        trash_page(ws, conn, page, actor=link["created_by"], client=SYNC_CLIENT)
    _sync(owner, link["id"])
    trash.purge(ws, page, actor=link["created_by"])
    monkeypatch.setattr(vault_disk, "TRASH_DAYS", 0)                   # thirty days later
    for _ in range(2):
        _sync(owner, link["id"])
    assert [p.relative_to(root).as_posix() for p in root.rglob("*.pdf")] == ["x.pdf"] and f.read_bytes()
    assert _state(owner, reader)["reader"]["missing"] is False


def test_a_vault_around_gammas_data_directory_is_refused(owner, disk):
    """Only a place inside the data directory was refused: a vault around it
    read every workspace's stored PDFs in place and moved them to its trash."""
    around = config.DATA_DIR.resolve().parent
    r = owner.post("/api/folder-links/preview", json={"path": str(around)})
    assert r.status_code == 400 and "data directory" in r.json()["detail"]
    r = owner.post("/api/folder-links", json={"path": str(around), "new_folder": True, "push": "everything"})
    assert r.status_code == 400 and "data directory" in r.json()["detail"]


def test_an_older_copy_of_the_vault_at_its_place_stops_it_until_relinked(owner, disk):
    """A backup disk given the vault's drive letter holds the vault's marker
    too: its rounds pushed last week's text over Gamma's and trashed the pages
    made since. Every file id differs, the marker's first."""
    root = disk / "E"
    link = _vault(owner, root, {f"n{i}.md": f"note {i} as it was\n" for i in range(10)})
    backup = disk / "Backup"
    shutil.copytree(root, backup)
    page = _page_of(link, "n0.md")
    _edit(link, page, _find(link, page, "note 0 as it was"), "a week of work typed in gamma")
    newer = _made_in(owner, "Written after the backup", link["folder_id"])
    _insert(link, newer, "new since the backup")
    _sync(owner, link["id"])
    os.rename(root, disk / "E unplugged")
    os.rename(backup, root)
    for _ in range(2):
        status = _sync(owner, link["id"])
        assert "copy" in status["stopped"] and status["counts"]["sent"] == 0
    assert _texts(link, page) == ["a week of work typed in gamma"] and _texts(link, newer) == ["new since the backup"]
    owner.post(f"/api/folder-links/{link['id']}/relink", json={"path": str(root)}).raise_for_status()
    assert not _sync(owner, link["id"]).get("stopped")


def test_a_round_asked_for_while_a_relink_holds_the_vault_starts_when_it_lets_go(owner, disk, monkeypatch):
    """``_let_go`` releases every holder of a link's lock: a relink, the
    making of a vault, a removal and the tick's forgetting held it with a
    plain ``with``, and a force asked for meanwhile waited for some later
    round, or for ever."""
    link = _vault(owner, disk / "Relinking", {"r.md": "r\n"})
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(kw))
    real = LocalRemote.relink

    def asking(self, old, new):
        folder_links.run_link(link["id"], force="pull", confirm=True)      # the relink holds the lock: kept
        return real(self, old, new)
    monkeypatch.setattr(LocalRemote, "relink", asking)
    folder_links.relink(link["id"], str(disk / "Relinking"))
    assert started[0] == {"force": "pull", "direction": "both", "confirm": True}
    assert link["id"] not in folder_links._again

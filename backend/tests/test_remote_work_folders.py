"""A work folder of another Gamma server (gamma/folder_links.py
``work_vault``, ``create_remote_link`` with ``choose``; step 4b of
docs/research/agent-collab.md), over a TestClient that stands in for that
server (test_folder_links.py ``over_http``), while this computer's own
in-process reads and writes of that workspace fail (``far``): made of an
empty folder there and refused otherwise, chosen in the tracking dialog here
(what goes there, its secrets), its library switch written to the mark there,
cancelled, removed (its files kept as pages there or sent to Recently deleted,
refused and forced while that server does not answer, a cancel too, the mark
said to stay when it stops answering midway), a mark whose answer was lost taken back, the questions between
rounds waiting as questions, stopped for good once that server let go of it
(never marking, pushing into or releasing that folder again), the remote kept folders
that push everything left as they were; the annotation files of such a work
folder and of a followed file there, read from that server with the vault's
token (gamma/annotations/files.py); the remote's own ``stamp`` and
``work_pages`` against ``LocalRemote``'s, a ``locked`` refusal as the file's,
a round of push alone that reads no listing. And every server's side: a
vault's mark refused in a clone or a Zotero-linked workspace and inside or
around a work folder (gamma/ops.py ``mark_rule``), ``pushing_vault`` seeing
a mark, whoami's ``work_folders``, the summaries' counts, and the Detach of a work
folder kept on no computer here (``POST /api/folders/{id}/detach``: for the
workspace's owner and the account whose computer keeps it, while it may edit
the workspace, which is woken, its paused vaults left to their next round;
refused in a clone or a Zotero-linked workspace, sweeping again once the mark
is off)."""

import io
import json
import os
import time

import pymupdf
import pytest

from conftest import (account_of, doc_props, fresh_client, login, make_folder, make_page, make_user, mark_work_folder,
                      thread_on, work_props, workspace_of)
from gamma import config, folder_links, gamma_sync, workspaces
from gamma.auth import DISK_CLIENT
from gamma.computers import channel, registry
from gamma.blocks_store import FOLDERS, create_page, new_block_id
from gamma.db import connect_pages_db, connect_users_db
from gamma.annotations import files as annotation_files
from gamma.integrations import create_token
from gamma.local_agents import runner
from gamma.ops import LOCKED, OpError, commit_ops
from gamma.vault import disk as vault_disk, http_remote as vault_http, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from gamma.vault.remote import Edit
from gamma.work_folders import elsewhere, selection, takeover
from test_folder_links import _create, _sync, disk, over_http  # noqa: F401: the disk and over_http fixtures
from test_highlight_relocate import _position
from test_vault_sync import insert

OWNER = "rwf_owner"
_made = []    # the tokens a test made, revoked after it: an account holds twenty at most


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def settled(monkeypatch):
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    yield
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id IN (SELECT value FROM json_each(?))",
                     (json.dumps(_made),))
    _made.clear()


@pytest.fixture
def far(monkeypatch, owner):
    """The owner's workspace is the other server's: this computer's own
    in-process reads and writes of it (``folder_links``', ``selection``'s
    and the feedback files' ``connect_pages_db``, ``folder_links``'
    ``commit_ops``) fail, so a path that reads the wrong database fails
    though both servers share one data directory here. The routes that
    stand in for the other server keep their own."""
    ws = workspace_of(OWNER)

    def guarded(fn):
        def call(at, *args, **kwargs):
            assert at != ws, "this computer read or wrote the other server's workspace in-process"
            return fn(at, *args, **kwargs)
        return call

    for module in (folder_links, selection, annotation_files):
        monkeypatch.setattr(module, "connect_pages_db", guarded(module.connect_pages_db))
    monkeypatch.setattr(folder_links, "commit_ops", guarded(folder_links.commit_ops))
    return ws


def _token():
    token = create_token(account_of(OWNER), workspace_of(OWNER), "rwf-write", 1, scope="write")
    _made.append(token["id"])
    return token


def _put(path, data, age: float = 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _body(server, folder, root, **extra):
    token = _token()
    return {"folder": folder, "path": str(root), "remote_url": server, "token": token["token"], "token_id": token["id"],
            "push": "everything", "pull": True, "choose": True, **extra}


def _work(c, server, folder, root, files=None, **extra):
    """A work folder of the folder ``folder`` there made from ``root``
    holding ``files`` ({path: text}): the link as the API answers it."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in (files or {}).items():
        _put(root / rel, data)
    return _create(c, **_body(server, folder, root, **extra))


def _mark(ws, folder):
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (folder,)).fetchone()
    return json.loads(row[0]).get("vault") if row else None


def _props(ws, page_id) -> dict:
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT parent_id, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    return {"parent": row[0], **json.loads(row[1])} if row else {}


def _files_of(ws, w) -> dict:
    with connect_pages_db(ws) as conn:
        return {r[0]: json.loads(r[1]) for r in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE work_folder = ? AND parent_id = 'root'", (w,))}


def _selection(base="preset", changes=()):
    return {"base": base, "changes": list(changes)}


def _preview(c, link, base="preset", changes=()):
    r = c.post(f"/api/folder-links/{link['id']}/selection/preview", json={"selection": _selection(base, changes)})
    assert r.status_code == 200, r.text
    return r.json()


def _save(c, link, base="preset", changes=(), **body):
    r = c.put(f"/api/folder-links/{link['id']}/selection",
              json={"selection": _selection(base, changes), "base_hash": "", **body})
    assert r.status_code == 200, r.text
    return r.json()


def _remote(link_id):
    keyed = folder_links.get_link(link_id, with_token=True)
    return vault_http.HttpRemote(keyed, folder_links.remote_server(keyed))


def _saved(c, server, folder, root, files, changes=(), **body):
    """A work folder made, its files chosen (the preset and ``changes``)
    and its first round run: the link."""
    link = _work(c, server, folder, root, files)
    _save(c, link, changes=changes, **body)
    status = _sync(c, link["id"])
    assert status["last_error"] == "", status
    return folder_links.get_link(link["id"])


# --- making one, choosing its files, removing it ---------------------------------------------------

def test_a_work_folder_of_another_server_is_made_of_an_empty_folder_there_and_chosen_here(owner, disk, over_http,
                                                                                          far, monkeypatch):
    monkeypatch.setattr(config, "vault_keep_bytes", lambda: 20)
    w = make_folder(owner, "RWF lab")
    root = disk / "RWF lab"
    files = {"notes.md": "a thought\n", "data.csv": "a,b\n" * 20, ".env": "KEY=1\n", "small.txt": "tiny\n"}
    link = _work(owner, over_http, w, root, files, library=False)
    assert link["choosing"] is True and link["work"] is True and link["status"]["paused_at"]
    assert link["remote_url"] == over_http and "token" not in link
    state = folder_links.get_link(link["id"])["state"]
    assert state["made"] == {"folder": False, "marker": True, "marker_dir": True} and state[takeover.TAKEN_OVER]
    assert _mark(far, w) == {"pull": True, "library": False}            # written there, by the vault's token
    assert (root / vault_disk.MARKER).is_file()

    # the dialog runs here, its maker's alone: another account learns nothing of it
    tree = owner.get(f"/api/folder-links/{link['id']}/tree", params={"base": "preset"}).json()
    assert {e["path"] for e in tree["entries"]} >= set(files)
    make_user("rwf_stranger", "pw")
    stranger = login("rwf_stranger", "pw")
    assert stranger.get(f"/api/folder-links/{link['id']}/tree").status_code == 404
    assert stranger.post(f"/api/folder-links/{link['id']}/selection/preview",
                         json={"selection": _selection()}).status_code == 404

    # what goes there: every ticked file's bytes but the large ones', and the secrets among them, by name
    ticks = [{"path": "data.csv", "on": True}, {"path": ".env", "on": True}, {"path": "small.txt", "on": True}]
    seen = _preview(owner, link, changes=ticks)
    sizes = {rel: (root / rel).stat().st_size for rel in files}
    assert seen["files"] == 4 and seen["large"] == 1                     # data.csv, past the keep limit
    assert seen["uploads"] == {"files": 3, "bytes": sizes["notes.md"] + sizes[".env"] + sizes["small.txt"]}
    assert seen["secrets"] == [".env"] and seen["leaving"] == []

    # saved with the library switch: the mark there says so, and the first round fills the folder there
    _save(owner, link, changes=ticks[2:], library=True)
    assert _mark(far, w) == {"pull": True, "library": True}
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["work_folder_at"]
    there = _files_of(far, w)
    assert sorted(p["file_name"] for p in there.values()) == ["notes.md", "small.txt"]
    assert {p["file_name"]: p.get("library") for p in there.values()} == {"notes.md": True, "small.txt": None}
    assert owner.get(f"/api/folders/{w}/pages").json()["total"] == 2

    # unticking a file says what leaves, its notes and threads as that server counts them
    note = next(i for i, p in there.items() if p["file_name"] == "notes.md")
    seen = _preview(owner, link, base="file", changes=[{"path": "notes.md", "on": False}])
    assert [(x["path"], x["page"], x["notes"], x["open_comments"]) for x in seen["leaving"]] == [
        ("notes.md", note, 1, 0)]

    # a round of push alone with nothing to push asks that server no listing
    asked = []
    opened = gamma_sync.default_open

    def counting(method, path, headers, body=None):
        asked.append(path)
        return opened(method, path, headers, body)

    monkeypatch.setattr(gamma_sync, "default_open", counting)
    status = _sync(owner, link["id"], direction="push")
    assert status["last_error"] == "" and not status["counts"]["sent"], status
    assert not [p for p in asked if "/api/blocks/root/children" in p or "/api/sync/changes?since=" in p], asked
    monkeypatch.setattr(gamma_sync, "default_open", opened)

    # removed: its files outside the library to Recently deleted there, the others library pages, the mark gone
    owner.patch(f"/api/folder-links/{link['id']}", json={"library": False}).raise_for_status()
    assert _mark(far, w) == {"pull": True, "library": False}
    said = owner.get(f"/api/folder-links/{link['id']}/removal").json()
    assert (said["files"], said["in_library"], said["pages"], said["bytes"]) == (2, 1, 2, 0)   # small.txt in no library
    assert "unreachable" not in said
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"pages": "remove"})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 1, "trashed": 1}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is None and _files_of(far, w) == {}
    for page_id, props in there.items():      # one in Recently deleted keeps its stamps, which a restore drops
        now = _props(far, page_id)
        if props["file_name"] == "notes.md":
            assert now["parent"] == "root" and "work_folder" not in now and "file_name" not in now
        else:
            assert now["parent"] == "trash"


def test_a_work_folder_of_another_server_is_refused_but_into_an_empty_unmarked_folder(owner, disk, over_http, far,
                                                                                      monkeypatch):
    # not empty: the mark written, read again, taken back
    full = make_folder(owner, "RWF full")
    owner.post("/api/pages", json={"title": "RWF there", "folders": [full]}).raise_for_status()
    root = disk / "Full"
    r = owner.post("/api/folder-links", json=_body(over_http, full, root))
    assert r.status_code == 409 and "not empty" in r.json()["detail"]
    assert _mark(far, full) is None and not (root / vault_disk.MARKER).exists() and folder_links.all_links() == []

    # a work folder already, inside one, around one
    marked = make_folder(owner, "RWF marked")
    inner = make_folder(owner, "RWF marked/Inner")
    mark_work_folder(owner, marked)
    r = owner.post("/api/folder-links", json=_body(over_http, marked, disk / "Marked"))
    assert r.status_code == 409 and "a synced folder already" in r.json()["detail"]
    r = owner.post("/api/folder-links", json=_body(over_http, inner, disk / "Inner"))
    assert r.status_code == 409 and "inside a synced folder, or holds one" in r.json()["detail"]
    around = make_folder(owner, "RWF around")
    mark_work_folder(owner, make_folder(owner, "RWF around/W"))
    r = owner.post("/api/folder-links", json=_body(over_http, around, disk / "Around"))
    assert r.status_code == 409 and "inside a synced folder, or holds one" in r.json()["detail"]

    # not pushing everything and pulling, or the library's top
    empty = make_folder(owner, "RWF empty")
    for extra in ({"push": "pdfs"}, {"pull": False}, {"folder": "root"}):
        r = owner.post("/api/folder-links", json={**_body(over_http, empty, disk / "Empty"), **extra})
        assert r.status_code == 400 and r.json()["detail"] == folder_links.REMOTE_WORK, extra

    # a server older than work folders: no mark at all
    remote = folder_links._remote
    monkeypatch.setattr(folder_links, "_remote", lambda *a: (lambda s, who: (s, {k: v for k, v in who.items()
                                                                               if k != "work_folders"}))(*remote(*a)))
    r = owner.post("/api/folder-links", json=_body(over_http, empty, disk / "Empty"))
    assert r.status_code == 400 and "keeps no synced folders" in r.json()["detail"] and _mark(far, empty) is None
    monkeypatch.setattr(folder_links, "_remote", remote)

    # that server refuses the mark (a Zotero-linked workspace there): refused, nothing kept
    said = ("This workspace is kept in step with a Zotero library: a vault in it pulls, or pushes PDFs, but cannot "
            "push everything.")
    kept_elsewhere = elsewhere.kept_elsewhere
    monkeypatch.setattr(elsewhere, "kept_elsewhere", lambda ws: said if ws == far else "")
    r = owner.post("/api/folder-links", json=_body(over_http, empty, disk / "Empty"))
    assert r.status_code == 409 and said in r.json()["detail"] and _mark(far, empty) is None
    assert not (disk / "Empty" / vault_disk.MARKER).exists() and folder_links.all_links() == []
    monkeypatch.setattr(elsewhere, "kept_elsewhere", kept_elsewhere)

    # that server not answering
    monkeypatch.setattr(vault_http.HttpRemote, "folder_op", lambda self, op: (_ for _ in ()).throw(
        vault_http.Unreachable("down")))
    r = owner.post("/api/folder-links", json=_body(over_http, empty, disk / "Empty"))
    assert r.status_code == 502 and folder_links.all_links() == []


def test_a_remote_kept_folder_that_pushes_everything_stays_one(owner, disk, over_http, far):
    folder = make_folder(owner, "RWF kept")
    owner.post("/api/pages", json={"title": "RWF kept page", "folders": [folder]}).raise_for_status()
    token = _token()
    body = {"folder": folder, "path": str(disk / "Kept"), "remote_url": over_http, "token": token["token"],
            "token_id": token["id"]}
    kept = _create(owner, **body, push="everything")
    assert kept["work"] is False and _mark(far, folder) is None
    assert _sync(owner, kept["id"])["last_error"] == "" and _mark(far, folder) is None
    assert owner.get(f"/api/folder-links/{kept['id']}/tree").status_code == 404      # no tracking dialog
    pdfs = _create(owner, **{**body, "path": str(disk / "Pdfs"), "folder": make_folder(owner, "RWF pdfs")}, push="pdfs")
    r = owner.patch(f"/api/folder-links/{pdfs['id']}", json={"push": "everything"})
    assert r.status_code == 400 and r.json()["detail"] == folder_links.REMOTE_RAISED


def test_a_work_folder_of_another_server_keeps_pushing_everything_and_pulling(owner, disk, over_http, far):
    link = _saved(owner, over_http, make_folder(owner, "RWF locked"), disk / "Locked", {"a.md": "a\n"})
    for change in ({"push": "pdfs"}, {"push": "off"}, {"pull": False}):
        r = owner.patch(f"/api/folder-links/{link['id']}", json=change)
        assert r.status_code == 400 and r.json()["detail"] == folder_links.REMOTE_LOWERED, change
    assert owner.get(f"/api/folder-links/{link['id']}").json()["push"] == "everything"
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_cancelled_work_folder_of_another_server_lets_its_folder_go_there(owner, disk, over_http, far):
    w = make_folder(owner, "RWF cancelled")
    root = disk / "Cancelled"
    link = _work(owner, over_http, w, root, {"a.md": "a\n"})
    # a member writes a note in the folder meanwhile: a file of the work folder there
    note = owner.post("/api/pages", json={"title": "RWF member note", "folders": [w]}).json()["id"]
    assert _props(far, note)["work_folder"] == w
    assert owner.delete(f"/api/folder-links/{link['id']}").json() == {"ok": True, "kept": 0, "trashed": 0}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is None
    props = _props(far, note)
    assert props["parent"] == "root" and "work_folder" not in props and props["folders"] == [w]   # a library page
    assert not (root / vault_disk.MARKER_DIR).exists() and (root / "a.md").is_file()


def test_removing_one_while_that_server_does_not_answer_is_refused_unless_forced(owner, disk, over_http, far,
                                                                                 monkeypatch):
    w = make_folder(owner, "RWF unreached")
    link = _saved(owner, over_http, w, disk / "Unreached", {"a.md": "a\n", "b.txt": "b\n"},
                  changes=[{"path": "b.txt", "on": True}])
    there = _files_of(far, w)
    assert len(there) == 2
    work_pages = vault_http.HttpRemote.work_pages
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", lambda self: (_ for _ in ()).throw(
        vault_http.Unreachable("down")))
    assert folder_links.removal(folder_links.get_link(link["id"]))["unreachable"] is True
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 409 and r.json()["detail"].startswith("The other server cannot be reached")
    assert folder_links.get_link(link["id"]) is not None and (disk / "Unreached" / vault_disk.MARKER).is_file()
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"force": 1})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 0, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", work_pages)

    # the workspace's owner detaches it there: its files out of the library to Recently deleted
    r = owner.post(f"/api/folders/{w}/detach", json={"pages": "remove"})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 2}
    assert _mark(far, w) is None and _files_of(far, w) == {}
    assert all(_props(far, page_id)["parent"] != "root" for page_id in there)
    assert owner.post(f"/api/folders/{w}/detach", json={"pages": "remove"}).status_code == 404


def test_a_work_folder_let_go_there_stops_its_vault_for_good(owner, disk, over_http, far, monkeypatch):
    w = make_folder(owner, "RWF let go")
    root = disk / "Let go"
    link = _saved(owner, over_http, w, root, {"a.md": "a\n"}, library=True)
    page = next(iter(_files_of(far, w)))
    assert owner.post(f"/api/folders/{w}/detach", json={"pages": "keep"}).json() == {"ok": True, "kept": 1, "trashed": 0}
    assert _mark(far, w) is None and "work_folder" not in _props(far, page)

    # its library switch, turned before its next round, writes no mark where none is
    owner.patch(f"/api/folder-links/{link['id']}", json={"library": False}).raise_for_status()
    assert _mark(far, w) is None
    stopped = []
    monkeypatch.setattr(runner, "stop_link", lambda link_id, why="": stopped.append((link_id, why)))
    status = _sync(owner, link["id"])
    assert status["stopped"] == folder_links.MARK_GONE and _mark(far, w) is None     # never marked again
    assert folder_links.get_link(link["id"])["state"][folder_links.LET_GO_THERE]
    assert stopped == [(link["id"], "detached")]                                      # its agent's run here stops
    said = owner.get(f"/api/folder-links/{link['id']}").json()
    assert said["work"] is False and said["let_go"] is True                           # the desktop app forgets it

    # another computer's vault makes a work folder of it there: this one never takes it for its own
    mark = mark_work_folder(owner, w, library=True)
    with connect_pages_db(far) as conn:
        theirs = create_page(conn, "RWF theirs", work_props(w, "b.txt"), actor=account_of(OWNER),
                             client=DISK_CLIENT)["id"]
    _put(root / "c.md", "c\n")
    asked = []
    opened = gamma_sync.default_open

    def counting(method, path, headers, body=None):
        asked.append(path)
        return opened(method, path, headers, body)

    monkeypatch.setattr(gamma_sync, "default_open", counting)
    status = _sync(owner, link["id"])
    assert status["stopped"] == folder_links.MARK_GONE and asked == []                # stopped at once
    assert stopped == [(link["id"], "detached")]
    folder_links._mark_again(folder_links.get_link(link["id"], with_token=True))
    assert asked == [] and folder_links.removal(folder_links.get_link(link["id"]))["files"] == 0
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    assert _mark(far, w) == mark and list(_files_of(far, w)) == [theirs]

    # removed: only forgotten, the other vault's work folder left as it is
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 0}
    assert _mark(far, w) == mark and list(_files_of(far, w)) == [theirs]
    owner.post(f"/api/folders/{w}/detach", json={"pages": "remove"}).raise_for_status()


def test_a_removal_that_server_stops_answering_midway_says_the_mark_stays(owner, disk, over_http, far, monkeypatch):
    w = make_folder(owner, "RWF midway")
    link = _saved(owner, over_http, w, disk / "Midway", {"a.md": "a\n", "b.txt": "b\n"},
                  changes=[{"path": "b.txt", "on": True}])
    trash = vault_http.HttpRemote.trash
    monkeypatch.setattr(vault_http.HttpRemote, "trash", lambda self, page_id: (_ for _ in ()).throw(
        vault_http.Unreachable("down")))
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"pages": "remove"})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 0, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "trash", trash)
    owner.post(f"/api/folders/{w}/detach", json={"pages": "remove"}).raise_for_status()


def test_a_cancel_while_that_server_does_not_answer_is_refused_unless_forced(owner, disk, over_http, far,
                                                                             monkeypatch):
    w = make_folder(owner, "RWF cancel unreached")
    root = disk / "Cancel unreached"
    link = _work(owner, over_http, w, root, {"a.md": "a\n"})
    work_pages = vault_http.HttpRemote.work_pages
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", lambda self: (_ for _ in ()).throw(
        vault_http.Unreachable("down")))
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 409 and r.json()["detail"].startswith("The other server cannot be reached")
    assert folder_links.get_link(link["id"]) is not None and (root / vault_disk.MARKER).is_file()
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"force": 1})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 0, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and not (root / vault_disk.MARKER).exists()
    assert _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", work_pages)
    owner.post(f"/api/folders/{w}/detach", json={"pages": "remove"}).raise_for_status()


def test_a_mark_whose_answer_was_lost_is_taken_back(owner, disk, over_http, far, monkeypatch):
    w = make_folder(owner, "RWF lost answer")
    folder_op = vault_http.HttpRemote.folder_op

    def lost(self, op):
        folder_op(self, op)
        if op["props"].get("vault") is not None:
            raise vault_http.Unreachable("the answer was lost")

    monkeypatch.setattr(vault_http.HttpRemote, "folder_op", lost)
    r = owner.post("/api/folder-links", json=_body(over_http, w, disk / "Lost answer"))
    assert r.status_code == 502 and _mark(far, w) is None and folder_links.all_links() == []


def test_the_questions_between_rounds_wait_as_questions(owner, disk, over_http, far, monkeypatch):
    link = _saved(owner, over_http, make_folder(owner, "RWF asked"), disk / "Asked", {"a.md": "a\n"})
    waits = []
    remote_server = folder_links.remote_server

    def recording(keyed, timeout=120):
        waits.append(timeout)
        return remote_server(keyed, timeout)

    monkeypatch.setattr(folder_links, "remote_server", recording)
    keyed = folder_links.get_link(link["id"], with_token=True)
    folder_links._mark_again(keyed)
    folder_links.removal(keyed)
    folder_links.delete_link(link["id"])
    assert waits and set(waits) == {folder_links.ASK_TIMEOUT_S}, waits


# --- the annotation files ---------------------------------------------------------------------

AREA = {"x1": 66, "y1": 60, "x2": 260, "y2": 110}
JPEG = b"\xff\xd8\xff"


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _area(ws, page) -> str:
    """An area highlight on the page's first PDF page, written there."""
    block_id = new_block_id()
    commit_ops(ws, page, [insert(page, "", {"pdf_position": _position(1, AREA, area=True), "color": "#ffe28fa6"},
                                 block_id)], actor=account_of(OWNER))
    return block_id


def _recorded(monkeypatch) -> list:
    """The paths this computer asks the other server, recorded."""
    asked, opened = [], gamma_sync.default_open

    def recording(method, path, headers, body=None):
        asked.append(path)
        return opened(method, path, headers, body)
    monkeypatch.setattr(gamma_sync, "default_open", recording)
    return asked


def test_a_work_folder_of_another_server_writes_the_annotations_it_reads_there(owner, disk, over_http, far,
                                                                              monkeypatch):
    """Each round reads its folder's entries there (``GET /api/annotations``)
    and the pictures they name, by the routes that server names, with the
    vault's token, never this computer's copy of that workspace (``far``),
    and writes the files a work folder of this server writes, its files
    named by its rows; the files of before go. A server that does not
    answer it is the files' error, never the round's."""
    w = make_folder(owner, "RWF marks")
    root = disk / "RWF marks"
    link = _saved(owner, over_http, w, root, {"main.pdf": _pdf("the method defines the loss"), "notes.md": "a\n"})
    page = vault_store.files(link["id"])["main.pdf"]["page_id"]
    area = _area(far, page)
    thread = thread_on(owner, page, page, "the whole paper")
    former = root / annotation_files.FORMER[0]
    former.mkdir(parents=True)
    (former / "open.json").write_bytes(b"{}\n")
    asked = _recorded(monkeypatch)
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["annotations"] == {"open": 1}
    here = root / annotation_files.DIR
    doc = json.loads((here / "open.json").read_text(encoding="utf-8"))
    assert (doc["format"], doc["work_folder"]["id"], doc["scope"]["kind"], doc["owner"]) == (
        "gamma-annotations-1", w, "work_folder", OWNER)
    assert [(e["id"], e["type"], e["file"]) for e in doc["entries"]] == [
        (thread["id"], "thread", "main.pdf"), (area, "highlight", "main.pdf")]
    entry = doc["entries"][1]
    assert entry["picture"] and (here / entry["picture"]).read_bytes()[:3] == JPEG and "picture_url" not in entry
    assert f"/api/annotations?scope={w}&state=open" in asked
    assert any(path.startswith(f"/api/annotations/{area}/picture?at=now") for path in asked)
    assert "## main.pdf" in (here / "open.md").read_text(encoding="utf-8")
    assert (here / ".gitignore").read_bytes() == annotation_files.GITIGNORE and not former.exists()

    # a round of push alone reads them again at most every REMOTE_POLL_S, keeping the last summary meanwhile
    asked.clear()
    status = _sync(owner, link["id"], direction="push")
    assert status["annotations"] == {"open": 1} and not any(p.startswith("/api/annotations") for p in asked)
    folder_links._annotations_read[link["id"]] -= folder_links.REMOTE_POLL_S
    status = _sync(owner, link["id"], direction="push")
    assert status["annotations"] == {"open": 1} and f"/api/annotations?scope={w}&state=open" in asked

    # a server of a release before the reader: the round goes on, the files stay as they were
    before = (here / "open.json").read_bytes()
    opened = gamma_sync.default_open

    def older(method, path, headers, body=None):
        if path.startswith("/api/annotations"):
            return 404, {"Content-Type": "application/json"}, io.BytesIO(b'{"detail": "Not Found"}')
        return opened(method, path, headers, body)
    monkeypatch.setattr(gamma_sync, "default_open", older)
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["annotations"]["error"] and (here / "open.json").read_bytes() == before
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not (root / vault_disk.MARKER_DIR).exists()


def test_a_followed_file_of_another_server_writes_its_pages_annotations_beside_it(owner, disk, over_http, far,
                                                                                 monkeypatch):
    """A vault of one file of another server reads its page's entries there
    at its rounds, one every ``REMOTE_POLL_S`` while the file rests, and
    writes them beside the file; removed, they go."""
    data = _pdf("the far draft")
    r = owner.post("/api/uploads", files={"file": ("draft.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    page = make_page(owner, "RWF far draft", doc_props(r.json()["doc_id"], name="draft.pdf"))["id"]
    build = _put(disk / "far" / "main.pdf", data)
    token = _token()
    link = _create(owner, kind="file", page=page, path=str(build), remote_url=over_http, token=token["token"],
                   token_id=token["id"], workspace=far)
    asked = _recorded(monkeypatch)
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 0 and status["annotations"] == {"open": 0}
    assert f"/api/annotations?scope={page}&state=open" in asked
    here = build.parent / annotation_files.DIR / "main.pdf"
    assert json.loads((here / "open.json").read_text(encoding="utf-8"))["scope"] == {
        "kind": "page", "id": page, "name": "RWF far draft"}

    # the file at rest: a round every REMOTE_POLL_S all the same, which sends nothing and reads them again
    area = _area(far, page)
    assert folder_links.due(folder_links.get_link(link["id"])) == "both"
    assert folder_links.due(folder_links.get_link(link["id"])) == ""         # asked a moment ago
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 0 and status["last_error"] == ""
    [entry] = json.loads((here / "open.json").read_text(encoding="utf-8"))["entries"]
    assert (entry["id"], entry["file"]) == (area, "main.pdf") and (here / entry["picture"]).is_file()
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()
    assert not (build.parent / vault_disk.MARKER_DIR).exists() and build.is_file()


# --- the remote's own pieces ------------------------------------------------------------------

def test_the_remote_stamps_and_reads_a_work_folders_files_as_this_servers_remote_does(owner, disk, over_http, far):
    w = make_folder(owner, "RWF stamps")
    link = _saved(owner, over_http, w, disk / "Stamps", {"a.md": "a\n", "b.md": "b\n"})
    local = LocalRemote(far, w, account_of(OWNER))
    http = _remote(link["id"])
    assert set(http.work_pages()) == set(local.work_pages()) and len(local.work_pages()) == 2
    for page_id, props in local.work_pages().items():
        assert {k: http.work_pages()[page_id].get(k) for k in ("work_folder", "file_name", "library")} == {
            k: props.get(k) for k in ("work_folder", "file_name", "library")}

    def plan(_page_id, props):
        return {"library": True} if props.get("library") is not True else None

    seqs = http.stamp(list(http.work_pages()), plan)
    assert set(seqs) == set(local.work_pages()) and all(after > before for before, after in seqs.values())
    assert all(p.get("library") is True for p in local.work_pages().values())
    assert http.stamp(list(http.work_pages()), plan) == {} == local.stamp(list(local.work_pages()), plan)
    for page_id in seqs:      # quiet: nobody's recents reorder
        batches = owner.get(f"/api/pages/{page_id}/ops", params={"since": 0}).json()["batches"]
        assert batches[-1]["client"] == DISK_CLIENT
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_locked_refusal_there_is_the_files_problem_never_planned_again(owner, disk, over_http, far, monkeypatch):
    error = vault_http._refusal(403, {"detail": "A synced folder's file is named by its file: rename it on disk.",
                                      "conflict": LOCKED}, "/api/pages/x/plan", "POST")
    assert isinstance(error, OpError) and error.conflict == LOCKED and error.status == 403
    assert isinstance(vault_http._refusal(403, {"detail": "no"}, "/x", "POST"), vault_http.Refused)
    w = make_folder(owner, "RWF locked file")
    link = _saved(owner, over_http, w, disk / "Locked file", {"a.md": "a\n"})
    page = next(iter(_files_of(far, w)))
    sent = []

    def refused(self, page_id, ops, token, seq):
        sent.append(page_id)
        raise vault_http._refusal(403, {"detail": "locked there", "conflict": LOCKED}, "/plan", "POST")

    send_plan = vault_http.HttpRemote._send_plan
    monkeypatch.setattr(vault_http.HttpRemote, "_send_plan", refused)
    with pytest.raises(OpError) as caught:
        _remote(link["id"]).apply(page, lambda now, held: Edit([{"op": "set", "id": page, "content": "x"}]))
    assert caught.value.conflict == LOCKED and sent == [page]          # raised at once
    monkeypatch.setattr(vault_http.HttpRemote, "_send_plan", send_plan)
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


# --- every server's side -----------------------------------------------------------------------

@pytest.fixture(scope="module")
def marks_owner(client):
    make_user("rwf_marks", "pw")
    return login("rwf_marks", "pw")


def _marking(ws, folder, mark=None, op="set", **fields):
    return commit_ops(ws, FOLDERS, [{"op": op, "id": folder, "props": {"vault": mark}, **fields}],
                      actor=account_of("rwf_marks"), client=DISK_CLIENT)


def test_a_vaults_mark_is_refused_inside_or_around_a_work_folder_and_in_a_kept_workspace(marks_owner, monkeypatch):
    ws = workspace_of("rwf_marks")
    assert folder_links.pushing_vault(ws) == "" and folder_links.pushing_vault("rwf-no-such-workspace") == ""
    w = make_folder(marks_owner, "RWF W")
    inner = make_folder(marks_owner, "RWF W/Inner")
    outer = make_folder(marks_owner, "RWF outer")
    below = make_folder(marks_owner, "RWF outer/Below")
    mark = {"pull": True, "library": False}
    _marking(ws, w, mark)
    _marking(ws, w, {"pull": False, "library": True})                   # its own mark changed: fine
    assert folder_links.pushing_vault(ws) == "RWF W"                     # a clone or a Zotero link is refused it
    for folder, fields in ((inner, {}), (new_block_id(), {"op": "insert", "parent": inner, "content": "New"})):
        with pytest.raises(OpError) as refused:
            _marking(ws, folder, mark, **fields)
        assert refused.value.status == 403 and refused.value.conflict == LOCKED
    _marking(ws, below, mark)
    with pytest.raises(OpError) as refused:
        _marking(ws, outer, mark)                                        # a work folder below it
    assert refused.value.conflict == LOCKED and "never inside another synced folder" in refused.value.detail
    _marking(ws, below, None)                                            # taken off: always
    _marking(ws, outer, mark)

    said = "This workspace is a clone of another: a vault in it pulls, or pushes PDFs, but cannot push everything."
    monkeypatch.setattr(elsewhere, "kept_elsewhere", lambda at: said if at == ws else "")
    fresh = make_folder(marks_owner, "RWF fresh")
    with pytest.raises(OpError) as refused:
        _marking(ws, fresh, mark)
    assert refused.value.detail == said and refused.value.conflict == LOCKED
    _marking(ws, outer, None)
    # a person's tree ops are held to their own rule, as before
    r = marks_owner.post(f"/api/pages/{FOLDERS}/ops", json={"ops": [{"op": "set", "id": fresh, "content": "Renamed"}]})
    assert r.status_code == 200, r.text


def test_whoami_says_work_folders_and_the_summaries_count_notes_and_threads(owner):
    r = owner.get("/api/sync/whoami")
    assert r.status_code == 200 and r.json()["work_folders"] == 1
    page = owner.post("/api/pages", json={"title": "RWF counted"}).json()["id"]
    owner.put(f"/api/blocks/{page}/children", json={"blocks": [
        {"id": f"rwf-{page}", "content": "a line", "properties": {}, "children": []}]}).raise_for_status()
    pages = owner.post("/api/pages/summaries", json={"ids": [page]}).json()["pages"]
    assert [(p["id"], p["notes"], p["open_comments"]) for p in pages] == [(page, 1, 0)]


def test_a_work_folder_kept_on_no_computer_here_is_detached_by_its_owner_or_its_computers_account(
        owner, disk, over_http, monkeypatch):
    shared = workspaces.create("RWF shared", account_of(OWNER), kind="shared")["id"]
    make_user("rwf_editor", "pw")
    workspaces.set_member(shared, account_of("rwf_editor"), "editor", by=account_of(OWNER))
    editor = login("rwf_editor", "pw")
    editor.headers["X-Gamma-Workspace"] = shared
    owner_there = login(OWNER, "pw")
    owner_there.headers["X-Gamma-Workspace"] = shared
    w = make_folder(owner_there, "RWF elsewhere")
    mark_work_folder(owner_there, w, library=False)
    with connect_pages_db(shared) as conn:
        file = create_page(conn, "RWF a file", work_props(w, "a.txt"), actor=account_of(OWNER),
                           client=DISK_CLIENT)["id"]
    url = f"/api/folders/{w}/detach"

    # another member may not, and learns nothing of it; nor does a token; a folder of no mark is none
    assert editor.get(url).json() == {"may": False}
    assert editor.post(url, json={"pages": "keep"}).status_code == 403
    assert owner_there.get(f"/api/folders/{make_folder(owner_there, 'RWF plain')}/detach").status_code == 404
    token = create_token(account_of(OWNER), shared, "rwf-token", 1, scope="write")
    _made.append(token["id"])
    r = fresh_client().post(url, json={"pages": "keep"}, headers={"Authorization": f"Bearer {token['token']}"})
    assert r.status_code == 403
    assert owner_there.post(url, json={"pages": "trash"}).status_code == 422

    # the workspace's owner may: no computer keeps it, so none is named and nothing new is stored
    assert owner_there.get(url).json() == {"may": True, "files": 1, "in_library": 0, "too_large": 0, "bytes": 0,
                                           "computer": None}

    # a viewer's computer token announces what it likes: announcing a vault of it is no authority
    make_user("rwf_viewer", "pw")
    workspaces.set_member(shared, account_of("rwf_viewer"), "viewer", by=account_of(OWNER))
    viewer = login("rwf_viewer", "pw")
    viewer.headers["X-Gamma-Workspace"] = shared
    r = viewer.post("/api/integrations/tokens", json={"name": "rwf-forged", "scope": "computer", "expires_in_days": 1})
    assert r.status_code == 201, r.text
    forged = r.json()
    _made.append(forged["id"])
    r = fresh_client(headers={"Authorization": f"Bearer {forged['token']}"}).post("/api/computers/announce", json={
        "computer": {"id": "rwf-viewer-pc", "name": "RWF-VIEWER"}, "app": "v9.9.9", "assistants": [],
        "vaults": [{"kind": "folder", "folder_id": w, "paused": False}]})
    assert r.status_code == 200, r.text
    assert forged["id"] in [c["token_id"] for c in registry.keepers(shared, w)]
    assert viewer.get(url).json() == {"may": False}
    assert viewer.post(url, json={"pages": "remove"}).status_code == 403
    assert _mark(shared, w) is not None and _props(shared, file)["parent"] == "root"
    registry.forget(forged["id"])

    # the account whose computer announced a vault of it may too, while it may edit the workspace, and is told
    # which computer, online or not
    computer = create_token(account_of("rwf_editor"), shared, "rwf-computer", 1, scope="computer")
    _made.append(computer["id"])
    registry.announce(account_of("rwf_editor"), "rwf_editor", shared, computer["id"],
                      {"computer": {"id": "rwf-pc", "name": "RWF-PC"}, "vaults": [{"kind": "folder", "folder_id": w}]})
    assert editor.get(url).json()["computer"] == {"name": "RWF-PC", "online": True}
    assert owner_there.get(url).json()["computer"] is None                      # named to its own account alone
    registry.reset()                                                            # gone offline
    assert editor.get(url).json() == {"may": True, "files": 1, "in_library": 0, "too_large": 0, "bytes": 0,
                                      "computer": {"name": "RWF-PC", "online": False}}

    # a clone or a Zotero-linked workspace carries copies of marks: detached where they come from
    kept_elsewhere = elsewhere.kept_elsewhere
    monkeypatch.setattr(elsewhere, "kept_elsewhere", lambda ws: "a clone" if ws == shared else "")
    assert editor.get(url).json() == {"may": False}
    r = owner_there.post(url, json={"pages": "remove"})
    assert r.status_code == 409 and "kept in step" in r.json()["detail"] and _mark(shared, w) is not None
    monkeypatch.setattr(elsewhere, "kept_elsewhere", kept_elsewhere)

    # a file the vault stamps while the files are swept, before the mark is off, is swept too
    unmark = takeover._unmark
    late = []

    def stamped_meanwhile(remote, folder):
        with connect_pages_db(shared) as conn:
            late.append(create_page(conn, "RWF late file", work_props(w, "b.txt"), actor=account_of(OWNER),
                                    client=DISK_CLIENT)["id"])
        unmark(remote, folder)

    monkeypatch.setattr(takeover, "_unmark", stamped_meanwhile)
    woken = []
    monkeypatch.setattr(registry, "wake", lambda token_id, reason: woken.append((token_id, reason)))
    assert editor.post(url, json={"pages": "remove"}).json() == {"ok": True, "kept": 0, "trashed": 2}
    assert _mark(shared, w) is None and _props(shared, file)["parent"] != "root"
    assert late and _props(shared, late[0])["parent"] != "root"
    assert woken == [(computer["id"], "let_go")]                                # it finds the mark gone at once
    registry.forget(computer["id"])


def test_detaching_keeps_a_computers_files_as_pages_there_storing_nothing_new(owner, disk, over_http, far,
                                                                              monkeypatch):
    """Keep, on the server the computer pushed to: its files' bytes are
    there already, so each file is a page filed where it was, a file too
    large to keep listed still; the computer finds the mark gone at its round
    (``channel._Channel._let_go``: a round of each work folder of that
    server, unless paused)."""
    monkeypatch.setattr(config, "vault_keep_bytes", lambda: 20)
    w = make_folder(owner, "RWF keep there")
    root = disk / "Keep there"
    link = _saved(owner, over_http, w, root, {"a.md": "a\n", "b.txt": "b\n", "big.csv": "x,y\n" * 20},
                  changes=[{"path": "b.txt", "on": True}, {"path": "big.csv", "on": True}])
    there = _files_of(far, w)
    assert len(there) == 3
    plan = owner.get(f"/api/folders/{w}/detach").json()
    assert (plan["files"], plan["in_library"], plan["too_large"], plan["bytes"]) == (3, 0, 1, 0)
    r = owner.post(f"/api/folders/{w}/detach", json={"pages": "keep"})
    assert r.json() == {"ok": True, "kept": 3, "trashed": 0}
    for page_id, props in there.items():
        now = _props(far, page_id)
        assert now["parent"] == "root" and not any(key in now for key in ("work_folder", "library", "file_name"))
        assert now.get("doc", {}).get("at") == props.get("doc", {}).get("at")       # stored, or listed, as it was
    assert {_props(far, p).get("doc", {}).get("at") for p in there} == {None, "store", "listed"}
    rounds = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: rounds.append(link_id))

    def let_go():
        rounds.clear()
        channel._Channel("rwf-channel")._let_go({"id": "rwf-channel", "remote_url": link["remote_url"],
                                                 "workspace_id": link["workspace_id"], "created_by": link["created_by"]})

    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True}).raise_for_status()
    let_go()
    assert rounds == []                                                             # paused: it waits for its round
    owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False}).raise_for_status()
    let_go()
    assert rounds == [link["id"]]
    assert _sync(owner, link["id"])["stopped"] == folder_links.MARK_GONE
    assert sorted(p.name for p in root.iterdir() if p.name != ".gamma") == [
        ".gammaignore", "a.md", "b.txt", "big.csv"]                                 # the directory as it was
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_work_folder_kept_by_a_vault_of_this_server_is_detached_by_removing_the_vault(owner, disk):
    folder = make_folder(owner, "RWF here")
    link = _create(owner, folder=folder, path=str(disk / "Here"), push="everything")
    assert owner.get(f"/api/folders/{folder}/detach").json() == {
        "may": True, "files": 0, "in_library": 0, "too_large": 0, "bytes": 0,
        "computer": {"name": channel.computer_name(), "online": True}}
    r = owner.post(f"/api/folders/{folder}/detach", json={"pages": "keep"})
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 0}
    assert folder_links.get_link(link["id"]) is None and _mark(workspace_of(OWNER), folder) is None


# --- a path of it, from this computer's file manager -----------------------------------------------

@pytest.fixture(scope="module")
def reader(client):
    """Another account of this server, which opens files of this computer too."""
    make_user("rwf_reader", "pw")
    return login("rwf_reader", "pw")


def _where(c, path) -> dict:
    r = c.post("/api/folder-links/where", json={"path": str(path)})
    assert r.status_code == 200, r.text
    return r.json()


def test_where_names_a_work_folder_of_another_server_by_that_servers_ids(owner, reader, disk, over_http, far):
    w = make_folder(owner, "RWF where")
    root = disk / "RWF where"
    link = _saved(owner, over_http, w, root, {"notes.md": "a thought\n", "sub/deep.md": "deeper\n"})
    sub = _where(owner, root / "sub")
    assert (sub["is"], sub["rel"], sub["exact"]) == ("vault", "sub", True)
    assert sub["folder"] == vault_store.dirs(link["id"])["sub"]["folder_id"] and sub["folder"] not in ("", w)
    assert sub["link"] == {"id": link["id"], "kind": "folder", "workspace": far, "folder": w, "remote_url": over_http,
                           "work": True, "choosing": False, "paused": False, "mine": True, "name": "RWF where"}
    note = _where(owner, root / "notes.md")
    assert (note["page"], note["trashed"], note["tracked"]) == (vault_store.files(link["id"])["notes.md"]["page_id"],
                                                                False, True)

    # opened here, a file it pushes there is refused, never a page of this server: one replacer per file
    _put(root / "later.md", "not read there yet\n")
    for rel in ("notes.md", "later.md"):
        r = owner.post("/api/folder-links/open", json={"path": str(root / rel)})
        assert r.status_code == 400 and r.json()["detail"] == f"{(root / rel).resolve()} is kept on testserver: open it there."

    # another account is told nothing of it, and the file is a lone file of its own
    assert _where(reader, root / "notes.md") == {"is": "hidden"}
    r = reader.post("/api/folder-links/open", json={"path": str(root / "notes.md")})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True and r.json()["workspace"] == workspace_of("rwf_reader")
    assert r.json()["format"] == "text"
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_file_a_remote_kept_folder_does_not_push_opens_as_a_lone_file(owner, disk, over_http, far):
    """The host's round of a kept folder pushes its PDFs and notes at most:
    a source beside them has one reader, the page it is opened as."""
    token = _token()
    root = disk / "RWF kept sources"
    kept = _create(owner, folder=make_folder(owner, "RWF kept sources"), path=str(root), remote_url=over_http,
                   token=token["token"], token_id=token["id"], push="pdfs")
    f = _put(root / "draft.tex", "\\section{Draft}\n")
    said = _where(owner, f)
    assert (said["is"], said["link"]["work"], said["page"], said["tracked"]) == ("vault", False, "", False)
    r = owner.post("/api/folder-links/open", json={"path": str(f)})
    assert r.status_code == 200, r.text
    assert r.json()["created"] is True and r.json()["format"] == "text"
    owner.delete(f"/api/folder-links/{kept['id']}").raise_for_status()

"""A work folder of another Gamma server (gamma/folder_links.py
``work_vault``, ``create_remote_link`` with ``choose``; step 4b of
docs/research/agent-collab.md), over a TestClient that stands in for that
server (test_folder_links.py ``over_http``), while this computer's own
in-process reads and writes of that workspace fail (``far``): made of an
empty folder there and refused otherwise, chosen in the tracking dialog here
(what goes there, its secrets), its library switch written to the mark there,
cancelled, removed (kept, trashed, refused and forced while that server does
not answer, a cancel too, the mark said to stay when it stops answering
midway), a mark whose answer was lost taken back, the questions between
rounds waiting as questions, stopped for good once that server let go of it
(never marking, pushing into or releasing that folder again), the remote kept folders
that push everything left as they were; the remote's own ``stamp`` and
``work_pages`` against ``LocalRemote``'s, a ``locked`` refusal as the file's,
a round of push alone that reads no listing. And every server's side: a
vault's mark refused in a clone or a Zotero-linked workspace and inside or
around a work folder (gamma/ops.py ``mark_rule``), ``pushing_vault`` seeing
a mark, whoami's ``work_folders``, the summaries' counts, and a workspace
owner's Let go (``POST /api/folders/{id}/let-go``: refused in a clone or a
Zotero-linked workspace, sweeping again once the mark is off)."""

import json
import os
import time

import pytest

from conftest import (account_of, fresh_client, login, make_folder, make_user, mark_work_folder, work_props,
                      workspace_of)
from gamma import config, folder_links, gamma_sync, workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import FOLDERS, create_page, new_block_id
from gamma.db import connect_pages_db, connect_users_db
from gamma.feedback import files as feedback_files
from gamma.integrations import create_token
from gamma.ops import LOCKED, OpError, commit_ops
from gamma.vault import disk as vault_disk, http_remote as vault_http, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from gamma.vault.remote import Edit
from gamma.work_folders import elsewhere, selection, takeover
from test_folder_links import _create, _sync, disk, over_http  # noqa: F401: the disk and over_http fixtures

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

    for module in (folder_links, selection, feedback_files):
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
    assert said["files"] == 1 and said["pages"] == 2 and "unreachable" not in said     # small.txt is in no library
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True}
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
    assert r.status_code == 409 and "a work folder already" in r.json()["detail"]
    r = owner.post("/api/folder-links", json=_body(over_http, inner, disk / "Inner"))
    assert r.status_code == 409 and "inside a work folder, or holds one" in r.json()["detail"]
    around = make_folder(owner, "RWF around")
    mark_work_folder(owner, make_folder(owner, "RWF around/W"))
    r = owner.post("/api/folder-links", json=_body(over_http, around, disk / "Around"))
    assert r.status_code == 409 and "inside a work folder, or holds one" in r.json()["detail"]

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
    assert r.status_code == 400 and "keeps no work folders" in r.json()["detail"] and _mark(far, empty) is None
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
    assert owner.delete(f"/api/folder-links/{link['id']}").json() == {"ok": True}
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
    assert r.status_code == 200 and r.json() == {"ok": True, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", work_pages)

    # the workspace's owner lets go of it there: its files out of the library to Recently deleted
    r = owner.post(f"/api/folders/{w}/let-go")
    assert r.status_code == 200 and r.json() == {"ok": True, "kept": 0, "trashed": 2}
    assert _mark(far, w) is None and _files_of(far, w) == {}
    assert all(_props(far, page_id)["parent"] != "root" for page_id in there)
    assert owner.post(f"/api/folders/{w}/let-go").status_code == 404


def test_a_work_folder_let_go_there_stops_its_vault_for_good(owner, disk, over_http, far, monkeypatch):
    w = make_folder(owner, "RWF let go")
    root = disk / "Let go"
    link = _saved(owner, over_http, w, root, {"a.md": "a\n"}, library=True)
    page = next(iter(_files_of(far, w)))
    assert owner.post(f"/api/folders/{w}/let-go").json() == {"ok": True, "kept": 1, "trashed": 0}
    assert _mark(far, w) is None and "work_folder" not in _props(far, page)

    # its library switch, turned before its next round, writes no mark where none is
    owner.patch(f"/api/folder-links/{link['id']}", json={"library": False}).raise_for_status()
    assert _mark(far, w) is None
    status = _sync(owner, link["id"])
    assert status["stopped"] == folder_links.MARK_GONE and _mark(far, w) is None     # never marked again
    assert folder_links.get_link(link["id"])["state"][folder_links.LET_GO_THERE]
    assert owner.get(f"/api/folder-links/{link['id']}").json()["work"] is False

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
    folder_links._mark_again(folder_links.get_link(link["id"], with_token=True))
    assert asked == [] and folder_links.removal(folder_links.get_link(link["id"]))["files"] == 0
    monkeypatch.setattr(gamma_sync, "default_open", opened)
    assert _mark(far, w) == mark and list(_files_of(far, w)) == [theirs]

    # removed: only forgotten, the other vault's work folder left as it is
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True}
    assert _mark(far, w) == mark and list(_files_of(far, w)) == [theirs]
    owner.post(f"/api/folders/{w}/let-go").raise_for_status()


def test_a_removal_that_server_stops_answering_midway_says_the_mark_stays(owner, disk, over_http, far, monkeypatch):
    w = make_folder(owner, "RWF midway")
    link = _saved(owner, over_http, w, disk / "Midway", {"a.md": "a\n", "b.txt": "b\n"},
                  changes=[{"path": "b.txt", "on": True}])
    trash = vault_http.HttpRemote.trash
    monkeypatch.setattr(vault_http.HttpRemote, "trash", lambda self, page_id: (_ for _ in ()).throw(
        vault_http.Unreachable("down")))
    r = owner.delete(f"/api/folder-links/{link['id']}")
    assert r.status_code == 200 and r.json() == {"ok": True, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "trash", trash)
    owner.post(f"/api/folders/{w}/let-go").raise_for_status()


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
    assert r.status_code == 200 and r.json() == {"ok": True, "mark_stays": True}
    assert folder_links.get_link(link["id"]) is None and not (root / vault_disk.MARKER).exists()
    assert _mark(far, w) is not None
    monkeypatch.setattr(vault_http.HttpRemote, "work_pages", work_pages)
    owner.post(f"/api/folders/{w}/let-go").raise_for_status()


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
    error = vault_http._refusal(403, {"detail": "A work folder's file is named by its file: rename it on disk.",
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
    assert refused.value.conflict == LOCKED and "never inside another work folder" in refused.value.detail
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


def test_a_workspace_owner_alone_lets_go_of_a_work_folder_kept_on_no_computer_here(owner, disk, over_http,
                                                                                    monkeypatch):
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
    r = editor.post(f"/api/folders/{w}/let-go")
    assert r.status_code == 403
    assert owner_there.post(f"/api/folders/{make_folder(owner_there, 'RWF plain')}/let-go").status_code == 404
    token = create_token(account_of(OWNER), shared, "rwf-token", 1, scope="write")
    _made.append(token["id"])
    r = fresh_client().post(f"/api/folders/{w}/let-go", headers={"Authorization": f"Bearer {token['token']}"})
    assert r.status_code == 403

    # a clone or a Zotero-linked workspace carries copies of marks: let go where they come from
    kept_elsewhere = elsewhere.kept_elsewhere
    monkeypatch.setattr(elsewhere, "kept_elsewhere", lambda ws: "a clone" if ws == shared else "")
    r = owner_there.post(f"/api/folders/{w}/let-go")
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
    assert owner_there.post(f"/api/folders/{w}/let-go").json() == {"ok": True, "kept": 0, "trashed": 2}
    assert _mark(shared, w) is None and _props(shared, file)["parent"] != "root"
    assert late and _props(shared, late[0])["parent"] != "root"


def test_a_work_folder_kept_by_a_vault_of_this_server_is_removed_not_let_go(owner, disk):
    folder = make_folder(owner, "RWF here")
    link = _create(owner, folder=folder, path=str(disk / "Here"), push="everything")
    r = owner.post(f"/api/folders/{folder}/let-go")
    assert r.status_code == 409 and "remove the vault instead" in r.json()["detail"]
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()

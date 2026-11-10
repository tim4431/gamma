"""Folders synced with this computer by the desktop app's own server
(gamma/folder_links.py, routers/folder_links.py): a vault made from a folder,
its directory where the user picked it, its rounds by hand (Sync now
answered with its round running, which says how far it is), from the loop's
tick and from the hot loop, a vault of another server read with a token of it,
what a removal takes back, the paths it refuses, who may use the API, and
that no other server keeps links at all. The engine's own steps:
test_vault_sync.py; a folder kept before synced vaults: test_vault_upgrade.py;
a vault of another server pushing: test_vault_http.py."""

import io
import json
import os
import threading
import time

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, workspace_of
from gamma import config, folder_links, gamma_sync, vault_disk, vault_sync
from gamma.db import connect_users_db
from gamma.integrations import create_token

REAL_BACKGROUND = folder_links.run_in_background  # before ``disk`` stubs it


@pytest.fixture(scope="module")
def owner(client):
    make_user("fl_owner", "pw")
    return login("fl_owner", "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it (links kept here), a place on
    "this computer" for the directories, and no background rounds: the tests
    run them inline with ``?wait=1`` or ``tick()``. The links a test leaves
    go with it: the data directory is the worker's, and a later file's
    ``tick()`` would run them (test_disk_push and test_linked_files take this
    fixture too)."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    before = {link["id"] for link in folder_links.all_links()}
    yield tmp_path / "disk"
    for link in folder_links.all_links():
        if link["id"] not in before:
            folder_links._forget(link)


def _create(c, **body):
    r = c.post("/api/folder-links", json=body)
    assert r.status_code == 201, r.text
    return r.json()


def _sync(c, link_id, **params):
    r = c.post(f"/api/folder-links/{link_id}/sync", params={"wait": 1, **params})
    assert r.status_code == 200, r.text
    return r.json()["status"]


def _note(c, page_id, text):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": [
        {"id": f"fl-{page_id}-{abs(hash(text)) % 10 ** 6}", "content": text, "properties": {}, "children": []}]})
    assert r.status_code == 200, r.text


def _due(link_id) -> str:
    return folder_links.due(folder_links.get_link(link_id))


def test_a_vault_keeps_the_folder_where_it_was_asked_and_the_tick_follows_changes(owner, disk):
    folder = make_folder(owner, "FL lab")
    make_folder(owner, "FL lab/Sub")
    paper = make_page(owner, "FL paper", {"folders": [folder]})
    _note(owner, paper["id"], "a first thought")
    dest = disk / "FL lab"
    link = _create(owner, folder=folder, path=str(dest))
    assert link["path"] == str(dest.resolve()) and link["dest"] == str(dest.resolve()) and link["notes"] is True
    assert link["pull"] is True and link["push"] == "off"
    assert sorted(p.relative_to(dest).as_posix() for p in dest.rglob("*")) == [".gamma", vault_disk.MARKER]
    listed = owner.get("/api/folder-links").json()
    assert set(listed) == {"links"} and [(l["id"], l["remote_url"]) for l in listed["links"]] == [(link["id"], "")]

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["added"] == 1 and status["running"] is False
    assert status["folder_path"] == ["FL lab"]
    text = (dest / "FL paper.md").read_text(encoding="utf-8")
    assert "a first thought" in text and "gamma_id" not in text and not (dest / gamma_sync.STATE).exists()
    assert owner.get(f"/api/folder-links/{link['id']}").json()["cursor"].isdigit()

    # the tick runs a vault each time (its scan is the truth); a note written in Gamma reaches the file
    assert _due(link["id"]) == "both"
    _note(owner, paper["id"], "a thought")
    folder_links.tick()
    assert "a thought" in (dest / "FL paper.md").read_text(encoding="utf-8")
    after = folder_links.get_link(link["id"])
    assert after["status"]["counts"]["updated"] == 1 and after["status"]["last_error"] == ""

    # a synced vault keeps its notes as files
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"notes": False})
    assert r.status_code == 400 and "keeps its notes" in r.json()["detail"]

    # removing the vault with its files moves what pull wrote to its trash, nothing else
    (dest / "mine.txt").write_text("not the vault's", encoding="utf-8")
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"})
    assert r.status_code == 200
    assert owner.get("/api/folder-links").json()["links"] == []
    assert not (dest / "FL paper.md").exists() and (dest / "mine.txt").is_file()
    assert not (dest / vault_disk.MARKER).exists()
    assert [p.name for p in (dest / ".gamma" / "trash").rglob("*.md")] == ["FL paper.md"]


def test_a_paused_link_runs_only_when_asked_and_no_round_undoes_the_pause(owner, disk, monkeypatch):
    folder = make_folder(owner, "FL paused")
    paper = make_page(owner, "FL pp", {"folders": [folder]})
    _note(owner, paper["id"], "before the pause")
    dest = disk / "Paused"
    link = _create(owner, folder=folder, path=str(dest))
    _sync(owner, link["id"])

    r = owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True})
    assert r.status_code == 200
    since = r.json()["status"]["paused_at"]
    assert since and r.json()["status"]["last_sync"]                 # the round's status stays beside it
    _note(owner, paper["id"], "while paused")
    assert _due(link["id"]) == ""
    folder_links.tick()
    assert "while paused" not in (dest / "FL pp.md").read_text(encoding="utf-8")
    assert owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True}).json()["status"]["paused_at"] == since

    # a sync asked for runs, and its round keeps the pause
    status = _sync(owner, link["id"])
    assert status["counts"]["updated"] == 1 and status["paused_at"] == since
    assert "while paused" in (dest / "FL pp.md").read_text(encoding="utf-8")

    # resumed: a round runs now and the tick runs it again
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(link_id))
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False})
    assert r.status_code == 200 and "paused_at" not in r.json()["status"] and started == [link["id"]]
    assert _due(link["id"]) == "both"

    # paused while a round runs: the round's own status write leaves the pause in place
    layout = vault_sync.LocalRemote.layout

    def pausing(self, page_id=""):
        folder_links.set_paused(link["id"], True)
        return layout(self, page_id)

    monkeypatch.setattr(vault_sync.LocalRemote, "layout", pausing)
    assert _sync(owner, link["id"])["paused_at"]
    assert _due(link["id"]) == ""
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_sync_now_answers_with_its_round_running_and_the_status_follows_it_to_its_end(owner, disk, monkeypatch):
    """Sync now without ``wait``: the round's thread starts with its
    ``running`` already written, so the answer says so and a client reads
    the link until ``running`` comes down, finding the round's outcome.
    Asked again while it runs, the answer is that round, and the request
    runs once that round is done (``_again``), never beside it."""
    folder = make_folder(owner, "FL now")
    make_page(owner, "FL now paper", {"folders": [folder]})
    dest = disk / "Now"
    link = _create(owner, folder=folder, path=str(dest))
    monkeypatch.setattr(folder_links, "run_in_background", REAL_BACKGROUND)
    gate, rounds = threading.Event(), []
    real = folder_links._vault_round

    def held(link_, started, **asked):
        rounds.append(asked["force"])
        assert gate.wait(10)
        return real(link_, started, **asked)

    monkeypatch.setattr(folder_links, "_vault_round", held)
    r = owner.post(f"/api/folder-links/{link['id']}/sync", params={"force": "pull"})
    assert r.status_code == 200, r.text
    started = r.json()["status"]
    assert started["running"] is True and started["started_at"] and "last_sync" not in started
    again = owner.post(f"/api/folder-links/{link['id']}/sync").json()["status"]
    assert again["running"] is True and again["started_at"] == started["started_at"]
    assert _due(link["id"]) == ""                     # the tick passes over it too
    gate.set()
    deadline = time.monotonic() + 20
    while (status := owner.get(f"/api/folder-links/{link['id']}").json()["status"]).get("running") or len(rounds) < 2:
        assert time.monotonic() < deadline, f"the rounds never ended: {status} {rounds}"
        time.sleep(0.02)
    assert status["last_error"] == "" and (dest / "FL now paper.md").is_file()
    assert rounds == ["pull", ""]  # the one asked for first, with what it asked, then the one asked meanwhile
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_a_links_running_is_a_round_holding_it_however_long_that_round_runs(owner, disk, monkeypatch):
    """``running`` reads as the link's lock, not the mark's age: a round 15
    minutes in still reads running (a client following Sync now waits for
    it, the tick passes over it, and a sync asked meanwhile is answered by
    it and kept for after it), and a mark nothing holds (a server stopped
    mid-round left it) reads not running, so the link is the tick's again at
    once."""
    folder = make_folder(owner, "FL long")
    make_page(owner, "FL long paper", {"folders": [folder]})
    link = _create(owner, folder=folder, path=str(disk / "Long"))
    monkeypatch.setattr(folder_links, "run_in_background", REAL_BACKGROUND)
    kept = []
    lock = folder_links._lock(link["id"])
    assert lock.acquire(blocking=False)  # a round of this process holds the link
    try:
        folder_links._write_status(link["id"], {"running": True, "started_at": time.time() - 900})
        status = owner.get(f"/api/folder-links/{link['id']}").json()["status"]
        assert status["running"] is True
        assert _due(link["id"]) == ""
        again = owner.post(f"/api/folder-links/{link['id']}/sync").json()["status"]  # answered by that round
        assert again["running"] is True and again["started_at"] == status["started_at"]
    finally:
        monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: kept.append(kw))
        folder_links._let_go(link["id"], lock)
    assert kept == [{"force": "", "direction": "both", "confirm": False}]  # the sync asked meanwhile, after it
    # the same mark with nothing holding the lock: what a server stopped mid-round leaves
    assert owner.get(f"/api/folder-links/{link['id']}").json()["status"]["running"] is False
    assert _due(link["id"]) == "both"  # never synced: the tick runs it now
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_a_sync_asked_while_a_page_round_holds_the_vault_reads_running_until_its_own_round_ends(owner, disk,
                                                                                               monkeypatch):
    """A page's round of the hot loop, a write from the file editor or a
    relink holds the link's lock and marks nothing running: a sync asked
    then is kept for after it (``_again``) and the link reads ``running``
    from the answer on, so a client following Sync now waits for that round
    and finds its outcome, not the last round's."""
    folder = make_folder(owner, "FL held")
    make_page(owner, "FL held paper", {"folders": [folder]})
    dest = disk / "Held"
    link = _create(owner, folder=folder, path=str(dest))
    assert _sync(owner, link["id"])["counts"]["added"] == 1
    make_page(owner, "FL held second", {"folders": [folder]})
    monkeypatch.setattr(folder_links, "run_in_background", REAL_BACKGROUND)
    lock = folder_links._lock(link["id"])
    assert lock.acquire(blocking=False)  # what a page's round does, with no mark
    try:
        assert owner.get(f"/api/folder-links/{link['id']}").json()["status"]["running"] is False
        asked = owner.post(f"/api/folder-links/{link['id']}/sync").json()["status"]
        assert asked["running"] is True and asked["counts"]["added"] == 1   # the last round's counts, still running
        assert owner.get(f"/api/folder-links/{link['id']}").json()["status"]["running"] is True
    finally:
        folder_links._let_go(link["id"], lock)
    deadline = time.monotonic() + 20
    while (status := owner.get(f"/api/folder-links/{link['id']}").json()["status"]).get("running"):
        assert time.monotonic() < deadline, f"the round never ended: {status}"
        time.sleep(0.02)
    assert status["last_error"] == "" and (dest / "FL held second.md").is_file()   # its own round's outcome
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_a_vaults_round_shows_its_progress_while_it_runs(owner, disk, monkeypatch):
    """A vault's round writes how far it is into the status as it goes
    (``progress``: the files push takes, then the pages pull looks at, and
    the bytes of the PDFs pushed), once it has run ``PROGRESS_S``, then at
    most that often and once all is done; a quicker round writes none, the
    round's own status at the end has none, and a mark no round holds shows
    none."""
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    dest, sizes = disk / "FL progress", []
    for name in ("A paper.pdf", "B paper.pdf"):
        data = _paper_pdf(f"FL progress {name}")
        (dest / name).parent.mkdir(parents=True, exist_ok=True)
        (dest / name).write_bytes(data)
        sizes.append(len(data))
    (dest / "Thoughts.md").write_text("a thought\n", encoding="utf-8")
    then = time.time() - 10
    for p in dest.iterdir():
        os.utime(p, (then, then))
    link = _create(owner, path=str(dest), new_folder=True, push="everything")
    written, real = [], folder_links._write_status

    def write(link_id, status, **kw):
        if "progress" in status:
            written.append(status)
        real(link_id, status, **kw)

    monkeypatch.setattr(folder_links, "_write_status", write)
    monkeypatch.setattr(folder_links, "PROGRESS_S", 0)
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["read"] == 3 and "progress" not in status
    assert {s["running"] for s in written} == {True} and all(s["started_at"] for s in written)
    shown = [s["progress"] for s in written]
    pdfs = sum(sizes)
    assert shown[0] == {"done": 0, "total": 3, "path": "", "bytes": {"done": 0, "total": pdfs}}   # push's files
    assert shown[-1] == {"done": 6, "total": 6, "path": "", "bytes": {"done": pdfs, "total": pdfs}}  # then pull's pages
    assert [p["done"] for p in shown] == sorted(p["done"] for p in shown)
    assert {p["path"] for p in shown if p["path"].endswith(".pdf")} == {"A paper.pdf", "B paper.pdf"}
    assert "progress" not in owner.get(f"/api/folder-links/{link['id']}").json()["status"]

    # a round quicker than PROGRESS_S (the tick's of a quiet vault) writes none
    written.clear()
    monkeypatch.setattr(folder_links, "PROGRESS_S", 3600)
    assert _sync(owner, link["id"])["last_error"] == "" and written == []

    # a mark a stopped server left, its progress with it: neither reads as a round
    real(link["id"], {"running": True, "started_at": time.time(), "progress": {"done": 1, "total": 5}})
    status = owner.get(f"/api/folder-links/{link['id']}").json()["status"]
    assert status["running"] is False and "progress" not in status
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_path_must_be_a_full_one_of_its_own_outside_the_data_directory(owner, disk):
    folder = make_folder(owner, "FL paths")
    make_page(owner, "FL p", {"folders": [folder]})
    for typed in ("Papers", "Papers/Quantum", "../up"):
        r = owner.post("/api/folder-links", json={"folder": folder, "path": typed})
        assert r.status_code == 400 and "full path" in r.json()["detail"], typed
    assert owner.post("/api/folder-links", json={"folder": folder}).status_code == 422          # a path is required
    assert owner.post("/api/folder-links", json={"folder": folder, "path": str(disk.anchor)}).status_code == 400
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(config.DATA_DIR / "workspaces" / "x")})
    assert r.status_code == 400 and "data directory" in r.json()["detail"]
    assert owner.post("/api/folder-links", json={"folder": "no-such-folder", "path": str(disk / "x")}).status_code == 400

    link = _create(owner, folder=folder, path=str(disk / "First"))
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "first")})   # another link's, ignoring case
    assert r.status_code == 400 and "by another synced vault" in r.json()["detail"]
    other = _create(owner, folder=folder, path=str(disk / "Second"))                           # the same folder twice
    _sync(owner, link["id"])
    _sync(owner, other["id"])
    assert (disk / "First" / "FL p.md").is_file() and (disk / "Second" / "FL p.md").is_file()

    # a directory a gamma-sync client kept a folder in (its state file): that folder's again, another's refused
    kept = disk / "Kept"
    kept.mkdir()
    (kept / gamma_sync.STATE).write_text(json.dumps({
        "format": gamma_sync.FORMAT, "server": "local", "workspace": workspace_of("fl_owner"), "folder": folder,
        "files": {}, "dirs": []}), encoding="utf-8")
    r = owner.post("/api/folder-links", json={"folder": make_folder(owner, "FL other"), "path": str(kept)})
    assert r.status_code == 400 and "another folder's files" in r.json()["detail"]
    again = _create(owner, folder=folder, path=str(kept))
    assert folder_links.older(folder_links.get_link(again["id"]))
    status = _sync(owner, again["id"])
    assert status["upgraded_at"] and status["counts"]["added"] == 1 and (kept / vault_disk.MARKER).is_file()
    assert not (kept / gamma_sync.STATE).exists()
    for l in owner.get("/api/folder-links").json()["links"]:
        owner.delete(f"/api/folder-links/{l['id']}", params={"files": "trash"}).raise_for_status()


def test_root_links_the_whole_library(owner, disk):
    make_page(owner, "FL loose")                   # at the library root: lands at the top
    link = _create(owner, folder="root", path=str(disk / "Everything"))
    assert _sync(owner, link["id"])["last_error"] == ""
    assert (disk / "Everything" / "FL loose.md").is_file()
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_a_failed_round_is_reported_and_retried_later(owner, disk, monkeypatch):
    folder = make_folder(owner, "FL failing")
    make_page(owner, "FL f", {"folders": [folder]})
    link = _create(owner, folder=folder, path=str(disk / "Failing"))
    layout = vault_sync.LocalRemote.layout
    monkeypatch.setattr(vault_sync.LocalRemote, "layout",
                        lambda self, page_id="": (_ for _ in ()).throw(vault_sync.Refused("disk full")))
    status = _sync(owner, link["id"])
    assert status["last_error"] == "disk full" and status["running"] is False and status["failed_at"]
    assert _due(link["id"]) == ""                                       # waits RETRY_S
    monkeypatch.setattr(folder_links, "RETRY_S", 0)
    assert _due(link["id"]) == "both"
    monkeypatch.setattr(vault_sync.LocalRemote, "layout", layout)
    assert _sync(owner, link["id"])["last_error"] == ""
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


@pytest.fixture
def over_http(monkeypatch):
    """The HTTP requests of the CLI and of a vault of another server routed
    through a TestClient of their own, so this server stands in for the
    "other" one a link reads."""
    http = fresh_client()

    def open_(method, path, headers, body=None):
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        r = http.request(method, path, headers=headers, content=content)
        return r.status_code, r.headers, io.BytesIO(r.content)

    monkeypatch.setattr(gamma_sync, "default_open", open_)
    return "http://testserver"


def test_a_vault_of_another_server_pulls_with_its_token(owner, disk, over_http, monkeypatch):
    folder = make_folder(owner, "FL remote")
    paper = make_page(owner, "FL far paper", {"folders": [folder]})
    _note(owner, paper["id"], "a far first thought")
    ws = workspace_of("fl_owner")
    token = create_token(account_of("fl_owner"), ws, "fl-remote", 1)
    far = disk / "Far copy"
    body = {"folder": folder, "path": str(far), "remote_url": over_http + "/", "token": token["token"], "token_id": token["id"]}
    link = _create(owner, **body, workspace=ws)
    assert link["remote_url"] == over_http and link["workspace_id"] == ws and link["token_id"] == token["id"]
    assert "token" not in link and link["dest"] == str(far.resolve()) and (far / vault_disk.MARKER).is_file()
    listed = owner.get("/api/folder-links", headers={"X-Gamma-Workspace": ""}).json()["links"]
    assert [(l["id"], l["remote_url"]) for l in listed] == [(link["id"], over_http)] and "token" not in listed[0]
    with folder_links.connect_users_db() as conn:          # stored sealed, read back whole
        sealed = conn.execute("SELECT token FROM folder_links WHERE id = ?", (link["id"],)).fetchone()[0]
    assert sealed and sealed != token["token"] and folder_links.get_link(link["id"], with_token=True)["token"] == token["token"]

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["added"] == 1 and status["folder_path"] == ["FL remote"]
    assert "a far first thought" in (far / "FL far paper.md").read_text(encoding="utf-8")
    assert not (far / gamma_sync.STATE).exists()

    # between rounds the other server is asked, at most every REMOTE_POLL_S, whether its feed moved
    assert _due(link["id"]) == ""
    _note(owner, paper["id"], "a far thought")
    assert _due(link["id"]) == ""                                       # asked a moment ago
    monkeypatch.setattr(folder_links, "REMOTE_POLL_S", 0)
    assert _due(link["id"]) == "both"
    run_link, started = folder_links.run_link, []  # the tick hands a remote link's round to a thread of its own
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append((link_id, kw)))
    monkeypatch.setattr(folder_links, "run_link", lambda link_id, **kw: pytest.fail("a remote round ran in the tick"))
    folder_links.tick()
    assert (link["id"], {"direction": "both"}) in started
    monkeypatch.setattr(folder_links, "run_link", run_link)
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    run_link(link["id"])                           # what that thread runs
    assert "a far thought" in (far / "FL far paper.md").read_text(encoding="utf-8")

    # a refused token is a failed round, reported and retried later; so is one checked at creation
    owner.delete(f"/api/integrations/tokens/{token['id']}").raise_for_status()
    _note(owner, paper["id"], "unseen")
    assert _due(link["id"]) == ""
    after = folder_links.get_link(link["id"])
    assert "refused the token" in after["status"]["last_error"] and after["status"]["failed_at"]
    second = {**body, "path": str(disk / "Far copy 2")}
    r = owner.post("/api/folder-links", json=second)
    assert r.status_code == 400 and "refused the token" in r.json()["detail"]
    other_token = create_token(account_of("fl_owner"), ws, "fl-remote-2", 1)
    r = owner.post("/api/folder-links", json={**second, "token": other_token["token"], "workspace": "ws-other"})
    assert r.status_code == 400 and "another workspace" in r.json()["detail"]
    r = owner.post("/api/folder-links", json={**second, "token": other_token["token"], "folder": "no-such"})
    assert r.status_code == 400 and "does not exist on the other server" in r.json()["detail"]
    assert owner.post("/api/folder-links", json={**second, "remote_url": "ftp://x"}).status_code == 400

    # the account's own: another account sees nothing of it
    make_user("fl_far_other", "pw")
    other = login("fl_far_other", "pw")
    assert other.get("/api/folder-links").json()["links"] == []
    assert other.get(f"/api/folder-links/{link['id']}").status_code == 404
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()
    assert not (far / "FL far paper.md").exists() and not (far / vault_disk.MARKER).exists()
    assert [p.name for p in (far / ".gamma" / "trash").rglob("*.md")] == ["FL far paper.md"]   # moved, never deleted


def test_who_may_use_it(owner, guest, disk):
    folder = make_folder(owner, "FL private")
    link = _create(owner, folder=folder, path=str(disk / "Private"))
    token = create_token(account_of("fl_owner"), workspace_of("fl_owner"), "fl", 1)["token"]
    assert fresh_client().get("/api/folder-links", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert guest.post("/api/folder-links", json={"folder": folder, "path": str(disk / "G")}).status_code == 403
    make_user("fl_other", "pw")
    other = login("fl_other", "pw")
    assert other.get("/api/folder-links").json()["links"] == []
    assert other.get(f"/api/folder-links/{link['id']}").status_code == 404
    assert other.delete(f"/api/folder-links/{link['id']}").status_code == 404
    assert fresh_client().get("/api/folder-links").status_code == 401
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


def test_no_other_server_keeps_folders_on_disk(owner, disk, monkeypatch):
    """A NAS or any server the desktop app did not start: every route
    answers 404 with where folders on disk are kept, and the tick leaves a
    row from before as it is. The folder reads a link elsewhere asks for
    stay open."""
    folder = make_folder(owner, "FL elsewhere")
    make_page(owner, "FL e", {"folders": [folder]})
    link = _create(owner, folder=folder, path=str(disk / "Elsewhere"))
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    for method, url in (("GET", "/api/folder-links"), ("POST", "/api/folder-links"), ("GET", f"/api/folder-links/{link['id']}"),
                        ("POST", f"/api/folder-links/{link['id']}/sync"), ("DELETE", f"/api/folder-links/{link['id']}")):
        r = owner.request(method, url, json={"folder": folder, "path": str(disk / "x")} if method == "POST" else None)
        assert r.status_code == 404 and "desktop app" in r.json()["detail"], url
    monkeypatch.setattr(folder_links, "run_link", lambda link_id, **kw: pytest.fail("a round ran where links are not kept"))
    folder_links.tick()
    assert not (disk / "Elsewhere" / "FL e.md").exists()
    assert owner.get(f"/api/sync/folders/{folder}").status_code == 200
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    owner.delete(f"/api/folder-links/{link['id']}", params={"files": "trash"}).raise_for_status()


# --- a vault's direction ------------------------------------------------------------------------

def _paper_pdf(text):
    import pymupdf

    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def test_a_vault_goes_one_way_when_asked(owner, disk):
    folder = make_folder(owner, "FL one way")
    up = owner.post("/api/uploads", files={"file": ("p.pdf", _paper_pdf("FL one way v1"), "application/pdf")}).json()
    paper = make_page(owner, "FL way paper", {"doc_id": up["doc_id"], "original_filename": "p.pdf", "folders": [folder]})
    _note(owner, paper["id"], "a first note")
    dest = disk / "One way"
    link = _create(owner, folder=folder, path=str(dest), push="pdfs")
    status = _sync(owner, link["id"])
    assert status["counts"]["added"] == 2 and status["counts"]["read"] == 0 and status["counts"]["conflicts"] == 0
    assert (dest / "FL way paper.pdf").read_bytes() == owner.get(f"/api/uploads/{up['doc_id']}.pdf").content

    (dest / "FL way paper.pdf").write_bytes(_paper_pdf("FL one way v2, built here"))
    then = time.time() - 10
    os.utime(dest / "FL way paper.pdf", (then, then))
    _note(owner, paper["id"], "a thought to pull")
    status = _sync(owner, link["id"], direction="push")             # only pushed
    assert status["counts"]["sent"] == 1 and status["counts"]["added"] == 0 and status["counts"]["updated"] == 0
    assert "a thought to pull" not in (dest / "FL way paper.md").read_text(encoding="utf-8")
    assert owner.get(f"/api/blocks/{paper['id']}").json()["properties"]["doc_id"] != up["doc_id"]
    status = _sync(owner, link["id"], direction="pull")             # only pulled
    assert status["counts"]["sent"] == 0 and "a thought to pull" in (dest / "FL way paper.md").read_text(encoding="utf-8")
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_vault_that_came_inside_another_stops_and_the_older_goes_on(owner, disk):
    outer = _create(owner, folder=make_folder(owner, "FL outer"), path=str(disk / "Outer"))
    inner = _create(owner, folder=make_folder(owner, "FL inner"), path=str(disk / "Elsewhere"))
    with connect_users_db() as conn:      # a path written before the rule
        conn.execute("UPDATE folder_links SET path = ? WHERE id = ?", (str((disk / "Outer" / "inner").resolve()), inner["id"]))
    status = _sync(owner, inner["id"])
    assert "do not overlap" in status["stopped"] and status["last_error"] == status["stopped"] and status["failed_at"]
    assert not (disk / "Outer" / "inner").exists()                    # nothing ran
    assert _due(inner["id"]) == ""                                    # it waits RETRY_S, as a failure does
    assert _sync(owner, outer["id"])["last_error"] == ""
    for link in (outer, inner):
        owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_the_hot_loop_gives_a_pulled_page_a_round_of_its_own(owner, disk, monkeypatch):
    folder = make_folder(owner, "FL hot")
    paper = make_page(owner, "FL hot paper", {"folders": [folder]})
    _note(owner, paper["id"], "a cold thought")
    link = _create(owner, folder=folder, path=str(disk / "Hot"))
    _sync(owner, link["id"])
    monkeypatch.setattr(folder_links, "HOT_S", 0)
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)    # the note pull wrote a moment ago is read as settled
    _note(owner, paper["id"], "a hot thought")
    key = (workspace_of("fl_owner"), paper["id"])
    assert key in folder_links._dirty
    folder_links.hot_tick()
    assert "a hot thought" in (disk / "Hot" / "FL hot paper.md").read_text(encoding="utf-8")
    assert key not in folder_links._dirty
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()

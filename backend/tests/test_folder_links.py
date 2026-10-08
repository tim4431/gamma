"""Folders kept on disk by the desktop app's own server (gamma/folder_links.py,
routers/folder_links.py): a link's directory where the user picked it, its
rounds by hand and from the loop's tick, a remote source read with a token
of another server, what a removal takes back, the paths it refuses, who may
use the API, and that no other server keeps links at all."""

import io
import json

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, workspace_of
from gamma import config, folder_links, gamma_sync
from gamma.integrations import create_token


@pytest.fixture(scope="module")
def owner(client):
    make_user("fl_owner", "pw")
    return login("fl_owner", "pw")


@pytest.fixture
def disk(tmp_path, monkeypatch):
    """This server as the desktop app runs it (links kept here), a place on
    "this computer" for the directories, and no background rounds: the tests
    run them inline with ``?wait=1`` or ``tick()``."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    return tmp_path / "disk"


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


def test_a_link_keeps_the_folder_where_it_was_asked_and_the_tick_follows_changes(owner, disk):
    folder = make_folder(owner, "FL lab")
    make_folder(owner, "FL lab/Sub")
    paper = make_page(owner, "FL paper", {"folders": [folder]})
    dest = disk / "FL lab"
    link = _create(owner, folder=folder, path=str(dest))
    assert link["path"] == str(dest.resolve()) and link["dest"] == str(dest.resolve()) and link["notes"] is True
    listed = owner.get("/api/folder-links").json()
    assert set(listed) == {"links"} and [(l["id"], l["remote_url"]) for l in listed["links"]] == [(link["id"], "")]

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["added"] == 1 and status["running"] is False
    assert (dest / "FL paper.md").is_file() and (dest / "Sub").is_dir()
    state = json.loads((dest / ".gamma-sync.json").read_text(encoding="utf-8"))
    assert state["folder"] == folder and state["server"] == "local" and "FL paper.md" in state["files"]
    assert owner.get(f"/api/folder-links/{link['id']}").json()["cursor"] == state["cursor"]

    # nothing changed: the tick leaves the link alone; a note written: the tick runs it
    assert folder_links.due(folder_links.get_link(link["id"])) is False
    _note(owner, paper["id"], "a thought")
    assert folder_links.due(folder_links.get_link(link["id"])) is True
    folder_links.tick()
    assert "a thought" in (dest / "FL paper.md").read_text(encoding="utf-8")
    after = folder_links.get_link(link["id"])
    assert after["status"]["counts"]["updated"] == 1 and folder_links.due(after) is False

    # notes off: the notes file leaves at the next round
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"notes": False})
    assert r.status_code == 200 and r.json()["notes"] is False
    assert _sync(owner, link["id"])["counts"]["removed"] == 1
    assert not (dest / "FL paper.md").exists()

    # removing the link with its files takes back what was written, nothing else
    owner.patch(f"/api/folder-links/{link['id']}", json={"notes": True})
    _sync(owner, link["id"])
    (dest / "mine.txt").write_text("not the sync's", encoding="utf-8")
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1})
    assert r.status_code == 200
    assert owner.get("/api/folder-links").json()["links"] == []
    assert not (dest / "FL paper.md").exists() and not (dest / "Sub").exists()
    assert (dest / "mine.txt").is_file() and not (dest / ".gamma-sync.json").exists()


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
    assert r.status_code == 400 and "Another link" in r.json()["detail"]
    other = _create(owner, folder=folder, path=str(disk / "Second"))                           # the same folder twice
    _sync(owner, link["id"])
    _sync(owner, other["id"])
    assert (disk / "First" / "FL p.md").is_file() and (disk / "Second" / "FL p.md").is_file()
    # a directory an earlier link left is adopted by the same folder, refused for another
    owner.delete(f"/api/folder-links/{other['id']}").raise_for_status()                         # files and state file stay
    assert (disk / "Second" / ".gamma-sync.json").is_file()
    again = _create(owner, folder=folder, path=str(disk / "Second"))
    assert _sync(owner, again["id"])["counts"]["unchanged"] == 1
    r = owner.post("/api/folder-links", json={"folder": make_folder(owner, "FL other"), "path": str(disk / "Second")})
    assert r.status_code == 400
    for l in owner.get("/api/folder-links").json()["links"]:
        owner.delete(f"/api/folder-links/{l['id']}", params={"remove_files": 1}).raise_for_status()


def test_root_links_the_whole_library(owner, disk):
    make_page(owner, "FL loose")                   # at the library root: lands at the top
    link = _create(owner, folder="root", path=str(disk / "Everything"))
    assert _sync(owner, link["id"])["last_error"] == ""
    assert (disk / "Everything" / "FL loose.md").is_file() and (disk / "Everything" / "FL lab").is_dir()
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()


def test_a_failed_round_is_reported_and_retried_later(owner, disk, monkeypatch):
    folder = make_folder(owner, "FL failing")
    make_page(owner, "FL f", {"folders": [folder]})
    link = _create(owner, folder=folder, path=str(disk / "Failing"))
    manifest = folder_links.LocalSource.manifest
    monkeypatch.setattr(folder_links.LocalSource, "manifest", lambda self: (_ for _ in ()).throw(gamma_sync.SyncError("disk full")))
    status = _sync(owner, link["id"])
    assert status["last_error"] == "disk full" and status["running"] is False
    assert folder_links.due(folder_links.get_link(link["id"])) is False      # waits RETRY_S
    monkeypatch.setattr(folder_links, "RETRY_S", 0)
    assert folder_links.due(folder_links.get_link(link["id"])) is True
    monkeypatch.setattr(folder_links.LocalSource, "manifest", manifest)
    assert _sync(owner, link["id"])["last_error"] == ""
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()


@pytest.fixture
def over_http(monkeypatch):
    """The engine's HTTP reads routed through a TestClient of their own, so
    this server stands in for the "other" one a remote source reads."""
    http = fresh_client()

    def open_(method, path, headers):
        r = http.request(method, path, headers=headers)
        return r.status_code, r.headers, io.BytesIO(r.content)

    monkeypatch.setattr(gamma_sync, "default_open", open_)
    return "http://testserver"


def test_a_link_with_a_remote_source_reads_that_server_with_its_token(owner, disk, over_http, monkeypatch):
    folder = make_folder(owner, "FL remote")
    paper = make_page(owner, "FL far paper", {"folders": [folder]})
    ws = workspace_of("fl_owner")
    token = create_token(account_of("fl_owner"), ws, "fl-remote", 1)
    far = disk / "Far copy"
    body = {"folder": folder, "path": str(far), "remote_url": over_http + "/", "token": token["token"], "token_id": token["id"]}
    link = _create(owner, **body, workspace=ws)
    assert link["remote_url"] == over_http and link["workspace_id"] == ws and link["token_id"] == token["id"]
    assert "token" not in link and link["dest"] == str(far.resolve())
    listed = owner.get("/api/folder-links", headers={"X-Gamma-Workspace": ""}).json()["links"]
    assert [(l["id"], l["remote_url"]) for l in listed] == [(link["id"], over_http)] and "token" not in listed[0]
    with folder_links.connect_users_db() as conn:          # stored sealed, read back whole
        sealed = conn.execute("SELECT token FROM folder_links WHERE id = ?", (link["id"],)).fetchone()[0]
    assert sealed and sealed != token["token"] and folder_links.get_link(link["id"], with_token=True)["token"] == token["token"]

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["added"] == 1 and status["folder_path"] == ["FL remote"]
    assert (far / "FL far paper.md").is_file()
    state = json.loads((far / ".gamma-sync.json").read_text(encoding="utf-8"))
    assert state["server"] == over_http and state["workspace"] == ws

    # between rounds the other server is asked, at most every REMOTE_POLL_S, whether its feed moved
    assert folder_links.due(folder_links.get_link(link["id"])) is False
    _note(owner, paper["id"], "a far thought")
    assert folder_links.due(folder_links.get_link(link["id"])) is False      # asked a moment ago
    monkeypatch.setattr(folder_links, "REMOTE_POLL_S", 0)
    assert folder_links.due(folder_links.get_link(link["id"])) is True
    run_link, started = folder_links.run_link, []  # the tick hands a remote link's round to a thread of its own
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(link_id))
    monkeypatch.setattr(folder_links, "run_link", lambda link_id, **kw: pytest.fail("a remote round ran in the tick"))
    folder_links.tick()
    assert started == [link["id"]]
    monkeypatch.setattr(folder_links, "run_link", run_link)
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    run_link(link["id"])                           # what that thread runs
    assert "a far thought" in (far / "FL far paper.md").read_text(encoding="utf-8")

    # a refused token is a failed round, reported and retried later; so is one checked at creation
    owner.delete(f"/api/integrations/tokens/{token['id']}").raise_for_status()
    _note(owner, paper["id"], "unseen")
    assert folder_links.due(folder_links.get_link(link["id"])) is False
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
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()
    assert not far.exists()


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
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()


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
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()

"""Folders kept on the server's own disk (gamma/folder_links.py,
routers/folder_links.py): a link's directory under the folders root, its
rounds by hand and from the loop's tick, what a removal takes back, the
paths it refuses, and who may use the API."""

import json

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, workspace_of
from gamma import folder_links
from gamma.integrations import create_token


@pytest.fixture(scope="module")
def owner(client):
    make_user("fl_owner", "pw")
    return login("fl_owner", "pw")


@pytest.fixture
def root(tmp_path, monkeypatch):
    """A folders root of the test's own, and no background rounds: the
    tests run them inline with ``?wait=1`` or ``tick()``."""
    monkeypatch.setenv("GAMMA_FOLDERS_DIR", str(tmp_path / "folders"))
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    return tmp_path / "folders"


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


def test_a_link_keeps_the_folder_under_the_root_and_the_tick_follows_changes(owner, root):
    folder = make_folder(owner, "FL lab")
    make_folder(owner, "FL lab/Sub")
    paper = make_page(owner, "FL paper", {"folders": [folder]})
    link = _create(owner, folder=folder)
    assert link["path"] == "FL lab" and link["dest"] == str(root / "FL lab") and link["notes"] is True
    listed = owner.get("/api/folder-links").json()
    assert listed["root"] == str(root) and [l["id"] for l in listed["links"]] == [link["id"]]

    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["added"] == 1 and status["running"] is False
    assert (root / "FL lab" / "FL paper.md").is_file() and (root / "FL lab" / "Sub").is_dir()
    state = json.loads((root / "FL lab" / ".gamma-sync.json").read_text(encoding="utf-8"))
    assert state["folder"] == folder and state["server"] == "local" and "FL paper.md" in state["files"]
    assert owner.get(f"/api/folder-links/{link['id']}").json()["cursor"] == state["cursor"]

    # nothing changed: the tick leaves the link alone; a note written: the tick runs it
    assert folder_links.due(folder_links.get_link(link["id"])) is False
    _note(owner, paper["id"], "a thought")
    assert folder_links.due(folder_links.get_link(link["id"])) is True
    folder_links.tick()
    assert "a thought" in (root / "FL lab" / "FL paper.md").read_text(encoding="utf-8")
    after = folder_links.get_link(link["id"])
    assert after["status"]["counts"]["updated"] == 1 and folder_links.due(after) is False

    # notes off: the notes file leaves at the next round; a page deleted, its files too
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"notes": False})
    assert r.status_code == 200 and r.json()["notes"] is False
    assert _sync(owner, link["id"])["counts"]["removed"] == 1
    assert not (root / "FL lab" / "FL paper.md").exists()

    # removing the link keeps the directory; removing it with its files takes back what was written
    owner.patch(f"/api/folder-links/{link['id']}", json={"notes": True})
    _sync(owner, link["id"])
    (root / "FL lab" / "mine.txt").write_text("not the sync's", encoding="utf-8")
    (root / "FL lab" / "Sub" / "FL paper 2.md")  # (nothing: Sub stays empty)
    r = owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1})
    assert r.status_code == 200
    assert owner.get("/api/folder-links").json()["links"] == []
    assert not (root / "FL lab" / "FL paper.md").exists() and not (root / "FL lab" / "Sub").exists()
    assert (root / "FL lab" / "mine.txt").is_file() and not (root / "FL lab" / ".gamma-sync.json").exists()


def test_paths_are_cleaned_unique_and_below_the_root(owner, root):
    folder = make_folder(owner, "FL paths")
    make_page(owner, "FL p", {"folders": [folder]})
    link = _create(owner, folder=folder, path="../..\\Escaped: name/")
    assert link["path"] == "Escaped name"          # dots, slashes and colons gone, nothing above the root
    assert owner.post("/api/folder-links", json={"folder": folder, "path": "escaped NAME"}).status_code == 400
    assert owner.post("/api/folder-links", json={"folder": "no-such-folder"}).status_code == 400
    assert owner.post("/api/folder-links", json={"folder": folder, "path": "///"}).status_code == 400
    other = _create(owner, folder=folder, path="Second copy")  # the same folder twice, two directories
    _sync(owner, link["id"])
    _sync(owner, other["id"])
    assert (root / "Escaped name" / "FL p.md").is_file() and (root / "Second copy" / "FL p.md").is_file()
    # a directory another folder's link wrote is refused for a new link
    owner.delete(f"/api/folder-links/{other['id']}").raise_for_status()           # files and state file stay
    assert (root / "Second copy" / ".gamma-sync.json").is_file()
    again = _create(owner, folder=folder, path="Second copy")                     # the same folder adopts it
    assert _sync(owner, again["id"])["counts"]["unchanged"] == 1
    assert owner.post("/api/folder-links", json={"folder": make_folder(owner, "FL other"), "path": "Second copy"}).status_code == 400
    for l in owner.get("/api/folder-links").json()["links"]:
        owner.delete(f"/api/folder-links/{l['id']}", params={"remove_files": 1}).raise_for_status()


def test_an_absolute_path_is_taken_only_where_links_may_go_anywhere(owner, root, tmp_path, monkeypatch):
    folder = make_folder(owner, "FL anywhere")
    make_page(owner, "FL a", {"folders": [folder]})
    mine = tmp_path / "mine" / "Papers"
    relative = _create(owner, folder=folder, path=str(mine))        # not allowed: made a name below the root
    assert relative["path"] != str(mine) and relative["dest"].startswith(str(root))
    owner.delete(f"/api/folder-links/{relative['id']}").raise_for_status()
    monkeypatch.setenv("GAMMA_FOLDERS_ANYWHERE", "1")
    assert owner.get("/api/folder-links").json()["anywhere"] is True
    link = _create(owner, folder=folder, path=str(mine))
    assert link["path"] == str(mine.resolve()) and link["dest"] == str(mine.resolve())
    assert _sync(owner, link["id"])["last_error"] == "" and (mine / "FL a.md").is_file()
    from gamma import config
    inside = config.DATA_DIR / "workspaces" / "x"
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(inside)})
    assert r.status_code == 400 and "data directory" in r.json()["detail"]
    assert owner.post("/api/folder-links", json={"folder": folder, "path": str(mine.anchor)}).status_code == 400
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()
    assert not mine.exists()


def test_root_links_the_whole_library(owner, root):
    make_page(owner, "FL loose")                   # at the library root: lands at the top
    link = _create(owner, folder="root", path="Everything")
    status = _sync(owner, link["id"])
    assert status["last_error"] == ""
    assert (root / "Everything" / "FL loose.md").is_file() and (root / "Everything" / "FL lab").is_dir()
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()


def test_a_failed_round_is_reported_and_retried_later(owner, root, monkeypatch):
    folder = make_folder(owner, "FL failing")
    make_page(owner, "FL f", {"folders": [folder]})
    link = _create(owner, folder=folder)
    monkeypatch.setattr(folder_links.LocalSource, "manifest", lambda self: (_ for _ in ()).throw(folder_links.gamma_sync.SyncError("disk full")))
    status = _sync(owner, link["id"])
    assert status["last_error"] == "disk full" and status["running"] is False
    assert folder_links.due(folder_links.get_link(link["id"])) is False      # waits RETRY_S
    monkeypatch.setattr(folder_links, "RETRY_S", 0)
    assert folder_links.due(folder_links.get_link(link["id"])) is True
    monkeypatch.undo()
    monkeypatch.setenv("GAMMA_FOLDERS_DIR", str(root))
    assert _sync(owner, link["id"])["last_error"] == ""
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()


def test_who_may_use_it(owner, guest, root):
    folder = make_folder(owner, "FL private")
    link = _create(owner, folder=folder)
    token = create_token(account_of("fl_owner"), workspace_of("fl_owner"), "fl", 1)["token"]
    assert fresh_client().get("/api/folder-links", headers={"Authorization": f"Bearer {token}"}).status_code == 403
    assert guest.post("/api/folder-links", json={"folder": folder}).status_code == 403
    make_user("fl_other", "pw")
    other = login("fl_other", "pw")
    assert other.get("/api/folder-links").json()["links"] == []
    assert other.get(f"/api/folder-links/{link['id']}").status_code == 404
    assert other.delete(f"/api/folder-links/{link['id']}").status_code == 404
    assert fresh_client().get("/api/folder-links").status_code == 401
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()

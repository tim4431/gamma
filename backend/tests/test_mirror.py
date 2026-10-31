"""The mirror (gamma/sync_engine.py, routers/mirrors.py) end to end: a
workspace of one account is the "remote", a mirror workspace of another
account follows it through the real HTTP API — the engine's transport is
an in-process TestClient carrying the write token."""

import io
import json
import time
from contextlib import closing

import pytest
from fastapi.testclient import TestClient
from fractional_indexing import generate_key_between

from conftest import account_of, login, make_user
from gamma import migrations, sync_engine
from gamma.db import connect_pages_db, connect_users_db, page_now, ws_uploads_dir
from gamma.integrations import create_token

PDF = b"%PDF-1.4 mirror test\n" + b"m" * 1500


@pytest.fixture(autouse=True)
def _transport(monkeypatch):
    """The engine talks to "the remote" through a cookie-less TestClient."""
    from gamma.app import app
    c = TestClient(app)

    def fetch(method, path, body, headers):
        r = c.request(method, path, content=body, headers=headers)
        return r.status_code, r.content

    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    # rounds run only when a test asks (the create endpoint's background fill would race the inline ones)
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: None)


class Side:
    """One account with its personal workspace and a client bound to it."""

    def __init__(self, name):
        self.name = name
        self.ws = make_user(name, "pw")
        self.id = account_of(name)
        self.client = login(name, "pw")
        self.client.headers["X-Gamma-Workspace"] = self.ws

    def bind(self, ws):
        self.ws = ws
        self.client.headers["X-Gamma-Workspace"] = ws

    def page(self, title, **props):
        r = self.client.post("/api/pages", json={"title": title, "properties": props})
        assert r.status_code == 200, r.text
        return r.json()

    def folder(self, bid, name, parent="folders"):
        """A folder made here (an insert on the ``folders`` tree); its id."""
        self.ops("folders", [{"op": "insert", "id": bid, "parent": parent, "content": name}])
        return bid

    def folders(self):
        """{id: (parent, name)} of every folder here."""
        tree = self.client.get("/api/blocks/root/children").json()["folders"]["children"]
        out, todo = {}, [("folders", n) for n in tree]
        while todo:
            parent, node = todo.pop()
            out[node["id"]] = (parent, node["content"])
            todo += [(node["id"], c) for c in node["children"]]
        return out

    def ops(self, page_id, ops):
        r = self.client.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": ops})
        assert r.status_code == 200, r.text
        return r.json()

    def insert(self, page_id, bid, content, parent=None, position=None):
        return self.ops(page_id, [{"op": "insert", "id": bid, "parent": parent or page_id,
                                   "position": position or generate_key_between(None, None), "content": content}])

    def tree(self, page_id):
        r = self.client.get(f"/api/blocks/{page_id}/subtree")
        return r.json()["block"] if r.status_code == 200 else None

    def texts(self, page_id):
        t = self.tree(page_id)
        return None if t is None else {c["id"]: c["content"] for c in t["children"]}

    def pages(self):
        return {b["id"]: b for b in self.client.get("/api/blocks/root/children").json()["children"]}


_n = [0]


def _pair(mode="two-way", scope="write"):
    """A remote side, a token on its workspace, and a local side whose
    mirror follows it (created through the API, first round run inline)."""
    _n[0] += 1
    remote = Side(f"mr_remote{_n[0]}")
    local = Side(f"mr_local{_n[0]}")
    token = create_token(remote.id, remote.ws, "mirror", 90, scope=scope)["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token, "mode": mode})
    assert r.status_code == 201, r.text
    mirror = r.json()
    local.bind(mirror["workspace_id"])
    return remote, local, mirror


def _sync(local):
    r = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    assert r.status_code == 200, r.text
    status = r.json()["status"]
    assert not status.get("last_error"), status
    return status


def test_create_validates_the_remote_and_fills_the_copy():
    remote, local, mirror = _pair()
    assert mirror["remote_ws"] == remote.ws and mirror["mode"] == "two-way"
    assert mirror["status"]["remote_user"] == remote.name and mirror["name"].endswith("(offline copy)")
    physics = remote.folder("mrPhysics", "physics")
    page = remote.page("Paper A", folders=[physics])
    remote.insert(page["id"], "blkA1", "first note")
    remote.insert(page["id"], "blkA2", "second note")
    status = _sync(local)
    assert status["pages_pulled"] >= 1
    assert local.texts(page["id"]) == {"blkA1": "first note", "blkA2": "second note"}
    assert local.pages()[page["id"]]["properties"]["folders"] == [physics]
    assert local.folders() == {physics: ("folders", "physics")}  # the tree came along
    # a second round changes nothing and pushes nothing
    status = _sync(local)
    assert status["pages_pulled"] == 0 and status["pages_pushed"] == 0
    listed = local.client.get("/api/mirrors").json()["mirrors"]
    assert [m["workspace_id"] for m in listed] == [local.ws]
    bad = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": "gamma_wrong"})
    assert bad.status_code == 400


def test_edits_flow_both_ways_and_different_blocks_merge():
    remote, local, _ = _pair()
    page = remote.page("Shared")
    remote.insert(page["id"], "b1", "one")
    remote.insert(page["id"], "b2", "two")
    _sync(local)
    # local edit → remote, under the token's account
    local.ops(page["id"], [{"op": "set", "id": "b1", "content": "one (local)"}])
    local.insert(page["id"], "b3", "three from local")
    status = _sync(local)
    assert status["pages_pushed"] == 1
    assert remote.texts(page["id"]) == {"b1": "one (local)", "b2": "two", "b3": "three from local"}
    log = remote.client.get(f"/api/pages/{page['id']}/ops?since=0").json()["batches"]
    assert log[-1]["actor"] == remote.id and log[-1]["client"] == "sync"
    # both sides edit different blocks between rounds → both survive
    remote.ops(page["id"], [{"op": "set", "id": "b2", "content": "two (remote)"}])
    local.ops(page["id"], [{"op": "set", "id": "b3", "content": "three (local again)"}])
    _sync(local)
    expect = {"b1": "one (local)", "b2": "two (remote)", "b3": "three (local again)"}
    assert remote.texts(page["id"]) == expect and local.texts(page["id"]) == expect
    # the local copy's own log marks the engine's writes
    with connect_pages_db(local.ws) as conn:
        clients = {r[0] for r in conn.execute("SELECT client FROM page_ops WHERE page_id = ?", (page["id"],))}
    assert "sync" in clients
    assert local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"] == []


def test_same_block_edits_merge_by_span_and_are_reported():
    remote, local, _ = _pair()
    page = remote.page("Same block")
    remote.insert(page["id"], "s1", "alpha beta gamma delta")
    _sync(local)
    remote.ops(page["id"], [{"op": "set", "id": "s1", "content": "ALPHA beta gamma delta"}])
    local.ops(page["id"], [{"op": "set", "id": "s1", "content": "alpha beta gamma DELTA"}])
    _sync(local)
    merged = "ALPHA beta gamma DELTA"
    assert local.texts(page["id"])["s1"] == merged and remote.texts(page["id"])["s1"] == merged
    conflicts = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"], c["mine"], c["theirs"], c["result"]) for c in conflicts] == [
        ("merged", "s1", "alpha beta gamma DELTA", "ALPHA beta gamma delta", merged)]
    assert conflicts[0]["base"] == "alpha beta gamma delta"  # so the resolver can show what each side changed
    # choosing "mine" writes it back as an ordinary edit, which the next round pushes
    r = local.client.post(f"/api/mirrors/{local.ws}/conflicts/{conflicts[0]['id']}", json={"choice": "mine"})
    assert r.status_code == 200
    _sync(local)
    assert remote.texts(page["id"])["s1"] == "alpha beta gamma DELTA"
    assert local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"] == []


def test_an_edit_beats_a_delete_in_both_directions():
    remote, local, _ = _pair()
    page = remote.page("Edit vs delete")
    remote.insert(page["id"], "d1", "remote will delete me")
    remote.insert(page["id"], "d2", "local will delete me")
    remote.insert(page["id"], "d3", "untouched, deleted remotely")
    _sync(local)
    remote.ops(page["id"], [{"op": "delete", "id": "d1"}, {"op": "delete", "id": "d3"},
                            {"op": "set", "id": "d2", "content": "remote edited"}])
    local.ops(page["id"], [{"op": "set", "id": "d1", "content": "local edited"}, {"op": "delete", "id": "d2"}])
    _sync(local)
    expect = {"d1": "local edited", "d2": "remote edited"}
    assert local.texts(page["id"]) == expect and remote.texts(page["id"]) == expect
    kinds = sorted((c["kind"], c["block_id"]) for c in
                   local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"])
    assert kinds == [("kept_local_edit", "d1"), ("restored_remote_edit", "d2")]


def test_pages_come_and_go_on_both_sides():
    remote, local, _ = _pair()
    keep = remote.page("Keep")
    gone_remote = remote.page("Deleted remotely")
    gone_local = remote.page("Deleted locally")
    edited_then_deleted = remote.page("Edited here, deleted there")
    for p in (keep, gone_remote, gone_local, edited_then_deleted):
        remote.insert(p["id"], f"{p['id']}_c", "note")
    _sync(local)
    # new pages on each side
    new_remote = remote.page("New remote", folders=[remote.folder("mrIn", "in")])
    new_local = local.page("New local")
    local.insert(new_local["id"], "nl1", "local note")
    # deletions
    remote.client.delete(f"/api/blocks/{gone_remote['id']}").raise_for_status()
    local.client.delete(f"/api/blocks/{gone_local['id']}").raise_for_status()
    # deleted there, edited here → comes back there
    local.ops(edited_then_deleted["id"], [{"op": "set", "id": f"{edited_then_deleted['id']}_c", "content": "kept"}])
    remote.client.delete(f"/api/blocks/{edited_then_deleted['id']}").raise_for_status()
    _sync(local)
    lp, rp = local.pages(), remote.pages()
    assert new_remote["id"] in lp and lp[new_remote["id"]]["properties"]["folders"] == ["mrIn"]
    assert local.folders()["mrIn"] == ("folders", "in")
    assert new_local["id"] in rp and remote.texts(new_local["id"]) == {"nl1": "local note"}
    assert gone_remote["id"] not in lp and gone_local["id"] not in rp
    assert edited_then_deleted["id"] in rp and remote.texts(edited_then_deleted["id"]) == {
        f"{edited_then_deleted['id']}_c": "kept"}
    kinds = {c["kind"] for c in local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]}
    assert kinds == {"page_restored"}
    # renaming a page is a root set that travels
    remote.ops(keep["id"], [{"op": "set", "id": keep["id"], "content": "Keep (renamed)"}])
    _sync(local)
    assert local.pages()[keep["id"]]["content"] == "Keep (renamed)"


def test_the_folder_and_label_trees_travel_like_pages():
    """The pseudo-pages ``folders`` / ``labels`` are in both feeds and are
    reconciled three ways like a page, before the pages: a rename, a move,
    a new folder on either side go over, both sides' new folders are kept,
    and a page filed in a new folder arrives with its folder there."""
    remote, local, _ = _pair()
    top = remote.folder("mtTop", "Top")
    remote.folder("mtSub", "Sub", parent=top)
    remote.ops("labels", [{"op": "insert", "id": "mtLabel", "parent": "labels", "content": "todo"}])
    _sync(local)
    assert local.folders() == {"mtTop": ("folders", "Top"), "mtSub": ("mtTop", "Sub")}
    labels = local.client.get("/api/blocks/labels/subtree").json()["block"]["children"]
    assert [(n["id"], n["content"]) for n in labels] == [("mtLabel", "todo")]

    # both sides change the tree between rounds
    remote.ops("folders", [{"op": "set", "id": "mtTop", "content": "Top (renamed there)"}])
    local.ops("folders", [{"op": "move", "id": "mtSub", "parent": "folders"}])
    local.folder("mtMine", "Made here")
    remote.folder("mtTheirs", "Made there")
    filed = local.page("Filed in a new folder", folders=["mtMine"], labels=["mtLabel"])
    _sync(local)
    expect = {"mtTop": ("folders", "Top (renamed there)"), "mtSub": ("folders", "Sub"),
              "mtMine": ("folders", "Made here"), "mtTheirs": ("folders", "Made there")}
    assert local.folders() == expect and remote.folders() == expect
    assert remote.pages()[filed["id"]]["properties"] == {"folders": ["mtMine"], "labels": ["mtLabel"]}
    # a folder deleted there goes here; the page refiled there comes back unfiled
    assert remote.client.delete("/api/folders/mtMine").json()["pages"] == [filed["id"]]
    _sync(local)
    assert "mtMine" not in local.folders() and "folders" not in local.pages()[filed["id"]]["properties"]
    # a second round has nothing to do
    status = _sync(local)
    assert status["pages_pulled"] == 0 and status["pages_pushed"] == 0
    names = {row["page_id"]: row["title"] for row in local.client.get(f"/api/mirrors/{local.ws}/log").json()["changes"]}
    assert names["folders"] == "Folders"


def test_files_travel_by_hash():
    remote, local, _ = _pair()
    up = remote.client.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(PDF), "application/pdf")}).json()
    page = remote.client.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "paper.pdf"}).json()
    _sync(local)
    assert (ws_uploads_dir(local.ws) / f"{up['doc_id']}.pdf").read_bytes() == PDF
    assert local.pages()[page["id"]]["properties"]["doc_id"] == up["doc_id"]
    # a file added on the local copy reaches the remote when its block does
    img = local.client.post("/api/upload-file", files={"file": ("pic.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"p" * 100), "image/png")}).json()
    name = img["url"].rsplit("/", 1)[1]
    local.insert(page["id"], "imgblk", f"![pic]({img['url']})")
    status = _sync(local)
    assert status["files_pushed"] == 1
    assert (ws_uploads_dir(remote.ws) / name).is_file()
    assert remote.texts(page["id"])["imgblk"] == f"![pic]({img['url']})"


def _recorded(monkeypatch):
    """The paths the engine asks the remote for, recorded around the
    in-process transport."""
    seen, fetch = [], sync_engine.default_fetch

    def recording(method, path, body, headers):
        seen.append(f"{method} {path}")
        return fetch(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", recording)
    return seen


def test_a_large_file_is_pushed_in_parts(monkeypatch):
    """Past what one request carries through a proxy (Cloudflare caps a
    body at 100 MB) a file goes over the remote's /api/uploads/parts, a
    PDF as a PDF and another file as a file, stored under the same name."""
    remote, local, _ = _pair()
    monkeypatch.setattr(sync_engine, "PART_BYTES", 1000)
    monkeypatch.setattr(sync_engine, "PUSH_PART_BYTES", 700)
    page = remote.page("Parts")
    _sync(local)
    seen = _recorded(monkeypatch)
    pdf = local.client.post("/api/uploads", files={"file": ("big.pdf", io.BytesIO(PDF + b"q" * 500), "application/pdf")}).json()
    data = b"\x89PNG\r\n\x1a\n" + b"r" * 1800
    img = local.client.post("/api/upload-file", files={"file": ("big.png", io.BytesIO(data), "image/png")}).json()
    local.insert(page["id"], "partpdf", f"[big.pdf]({pdf['source_url']})")
    local.insert(page["id"], "partimg", f"![big]({img['url']})")
    assert _sync(local)["files_pushed"] == 2
    assert (ws_uploads_dir(remote.ws) / f"{pdf['doc_id']}.pdf").read_bytes() == PDF + b"q" * 500
    assert (ws_uploads_dir(remote.ws) / img["url"].rsplit("/", 1)[1]).read_bytes() == data
    parts = [s for s in seen if s.startswith("POST /api/uploads/parts/") and not s.endswith("/finish")]
    assert len(parts) == 3 + 3, seen  # 2,021 and 1,808 bytes in parts of 700
    assert "POST /api/uploads" not in seen and "POST /api/upload-file" not in seen


def test_a_remote_without_parts_for_files_takes_them_whole(monkeypatch):
    """An older remote takes every upload in parts for a PDF (it echoes no
    ``kind``): the engine drops that upload and sends the file whole."""
    remote, local, _ = _pair()
    monkeypatch.setattr(sync_engine, "PART_BYTES", 1000)
    page = remote.page("Old remote")
    _sync(local)
    seen, fetch = [], sync_engine.default_fetch

    def older(method, path, body, headers):
        seen.append(f"{method} {path}")
        status, out = fetch(method, path, body, headers)
        if path == "/api/uploads/parts" and status == 200:
            out = json.dumps({k: v for k, v in json.loads(out).items() if k != "kind"}).encode()
        return status, out

    monkeypatch.setattr(sync_engine, "default_fetch", older)
    data = b"\x89PNG\r\n\x1a\n" + b"s" * 1800
    img = local.client.post("/api/upload-file", files={"file": ("old.png", io.BytesIO(data), "image/png")}).json()
    local.insert(page["id"], "oldimg", f"![old]({img['url']})")
    assert _sync(local)["files_pushed"] == 1
    assert (ws_uploads_dir(remote.ws) / img["url"].rsplit("/", 1)[1]).read_bytes() == data
    assert "POST /api/upload-file" in seen and any(s.startswith("DELETE /api/uploads/parts/") for s in seen)


def test_a_file_missing_from_the_copy_is_fetched_again():
    """A round cut short after a page landed but before its PDF did (or a
    file lost on disk) leaves a page that cannot open; the next round
    fetches the file, whether or not the page changed since."""
    remote, local, _ = _pair()
    up = remote.client.post("/api/uploads", files={"file": ("paper.pdf", io.BytesIO(PDF), "application/pdf")}).json()
    remote.client.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "paper.pdf"}).json()
    _sync(local)
    path = ws_uploads_dir(local.ws) / f"{up['doc_id']}.pdf"
    assert path.is_file()
    # The pulled PDF's manifest walk (pdf_meta.schedule) runs on a background
    # thread and may still hold the file open; Windows refuses the unlink
    # until it closes.
    for _ in range(40):
        try:
            path.unlink()
            break
        except PermissionError:
            time.sleep(0.05)
    assert sync_engine.missing_uploads(local.ws) == {f"{up['doc_id']}.pdf"}
    status = _sync(local)  # nothing changed on either side, the file still comes back
    assert status["files_pulled"] == 1 and path.read_bytes() == PDF
    assert sync_engine.missing_uploads(local.ws) == set()


def test_rounds_reuse_whoami_until_one_fails(monkeypatch):
    """A round trusts the last round's whoami (WHOAMI_TTL_S); a round that
    fails forgets it, so the next one asks again."""
    remote, local, _ = _pair()
    calls = []
    inner = sync_engine.default_fetch
    fail = [False]

    def fetch(method, path, body, headers):
        calls.append(path.split("?")[0])
        if fail[0] and path.startswith("/api/sync/changes"):
            fail[0] = False
            return 503, b'{"detail": "down for a moment"}'
        return inner(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    _sync(local)
    _sync(local)
    assert calls.count("/api/sync/whoami") == 1 and calls.count("/api/sync/changes") == 2
    fail[0] = True
    r = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    assert "down for a moment" in r.json()["status"]["last_error"]
    calls.clear()
    _sync(local)
    assert calls.count("/api/sync/whoami") == 1
    # the pill's conflict fingerprint rides on the mirror's answer
    info = local.client.get(f"/api/mirrors/{local.ws}").json()
    assert info["conflicts_open"] == 0 and info["conflicts_newest"] == 0


def test_a_remote_with_another_data_format_pauses_the_round(monkeypatch):
    """A remote whose feed names another workspace data format than this
    server's (one of the two runs a release with a workspace migration step
    the other lacks) neither gives nor gets anything: the round stops before
    any write, its status says which side to update, local edits wait, and
    the next round once the two agree carries both sides' changes."""
    remote, local, _ = _pair()
    page = remote.page("Formats")
    remote.insert(page["id"], "fm1", "text")
    _sync(local)
    here = migrations.workspace_format()
    assert remote.client.get("/api/sync/changes").json()["format"] == here
    told = ["unset"]  # the format the remote's feed names: an int, None for none (a release before the check), "unset" as it is
    inner = sync_engine.default_fetch

    def fetch(method, path, body, headers):
        code, content = inner(method, path, body, headers)
        if path.startswith("/api/sync/changes") and told[0] != "unset":
            out = {k: v for k, v in json.loads(content).items() if k != "format"}
            content = json.dumps(out if told[0] is None else {**out, "format": told[0]}).encode()
        return code, content

    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    remote.ops(page["id"], [{"op": "set", "id": "fm1", "content": "text (remote)"}])
    local.insert(page["id"], "fm2", "made here")
    for there, update in ((here + 1, "this server is updated"), (here - 1, "the remote is updated")):
        told[0] = there
        status = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").json()["status"]
        assert status["version_gap"] == {"here": here, "remote": there}
        assert update in status["last_error"] and not status["running"]
        assert local.texts(page["id"]) == {"fm1": "text", "fm2": "made here"}
        assert remote.texts(page["id"]) == {"fm1": "text (remote)"}
        info = local.client.get(f"/api/mirrors/{local.ws}").json()
        assert info["pending_local"] is True and info["status"]["version_gap"]["remote"] == there
    told[0] = None  # a remote from before the check names no format: synced as before
    status = _sync(local)
    assert "version_gap" not in status
    assert local.texts(page["id"]) == remote.texts(page["id"]) == {"fm1": "text (remote)", "fm2": "made here"}


def test_pull_only_with_a_read_token():
    remote, local, mirror = _pair(scope="read")
    assert mirror["mode"] == "pull"
    page = remote.page("Read only")
    remote.insert(page["id"], "r1", "alpha beta gamma delta")
    _sync(local)
    assert local.texts(page["id"]) == {"r1": "alpha beta gamma delta"}
    local.ops(page["id"], [{"op": "set", "id": "r1", "content": "ALPHA beta gamma delta"}])
    status = _sync(local)
    assert status["pages_pushed"] == 0 and remote.texts(page["id"]) == {"r1": "alpha beta gamma delta"}
    # the local edit survives a later remote change to another span of the same block
    remote.ops(page["id"], [{"op": "set", "id": "r1", "content": "alpha beta gamma DELTA"}])
    _sync(local)
    assert local.texts(page["id"])["r1"] == "ALPHA beta gamma DELTA"
    assert remote.texts(page["id"])["r1"] == "alpha beta gamma DELTA"


def test_the_sync_log_names_what_a_round_did():
    remote, local, _ = _pair()
    page = remote.page("Logged")
    remote.insert(page["id"], "lg1", "one")
    _sync(local)
    local.ops(page["id"], [{"op": "set", "id": "lg1", "content": "one (local)"}])
    gone = remote.page("Gone")
    _sync(local)
    remote.client.delete(f"/api/blocks/{gone['id']}").raise_for_status()
    _sync(local)
    log = local.client.get(f"/api/mirrors/{local.ws}/log").json()["changes"]
    actions = [(c["action"], c["title"]) for c in log]
    assert actions[0] == ("deleted here", "Gone")  # newest first
    # a round works its pages in id order, so the middle two may swap
    assert set(actions[1:3]) == {("created here", "Gone"), ("pushed", "Logged")}
    assert actions[3] == ("created here", "Logged")
    assert log[0]["exists"] is False and next(c for c in log if c["action"] == "pushed")["exists"] is True
    # git-style counts per row: the blocks a page gained, lost or changed
    by = {(c["action"], c["title"]): c["stats"] for c in log}
    assert by[("created here", "Logged")] == {"add": 1, "del": 0, "mod": 0}
    assert by[("pushed", "Logged")] == {"add": 0, "del": 0, "mod": 1}
    assert by[("created here", "Gone")] == {"add": 0, "del": 0, "mod": 0}
    # and what each edit did, block by block
    changes = {(c["action"], c["title"]): c["changes"] for c in log}
    assert changes[("pushed", "Logged")] == [{"k": "mod", "id": "lg1", "old": "one", "text": "one (local)"}]
    assert changes[("created here", "Logged")] == [{"k": "add", "id": "lg1", "text": "one"}]
    assert changes[("created here", "Gone")] == []
    info = local.client.get(f"/api/mirrors/{local.ws}").json()
    assert info["conflicts_open"] == 0 and info["status"]["last_sync"]
    # a subtree deleted on the original counts every block it took, and the round adds the counts up
    remote.insert(page["id"], "lg2", "parent")
    remote.insert(page["id"], "lg3", "child", parent="lg2")
    _sync(local)
    remote.ops(page["id"], [{"op": "delete", "id": "lg2"}, {"op": "insert", "id": "lg4", "parent": page["id"],
                            "position": "a5", "content": "new"}])
    st = _sync(local)
    log = local.client.get(f"/api/mirrors/{local.ws}/log?limit=1").json()["changes"]
    assert (log[0]["action"], log[0]["stats"]) == ("pulled", {"add": 1, "del": 2, "mod": 0})
    assert sorted((c["k"], c["id"], c["text"]) for c in log[0]["changes"]) == [
        ("add", "lg4", "new"), ("del", "lg2", "parent"), ("del", "lg3", "child")]
    assert (st["blocks_added"], st["blocks_removed"], st["blocks_changed"]) == (1, 2, 0)


def test_a_round_reports_its_progress_and_an_interrupted_one_is_reset(monkeypatch):
    remote, local, _ = _pair()
    for i in range(3):
        remote.page(f"Progress {i}")
    seen = []
    real = sync_engine._sync_page

    def spy(ws, *a, **kw):
        seen.append(sync_engine.get_mirror(ws)["status"]["progress"])
        return real(ws, *a, **kw)

    monkeypatch.setattr(sync_engine, "_sync_page", spy)
    _sync(local)
    assert [(p["done"], p["total"], p["first"]) for p in seen] == [(0, 3, True), (1, 3, True), (2, 3, True)]
    info = local.client.get(f"/api/mirrors/{local.ws}").json()
    assert info["status"]["running"] is False and "progress" not in info["status"]
    assert info["interval_s"] == 0  # the tests run with GAMMA_SYNC_INTERVAL=0
    # a later round is no longer "first"
    remote.page("Later")
    seen.clear()
    _sync(local)
    assert seen and all(p["first"] is False for p in seen)
    # the server stopped in the middle of a round: the flag it left is reset at startup
    sync_engine._save(local.ws, status={**info["status"], "running": True, "started_at": "2026-01-01T00:00:00.000000Z",
                                        "progress": {"done": 1, "total": 9}})
    sync_engine.reset_interrupted()
    st = sync_engine.get_mirror(local.ws)["status"]
    assert st["running"] is False and st["interrupted"] is True and "progress" not in st
    assert "interrupted" not in _sync(local)  # the next round clears the note


def test_a_mirror_status_that_does_not_read_keeps_no_other_from_its_reset(client):
    make_user("mr_status_reset", "pw-mr_status_reset")
    spaces = [f"mrStatus{n}" for n in range(2)]
    with connect_users_db() as conn:
        for ws, status in zip(spaces, ("{not json", json.dumps({"running": True, "progress": {"done": 1}}))):
            conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, status, created_at) "
                         "VALUES (?, 'http://testserver', 'r', '', ?, ?, ?)",
                         (ws, account_of("mr_status_reset"), status, page_now()))
        conn.commit()
    try:
        sync_engine.reset_interrupted()
        with connect_users_db() as conn:
            status = json.loads(conn.execute("SELECT status FROM mirrors WHERE workspace_id = ?",
                                             (spaces[1],)).fetchone()[0])
        assert status == {"running": False, "interrupted": True}
    finally:
        with connect_users_db() as conn:
            conn.execute(f"DELETE FROM mirrors WHERE workspace_id IN ({','.join('?' * len(spaces))})", spaces)
            conn.commit()


def test_a_mirrors_status_that_does_not_read_is_patched_over(client):
    make_user("mr_status_unread", "pw-mr_status_unread")
    owner = account_of("mr_status_unread")
    ws = "mrStatusBad"
    with connect_users_db() as conn:
        conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, mode, status, "
                     "created_at) VALUES (?, 'http://testserver', 'r', '', ?, 'pull', '{not json', ?)",
                     (ws, owner, page_now()))
        conn.commit()
    try:  # read as no status, as the shared table reads it
        assert sync_engine.get_mirror(ws)["status"] == {} and sync_engine.list_mirrors(owner)[0]["status"] == {}
        assert sync_engine._patch_status(ws, {"last_error": ""}, drop=("running",)) == {"last_error": ""}
        with connect_users_db() as conn:
            assert json.loads(conn.execute("SELECT status FROM mirrors WHERE workspace_id = ?",
                                           (ws,)).fetchone()[0]) == {"last_error": ""}
        assert sync_engine._stored_mode(ws) == "pull" and sync_engine._stored_mode("mrNoMirror") == "off"
        sync_engine._filters[ws] = frozenset({"x"})
        sync_engine._save(ws, page_filter=json.dumps(["a"]))
        assert ws not in sync_engine._filters  # a new filter is read again
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM mirrors WHERE workspace_id = ?", (ws,))
            conn.commit()


def test_a_mirror_conflict_can_be_written_resolved_and_says_which_row_it_is(client):
    ws = make_user("mr_conflicts_resolved", "pw-mr_conflicts_resolved")
    with closing(connect_pages_db(ws)) as conn:
        first = sync_engine._conflict(conn, "p", "b", "diverged", mine="a", theirs="b")
        done = sync_engine._conflict(conn, "p", "b2", "merged", mine="a", theirs="b", resolved=True)
        again = sync_engine._conflict(conn, "p", "b", "diverged", mine="a", theirs="b", once=True)
        rows = dict(conn.execute("SELECT id, resolved FROM sync_conflicts").fetchall())
    assert again is None and rows == {first: 0, done: 1}


def test_an_unexpected_error_in_one_page_does_not_stick_the_round(monkeypatch):
    remote, local, _ = _pair()
    bad = remote.page("Explodes")
    good = remote.page("Fine")
    real = sync_engine._sync_page

    def boom(ws, remote_, page_id, **kw):
        if page_id == bad["id"]:
            raise KeyError("a bug in the engine")
        return real(ws, remote_, page_id, **kw)

    monkeypatch.setattr(sync_engine, "_sync_page", boom)
    r = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    status = r.json()["status"]
    assert status["running"] is False and bad["id"] in status["last_error"]
    assert good["id"] in local.pages() and bad["id"] not in local.pages()
    monkeypatch.setattr(sync_engine, "_sync_page", real)
    assert list(status["retry"]) == [bad["id"]]
    _sync(local)  # retried next time from the mirror's retry list (the feed's cursor has moved past it)
    assert sync_engine.get_mirror(local.ws)["status"]["retry"] == {}
    assert bad["id"] in local.pages()


def test_detach_keeps_the_link_and_relink_merges_three_ways():
    remote, local, _ = _pair()
    page = remote.page("Detached")
    remote.insert(page["id"], "dt1", "one two three")
    remote.insert(page["id"], "dt2", "untouched")
    _sync(local)
    assert local.client.post(f"/api/mirrors/{local.ws}/detach").json()["detached"] is True
    # detached: an ordinary workspace again (no pill), no round runs
    assert not next(w for w in local.client.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == local.ws)["mirror_of"]
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["mode"] == "off"
    remote.ops(page["id"], [{"op": "set", "id": "dt1", "content": "ONE two three"}])
    local.ops(page["id"], [{"op": "set", "id": "dt1", "content": "one two THREE"}])
    local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    assert remote.texts(page["id"])["dt1"] == "ONE two three" and local.texts(page["id"])["dt1"] == "one two THREE"
    # relink with the stored token: what both sides did meanwhile merges from the saved base
    r = local.client.post(f"/api/mirrors/{local.ws}/relink", json={})
    assert r.status_code == 200 and r.json()["mode"] == "two-way", r.text
    _sync(local)
    assert local.texts(page["id"])["dt1"] == "ONE two THREE" == remote.texts(page["id"])["dt1"]
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["conflicts_open"] == 1  # the same-block merge, to look at
    assert next(w for w in local.client.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == local.ws)["mirror_of"]


def test_link_an_existing_workspace_adopts_one_side_and_keeps_the_other_text():
    remote = Side("mr_link_remote")
    local = Side("mr_link_local")
    page = remote.page("Shared id")
    remote.insert(page["id"], "lk1", "the original's text")
    remote.page("Only there")
    # the local workspace already holds the same page id with a different text, and a page of its own
    local.client.post("/api/pages", json={"id": page["id"], "title": "Shared id"}).raise_for_status()
    local.insert(page["id"], "lk1", "the copy's text")
    mine = local.page("Only here")
    token = create_token(remote.id, remote.ws, "link", 90, scope="write")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws, "adopt": "theirs"})
    assert r.status_code == 201, r.text
    assert r.json()["workspace_id"] == local.ws
    _sync(local)
    assert local.texts(page["id"])["lk1"] == "the original's text"
    c = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(x["kind"], x["mine"], x["theirs"]) for x in c] == [("diverged", "the copy's text", "the original's text")]
    assert "Only there" in {b["content"] for b in local.pages().values()}
    assert mine["id"] in remote.pages()  # a page only the copy had goes over (no force: nothing is pruned)
    # the kept text can still be chosen
    local.client.post(f"/api/mirrors/{local.ws}/conflicts/{c[0]['id']}", json={"choice": "mine"}).raise_for_status()
    _sync(local)
    assert remote.texts(page["id"])["lk1"] == "the copy's text"


def test_force_pull_and_push_make_one_side_identical(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Forced")
    remote.insert(page["id"], "fc1", "original")
    _sync(local)
    # rounds asked for in the background run inline here
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: sync_engine.sync_workspace(ws))
    local.ops(page["id"], [{"op": "set", "id": "fc1", "content": "copy's edit"}])
    extra = local.page("Only in the copy")
    r = local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "pull"})
    assert r.status_code == 200, r.text
    assert local.texts(page["id"])["fc1"] == "original"
    assert extra["id"] not in local.pages() and extra["id"] not in remote.pages()
    c = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    # the replaced text and the removed page are both kept to look at
    assert sorted((x["kind"], x["mine"]) for x in c) == [("diverged", "copy's edit"), ("dropped", "Only in the copy")]
    st = local.client.get(f"/api/mirrors/{local.ws}").json()["status"]
    assert "adopt" not in st and "prune" not in st
    # and the other way
    remote.ops(page["id"], [{"op": "set", "id": "fc1", "content": "original again"}])
    theirs = remote.page("Only on the original")
    local.ops(page["id"], [{"op": "set", "id": "fc1", "content": "the copy wins"}])
    r = local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"})
    assert r.status_code == 200, r.text
    assert remote.texts(page["id"])["fc1"] == "the copy wins"
    assert theirs["id"] not in remote.pages() and theirs["id"] not in local.pages()


def test_force_push_removes_what_the_copy_deleted_and_skips_identical_pages(monkeypatch):
    """A page (and a block) deleted in the copy after a sync goes from the
    original on a force push; pages that are identical cost one read each
    and no write."""
    remote, local, _ = _pair()
    gone = remote.page("Deleted in the copy")
    kept = remote.page("Untouched")
    remote.insert(kept["id"], "k1", "same on both sides")
    edited = remote.page("Block removed in the copy")
    remote.insert(edited["id"], "e1", "stays")
    remote.insert(edited["id"], "e2", "removed in the copy")
    _sync(local)
    assert gone["id"] in local.pages() and local.texts(edited["id"])["e2"] == "removed in the copy"
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: sync_engine.sync_workspace(ws))
    assert local.client.delete(f"/api/blocks/{gone['id']}").status_code == 200
    local.ops(edited["id"], [{"op": "delete", "id": "e2"}])
    writes = []
    real = sync_engine.default_fetch

    def counting(method, path, body, headers):
        if method in ("POST", "PUT", "DELETE"):
            writes.append((method, path))
        return real(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", counting)
    r = local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"})
    assert r.status_code == 200, r.text
    assert gone["id"] not in remote.pages(), "the page deleted in the copy is deleted on the original"
    assert remote.texts(edited["id"]) == {"e1": "stays"}, "the block deleted in the copy is deleted there"
    assert not [w for w in writes if kept["id"] in w[1]], "an identical page is not pushed"
    assert [w for w in writes if w[0] == "DELETE"] == [("DELETE", f"/api/blocks/{gone['id']}")]


def test_cadence_and_sync_on_change():
    remote, local, _ = _pair()
    info = local.client.get(f"/api/mirrors/{local.ws}").json()
    assert (info["poll_s"], info["on_change"]) == (30, True)
    r = local.client.patch(f"/api/mirrors/{local.ws}", json={"poll_s": 5, "on_change": False})
    assert (r.json()["poll_s"], r.json()["on_change"]) == (5, False)
    assert local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "off"}).status_code == 422
    # the loop's clock: a mirror is due by its own cadence, a requested round after its quiet time
    now = 1000.0
    sync_engine._last_run[local.ws] = now - 4
    assert sync_engine.due_now(local.ws, "two-way", 5, now) is False
    assert sync_engine.due_now(local.ws, "two-way", 5, now + 1) is True
    assert sync_engine.due_now(local.ws, "two-way", 0, now + 100) is False
    assert sync_engine.due_now(local.ws, "off", 5, now + 100) is False
    # a local edit asks for a round only when the copy wants it and the write is not the engine's own
    sync_engine._pending.clear()
    sync_engine._refresh_wants()
    assert sync_engine._wants_change[local.ws] is False
    page = remote.page("Edited here")
    _sync(local)
    local.ops(page["id"], [{"op": "insert", "id": "oc1", "parent": page["id"], "position": "a0", "content": "x"}])
    assert local.ws not in sync_engine._pending
    local.client.patch(f"/api/mirrors/{local.ws}", json={"on_change": True})
    sync_engine._refresh_wants()
    local.ops(page["id"], [{"op": "set", "id": "oc1", "content": "y"}])
    assert local.ws in sync_engine._pending
    sync_engine._pending.clear()
    _sync(local)  # the engine's own writes on the remote side land under client "sync"
    assert remote.ws not in sync_engine._pending
    sync_engine._last_run.pop(local.ws, None)


def test_file_transfers_report_progress():
    seen = []
    remote = sync_engine.Remote("http://testserver", "", "gamma_x", lambda m, p, b, h: (200, b"abc"))
    assert remote.get_bytes("/api/uploads/x.pdf", progress=lambda d, t: seen.append((d, t))) == b"abc"
    assert seen == [(3, 3)]


def test_stop_mirroring_keeps_the_workspace():
    remote, local, _ = _pair()
    page = remote.page("Stays")
    _sync(local)
    assert local.client.delete(f"/api/mirrors/{local.ws}").status_code == 200
    assert local.client.get(f"/api/mirrors/{local.ws}").status_code == 404
    assert page["id"] in local.pages()
    assert local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").status_code == 404


def test_pending_local_edits_are_reported_until_pushed():
    remote, local, _ = _pair()
    page = remote.page("Pending")
    remote.insert(page["id"], "pd1", "text")
    _sync(local)
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is False
    local.ops(page["id"], [{"op": "set", "id": "pd1", "content": "text (local)"}])
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is True
    _sync(local)
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is False
    assert remote.texts(page["id"])["pd1"] == "text (local)"


def test_switching_back_to_two_way_pushes_what_receive_only_kept():
    remote, local, _ = _pair()
    page = remote.page("Kept here")
    remote.insert(page["id"], "kh1", "text")
    _sync(local)
    local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "pull"}).raise_for_status()
    local.ops(page["id"], [{"op": "set", "id": "kh1", "content": "text (local)"}])
    _sync(local)  # receive only: the edit stays here, the cursor moves past it
    assert remote.texts(page["id"])["kh1"] == "text"
    local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "two-way"}).raise_for_status()
    _sync(local)  # two-way again: the page is looked at once more and the edit goes out
    assert remote.texts(page["id"])["kh1"] == "text (local)"
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is False


def _mid_round(monkeypatch, action):
    """Run ``action`` once, from inside the next round (just before its
    first page is reconciled), as a person would from the UI meanwhile."""
    real = sync_engine._sync_page
    done = [False]

    def hooked(*args, **kwargs):
        if not done[0]:
            done[0] = True
            action()
        return real(*args, **kwargs)

    monkeypatch.setattr(sync_engine, "_sync_page", hooked)


def test_a_detach_during_a_round_is_kept_with_its_direction(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Busy detach")
    remote.insert(page["id"], "bd1", "one")
    _sync(local)
    local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "pull"}).raise_for_status()
    remote.insert(page["id"], "bd2", "two")
    other = remote.page("Second page")
    _mid_round(monkeypatch, lambda: local.client.post(f"/api/mirrors/{local.ws}/detach").raise_for_status())
    st = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").json()["status"]
    info = local.client.get(f"/api/mirrors/{local.ws}").json()
    assert info["mode"] == "off" and info["status"]["detached_mode"] == "pull" and not st.get("running")
    assert set(st.get("retry") or {}) & {page["id"], other["id"]}, "the pages left over wait for the reattach"
    # reattached: the direction it had, and the rest of that round's work
    assert local.client.post(f"/api/mirrors/{local.ws}/relink", json={}).json()["mode"] == "pull"
    _sync(local)
    assert local.texts(page["id"]) == {"bd1": "one", "bd2": "two"} and other["id"] in local.pages()


def test_a_force_asked_for_during_a_round_runs_as_asked(monkeypatch):
    """A force push pressed while a round runs is not turned into a pull by
    that round's bookkeeping: it waits for the next round and replaces the
    original with the copy."""
    remote, local, _ = _pair()
    page = remote.page("Busy force")
    remote.insert(page["id"], "bf1", "original")
    _sync(local)
    local.ops(page["id"], [{"op": "set", "id": "bf1", "content": "the copy's text"}])
    remote.insert(page["id"], "bf2", "added there")
    _mid_round(monkeypatch, lambda: local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"}).raise_for_status())
    st = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").json()["status"]
    assert st.get("force") == "push", "the force waits for a round of its own"
    _sync(local)
    assert remote.texts(page["id"])["bf1"] == "the copy's text" == local.texts(page["id"])["bf1"]
    st = local.client.get(f"/api/mirrors/{local.ws}").json()["status"]
    assert "force" not in st and "adopt" not in st and "prune" not in st


def test_edits_made_while_the_remote_was_read_only_are_pushed_later(monkeypatch):
    """The remote demotes the account for a while (a viewer): rounds pull
    only, and the edits made here meanwhile go out once writing is allowed
    again — the pill says they are pending until then."""
    remote, local, _ = _pair()
    page = remote.page("Demoted")
    remote.insert(page["id"], "dm1", "text")
    quiet = remote.page("Untouched afterwards")
    remote.insert(quiet["id"], "dm2", "text")
    _sync(local)
    real = sync_engine.whoami
    monkeypatch.setattr(sync_engine, "whoami", lambda r: {**real(r), "role": "viewer"})
    sync_engine._whoami_seen.clear()  # a round trusts the last answer for a while; the demotion is seen now
    local.ops(quiet["id"], [{"op": "set", "id": "dm2", "content": "text (edited while a viewer)"}])
    st = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").json()["status"]
    assert "read-only" in st["last_error"] and st["mode"] == "pull"
    assert remote.texts(quiet["id"])["dm2"] == "text"
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is True
    monkeypatch.setattr(sync_engine, "whoami", real)
    st = _sync(local)
    assert st["mode"] == "two-way" and remote.texts(quiet["id"])["dm2"] == "text (edited while a viewer)"
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["pending_local"] is False


def test_force_pull_on_a_receive_only_clone_removes_its_own_pages(monkeypatch):
    remote, local, _ = _pair(mode="pull")
    page = remote.page("Origin's")
    _sync(local)
    extra = local.page("Only in the clone")
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: sync_engine.sync_workspace(ws))
    assert local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "pull"}).status_code == 200
    assert set(local.pages()) == {page["id"]} and extra["id"] not in remote.pages()


def test_the_direction_of_a_detached_clone_cannot_be_changed():
    remote, local, _ = _pair()
    local.client.post(f"/api/mirrors/{local.ws}/detach").raise_for_status()
    r = local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "pull"})
    assert r.status_code == 400 and "detached" in r.json()["detail"]
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["mode"] == "off"
    assert local.client.patch(f"/api/mirrors/{local.ws}", json={"poll_s": 5}).status_code == 200


def test_resolving_a_conflict_writes_into_the_page_the_block_is_in_now():
    remote, local, _ = _pair()
    page = remote.page("Conflict page")
    other = remote.page("Other page")
    remote.insert(page["id"], "rc1", "alpha beta")
    _sync(local)
    local.ops(page["id"], [{"op": "set", "id": "rc1", "content": "ALPHA beta"}])
    remote.ops(page["id"], [{"op": "set", "id": "rc1", "content": "alpha BETA"}])
    _sync(local)
    c = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [x["kind"] for x in c] == ["merged"]
    # the block moves to another page here before the conflict is looked at
    assert local.client.post("/api/blocks/rc1/reorder", json={"parent_id": other["id"]}).status_code == 200
    r = local.client.post(f"/api/mirrors/{local.ws}/conflicts/{c[0]['id']}", json={"choice": "mine"})
    assert r.status_code == 200, r.text
    assert local.texts(other["id"])["rc1"] == "ALPHA beta"
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["conflicts_open"] == 0


def test_a_page_pushed_whole_with_a_refused_drawing_is_looked_at_again(monkeypatch):
    """A page new here that goes over whole (``_push_whole``) with a drawing
    the remote refuses lands without it, like an edited page does, and is
    deferred the same way: the drawing goes the round after the remote
    takes it, with no other edit here."""
    from gamma import ink as inkmod
    from gamma.routers import ink as ink_router

    remote, local, _ = _pair()
    page = local.page("New here, drawn on")
    local.insert(page["id"], "txt", "words")
    ul = _ink_file(local, [("a", 100)])
    local.ops(page["id"], [{"op": "insert", "id": "inkW", "parent": page["id"], "content": "",
                            "props": {"ink_url": ul, "ink_strokes": 1}}])

    def refuse(data):
        raise inkmod.InkVersionError("pretend the remote is older")
    monkeypatch.setattr(ink_router, "parse_ink", refuse)
    status = _sync(local)
    assert status["pages_pushed"] == 1
    assert remote.texts(page["id"])["txt"] == "words", "the page went over whole, but for the drawing"
    there = next(c for c in remote.tree(page["id"])["children"] if c["id"] == "inkW")
    assert "ink_url" not in there["properties"]
    conflicts = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"]) for c in conflicts] == [("file", "inkW")]
    monkeypatch.setattr(ink_router, "parse_ink", inkmod.parse_ink)
    status = _sync(local)
    assert status["files_pushed"] == 1 and _strokes(remote, "inkW") == (ul, ["a"])


def _ink_file(side, strokes, version=2):
    """Upload a drawing of ``[(stroke id, x)]`` on ``side``: its url."""
    import json as _json
    from gamma import ink as inkmod
    data = {"format": "gamma-ink", "version": version, "space": {"kind": "pdf-page", "page": 1, "width": 612, "height": 792},
            "strokes": [{"id": sid, "tool": "pen", "color": "#1f1f1f", "size": 2, "opacity": 1, "pen": True, "ch": "xy",
                         "pts": inkmod.encode_points([{"x": x, "y": 100.0}, {"x": x + 5, "y": 105.0}], "xy")}
                        for sid, x in strokes]}
    body = _json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
    r = side.client.post("/api/upload-ink", content=body, headers={"Content-Type": "application/json"})
    assert r.status_code == 200, r.text
    return r.json()["url"]


def _strokes(side, bid):
    from gamma import ink as inkmod
    tree = side.tree(side.client.get(f"/api/blocks/{bid}").json()["parent_id"])
    url = next(c for c in tree["children"] if c["id"] == bid)["properties"]["ink_url"]
    return url, [s.id for s in inkmod.parse_ink(side.client.get(url).content).strokes]


def test_both_sides_drawing_in_one_group_keep_every_stroke():
    """A drawing both sides changed is merged by stroke (sync_tree's
    base_props, ops.py merge_ink), never replaced by one side's."""
    remote, local, _ = _pair()
    page = remote.page("Ink on both sides")
    u0 = _ink_file(remote, [("a", 100)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkG", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 1}}])
    _sync(local)
    assert _strokes(local, "inkG") == (u0, ["a"])
    ur = _ink_file(remote, [("a", 100), ("x", 300)])
    remote.ops(page["id"], [{"op": "set", "id": "inkG", "props": {"ink_url": ur, "ink_strokes": 2},
                             "base_props": {"ink_url": u0}}])
    ul = _ink_file(local, [("a", 100), ("b", 400)])
    local.ops(page["id"], [{"op": "set", "id": "inkG", "props": {"ink_url": ul, "ink_strokes": 2},
                            "base_props": {"ink_url": u0}}])
    _sync(local)
    there, here = _strokes(remote, "inkG"), _strokes(local, "inkG")
    assert there == here and sorted(here[1]) == ["a", "b", "x"]
    assert remote.tree(page["id"])["children"][0]["properties"]["ink_strokes"] == 3
    # settled: the next round moves nothing
    status = _sync(local)
    assert status["pages_pulled"] == 0 and status["pages_pushed"] == 0


def test_a_drawing_the_remote_refuses_waits_in_a_conflict_row(monkeypatch):
    """A 400 on an .ink push (a remote whose release does not take this
    copy's files) is one ``file`` conflict row on the block, and the page's
    other changes land; the next round does not write the row twice."""
    from gamma import ink as inkmod
    from gamma.db import connect_pages_db
    from gamma.routers import ink as ink_router

    remote, local, _ = _pair()
    page = remote.page("Ink refused")
    remote.insert(page["id"], "txt", "before")
    _sync(local)
    ul = _ink_file(local, [("a", 100)])
    name = ul.rsplit("/", 1)[1]
    local.ops(page["id"], [{"op": "set", "id": "txt", "content": "after"},
                           {"op": "insert", "id": "inkR", "parent": page["id"], "content": "",
                            "props": {"ink_url": ul, "ink_strokes": 1}}])

    def refuse(data):
        raise inkmod.InkVersionError("pretend the remote is older")
    monkeypatch.setattr(ink_router, "parse_ink", refuse)
    status = _sync(local)
    assert status["pages_pushed"] == 1
    assert remote.texts(page["id"])["txt"] == "after", "the page's other ops landed"
    there = next(c for c in remote.tree(page["id"])["children"] if c["id"] == "inkR")
    assert "ink_url" not in there["properties"], "the block went without the drawing it names"
    conflicts = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"], c["mine"], c["theirs"]) for c in conflicts] == \
        [("file", "inkR", name, "update Gamma")]
    _sync(local)  # still refused: the same open row, not another
    assert len(local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]) == 1
    with connect_pages_db(local.ws) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE kind = 'file'").fetchone()[0] == 1
    # the remote updated: the drawing goes with the next round
    monkeypatch.setattr(ink_router, "parse_ink", inkmod.parse_ink)
    assert _strokes(local, "inkR") == (ul, ["a"]), "the drawing never left this copy"
    status = _sync(local)
    assert status["files_pushed"] == 1 and _strokes(remote, "inkR") == (ul, ["a"])


def test_a_page_adopted_with_a_refused_drawing_is_looked_at_again(monkeypatch):
    """A page both sides hold, linked with this copy's version
    (``_adopt_page``, ``adopt: mine``), whose drawing the remote refuses is
    deferred like an edited page or one pushed whole: the drawing goes the
    round after the remote takes it, with no other edit on either side."""
    from gamma import ink as inkmod
    from gamma.routers import ink as ink_router

    remote = Side("mr_adopt_ink_remote")
    local = Side("mr_adopt_ink_local")
    page = remote.page("Drawn on in the copy")
    remote.insert(page["id"], "inkA", "")
    local.client.post("/api/pages", json={"id": page["id"], "title": "Drawn on in the copy"}).raise_for_status()
    local.insert(page["id"], "inkA", "")
    ul = _ink_file(local, [("a", 100)])
    local.ops(page["id"], [{"op": "set", "id": "inkA", "props": {"ink_url": ul, "ink_strokes": 1}}])
    token = create_token(remote.id, remote.ws, "link", 90, scope="write")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws, "adopt": "mine"})
    assert r.status_code == 201, r.text

    def refuse(data):
        raise inkmod.InkVersionError("pretend the remote is older")
    monkeypatch.setattr(ink_router, "parse_ink", refuse)
    _sync(local)
    assert "ink_url" not in remote.tree(page["id"])["children"][0]["properties"]
    conflicts = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"]) for c in conflicts] == [("file", "inkA")]
    assert page["id"] in sync_engine.get_mirror(local.ws)["status"]["retry"]
    # the remote updated: the drawing goes with the next round
    monkeypatch.setattr(ink_router, "parse_ink", inkmod.parse_ink)
    status = _sync(local)
    assert status["files_pushed"] == 1 and _strokes(remote, "inkA") == (ul, ["a"])


def test_a_refused_drawing_is_charged_to_the_block_that_draws_it():
    """The ``file`` row of a refused drawing names the block whose
    ``ink_url`` is the file, the block ``_without_files`` holds back, never
    one that only mentions the file in another property."""
    side = Side("mr_ink_lookup")
    page = side.page("Two blocks name one drawing")
    url = _ink_file(side, [("a", 100)])
    name = url.rsplit("/", 1)[1]
    blocks = {page["id"]: {"parent": "root", "position": "a0", "content": "Two blocks name one drawing", "props": {}},
              "mentions": {"parent": page["id"], "position": "a0", "content": "", "props": {"note": f"from {url}"}},
              "draws": {"parent": page["id"], "position": "a1", "content": "",
                        "props": {"ink_url": url, "ink_strokes": 1}}}

    class Older:
        """A remote whose release refuses this copy's drawings."""

        def head_ok(self, path):
            return False

        def post_file(self, path, name, data, progress=None):
            raise sync_engine.RemoteError(400, "update Gamma")

    refused = sync_engine._push_files(side.ws, Older(), {name}, {"files_pushed": 0}, page_id=page["id"], blocks=blocks)
    assert refused == {name}
    with connect_pages_db(side.ws) as conn:
        assert [r[0] for r in conn.execute("SELECT block_id FROM sync_conflicts WHERE kind = 'file'")] == ["draws"]
    ops = [{"op": "set", "id": bid, "props": dict(blocks[bid]["props"])} for bid in ("mentions", "draws")]
    assert sync_engine._without_files(ops, refused, blocks) == ops[:1]


def _drawn_meanwhile(monkeypatch, remote, page_id, ops=(), *, older=False):
    """The ops the engine pushes to ``page_id``, recorded as sent. Before
    the first push reaches the remote, ``ops`` are written there: someone
    drawing while the round runs. ``older``: the remote answers like a
    release from before ink merge reports, which ignores ``base_ids`` and
    names no ``ink_conflicts``."""
    sent, waiting, fetch = [], list(ops), sync_engine.default_fetch
    path_ = f"/api/pages/{page_id}/ops"

    def racing(method, path, body, headers):
        if method == "POST" and path == path_:
            payload = json.loads(body)
            sent.extend(payload["ops"])
            if waiting:
                remote.ops(page_id, waiting)
                waiting.clear()
            if older:
                payload["ops"] = [{k: v for k, v in op.items() if k != "base_ids"} for op in payload["ops"]]
                body = json.dumps(payload).encode()
        status, out = fetch(method, path, body, headers)
        if older and method == "POST" and path == path_ and status == 200:
            out = json.dumps({k: v for k, v in json.loads(out).items() if k != "ink_conflicts"}).encode()
        return status, out

    monkeypatch.setattr(sync_engine, "default_fetch", racing)
    return sent


def _name(url):
    return url.rsplit("/", 1)[1]


def test_a_drawing_push_names_its_base_strokes_when_this_copy_holds_the_base(monkeypatch):
    """A drawing pushed with the one it was edited from (``base_props``)
    also names that file's stroke ids (``base_ids``, as the web client's
    save does), read from this copy's uploads, so a remote that no longer
    holds the base merges by deletions; with the file gone here they are
    left off."""
    from gamma import storage

    remote, local, _ = _pair()
    page = remote.page("Base strokes")
    u0 = _ink_file(remote, [("a", 100), ("c", 500)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkB", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 2}}])
    _sync(local)
    sent = _drawn_meanwhile(monkeypatch, remote, page["id"])
    u1 = _ink_file(local, [("a", 100), ("c", 500), ("b", 300)])
    local.ops(page["id"], [{"op": "set", "id": "inkB", "props": {"ink_url": u1, "ink_strokes": 3},
                            "base_props": {"ink_url": u0}}])
    _sync(local)
    u2 = _ink_file(local, [("a", 100), ("c", 500), ("b", 300), ("d", 600)])
    local.ops(page["id"], [{"op": "set", "id": "inkB", "props": {"ink_url": u2, "ink_strokes": 4},
                            "base_props": {"ink_url": u1}}])
    storage.delete(local.ws, _name(u1))  # the base purged here meanwhile
    _sync(local)
    assert [(op["props"]["ink_url"], op.get("base_ids")) for op in sent if op["id"] == "inkB"] == \
        [(u1, ["a", "c"]), (u2, None)]
    assert _strokes(remote, "inkB") == (u2, ["a", "c", "b", "d"])


def test_a_base_the_remote_no_longer_holds_is_pushed_so_it_merges_three_way(monkeypatch):
    """This copy adds a stroke and leaves another alone while the remote
    erases that one and has purged the file both started from: the push
    uploads the base first, so the remote merges three-way — the erase
    stands, the new stroke joins it, and nothing is reported (by stroke ids
    alone the kept stroke would read as a change made here that was lost)."""
    from gamma import storage

    remote, local, _ = _pair()
    page = remote.page("Base purged there")
    u0 = _ink_file(remote, [("a", 100), ("c", 500)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkP", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 2}}])
    _sync(local)
    ul = _ink_file(local, [("a", 100), ("c", 500), ("b", 300)])
    local.ops(page["id"], [{"op": "set", "id": "inkP", "props": {"ink_url": ul}, "base_props": {"ink_url": u0}}])
    ur = _ink_file(remote, [("a", 100)])
    _drawn_meanwhile(monkeypatch, remote, page["id"],
                     [{"op": "set", "id": "inkP", "props": {"ink_url": ur}, "base_props": {"ink_url": u0}}])
    storage.delete(remote.ws, _name(u0))
    _sync(local)
    assert _strokes(local, "inkP") == _strokes(remote, "inkP")
    assert sorted(_strokes(remote, "inkP")[1]) == ["a", "b"]
    assert local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"] == []
    assert storage.exists(remote.ws, _name(u0)), "the base went there first"


def test_a_drawing_the_remote_merged_over_this_copys_change_is_one_conflict_row(monkeypatch):
    """Someone moved a stroke there while this copy's move of it was on its
    way: the remote merges the two drawings, keeps its own move and says
    so (``ink_conflicts``). That is one ``ink_merged`` row on the group —
    ``mine`` the drawing sent, ``theirs`` the remote's report, ``result``
    the merged drawing both sides hold, ``base`` the one both started from
    — and only one, however many rounds follow. Resolving it writes
    nothing into the group."""
    remote, local, _ = _pair()
    page = remote.page("Drawn on both sides at once")
    u0 = _ink_file(remote, [("a", 100), ("c", 500)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkM", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 2}}])
    _sync(local)
    ul = _ink_file(local, [("a", 150), ("c", 500)])
    ur = _ink_file(remote, [("a", 200), ("c", 500)])
    local.ops(page["id"], [{"op": "set", "id": "inkM", "props": {"ink_url": ul}, "base_props": {"ink_url": u0}}])
    _drawn_meanwhile(monkeypatch, remote, page["id"],
                     [{"op": "set", "id": "inkM", "props": {"ink_url": ur}, "base_props": {"ink_url": u0}}])
    _sync(local)
    assert _strokes(local, "inkM") == _strokes(remote, "inkM") == (ur, ["a", "c"])
    rows = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"], c["mine"], c["result"], c["base"]) for c in rows] == \
        [("ink_merged", "inkM", _name(ul), _name(ur), _name(u0))]
    assert json.loads(rows[0]["theirs"]) == {"clean": False, "lost_ours": ["a"], "returned": []}
    _sync(local)
    assert len(local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]) == 1
    r = local.client.post(f"/api/mirrors/{local.ws}/conflicts/{rows[0]['id']}", json={"choice": "mine"})
    assert r.status_code == 200 and _strokes(local, "inkM") == (ur, ["a", "c"])
    assert local.client.get(f"/api/mirrors/{local.ws}").json()["conflicts_open"] == 0


def test_a_drawing_sent_again_after_a_lost_push_is_merged_there_and_reported(monkeypatch):
    """A drawing whose push never reached the remote (the link dropped)
    goes again next round (``_confirm_push``) with its base's stroke ids;
    someone moved the same stroke there meanwhile, so the remote merges the
    resend, and its report is the one ``ink_merged`` row."""
    remote, local, _ = _pair()
    page = remote.page("Lost on the way")
    u0 = _ink_file(remote, [("a", 100), ("c", 500)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkL", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 2}}])
    _sync(local)
    ul = _ink_file(local, [("a", 150), ("c", 500)])
    local.ops(page["id"], [{"op": "set", "id": "inkL", "props": {"ink_url": ul}, "base_props": {"ink_url": u0}}])
    posts, fetch = [], sync_engine.default_fetch

    def dropped(method, path, body, headers):
        if method == "POST" and path == f"/api/pages/{page['id']}/ops":
            posts.append(json.loads(body)["ops"])
            if len(posts) == 1:
                raise sync_engine.RemoteError(0, "cannot reach the remote")
        return fetch(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", dropped)
    status = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1").json()["status"]
    assert status["last_error"] and page["id"] in status["retry"]
    ur = _ink_file(remote, [("a", 200), ("c", 500)])
    remote.ops(page["id"], [{"op": "set", "id": "inkL", "props": {"ink_url": ur}, "base_props": {"ink_url": u0}}])
    _sync(local)
    assert [op.get("base_ids") for batch in posts for op in batch if op["id"] == "inkL"] == [["a", "c"]] * 2
    assert _strokes(local, "inkL") == _strokes(remote, "inkL") == (ur, ["a", "c"])
    rows = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert [(c["kind"], c["block_id"], c["mine"], json.loads(c["theirs"])["lost_ours"]) for c in rows] == \
        [("ink_merged", "inkL", _name(ul), ["a"])]


@pytest.mark.parametrize("case", ["clean", "older"])
def test_a_clean_ink_merge_or_an_older_remote_records_nothing(monkeypatch, case):
    """A merge that kept every change of both sides (a stroke added on
    each), or a remote of a release that reports no merge (an answer
    without ``ink_conflicts``), leaves no row; both sides end on one
    drawing."""
    remote, local, _ = _pair()
    page = remote.page("Drawn on both sides")
    u0 = _ink_file(remote, [("a", 100)])
    remote.ops(page["id"], [{"op": "insert", "id": "inkC", "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 1}}])
    _sync(local)
    if case == "clean":
        ul, ur = _ink_file(local, [("a", 100), ("b", 300)]), _ink_file(remote, [("a", 100), ("x", 600)])
    else:  # the same stroke moved on both sides: not clean, but this remote cannot say so
        ul, ur = _ink_file(local, [("a", 150)]), _ink_file(remote, [("a", 200)])
    local.ops(page["id"], [{"op": "set", "id": "inkC", "props": {"ink_url": ul}, "base_props": {"ink_url": u0}}])
    _drawn_meanwhile(monkeypatch, remote, page["id"],
                     [{"op": "set", "id": "inkC", "props": {"ink_url": ur}, "base_props": {"ink_url": u0}}],
                     older=case == "older")
    _sync(local)
    there = _strokes(remote, "inkC")
    assert _strokes(local, "inkC") == there
    assert sorted(there[1]) == (["a", "b", "x"] if case == "clean" else ["a"])
    assert local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"] == []


def test_drawings_go_in_one_batch_and_each_merge_is_reported_on_its_group(monkeypatch):
    """Drawings ride in the batches every op rides in (MAX_OPS each), and
    the remote reports each ink merge by its block (``ink_conflicts``):
    two groups whose stroke was moved on both sides at once go in one POST
    and are two ``ink_merged`` rows, each on its own group with its own
    report; a group merged cleanly in the same batch is none."""
    remote, local, _ = _pair()
    page = remote.page("Three groups drawn on both sides")
    u0 = _ink_file(remote, [("a", 100), ("c", 500)])
    groups = ("inkG1", "inkG2", "inkG3")
    remote.ops(page["id"], [{"op": "insert", "id": g, "parent": page["id"], "content": "",
                             "props": {"ink_url": u0, "ink_strokes": 2}} for g in groups])
    _sync(local)
    here = {"inkG1": [("a", 150), ("c", 500)], "inkG2": [("a", 100), ("c", 550)], "inkG3": [("a", 100), ("c", 500), ("b", 300)]}
    there = {"inkG1": [("a", 200), ("c", 500)], "inkG2": [("a", 100), ("c", 600)], "inkG3": [("a", 100), ("c", 500), ("x", 700)]}
    mine = {g: _ink_file(local, s) for g, s in here.items()}
    local.ops(page["id"], [{"op": "set", "id": g, "props": {"ink_url": u}, "base_props": {"ink_url": u0}} for g, u in mine.items()])
    _drawn_meanwhile(monkeypatch, remote, page["id"],
                     [{"op": "set", "id": g, "props": {"ink_url": _ink_file(remote, s)}, "base_props": {"ink_url": u0}}
                      for g, s in there.items()])
    posts, racing = [], sync_engine.default_fetch

    def counted(method, path, body, headers):
        if method == "POST" and path == f"/api/pages/{page['id']}/ops":
            posts.append(sorted(op["id"] for op in json.loads(body)["ops"]))
        return racing(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", counted)
    _sync(local)
    assert posts == [list(groups)], "one POST for the three drawings"
    rows = local.client.get(f"/api/mirrors/{local.ws}/conflicts").json()["conflicts"]
    assert sorted((c["kind"], c["block_id"], c["mine"], json.loads(c["theirs"])["lost_ours"]) for c in rows) == \
        [("ink_merged", "inkG1", _name(mine["inkG1"]), ["a"]), ("ink_merged", "inkG2", _name(mine["inkG2"]), ["c"])]
    assert sorted(_strokes(remote, "inkG3")[1]) == ["a", "b", "c", "x"], "the clean merge keeps both strokes"
    assert all(_strokes(local, g) == _strokes(remote, g) for g in groups)


def test_a_drawing_waiting_on_the_remote_does_not_hold_a_force_back(monkeypatch):
    """A force push to a remote that refuses this copy's drawings: the page
    holding one lands without it and waits on the retry list, but it went
    through the force (its other changes landed, its base is saved), so
    the force's policy is spent — a page made on the remote afterwards is
    pulled, not deleted there as one only the remote had. A page that
    failed outright went through nothing: the policy waits for it."""
    from gamma import ink as inkmod
    from gamma.routers import ink as ink_router

    remote, local, _ = _pair()
    page = remote.page("Drawn on here")
    remote.insert(page["id"], "txt", "words")
    _sync(local)
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: sync_engine.sync_workspace(ws))
    ul = _ink_file(local, [("a", 100)])
    local.ops(page["id"], [{"op": "insert", "id": "inkF", "parent": page["id"], "content": "",
                            "props": {"ink_url": ul, "ink_strokes": 1}}])

    def refuse(data):
        raise inkmod.InkVersionError("pretend the remote is older")
    monkeypatch.setattr(ink_router, "parse_ink", refuse)
    local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"}).raise_for_status()
    status = sync_engine.get_mirror(local.ws)["status"]
    assert list(status["retry"]) == [page["id"]] and "prune" not in status and "adopt" not in status
    later = remote.page("Made there after the force")
    _sync(local)
    assert later["id"] in remote.pages() and later["id"] in local.pages()
    monkeypatch.setattr(ink_router, "parse_ink", inkmod.parse_ink)
    status = _sync(local)
    assert _strokes(remote, "inkF") == (ul, ["a"]) and status["retry"] == {}
    real = sync_engine._sync_page

    def boom(ws, remote_, page_id, **kw):
        if page_id == page["id"]:
            raise KeyError("a bug in the engine")
        return real(ws, remote_, page_id, **kw)

    monkeypatch.setattr(sync_engine, "_sync_page", boom)
    local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"}).raise_for_status()
    status = sync_engine.get_mirror(local.ws)["status"]
    assert status["prune"] is True and status["adopt"] == "mine"
    monkeypatch.setattr(sync_engine, "_sync_page", real)
    status = _sync(local)
    assert "prune" not in status and "adopt" not in status


def test_a_redirect_off_the_remote_drops_its_credentials():
    """A file's GET redirected to another origin goes there without the
    write token, the workspace header or a cookie; one on the remote's own
    origin keeps them."""
    import urllib.request

    handler = sync_engine._OffOriginRedirect()
    req = urllib.request.Request("https://gamma.example/api/uploads/a.pdf", headers={
        "Authorization": "Bearer secret", "X-Gamma-Workspace": "w1", "Accept": "application/json"})
    there = handler.redirect_request(req, None, 302, "Found", {}, "https://files.example/a.pdf")
    assert not there.has_header("Authorization") and not there.has_header("X-gamma-workspace")
    assert there.has_header("Accept")
    here = handler.redirect_request(req, None, 302, "Found", {}, "https://gamma.example/api/uploads/b.pdf")
    assert here.get_header("Authorization") == "Bearer secret"

"""A round cut short loses and doubles nothing (docs/dev/mirror.md "Rounds
cut short"). A page's first push or pull stopped half way goes on from the
bare page's base; a remote change applied here is never applied again after
a refused push, a dropped link or a lost answer, with typing in between; a
page both sides hold without a base keeps both; a link or a force keeps what
it removes; a restored backup linked to its original keeps the pages being
recovered. Same in-process pair as test_mirror.py."""
import pytest

from gamma import sync_engine
from gamma.db import connect_pages_db, ws_uploads_dir
from gamma.integrations import create_token
from test_mirror import Side, _pair, _sync, _transport  # noqa: F401 — the transport fixture applies here too
from test_mirror_edges import conflicts, same_tree

PDF = b"%PDF-1.4 cut short\n" + b"c" * 3000
PNG = b"\x89PNG\r\n\x1a\n" + b"q" * 200


def _round(local):
    """One round that may fail; its status."""
    r = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    assert r.status_code == 200, r.text
    return r.json()["status"]


def _refuse(monkeypatch, refuse):
    """The remote answers ``refuse(method, path)`` — a (status, body) pair —
    where that is not None, and normally elsewhere; returns the undo."""
    real = sync_engine.default_fetch

    def fetch(method, path, body, headers):
        out = refuse(method, path)
        return out if out else real(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    return lambda: monkeypatch.setattr(sync_engine, "default_fetch", real)


def _drop_link(method, path):
    raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")


# --- M1: a first push or pull cut short ---------------------------------------------------

def test_a_first_push_cut_short_by_a_refused_file_keeps_the_page(monkeypatch):
    remote, local, _ = _pair()
    _sync(local)
    up = local.client.post("/api/uploads", files={"file": ("p.pdf", PDF, "application/pdf")}).json()
    page = local.page("Offline notes", doc_id=up["doc_id"], source_url=up["source_url"])
    notes = {f"n{i}": f"important note {i}" for i in range(5)}
    for i, (bid, text) in enumerate(notes.items()):
        local.insert(page["id"], bid, text, position=f"a{i}")
    undo = _refuse(monkeypatch, lambda m, p: (507, b'{"detail": "storage full"}') if p == "/api/uploads" else None)
    st = _round(local)
    assert "storage full" in st["last_error"] and page["id"] in st["retry"]
    assert local.texts(page["id"]) == notes
    undo()
    _sync(local)
    assert local.texts(page["id"]) == notes == remote.texts(page["id"])
    assert (ws_uploads_dir(remote.ws) / f"{up['doc_id']}.pdf").read_bytes() == PDF  # the page's own PDF too
    assert conflicts(local) == []
    same_tree(remote, local, page["id"])


def test_a_first_push_cut_short_by_a_dropped_link_keeps_the_page(monkeypatch):
    remote, local, _ = _pair()
    _sync(local)
    page = local.page("Train notes")
    notes = {f"m{i}": f"typed on the train {i}" for i in range(5)}
    for i, (bid, text) in enumerate(notes.items()):
        local.insert(page["id"], bid, text, position=f"a{i}")
    undo = _refuse(monkeypatch, lambda m, p: _drop_link(m, p) if m == "POST" and p.endswith("/ops") else None)
    assert "connection reset" in _round(local)["last_error"]
    assert page["id"] in remote.pages() and local.texts(page["id"]) == notes
    undo()
    _sync(local)
    assert local.texts(page["id"]) == notes == remote.texts(page["id"])
    assert conflicts(local) == []


def test_a_first_push_cut_short_half_way_is_finished_not_doubled(monkeypatch):
    """Two of five blocks landed before the link dropped; the person typed on
    in one of them. The rest goes over, the typing wins, nothing twice."""
    remote, local, _ = _pair()
    _sync(local)
    page = local.page("Half pushed")
    for i in range(5):
        local.insert(page["id"], f"h{i}", f"block {i}", position=f"a{i}")
    monkeypatch.setattr(sync_engine, "MAX_OPS", 2)
    batches = []

    def second_batch_drops(method, path):
        if method == "POST" and path.endswith("/ops"):
            batches.append(path)
            if len(batches) == 2:
                _drop_link(method, path)
        return None

    undo = _refuse(monkeypatch, second_batch_drops)
    assert _round(local)["last_error"]
    assert remote.texts(page["id"]) == {"h0": "block 0", "h1": "block 1"}
    undo()
    local.ops(page["id"], [{"op": "set", "id": "h1", "content": "block 1, typed on", "base": "block 1"}])
    _sync(local)
    expect = {"h0": "block 0", "h1": "block 1, typed on", "h2": "block 2", "h3": "block 3", "h4": "block 4"}
    assert local.texts(page["id"]) == expect == remote.texts(page["id"])
    assert conflicts(local) == []  # the remote's "block 1" is in the text here: nothing to look at
    same_tree(remote, local, page["id"])


def test_a_first_pull_waits_for_its_files_and_goes_on_from_the_bare_page(monkeypatch):
    remote, local, _ = _pair()
    up = remote.client.post("/api/uploads", files={"file": ("paper.pdf", PDF, "application/pdf")}).json()
    page = remote.client.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": "paper.pdf"}).json()
    for i in range(5):
        remote.insert(page["id"], f"p{i}", f"remote note {i}", position=f"a{i}")
    # the PDF cannot be fetched: the page does not appear here without it
    undo = _refuse(monkeypatch, lambda m, p: (503, b'{"detail": "busy"}') if p.startswith("/api/uploads/") else None)
    assert _round(local)["last_error"]
    assert page["id"] not in local.pages()
    undo()
    # the page is made here, then the round stops after the first two blocks
    monkeypatch.setattr(sync_engine, "MAX_OPS", 2)
    real_commit = sync_engine.commit_ops
    calls = []

    def stop_after_one_batch(*a, **kw):
        calls.append(1)
        if len(calls) == 2:
            raise RuntimeError("the app quit")
        return real_commit(*a, **kw)

    monkeypatch.setattr(sync_engine, "commit_ops", stop_after_one_batch)
    assert "the app quit" in _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "commit_ops", real_commit)
    assert (ws_uploads_dir(local.ws) / f"{up['doc_id']}.pdf").read_bytes() == PDF
    assert local.texts(page["id"]) == {"p0": "remote note 0", "p1": "remote note 1"}
    local.ops(page["id"], [{"op": "set", "id": "p0", "content": "remote note 0 — read", "base": "remote note 0"}])
    _sync(local)
    expect = {f"p{i}": f"remote note {i}" for i in range(5)} | {"p0": "remote note 0 — read"}
    assert local.texts(page["id"]) == expect == remote.texts(page["id"])
    assert conflicts(local) == []


def test_a_page_both_sides_hold_without_a_base_keeps_both():
    """No base and no link or force asking for one side: a union — nothing
    either side has is dropped, a text the remote wrote that this copy's does
    not hold waits in a conflict."""
    remote, local, _ = _pair()
    page = remote.page("No base")
    remote.insert(page["id"], "u1", "one", position="a0")
    remote.insert(page["id"], "u2", "two", position="a1")
    _sync(local)
    remote.ops(page["id"], [{"op": "set", "id": "u1", "content": "one (remote)"},
                            {"op": "insert", "id": "u3", "parent": page["id"], "position": "a2", "content": "three there"}])
    local.ops(page["id"], [{"op": "set", "id": "u1", "content": "one (local)"},
                           {"op": "set", "id": "u2", "content": "two (local)"},
                           {"op": "insert", "id": "u4", "parent": page["id"], "position": "a3", "content": "four here"}])
    with connect_pages_db(local.ws) as conn:
        conn.execute("DELETE FROM sync_pages WHERE page_id = ?", (page["id"],))
        conn.commit()
    _sync(local)
    expect = {"u1": "one (local)", "u2": "two (local)", "u3": "three there", "u4": "four here"}
    assert local.texts(page["id"]) == expect == remote.texts(page["id"])
    c = conflicts(local)
    assert [(x["kind"], x["block_id"], x["mine"], x["theirs"]) for x in c] == [
        ("diverged", "u1", "one (local)", "one (remote)")]
    # the remote's text can still be chosen
    local.client.post(f"/api/mirrors/{local.ws}/conflicts/{c[0]['id']}", json={"choice": "theirs"}).raise_for_status()
    _sync(local)
    assert remote.texts(page["id"])["u1"] == "one (remote)" == local.texts(page["id"])["u1"]


# --- M1: what a link or a force removes is kept -------------------------------------------

def test_a_force_keeps_the_blocks_and_pages_it_removes(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Forced again")
    remote.insert(page["id"], "f1", "shared", position="a0")
    _sync(local)
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: sync_engine.sync_workspace(ws))
    local.insert(page["id"], "f2", "a section only here", position="a1")
    local.insert(page["id"], "f3", "its child", parent="f2")
    mine = local.page("A page only here")
    local.insert(mine["id"], "f4", "its note")
    local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "pull"}).raise_for_status()
    assert local.texts(page["id"]) == {"f1": "shared"} and mine["id"] not in local.pages()
    dropped = {x["block_id"]: x for x in conflicts(local) if x["kind"] == "dropped"}
    assert set(dropped) == {"f2", mine["id"]}
    assert dropped["f2"]["mine"] == "a section only here\n- its child" and dropped["f2"]["base"] == page["id"]
    assert dropped[mine["id"]]["mine"] == "A page only here\n- its note" and dropped[mine["id"]]["theirs"] == ""
    # and the other way: what only the original had is kept too
    remote.insert(page["id"], "f5", "added on the original", position="a2")
    theirs = remote.page("A page only there")
    local.client.post(f"/api/mirrors/{local.ws}/force", json={"direction": "push"}).raise_for_status()
    assert remote.texts(page["id"]) == {"f1": "shared"} and theirs["id"] not in remote.pages()
    dropped = {x["block_id"]: x for x in conflicts(local) if x["kind"] == "dropped" and x["theirs"]}
    assert {k: v["theirs"] for k, v in dropped.items()} == {"f5": "added on the original",
                                                             theirs["id"]: "A page only there"}
    # a dropped row is acknowledged like any decision
    r = local.client.post(f"/api/mirrors/{local.ws}/conflicts/{dropped['f5']['id']}", json={"choice": "keep"})
    assert r.status_code == 200


def test_linking_keeps_the_blocks_only_the_copy_had():
    remote = Side("mi_link_remote")
    local = Side("mi_link_local")
    page = remote.page("Linked")
    remote.insert(page["id"], "lb1", "the original's text")
    local.client.post("/api/pages", json={"id": page["id"], "title": "Linked"}).raise_for_status()
    local.insert(page["id"], "lb1", "the copy's text")
    local.insert(page["id"], "lb2", "only the copy wrote this")
    token = create_token(remote.name, remote.ws, "link", 90, scope="write")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws, "adopt": "theirs"})
    assert r.status_code == 201, r.text
    _sync(local)
    assert local.texts(page["id"]) == {"lb1": "the original's text"}
    assert sorted((x["kind"], x["block_id"], x["mine"]) for x in conflicts(local)) == [
        ("diverged", "lb1", "the copy's text"), ("dropped", "lb2", "only the copy wrote this")]


# --- M2: a remote change is applied once --------------------------------------------------

def test_a_refused_push_does_not_apply_the_remote_change_twice(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Refused")
    remote.insert(page["id"], "d1", "The quick fox")
    _sync(local)
    remote.ops(page["id"], [{"op": "set", "id": "d1", "content": "The quick brown fox"}])
    local.ops(page["id"], [{"op": "set", "id": "d1", "content": "The quick fox jumps"}])
    undo = _refuse(monkeypatch, lambda m, p: (507, b'{"detail": "storage full"}')
                   if m == "POST" and p.endswith("/ops") else None)
    for extra in ("", " over", " the dog"):
        if extra:
            now = local.texts(page["id"])["d1"]
            local.ops(page["id"], [{"op": "set", "id": "d1", "content": now + extra, "base": now}])
        assert _round(local)["last_error"]
    assert local.texts(page["id"])["d1"] == "The quick brown fox jumps over the dog"
    undo()
    _sync(local)
    assert remote.texts(page["id"])["d1"] == "The quick brown fox jumps over the dog" == local.texts(page["id"])["d1"]
    assert [x["kind"] for x in conflicts(local)] == ["merged"]  # the one real merge, recorded once


def test_a_refused_file_does_not_grow_the_text_every_round(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Grow")
    remote.insert(page["id"], "g1", "The quick fox")
    _sync(local)
    remote.ops(page["id"], [{"op": "set", "id": "g1", "content": "The quick brown fox"}])
    img = local.client.post("/api/upload-file", files={"file": ("pic.png", PNG, "image/png")}).json()
    local.ops(page["id"], [{"op": "set", "id": "g1", "content": f"The quick fox ![]({img['url']})"}])
    undo = _refuse(monkeypatch, lambda m, p: (507, b'{"detail": "storage limit reached"}')
                   if m == "POST" and p in ("/api/uploads", "/api/upload-file") else None)
    for _ in range(4):
        assert _round(local)["last_error"]
        assert local.texts(page["id"])["g1"] == f"The quick brown fox ![]({img['url']})"
    assert len(conflicts(local)) == 1
    undo()
    _sync(local)
    assert remote.texts(page["id"])["g1"] == f"The quick brown fox ![]({img['url']})"


@pytest.mark.parametrize("cut", ["the base after the push", "the remote's answer", "the last base"])
def test_a_round_cut_short_after_the_push_then_more_typing(monkeypatch, cut):
    """test_mirror_edges' cut-short case wherever the round stops after its
    push, with the person typing on in the same block before the next one."""
    remote, local, _ = _pair()
    page = remote.page(f"Cut, then typing ({cut})")
    remote.insert(page["id"], "c1", "one", position="a0")
    _sync(local)
    local.insert(page["id"], "c2", "two (local)", position="a1")
    local.ops(page["id"], [{"op": "set", "id": "c1", "content": "one (local)"}])
    real_save, real_tree = sync_engine._save_state, sync_engine._remote_tree
    done = []

    def save(conn, page_id, seq, base):
        if not done and (seq == sync_engine.UNKNOWN_SEQ) == (cut == "the base after the push"):
            done.append(1)
            raise RuntimeError("disk full")
        real_save(conn, page_id, seq, base)

    def tree(remote_, page_id):
        if not done:
            done.append(1)
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")
        return real_tree(remote_, page_id)

    if cut == "the remote's answer":
        monkeypatch.setattr(sync_engine, "_remote_tree", tree)
    else:
        monkeypatch.setattr(sync_engine, "_save_state", save)
    st = _round(local)
    assert st["last_error"] and page["id"] in st["retry"]
    assert remote.texts(page["id"]) == {"c1": "one (local)", "c2": "two (local)"}  # the push had landed
    monkeypatch.setattr(sync_engine, "_save_state", real_save)
    monkeypatch.setattr(sync_engine, "_remote_tree", real_tree)
    local.ops(page["id"], [{"op": "set", "id": "c1", "content": "one (local) more", "base": "one (local)"},
                           {"op": "set", "id": "c2", "content": "two (local) more", "base": "two (local)"}])
    _sync(local)
    expect = {"c1": "one (local) more", "c2": "two (local) more"}
    assert local.texts(page["id"]) == expect == remote.texts(page["id"])
    assert conflicts(local) == []
    same_tree(remote, local, page["id"])


def test_a_push_whose_answer_was_lost_is_not_applied_back(monkeypatch):
    """The push landed but its answer never came back (the link dropped on
    the way back): the remote's tree holds this copy's edit; typing on here
    and the next round keep it once."""
    remote, local, _ = _pair()
    page = remote.page("Lost answer")
    remote.insert(page["id"], "l1", "alpha")
    _sync(local)
    local.ops(page["id"], [{"op": "set", "id": "l1", "content": "alpha beta"}])
    real = sync_engine.default_fetch

    def lost(method, path, body, headers):
        out = real(method, path, body, headers)
        if method == "POST" and path.endswith("/ops"):
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")
        return out

    monkeypatch.setattr(sync_engine, "default_fetch", lost)
    assert _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "default_fetch", real)
    assert remote.texts(page["id"])["l1"] == "alpha beta"
    local.ops(page["id"], [{"op": "set", "id": "l1", "content": "alpha beta gamma", "base": "alpha beta"}])
    _sync(local)
    assert local.texts(page["id"])["l1"] == "alpha beta gamma" == remote.texts(page["id"])["l1"]
    assert conflicts(local) == []


def test_a_round_cut_short_before_its_first_base_does_not_double_the_pull(monkeypatch):
    """The remote's change landed here and the round stopped before it saved
    that (the app quit): the next round finds the change in the text already,
    typing and all."""
    remote, local, _ = _pair()
    page = remote.page("Quit mid pull")
    remote.insert(page["id"], "q1", "Results table")
    _sync(local)
    remote.ops(page["id"], [{"op": "set", "id": "q1", "content": "Results table v2"}])
    real_save = sync_engine._save_state

    def quit_(conn, page_id, seq, base):
        raise RuntimeError("the app quit")

    monkeypatch.setattr(sync_engine, "_save_state", quit_)
    assert _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "_save_state", real_save)
    assert local.texts(page["id"])["q1"] == "Results table v2"
    local.ops(page["id"], [{"op": "set", "id": "q1", "content": "Results table v2 (checked)", "base": "Results table v2"}])
    _sync(local)
    assert local.texts(page["id"])["q1"] == "Results table v2 (checked)" == remote.texts(page["id"])["q1"]


def test_a_new_block_pushed_then_typed_in_keeps_the_typing(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("New block, then typing")
    remote.insert(page["id"], "nb0", "intro")
    _sync(local)
    local.ops(page["id"], [{"op": "insert", "id": "nb1", "parent": page["id"], "position": "a5", "content": "draft"}])
    real_tree = sync_engine._remote_tree
    calls = []

    def flaky(remote_, page_id):
        calls.append(1)
        if len(calls) == 1:
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")  # the read after the push
        return real_tree(remote_, page_id)

    monkeypatch.setattr(sync_engine, "_remote_tree", flaky)
    assert _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "_remote_tree", real_tree)
    local.ops(page["id"], [{"op": "set", "id": "nb1", "content": "draft, and the paragraph after", "base": "draft"}])
    _sync(local)
    expect = {"nb0": "intro", "nb1": "draft, and the paragraph after"}
    assert local.texts(page["id"]) == expect == remote.texts(page["id"])
    assert conflicts(local) == []


def test_a_remote_insert_of_a_block_already_here_keeps_the_text_here():
    """A block both sides hold that the base lacks (a round cut short between
    applying and saving): the remote's version is not laid over the text
    here; a remote text this copy does not hold waits in a conflict."""
    remote, local, _ = _pair()
    page = remote.page("Known twice")
    remote.insert(page["id"], "k0", "intro")
    _sync(local)
    remote.ops(page["id"], [{"op": "insert", "id": "k1", "parent": page["id"], "position": "a5",
                             "content": "written there", "props": {"color": "red"}}])
    local.ops(page["id"], [{"op": "insert", "id": "k1", "parent": page["id"], "position": "a5",
                            "content": "written here", "props": {"tag": "x"}}])
    _sync(local)
    for side in (remote, local):
        k1 = next(c for c in side.tree(page["id"])["children"] if c["id"] == "k1")
        assert (k1["content"], k1["properties"]) == ("written here", {"color": "red", "tag": "x"}), side.name
    assert [(x["kind"], x["mine"], x["theirs"]) for x in conflicts(local)] == [
        ("diverged", "written here", "written there")]


# --- M3: a tombstone says nothing about a page with no base ---------------------------------

@pytest.mark.parametrize("adopt", ["mine", "theirs"])
def test_linking_a_restored_backup_keeps_the_pages_deleted_on_the_original(adopt):
    remote = Side(f"mi_backup_remote_{adopt}")
    local = Side(f"mi_backup_local_{adopt}")
    lost = remote.page("Thesis chapter 3")
    remote.insert(lost["id"], "t1", "three months of notes")
    kept = remote.page("Still there")
    # last night's backup of the original, restored here: the same pages under the same ids
    for p, blocks in ((lost, {"t1": "three months of notes"}), (kept, {})):
        local.client.post("/api/pages", json={"id": p["id"], "title": p["content"]}).raise_for_status()
        for bid, text in blocks.items():
            local.insert(p["id"], bid, text)
    # this morning the chapter was deleted on the original — the reason for the recovery
    remote.client.delete(f"/api/blocks/{lost['id']}").raise_for_status()
    token = create_token(remote.name, remote.ws, "recover", 90, scope="write")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws, "adopt": adopt})
    assert r.status_code == 201, r.text
    st = _sync(local)
    assert st["pages_deleted"] == 0
    assert local.texts(lost["id"]) == {"t1": "three months of notes"} == remote.texts(lost["id"])
    assert kept["id"] in local.pages() and kept["id"] in remote.pages()
    assert [(x["kind"], x["page_id"]) for x in conflicts(local)] == [("page_restored", lost["id"])]
    log = local.client.get(f"/api/mirrors/{local.ws}/log").json()["changes"]
    assert ("restored there", lost["id"]) in [(x["action"], x["page_id"]) for x in log]
    _sync(local)  # settled: the next round changes nothing
    assert local.texts(lost["id"]) == remote.texts(lost["id"])


def test_a_receive_only_link_keeps_a_page_the_original_deleted():
    remote = Side("mi_backup_remote_pull")
    local = Side("mi_backup_local_pull")
    lost = remote.page("Deleted there")
    remote.insert(lost["id"], "rp1", "kept here")
    local.client.post("/api/pages", json={"id": lost["id"], "title": "Deleted there"}).raise_for_status()
    local.insert(lost["id"], "rp1", "kept here")
    remote.client.delete(f"/api/blocks/{lost['id']}").raise_for_status()
    token = create_token(remote.name, remote.ws, "recover", 90, scope="read")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws})
    assert r.status_code == 201 and r.json()["mode"] == "pull", r.text
    _sync(local)
    assert local.texts(lost["id"]) == {"rp1": "kept here"} and lost["id"] not in remote.pages()

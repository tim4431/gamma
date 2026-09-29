"""The mirror's regressions from the sync test campaign (docs/dev/mirror.md).

A copy that made no edits writes nothing to its origin: not after a batch
that deletes a block and inserts another at its key, not after a block
moved to another page there (edited there since, or holding blocks that
had moved out of it), not after a receive-only round, not over a key an
earlier round left behind. A push whose answer was lost is confirmed under
its batch id before anything else, so it is never taken for the remote's
own change (a collaborator's edit reverted, a text doubled). A page whose
creation there lost its answer is not replaced under a link's policy. A
page restored from the remote's Recently deleted comes back to a copy
that had removed it. A file cut short on the way is never stored, and a
page pulled whole takes the remote's place in the library. Same in-process
pair as test_mirror.py."""
import socket
import threading

import pytest

from gamma import ops, sync_engine
from gamma.db import connect_pages_db, ws_uploads_dir
from gamma.integrations import create_token
from gamma.sync_tree import snapshot_from_tree
from test_mirror import Side, _pair, _sync, _transport  # noqa: F401 — the transport fixture applies here too
from test_mirror_edges import conflicts, flat, same_tree


def _round(local):
    """One round that may fail; its status."""
    r = local.client.post(f"/api/mirrors/{local.ws}/sync?wait=1")
    assert r.status_code == 200, r.text
    return r.json()["status"]


def _page(side, page_id, title):
    side.client.post("/api/pages", json={"id": page_id, "title": title}).raise_for_status()
    return page_id


def _head(side, page_id):
    return side.client.get(f"/api/pages/{page_id}/ops?since=0").json()["seq"]


def _pushed(remote, page_id, since):
    """The batches a copy wrote into the remote's page after seq ``since``."""
    batches = remote.client.get(f"/api/pages/{page_id}/ops?since={since}").json()["batches"]
    return [b["ops"] for b in batches if b["client"] == sync_engine.CLIENT]


def _blocks(side, page_id):
    """Every block under the page as ``{id: (parent, key, text, props)}`` — keys included."""
    snap = snapshot_from_tree(side.tree(page_id))
    return {bid: (b["parent"], b["position"], b["content"], b["props"]) for bid, b in snap.items() if bid != page_id}


def _cross(side, block_id, page_id, lower="a0"):
    """Move a block to another page there, past the key ``lower`` (the cross-page move)."""
    r = side.client.post(f"/api/blocks/{block_id}/reorder", json={"parent_id": page_id, "before": lower, "after": None})
    assert r.status_code == 200, r.text


def _text(side, block_id):
    return side.client.get(f"/api/blocks/{block_id}").json()["content"]


# --- a copy that made no edits pushes nothing ---------------------------------------------------

def test_a_delete_and_an_insert_at_one_key_push_nothing_back():
    """The remote's one batch deletes A and inserts B at A's key: applied
    here inserts first, B is re-keyed on arrival — and was pushed back as a
    move, every such batch one unrequested write."""
    remote, local, _ = _pair()
    page = remote.page("Key churn")["id"]
    remote.insert(page, "kcA", "A", position="a0")
    remote.insert(page, "kcC", "C", position="a1")
    _sync(local)
    since = _head(remote, page)
    remote.ops(page, [{"op": "delete", "id": "kcA"},
                      {"op": "insert", "id": "kcB", "parent": page, "position": "a0", "content": "B"}])
    for _ in range(2):
        assert _sync(local)["pages_pushed"] == 0
    assert _pushed(remote, page, since) == []
    assert _blocks(local, page) == _blocks(remote, page)  # the keys too


def test_a_receive_only_round_leaves_no_key_behind_to_resurrect_a_block():
    remote, local, _ = _pair()
    page = remote.page("Resurrection")["id"]
    remote.insert(page, "rsA", "A", position="a0")
    remote.insert(page, "rsC", "C", position="a1")
    _sync(local)
    remote.ops(page, [{"op": "delete", "id": "rsA"},
                      {"op": "insert", "id": "rsB", "parent": page, "position": "a0", "content": "B"}])
    local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "pull"}).raise_for_status()
    _sync(local)
    assert _blocks(local, page) == _blocks(remote, page)
    remote.client.delete("/api/blocks/rsB").raise_for_status()
    local.client.patch(f"/api/mirrors/{local.ws}", json={"mode": "two-way"}).raise_for_status()
    since = _head(remote, page)
    _sync(local)
    assert set(local.texts(page)) == {"rsC"} == set(remote.texts(page))
    assert _pushed(remote, page, since) == [] and conflicts(local) == []


@pytest.mark.parametrize("then", ["nothing", "the remote deletes it"])
def test_a_key_an_earlier_round_left_here_is_put_back_not_pushed(then):
    """A copy an older version left with a block on another key than the
    remote's, in the same order (a re-key on arrival): that is no move made
    here — never pushed, never an edit that beats the remote's delete."""
    remote, local, _ = _pair()
    page = remote.page(f"Left behind ({then})")["id"]
    remote.insert(page, "lbA", "A", position="a0")
    remote.insert(page, "lbC", "C", position="a1")
    _sync(local)
    r = local.client.post(f"/api/pages/{page}/ops", json={"client": sync_engine.CLIENT, "ops": [
        {"op": "move", "id": "lbA", "parent": page, "position": "a0V"}]})  # as the engine wrote it then
    assert r.status_code == 200, r.text
    if then != "nothing":
        remote.client.delete("/api/blocks/lbA").raise_for_status()
    since = _head(remote, page)
    _sync(local)
    assert _pushed(remote, page, since) == []
    assert _blocks(local, page) == _blocks(remote, page)
    assert ("lbA" in local.texts(page)) == (then == "nothing")


def test_a_block_moved_next_to_a_block_made_here_is_pushed_not_put_back():
    """A move here that keeps the order of the blocks the base knew, but not
    against a block made here, is still a move."""
    remote, local, _ = _pair()
    page = remote.page("Moved next to a new block")["id"]
    remote.insert(page, "mnA", "A", position="a0")
    remote.insert(page, "mnB", "B", position="a1")
    _sync(local)
    local.ops(page, [{"op": "insert", "id": "mnN", "parent": page, "position": "a0V", "content": "N, made here"},
                     {"op": "move", "id": "mnB", "parent": page, "position": "a0G"}])
    _sync(local)
    order = [c["id"] for c in remote.tree(page)["children"]]
    assert order == ["mnA", "mnB", "mnN"] == [c["id"] for c in local.tree(page)["children"]]


def test_a_block_moved_to_another_page_there_then_edited_there_keeps_the_edit():
    """The destination page is reconciled first (ids sort that way): the
    block arrives from its page here with the remote's text, not the older
    one this copy held — which was pushed back over the edit."""
    remote, local, _ = _pair()
    src, dst = _page(remote, "Z1src000000A", "Source"), _page(remote, "A1dst000000A", "Destination")
    remote.insert(src, "m1X", "one", position="a0")
    remote.insert(dst, "m1Y", "y", position="a0")
    _sync(local)
    heads = {p: _head(remote, p) for p in (src, dst)}
    _cross(remote, "m1X", dst)
    remote.client.put("/api/blocks/m1X", json={"content": "one two", "base": "one"}).raise_for_status()
    _sync(local)
    assert _text(remote, "m1X") == "one two" == local.texts(dst)["m1X"]
    assert all(_pushed(remote, p, heads[p]) == [] for p in (src, dst))
    assert conflicts(local) == []
    for p in (src, dst):
        assert _blocks(local, p) == _blocks(remote, p)


def test_a_block_moved_out_of_a_subtree_that_moved_to_another_page_is_not_deleted():
    """S leaves V for E there, gets a child, then V moves to another page
    (reconciled first). V left its page here with S still under it — and
    that page's round took S for deleted here and deleted it there."""
    remote, local, _ = _pair()
    src, dst = _page(remote, "Z2src000000A", "Source"), _page(remote, "A2dst000000A", "Destination")
    remote.insert(src, "m2V", "V", position="a0")
    remote.insert(src, "m2S", "S keeps my text", parent="m2V", position="a0")
    remote.insert(src, "m2E", "E", position="a1")
    remote.insert(dst, "m2W", "w", position="a0")
    _sync(local)
    heads = {p: _head(remote, p) for p in (src, dst)}
    remote.ops(src, [{"op": "move", "id": "m2S", "parent": "m2E", "position": "a0"}])
    remote.insert(src, "m2Q", "Q, a new child of S", parent="m2S", position="a0")
    _cross(remote, "m2V", dst)
    _sync(local)
    t = flat(remote.tree(src))
    assert (t["m2S"]["parent"], t["m2S"]["content"], t["m2Q"]["parent"]) == ("m2E", "S keeps my text", "m2S")
    assert all(_pushed(remote, p, heads[p]) == [] for p in (src, dst))
    assert conflicts(local) == []
    for p in (src, dst):
        assert _blocks(local, p) == _blocks(remote, p)


def test_what_this_copy_did_under_a_block_moved_to_another_page_there_is_kept():
    """The same move with edits here: S's text edited (S stays in its page,
    where the remote put it, with the edit), a child made under V (it goes
    along with V)."""
    remote, local, _ = _pair()
    src, dst = _page(remote, "Z3src000000A", "Source"), _page(remote, "A3dst000000A", "Destination")
    remote.insert(src, "m3V", "V", position="a0")
    remote.insert(src, "m3S", "S", parent="m3V", position="a0")
    remote.insert(src, "m3E", "E", position="a1")
    _sync(local)
    local.ops(src, [{"op": "set", "id": "m3S", "content": "S, edited here", "base": "S"}])
    local.insert(src, "m3N", "made here under V", parent="m3V", position="a1")
    remote.ops(src, [{"op": "move", "id": "m3S", "parent": "m3E", "position": "a0"}])
    _cross(remote, "m3V", dst, lower=None)
    _sync(local)
    _sync(local)
    t, d = flat(remote.tree(src)), flat(remote.tree(dst))
    assert (t["m3S"]["parent"], t["m3S"]["content"]) == ("m3E", "S, edited here")
    assert (d["m3N"]["parent"], d["m3N"]["content"]) == ("m3V", "made here under V")
    for p in (src, dst):
        same_tree(remote, local, p)


@pytest.mark.parametrize("first", ["the destination", "the source"])
def test_a_block_edited_here_and_moved_and_edited_there_keeps_both_edits(first):
    """Both sides edited the block, the remote after moving it to another
    page: the text arrives merged, whichever page the round reaches first.
    The text here used to win outright, dropping the remote's edit."""
    remote, local, _ = _pair()
    ids = ("A4src000000A", "Z4dst000000A") if first == "the source" else ("Z4src000000A", "A4dst000000A")
    src, dst = _page(remote, ids[0], "Source"), _page(remote, ids[1], "Destination")
    remote.insert(src, "m4X", "alpha beta gamma", position="a0")
    _sync(local)
    local.ops(src, [{"op": "set", "id": "m4X", "content": "alpha beta gamma (here)", "base": "alpha beta gamma"}])
    _cross(remote, "m4X", dst, lower=None)
    remote.client.put("/api/blocks/m4X", json={"content": "ALPHA beta gamma", "base": "alpha beta gamma"}).raise_for_status()
    _round(local)
    st = _sync(local)
    assert not st.get("retry"), st
    assert _text(remote, "m4X") == "ALPHA beta gamma (here)" == local.texts(dst)["m4X"]
    assert "m4X" not in local.texts(src) and "m4X" not in remote.texts(src)
    assert "merged" in [c["kind"] for c in conflicts(local)]


def test_typing_while_a_round_runs_is_pushed_not_put_back(monkeypatch):
    """What differs only through the engine's writes is put back as the
    remote has it; a block typed into while the round ran is not that."""
    remote, local, _ = _pair()
    page = remote.page("Typing mid-round")["id"]
    remote.insert(page, "tr1", "remote changes this", position="a0")
    remote.insert(page, "tr2", "typed into here", position="a1")
    _sync(local)
    remote.ops(page, [{"op": "set", "id": "tr1", "content": "remote changed this"}])
    real, typed = sync_engine._pull_files, []

    def type_meanwhile(ws, remote_, names, report):
        # between the snapshot the round reads and the one it pushes from
        if ws == local.ws and not typed:
            typed.append(1)
            local.ops(page, [{"op": "set", "id": "tr2", "content": "typed into here, mid-round", "base": "typed into here"}])
        return real(ws, remote_, names, report)

    monkeypatch.setattr(sync_engine, "_pull_files", type_meanwhile)
    _sync(local)
    assert typed
    expect = {"tr1": "remote changed this", "tr2": "typed into here, mid-round"}
    assert local.texts(page) == expect == remote.texts(page)


# --- a push whose answer was lost ----------------------------------------------------------

def _lose_push_answer(monkeypatch, how="reset"):
    """The next POST …/ops lands on the remote and its answer never comes back."""
    real = sync_engine.default_fetch
    hit = []

    def lose(method, path, body, headers):
        out = real(method, path, body, headers)
        if method == "POST" and path.endswith("/ops") and not hit:
            hit.append(out[0])
            if how == "504":
                return 504, b"<html>504 Gateway Time-out</html>"
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")
        return out

    monkeypatch.setattr(sync_engine, "default_fetch", lose)
    return lambda: monkeypatch.setattr(sync_engine, "default_fetch", real)


@pytest.mark.parametrize("how", ["reset", "504"])
def test_a_new_block_whose_push_answer_was_lost_keeps_the_remote_edit(monkeypatch, how):
    """The block made here went over, the answer was lost, a collaborator
    typed in it: the collaborator's text stays on both sides (it used to be
    reverted, with a ``diverged`` conflict)."""
    remote, local, _ = _pair()
    page = remote.page(f"Lost answer, new block ({how})")["id"]
    remote.insert(page, "la0", "intro", position="a0")
    _sync(local)
    local.ops(page, [{"op": "insert", "id": "la1", "parent": page, "position": "a5", "content": "Draft paragraph"}])
    undo = _lose_push_answer(monkeypatch, how)
    assert _round(local)["last_error"]
    undo()
    remote.ops(page, [{"op": "set", "id": "la1", "content": "Draft paragraph, reviewed", "base": "Draft paragraph"}])
    _sync(local)
    assert local.texts(page)["la1"] == "Draft paragraph, reviewed" == remote.texts(page)["la1"]
    assert conflicts(local) == []
    assert len(_pushed(remote, page, 0)) == 1  # the one push, never sent twice


def test_the_app_quit_after_a_push_then_the_remote_edits_the_new_block(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Quit after push")["id"]
    remote.insert(page, "aq0", "intro", position="a0")
    _sync(local)
    local.ops(page, [{"op": "insert", "id": "aq1", "parent": page, "position": "a5", "content": "Draft paragraph"}])
    real_save = sync_engine._save_state

    def quit_after_push(conn, page_id, seq, base):
        if seq == sync_engine.UNKNOWN_SEQ:
            raise RuntimeError("the app quit")
        real_save(conn, page_id, seq, base)

    monkeypatch.setattr(sync_engine, "_save_state", quit_after_push)
    assert _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "_save_state", real_save)
    remote.ops(page, [{"op": "set", "id": "aq1", "content": "Draft paragraph, reviewed", "base": "Draft paragraph"}])
    _sync(local)
    assert local.texts(page)["aq1"] == "Draft paragraph, reviewed" == remote.texts(page)["aq1"]
    assert conflicts(local) == []


@pytest.mark.parametrize("remote_forgot", [False, True])
def test_a_text_edit_whose_push_answer_was_lost_is_not_merged_twice(monkeypatch, remote_forgot):
    """The edit landed, its answer was lost, the remote typed elsewhere in
    the block: the next round finds the edit there (the batch answered
    under its id — or, the remote having forgotten it, shown in its text)
    and merges nothing twice ("jumps jumps")."""
    remote, local, _ = _pair()
    page = remote.page(f"Lost answer, same block ({remote_forgot})")["id"]
    remote.insert(page, "lt1", "The quick fox", position="a0")
    _sync(local)
    local.ops(page, [{"op": "set", "id": "lt1", "content": "The quick fox jumps", "base": "The quick fox"}])
    undo = _lose_push_answer(monkeypatch)
    assert _round(local)["last_error"]
    undo()
    remote.ops(page, [{"op": "set", "id": "lt1", "content": "The quick brown fox jumps", "base": "The quick fox jumps"}])
    if remote_forgot:
        monkeypatch.setattr(ops, "_replays", type(ops._replays)())  # a restart there
    _sync(local)
    assert local.texts(page)["lt1"] == "The quick brown fox jumps" == remote.texts(page)["lt1"]
    assert conflicts(local) == []


def test_a_lost_answer_then_typing_on_both_sides(monkeypatch):
    remote, local, _ = _pair()
    page = remote.page("Lost answer, both type")["id"]
    remote.insert(page, "lb1", "alpha", position="a0")
    _sync(local)
    local.ops(page, [{"op": "set", "id": "lb1", "content": "alpha beta", "base": "alpha"}])
    undo = _lose_push_answer(monkeypatch)
    assert _round(local)["last_error"]
    undo()
    local.ops(page, [{"op": "set", "id": "lb1", "content": "alpha beta gamma", "base": "alpha beta"}])
    remote.ops(page, [{"op": "set", "id": "lb1", "content": "ALPHA beta", "base": "alpha beta"}])
    _sync(local)
    assert local.texts(page)["lb1"] == "ALPHA beta gamma" == remote.texts(page)["lb1"]
    assert [c["kind"] for c in conflicts(local)] == ["merged"]


def test_a_push_that_never_arrived_is_sent_again_under_its_id(monkeypatch):
    """The link dropped before the batch reached the remote; the remote
    edited the block meanwhile. The next round sends the batch again under
    its id: applied once, merged with the remote's edit."""
    remote, local, _ = _pair()
    page = remote.page("Never arrived")["id"]
    remote.insert(page, "na1", "alpha", position="a0")
    _sync(local)
    local.ops(page, [{"op": "set", "id": "na1", "content": "alpha beta", "base": "alpha"}])
    real = sync_engine.default_fetch
    sent = []

    def drop(method, path, body, headers):
        if method == "POST" and path.endswith("/ops") and not sent:
            sent.append(body)
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")
        return real(method, path, body, headers)

    monkeypatch.setattr(sync_engine, "default_fetch", drop)
    assert _round(local)["last_error"]
    monkeypatch.setattr(sync_engine, "default_fetch", real)
    remote.ops(page, [{"op": "set", "id": "na1", "content": "ALPHA", "base": "alpha"}])
    since = _head(remote, page)
    _sync(local)
    assert local.texts(page)["na1"] == "ALPHA beta" == remote.texts(page)["na1"]
    assert len(_pushed(remote, page, since)) == 1


# --- a page's creation whose answer was lost ------------------------------------------------

def _lose_create_answer(monkeypatch, how="reset"):
    real = sync_engine.default_fetch
    hit = []

    def lose(method, path, body, headers):
        out = real(method, path, body, headers)  # the remote does create the page
        if method == "POST" and path == "/api/pages" and not hit:
            hit.append(out[0])
            if how == "504":
                return 504, b"<html>504 Gateway Time-out</html>"
            raise sync_engine.RemoteError(0, "cannot reach the remote: connection reset")
        return out

    monkeypatch.setattr(sync_engine, "default_fetch", lose)
    return lambda: monkeypatch.setattr(sync_engine, "default_fetch", real)


def _link(remote, local, adopt="theirs"):
    token = create_token(remote.name, remote.ws, "link", 90, scope="write")["token"]
    r = local.client.post("/api/mirrors", json={"remote_url": "http://testserver", "token": token,
                                                "workspace_id": local.ws, "adopt": adopt})
    assert r.status_code == 201, r.text


def test_a_page_whose_creation_answer_was_lost_is_not_emptied_under_a_link(monkeypatch):
    """A linked workspace (adopt: the original's version) sends its own page
    over; the create landed, its answer did not. The next round found the
    page on both sides with no base and took the remote's empty one."""
    remote, local = Side("mreg_link_remote"), Side("mreg_link_local")
    remote.page("Shared start")
    page = local.page("Only on the laptop")["id"]
    notes = {f"lk{i}": f"laptop note {i}" for i in range(3)}
    for i, (bid, text) in enumerate(notes.items()):
        local.insert(page, bid, text, position=f"a{i}")
    _link(remote, local)
    undo = _lose_create_answer(monkeypatch)
    assert _round(local)["last_error"]
    undo()
    _sync(local)
    assert local.texts(page) == notes == remote.texts(page)
    assert conflicts(local) == []


@pytest.mark.parametrize("how", ["reset", "504"])
def test_a_page_going_back_there_whose_creation_answer_was_lost_is_kept(monkeypatch, how):
    """A restored backup linked to its original, which deleted a page since:
    the page goes back there (``page_restored``) and its creation's answer
    is lost."""
    remote, local = Side(f"mreg_back_remote_{how}"), Side(f"mreg_back_local_{how}")
    lost = remote.page("Thesis chapter 3")["id"]
    remote.insert(lost, "bk1", "three months of notes", position="a0")
    local.client.post("/api/pages", json={"id": lost, "title": "Thesis chapter 3"}).raise_for_status()
    local.insert(lost, "bk1", "three months of notes", position="a0")
    remote.client.delete(f"/api/blocks/{lost}").raise_for_status()
    _link(remote, local)
    undo = _lose_create_answer(monkeypatch, how)
    assert _round(local)["last_error"]
    undo()
    _sync(local)
    _sync(local)
    assert local.texts(lost) == {"bk1": "three months of notes"} == remote.texts(lost)
    assert [(c["kind"], c["page_id"]) for c in conflicts(local)] == [("page_restored", lost)]


# --- a page restored from the remote's Recently deleted -------------------------------------

def test_a_page_restored_from_the_remote_trash_comes_back_here():
    """The round that took the remote's delete put this copy's page in its
    own Recently deleted; the tombstone that wrote was read next round as a
    deletion made here, and the restored page never came back."""
    remote, local, _ = _pair()
    page = remote.page("Deleted by mistake")["id"]
    remote.insert(page, "tr1", "important notes")
    _sync(local)
    remote.client.delete(f"/api/blocks/{page}").raise_for_status()
    assert _sync(local)["pages_deleted"] == 1 and page not in local.pages()
    remote.client.post(f"/api/trash/{page}/restore").raise_for_status()
    _sync(local)
    assert local.texts(page) == {"tr1": "important notes"}
    assert page not in [p["id"] for p in local.client.get("/api/trash").json()["pages"]]
    remote.ops(page, [{"op": "set", "id": "tr1", "content": "important notes, edited later"}])
    _sync(local)
    assert local.texts(page) == {"tr1": "important notes, edited later"}


# --- files and places -----------------------------------------------------------------------

def test_a_file_cut_short_on_the_way_is_not_stored(monkeypatch):
    """The remote announces the length and the link drops half way: the
    real transport's streaming read just stops, and a PDF only has to start
    like one — the half file was stored for good."""
    body = b"%PDF-1.4 cut short\n" + b"t" * 5000
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        conn, _ = srv.accept()
        conn.recv(65536)
        conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: application/pdf\r\nContent-Length: %d\r\n\r\n" % len(body))
        conn.sendall(body[:1200])
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    local = Side("mreg_cut_file")
    monkeypatch.setattr(sync_engine, "default_fetch", None)  # the real, streaming transport
    remote = sync_engine.Remote(f"http://127.0.0.1:{srv.getsockname()[1]}", "ws", "gamma_x")
    name = "0123456789abcdef01234567.pdf"
    try:
        with pytest.raises(sync_engine.RemoteError, match="stopped at 1200 of"):
            sync_engine._pull_files(local.ws, remote, {name}, {"files_pulled": 0})
    finally:
        srv.close()
    assert not (ws_uploads_dir(local.ws) / name).exists()


def test_pages_pulled_whole_keep_the_remote_place_in_the_library():
    remote, local, _ = _pair()
    for pid in ("zzPlace0000A", "mmPlace0000A", "aaPlace0000A"):  # made in the order their ids sort last
        _page(remote, pid, pid)
    _sync(local)
    keys = {pid: p["position"] for pid, p in remote.pages().items()}
    assert {pid: p["position"] for pid, p in local.pages().items()} == keys


def test_a_pending_push_does_not_read_as_a_block():
    """The push in flight rides in the stored base under a key no block id
    can take; every reader of the base gets the blocks alone."""
    remote, local, _ = _pair()
    page = remote.page("Pending")["id"]
    remote.insert(page, "pp1", "one", position="a0")
    _sync(local)
    with connect_pages_db(local.ws) as conn:
        state = sync_engine._state(conn, page)
        sync_engine._store_state(conn, page, state["remote_seq"], state["base"],
                                 {"batches": [{"id": "b1", "ops": []}], "at": ""})
        again = sync_engine._state(conn, page)
    assert again["base"] == state["base"] and again["pending"]["batches"][0]["id"] == "b1"
    assert sync_engine.PENDING not in again["base"]

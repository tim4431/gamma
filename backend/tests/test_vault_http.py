"""A synced vault of a folder on another Gamma server (gamma/vault_http.py
``HttpRemote``, docs/research/vaults.md "In steps" 5), over a TestClient that
stands in for that server (test_folder_links.py ``over_http``): pull with a
token that may read, push of everything with one that may write (a note
changed, a note new, a directory new, a PDF new and stored there, a picture a
note links, a note removed), the file winning a clash with Gamma's text kept
in this computer's history, a round that died after its batch, a refused
token that stops the vault and no file; and a server that misbehaves: blocks
or a PDF refused, a plan's answer lost, a page written between a plan's read
and its request, a log that cannot say, a feed that does not move, a folder
gone there, a server error."""

import base64
import io
import json
import os
import time
import zlib

import pymupdf
import pytest

from conftest import (account_of, doc_props, fresh_client, login, make_folder, make_page, make_user, mark_work_folder,
                      work_props, workspace_of)
from gamma import folder_links, gamma_sync, vault_http, vault_store, vault_sync
from gamma import ops as ops_module
from gamma.auth import CLIENT_HEADER
from gamma.blocks_store import block_kind, new_block_id
from gamma.db import connect_pages_db, connect_users_db, connect_vaults_db
from gamma.integrations import create_token
from test_folder_links import _create, _sync, disk, over_http  # noqa: F401: the disk and over_http fixtures

OWNER = "vh_owner"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


class Killed(BaseException):
    """The process dying: nothing below catches it."""


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def settled(monkeypatch):
    monkeypatch.setattr(vault_sync, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)


_made = []    # the tokens a test made, revoked after it: an account holds twenty at most


@pytest.fixture(autouse=True)
def _tokens_revoked():
    yield
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id IN (SELECT value FROM json_each(?))", (json.dumps(_made),))
    _made.clear()


def _token(scope="read"):
    token = create_token(account_of(OWNER), workspace_of(OWNER), f"vh-{scope}", 1,
                         **({"scope": "write"} if scope == "write" else {}))
    _made.append(token["id"])
    return token


def _put(path, data, age: float = 10):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data.encode("utf-8") if isinstance(data, str) else data)
    then = time.time() - age
    os.utime(path, (then, then))
    return path


def _pdf(text: str) -> bytes:
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _remote_vault(c, server, folder, root, token, **body):
    link = _create(c, folder=folder, path=str(root), remote_url=server, token=token["token"], token_id=token["id"], **body)
    status = _sync(c, link["id"])
    assert status["last_error"] == "", status
    return link


def _layout(folder):
    return vault_sync.LocalRemote(workspace_of(OWNER), folder, account_of(OWNER)).layout()


def _texts(page_id) -> list:
    blocks = vault_sync.LocalRemote(workspace_of(OWNER), "root", account_of(OWNER)).page(page_id).blocks
    return sorted(b["content"] for i, b in blocks.items() if i != page_id and block_kind(b["parent"], page_id, b["props"]) == "note")


def _page_of(link, rel):
    return vault_store.files(link["id"])[rel]["page_id"]


def _quiet(c, link_id):
    status = _sync(c, link_id)
    moved = {k: v for k, v in status["counts"].items() if k in ("added", "updated", "renamed", "removed", "sent") and v}
    assert status["last_error"] == "" and not moved and not status["kept"], status
    return status


def test_a_vault_of_another_server_pushes_everything_with_a_token_that_may_write(owner, disk, over_http):
    folder = make_folder(owner, "VH lab")
    page = make_page(owner, "VH page", {"folders": [folder]})["id"]
    block = f"vh-{page}"
    owner.put(f"/api/blocks/{page}/children", json={"blocks": [
        {"id": block, "content": "gamma words", "properties": {}, "children": []}]}).raise_for_status()
    root = disk / "VH"
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(root), "remote_url": over_http,
                                              "token": _token()["token"], "push": "everything"})
    assert r.status_code == 400 and "token that may write" in r.json()["detail"]
    link = _remote_vault(owner, over_http, folder, root, _token("write"), push="everything")
    assert link["push"] == "everything" and link["notes"] is True
    assert (root / "VH page.md").read_text(encoding="utf-8") == "gamma words\n"
    _quiet(owner, link["id"])

    # a note changed on disk: its page there takes the change, through the ops route, as the vault's client
    _put(root / "VH page.md", "gamma words\n\na line from disk\n")
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 1 and _texts(page) == ["a line from disk", "gamma words"]
    batches = owner.get(f"/api/pages/{page}/ops", params={"since": 0}).json()["batches"]
    assert batches[-1]["client"] == vault_sync.CLIENT and batches[-1]["batch"]

    # new notes, a new directory, a new PDF (stored there: a NAS reads no file here), a picture a note links
    _put(root / "New note.md", "fresh text\n")
    _put(root / "Sub dir" / "Deep.md", "deep text\n")
    paper = _put(root / "Paper.pdf", _pdf("VH paper"))
    _put(root / "pic.png", PNG)
    _put(root / "With picture.md", "a figure ![[pic.png]]\n")
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["kept"] == [] and status["counts"]["sent"] == 4, status
    pages = {info.title: info for info in _layout(folder).pages.values()}
    assert {"New note", "Deep", "Paper", "With picture"} <= set(pages)
    assert _texts(pages["New note"].id) == ["fresh text"]
    sub = next(f for f, (_parent, name) in _layout(folder).folders.items() if name == "Sub dir")
    assert pages["Deep"].folders == [sub]
    props = owner.get(f"/api/blocks/{pages['Paper'].id}").json()["properties"]
    assert props["doc"]["hash"] == gamma_sync.doc_of(paper) and props["doc"]["at"] == "store"
    assert owner.get(f"/api/uploads/{props['doc']['hash']}.pdf").content == paper.read_bytes()
    picture = _texts(pages["With picture"].id)[0]
    name = picture.split("/api/uploads/", 1)[1].split(")", 1)[0]
    assert owner.get(f"/api/uploads/{name}").content == PNG
    assert vault_store.files(link["id"])["pic.png"]["kind"] == "picture"
    _quiet(owner, link["id"])

    # a clash: the file wins, and Gamma's text, which that server's conflict table cannot take, is kept here
    with connect_pages_db(workspace_of(OWNER)) as conn:
        before = conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE page_id = ?", (page,)).fetchone()[0]
    owner.put(f"/api/blocks/{block}", json={"content": "gamma sentences"}).raise_for_status()
    _put(root / "VH page.md", "gamma lines\n\na line from disk\n")
    status = _sync(owner, link["id"])
    assert status["counts"]["conflicts"] == 1 and _texts(page) == ["a line from disk", "gamma lines"], status
    kept = [e["data"] for e in vault_store.history(link["id"], "VH page.md")]
    assert b"gamma sentences" in kept
    with connect_pages_db(workspace_of(OWNER)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sync_conflicts WHERE page_id = ?", (page,)).fetchone()[0] == before

    # a note removed on disk: its page there goes to Recently deleted, once two scans agree
    (root / "New note.md").unlink()
    _sync(owner, link["id"])
    status = _sync(owner, link["id"])
    assert status["counts"]["removed"] == 1
    assert owner.get(f"/api/blocks/{pages['New note'].id}").json()["trashed"]
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_round_over_http_that_died_after_its_batch_finds_it_landed(owner, disk, over_http, monkeypatch):
    folder = make_folder(owner, "VH crash")
    root = disk / "VH crash"
    link = _remote_vault(owner, over_http, folder, root, _token("write"), push="everything")
    _put(root / "Crash.md", "first\n")
    _sync(owner, link["id"])
    page = _page_of(link, "Crash.md")
    apply = vault_http.HttpRemote.apply

    def landed_then_killed(self, *args, **kw):
        apply(self, *args, **kw)
        raise Killed()
    monkeypatch.setattr(vault_http.HttpRemote, "apply", landed_then_killed)
    _put(root / "Crash.md", "first\n\nsecond\n")
    with pytest.raises(Killed):
        folder_links.run_link(link["id"])
    assert vault_store.pending(link["id"]) and _texts(page) == ["first", "second"]
    monkeypatch.setattr(vault_http.HttpRemote, "apply", apply)
    status = _sync(owner, link["id"])                  # the journal finds the batch in the page's log there
    assert status["last_error"] == "" and not vault_store.pending(link["id"])
    assert _texts(page) == ["first", "second"]         # never read twice
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_page_made_there_without_its_blocks_is_the_note_s_and_takes_them(owner, disk, over_http, monkeypatch):
    """A page is made over HTTP in two requests, the page then its blocks: a
    round that died between them left a page with none, which the journal
    keeps as the note's page, its blocks merged into it from no base: never a
    second page, where once each round made one."""
    folder = make_folder(owner, "VH half")
    root = disk / "VH half"
    link = _remote_vault(owner, over_http, folder, root, _token("write"), push="everything")
    call = vault_http.HttpRemote._call

    def dying_before_the_blocks(self, method, path, obj=None):
        if method == "PUT" and path.endswith("/children"):
            raise Killed()
        return call(self, method, path, obj)
    monkeypatch.setattr(vault_http.HttpRemote, "_call", dying_before_the_blocks)
    _put(root / "Half.md", "the whole text\n")
    with pytest.raises(Killed):
        folder_links.run_link(link["id"])
    monkeypatch.setattr(vault_http.HttpRemote, "_call", call)
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and _texts(_page_of(link, "Half.md")) == ["the whole text"]
    halves = [info for info in _layout(folder).pages.values() if info.title == "Half"]
    assert len(halves) == 1 and halves[0].id == _page_of(link, "Half.md")
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_vault_of_another_server_that_only_pulls_keeps_what_changed_on_disk(owner, disk, over_http, monkeypatch):
    folder = make_folder(owner, "VH pulled")
    page = make_page(owner, "VH pulled page", {"folders": [folder]})["id"]
    owner.put(f"/api/blocks/{page}/children", json={"blocks": [
        {"id": f"vh-{page}", "content": "from gamma", "properties": {}, "children": []}]}).raise_for_status()
    root, token = disk / "VH pulled", _token()
    link = _remote_vault(owner, over_http, folder, root, token)
    assert link["push"] == "off"
    _put(root / "VH pulled page.md", "from gamma, and mine\n")
    status = _sync(owner, link["id"])
    assert status["kept"] == [{"path": "VH pulled page.md", "why": "changed on this computer and not pushed; a force "
                                                                    "pull replaces it", "force": True}]
    assert _texts(page) == ["from gamma"]               # never pushed
    assert folder_links.due(folder_links.get_link(link["id"])) == ""   # its feed asked a moment ago, nothing to push

    # a refused token stops the vault, never a file
    owner.delete(f"/api/integrations/tokens/{token['id']}").raise_for_status()
    status = _sync(owner, link["id"])
    assert "refused" in status["last_error"] and status["failed_at"]
    assert vault_store.files(link["id"])["VH pulled page.md"]["problem"] == ""
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_only_one_vault_pushes_into_a_page_of_another_server_and_one_above_leaves_its_pages_out(owner, disk, over_http):
    """Two vaults of this computer pushing into one folder of a NAS, or one
    into a folder around the other's, were made, and both pushed into its
    pages; one that only pulls from above wrote the pushing one's pages too.
    The rules of vaults of this server hold there."""
    folder = make_folder(owner, "VH pushed once")
    sub = make_folder(owner, "VH pushed once/Sub")
    make_page(owner, "VH below", {"folders": [sub]})
    make_page(owner, "VH above", {"folders": [folder]})
    token = _token("write")
    pushing = _remote_vault(owner, over_http, sub, disk / "VH sub", token, push="pdfs")
    try:
        for name, target in (("VH same", sub), ("VH around", folder)):
            r = owner.post("/api/folder-links", json={"folder": target, "path": str(disk / name), "remote_url": over_http,
                                                      "token": token["token"], "token_id": token["id"], "push": "pdfs"})
            assert r.status_code == 400 and "pushes into too" in r.json()["detail"], (name, r.text)
        above = _remote_vault(owner, over_http, folder, disk / "VH above", _token())
        assert sorted(p.name for p in (disk / "VH above").glob("*.md")) == ["VH above.md"]
        owner.delete(f"/api/folder-links/{above['id']}").raise_for_status()
    finally:
        owner.delete(f"/api/folder-links/{pushing['id']}").raise_for_status()


# --- a server that misbehaves, a first set -------------------------------------------------------

HTML_413 = b"<html><head><title>413 Request Entity Too Large</title></head><body>nginx</body></html>"


@pytest.fixture
def faulty(monkeypatch):
    """``over_http`` with rules in front of the other server: each rule
    ``(method, path, body)`` → None (the server answers), ``(status, bytes)``
    (answered instead) or an exception (raised), and ``log`` the requests."""
    http = fresh_client()
    rules, log = [], []

    def open_(method, path, headers, body=None):
        content = body if body is None or isinstance(body, bytes) else b"".join(body)
        log.append((method, path))
        for rule in list(rules):
            hit = rule(method, path, content)
            if isinstance(hit, Exception):
                raise hit
            if hit is not None:
                return hit[0], {}, io.BytesIO(hit[1])
        r = http.request(method, path, headers=headers, content=content)
        return r.status_code, r.headers, io.BytesIO(r.content)
    monkeypatch.setattr(gamma_sync, "default_open", open_)
    return "http://testserver", rules, log


def _lost_answer(monkeypatch, page, *, once=True, silent=False):
    """The answer to the vault's next write of ``page``'s blocks lost after
    the server took it (a timeout, a proxy dropping the connection); with
    ``silent`` the server answers nothing more that round."""
    real, state = gamma_sync.Server._send, {"hit": False}

    def send(srv, method, path, headers, body=None):
        if silent and state["hit"]:
            raise gamma_sync.Unreachable("timed out")
        out = real(srv, method, path, headers, body)
        if method == "POST" and page in path and path.rsplit("/", 1)[-1] in ("ops", "plan") and not (once and state["hit"]):
            state["hit"] = True
            raise gamma_sync.Unreachable("the answer broke off")
        return out
    monkeypatch.setattr(gamma_sync.Server, "_send", send)
    return lambda: monkeypatch.setattr(gamma_sync.Server, "_send", real)


def _titles(folder) -> list:
    return sorted(info.title for info in _layout(folder).pages.values())


def _all(link_id, word) -> list:
    """Where ``word`` is: blocks, conflicts, this computer's vault history."""
    found = []
    with connect_pages_db(workspace_of(OWNER)) as conn:
        if conn.execute("SELECT 1 FROM unified_blocks WHERE content LIKE ?", (f"%{word}%",)).fetchone():
            found.append("block")
        if any(word in (t or "") for row in conn.execute("SELECT mine, theirs FROM sync_conflicts") for t in row):
            found.append("conflict")
    with connect_vaults_db() as conn:
        if any(word.encode() in zlib.decompress(d) for (d,) in conn.execute(
                "SELECT data FROM vault_history WHERE link_id = ?", (link_id,))):
            found.append("history")
    return found


@pytest.mark.parametrize("answer", [(422, b'{"detail": "refused"}'), (413, HTML_413)])
def test_a_note_whose_blocks_the_server_refuses_makes_one_page_there_and_lands_in_it(owner, disk, faulty, answer):
    """The page was made, its blocks refused, and each round made
    one more page (an HTML 413 also stopped the vault, so the notes after it
    never went). The page made stays the note's, tried again, and a refusal
    is the file's alone."""
    url, rules, _log = faulty
    folder = make_folder(owner, f"VH refused {answer[0]}")
    root = disk / f"VH refused {answer[0]}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    rules.append(lambda m, p, b: answer if m == "PUT" and p.endswith("/children") else None)
    _put(root / "Big.md", "a big note\n")
    _put(root / "Other.md", "another note\n")
    for _ in range(3):
        status = _sync(owner, link["id"])
        assert status["last_error"] == "", status
    assert _titles(folder).count("Big") == 1 and "Other" in _titles(folder)
    assert _texts(_page_of(link, "Big.md")) == ["a big note"]           # tried again into the page made
    assert sorted(p for p in os.listdir(root) if p.endswith(".md")) == ["Big.md", "Other.md"]
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_plan_of_several_batches_lands_whole_or_not_at_all(owner, disk, faulty, monkeypatch):
    """A plan past ``MAX_OPS`` went as several requests;
    a later one refused, or the answer to the first lost, left the first
    batch's blocks to be read again each round. A plan lands in one
    request, whole or not at all."""
    monkeypatch.setattr(ops_module, "MAX_OPS", 3)
    monkeypatch.setattr(vault_http, "MAX_OPS", 3, raising=False)
    url, rules, _log = faulty
    folder = make_folder(owner, "VH batches")
    root = disk / "VH batches"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "one\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    refuse = [True]

    def last_part(m, p, b):         # the request that carries the plan's last ops
        if refuse[0] and m == "POST" and p.startswith(f"/api/pages/{page}/") and "." not in json.loads(b)["batch"]:
            return 500, b'{"detail": "boom"}'
        return None
    rules.append(last_part)
    _put(root / "N.md", "one\n\n" + "\n\n".join(f"line {i}" for i in range(7)) + "\n")
    for _ in range(2):
        _sync(owner, link["id"])
    assert _texts(page) == ["one"]
    refuse[0] = False
    back = _lost_answer(monkeypatch, page)
    folder_links.run_link(link["id"], wait=True)
    back()
    for _ in range(2):
        status = _sync(owner, link["id"])
    assert status["last_error"] == "" and _texts(page) == sorted(["one", *(f"line {i}" for i in range(7))])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("meanwhile", ["child", "typed"])
def test_what_is_typed_there_between_the_read_and_the_write_is_never_deleted_unrecorded(owner, disk, faulty, meanwhile):
    """A delete carried no base, so a child typed under a
    block the file dropped, or words typed into it, between the plan's read
    and its request, went with it. The write is refused when the page moved,
    and the plan made again sees them."""
    url, rules, _log = faulty
    folder = make_folder(owner, f"VH between {meanwhile}")
    root = disk / f"VH between {meanwhile}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "keep me\n\nB goes\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    target = next(b["id"] for b in owner.get(f"/api/blocks/{page}/subtree").json()["block"]["children"]
                  if b["content"] == "B goes")
    done = []

    def typed_between(m, p, b):
        if m == "POST" and p.startswith(f"/api/pages/{page}/") and not done:
            done.append(1)
            op = ({"op": "insert", "id": f"vh-kid-{page}", "parent": target, "content": "typed there meanwhile"}
                  if meanwhile == "child" else {"op": "set", "id": target, "content": "B goes typedmeanwhile"})
            owner.post(f"/api/pages/{page}/ops", json={"client": "web", "ops": [op]}).raise_for_status()
        return None
    rules.append(typed_between)
    _put(root / "N.md", "keep me\n")
    for _ in range(2):
        status = _sync(owner, link["id"])
    assert status["last_error"] == ""
    word = "typed there meanwhile" if meanwhile == "child" else "typedmeanwhile"
    assert word in " ".join(_texts(page)) or _all(link["id"], word), _texts(page)
    if meanwhile == "child":
        assert _texts(page) == ["keep me", "typed there meanwhile"]
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("log", ["pruned", "big"])
def test_a_lost_answer_is_found_landed_when_the_pages_log_cannot_say(owner, disk, faulty, monkeypatch, log):
    """The log of a page edited a day ago keeps its newest row,
    and a big one answers 410: the batch that landed read as not, and the
    note's new lines went in twice. The blocks it made say it landed."""
    url, _rules, _log = faulty
    folder = make_folder(owner, f"VH log {log}")
    root = disk / f"VH log {log}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "one\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    for i in range(3):
        _put(root / "N.md", "one\n\n" + "\n\n".join(f"old {j}" for j in range(i + 1)) + "\n")
        _sync(owner, link["id"])
    if log == "pruned":
        with connect_pages_db(workspace_of(OWNER)) as conn:      # what a day's pruning leaves: the newest row
            top = conn.execute("SELECT MAX(seq) FROM page_ops WHERE page_id = ?", (page,)).fetchone()[0]
            conn.execute("DELETE FROM page_ops WHERE page_id = ? AND seq < ?", (page, top))
    else:
        monkeypatch.setattr(ops_module, "CATCHUP_MAX_BYTES", 300)
    back = _lost_answer(monkeypatch, page, silent=True)
    _put(root / "N.md", "one\n\nold 0\n\nold 1\n\nold 2\n\n" + "\n\n".join(f"new {i} " + "x" * 40 for i in range(8)) + "\n")
    _sync(owner, link["id"])
    back()
    for _ in range(2):
        status = _sync(owner, link["id"])
    texts = _texts(page)
    assert status["last_error"] == "" and len(texts) == len(set(texts)) == 12, texts
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("log", ["trashed", "pruned", "big"])
def test_a_lost_answer_of_a_plan_that_made_no_block_is_found_landed(owner, disk, faulty, monkeypatch, log):
    """A plan that only set a block's text makes no block to say it landed,
    and the log of a page in Recently deleted there, or one pruned, could not
    say either (read from its start, then ``LOG_BACK`` back, 0 again for a
    short log): the pending went, and the next push merged the note's newer
    text from the old base into the text the lost plan wrote, its words twice
    in Gamma and in the file. The log is read from the seq the plan was made
    at, the first batch after it the plan's if it landed, and a page in
    Recently deleted settles once the push brings it back."""
    url, _rules, _log = faulty
    folder = make_folder(owner, f"VH lost {log}")
    root = disk / f"VH lost {log}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "alpha beta\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    for i in range(2):
        _put(root / "N.md", f"alpha beta\n\nold {i}\n")
        _sync(owner, link["id"])
    if log == "big":
        monkeypatch.setattr(ops_module, "CATCHUP_MAX_BYTES", 300)
    back = _lost_answer(monkeypatch, page, silent=True)
    _put(root / "N.md", "alpha beta gamma\n\nold 1\n")
    _sync(owner, link["id"])
    back()
    with connect_pages_db(workspace_of(OWNER)) as conn:
        if log == "trashed":
            ops_module.trash_page(workspace_of(OWNER), conn, page, actor=account_of(OWNER))
        elif log == "pruned":                                       # what a day's pruning leaves: the newest row
            top = conn.execute("SELECT MAX(seq) FROM page_ops WHERE page_id = ?", (page,)).fetchone()[0]
            conn.execute("DELETE FROM page_ops WHERE page_id = ? AND seq < ?", (page, top))
    _put(root / "N.md", "alpha beta gamma delta\n\nold 1\n")
    for _ in range(2):
        status = _sync(owner, link["id"])
    assert status["last_error"] == "" and _texts(page) == ["alpha beta gamma delta", "old 1"], _texts(page)
    assert (root / "N.md").read_text(encoding="utf-8") == "alpha beta gamma delta\n\nold 1\n"
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_lost_plan_whose_batch_others_pushed_out_of_the_log_is_found_on_the_page(owner, disk, faulty, monkeypatch):
    """The same plan, then others typing on that page there before the next
    round, and pruning keeping only the newest row, not the plan's: the log
    answered 410, the plan made no block, and its words went in twice. The
    page holds the text the plan wrote: it landed."""
    url, _rules, _log = faulty
    folder = make_folder(owner, "VH pruned past")
    root = disk / "VH pruned past"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "alpha beta\n\nkeep\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    back = _lost_answer(monkeypatch, page, silent=True)
    _put(root / "N.md", "alpha beta gamma\n\nkeep\n")
    _sync(owner, link["id"])
    back()
    ws, user = workspace_of(OWNER), account_of(OWNER)
    with connect_pages_db(ws) as conn:
        keep = conn.execute("SELECT id FROM unified_blocks WHERE page_id = ? AND content = 'keep'", (page,)).fetchone()[0]
    for n in range(3):
        ops_module.commit_ops(ws, page, [{"op": "set", "id": keep, "content": f"keep {n}"}], actor=user)
    with connect_pages_db(ws) as conn:
        newest = conn.execute("SELECT MAX(seq) FROM page_ops WHERE page_id = ?", (page,)).fetchone()[0]
        conn.execute("DELETE FROM page_ops WHERE page_id = ? AND seq < ?", (page, newest))
    _put(root / "N.md", "alpha beta gamma delta\n\nkeep\n")
    for _ in range(2):
        status = _sync(owner, link["id"])
    assert status["last_error"] == "" and _texts(page) == ["alpha beta gamma delta", "keep 2"], _texts(page)
    assert (root / "N.md").read_text(encoding="utf-8") == "alpha beta gamma delta\n\nkeep 2\n"
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_pdf_the_server_refuses_is_sent_once_until_it_changes(owner, disk, faulty, monkeypatch):
    """A proxy's 413 on a new PDF's upload, and the whole file went
    again every tick. It is kept and reported, and sent again once it changes."""
    url, rules, _log = faulty
    folder = make_folder(owner, "VH big pdf")
    root = disk / "VH big pdf"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    sent = []
    refuse = [True]

    def too_big(m, p, b):
        if m == "POST" and p == "/api/uploads":
            sent.append(len(b or b""))
            return (413, HTML_413) if refuse[0] else None
        return None
    rules.append(too_big)
    _put(root / "Big paper.pdf", _pdf("big paper"))
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: folder_links.run_link(link_id, **kw))
    for _ in range(4):
        folder_links.tick()
    assert len(sent) == 1
    assert vault_store.files(link["id"])["Big paper.pdf"]["problem"]
    refuse[0] = False
    _put(root / "Big paper.pdf", _pdf("big paper, smaller now"))
    status = _sync(owner, link["id"])
    assert len(sent) == 2 and status["counts"]["sent"] == 1 and "Big paper" in _titles(folder)
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_feed_that_says_more_and_never_moves_stops_the_round(owner, disk, faulty):
    """``more`` with the cursor where it was looped forever,
    holding the link's lock."""
    url, rules, _log = faulty
    folder = make_folder(owner, "VH stuck feed")
    root = disk / "VH stuck feed"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    calls = []

    def stuck(m, p, b):
        if p.startswith("/api/sync/changes"):
            calls.append(p)
            return RuntimeError("the feed looped") if len(calls) > 50 else (
                200, b'{"pages": [], "deleted": [], "more": true, "cursor": "7"}')
        return None
    rules.append(stuck)
    status = _sync(owner, link["id"])
    assert len(calls) <= 3 and "feed" in status["last_error"] and "unexpected" not in status["last_error"], status
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("there", [44, 53])
def test_a_server_that_keeps_its_pages_in_another_format_pauses_the_vault_before_anything(owner, disk, faulty, there):
    """A server of an older or a newer release, whose pages have another
    shape (its feed's ``format``, ``migrations.workspace_format``), as a
    clone pauses: the round reads no listing and writes nothing, the vault
    waits with the reason, and no file is a problem."""
    url, rules, log = faulty
    folder = make_folder(owner, f"VH format {there}")
    root = disk / f"VH format {there}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    feed = {"since": "", "pages": [], "deleted": [], "more": False, "cursor": "0", "format": there}
    rules.append(lambda m, p, b: (200, json.dumps(feed).encode()) if p.startswith("/api/sync/changes") else None)
    _put(root / "Written meanwhile.md", "a note the other server cannot take now\n")
    log.clear()
    status = _sync(owner, link["id"])
    assert f"workspace data format {there}" in status["last_error"] and "paused" in status["last_error"], status
    assert status["kept"] == [] and not status.get("stopped")
    assert [p for m, p in log if m != "GET"] == [] and not any(p.startswith("/api/blocks/root/") for _m, p in log)
    rules.clear()
    assert "Written meanwhile" not in _titles(folder)
    assert _sync(owner, link["id"])["last_error"] == ""          # both agree again: the round goes on
    assert "Written meanwhile" in _titles(folder)
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_page_round_asks_the_format_before_it_reads_or_sends_a_pdf(owner, disk, faulty):
    """The hot loop's round of one page (``sync_page``) pushes before it
    reads any listing: against a server of another format it sent the new
    version of a PDF, in a shape that server does not read. It reads
    nothing but the feed, and sends nothing."""
    url, rules, log = faulty
    folder = make_folder(owner, "VH format page")
    root = disk / "VH format page"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "Paper.pdf", _pdf("first version"))
    assert _sync(owner, link["id"])["last_error"] == ""
    page_id = _page_of(link, "Paper.pdf")
    _put(root / "Paper.pdf", _pdf("second version"))
    feed = {"since": "", "pages": [], "deleted": [], "more": False, "cursor": "0", "format": 44}
    rules.append(lambda m, p, b: (200, json.dumps(feed).encode()) if p.startswith("/api/sync/changes") else None)
    log.clear()
    status = vault_sync.sync_page(folder_links.get_link(link["id"]), page_id).status
    assert "workspace data format 44" in status["last_error"], status
    assert [p for _m, p in log] == ["/api/sync/changes?limit=1"]
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_push_round_after_the_vaults_folder_went_there_stops_and_makes_nothing(owner, disk, faulty):
    """A round of push alone does not read the layout, so it made
    a page filed in the folder deleted there and said nothing."""
    url, _rules, _log = faulty
    folder = make_folder(owner, "VH folder gone")
    root = disk / "VH folder gone"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    owner.delete(f"/api/folders/{folder}").raise_for_status()
    pages = {p["id"] for p in owner.get("/api/blocks/root/children").json()["children"]}
    _put(root / "New.md", "written after the folder went\n")
    status = _sync(owner, link["id"], direction="push")
    assert status["stopped"] and "no longer in Gamma" in status["last_error"], status
    assert {p["id"] for p in owner.get("/api/blocks/root/children").json()["children"]} == pages
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_server_error_on_a_read_the_round_needs_waits_and_shows_no_token(owner, disk, faulty, caplog):
    """A JSON 500 on the library listing read as an unexpected
    error with a traceback; and a detail that echoes the token showed it."""
    url, rules, _log = faulty
    folder = make_folder(owner, "VH listing 500")
    root = disk / "VH listing 500"
    token = _token("write")
    link = _remote_vault(owner, url, folder, root, token, push="everything")
    rules.append(lambda m, p, b: (500, json.dumps({"detail": f"boom {token['token']}"}).encode())
                 if p.startswith("/api/blocks/root/children") else None)
    _put(root / "X.md", "x\n")
    status = _sync(owner, link["id"])
    assert "answered 500" in status["last_error"] and "unexpected" not in status["last_error"], status
    assert token["token"] not in json.dumps(status) + caplog.text and "Traceback" not in caplog.text
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("how", ["killed", "lost"])
def test_gammas_text_a_clash_took_is_kept_before_the_write_goes(owner, disk, over_http, monkeypatch, how):
    """Gamma's side of a clash the file won was kept in the history
    only after the request came back: a round that died then, or an answer
    lost, left Gamma's words nowhere."""
    folder = make_folder(owner, f"VH clash {how}")
    root = disk / f"VH clash {how}"
    link = _remote_vault(owner, over_http, folder, root, _token("write"), push="everything")
    _put(root / "Clash.md", "alpha beta\n")
    _sync(owner, link["id"])
    page = _page_of(link, "Clash.md")
    block = next(b["id"] for b in owner.get(f"/api/blocks/{page}/subtree").json()["block"]["children"])
    owner.put(f"/api/blocks/{block}", json={"content": "alpha gammaword"}).raise_for_status()
    _put(root / "Clash.md", "alpha diskword\n")
    if how == "killed":
        ask = vault_http.HttpRemote._ask

        def landed_then_killed(self, method, path, obj=None):
            out = ask(self, method, path, obj)
            if method == "POST" and page in path:
                raise Killed()
            return out
        monkeypatch.setattr(vault_http.HttpRemote, "_ask", landed_then_killed)
        with pytest.raises(Killed):
            folder_links.run_link(link["id"])
        monkeypatch.setattr(vault_http.HttpRemote, "_ask", ask)
        folder_links._write_status(link["id"], {**folder_links.get_link(link["id"])["status"], "running": False})
    else:
        back = _lost_answer(monkeypatch, page)
        folder_links.run_link(link["id"], wait=True)
        back()
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and _texts(page) == ["alpha diskword"]
    assert "history" in _all(link["id"], "gammaword")
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


# --- a server that misbehaves, a second set -----------------------------------------------------------

PORTAL = b"<html><head><title>Hotel WiFi - sign in</title></head><body>Please accept the terms</body></html>"


@pytest.mark.parametrize("what", ["pdf", "picture"])
def test_a_download_that_is_not_the_stored_file_is_never_written(owner, disk, faulty, what):
    """A captive portal's page answered with a 200 for a stored PDF
    or picture was written into the vault as that file and recorded as its
    bytes, so no later round ever fetched the real one. Bytes that cannot be
    the file wait, and the next round fetches it again."""
    url, rules, _log = faulty
    folder = make_folder(owner, f"VH portal {what}")
    portal = [True]
    rules.append(lambda m, p, b: (200, PORTAL) if portal[0] and m == "GET" and p.startswith("/api/uploads/") else None)
    if what == "pdf":
        data = _pdf("VH portal paper")
        up = owner.post("/api/uploads", files={"file": ("p.pdf", data, "application/pdf")}).json()
        make_page(owner, "VH portal paper", {**doc_props(up["doc_id"], name="p.pdf"), "folders": [folder]})
        suffix = ".pdf"
    else:
        data, suffix = PNG, ".png"
        up = owner.post("/api/upload-file", files={"file": ("x.png", PNG, "image/png")}).json()
        page = make_page(owner, "VH portal note", {"folders": [folder]})["id"]
        owner.put(f"/api/blocks/{page}/children", json={"blocks": [
            {"id": f"vhp-{page}", "content": f"see ![x]({up['url']})", "properties": {}, "children": []}]}
                  ).raise_for_status()
    root = disk / f"VH portal {what}"
    link = _create(owner, folder=folder, path=str(root), remote_url=url, **_token_of(_token()))
    status = _sync(owner, link["id"])
    assert status["last_error"] == "" and status["counts"]["waiting"], status
    assert not [p for p in root.rglob(f"*{suffix}")]
    portal[0] = False
    _sync(owner, link["id"])
    files = [p for p in root.rglob(f"*{suffix}") if ".gamma" not in p.parts]
    assert len(files) == 1 and files[0].read_bytes() == data
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def _token_of(token) -> dict:
    return {"token": token["token"], "token_id": token["id"]}


@pytest.mark.parametrize("answer", [(413, HTML_413), (422, b'{"detail": "bad op"}')])
def test_a_plan_the_server_refuses_is_sent_once_until_the_file_changes(owner, disk, faulty, monkeypatch, answer):
    """A plan refused (a proxy's 413, any 4xx) was read, planned and
    sent again whole at every tick. It is the note's problem until the note
    changes, as a refused PDF is, and a 413 says it was too large."""
    url, rules, _log = faulty
    folder = make_folder(owner, f"VH refused plan {answer[0]}")
    root = disk / f"VH refused plan {answer[0]}"
    link = _remote_vault(owner, url, folder, root, _token("write"), push="everything")
    _put(root / "N.md", "one\n")
    _sync(owner, link["id"])
    page = _page_of(link, "N.md")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: folder_links.run_link(link_id, **kw))
    monkeypatch.setattr(folder_links, "REMOTE_POLL_S", 0)
    sent, refuse = [], [True]

    def refused(m, p, b):
        if m == "POST" and p == f"/api/pages/{page}/plan":
            sent.append(len(b))
            return answer if refuse[0] else None
        return None
    rules.append(refused)
    _put(root / "N.md", "one\n\ntwo " + "x" * 2000 + "\n")
    for _ in range(4):
        folder_links.tick()
    assert len(sent) == 1, sent
    problem = vault_store.files(link["id"])["N.md"]["problem"]
    assert ("too large" in problem) if answer[0] == 413 else problem == "bad op"
    refuse[0] = False
    _put(root / "N.md", "one\n\ntwo, shorter\n")
    status = _sync(owner, link["id"])
    assert len(sent) == 2 and _texts(page) == ["one", "two, shorter"], status
    assert vault_store.files(link["id"])["N.md"]["problem"] == ""
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("status_code", [403, 500])
def test_a_token_the_server_echoes_is_in_no_answer_when_a_vault_is_made(owner, disk, faulty, caplog, status_code):
    """The refusal of ``whoami`` copied the server's detail, the
    token it echoed in it, into the 400 of ``POST /api/folder-links``."""
    url, rules, _log = faulty
    token = _token("write")
    rules.append(lambda m, p, b: (status_code, json.dumps({"detail": f"refused Bearer {token['token']}"}).encode())
                 if p.startswith("/api/sync/") else None)
    caplog.set_level("INFO")
    r = owner.post("/api/folder-links", json={"folder": make_folder(owner, f"VH echo {status_code}"),
                                              "path": str(disk / f"VH echo {status_code}"), "remote_url": url,
                                              "push": "everything", **_token_of(token)})
    assert r.status_code == 400 and "refused Bearer" in r.text, r.text
    assert token["token"] not in r.text + caplog.text


@pytest.mark.parametrize("status_code", [403, 500, 409])
def test_a_token_the_server_echoes_is_in_no_status_nor_log_of_a_tick(owner, disk, faulty, monkeypatch, caplog,
                                                                    status_code):
    """The tick's question to the change feed failed with the
    server's detail as it came, the token in it, into the status and a
    WARNING."""
    url, rules, _log = faulty
    folder = make_folder(owner, f"VH tick echo {status_code}")
    token = _token("write")
    link = _remote_vault(owner, url, folder, disk / f"VH tick echo {status_code}", token, push="everything")
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: folder_links.run_link(link_id, **kw))
    monkeypatch.setattr(folder_links, "REMOTE_POLL_S", 0)
    rules.append(lambda m, p, b: (status_code, json.dumps({"detail": f"no such session Bearer {token['token']}"}).encode())
                 if p.startswith("/api/sync/changes") else None)
    caplog.set_level("INFO")
    folder_links.tick()
    shown = owner.get(f"/api/folder-links/{link['id']}").text
    assert "no such session" in shown and token["token"] not in shown + caplog.text
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_token_echoed_in_a_detail_that_is_no_string_is_in_no_status_nor_log(owner, disk, faulty, caplog):
    """A detail that is a list (a validation error echoing the
    request's headers) went into the file's problem, the status and the log
    as it was."""
    url, rules, _log = faulty
    folder = make_folder(owner, "VH list echo")
    root = disk / "VH list echo"
    token = _token("write")
    link = _remote_vault(owner, url, folder, root, token, push="everything")
    _put(root / "N.md", "one\n")
    _sync(owner, link["id"])
    detail = [{"msg": "bad", "headers": {"authorization": f"Bearer {token['token']}"}}]
    rules.append(lambda m, p, b: (400, json.dumps({"detail": detail}).encode()) if m == "POST" and p.endswith("/plan")
                 else None)
    caplog.set_level("INFO")
    _put(root / "N.md", "one\n\ntwo\n")
    status = _sync(owner, link["id"])
    shown = owner.get(f"/api/folder-links/{link['id']}").text
    assert status["kept"] and "authorization" in status["kept"][0]["why"]
    assert token["token"] not in shown + caplog.text + json.dumps(vault_store.files(link["id"])["N.md"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.parametrize("push", ["everything", "pdfs"])
def test_a_pushing_vault_whose_token_was_revoked_keeps_saying_so_between_ticks(owner, disk, over_http, monkeypatch, push):
    """The tick's question to the change feed failed and wrote why,
    then the round of push alone it ran anyway wrote a clean status over it:
    no pull ran and nothing said so. The link rests as a failed round does."""
    folder = make_folder(owner, f"VH revoked {push}")
    token = _token("write")
    link = _remote_vault(owner, over_http, folder, disk / f"VH revoked {push}", token, push=push)
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: folder_links.run_link(link_id, **kw))
    monkeypatch.setattr(folder_links, "REMOTE_POLL_S", 0)
    owner.delete(f"/api/integrations/tokens/{token['id']}").raise_for_status()
    for _ in range(3):
        folder_links.tick()
        shown = owner.get(f"/api/folder-links/{link['id']}").json()["status"]
        assert "refused" in shown["last_error"] and shown["failed_at"], shown
    assert folder_links.due(folder_links.get_link(link["id"])) == ""       # RETRY_S
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_a_new_token_given_to_a_vault_replaces_the_revoked_one_or_is_refused(owner, disk, over_http):
    """``PATCH {token, token_id}`` answered 200 and kept nothing,
    so a pull-only vault whose token was revoked could not be given another;
    and a token that may only read, given to a vault that pushes everything,
    answered 200 too."""
    folder = make_folder(owner, "VH new token")
    old = _token("read")
    link = _remote_vault(owner, over_http, folder, disk / "VH new token", old)
    owner.delete(f"/api/integrations/tokens/{old['id']}").raise_for_status()
    assert "refused" in _sync(owner, link["id"])["last_error"]
    r = owner.patch(f"/api/folder-links/{link['id']}", json=_token_of(old))
    assert r.status_code == 400 and folder_links.get_link(link["id"])["token_id"] == old["id"]
    new = _token("read")
    r = owner.patch(f"/api/folder-links/{link['id']}", json=_token_of(new))
    assert r.status_code == 200 and r.json()["token_id"] == new["id"], r.text
    assert _sync(owner, link["id"])["last_error"] == ""
    newer = _token("read")
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"push": "off", **_token_of(newer)})
    assert r.status_code == 200 and r.json()["token_id"] == newer["id"]
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()

    pushing = _remote_vault(owner, over_http, make_folder(owner, "VH new token pushing"), disk / "VH new token pushing",
                            _token("write"), push="everything")
    reader = _token("read")
    r = owner.patch(f"/api/folder-links/{pushing['id']}", json=_token_of(reader))
    assert r.status_code == 400 and "may write" in r.json()["detail"]
    assert folder_links.get_link(pushing["id"], with_token=True)["token"] != reader["token"]
    owner.delete(f"/api/folder-links/{pushing['id']}").raise_for_status()


@pytest.mark.parametrize("honoured", [True, False])
def test_a_vault_reads_its_folders_pages_whether_the_library_lists_them_or_not(owner, disk, over_http, monkeypatch,
                                                                              honoured):
    """The listing a vault reads is its folder's (``?within=``), so a work
    folder's file the library does not list is among its pages; a server from
    before work folders, which knows no such parameter and keeps no such
    file, lists the library, read the same."""
    folder = make_folder(owner, f"VH within {honoured}")
    make_page(owner, f"VH elsewhere {honoured}")
    mark_work_folder(owner, folder)
    vault_sync.LocalRemote(workspace_of(OWNER), folder, account_of(OWNER)).create(
        new_block_id(), f"VH file {honoured}", work_props(folder, "file.md"),
        [{"id": new_block_id(), "content": "a file's words", "properties": {}, "children": []}])
    real, asked = gamma_sync.Server._send, []

    def send(srv, method, path, headers, body=None):
        if path.startswith("/api/blocks/root/children"):
            asked.append(path)
            path = path if honoured else "/api/blocks/root/children"
        return real(srv, method, path, headers, body)
    monkeypatch.setattr(gamma_sync.Server, "_send", send)
    root = disk / f"VH within {honoured}"
    link = _remote_vault(owner, over_http, folder, root, _token())
    assert asked and set(asked) == {f"/api/blocks/root/children?within={folder}"}
    assert sorted(p.name for p in root.glob("*.md")) == ([f"VH file {honoured}.md"] if honoured else [])
    if honoured:
        assert (root / f"VH file {honoured}.md").read_text(encoding="utf-8") == "a file's words\n"
    _quiet(owner, link["id"])
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_each_request_names_the_vaults_client(owner, disk, over_http, monkeypatch):
    """A write of a route whose body takes no op client (a page made, its
    blocks, the trash) is the vault's to the work-folder rule there
    (gamma/auth.py ``writer_client``): each request says so in
    ``X-Gamma-Client``."""
    real, sent = gamma_sync.Server._send, []

    def send(srv, method, path, headers, body=None):
        sent.append((method, path.split("?")[0], headers.get(CLIENT_HEADER)))
        return real(srv, method, path, headers, body)
    monkeypatch.setattr(gamma_sync.Server, "_send", send)
    folder = make_folder(owner, "VH client")
    root = disk / "VH client"
    link = _remote_vault(owner, over_http, folder, root, _token("write"), push="everything")
    _put(root / "Made.md", "made over http\n")
    _sync(owner, link["id"])
    page = _page_of(link, "Made.md")
    (root / "Made.md").unlink()
    _sync(owner, link["id"])
    _sync(owner, link["id"])
    assert owner.get(f"/api/blocks/{page}").json()["trashed"]
    writes = {(m, p) for m, p, _c in sent if m != "GET"}
    assert {("POST", "/api/pages"), ("PUT", f"/api/blocks/{page}/children"), ("DELETE", f"/api/blocks/{page}")} <= writes
    assert {c for m, p, c in sent if m != "GET" and not p.startswith("/api/uploads")} == {vault_sync.CLIENT}
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()

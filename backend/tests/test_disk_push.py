"""PDFs that change on disk sent back to Gamma (gamma/gamma_sync.py ``Push``,
gamma/folder_links.py, PUT /pages/{id}/attachment's ``keep_old_days``): a
kept folder that pushes, a page that follows a file, on this server and on
another one read with a token, and the rules that keep a push safe — only
whole files, only bytes Gamma does not show, never over a change made in
Gamma meanwhile, and the file on disk never touched."""

import io
import json
import os
import sys
import threading
import time

import pymupdf
import pytest

from conftest import account_of, login, make_folder, make_page, make_user, workspace_of
from gamma import config, folder_links, gamma_sync, upload_gc
from gamma.integrations import create_token
from test_folder_links import _create, _sync, disk, over_http  # noqa: F401 — the disk and over_http fixtures

BOTH = "changed here and in Gamma; --force takes Gamma's"


@pytest.fixture(scope="module")
def owner(client):
    make_user("dp_owner", "pw")
    return login("dp_owner", "pw")


@pytest.fixture
def retained(monkeypatch):
    """The short lives a replacement asks of the orphan purge
    (``upload_gc.retain``), recorded instead of kept."""
    calls = []
    monkeypatch.setattr(upload_gc, "retain", lambda ws, name, keep_s: calls.append((ws, name, keep_s)))
    return calls


def _pdf(text: str) -> bytes:
    """A real one-page PDF that reads ``text``."""
    doc = pymupdf.open()
    doc.new_page().insert_text((72, 72), text)
    return doc.tobytes()


def _upload(c, data: bytes) -> str:
    r = c.post("/api/uploads", files={"file": ("paper.pdf", data, "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["doc_id"]


def _age(path, seconds: float = 10) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


def _write(path, data: bytes, age: float = 10) -> None:
    """``data`` written at ``path`` as a build that finished ``age`` seconds
    ago (two writes in a row take two ages: a file of the same size and
    time within a second counts as unchanged)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    _age(path, age)


def _props(c, page_id) -> dict:
    return c.get(f"/api/blocks/{page_id}").json()["properties"]


def _entry(dest, path) -> dict:
    return json.loads((dest / ".gamma-sync.json").read_text(encoding="utf-8"))["files"][path]


def _due(link_id) -> bool:
    return folder_links.due(folder_links.get_link(link_id))


def _kept_paper(c, disk, name, *, push=True):
    """A folder ``name`` holding one paper, kept on disk (sending PDFs back
    with ``push``) after its first round: ``(link, page id, the paper's
    file, its doc_id)``."""
    folder = make_folder(c, name)
    doc = _upload(c, _pdf(f"{name} v1"))
    page = make_page(c, f"{name} paper", {"doc_id": doc, "original_filename": "v1.pdf", "folders": [folder]})
    link = _create(c, folder=folder, path=str(disk / name), push=push)
    status = _sync(c, link["id"])
    assert status["counts"]["added"] == 2 and status["counts"]["sent"] == 0, status
    return link, page["id"], disk / name / f"{name} paper.pdf", doc


# --- the engine's pieces -------------------------------------------------------

def test_a_file_s_identity_is_the_one_gamma_gives_its_bytes(tmp_path):
    from gamma import storage

    data = _pdf("identity")
    (tmp_path / "a.pdf").write_bytes(data)
    assert gamma_sync.DOC_CHARS == storage.DIGEST_CHARS
    assert gamma_sync.doc_of(tmp_path / "a.pdf") == storage.content_digest(data)


def test_a_file_is_whole_once_it_rests_reads_and_ends_as_a_pdf(tmp_path):
    f = tmp_path / "main.pdf"
    f.write_bytes(_pdf("whole"))
    assert gamma_sync.settled(f) == "still being written"            # written a moment ago
    _age(f)
    assert gamma_sync.settled(f) == ""
    _write(f, b"%PDF-1.7\n1 0 obj\n<< >>\n" + b"x" * 3000)              # cut off before its end
    assert gamma_sync.settled(f) == "not a whole PDF yet"
    assert gamma_sync.settled(tmp_path / "gone.pdf") == "the file is not there"


class _Sink:
    """A sink for the engine's own tests: the page shows ``doc``, and
    ``store`` runs ``on_store`` (the file's path) before it answers."""

    def __init__(self, doc="0" * 24, on_store=None):
        self.doc, self.on_store, self.stores = doc, on_store, 0

    def current(self, page_id):
        return {"doc_id": self.doc, "filename": "main.pdf", "title": "T"}

    def store(self, path):
        self.stores += 1
        if self.on_store:
            return self.on_store(path)
        return gamma_sync.doc_of(path)

    def replace(self, page_id, doc_id, filename):
        self.doc = doc_id
        return {"content": "T"}


def _push(f, entry, sink):
    push = gamma_sync.Push([(f.name, f, entry)], sink, mode="file", say=lambda *_: None)
    push.run()
    return push


def test_a_file_that_changes_while_it_is_sent_waits_and_is_looked_at_again(tmp_path, monkeypatch):
    f = tmp_path / "main.pdf"
    _write(f, _pdf("a long build " * 40))
    smaller = _pdf("the next build")
    # over HTTP: the server reads slowly, and latexmk writes a smaller build meanwhile
    monkeypatch.setattr(gamma_sync, "CHUNK", 16)
    server = gamma_sync.Server("http://gamma.test", "t")

    def slow(method, path, headers, body=None):
        if method == "GET":   # the page, showing another file
            page = {"parent_id": "root", "content": "T", "properties": {"doc_id": "0" * 24}}
            return 200, {}, io.BytesIO(json.dumps(page).encode())
        for n, _chunk in enumerate(body):
            if n == 1:
                _write(f, smaller, age=20)
        return 200, {}, io.BytesIO(b'{"doc_id": "never"}')

    server.open_ = slow
    entry = {"page": "p"}
    push = _push(f, entry, gamma_sync.RemoteSink(server))
    assert push.counts == {"sent": 0, "waiting": 1, "kept": 0}
    assert push.waiting == [("main.pdf", "the file changed while it was sent")]
    assert "seen" not in entry and gamma_sync.moved(f, entry)       # the loop starts a pass for the new build

    # in-process: whatever the sink makes of the change (here a refusal), the file waits
    def refused(path):
        _write(path, _pdf("yet another build"), age=30)
        raise gamma_sync.SyncError("not a PDF")

    entry = {"page": "p"}
    push = _push(f, entry, _Sink(on_store=refused))
    assert push.counts == {"sent": 0, "waiting": 1, "kept": 0} and "seen" not in entry
    # a refusal of a file that stood still is kept, until the file moves
    entry = {"page": "p"}
    push = _push(f, entry, _Sink(on_store=lambda path: (_ for _ in ()).throw(gamma_sync.SyncError("too large"))))
    assert push.kept == [("main.pdf", "too large")] and entry["seen"] == gamma_sync.file_stat(f)
    assert gamma_sync.moved(f, entry) is False


def test_a_kept_file_is_sent_again_by_the_next_pass_that_runs(tmp_path):
    f = tmp_path / "main.pdf"
    _write(f, _pdf("kept"))
    sink = _Sink(on_store=lambda path: (_ for _ in ()).throw(gamma_sync.SyncError("the quota")))
    entry = {"page": "p"}
    _push(f, entry, sink)
    assert sink.stores == 1 and gamma_sync.moved(f, entry) is False   # no loop starts a pass for it...
    push = _push(f, entry, sink)                                       # ...but a pass that runs tries again
    assert sink.stores == 2 and push.kept == [("main.pdf", "the quota")]


def test_a_file_at_rest_that_is_no_whole_pdf_starts_no_pass_until_it_is_written_again(tmp_path):
    f = tmp_path / "main.pdf"
    _write(f, b"%PDF-1.7\n% a download cut short" + b"x" * 3000)
    entry = {"page": "p"}
    push = _push(f, entry, _Sink())
    assert push.waiting == [("main.pdf", "not a whole PDF yet")] and entry["seen"] == gamma_sync.file_stat(f)
    assert gamma_sync.moved(f, entry) is False
    _write(f, _pdf("whole at last"), age=20)
    assert gamma_sync.moved(f, entry) is True
    push = _push(f, entry, _Sink())
    assert push.counts["sent"] == 1 and "seen" not in entry
    # a file still being written, or locked, is looked at again at every pass
    f.write_bytes(_pdf("written a moment ago"))
    entry = {"page": "p"}
    push = _push(f, entry, _Sink())
    assert push.waiting == [("main.pdf", "still being written")] and "seen" not in entry


def test_push_never_sleeps_longer_than_a_file_takes_to_settle(tmp_path, monkeypatch):
    f = tmp_path / "main.pdf"
    f.write_bytes(_pdf("from the future"))
    then = time.time() + 50 * 86400                                    # a clock set wrong
    os.utime(f, (then, then))
    slept = []
    monkeypatch.setattr(gamma_sync.time, "sleep", slept.append)
    gamma_sync._rested(f)
    assert slept and max(slept) <= gamma_sync.SETTLE_S


def test_an_answer_that_is_no_json_is_a_sync_error(tmp_path):
    server = gamma_sync.Server("http://gamma.test", "t")
    server.open_ = lambda method, path, headers, body=None: (200, {}, io.BytesIO(b"<html>Sign in</html>"))
    with pytest.raises(gamma_sync.SyncError, match="did not answer JSON"):
        server.get_json("/api/sync/whoami")
    f = tmp_path / "main.pdf"
    _write(f, _pdf("behind a portal"))
    entry = {"page": "p"}
    push = _push(f, entry, gamma_sync.RemoteSink(server))
    assert push.kept == [("main.pdf", "/api/blocks/p: the server did not answer JSON")]


# --- a kept folder that sends PDFs back -----------------------------------------

def test_a_pdf_changed_in_a_kept_folder_is_sent_and_the_pull_leaves_it(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP sent")
    assert link["push"] is True
    assert json.loads((disk / "DP sent" / ".gamma-sync.json").read_text(encoding="utf-8"))["push"] is True
    assert _due(link["id"]) is False

    _write(paper, _pdf("DP sent v2, annotated elsewhere"))
    assert _due(link["id"]) is True                                     # the PDF moved on disk
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 1 and status["counts"]["kept"] == 0 and status["last_error"] == ""
    props = _props(owner, page_id)
    assert props["doc_id"] == gamma_sync.doc_of(paper) != v1
    assert props["original_filename"] == "v1.pdf"                       # the kept name is a title, not a file name
    assert retained == [(workspace_of("dp_owner"), f"{v1}.pdf", 86400)]  # the build before lives a day
    entry = _entry(disk / "DP sent", "DP sent paper.pdf")
    assert entry["doc"] == props["doc_id"] and "seen" not in entry
    assert _due(link["id"]) is False

    # the next pull leaves the sent file alone
    status = _sync(owner, link["id"])
    assert status["counts"]["kept"] == 0 and status["counts"]["sent"] == 0
    assert _entry(disk / "DP sent", "DP sent paper.pdf") == entry
    assert gamma_sync.doc_of(paper) == props["doc_id"]


def test_the_same_bytes_touched_send_nothing(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP touched")
    _age(paper, 60)
    assert _due(link["id"]) is True
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 0 and status["counts"]["kept"] == 0
    assert _props(owner, page_id)["doc_id"] == v1 and retained == []
    assert _entry(disk / "DP touched", "DP touched paper.pdf")["mtime"] == round(paper.stat().st_mtime, 3)
    assert _due(link["id"]) is False


def test_a_pdf_changed_here_and_in_gamma_is_kept_until_a_forced_sync_takes_gamma_s(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP both")
    v3_bytes = _pdf("DP both v3, replaced in Gamma")
    v3 = _upload(owner, v3_bytes)
    assert owner.put(f"/api/pages/{page_id}/attachment", json={"doc_id": v3}).status_code == 200
    mine = _pdf("DP both v4, changed on disk")
    _write(paper, mine)

    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 0 and status["kept"] == [{"path": "DP both paper.pdf", "why": BOTH, "force": True}]
    assert _props(owner, page_id)["doc_id"] == v3 and paper.read_bytes() == mine
    assert _due(link["id"]) is False                                    # not tried again until the file moves
    _age(paper, 30)
    assert _due(link["id"]) is True

    status = _sync(owner, link["id"], force=1)
    assert status["counts"]["kept"] == 0 and paper.read_bytes() == v3_bytes
    assert _props(owner, page_id)["doc_id"] == v3
    assert "seen" not in _entry(disk / "DP both", "DP both paper.pdf") and _due(link["id"]) is False


def test_without_push_a_changed_pdf_stays_on_disk_as_before(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP off", push=False)
    assert link["push"] is False
    mine = _pdf("DP off, mine")
    _write(paper, mine)
    assert _due(link["id"]) is False                                    # the disk is not looked at
    assert _sync(owner, link["id"])["counts"]["sent"] == 0
    assert _props(owner, page_id)["doc_id"] == v1
    v2 = _upload(owner, _pdf("DP off v2"))
    owner.put(f"/api/pages/{page_id}/attachment", json={"doc_id": v2}).raise_for_status()
    status = _sync(owner, link["id"])
    assert status["kept"] == [{"path": "DP off paper.pdf", "why": "changed on disk; --force replaces it", "force": True}]
    assert paper.read_bytes() == mine and retained == []


def test_a_pdf_still_being_written_waits_for_a_later_round(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP waits")
    paper.write_bytes(b"%PDF-1.7\n% half of a build")
    _age(paper)
    status = _sync(owner, link["id"])
    assert status["counts"]["waiting"] == 1 and status["counts"]["sent"] == 0 and status["last_error"] == ""
    assert _due(link["id"]) is False                                    # at rest and no whole PDF: not every tick
    paper.write_bytes(_pdf("DP waits v2, the whole build"))              # whole, but written a moment ago
    status = _sync(owner, link["id"])
    assert status["counts"]["waiting"] == 1 and _props(owner, page_id)["doc_id"] == v1
    assert _due(link["id"]) is True                                     # waiting is asked again at every tick
    _age(paper)
    folder_links.tick()
    assert _props(owner, page_id)["doc_id"] == gamma_sync.doc_of(paper)
    assert folder_links.get_link(link["id"])["status"]["counts"]["sent"] == 1


def test_bytes_another_page_carries_are_kept_with_the_server_s_reason(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP taken")
    other_bytes = _pdf("DP taken, another paper")
    make_page(owner, "DP another paper", {"doc_id": _upload(owner, other_bytes)})
    _write(paper, other_bytes)
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 0
    # not sent: Gamma's file did not change, so a forced round has nothing to replace
    assert status["kept"] == [{"path": "DP taken paper.pdf", "why": "attachment belongs to another page", "force": False}]
    assert _props(owner, page_id)["doc_id"] == v1 and _due(link["id"]) is False
    status = _sync(owner, link["id"], force=1)
    assert status["kept"] == [{"path": "DP taken paper.pdf", "why": "attachment belongs to another page", "force": False}]
    assert paper.read_bytes() == other_bytes


# --- a page that follows a file ---------------------------------------------------

def test_a_page_follows_a_file_on_this_computer(owner, disk, retained, monkeypatch):
    v1 = _upload(owner, _pdf("DP draft v1"))
    page = make_page(owner, "DP draft", {"doc_id": v1, "original_filename": "draft.pdf"})
    build = disk / "tex" / "build" / "main.pdf"
    _write(build, _pdf("DP draft v2"))

    link = _create(owner, kind="file", page=page["id"], path=str(build))
    assert link["kind"] == "file" and link["page_id"] == page["id"] and link["folder_id"] == ""
    assert link["title"] == "DP draft" and link["push"] is True and link["dest"] == str(build.resolve())
    assert "state" not in link and "token" not in link
    listed = [l for l in owner.get("/api/folder-links").json()["links"] if l["id"] == link["id"]]
    assert listed[0]["title"] == "DP draft" and "state" not in listed[0]

    status = _sync(owner, link["id"])
    assert status["counts"] == {"sent": 1, "waiting": 0, "kept": 0} and status["last_error"] == ""
    assert status["last_sent"] and status["title"] == "DP draft" and status["waiting"] == ""
    props = _props(owner, page["id"])
    assert props["doc_id"] == gamma_sync.doc_of(build) and props["original_filename"] == "draft.pdf"
    assert retained == [(workspace_of("dp_owner"), f"{v1}.pdf", 86400)]
    assert not (build.parent / ".gamma-sync.json").exists()           # no state file: the row holds it
    assert _due(link["id"]) is False

    # the next build is sent at the next tick
    _write(build, _pdf("DP draft v3"), age=5)
    assert _due(link["id"]) is True
    folder_links.tick()
    assert _props(owner, page["id"])["doc_id"] == gamma_sync.doc_of(build)
    assert _due(link["id"]) is False

    # a file that is gone waits for it, without a failure
    v3 = build.read_bytes()
    build.unlink()
    status = _sync(owner, link["id"])
    assert status["waiting"] == "the file is not there" and status["last_error"] == "" and status["last_sent"]
    _write(build, v3)                                                  # back, as Gamma has it: nothing to send
    assert _due(link["id"]) is True
    status = _sync(owner, link["id"])
    assert status["counts"] == {"sent": 0, "waiting": 0, "kept": 0} and status["waiting"] == ""

    # notes and push are a folder's
    assert owner.patch(f"/api/folder-links/{link['id']}", json={"notes": False}).status_code == 400
    assert owner.patch(f"/api/folder-links/{link['id']}", json={"push": False}).status_code == 400

    # paused, the tick leaves it alone; resumed, a round runs
    r = owner.patch(f"/api/folder-links/{link['id']}", json={"paused": True})
    assert r.status_code == 200 and r.json()["status"]["paused_at"]
    _write(build, _pdf("DP draft v4"), age=20)
    assert _due(link["id"]) is False
    started = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: started.append(link_id))
    assert owner.patch(f"/api/folder-links/{link['id']}", json={"paused": False}).status_code == 200
    assert started == [link["id"]] and _due(link["id"]) is True

    # a page that is gone fails the link, which waits before it tries again
    owner.delete(f"/api/blocks/{page['id']}").raise_for_status()
    status = _sync(owner, link["id"], force=1)
    assert status["last_error"] == "the page is no longer in Gamma" and status["failed_at"]
    assert _due(link["id"]) is False

    # removing the link never touches the file
    kept = build.read_bytes()
    owner.delete(f"/api/folder-links/{link['id']}", params={"remove_files": 1}).raise_for_status()
    assert build.read_bytes() == kept
    assert folder_links.get_link(link["id"]) is None


def test_a_followed_file_must_be_a_pdf_that_is_there_outside_the_data_directory(owner, disk):
    page = make_page(owner, "DP paths")
    (disk / "dir.pdf").mkdir(parents=True)
    (disk / "notes.txt").write_text("not a pdf", encoding="utf-8")
    _write(disk / "fine.pdf", _pdf("DP paths"))
    inside = config.DATA_DIR / "dp-inside.pdf"
    inside.write_bytes(_pdf("DP inside"))
    try:
        for path, why in (("main.pdf", "full path"), ("build/main.pdf", "full path"), (str(disk / "notes.txt"), "PDF"),
                          (str(disk / "dir.pdf"), "no file"), (str(disk / "missing.pdf"), "no file"),
                          (str(inside), "data directory")):
            r = owner.post("/api/folder-links", json={"kind": "file", "page": page["id"], "path": path})
            assert r.status_code == 400 and why in r.json()["detail"], (path, r.text)
    finally:
        inside.unlink()
    r = owner.post("/api/folder-links", json={"kind": "file", "page": "no-such-page", "path": str(disk / "fine.pdf")})
    assert r.status_code == 400 and "page does not exist" in r.json()["detail"]
    r = owner.post("/api/folder-links", json={"kind": "file", "path": str(disk / "fine.pdf")})
    assert r.status_code == 400
    link = _create(owner, kind="file", page=page["id"], path=str(disk / "fine.pdf"))
    # one file feeds one page, under every spelling the filesystem takes for it
    # (FINE.pdf is that file only where names ignore case: Windows, not CI's Linux)
    for name in ("fine.pdf", "FINE.pdf") if (disk / "FINE.pdf").is_file() else ("fine.pdf",):
        r = owner.post("/api/folder-links", json={"kind": "file", "page": page["id"], "path": str(disk / name)})
        assert r.status_code == 400 and "Another link" in r.json()["detail"], name
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows spellings of a path")
def test_another_spelling_of_the_data_directory_is_refused_too(owner, disk):
    page = make_page(owner, "DP spellings")
    inside = config.DATA_DIR.resolve() / "dp-spelled.pdf"
    inside.write_bytes(_pdf("DP spelled"))
    try:
        for path in ("\\\\?\\" + str(inside), "//?/" + str(inside).replace("\\", "/")):
            r = owner.post("/api/folder-links", json={"kind": "file", "page": page["id"], "path": path})
            assert r.status_code == 400 and "data directory" in r.json()["detail"], (path, r.text)
            with pytest.raises(folder_links.LinkError, match="data directory"):
                folder_links.clean_path(path[:-len(inside.name) - 1] + "\\kept")
    finally:
        inside.unlink()
    assert folder_links.clean_path(str(disk / "fine")) == str((disk / "fine").resolve())


def test_files_and_folders_are_counted_apart(owner, disk, monkeypatch):
    ws = workspace_of("dp_owner")
    links = [l for l in owner.get("/api/folder-links").json()["links"] if l["workspace_id"] == ws]
    monkeypatch.setattr(folder_links, "MAX_FILE_LINKS", sum(l["kind"] == "file" for l in links) + 1)
    monkeypatch.setattr(folder_links, "MAX_LINKS", sum(l["kind"] == "folder" for l in links) + 1)
    page = make_page(owner, "DP limits")
    _write(disk / "one.pdf", _pdf("DP one"))
    _write(disk / "two.pdf", _pdf("DP two"))
    first = _create(owner, kind="file", page=page["id"], path=str(disk / "one.pdf"))
    r = owner.post("/api/folder-links", json={"kind": "file", "page": page["id"], "path": str(disk / "two.pdf")})
    assert r.status_code == 400 and "files on disk" in r.json()["detail"]
    folder = _create(owner, folder=make_folder(owner, "DP limits"), path=str(disk / "Limits"))   # a folder still fits
    r = owner.post("/api/folder-links", json={"folder": make_folder(owner, "DP limits"), "path": str(disk / "Limits 2")})
    assert r.status_code == 400 and "folders on disk" in r.json()["detail"]
    for l in (first, folder):
        owner.delete(f"/api/folder-links/{l['id']}", params={"remove_files": 1}).raise_for_status()


def test_a_push_stops_once_the_account_may_no_longer_edit(owner, disk, retained, monkeypatch):
    v1 = _upload(owner, _pdf("DP role v1"))
    page = make_page(owner, "DP role", {"doc_id": v1})
    build = disk / "role.pdf"
    _write(build, _pdf("DP role v2"))
    link = _create(owner, kind="file", page=page["id"], path=str(build))
    role_of = folder_links.workspaces.role_of
    monkeypatch.setattr(folder_links.workspaces, "role_of", lambda ws, user_id: "viewer")
    status = folder_links.run_link(link["id"])
    assert status["last_error"] == "you may no longer edit this workspace" and status["counts"]["kept"] == 1
    monkeypatch.setattr(folder_links.workspaces, "role_of", role_of)
    assert _props(owner, page["id"])["doc_id"] == v1
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()


def test_turning_push_on_leaves_the_state_file_to_the_rounds(owner, disk, retained):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP toggled", push=False)
    state = disk / "DP toggled" / ".gamma-sync.json"
    before = state.read_bytes()
    lock = folder_links._lock(link["id"])
    done = []
    with lock:                                                          # a round is running, and saves its files map
        t = threading.Thread(target=lambda: done.append(folder_links.set_push(link["id"], True)))
        t.start()
        t.join(10)
        assert done and done[0]["push"] is True                         # not held up by the round
    assert state.read_bytes() == before                                 # nothing for the round's save to overwrite
    _write(paper, _pdf("DP toggled v2"))
    assert _due(link["id"]) is True
    status = _sync(owner, link["id"])
    assert status["counts"]["sent"] == 1 and json.loads(state.read_text(encoding="utf-8"))["push"] is True


def test_a_round_that_fails_in_an_unexpected_way_says_so(owner, disk, monkeypatch):
    link, page_id, paper, v1 = _kept_paper(owner, disk, "DP unexpected", push=False)

    def broken(*args, **kw):
        raise RuntimeError("something nobody planned for")

    monkeypatch.setattr(folder_links, "_folder_round", broken)
    status = folder_links.run_link(link["id"])
    assert status["running"] is False and "something nobody planned for" in status["last_error"]
    assert folder_links.get_link(link["id"])["status"]["running"] is False


def test_the_replacement_keeps_the_old_file_short_only_when_asked_and_only_when_it_changed(owner, retained):
    v1, v2 = _upload(owner, _pdf("DP keep v1")), _upload(owner, _pdf("DP keep v2"))
    page = make_page(owner, "DP keep", {"doc_id": v1})
    for days in (0, 31):
        r = owner.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": v2, "keep_old_days": days})
        assert r.status_code == 422
    assert owner.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": v2}).status_code == 200
    assert retained == []                                              # not asked: the usual 30 days
    assert owner.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": v2, "keep_old_days": 3}).status_code == 200
    assert retained == []                                              # the same file: nothing let go of
    r = owner.put(f"/api/pages/{page['id']}/attachment", json={"doc_id": v1, "keep_old_days": 3})
    assert r.status_code == 200 and retained == [(workspace_of("dp_owner"), f"{v2}.pdf", 3 * 86400)]


# --- on another server ------------------------------------------------------------

def test_a_file_or_a_folder_on_another_server_sends_with_a_token_that_may_write(owner, disk, over_http, retained):
    ws = workspace_of("dp_owner")
    read = create_token(account_of("dp_owner"), ws, "dp-read", 1)
    write = create_token(account_of("dp_owner"), ws, "dp-write", 1, scope="write")
    v1 = _upload(owner, _pdf("DP far v1"))
    page = make_page(owner, "DP far draft", {"doc_id": v1})
    build = disk / "far" / "main.pdf"
    _write(build, _pdf("DP far v2"))
    body = {"kind": "file", "page": page["id"], "path": str(build), "remote_url": over_http, "workspace": ws}

    r = owner.post("/api/folder-links", json={**body, "token": read["token"], "token_id": read["id"]})
    assert r.status_code == 400 and "token that may write" in r.json()["detail"]
    r = owner.post("/api/folder-links", json={**body, "page": "no-such-page", "token": write["token"]})
    assert r.status_code == 400 and "does not exist on the other server" in r.json()["detail"]
    link = _create(owner, **body, token=write["token"], token_id=write["id"])
    assert link["remote_url"] == over_http and link["title"] == "DP far draft" and link["token_id"] == write["id"]
    status = _sync(owner, link["id"])                                  # an upload in one part, then the PUT
    assert status["counts"]["sent"] == 1 and status["last_error"] == ""
    assert _props(owner, page["id"])["doc_id"] == gamma_sync.doc_of(build)
    assert retained == [(ws, f"{v1}.pdf", 86400)]
    owner.delete(f"/api/folder-links/{link['id']}").raise_for_status()

    # a folder read with a read token sends nothing back until push is on with a token that may write
    folder = make_folder(owner, "DP far folder")
    f1 = _upload(owner, _pdf("DP far folder v1"))
    paper = make_page(owner, "DP far paper", {"doc_id": f1, "folders": [folder]})
    kept = _create(owner, folder=folder, path=str(disk / "Far folder"), remote_url=over_http, token=read["token"],
                   token_id=read["id"])
    assert _sync(owner, kept["id"])["counts"]["added"] == 2
    r = owner.post("/api/folder-links", json={"folder": folder, "path": str(disk / "Far 2"), "remote_url": over_http,
                                              "token": read["token"], "push": True})
    assert r.status_code == 400 and "may write" in r.json()["detail"]
    r = owner.patch(f"/api/folder-links/{kept['id']}", json={"push": True})
    assert r.status_code == 400 and "may write" in r.json()["detail"]
    other = create_token(account_of("dp_owner"), ws, "dp-write-2", 1, scope="write")
    r = owner.patch(f"/api/folder-links/{kept['id']}", json={"push": True, "token": other["token"], "token_id": other["id"]})
    assert r.status_code == 200 and r.json()["push"] is True and r.json()["token_id"] == other["id"]
    assert folder_links.get_link(kept["id"], with_token=True)["token"] == other["token"]   # swapped in for the read one

    target = disk / "Far folder" / "DP far paper.pdf"
    _write(target, _pdf("DP far folder v2"))
    status = _sync(owner, kept["id"])
    assert status["counts"]["sent"] == 1 and status["counts"]["kept"] == 0
    assert _props(owner, paper["id"])["doc_id"] == gamma_sync.doc_of(target)
    r = owner.patch(f"/api/folder-links/{kept['id']}", json={"push": False})
    assert r.status_code == 200 and r.json()["push"] is False
    owner.delete(f"/api/folder-links/{kept['id']}", params={"remove_files": 1}).raise_for_status()

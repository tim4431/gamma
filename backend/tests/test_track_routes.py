"""A work folder's tracking dialog through its routes (routers/folder_links.py,
gamma/work_folders/selection.py): a work folder made to choose its files,
which runs no round until they are chosen, and cancelled; the tree of its
directory, what git ignores and what the scan never tracks; the preview of a
selection and its save; who may use them. And the link's side of work
folders (gamma/folder_links.py): the push that comes down only with its
files in the library, the library switch and the pull in the mark, the
whole library that pushes everything nowhere, a file's vault by its stamp,
and a file the work folder does not track opened from this computer."""

import hashlib
import json
import os
import sys

import pytest

from conftest import (account_of, doc_props, login, make_folder, make_page, make_user, mark_work_folder, work_props,
                      workspace_of)
from gamma import folder_links, workspaces
from gamma.vault import disk as vault_disk, store as vault_store, sync as vault_sync
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page, new_block_id
from gamma.db import connect_pages_db
from gamma.ops import commit_ops
from gamma.work_folders import ignore_rules, selection
from test_vault_routes import _create, _page_of, _pdf, _put, _sync, _vault, disk  # noqa: F401: the disk fixture

OWNER = "trk_owner"
WINDOWS = sys.platform == "win32"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture
def rounds(disk, monkeypatch):
    """The rounds asked for in the background, ``[(link id, kw)]``: none runs."""
    asked = []
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: asked.append((link_id, kw)))
    return asked


def _choosing(c, root, files=None, **body):
    """A work folder made from the directory ``root`` holding ``files``
    ({path: text or bytes}), its files to be chosen: the link as the API
    answers it."""
    root.mkdir(parents=True, exist_ok=True)
    for rel, data in (files or {}).items():
        _put(root / rel, data)
    return _create(c, path=str(root), new_folder=True, push="everything", choose=True, **body)


def _tree(c, link, **params):
    r = c.get(f"/api/folder-links/{link['id']}/tree", params=params)
    assert r.status_code == 200, r.text
    return r.json()


def _rows(tree) -> dict:
    return {e["path"]: e for e in tree["entries"]}


def _selection(base="file", changes=()):
    return {"base": base, "changes": list(changes)}


def _preview(c, link, base="file", changes=()):
    r = c.post(f"/api/folder-links/{link['id']}/selection/preview", json={"selection": _selection(base, changes)})
    assert r.status_code == 200, r.text
    return r.json()


def _save(c, link, base="file", changes=(), base_hash="", **body):
    return c.put(f"/api/folder-links/{link['id']}/selection",
                 json={"selection": _selection(base, changes), "base_hash": base_hash, **body})


def _mark(link):
    with connect_pages_db(link["workspace_id"]) as conn:
        row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (link["folder_id"],)).fetchone()
    return json.loads(row[0]).get("vault") if row else None


def _props(link, page_id) -> dict:
    with connect_pages_db(link["workspace_id"]) as conn:
        return json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()[0])


def _files(root) -> dict:
    return {p.relative_to(root).as_posix(): (p.stat().st_size, p.stat().st_mtime_ns) for p in root.rglob("*")}


# --- making one, and its cancel ------------------------------------------------------------------

def test_a_work_folder_made_to_choose_its_files_runs_no_round_until_they_are_chosen(owner, disk, rounds):
    root = disk / "Paper"
    link = _choosing(owner, root, {"main.pdf": _pdf("main"), "notes.md": "notes\n"})
    assert link["choosing"] is True and link["status"]["paused_at"] and link["library"] is False and rounds == []
    state = folder_links.get_link(link["id"])["state"]
    assert state["choosing"] is True and state["made"] == {"folder": True, "marker": True, "marker_dir": True}
    assert state["work_folder_at"]                                     # nothing to take over
    assert (root / vault_disk.MARKER).is_file() and not (root / ignore_rules.NAME).exists()
    assert _mark(link) == {"pull": True, "library": False}
    with connect_pages_db(link["workspace_id"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM unified_blocks WHERE parent_id = 'root' AND work_folder = ?",
                            (link["folder_id"],)).fetchone()[0] == 0

    url = f"/api/folder-links/{link['id']}"
    r = owner.post(f"{url}/sync")
    assert r.status_code == 409 and r.json()["detail"] == "Choose its files first."
    r = owner.patch(url, json={"paused": False})
    assert r.status_code == 400 and r.json()["detail"] == "Choose its files first."
    assert folder_links.run_link(link["id"])["stopped"] == folder_links.CHOOSE_FIRST
    assert folder_links.run_page(link["id"], new_block_id()) is False
    owner.post(f"{url}/relink", json={"path": str(root)}).raise_for_status()
    r = owner.post("/api/folder-links/open", json={"path": str(root / "main.pdf")})
    assert r.status_code == 400 and r.json()["detail"] == "Choose what this work folder tracks first."
    assert rounds == [] and vault_store.files(link["id"]) == {}
    assert "not chosen yet" in folder_links._halted(folder_links.get_link(link["id"]))
    assert owner.get(url).json()["choosing"] is True


def test_only_a_work_folder_chooses_its_files_and_the_whole_library_pushes_everything_nowhere(owner, disk):
    f = _put(disk / "one.pdf", _pdf("one"))
    (disk / "Pdfs").mkdir()
    for body in ({"path": str(disk / "Pdfs"), "new_folder": True, "push": "pdfs"},
                 {"path": str(disk / "Far"), "folder": "f", "remote_url": "http://nas", "token": "t"},
                 {"kind": "file", "page": make_page(owner, "TR followed")["id"], "path": str(f)}):
        r = owner.post("/api/folder-links", json={**body, "choose": True})
        assert r.status_code == 400 and r.json()["detail"] == folder_links.CHOOSES_ONLY, body
    (disk / "Whole").mkdir(parents=True)
    r = owner.post("/api/folder-links", json={"folder": "root", "path": str(disk / "Whole"), "push": "everything"})
    assert r.status_code == 400 and "whole library cannot push everything" in r.json()["detail"]
    whole = _create(owner, folder="root", path=str(disk / "Whole"))                  # it pulls: a kept folder
    r = owner.patch(f"/api/folder-links/{whole['id']}", json={"push": "everything"})
    assert r.status_code == 400 and "whole library cannot push everything" in r.json()["detail"]


def test_cancelling_a_work_folder_takes_back_what_making_it_made(owner, disk, rounds):
    ws = workspace_of(OWNER)
    root = disk / "Cancelled"
    link = _choosing(owner, root, {"a.md": "a\n"})
    assert owner.delete(f"/api/folder-links/{link['id']}").json() == {"ok": True}
    assert folder_links.get_link(link["id"]) is None and vault_store.files(link["id"]) == {}
    assert not (root / vault_disk.MARKER_DIR).exists() and not (root / ignore_rules.NAME).exists()
    assert (root / "a.md").read_text(encoding="utf-8") == "a\n"
    with connect_pages_db(ws) as conn:
        assert conn.execute("SELECT 1 FROM unified_blocks WHERE id = ?", (link["folder_id"],)).fetchone() is None

    # of a folder that was there, into a directory whose .gamma/ holds more: those stay, and the mark goes
    folder = make_folder(owner, "TR kept folder")
    other = disk / "Kept dir"
    _put(other / vault_disk.MARKER_DIR / "mine.txt", "mine\n")
    link = _create(owner, folder=folder, path=str(other), push="everything", choose=True)
    assert folder_links.get_link(link["id"])["state"]["made"] == {"folder": False, "marker": True, "marker_dir": False}
    assert _mark(link) == {"pull": True, "library": False}
    assert owner.delete(f"/api/folder-links/{link['id']}").status_code == 200
    assert (other / vault_disk.MARKER_DIR / "mine.txt").is_file() and not (other / vault_disk.MARKER).exists()
    with connect_pages_db(ws) as conn:
        props = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (folder,)).fetchone()
    assert props is not None and "vault" not in json.loads(props[0]) and rounds == []


# --- the tree --------------------------------------------------------------------------------------

def test_the_tree_lists_the_directory_whole_and_what_is_never_tracked_closed(owner, disk, rounds, monkeypatch):
    root = disk / "Lister"
    link = _choosing(owner, root, {"main.tex": "\\documentclass{article}\n", "refs.bib": "@misc{a}\n", "notes.md": "n\n",
                                   "fig/plot.py": "print(1)\n", "fig/a.png": b"\x89PNG not really", ".env": "SECRET=1\n",
                                   ".hidden/x.md": "x\n", "node_modules/pkg/i.md": "i\n", "~$lock.md": "",
                                   "odd.md": "odd\n", "sub/deep/d.md": "d\n"})
    _put(root / ".paper.pdf.icloud", b"stub")
    monkeypatch.setattr(vault_disk, "refusal", lambda name: "refused here" if name == "odd.md" else "")
    linked = False
    if WINDOWS:
        import _winapi
        _put(disk / "elsewhere" / "secret.md", "not the vault's")
        _winapi.CreateJunction(str(disk / "elsewhere"), str(root / "j"))
        linked = True
    else:
        try:
            os.symlink(disk, root / "j", target_is_directory=True)
            linked = True
        except OSError:
            pass
    before = _files(root)
    tree = _tree(owner, link)
    assert _files(root) == before                                      # nothing written, nothing hydrated
    rows = _rows(tree)
    assert tree["dir"] == "" and tree["base"] == "file" and tree["capped"] is False
    assert tree["rules"] == {"exists": False, "ours": False, "hash": ""} and str(root) not in json.dumps(tree)
    tex = rows["main.tex"]
    assert (tex["directory"], tex["format"], tex["language"], tex["kind"]) == (False, "text", "latex", "file")
    assert tex["size"] == len("\\documentclass{article}\n") and tex["mtime"] == (root / "main.tex").stat().st_mtime_ns // 10**6
    assert rows["refs.bib"]["language"] == "bibtex" and rows["fig/a.png"]["kind"] == "file"   # a work folder's picture is a file
    assert rows["notes.md"]["kind"] == "notes" and rows["paper.pdf"]["kind"] == "pdf"
    assert rows["fig"]["directory"] and rows["fig"]["walked"] is True and rows["sub/deep"]["walked"] is True
    assert rows[".hidden"]["never"] == "dot" and rows[".hidden"]["walked"] is False
    assert rows["node_modules"]["never"] == "node_modules" and rows[".gamma"]["never"] == "dot"
    assert not any(p.startswith((".hidden/", "node_modules/", ".gamma/")) for p in rows)
    assert "~$lock.md" not in rows and rows[".env"]["never"] is None and rows["odd.md"]["never"] == "name"
    assert rows["paper.pdf"]["placeholder"] is True and rows["paper.pdf"]["size"] == 0
    if linked:
        assert rows["j"]["never"] == "link" and not any(p.startswith("j/") for p in rows)
    # what the default tracks: notes, PDFs and pictures
    assert {p for p, e in rows.items() if e["selected"] and not e["directory"]} == {
        "notes.md", "fig/a.png", "sub/deep/d.md", "paper.pdf"}
    assert all(e["page"] is None and e["pair"] is None and e["locked"] is None for e in rows.values() if not e["directory"])
    assert _rows(_tree(owner, link, dir="sub")).keys() == {"sub/deep", "sub/deep/d.md"}

    if sys.platform not in ("win32", "darwin"):   # names that differ only by case, and a name that is no UTF-8
        _put(root / "Case.md", "c\n")
        _put(root / "case.md", "c\n")
        (root / os.fsdecode(b"bad\xff.md")).write_text("b\n")
        rows = _rows(_tree(owner, link))
        assert rows["Case.md"]["never"] == rows["case.md"]["never"] == "case"
        assert rows["bad\\xff.md"]["never"] == "encoding"


def test_the_tree_names_no_place_on_this_computer_and_stops_where_the_vault_is_not(owner, disk, rounds):
    root = disk / "Bounds"
    link = _choosing(owner, root, {"a.md": "a\n", "sub/b.md": "b\n"})
    url = f"/api/folder-links/{link['id']}/tree"
    for bad in ("..", "../Bounds", str(root), "C:/x", ".gamma", "sub/.git", "node_modules", "a.md", "nowhere"):
        r = owner.get(url, params={"dir": bad})
        assert r.status_code == 400, (bad, r.text)
    if WINDOWS:
        import _winapi
        _put(disk / "out" / "x.md", "x\n")
        _winapi.CreateJunction(str(disk / "out"), str(root / "j"))
        assert owner.get(url, params={"dir": "j"}).status_code == 400
    away = disk / "Bounds away"
    os.rename(root, away)
    r = owner.get(url)
    assert r.status_code == 409 and r.json()["detail"] == "the vault's directory is not there"
    root.mkdir()
    vault_disk.make_marker(root)
    r = owner.get(url)
    assert r.status_code == 409 and "another vault's" in r.json()["detail"]
    r = owner.post(f"/api/folder-links/{link['id']}/selection/preview", json={"selection": _selection()})
    assert r.status_code == 409


def test_the_tree_marks_what_git_ignores_and_leaves_a_directory_it_ignores_closed(owner, disk, rounds):
    root = disk / "Gitted"
    link = _choosing(owner, root, {
        ".gitignore": "build/\n*.pdf\n!keep.pdf\n!build/out.pdf\n", "sub/.gitignore": "*.log\n",
        "main.pdf": _pdf("main"), "keep.pdf": _pdf("keep"), "notes.md": "n\n", "secret.md": "s\n",
        "build/out.pdf": _pdf("out"), "build/main.aux": "aux\n", "build/deep/x.md": "x\n",
        "sub/run.log": "log\n", "sub/a.md": "a\n", "figures/fig1.pdf": _pdf("fig1")})
    _put(root / ".git" / "info" / "exclude", "secret.md\n")
    rows = _rows(_tree(owner, link, base="preset"))
    ignored = {p for p, e in rows.items() if e["git_ignored"]}
    assert {"build", "main.pdf", "secret.md", "sub/run.log", "figures/fig1.pdf"} <= ignored
    assert not {"keep.pdf", "notes.md", "sub/a.md"} & ignored
    assert rows["build"]["walked"] is False and not any(p.startswith("build/") for p in rows)
    # the preset: PDFs and notes, a PDF git ignores by a pattern of names among them, none inside a directory it ignores
    assert {p for p, e in rows.items() if e["selected"] and not e["directory"]} == {
        "main.pdf", "keep.pdf", "notes.md", "secret.md", "sub/a.md", "figures/fig1.pdf"}
    build = _tree(owner, link, dir="build", base="preset")
    inside = _rows(build)
    assert build["dir"] == "build" and set(inside) == {"build/out.pdf", "build/main.aux", "build/deep", "build/deep/x.md"}
    assert all(e["git_ignored"] for e in inside.values())               # a file below it whatever its negation
    assert not any(e["selected"] for e in inside.values()) and inside["build/deep"]["walked"] is True

    # a vault inside a repository whose top file ignores its build directory
    repo = disk / "Repo"
    _put(repo / ".gitignore", "paper/build/\n")
    (repo / ".git").mkdir(parents=True)
    paper = _choosing(owner, repo / "paper", {"main.md": "m\n", "build/x.pdf": _pdf("x")})
    rows = _rows(_tree(owner, paper))
    assert rows["build"]["git_ignored"] and rows["build"]["walked"] is False and not rows["main.md"]["git_ignored"]


def test_the_tree_stops_at_its_budget_and_reads_no_file_id(owner, disk, rounds, monkeypatch):
    root = disk / "Budget"
    link = _choosing(owner, root, {**{f"top{i}.md": "t\n" for i in range(3)}, "a/one.md": "1\n", "b/two.md": "2\n"})
    real = vault_disk._entries

    class NoInode:
        def __init__(self, entry):
            self._entry = entry

        def __getattr__(self, name):
            assert name != "inode", "the tree read a file id"
            return getattr(self._entry, name)
    monkeypatch.setattr(vault_disk, "_entries", lambda directory: [NoInode(e) for e in real(directory)])
    monkeypatch.setattr(selection, "LIST_ENTRIES", 6)
    tree = _tree(owner, link)
    rows = _rows(tree)
    shut = [p for p, e in rows.items() if e["directory"] and not e["walked"] and not e["never"]]
    assert tree["capped"] is True and sorted(shut) == ["a", "b"]
    shut = shut[0]
    assert _rows(_tree(owner, link, dir=shut)).keys() == {f"{shut}/{'one' if shut == 'a' else 'two'}.md"}


def test_the_tree_selects_what_the_gammaignore_tracks_and_names_each_files_page(owner, disk, rounds):
    root = disk / "Tracked"
    link = _vault(owner, root, {"paper.md": "the paper's notes\n", "a.md": "a\n", "pic.png": b"\x89PNG x"})
    _put(root / "paper.pdf", _pdf("paper"))
    _put(root / "main.tex", "tex\n")
    _sync(owner, link["id"])
    page = _page_of(link, "paper.pdf")
    assert page == _page_of(link, "paper.md")                          # a paper's PDF and its notes: one page
    rows = _rows(_tree(owner, link))
    assert rows["paper.pdf"]["page"] == page and rows["paper.pdf"]["pair"] == "paper.md"
    assert rows["paper.md"]["pair"] == "paper.pdf" and rows["a.md"]["pair"] is None and rows["main.tex"]["page"] is None
    assert {p for p, e in rows.items() if e["selected"]} == {"paper.md", "paper.pdf", "a.md", "pic.png"}
    _put(root / ignore_rules.NAME, "*\n!*.tex\n!*.pdf\n")
    tree = _tree(owner, link)
    text = (root / ignore_rules.NAME).read_text(encoding="utf-8")
    assert tree["rules"] == {"exists": True, "ours": False, "hash": hashlib.sha256(text.encode()).hexdigest()}
    assert {p for p, e in _rows(tree).items() if e["selected"]} == {"main.tex", "paper.pdf"}


# --- the preview and the save ----------------------------------------------------------------------

def test_the_preview_counts_what_a_selection_tracks_and_what_leaves(owner, disk, rounds, monkeypatch):
    root = disk / "Previewed"
    link = _vault(owner, root, {"paper.md": "notes on it\n", "a.md": "a\n", "figs/f1.pdf": _pdf("f1"),
                                "figs/f2.pdf": _pdf("f2"), "pic.png": b"\x89PNG x"})
    _put(root / "paper.pdf", _pdf("the paper"))
    _put(root / "main.tex", "tex\n")
    _put(root / "refs.bib", "@misc{x}\n")
    _sync(owner, link["id"])
    f1 = _page_of(link, "figs/f1.pdf")
    note = new_block_id()
    commit_ops(link["workspace_id"], f1, [{"op": "insert", "id": note, "parent": f1, "content": "a note on f1",
                                           "props": {}}], actor=link["created_by"])
    r = owner.post(f"/api/pages/{f1}/comments", json={"block_id": note, "body": "is this right?"})
    assert r.status_code == 201, r.text

    seen = _preview(owner, link)
    assert seen["files"] == 6 and seen["by_format"] == {"pdf": 3, "markdown": 2, "image": 1, "other": 0}
    assert seen["bytes"] == sum((root / p).stat().st_size for p in ("paper.md", "a.md", "figs/f1.pdf", "figs/f2.pdf",
                                                                    "pic.png", "paper.pdf"))
    assert seen["leaving"] == [] and seen["joined"] == [] and seen["large"] == 0 and seen["rules_ours"] is True
    assert seen["over_cap"] is False and seen["cap"] == selection.MAX_FILES and seen["keep_bytes"] == 64 * 1024 * 1024

    seen = _preview(owner, link, changes=[{"path": "figs/", "on": False}, {"format": "latex", "on": True}])
    assert seen["files"] == 6 and seen["large"] == 0 and seen["by_format"]["other"] == 2
    with monkeypatch.context() as m:        # the keep limit: files of other formats than notes and PDFs past it are listed
        m.setenv("GAMMA_VAULT_KEEP_MB", "0")
        small = _preview(owner, link, changes=[{"path": "figs/", "on": False}, {"format": "latex", "on": True}])
    assert small["large"] == 3 and small["keep_bytes"] == 0 and small["files"] == 6
    assert [(x["path"], x["page"], x["notes"], x["open_comments"]) for x in seen["leaving"]] == [
        ("figs/f1.pdf", f1, 1, 1), ("figs/f2.pdf", _page_of(link, "figs/f2.pdf"), 0, 0)]
    rules = ignore_rules.Rules.parse(seen["text"])
    assert seen["text"].startswith(ignore_rules.HEADER + "\n")
    assert {p for p in ("paper.md", "a.md", "figs/f1.pdf", "figs/f2.pdf", "pic.png", "paper.pdf", "main.tex",
                        "refs.bib") if rules.tracks(p)} == {"paper.md", "a.md", "pic.png", "paper.pdf", "main.tex",
                                                           "refs.bib"}
    seen = _preview(owner, link, changes=[{"path": "paper.md", "on": False}, {"path": "figs/f2.pdf", "on": False}])
    assert seen["joined"] == [{"path": "paper.md", "with": "paper.pdf"}] and seen["files"] == 5
    monkeypatch.setattr(selection, "MAX_FILES", 3)
    seen = _preview(owner, link)
    assert seen["over_cap"] is True and seen["cap"] == 3
    r = _save(owner, link)
    assert r.status_code == 400 and "more than a work folder tracks" in r.json()["detail"]
    r = owner.post(f"/api/folder-links/{link['id']}/selection/preview", json={"selection": {"changes": [{"on": True}]}})
    assert r.status_code == 400


def test_saving_writes_the_gammaignore_and_runs_the_first_round_confirmed(owner, disk, rounds):
    root = disk / "Saved"
    link = _choosing(owner, root, {".gitignore": "build/\n*.pdf\n", "main.tex": "tex\n", "main.pdf": _pdf("main"),
                                   "notes.md": "n\n", "figures/fig1.pdf": _pdf("f1"), "figures/fig2.pdf": _pdf("f2"),
                                   "figures/plot.py": "p\n", "build/main.aux": "aux\n"})
    rows = _rows(_tree(owner, link, base="preset"))
    assert {p for p, e in rows.items() if e["selected"] and not e["directory"]} == {
        "main.pdf", "notes.md", "figures/fig1.pdf", "figures/fig2.pdf"}
    changes = [{"path": "figures/fig2.pdf", "on": False}]
    assert _preview(owner, link, "preset", changes)["files"] == 3
    r = _save(owner, link, "preset", changes, base_hash="0" * 64)
    assert r.status_code == 409 and r.json() == {"detail": selection.CHANGED, "hash": ""}
    assert rounds == [] and not (root / ignore_rules.NAME).exists()

    r = _save(owner, link, "preset", changes, library=True)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["choosing"] is False and "paused_at" not in saved["status"] and saved["library"] is True
    assert rounds == [(link["id"], {"confirm": True})]
    text = (root / ignore_rules.NAME).read_text(encoding="utf-8")
    assert text.startswith(ignore_rules.HEADER + "\n")
    state = folder_links.get_link(link["id"])["state"]
    assert state[vault_sync.SELECTION] == hashlib.sha256(text.encode()).hexdigest()
    assert "choosing" not in state and "made" not in state and _mark(link) == {"pull": True, "library": True}
    assert _sync(owner, link["id"])["last_error"] == ""
    assert set(vault_store.files(link["id"])) == {"main.pdf", "notes.md", "figures/fig1.pdf"}
    page = _page_of(link, "main.pdf")
    assert _props(link, page)["work_folder"] == link["folder_id"] and _props(link, page)["library"] is True

    # Choose files… again over a .gammaignore the person rewrote: theirs is kept in the vault's trash
    _put(root / ignore_rules.NAME, "*\n!*/\n!*.md\n")
    tree = _tree(owner, link)
    assert tree["rules"]["ours"] is False and {p for p, e in _rows(tree).items() if e["selected"] and not e["directory"]} \
        == {"notes.md"}
    assert _preview(owner, link)["rules_ours"] is False
    r = _save(owner, link, changes=[{"format": "pdf", "on": True}], base_hash=tree["rules"]["hash"])
    assert r.status_code == 200, r.text
    kept = list((root / vault_disk.TRASH).rglob(ignore_rules.NAME))
    assert [p.read_text(encoding="utf-8") for p in kept] == ["*\n!*/\n!*.md\n"]
    assert ignore_rules.read(root).ours and rounds[-1] == (link["id"], {"confirm": True})


def test_saving_takes_in_the_vaults_of_one_file_inside_and_keeps_their_pdfs_ticked(owner, disk, rounds):
    root = disk / "Taking in"
    f = _put(root / "papers" / "followed.pdf", _pdf("followed, then a work folder's"))
    up = owner.post("/api/uploads", files={"file": ("f.pdf", f.read_bytes(), "application/pdf")}).json()
    page = make_page(owner, "TR followed paper", doc_props(up["doc_id"], name="followed.pdf"))["id"]
    file_link = _create(owner, kind="file", page=page, path=str(f))
    link = _choosing(owner, root, {"notes.md": "n\n"})
    assert folder_links.get_link(file_link["id"]) is not None            # taken in at the save
    rows = _rows(_tree(owner, link, base="preset"))
    assert rows["papers/followed.pdf"]["locked"] == selection.FOLLOWED and rows["papers/followed.pdf"]["selected"]
    changes = [{"path": "papers/", "on": False}]
    assert _preview(owner, link, "preset", changes)["files"] == 2
    assert _save(owner, link, "preset", changes).status_code == 200
    assert folder_links.get_link(file_link["id"]) is None
    assert vault_store.files(link["id"])["papers/followed.pdf"]["page_id"] == page
    props = _props(link, page)
    assert (props["work_folder"], props["library"], props["file_name"]) == (link["folder_id"], True, "followed.pdf")
    assert ignore_rules.read(root).tracks("papers/followed.pdf")


def test_a_file_its_work_folder_does_not_track_opens_from_no_page(owner, disk, rounds):
    root = disk / "Excluding"
    link = _vault(owner, root, {"a.md": "a\n"})
    _put(root / ignore_rules.NAME, "drafts/\n")
    _sync(owner, link["id"])
    _put(root / "drafts" / "x.pdf", _pdf("a draft"))
    r = owner.post("/api/folder-links/open", json={"path": str(root / "drafts" / "x.pdf")})
    assert r.status_code == 400 and "is not tracked by its work folder" in r.json()["detail"]
    _put(root / "later.pdf", _pdf("tracked, not read yet"))
    r = owner.post("/api/folder-links/open", json={"path": str(root / "later.pdf")})
    assert r.status_code == 200 and r.json()["page"] == _page_of(link, "later.pdf")


# --- who may -------------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def lab(owner):
    """A shared workspace of trk_owner's with an editor and a viewer, and an
    account of none: ``(workspace, owner, editor, viewer, stranger)``, each
    asking in it."""
    info = workspaces.create("TR lab", account_of(OWNER), kind="shared")

    def member(name, role):
        make_user(name, "pw")
        if role:
            workspaces.set_member(info["id"], account_of(name), role, by=account_of(OWNER))
        return login(name, "pw")
    clients = [login(OWNER, "pw"), member("trk_bob", "editor"), member("trk_vera", "viewer"), member("trk_stranger", "")]
    for c in clients:
        c.params = {"ws": info["id"]}
    return info["id"], *clients


def test_only_the_vaults_maker_uses_its_tracking_dialog(lab, disk, rounds, monkeypatch):
    _ws, alice, bob, vera, stranger = lab
    link = _choosing(alice, disk / "Alice's", {"a.md": "a\n"})
    f = _put(disk / "alone.pdf", _pdf("alone"))
    file_link = _create(alice, kind="file", page=make_page(alice, "TR alone")["id"], path=str(f))
    calls = (("GET", "tree", None), ("POST", "selection/preview", {"selection": _selection()}),
             ("PUT", "selection", {"selection": _selection(), "base_hash": ""}))
    for method, path, body in calls:
        assert bob.request(method, f"/api/folder-links/{link['id']}/{path}", json=body).status_code == 403
        for who in (vera, stranger):
            assert who.request(method, f"/api/folder-links/{link['id']}/{path}", json=body).status_code == 404
        assert alice.request(method, f"/api/folder-links/{file_link['id']}/{path}", json=body).status_code == 404
    for method, path, body in calls:                                     # paused and choosing: the maker's still
        r = alice.request(method, f"/api/folder-links/{link['id']}/{path}", json=body)
        assert r.status_code == 200, (path, r.text)
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    assert alice.get(f"/api/folder-links/{link['id']}/tree").status_code == 404


# --- the link's side ---------------------------------------------------------------------------------

def test_a_work_folders_push_comes_down_only_with_its_files_in_the_library(owner, disk):
    root = disk / "Lowering"
    link = _vault(owner, root, {"a.md": "a\n", "b.md": "b\n"})
    url = f"/api/folder-links/{link['id']}"
    r = owner.patch(url, json={"push": "pdfs"})
    assert r.status_code == 400 and "not in the library" in r.json()["detail"]
    r = owner.patch(url, json={"library": True, "pull": False})
    assert r.status_code == 200 and r.json()["library"] is True and r.json()["pull"] is False
    assert _mark(link) == {"pull": False, "library": True}
    pages = [_page_of(link, p) for p in ("a.md", "b.md")]
    assert all("library" not in _props(link, p) for p in pages)         # the switch changes no page there is
    owner.patch(url, json={"pull": True}).raise_for_status()
    for page in pages:
        commit_ops(link["workspace_id"], page, [{"op": "set", "id": page, "props": {"library": True}}],
                   actor=link["created_by"])
    assert owner.patch(url, json={"push": "pdfs"}).json()["push"] == "pdfs"
    status = _sync(owner, link["id"])                                    # its next round lets it go
    assert status["last_error"] == "" and _mark(link) is None
    assert not any(key in _props(link, p) for p in pages for key in ("work_folder", "library", "file_name"))
    assert "work_folder_at" not in folder_links.get_link(link["id"])["state"]
    r = owner.patch(url, json={"push": "everything"})                    # a kept folder becomes one again
    assert r.status_code == 200 and r.json()["library"] is True
    _sync(owner, link["id"])
    assert _mark(link) == {"pull": True, "library": True}
    assert all(_props(link, p)["work_folder"] == link["folder_id"] and _props(link, p)["library"] is True for p in pages)


def test_a_file_is_its_work_folders_by_its_stamp_alone(owner, disk):
    """A file of a work folder of another computer (a clone's), filed in a
    library folder this computer keeps a vault of too: it is no page of that
    vault's."""
    w, shelf = make_folder(owner, "TR their work"), make_folder(owner, "TR shelf")
    mark_work_folder(owner, w)
    ws = folder_links.get_link(_create(owner, folder=shelf, path=str(disk / "Shelf"))["id"])["workspace_id"]
    with connect_pages_db(ws) as conn:
        theirs = create_page(conn, "TR their file", {**work_props(w, "f.md", library=True), "folders": [w, shelf]},
                             actor=account_of(OWNER), client=DISK_CLIENT)["id"]
    mine = make_page(owner, "TR on the shelf", {"folders": [shelf]})["id"]
    assert folder_links.vault_of_page(ws, theirs) is None and folder_links.vault_of_page(ws, mine) is not None

"""A work folder's file by its path (gamma/work_folders/paths.py,
docs/dev/work_folders.md "Paths"): ``page_path`` and ``page_at_path`` are a
pair, over the folder tree and the vault's stamps, and a typed name matches
exactly, else ignoring case by ``blocks_store.named``'s ``lower()``;
``page_at_href`` resolves a note's relative link from the note's directory.
tests/shared/workpaths.json pins the path and the directory of a page, and
which links are relative, with the client's twin
(frontend/tests/workPaths.test.mjs, which reads its crumbs too)."""

import json
import uuid
from pathlib import Path

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, workspace_of
from gamma import folder_links, gamma_sync, pdf_meta, storage
from gamma.db import connect_pages_db, connect_vaults_db
from gamma.vault import disk as vault_disk, kinds, store as vault_store
from gamma.vault.local_remote import LocalRemote
from gamma.work_folders import paths
from gamma.work_folders.marks import Tree
from test_vault_sync import Vault, _pdf

OWNER = "wfpaths_owner"


@pytest.fixture(scope="module")
def owner(client):
    make_user(OWNER, "pw")
    return login(OWNER, "pw")


@pytest.fixture(autouse=True)
def quick(monkeypatch):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setattr(kinds, "SETTLE_NOTE_S", 0)
    monkeypatch.setattr(gamma_sync, "SETTLE_S", 0)
    monkeypatch.setattr(vault_store, "MISSING_S", 0)
    monkeypatch.setattr(pdf_meta, "schedule", lambda ws, doc_id: None)
    monkeypatch.setattr(storage, "linked_moved", [])
    monkeypatch.setattr(storage, "LINKED_STAT_S", 0)
    monkeypatch.setattr(storage, "_stats", {})


class PathVault(Vault):
    """``test_vault_sync.Vault`` of this module's account, its folder a work
    folder's, tracking every file."""

    def __init__(self, owner, root):
        self.owner, self.root = owner, root
        self.ws, self.user = workspace_of(OWNER), account_of(OWNER)
        self.name = f"WP {uuid.uuid4().hex[:8]}"
        self.folder = make_folder(owner, self.name)
        root.mkdir(parents=True, exist_ok=True)
        (root / ".gammaignore").write_text("# every file\n", encoding="utf-8")
        marker = vault_disk.make_marker(root)
        self.link = {"id": uuid.uuid4().hex, "workspace_id": self.ws, "folder_id": self.folder, "path": str(root),
                     "pull": 1, "push": folder_links.PUSH_ALL, "created_by": self.user,
                     "state": {"vault": marker["id"]}, "kind": "folder", "remote_url": "", "library": 0}
        self.remote = LocalRemote(self.ws, self.folder, self.user)
        mark_work_folder(owner, self.folder)

    def props(self, page_id) -> dict:
        return self.blocks(page_id)[page_id]["props"]


FILES = {
    "main.tex": "\\documentclass{article}\n\\input{sections/method}\n\\bibliography{refs}\n",
    "refs.bib": "@article{a, title = {A}}\n",
    "notes.md": "the paper's notes\n",
    "sections/method.tex": "\\section{Method}\ngradient descent\n",
    "sections/notes.md": "the method's notes\n",
    "figures/fig1.pdf": _pdf("fig1"),
    "figures/deep/plot.py": "print(1)\n",
}


@pytest.fixture
def paper(owner, tmp_path):
    v = PathVault(owner, tmp_path / "paper")
    for rel, data in FILES.items():
        v.write(rel, data, age=10)
    v.sync()
    yield v
    vault_store.forget_link(v.link["id"])
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM linked_files WHERE ws = ? AND substr(path, 1, ?) = ?",
                     (v.ws, len(str(v.root)), str(v.root)))


def _at(v, path):
    with connect_pages_db(v.ws) as conn:
        return paths.page_at_path(conn, Tree.read(conn), v.folder, path)


def test_every_file_is_at_its_path_and_its_path_finds_it(paper):
    v = paper
    with connect_pages_db(v.ws) as conn:
        tree = Tree.read(conn)
        for rel in FILES:
            page_id = v.page_of(rel)
            row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
            assert paths.page_path(tree, v.props(page_id), title=row[0]) == rel
            assert paths.page_at_path(conn, tree, v.folder, rel) == [page_id], rel


def test_a_path_is_found_by_case_by_backslashes_and_without_its_extension(paper):
    v = paper
    method, refs = v.page_of("sections/method.tex"), v.page_of("refs.bib")
    for typed in ("Sections/Method.TEX", "sections\\method.tex", "/sections//method.tex", "./sections/method.tex",
                  "sections/method"):
        assert _at(v, typed) == [method], typed
    assert _at(v, "refs") == [refs] and _at(v, "main") == [v.page_of("main.tex")]
    assert _at(v, "figures/deep/plot.py") == [v.page_of("figures/deep/plot.py")]
    for typed in ("", "/", "sections", "sections/absent.tex", "method.tex", "figures/fig1", "../main.tex"):
        assert _at(v, typed) == [], typed


def test_two_files_of_one_name_are_not_confused(paper):
    v = paper
    assert _at(v, "notes.md") == [v.page_of("notes.md")]
    assert _at(v, "sections/notes.md") == [v.page_of("sections/notes.md")]
    assert v.page_of("notes.md") != v.page_of("sections/notes.md")


def test_a_note_is_found_by_its_title(paper, owner):
    v = paper
    sections = vault_store.dirs(v.link["id"])["sections"]["folder_id"]
    r = owner.post("/api/pages", json={"title": "Draft ideas", "folders": [sections]})
    assert r.status_code == 200, r.text
    page_id = r.json()["id"]
    with connect_pages_db(v.ws) as conn:
        props = v.props(page_id)
        assert "file_name" not in props
        assert paths.page_path(Tree.read(conn), props, title="Draft ideas") == "Draft ideas"
    assert _at(v, "sections/Draft ideas") == [page_id] == _at(v, "sections/draft ideas.md")


def test_a_link_resolves_from_its_notes_directory(paper):
    v = paper
    method, main = v.page_of("sections/method.tex"), v.page_of("main.tex")

    def href(base, link):
        with connect_pages_db(v.ws) as conn:
            return paths.page_at_href(conn, Tree.read(conn), v.folder, base, link)

    assert href("sections", "method.tex") == [method] == href("", "sections/method.tex")
    assert href("sections", "../main.tex") == [main] == href("sections", "main.tex")   # then from W's top
    assert href("figures/deep", "../fig1.pdf#page=2") == [v.page_of("figures/fig1.pdf")]
    assert href("sections", "./notes.md") == [v.page_of("sections/notes.md")]       # the note's directory first
    assert href("", "notes.md") == [v.page_of("notes.md")]
    assert href("sections", "sections%5Cmethod.tex") == [method]
    for nothing in ("../../main.tex", "plot.py", "/main.tex", "deep/plot.py"):
        assert href("sections", nothing) == [], nothing


def test_a_link_resolves_among_the_pages_a_share_reaches(paper):
    """What is out of reach changes no answer: a candidate left with none
    is tried as if it named nothing."""
    v = paper
    inner, top = v.page_of("sections/notes.md"), v.page_of("notes.md")
    with connect_pages_db(v.ws) as conn:
        tree = Tree.read(conn)
        assert paths.page_at_href(conn, tree, v.folder, "sections", "notes.md", allows=lambda i: i != inner) == [top]
        assert paths.page_at_href(conn, tree, v.folder, "sections", "notes.md", allows=lambda i: i != top) == [inner]
        assert paths.page_at_href(conn, tree, v.folder, "sections", "notes.md", allows=lambda i: False) == []


def test_a_library_page_has_no_path(owner):
    page = make_page(owner, "a library page")
    with connect_pages_db(workspace_of(OWNER)) as conn:
        tree = Tree.read(conn)
        assert paths.page_path(tree, {}, title="a library page") == ""
        assert paths.page_path(tree, {"file_name": "x.tex", "folders": [page["id"]]}, title="t") == ""


def _tree(*folders) -> Tree:
    """A tree of ``(id, parent, name, marked)`` folders."""
    return Tree.of([(f, parent, name, {"vault": {"pull": True, "library": False}} if marked else {})
                    for f, parent, name, marked in folders])


TREE = _tree(("W1", "", "Paper", True), ("D1", "W1", "Sections", False), ("W2", "", "Straße", True),
             ("T1", "", "Twin", True), ("T2", "", "twin", True), ("T3", "", "TWIN", True), ("L", "", "Library", False))


def test_work_folders_by_id_by_name_and_by_case():
    assert paths.work_folders_named(TREE, "W1") == ["W1"]
    assert paths.work_folders_named(TREE, "Paper") == ["W1"] == paths.work_folders_named(TREE, " paper ")
    assert paths.work_folders_named(TREE, "STRASSE") == []           # lower(), not casefold()
    assert paths.work_folders_named(TREE, "straße") == ["W2"]
    assert paths.work_folders_named(TREE, "Twin") == ["T1"]          # an exact name first
    assert sorted(paths.work_folders_named(TREE, "tWin")) == ["T1", "T2", "T3"]
    for nothing in ("Library", "D1", "Sections", "", "  ", None):
        assert paths.work_folders_named(TREE, nothing) == [], nothing


def test_a_paths_first_segment_names_its_work_folder():
    assert paths.work_of_path(None, TREE, "paper/sections/method.tex") == ("W1", "sections/method.tex")
    assert paths.work_of_path(None, TREE, "\\Paper\\main.tex") == ("W1", "main.tex")
    assert paths.work_of_path(None, TREE, "W1") == ("W1", "")
    assert paths.work_of_path(None, TREE, "Library/x.tex") is None and paths.work_of_path(None, TREE, "") is None
    with pytest.raises(ValueError, match="T1, T2, T3"):
        paths.work_of_path(None, TREE, "tWin/x.tex")


SHARED = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "workpaths.json")
                    .read_text(encoding="utf-8"))
SHARED_TREE = _tree(*((f, parent, name, mark == "vault") for f, parent, name, mark in SHARED["tree"]))


@pytest.mark.parametrize("case", SHARED["cases"], ids=lambda c: c["name"])
def test_the_shared_paths(case):
    props, w = case["props"], case["props"].get("work_folder", "")
    assert paths.page_path(SHARED_TREE, props, title=case["title"]) == case["path"]
    assert paths.dir_path(SHARED_TREE, props["folders"][0], w) == case["dir"]


def test_a_folder_share_measures_a_path_from_its_folders_parent():
    raw = {"work_folder": "W", "folders": ["raw"], "file_name": "data.csv"}
    assert paths.page_path(SHARED_TREE, raw, shared="raw") == "raw/data.csv"
    assert paths.page_path(SHARED_TREE, raw, shared="figures") == "figures/raw/data.csv"
    assert paths.page_path(SHARED_TREE, raw, shared="W") == "figures/raw/data.csv"
    assert paths.path_top(SHARED_TREE, "W", "raw") == "figures"
    assert paths.path_top(SHARED_TREE, "W", "shelf") == paths.path_top(SHARED_TREE, "W") == "W"


@pytest.mark.parametrize("case", SHARED["hrefs"], ids=lambda c: c["href"] or "empty")
def test_the_shared_relative_links(case):
    assert paths.is_relative_href(case["href"]) is case["relative"]
    if not case["relative"]:
        assert paths.page_at_href(None, SHARED_TREE, "W", "sections", case["href"]) == []

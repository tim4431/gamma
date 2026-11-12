"""Sub-pages in the exports and imports (docs/research/sub-pages.md): a
sub-page's file sits in a directory named for its parent, beside the
parent's file, and its front matter names the parent — in the readable
Markdown export, the Obsidian vault, the annotated-PDF zip and a folder link
on disk (gamma/folder_sync.py) — while a library page's file stays where it
was; a single sub-page goes where its top's folders put it; the
markdown-zip import reads ``parent:`` back as ``parent_page``; a Gamma
backup carries the key as stored; a page's export with ``sub_pages`` takes
the pages inside it, in every format, laid out as a folder's are."""

import base64
import io
import json
import re
import sqlite3
import zipfile
from urllib.parse import quote, unquote

import pytest
from pypdf import PdfReader

from conftest import account_of, doc_props, login, make_folder, make_user, workspace_of
from gamma import bibtex, blocks_store
from gamma.integrations import create_token
from gamma.markdown_export import slugify
from test_folder_sync import SERVER, _round, _tool
from test_pdf_export import _blank_pdf

USER, PASSWORD = "spx_owner", "pw-spx-1"
GUEST, GUEST_PASSWORD = "spx_guest", "pw-spx-2"
PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==")


@pytest.fixture(scope="module")
def owner():
    make_user(USER, PASSWORD)
    return login(USER, PASSWORD)


@pytest.fixture(scope="module")
def guest():
    make_user(GUEST, GUEST_PASSWORD)
    return login(GUEST, GUEST_PASSWORD)


def _page(c, title, **body):
    r = c.post("/api/pages", json={"title": title, **body})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _children(c, page_id, contents):
    r = c.put(f"/api/blocks/{page_id}/children", json={"blocks": [
        {"id": f"{page_id}-{n}", "content": text, "properties": {}, "children": []}
        for n, text in enumerate(contents)]})
    assert r.status_code == 200, r.text


@pytest.fixture(scope="module")
def lab(owner):
    """Folder "SPX lab": "Other" at its top; in "SPX lab/Sub" the library
    page "Top", "Child" inside Top and "Grandchild" inside Child, which
    shows a picture and mentions Other (Other mentions it back). "Lone"
    is a library page filed in Sub too, with nothing inside."""
    folder, sub = make_folder(owner, "SPX lab"), make_folder(owner, "SPX lab/Sub")
    up = owner.post("/api/upload-image", files={"file": ("p.png", PNG, "image/png")})
    assert up.status_code == 200, up.text
    other = _page(owner, "Other", folders=[folder])
    top = _page(owner, "Top", folders=[sub])
    child = _page(owner, "Child", parent_page=top)
    grandchild = _page(owner, "Grandchild", parent_page=child)
    lone = _page(owner, "Lone", folders=[sub])
    _children(owner, top, ["top notes"])
    _children(owner, child, ["child notes"])
    _children(owner, grandchild, [f"a figure ![pic]({up.json()['url']})", f"see [[{other}]]"])
    _children(owner, other, [f"see [[{grandchild}]]"])
    return {"folder": folder, "sub": sub, "png": up.json()["url"].rsplit("/", 1)[1], "other": other, "top": top,
            "child": child, "grandchild": grandchild, "lone": lone}


def _zip(r) -> zipfile.ZipFile:
    assert r.status_code == 200, r.text
    return zipfile.ZipFile(io.BytesIO(r.content))


def _front(md: str) -> list[str]:
    """The front matter's lines."""
    assert md.startswith("---\n"), md
    return md[4:md.index("\n---\n")].split("\n")


def _import(c, data: bytes, folder: str) -> dict:
    """The import's page entries by the note's path in the zip."""
    r = c.post("/api/import/markdown-zip", files={"file": ("export.zip", data, "application/zip")},
               data={"folder": folder})
    assert r.status_code == 200, r.text
    return {p["selection_ids"][0]: p for p in r.json()["pages"]}


def _props(c, page_id) -> dict:
    r = c.get(f"/api/blocks/{page_id}")
    assert r.status_code == 200, r.text
    return r.json()["properties"]


# --- the readable Markdown export --------------------------------------------

def test_readable_export_nests_a_sub_page_beside_its_parent(owner, lab):
    stem = {k: slugify(t, lab[k]) for k, t in
            (("other", "Other"), ("top", "Top"), ("child", "Child"), ("grandchild", "Grandchild"), ("lone", "Lone"))}
    z = _zip(owner.get(f"/api/folders/{lab['folder']}/export", params={"mode": "readable"}))
    top, child = f"{stem['top']}.md", f"{stem['top']}/{stem['child']}.md"
    grandchild = f"{stem['top']}/{stem['child']}/{stem['grandchild']}.md"
    assert {f"{stem['other']}.md", top, child, grandchild, f"{stem['lone']}.md"} <= set(z.namelist())

    assert "parent: " not in z.read(top).decode()                        # a library page: as before
    assert _front(z.read(top).decode())[1:] == ["folder: Sub"]
    child_md = z.read(child).decode()
    assert _front(child_md)[1:] == ["folder: Sub", f"parent: ../{quote(stem['top'])}.md"]  # its top's folder
    md = z.read(grandchild).decode()
    assert _front(md)[1:] == ["folder: Sub", f"parent: ../{quote(stem['child'])}.md"]
    # links and pictures are relative to the file, so the zip reads anywhere
    assert f"](../../{quote(stem['other'])}.md)" in md and f"![pic](../../assets/{lab['png']})" in md
    assert f"assets/{lab['png']}" in z.namelist()
    assert f"]({quote(grandchild)})" in z.read(f"{stem['other']}.md").decode()


def test_one_sub_page_exported_alone_goes_where_its_top_is_filed(owner, lab):
    r = owner.get(f"/api/pages/{lab['child']}/export", params={"mode": "readable"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/markdown")
    assert _front(r.text)[1:] == ["folder: SPX lab/Sub"]                 # no parent in the export: none named
    z = _zip(owner.get(f"/api/pages/{lab['child']}/export", params={"mode": "obsidian"}))
    assert "SPX lab/Sub/Child.md" in z.namelist() and "parent:" not in z.read("SPX lab/Sub/Child.md").decode()


# --- the Obsidian vault --------------------------------------------------------

def test_vault_nests_a_sub_page_and_names_its_parent(owner, lab):
    z = _zip(owner.get(f"/api/folders/{lab['folder']}/export", params={"mode": "obsidian"}))
    names = set(z.namelist())
    assert {"Other.md", "Sub/Top.md", "Sub/Lone.md", "Sub/Top/Child.md", "Sub/Top/Child/Grandchild.md"} <= names
    assert "parent:" not in z.read("Sub/Top.md").decode()
    assert _front(z.read("Sub/Top/Child.md").decode()) == ['parent: "[[Top]]"']
    md = z.read("Sub/Top/Child/Grandchild.md").decode()
    assert _front(md) == ['parent: "[[Child]]"'] and "see [[Other]]" in md
    assert "see [[Grandchild]]" in z.read("Other.md").decode()


def test_annotated_pdfs_nest_a_sub_page_inside_its_parents_directory(owner):
    folder = make_folder(owner, "SPX papers")

    def paper(title, **body):
        up = owner.post("/api/uploads", files={"file": (f"{title}.pdf", _blank_pdf(pages=1) + title.encode(),
                                                        "application/pdf")})
        assert up.status_code == 200, up.text
        return _page(owner, title, properties=doc_props(up.json()["doc_id"]),
                     **body)

    review = paper("Review", folders=[folder])
    paper("Cited", parent_page=review)
    notes = _page(owner, "Reading notes", folders=[folder])           # no PDF: left out, so ...
    paper("Inside notes", parent_page=notes)                          # ... this one stays at the top
    z = _zip(owner.get(f"/api/folders/{folder}/export", params={"mode": "annotated-pdf"}))
    assert sorted(z.namelist()) == ["Inside notes.pdf", "Review.pdf", "Review/Cited.pdf"]


# --- the round trip --------------------------------------------------------------

@pytest.mark.parametrize("mode", ["readable", "obsidian"])
def test_the_zip_import_reads_the_parent_back(owner, guest, lab, mode):
    data = owner.get(f"/api/folders/{lab['folder']}/export", params={"mode": mode}).content
    back = make_folder(guest, f"SPX back {mode}")
    pages = _import(guest, data, back)
    by_title = {p["title"]: p for p in pages.values()}
    top, child, grandchild = by_title["Top"], by_title["Child"], by_title["Grandchild"]
    assert _props(guest, child["id"])["parent_page"] == top["id"]
    assert _props(guest, grandchild["id"])["parent_page"] == child["id"]
    assert "parent_page" not in _props(guest, top["id"])
    assert "folders" not in _props(guest, child["id"])                 # filed where its top is
    assert child["folders"] == grandchild["folders"] == top["folders"] == [[f"SPX back {mode}", "Sub"]]
    folders = guest.get("/api/sync/folders").json()["folders"]           # none for a parent's directory
    assert [f["path"] for f in folders if f["path"][0] == f"SPX back {mode}"] == [[f"SPX back {mode}"],
                                                                               [f"SPX back {mode}", "Sub"]]
    family = guest.get(f"/api/pages/{top['id']}/family").json()
    assert [p["id"] for p in family["sub_pages"]] == [child["id"]]


def test_a_notion_zip_reads_back_with_its_directories_as_folders(owner, guest, lab):
    """It holds no ids, so it is a Plain zip: a directory pairs with the file
    beside it only in a zip that has ids (``Paper/Child.md``), so sub-pages
    come back as pages in a folder. Only Notion, once it has written ids
    into its own export of them, gives the tree back."""
    data = owner.get(f"/api/folders/{lab['folder']}/export", params={"mode": "notion"}).content
    pages = _import(guest, data, make_folder(guest, "SPX back notion"))
    by_title = {p["title"]: p for p in pages.values()}
    assert not any("parent_page" in _props(guest, p["id"]) for p in pages.values())
    assert by_title["Top"]["folders"] == [["SPX back notion", "Sub"]]
    assert by_title["Child"]["folders"] == [["SPX back notion", "Sub", "Top"]]
    assert by_title["Grandchild"]["folders"] == [["SPX back notion", "Sub", "Top", "Child"]]


def test_a_parent_that_is_not_in_the_import_leaves_a_library_page(guest):
    """A ``parent:`` naming no note of the zip (or the note itself) is
    passed by: the note is filed by its directory as before."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("Gone/Kid.md", "---\ntitle: Kid\nparent: ../Gone.md\n---\n\nhello\n")
        z.writestr("Self.md", "---\ntitle: Self\nparent: Self.md\n---\n\nme\n")
    pages = _import(guest, buf.getvalue(), make_folder(guest, "SPX orphans"))
    assert "parent_page" not in _props(guest, pages["Gone/Kid.md"]["id"])
    assert pages["Gone/Kid.md"]["folders"] == [["SPX orphans", "Gone"]]
    assert "parent_page" not in _props(guest, pages["Self.md"]["id"])


@pytest.mark.parametrize("mode", ["readable", "obsidian"])
def test_a_sub_page_with_its_parent_s_title_goes_back_inside_it(owner, guest, mode):
    """``Meeting/Meeting.md`` names ``Meeting.md`` (the file beside its
    directory), not itself; ``Meeting/Agenda.md`` names the top, not its
    namesake sibling."""
    folder = make_folder(owner, f"SPX meet {mode}")
    top = _page(owner, "Meeting", folders=[folder])
    for page in (top, _page(owner, "Meeting", parent_page=top), _page(owner, "Agenda", parent_page=top)):
        _children(owner, page, [f"{mode} notes of {page}"])  # (an import passes by a note it holds already)
    data = owner.get(f"/api/folders/{folder}/export", params={"mode": mode}).content
    pages = _import(guest, data, make_folder(guest, f"SPX meet back {mode}"))
    tops = [p for p in pages.values() if "parent_page" not in _props(guest, p["id"])]
    assert [p["title"] for p in tops] == ["Meeting"]
    inside = {p["title"]: _props(guest, p["id"])["parent_page"] for p in pages.values() if p not in tops}
    assert inside == {"Meeting": tops[0]["id"], "Agenda": tops[0]["id"]}


def test_a_cycle_of_parents_in_an_import_leaves_library_pages(guest):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("D/A.md", "---\ntitle: A\nparent: B.md\n---\n\na\n")
        z.writestr("D/B.md", "---\ntitle: B\nparent: A.md\n---\n\nb\n")
        z.writestr("D/C.md", "---\ntitle: C\nparent: A.md\n---\n\nc\n")
        z.writestr("E.md", "---\ntitle: E\n---\n\ne\n")  # (one top directory reads as a wrapper)
    pages = _import(guest, buf.getvalue(), make_folder(guest, "SPX cycle"))
    for note in ("D/A.md", "D/B.md"):
        assert "parent_page" not in _props(guest, pages[note]["id"])
        assert pages[note]["folders"] == [["SPX cycle", "D"]]
    assert _props(guest, pages["D/C.md"]["id"])["parent_page"] == pages["D/A.md"]["id"]


def test_a_sub_page_never_takes_a_library_page_s_file_name(owner):
    folder = make_folder(owner, "SPX clash")
    physics = _page(owner, "Physics", folders=[folder])
    library = _page(owner, "A", folders=[make_folder(owner, "SPX clash/Physics")])

    def stems():
        return {p["id"]: p["stem"] for p in owner.get(f"/api/sync/folders/{folder}").json()["pages"]}

    assert stems()[library] == "Physics/A"
    inside = _page(owner, "A", parent_page=physics)  # its directory is the folder's
    assert stems()[library] == "Physics/A" and stems()[inside] == "Physics/A 2"
    names = _zip(owner.get(f"/api/folders/{folder}/export", params={"mode": "obsidian"})).namelist()
    assert "Physics/A.md" in names and "Physics/A 2.md" in names


def test_a_sub_page_s_notes_change_with_the_text_naming_its_parent(owner):
    folder, sub = make_folder(owner, "SPX ver"), make_folder(owner, "SPX ver/Sub")
    top = _page(owner, "Top", folders=[sub])
    child = _page(owner, "Child", parent_page=top)

    def version():
        return next(p["version"] for p in owner.get(f"/api/sync/folders/{folder}").json()["pages"] if p["id"] == child)

    before = version()
    _page(owner, "Top", folders=[folder])  # a namesake: the link text becomes the path
    assert version() != before
    notes = owner.get(f"/api/sync/folders/{folder}/notes", params={"pages": child}).json()["pages"]
    assert 'parent: "[[Sub/Top]]"' in _front(notes[child]["markdown"])


# --- the Gamma backup --------------------------------------------------------------

def test_a_gamma_folder_export_carries_the_sub_pages_with_their_key(owner, lab, tmp_path):
    z = _zip(owner.get(f"/api/folders/{lab['folder']}/export", params={"mode": "gamma"}))
    (tmp_path / "pages.db").write_bytes(z.read("pages.db"))
    with sqlite3.connect(tmp_path / "pages.db") as conn:
        props = {pid: json.loads(raw) for pid, raw in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE parent_id = 'root'")}
    assert props[lab["child"]]["parent_page"] == lab["top"]
    assert props[lab["grandchild"]]["parent_page"] == lab["child"]


# --- a folder link on disk -------------------------------------------------------

def test_the_folder_layout_nests_sub_pages_for_the_folder_and_the_library(owner, lab):
    m = owner.get(f"/api/sync/folders/{lab['folder']}").json()
    stems = {p["id"]: p["stem"] for p in m["pages"]}
    assert [stems[lab[k]] for k in ("other", "top", "child", "grandchild", "lone")] == [
        "Other", "Sub/Top", "Sub/Top/Child", "Sub/Top/Child/Grandchild", "Sub/Lone"]
    assert sorted(d["path"] for d in m["dirs"]) == ["Sub", "Sub/Top", "Sub/Top/Child"]  # a page with pages inside
    whole = {p["id"]: p["stem"] for p in owner.get("/api/sync/folders/root").json()["pages"]}
    assert whole[lab["grandchild"]] == "SPX lab/Sub/Top/Child/Grandchild"
    r = owner.get(f"/api/sync/folders/{lab['folder']}/notes", params={"pages": f"{lab['grandchild']},{lab['top']}"})
    notes = r.json()["pages"]
    assert _front(notes[lab["grandchild"]]["markdown"]) == [f"gamma_id: {lab['grandchild']}", 'parent: "[[Child]]"']
    assert f"![pic](../../../attachments/{lab['png']})" in notes[lab["grandchild"]]["markdown"]
    assert _front(notes[lab["top"]]["markdown"]) == [f"gamma_id: {lab['top']}"]


def test_a_folder_link_on_disk_nests_the_files_and_follows_a_rename(owner, tmp_path, monkeypatch):
    folder = make_folder(owner, "SPX disk")
    top = _page(owner, "Project", folders=[folder])
    child = _page(owner, "Part", parent_page=top)
    grandchild = _page(owner, "Detail", parent_page=child)
    token = create_token(account_of(USER), workspace_of(USER), "spx sync", 90)["token"]
    mod = _tool(monkeypatch)
    dest = tmp_path / "disk"
    assert mod.main(["init", str(dest), "--server", SERVER, "--folder", "SPX disk", "--token", token,
                     "--save-token"]) == 0
    assert mod.main(["sync", str(dest)]) == 0
    assert (dest / "Project.md").is_file()
    assert 'parent: "[[Project]]"' in (dest / "Project" / "Part.md").read_text(encoding="utf-8")
    assert f"gamma_id: {grandchild}" in (dest / "Project" / "Part" / "Detail.md").read_text(encoding="utf-8")

    # the parent renamed: the files inside follow; the one that names it is written again, the
    # one whose parent kept its name (Detail's "[[Part]]") only moves
    r = owner.put(f"/api/blocks/{top}", json={"content": "Programme"})
    assert r.status_code == 200, r.text
    counts = _round(mod, dest, token)
    assert counts["renamed"] == 3 and counts["updated"] == 2 and counts["added"] == 0
    assert 'parent: "[[Programme]]"' in (dest / "Programme" / "Part.md").read_text(encoding="utf-8")
    assert (dest / "Programme" / "Part" / "Detail.md").is_file()
    assert not (dest / "Project").exists() and not (dest / "Project.md").exists()

    # moved to the library: its file leaves the parent's directory, which goes once empty
    r = owner.post(f"/api/pages/{grandchild}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": grandchild, "props": {"parent_page": None, "folders": [folder]}}]})
    assert r.status_code == 200, r.text
    _round(mod, dest, token)
    assert (dest / "Detail.md").is_file() and not (dest / "Programme" / "Part").exists()


# --- a page's export with the pages inside it (sub_pages) ---------------------------

def _paper(c, title, **body):
    """A page with its own PDF, a note and a paper record."""
    up = c.post("/api/uploads", files={"file": (f"{title}.pdf", _blank_pdf(pages=1) + title.encode(),
                                                "application/pdf")})
    assert up.status_code == 200, up.text
    record = {"title": title, "authors": ["Ada Lovelace"], "year": "1843", "venue": "Notes"}
    page = _page(c, title, properties={**doc_props(up.json()["doc_id"]),
                                       "meta": record, "bibtex": bibtex.build_entry(record, title.lower())},
                 **body)
    _children(c, page, [f"{title} notes"])
    return page


@pytest.fixture(scope="module")
def tree(owner):
    """In "SPX tree/Inner": the paper "Tree", "Branch" inside it and "Leaf"
    inside Branch; "Solo", a paper with no page inside it; "Bare", a page
    with no PDF, and "Inside bare", a paper inside it."""
    inner = make_folder(owner, "SPX tree/Inner")
    top = _paper(owner, "Tree", folders=[inner])
    branch = _paper(owner, "Branch", parent_page=top)
    bare = _page(owner, "Bare", folders=[inner])
    _children(owner, bare, ["no paper here"])
    pages = {"tree": top, "branch": branch, "leaf": _paper(owner, "Leaf", parent_page=branch),
             "solo": _paper(owner, "Solo", folders=[inner]), "bare": bare,
             "inside_bare": _paper(owner, "Inside bare", parent_page=bare)}
    return {"folder": make_folder(owner, "SPX tree"), **pages,
            **{f"{k}_stem": slugify(t, pages[k]) for k, t in (("tree", "Tree"), ("branch", "Branch"),
                                                              ("leaf", "Leaf"), ("solo", "Solo"))}}


def _name(r) -> str:
    """A download's file name."""
    assert r.status_code == 200, r.text
    return unquote(r.headers["content-disposition"].rsplit("''", 1)[1])


def _held(name: str, body: bytes):
    """What a download holds, comparably: a zip's entry names but the
    bundled files (a Gamma backup's pages and their parents instead), a
    PDF's page count, a .bib's citation keys, a .md's first line; a .note
    is just one."""
    if name.endswith("-gamma.zip"):
        conn = sqlite3.connect(":memory:")
        conn.deserialize(zipfile.ZipFile(io.BytesIO(body)).read("pages.db"))
        rows = conn.execute("SELECT content, properties FROM unified_blocks WHERE parent_id = 'root' "
                            "ORDER BY content").fetchall()
        conn.close()
        return [(title, json.loads(props).get("parent_page", "")) for title, props in rows]
    if name.endswith(".zip"):
        return sorted(n for n in zipfile.ZipFile(io.BytesIO(body)).namelist()
                      if not n.startswith(("assets/", "attachments/")))
    if name.endswith(".pdf"):
        return len(PdfReader(io.BytesIO(body)).pages)
    if name.endswith(".bib"):
        return re.findall(r"^@\w+\{([^,]+),", body.decode(), re.M)
    if name.endswith(".md"):
        return body.decode().split("\n")[0]
    return "note"


def _job(c, **body):
    """An export job run to its end, and its download."""
    from gamma import jobs
    started = c.post("/api/jobs/export", json=body)
    assert started.status_code == 200, started.text
    job = jobs.wait(started.json()["id"])
    assert job["state"] == "done", job["error"]
    return job, c.get(f"/api/jobs/{job['id']}/download")


# Each mode's download of Tree as (its name, what it holds; ``_held``, a
# Gamma backup's parents named "@<fixture key>"), from the pages' stems: with
# the pages inside it, and without them.
_TREE_EXPORTS = {
    "readable": lambda t, b, leaf: (
        (f"{t}.zip", [f"{t}.md", f"{t}/{b}.md", f"{t}/{b}/{leaf}.md"]),
        (f"{t}.md", "---")),
    "obsidian": lambda t, b, leaf: (
        (f"{t}-obsidian.zip", [".obsidian/app.json", "SPX tree/Inner/Tree.md", "SPX tree/Inner/Tree/Branch.md",
                               "SPX tree/Inner/Tree/Branch/Leaf.md"]),
        (f"{t}-obsidian.zip", [".obsidian/app.json", "SPX tree/Inner/Tree.md"])),
    "notes-pdf": lambda t, b, leaf: (
        (f"{t}-notes.pdf", 3),
        (f"{t}-notes.pdf", 1)),
    "annotated-pdf": lambda t, b, leaf: (
        (f"{t}-annotated.zip", ["Tree.pdf", "Tree/Branch.pdf", "Tree/Branch/Leaf.pdf"]),
        (f"{t}-notes.pdf", 1)),  # the notes switch is on
    "notability": lambda t, b, leaf: (
        (f"{t}-notability.zip", ["Tree.note", "Tree/Branch.note", "Tree/Branch/Leaf.note"]),
        ("Tree.note", "note")),
    "notion": lambda t, b, leaf: (
        (f"{t}-notion.zip", ["Tree.md", "Tree/Branch.md", "Tree/Branch/Leaf.md"]),
        (f"{t}-notion.zip", ["Tree.md"])),
    "logseq-graph": lambda t, b, leaf: (
        (f"{t}-logseq.zip", sorted(["logseq/config.edn"] + [f"pages/{p}{s}.md" for s in (t, b, leaf)
                                                            for p in ("", "hls__")])),
        (f"{t}-logseq.zip", sorted(["logseq/config.edn", f"pages/hls__{t}.md", f"pages/{t}.md"]))),
    "zotero-rdf": lambda t, b, leaf: (
        (f"{t}-zotero.zip", sorted([f"{t}/README.txt", f"{t}/files/1/Tree.pdf", f"{t}/files/2/Branch.pdf",
                                    f"{t}/files/3/Leaf.pdf", f"{t}/{t}.rdf"])),
        (f"{t}-zotero.zip", sorted([f"{t}/README.txt", f"{t}/files/1/Tree.pdf", f"{t}/{t}.rdf"]))),
    "bibtex": lambda t, b, leaf: (
        (f"{t}.bib", ["branch", "leaf", "tree"]),
        (f"{t}.bib", ["tree"])),
    "gamma": lambda t, b, leaf: (
        (f"{t}-gamma.zip", [("Branch", "@tree"), ("Leaf", "@branch"), ("Tree", "")]),
        (f"{t}-gamma.zip", [("Tree", "")])),
}


@pytest.mark.parametrize("mode", list(_TREE_EXPORTS))
def test_a_page_s_export_with_the_pages_inside_it(owner, tree, mode):
    """The GET and the job, with the flag and without: the page's file at
    the top and each page inside it in a directory named for its parent
    (the vault's under the page's folders, as its page export always is),
    and a format that writes one file for one page a zip for several,
    named for the page."""
    def ids(held):
        return [(title, tree[parent[1:]] if parent else "") for title, parent in held] if mode == "gamma" else held

    with_them, alone = _TREE_EXPORTS[mode](tree["tree_stem"], tree["branch_stem"], tree["leaf_stem"])
    params = {"mode": mode, "pdf": int(mode != "readable")}  # a readable page without its PDF is a bare .md
    url = f"/api/pages/{tree['tree']}/export"
    for flag, expected, pages in ((1, with_them, 3), (0, alone, 1)):
        r = owner.get(url, params={**params, "sub_pages": flag})
        assert (_name(r), _held(_name(r), r.content)) == (expected[0], ids(expected[1])), flag
        job, r = _job(owner, page_id=tree["tree"], mode=mode, pdf=bool(params["pdf"]), sub_pages=bool(flag))
        assert (job["artifact"]["name"], _held(_name(r), r.content)) == (expected[0], ids(expected[1])), flag
        assert job["result"]["pages"] == pages and job["params"]["sub_pages"] is bool(flag)


def test_the_readable_export_of_a_sub_page_with_the_pages_inside_it(owner, tree):
    """The page exported is the zip's top: its own parent is not in the
    export, so it names none."""
    b, leaf = tree["branch_stem"], tree["leaf_stem"]
    z = _zip(owner.get(f"/api/pages/{tree['branch']}/export", params={"mode": "readable", "pdf": 0, "sub_pages": 1}))
    assert sorted(z.namelist()) == [f"{b}.md", f"{b}/{leaf}.md"]

    def filed(name):
        return [line for line in _front(z.read(name).decode()) if line.startswith(("folder:", "parent:"))]

    assert filed(f"{b}.md") == ["folder: SPX tree/Inner"]
    assert filed(f"{b}/{leaf}.md") == ["folder: SPX tree/Inner", f"parent: ../{quote(b)}.md"]
    z = _zip(owner.get(f"/api/pages/{tree['branch']}/export", params={"mode": "annotated-pdf", "sub_pages": 1}))
    assert sorted(z.namelist()) == ["Branch.pdf", "Branch/Leaf.pdf"]


def test_the_annotated_pdf_route_takes_the_flag(owner, tree):
    r = owner.get(f"/api/pages/{tree['tree']}/export-pdf", params={"sub_pages": 1})
    assert _name(r) == f"{tree['tree_stem']}-annotated.zip" and r.headers["content-type"] == "application/zip"
    assert _held(_name(r), r.content) == ["Tree.pdf", "Tree/Branch.pdf", "Tree/Branch/Leaf.pdf"]
    solo, plain = (owner.get(f"/api/pages/{tree['solo']}/export-pdf", params=p) for p in ({"sub_pages": 1}, {}))
    assert solo.content == plain.content and _name(solo) == _name(plain) == f"{tree['solo_stem']}-annotated.pdf"
    assert solo.headers["x-annotations-written"] == plain.headers["x-annotations-written"] == "0"


def test_the_annotated_pdf_route_reads_the_sub_pages_once(owner, tree, monkeypatch):
    """The route asks the page itself whether it holds any page; only the
    export of the pages inside it reads the workspace's sub-pages."""
    reads, real = [], blocks_store.page_parents

    def counted(conn):
        reads.append(1)
        return real(conn)

    monkeypatch.setattr(blocks_store, "page_parents", counted)
    assert owner.get(f"/api/pages/{tree['solo']}/export-pdf", params={"sub_pages": 1}).status_code == 200
    assert reads == []
    assert owner.get(f"/api/pages/{tree['tree']}/export-pdf", params={"sub_pages": 1}).status_code == 200
    assert reads == [1]


def test_a_page_left_out_leaves_one_file_or_a_zip_of_the_rest(owner, tree):
    """Bare has no PDF and no paper record: the PDF of the one page inside
    it is the download itself, and its bibliography and the review hold
    that page's entry, Bare listed as left out."""
    r = owner.get(f"/api/pages/{tree['bare']}/export", params={"mode": "annotated-pdf", "sub_pages": 1})
    assert _name(r) == f"{slugify('Inside bare', tree['inside_bare'])}-notes.pdf"
    assert r.headers["content-type"] == "application/pdf"
    job, _ = _job(owner, page_id=tree["bare"], mode="annotated-pdf", sub_pages=True)
    assert job["result"]["pages"] == 1 and [s["title"] for s in job["result"]["skipped"]] == ["Bare"]
    r = owner.get(f"/api/pages/{tree['bare']}/export", params={"mode": "bibtex", "sub_pages": 1})
    assert _held(_name(r), r.content) == ["insidebare"]
    preview = owner.get("/api/bibliography", params={"page_id": tree["bare"], "sub_pages": 1}).json()
    assert [e["page_id"] for e in preview["entries"]] == [tree["inside_bare"]]
    assert [s["title"] for s in preview["skipped"]] == ["Bare"]
    assert owner.get("/api/bibliography", params={"page_id": tree["bare"]}).json()["entries"] == []
    r = owner.get(f"/api/pages/{tree['bare']}/export", params={"mode": "bibtex"})
    assert r.status_code == 400 and r.json()["detail"] == "this page has no paper metadata to cite"


def test_a_tree_without_any_pdf_says_why(owner):
    notes = _page(owner, "Notes only", folders=[make_folder(owner, "SPX tree none")])
    _page(owner, "More notes", parent_page=notes)
    r = owner.get(f"/api/pages/{notes}/export", params={"mode": "annotated-pdf", "sub_pages": 1})
    assert r.status_code == 400 and r.json()["detail"] == "page has no PDF"
    r = owner.get(f"/api/pages/{notes}/export", params={"mode": "bibtex", "sub_pages": 1})
    assert r.status_code == 400 and r.json()["detail"] == "none of these pages has paper metadata to cite"


@pytest.mark.parametrize("mode", list(_TREE_EXPORTS))
def test_the_flag_on_a_page_with_none_inside_changes_nothing(owner, tree, mode):
    def download(flag):
        r = owner.get(f"/api/pages/{tree['solo']}/export", params={"mode": mode, "sub_pages": flag})
        name = _name(r)
        if mode == "gamma":
            return name, _held(name, r.content)
        if name.endswith(".note"):  # a note names its parts by fresh UUIDs and dates its writing
            return name, r.headers["content-type"], len(zipfile.ZipFile(io.BytesIO(r.content)).namelist())
        if name.endswith(".zip"):  # what each file holds, not when it was packed
            z = zipfile.ZipFile(io.BytesIO(r.content))
            return name, {n: z.read(n) for n in z.namelist()}
        return name, r.headers["content-type"], r.content if mode != "notes-pdf" else _held(name, r.content)

    assert download(1) == download(0)


def test_a_share_exports_the_pages_inside_the_page_it_reaches(owner, tree, anon):
    page_share = {"share": owner.post(f"/api/share/{tree['branch']}", json={"audience": "anyone"}).json()["token"]}
    b, leaf = tree["branch_stem"], tree["leaf_stem"]
    r = anon.get(f"/api/pages/{tree['branch']}/export", params={"mode": "readable", "pdf": 0, "sub_pages": 1,
                                                                 **page_share})
    assert _held(_name(r), r.content) == [f"{b}.md", f"{b}/{leaf}.md"]
    r = anon.get(f"/api/pages/{tree['branch']}/export", params={"mode": "gamma", "sub_pages": 1, **page_share})
    assert _held(_name(r), r.content) == [("Branch", tree["tree"]), ("Leaf", tree["branch"])]  # as stored
    r = anon.get(f"/api/pages/{tree['branch']}/export-pdf", params={"sub_pages": 1, **page_share})
    assert _held(_name(r), r.content) == ["Branch.pdf", "Branch/Leaf.pdf"]
    # the page above the shared one is out of its reach, with the flag or without
    for path in ("export", "export-pdf"):
        for flag in (0, 1):
            r = anon.get(f"/api/pages/{tree['tree']}/{path}", params={"sub_pages": flag, **page_share})
            assert r.status_code == 403, (path, flag)
    r = anon.get("/api/bibliography", params={"page_id": tree["tree"], "sub_pages": 1, **page_share})
    assert r.status_code == 403

    folder_share = {"share": owner.post(f"/api/share/folder/{tree['folder']}",
                                        json={"audience": "anyone"}).json()["token"]}
    r = anon.get(f"/api/pages/{tree['tree']}/export", params={"mode": "annotated-pdf", "sub_pages": 1,
                                                               **folder_share})
    assert _held(_name(r), r.content) == ["Tree.pdf", "Tree/Branch.pdf", "Tree/Branch/Leaf.pdf"]
    elsewhere = _page(owner, "Elsewhere", folders=[make_folder(owner, "SPX away")])
    _page(owner, "Inside elsewhere", parent_page=elsewhere)
    r = anon.get(f"/api/pages/{elsewhere}/export", params={"sub_pages": 1, **folder_share})
    assert r.status_code == 403


def test_a_folder_export_takes_no_flag(owner, tree):
    """A folder's pages always bring the pages inside them; the job's
    params name no flag for it."""
    job, r = _job(owner, folder=tree["folder"], mode="annotated-pdf", sub_pages=True)
    assert job["params"]["sub_pages"] is False
    assert _held(_name(r), r.content) == ["Inner/Inside bare.pdf", "Inner/Solo.pdf", "Inner/Tree.pdf",
                                          "Inner/Tree/Branch.pdf", "Inner/Tree/Branch/Leaf.pdf"]

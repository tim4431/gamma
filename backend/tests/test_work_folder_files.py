"""A work folder's files found by name or path and a path resolved to its
pages (docs/dev/work_folders.md "Files by name" and "Paths"): ``GET
/api/files``, quick open's Files group, ranked by
``work_folders.file_search``; ``GET /api/folders/{id}/at``, a note's
relative link or a typed path resolved by ``paths.page_at_href``."""

import json
import secrets
import unicodedata
from pathlib import Path
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_folder, make_page, make_user, mark_work_folder, work_props, workspace_of
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.db import connect_pages_db
from gamma.documents.model import document
from gamma.ops import trash_page
from gamma.work_folders import file_search
from gamma.work_folders.marks import Tree

USER, PASSWORD = "wff_owner", "pw-wff-1"
HREFS = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "workpaths.json")
                   .read_text(encoding="utf-8"))["hrefs"]


def nfd(text: str) -> str:
    return unicodedata.normalize("NFD", text)


@pytest.fixture(scope="module")
def f():
    """W ``wff-W`` with ``sections/``, ``figures/``, ``figures/raw/`` and
    ``old drafts/``; its files ``main.tex``, ``main.pdf`` (in the library
    too, filed in L, titled "Attention"), ``sections/intro.tex``,
    ``sections/method.tex``, ``figures/fig1.pdf``, ``figures/fig_1.pdf``,
    ``figures/raw/data.csv``, ``old drafts/v1.tex``, ``my fig.pdf``,
    ``İstanbul.pdf``, ``图1.pdf``, the note ``notes.md``, two notes made in
    Gamma titled "Plan" and "Draft" and another "Draft" in ``sections``
    (no ``file_name``), and ``old.tex`` in Recently deleted; ``résumé.md``
    and the directory ``études/`` with ``chapter.tex``, both named in NFD
    as a Mac's disk gives them. Beside it a kept folder K's page titled
    ``main.tex`` and a second work folder ``wff-W2`` with its own
    ``main.tex``."""
    make_user(USER, PASSWORD)
    owner = login(USER, PASSWORD)
    ws, account = workspace_of(USER), account_of(USER)
    ids = {key: make_folder(owner, path) for key, path in (
        ("W", "wff-W"), ("sections", "wff-W/sections"), ("figures", "wff-W/figures"),
        ("raw", "wff-W/figures/raw"), ("drafts", "wff-W/old drafts"), ("etudes", f"wff-W/{nfd('études')}"),
        ("W2", "wff-W2"), ("L", "wff-L"), ("K", "wff-kept"))}
    mark_work_folder(owner, ids["W"])
    mark_work_folder(owner, ids["W2"])
    W = ids["W"]

    def pdf(name):
        return document(secrets.token_hex(32), name=name, at="path")

    files = {
        "main.tex": ("main.tex", W, W, None),
        "main.pdf": ("Attention", W, W, pdf("main.pdf")),
        "intro.tex": ("intro.tex", W, ids["sections"], None),
        "method.tex": ("method.tex", W, ids["sections"], None),
        "fig1.pdf": ("fig1", W, ids["figures"], pdf("fig1.pdf")),
        "fig_1.pdf": ("fig_1", W, ids["figures"], pdf("fig_1.pdf")),
        "data.csv": ("data.csv", W, ids["raw"], None),
        "v1.tex": ("v1.tex", W, ids["drafts"], None),
        "my fig.pdf": ("my fig", W, W, pdf("my fig.pdf")),
        "İstanbul.pdf": ("İstanbul", W, W, pdf("İstanbul.pdf")),
        "图1.pdf": ("图1", W, W, pdf("图1.pdf")),
        "notes.md": ("notes", W, W, None),
        "old.tex": ("old.tex", W, W, None),
        nfd("résumé.md"): ("résumé", W, W, None),
        "chapter.tex": ("chapter.tex", W, ids["etudes"], None),
        "W2 main.tex": ("main.tex", ids["W2"], ids["W2"], None),
    }
    p = {}
    with connect_pages_db(ws) as conn:
        for key, (title, w, directory, doc) in files.items():
            props = work_props(w, key.removeprefix("W2 "), doc=doc, directory=directory,
                               library=key == "main.pdf")
            if key == "main.pdf":
                props["folders"] = [W, ids["L"]]
            p[key] = create_page(conn, title, props, actor=account, client=DISK_CLIENT)["id"]
        conn.commit()
        trash_page(ws, conn, p["old.tex"], actor=account, client=DISK_CLIENT)
        conn.commit()
    for key, title in (("Plan", "Plan"), ("Draft", "Draft"), ("Draft 2", "Draft")):
        made = owner.post("/api/pages", json={"title": title, "folders": [ids["sections"]]})
        assert made.status_code == 200, made.text
        p[key] = made.json()["id"]
    p["kept"] = make_page(owner, "main.tex", properties={"folders": [ids["K"]]})["id"]
    return SimpleNamespace(owner=owner, ws=ws, p=p, **ids)


def _find(f, q, client=None, **params):
    got = (client or f.owner).get("/api/files", params={"q": q, **params})
    assert got.status_code == 200, got.text
    return got.json()


def _names(f, q, **params):
    return [r["properties"].get("file_name", r["content"]) for r in _find(f, q, **params)["files"]]


def _ids(f, q, **params):
    return [r["id"] for r in _find(f, q, **params)["files"]]


# ---- the match rule ------------------------------------------------------------------------

def test_the_query_has_two_shapes():
    Q = file_search.Query
    assert file_search.parse("dir 3/file-012") == Q([], ["dir 3"], "file 012", "")
    assert file_search.parse("figures/") == Q([], ["figures"], "", "")
    assert file_search.parse("\\main.tex") == Q([], [], "main tex", "")
    assert file_search.parse(" Fig_1  PDF ") == Q(["fig 1", "pdf"], None, "", "fig 1 pdf")
    for nothing in ("", "  ", "/", "-", "-_", " / - /"):
        assert file_search.parse(nothing) is None, nothing
    assert file_search.runs(file_search.parse("dir 3/file-012")) == ["file", "012"]
    assert file_search.runs(file_search.parse("main main.tex")) == ["main", "tex"]
    assert file_search.runs(file_search.parse("图")) == []


def test_a_term_scores_by_where_it_is_in_the_name():
    def points(q, name, directory=""):
        return file_search.score(file_search.parse(q), name, directory)[0]

    assert points("main", "main.tex") == 100 + 40
    assert points("tex", "main.tex") == 60 + 20
    assert points("ain", "main.tex") == 30 + 20
    assert points("main.tex", "main.tex") == 100 + 200
    assert points("sections", "method.tex", "sections") == 0       # a name term never matches the directory
    assert points("sections/met", "method.tex", "paper/sections") == 100 + 20
    assert points("ctions/met", "method.tex", "sections") == 100 + 10
    assert points("a/c/x", "x.tex", "a/b/c") == 100 + 20 + 20     # in order, not necessarily adjacent
    assert points("c/a/x", "x.tex", "a/b/c") == 0
    assert points("figures/", "data.csv", "figures/raw") == 20


# ---- GET /api/files ------------------------------------------------------------------------

def test_a_file_is_found_by_its_name(f):
    rows = _find(f, "main")["files"]
    assert [r["id"] for r in rows[:1]] == [f.p["main.pdf"]]        # main.pdf before main.tex: the folded name
    assert {r["id"] for r in rows[1:]} == {f.p["main.tex"], f.p["W2 main.tex"]}
    assert rows[0]["content"] == "Attention" and rows[0]["path"] == "main.pdf"
    assert {r["properties"]["work_folder"] for r in rows[1:]} == {f.W, f.W2}
    assert f.p["kept"] not in [r["id"] for r in rows]
    assert _names(f, "method") == ["method.tex"]
    assert _names(f, "Method.TEX") == ["method.tex"]
    assert _names(f, "main.tex")[:2] == ["main.tex", "main.tex"] and len(_names(f, "main.tex")) == 2


def test_separators_and_spaces_are_one(f):
    assert _names(f, "fig 1") == ["fig_1.pdf", "fig1.pdf"]
    assert _names(f, "fig1") == ["fig1.pdf"]
    assert _names(f, "my fig") == ["my fig.pdf"]


def test_name_terms_never_match_a_directory(f):
    assert _find(f, "figures fig1") == {"files": [], "total": 0}
    assert _find(f, "sections method") == {"files": [], "total": 0}
    assert _find(f, "old drafts") == {"files": [], "total": 0}


def test_a_path_fragment_finds_a_file_below_its_directories(f):
    for q in ("sections/met", "sections\\met"):
        rows = _find(f, q)["files"]
        assert [r["id"] for r in rows] == [f.p["method.tex"]], q
        assert rows[0]["path"] == "sections/method.tex"
        assert rows[0]["match"] == {"name": [[0, 3]], "path": [[0, 8]]}
    assert set(_ids(f, "figures/")) == {f.p["fig1.pdf"], f.p["fig_1.pdf"], f.p["data.csv"]}
    assert _ids(f, "old drafts/v1") == [f.p["v1.tex"]]
    assert _ids(f, "raw/") == [f.p["data.csv"]]


def test_a_note_without_its_file_is_found_by_its_title(f):
    rows = _find(f, "Plan")["files"]
    assert [r["id"] for r in rows] == [f.p["Plan"]]
    assert rows[0]["path"] == "Plan" and "file_name" not in rows[0]["properties"]


def test_what_is_no_live_file_of_a_work_folder_is_not_found(f):
    assert _find(f, "old") == {"files": [], "total": 0}             # old.tex is in Recently deleted
    assert f.p["kept"] not in _ids(f, "main.tex")


def test_a_folder_keeps_the_search_to_its_subtree(f):
    assert _names(f, "data", folder=f.figures) == ["data.csv"]
    assert set(_names(f, "fig", folder=f.figures)) == {"fig1.pdf", "fig_1.pdf"}
    assert _names(f, "fig", folder=f.sections) == []
    assert _ids(f, "main", folder=f.W2) == [f.p["W2 main.tex"]]
    got = f.owner.get("/api/files", params={"q": "main", "folder": f.L})
    assert got.status_code == 400 and got.json()["detail"] == "the library lists this folder's pages"
    assert f.owner.get("/api/files", params={"q": "main", "folder": "no-such-folder"}).status_code == 400


def test_limit_and_total(f):
    every = _find(f, "pdf")
    assert every["total"] == 6 and len(every["files"]) == 6
    two = _find(f, "pdf", limit=2)
    assert two["total"] == 6 and [r["id"] for r in two["files"]] == [r["id"] for r in every["files"][:2]]
    assert len(_find(f, "pdf", limit=0)["files"]) == 1


def test_the_spans_are_code_points_of_the_name_and_the_directory(f):
    assert _find(f, "main", folder=f.W2)["files"][0]["match"] == {"name": [[0, 4]], "path": []}
    assert _find(f, "data", folder=f.raw)["files"][0]["match"] == {"name": [[0, 4]], "path": []}
    assert _find(f, "igures/raw/csv")["files"][0]["match"] == {"name": [[5, 8]], "path": [[1, 7], [8, 11]]}
    lengthened = _find(f, "stanbul")["files"]                     # "İ".lower() is two code points
    assert [r["id"] for r in lengthened] == [f.p["İstanbul.pdf"]] and lengthened[0]["match"]["name"] == []


def test_a_query_that_names_nothing_answers_no_rows(f):
    for q in ("", " ", "/", "-_"):
        assert _find(f, q) == {"files": [], "total": 0}, q
    assert f.owner.get("/api/files").status_code == 422


def test_a_query_with_no_latin_run_reads_every_file(f):
    rows = _find(f, "图")["files"]
    assert [r["id"] for r in rows] == [f.p["图1.pdf"]] and rows[0]["match"]["name"] == [[0, 1]]


def test_a_pasted_paragraph_asks_a_few_likes_and_answers(f):
    words = " ".join(f"w{n}" for n in range(1001))
    assert len(file_search.runs(file_search.parse(words))) == file_search.MAX_RUNS
    assert _find(f, words) == {"files": [], "total": 0}
    many = "ma ai in te ex mai ain tex main"                          # nine runs: "te" is past the cut
    assert "te" not in file_search.runs(file_search.parse(many))
    assert _names(f, many) == ["main.tex", "main.tex"]
    assert _names(f, f"{many} zz") == []                              # a term past the cut is still ranked


def test_a_row_names_the_directory_its_spans_index(f):
    rows = _find(f, "sections/pl")["files"]
    assert [(r["id"], r["path"], r["dir"]) for r in rows] == [(f.p["Plan"], "Plan", "sections")]
    assert rows[0]["match"]["path"] == [[0, 8]]
    assert _find(f, "main", folder=f.W2)["files"][0]["dir"] == ""
    assert _find(f, "data")["files"][0]["dir"] == "figures/raw"


def test_shares_keep_to_what_they_reach(f, anon):
    page = f.owner.post(f"/api/share/{f.p['main.tex']}", json={"audience": "anyone", "role": "view"})
    assert page.status_code == 200, page.text
    refused = anon.get("/api/files", params={"q": "main", "share": page.json()["token"]})
    assert refused.status_code == 403
    made = f.owner.post(f"/api/share/folder/{f.figures}", json={"audience": "anyone", "role": "view"})
    assert made.status_code == 200, made.text
    share = made.json()["token"]
    assert [r["id"] for r in _find(f, "fig1", anon, share=share)["files"]] == [f.p["fig1.pdf"]]
    assert _find(f, "main", anon, share=share)["files"] == []
    assert anon.get("/api/files", params={"q": "main", "folder": f.W, "share": share}).status_code == 403
    assert [r["id"] for r in _find(f, "data", anon, share=share, folder=f.raw)["files"]] == [f.p["data.csv"]]


def test_a_share_below_w_names_no_folder_above_its_own(f, anon):
    """A folder share of ``figures/raw``: its paths start at ``raw``, the
    folder its view shows at the top, and a name above it matches nothing."""
    share = f.owner.post(f"/api/share/folder/{f.raw}", json={"audience": "anyone", "role": "view"}).json()["token"]
    [row] = _find(f, "data", anon, share=share)["files"]
    assert (row["path"], row["dir"]) == ("raw/data.csv", "raw")
    assert _find(f, "figures/data", anon, share=share) == {"files": [], "total": 0}
    assert _find(f, "raw/data", anon, share=share)["files"][0]["match"]["path"] == [[0, 3]]
    got = _at(f, f.raw, "data.csv", anon, share=share)
    assert got.status_code == 200 and [r["path"] for r in got.json()["pages"]] == ["raw/data.csv"]
    assert _find(f, "figures/data")["files"][0]["path"] == "figures/raw/data.csv"   # a member's from W


# ---- GET /api/folders/{id}/at --------------------------------------------------------------

def _at(f, folder, path, client=None, **params):
    return (client or f.owner).get(f"/api/folders/{folder}/at", params={"path": path, **params})


def _at_ids(f, folder, path, **params):
    got = _at(f, folder, path, **params)
    assert got.status_code == 200, got.text
    return [r["id"] for r in got.json()["pages"]]


def test_a_path_from_w_resolves_to_its_page(f):
    got = _at(f, f.W, "sections/method.tex")
    assert got.status_code == 200, got.text
    [row] = got.json()["pages"]
    assert row["id"] == f.p["method.tex"] and row["path"] == "sections/method.tex"
    assert _at_ids(f, f.figures, "sections/method.tex") == [f.p["method.tex"]]   # any folder of W
    assert _at_ids(f, f.W, "sections/method") == [f.p["method.tex"]]             # LaTeX's leaf
    assert _at_ids(f, f.W, "my%20fig.pdf") == [f.p["my fig.pdf"]]
    assert _at_ids(f, f.W, "sections/method.tex#method") == [f.p["method.tex"]]


def test_a_link_resolves_from_its_notes_directory(f):
    for path, key in (("method.tex", "method.tex"), ("../main.tex", "main.tex"),
                      ("../figures/fig1.pdf", "fig1.pdf"), ("./intro.tex", "intro.tex"), ("main.tex", "main.tex")):
        assert _at_ids(f, f.W, path, dir="sections") == [f.p[key]], path
    assert _at_ids(f, f.W, "../fig1.pdf", dir="figures/raw") == [f.p["fig1.pdf"]]


def test_a_note_made_in_gamma_resolves_from_its_directory(f):
    """The client sends the note's directory, never its path: "Plan" has
    no ``file_name``, and its path ("Plan") would resolve from W."""
    assert _at_ids(f, f.W, "method.tex", dir="sections") == [f.p["method.tex"]]
    assert _at(f, f.W, "method.tex").status_code == 404


def test_a_link_without_dir_resolves_from_the_folder_asked(f):
    assert _at_ids(f, f.sections, "method.tex") == [f.p["method.tex"]]
    assert _at_ids(f, f.raw, "../fig1.pdf") == [f.p["fig1.pdf"]]
    assert _at(f, f.raw, "../fig1.pdf", dir="").status_code == 404           # a dir given is the one read


def test_two_pages_the_rule_cannot_tell_apart_are_both_answered_in_one_order(f):
    assert _at_ids(f, f.W, "sections/Draft") == sorted([f.p["Draft"], f.p["Draft 2"]])


def test_a_name_in_nfd_is_found_typed_in_either_form(f):
    """The disk's name stands as it came (NFD from a Mac); a typed or
    linked one is NFC as a browser gives it, or NFD pasted."""
    with connect_pages_db(f.ws) as conn:
        assert Tree.read(conn).names[f.etudes] == nfd("études")
    for form in ("NFC", "NFD"):
        def typed(text):
            return unicodedata.normalize(form, text)

        assert _at_ids(f, f.W, typed("résumé.md")) == [f.p[nfd("résumé.md")]], form
        assert _at_ids(f, f.W, typed("Résumé.MD")) == [f.p[nfd("résumé.md")]], form
        assert _at_ids(f, f.W, typed("études/chapter.tex")) == [f.p["chapter.tex"]], form
        assert _at_ids(f, f.W, typed("chapter.tex"), dir=typed("études")) == [f.p["chapter.tex"]], form
    assert _ids(f, "résumé") == [f.p[nfd("résumé.md")]]


def test_what_names_no_tracked_file_is_404(f):
    for path, base in (("../../x", "sections"), ("../main.tex", ""), ("sections/absent.tex", ""), ("old.tex", "")):
        got = _at(f, f.W, path, dir=base)
        assert got.status_code == 404 and got.json()["detail"] == "no tracked file at that path", path
    for case in HREFS:
        if not case["relative"]:
            assert _at(f, f.W, case["href"], dir="sections").status_code == 404, case["href"]
    assert _at(f, f.L, "main.tex").status_code == 400
    assert f.owner.get(f"/api/folders/{f.W}/at").status_code == 422


def test_a_path_through_a_share(f, anon):
    page = f.owner.post(f"/api/share/{f.p['main.tex']}", json={"audience": "anyone", "role": "view"}).json()["token"]
    assert _at(f, f.W, "main.tex", anon, share=page).status_code == 403
    share = f.owner.post(f"/api/share/folder/{f.figures}", json={"audience": "anyone", "role": "view"}).json()["token"]
    got = _at(f, f.figures, "fig1.pdf", anon, dir="figures", share=share)
    assert got.status_code == 200 and [r["id"] for r in got.json()["pages"]] == [f.p["fig1.pdf"]]
    assert _at(f, f.raw, "../fig_1.pdf", anon, dir="figures/raw", share=share).status_code == 200
    assert _at(f, f.figures, "main.tex", anon, share=share).status_code == 404   # resolved, out of its reach
    assert _at(f, f.W, "figures/fig1.pdf", anon, share=share).status_code == 403

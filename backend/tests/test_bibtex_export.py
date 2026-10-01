"""The BibTeX bibliography: the entry builder and its citation keys
(gamma/bibtex.py), the `?mode=bibtex` export of a page and of a folder, the
pinned key that survives a refetch, and the share-link URL a LaTeX editor
refreshes a folder's .bib from.
"""

import pytest

from conftest import login, make_page, make_user
from gamma import bibtex


@pytest.fixture(scope="module")
def user(client):
    make_user("bib_user", "bib-user-pw-123")
    return login("bib_user", "bib-user-pw-123")


def _paper(c, title, folder, meta=None, cite_key=""):
    """A page carrying a cached metadata record and its rendered entry, the
    way a metadata lookup leaves one."""
    record = {"title": title, "authors": ["Ada Lovelace"], "year": "1843",
              "venue": "Notes", "source": "crossref", **(meta or {})}
    props = {"folder": folder, "meta": record,
             "bibtex": bibtex.build_entry(record, cite_key)}
    if cite_key:
        props["cite_key"] = cite_key
    return make_page(c, title, props)


# --- the entry and its key ---------------------------------------------------

def test_default_key_is_surname_and_year():
    assert bibtex.default_key({"authors": ["Ada Lovelace"], "year": "1843"}) == "lovelace1843"
    assert bibtex.default_key({"authors": [], "year": "1999"}) == "paper1999"
    assert bibtex.default_key({}) == "paper"


def test_clean_key_drops_what_bibtex_breaks_on():
    assert bibtex.clean_key("  smith:2020-attention ") == "smith:2020-attention"
    assert bibtex.clean_key("a b,c{d}e=f") == "abcdef"
    assert bibtex.clean_key("   ") == ""


def test_entry_key_round_trips_through_with_key():
    entry = bibtex.build_entry({"title": "T", "authors": ["Ada Lovelace"], "year": "1843"})
    assert bibtex.entry_key(entry) == "lovelace1843"
    moved = bibtex.with_key(entry, "pinned:1")
    assert bibtex.entry_key(moved) == "pinned:1"
    assert "title = {T}" in moved          # only the key changed
    assert bibtex.with_key(entry, "") == entry  # nothing to pin


def test_pinned_keys_are_assigned_before_generated_ones():
    def rec(title, pinned):
        return {"text": f"@article{{lovelace1843,\n  title = {{{title}}}\n}}",
                "key": "lovelace1843", "pinned": pinned, "title": title}
    keys = [r["key"] for r in bibtex.unique_keys([rec("A", False), rec("B", True), rec("C", False)])]
    # The pin keeps the bare key wherever it sits in the order; the generated
    # clashes move out of its way, in input order.
    assert keys == ["lovelace1843a", "lovelace1843", "lovelace1843b"]
    assert len(set(keys)) == 3


def test_suffixes_pass_z():
    records = [{"text": "@article{k,\n}", "key": "k", "pinned": False} for _ in range(28)]
    keys = [r["key"] for r in bibtex.unique_keys(records)]
    assert keys[:3] == ["k", "ka", "kb"]
    assert keys[26:] == ["kz", "kaa"]
    assert len(set(keys)) == 28


def test_bibliography_has_no_timestamp():
    entry = bibtex.build_entry({"title": "T", "authors": ["Ada Lovelace"], "year": "1843"})
    records = bibtex.unique_keys([{"text": entry, "key": "lovelace1843", "pinned": False}])
    text = bibtex.bibliography(records, "Optics")
    assert text.startswith("% 1 entry from Optics, exported from Gamma")
    assert text == bibtex.bibliography(records, "Optics")  # byte-identical re-export


# --- the export --------------------------------------------------------------

def test_one_page_exports_its_entry_as_a_bib_file(user):
    page = _paper(user, "Analytical engine", "Bib lab")
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/x-bibtex")
    assert ".bib\"" in r.headers["content-disposition"]
    assert "@article{lovelace1843," in r.text
    assert "title = {Analytical engine}" in r.text


def test_a_folder_exports_one_bib_with_unique_keys(user):
    _paper(user, "First paper", "Bib folder")
    _paper(user, "Second paper", "Bib folder")
    _paper(user, "Deep paper", "Bib folder/Sub")
    _paper(user, "Other paper", "Bib elsewhere")
    r = user.get("/api/folders/export", params={"name": "Bib folder", "mode": "bibtex"})
    assert r.status_code == 200, r.text
    keys = [line.split("{", 1)[1].rstrip(",") for line in r.text.splitlines() if line.startswith("@")]
    # The folder's three pages (the subfolder's included), never the one outside.
    assert sorted(keys) == ["lovelace1843", "lovelace1843a", "lovelace1843b"]
    assert "Other paper" not in r.text
    assert r.text.startswith("% 3 entries from Bib folder, exported from Gamma")


def test_a_pinned_key_keeps_its_spelling_against_a_clash(user):
    _paper(user, "Generated one", "Bib pinned")
    _paper(user, "Pinned one", "Bib pinned", cite_key="lovelace1843")
    r = user.get("/api/folders/export", params={"name": "Bib pinned", "mode": "bibtex"})
    assert r.status_code == 200, r.text
    entries = dict(_entries(r.text))
    assert entries["Pinned one"] == "lovelace1843"
    assert entries["Generated one"] == "lovelace1843a"


def _entries(text):
    """(title, key) per entry in a .bib."""
    key = None
    for line in text.splitlines():
        if line.startswith("@"):
            key = line.split("{", 1)[1].rstrip(",")
        elif line.strip().startswith("title = {") and key:
            yield line.strip()[len("title = {"):].rstrip("},"), key


def test_a_cached_entry_is_rekeyed_to_the_pin(user):
    """A registrar renders its own BibTeX with its own key; the pin wins."""
    record = {"title": "From doi.org", "authors": ["Ada Lovelace"], "year": "1843", "source": "doi"}
    page = make_page(user, "From doi.org", {
        "folder": "Bib rekey", "meta": record, "cite_key": "mine:2026",
        "bibtex": "@article{10.1234/abc,\n  title = {From doi.org},\n  year = {1843}\n}"})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert "@article{mine:2026," in r.text
    assert "10.1234/abc" not in r.text.split("\n", 2)[1]  # the head line was rewritten


def test_a_page_with_a_record_but_no_rendering_still_exports(user):
    record = {"title": "Record only", "authors": ["Grace Hopper"], "year": "1952", "source": "crossref"}
    page = make_page(user, "Record only", {"meta": record})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert "@article{hopper1952," in r.text


def test_a_page_without_metadata_cannot_be_cited(user):
    page = make_page(user, "Just notes", {"folder": "Bib empty"})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 400
    assert r.json()["detail"] == "this page has no paper metadata to cite"
    # A folder of such pages says it about the set.
    r = user.get("/api/folders/export", params={"name": "Bib empty", "mode": "bibtex"})
    assert r.status_code == 400
    assert r.json()["detail"] == "none of these pages has paper metadata to cite"


def test_a_folder_lists_the_pages_it_left_out(user):
    from gamma import jobs
    _paper(user, "Cited paper", "Bib mixed")
    make_page(user, "Uncited notes", {"folder": "Bib mixed"})
    started = user.post("/api/jobs/export", json={"folder": "Bib mixed", "mode": "bibtex"})
    assert started.status_code == 200, started.text
    job = jobs.wait(started.json()["id"])
    assert job["state"] == "done", job["error"]
    assert job["artifact"]["name"] == "Bib mixed.bib"
    assert job["result"]["pages"] == 1
    assert job["result"]["skipped"] == [{"title": "Uncited notes", "reason": "page has no paper metadata"}]
    r = user.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200
    assert r.text.count("@article{") == 1


# --- the stable link ---------------------------------------------------------

def test_a_folder_share_link_serves_the_current_bib(user, anon):
    """What a LaTeX editor refreshes from: the token names the workspace, so
    the URL needs no session, and a page added later is simply in the file."""
    _paper(user, "Shared paper", "Bib shared")
    r = user.post("/api/share/folder", params={"name": "Bib shared"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    first = anon.get("/api/folders/export", params={"name": "Bib shared", "mode": "bibtex", "share": token})
    assert first.status_code == 200, first.text
    assert first.text.count("@article{") == 1

    _paper(user, "Added later", "Bib shared")
    again = anon.get("/api/folders/export", params={"name": "Bib shared", "mode": "bibtex", "share": token})
    assert again.status_code == 200
    assert again.text.count("@article{") == 2


def test_a_share_token_cannot_reach_another_folders_bib(user, anon):
    _paper(user, "Private paper", "Bib private")
    token = user.post("/api/share/folder", params={"name": "Bib shared"}).json()["token"]
    r = anon.get("/api/folders/export", params={"name": "Bib private", "mode": "bibtex", "share": token})
    assert r.status_code == 403

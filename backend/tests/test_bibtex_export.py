"""The BibTeX bibliography: the entry builder and its citation keys
(gamma/bibtex.py, gamma/bibtex_keys.py), the `?mode=bibtex` export of a
page and of a folder, the pinned key that survives a refetch, and the
share-link URL a LaTeX editor refreshes a folder's .bib from; the same
bibliography as BibLaTeX, RIS and CSL JSON under the same keys, and a
selection's (POST /api/bibliography).
"""

import json

import pytest

from conftest import login, make_folder, make_page, make_user
from gamma import bibtex, bibtex_keys


@pytest.fixture(scope="module")
def user(client):
    make_user("bib_user", "bib-user-pw-123")
    return login("bib_user", "bib-user-pw-123")


def _paper(c, title, folder, meta=None, cite_key=""):
    """A page carrying a cached metadata record and its rendered entry, the
    way a metadata lookup leaves one."""
    record = {"title": title, "authors": ["Ada Lovelace"], "year": "1843",
              "venue": "Notes", "source": "crossref", **(meta or {})}
    props = {"folders": [make_folder(c, folder)], "meta": record,
             "bibtex": bibtex.build_entry(record, cite_key)}
    if cite_key:
        props["cite_key"] = cite_key
    return make_page(c, title, props)


def _export_url(c, path):
    """The download of the folder at ``path`` (made where missing)."""
    return f"/api/folders/{make_folder(c, path)}/export"


def _share(c, path):
    """Share the folder at ``path``."""
    return c.post(f"/api/share/folder/{make_folder(c, path)}")


# --- the entry and its key ---------------------------------------------------

def test_default_key_is_surname_and_year():
    assert bibtex_keys.default_key({"authors": ["Ada Lovelace"], "year": "1843"}) == "lovelace1843"
    assert bibtex_keys.default_key({"authors": [], "year": "1999"}) == "paper1999"
    assert bibtex_keys.default_key({}) == "paper"


def test_clean_key_drops_what_bibtex_breaks_on():
    assert bibtex_keys.clean_key("  smith:2020-attention ") == "smith:2020-attention"
    assert bibtex_keys.clean_key("a b,c{d}e=f") == "abcdef"
    assert bibtex_keys.clean_key("   ") == ""


def test_entry_key_round_trips_through_with_key():
    entry = bibtex.build_entry({"title": "T", "authors": ["Ada Lovelace"], "year": "1843"})
    assert bibtex_keys.entry_key(entry) == "lovelace1843"
    moved = bibtex_keys.with_key(entry, "pinned:1")
    assert bibtex_keys.entry_key(moved) == "pinned:1"
    assert "title = {T}" in moved          # only the key changed
    assert bibtex_keys.with_key(entry, "") == entry  # nothing to pin


def test_pinned_keys_are_assigned_before_generated_ones():
    def rec(title, pinned):
        return {"text": f"@article{{lovelace1843,\n  title = {{{title}}}\n}}",
                "key": "lovelace1843", "pinned": pinned, "title": title}
    keys = [r["key"] for r in bibtex_keys.unique_keys([rec("A", False), rec("B", True), rec("C", False)])]
    # The pin keeps the bare key wherever it sits in the order; the generated
    # clashes move out of its way, in input order.
    assert keys == ["lovelace1843a", "lovelace1843", "lovelace1843b"]
    assert len(set(keys)) == 3


def test_suffixes_pass_z():
    records = [{"text": "@article{k,\n}", "key": "k", "pinned": False} for _ in range(28)]
    keys = [r["key"] for r in bibtex_keys.unique_keys(records)]
    assert keys[:3] == ["k", "ka", "kb"]
    assert keys[26:] == ["kz", "kaa"]
    assert len(set(keys)) == 28


def test_bibliography_has_no_timestamp():
    entry = bibtex.build_entry({"title": "T", "authors": ["Ada Lovelace"], "year": "1843"})
    records = bibtex_keys.unique_keys([{"text": entry, "key": "lovelace1843", "pinned": False}])
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
    r = user.get(_export_url(user, "Bib folder"), params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    keys = [line.split("{", 1)[1].rstrip(",") for line in r.text.splitlines() if line.startswith("@")]
    # The folder's three pages (the subfolder's included), never the one outside.
    assert sorted(keys) == ["lovelace1843", "lovelace1843a", "lovelace1843b"]
    assert "Other paper" not in r.text
    assert r.text.startswith("% 3 entries from Bib folder, exported from Gamma")


def test_a_pinned_key_keeps_its_spelling_against_a_clash(user):
    _paper(user, "Generated one", "Bib pinned")
    _paper(user, "Pinned one", "Bib pinned", cite_key="lovelace1843")
    r = user.get(_export_url(user, "Bib pinned"), params={"mode": "bibtex"})
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
        "folders": [make_folder(user, "Bib rekey")], "meta": record, "cite_key": "mine:2026",
        "bibtex": "@article{10.1234/abc,\n  title = {From doi.org},\n  journal = {Notes},\n  year = {1843}\n}"})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert "@article{mine:2026," in r.text
    assert "10.1234/abc" not in r.text.split("\n", 2)[1]  # the head line was rewritten


def test_a_page_with_a_record_but_no_rendering_still_exports(user):
    record = {"title": "Record only", "authors": ["Grace Hopper"], "year": "1952", "source": "crossref"}
    page = make_page(user, "Record only", {"meta": record})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 200, r.text
    assert "@misc{hopper1952," in r.text


def test_a_page_without_metadata_cannot_be_cited(user):
    page = make_page(user, "Just notes", {"folders": [make_folder(user, "Bib empty")]})
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "bibtex"})
    assert r.status_code == 400
    assert r.json()["detail"] == "this page has no paper metadata to cite"
    # A folder of such pages says it about the set.
    r = user.get(_export_url(user, "Bib empty"), params={"mode": "bibtex"})
    assert r.status_code == 400
    assert r.json()["detail"] == "none of these pages has paper metadata to cite"


def test_a_folder_lists_the_pages_it_left_out(user):
    from gamma import jobs
    _paper(user, "Cited paper", "Bib mixed")
    make_page(user, "Uncited notes", {"folders": [make_folder(user, "Bib mixed")]})
    started = user.post("/api/jobs/export", json={"folder": make_folder(user, "Bib mixed"), "mode": "bibtex"})
    assert started.status_code == 200, started.text
    job = jobs.wait(started.json()["id"])
    assert job["state"] == "done", job["error"]
    assert job["artifact"]["name"] == "Bib mixed.bib"
    assert job["result"]["pages"] == 1
    left_out = job["result"]["skipped"]
    assert [(p["title"], p["reason"]) for p in left_out] == [("Uncited notes", "page has no paper metadata")]
    assert left_out[0]["page_id"]
    r = user.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200
    assert r.text.count("@article{") == 1


# --- the stable link ---------------------------------------------------------

def test_a_folder_share_link_serves_the_current_bib(user, anon):
    """What a LaTeX editor refreshes from: the token names the workspace, so
    the URL needs no session, and a page added later is simply in the file."""
    _paper(user, "Shared paper", "Bib shared")
    r = _share(user, "Bib shared")
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    first = anon.get(_export_url(user, "Bib shared"), params={"mode": "bibtex", "share": token})
    assert first.status_code == 200, first.text
    assert first.text.count("@article{") == 1

    _paper(user, "Added later", "Bib shared")
    again = anon.get(_export_url(user, "Bib shared"), params={"mode": "bibtex", "share": token})
    assert again.status_code == 200
    assert again.text.count("@article{") == 2


def test_a_share_token_cannot_reach_another_folders_bib(user, anon):
    _paper(user, "Private paper", "Bib private")
    token = _share(user, "Bib shared").json()["token"]
    r = anon.get(_export_url(user, "Bib private"), params={"mode": "bibtex", "share": token})
    assert r.status_code == 403
    # a folder below the shared one is inside the share; its .bib is named by its path
    _paper(user, "Nested paper", "Bib shared/Nested")
    r = anon.get(_export_url(user, "Bib shared/Nested"), params={"mode": "bibtex", "share": token})
    assert r.status_code == 200, r.text
    assert r.text.startswith("% 1 entry from Bib shared / Nested, exported from Gamma")
    assert "Bib shared-Nested.bib" in r.headers["content-disposition"]
    page = _paper(user, "A page id", "Bib private")
    assert anon.get(f"/api/folders/{page['id']}/export", params={"share": token}).status_code == 403


# --- the review the dialog shows before exporting -----------------------------

def test_preview_pairs_every_paper_with_its_entry(user):
    _paper(user, "Preview one", "Bib preview")
    _paper(user, "Preview two", "Bib preview", cite_key="pinned:two")
    folder = make_folder(user, "Bib preview")
    make_page(user, "Preview note", {"folders": [folder]})
    r = user.get("/api/bibliography", params={"folder": folder})
    assert r.status_code == 200, r.text
    data = r.json()
    assert [(e["title"], e["key"], e["pinned"]) for e in data["entries"]] == [
        ("Preview one", "lovelace1843", False), ("Preview two", "pinned:two", True)]
    assert all(e["page_id"] and e["text"].startswith("@article{") for e in data["entries"])
    assert data["skipped"] == [{"page_id": data["skipped"][0]["page_id"], "title": "Preview note",
                                "reason": "page has no paper metadata"}]
    assert data["name"] == "Bib preview"  # the export's file name, which a Formatted download takes
    # In each format the text is the file that mode's download writes, entry
    # for entry, under the same keys; every entry carries its CSL JSON item.
    for mode in ("bibtex", "biblatex", "ris", "csljson"):
        answer = user.get("/api/bibliography", params={"folder": folder, "format": mode}).json()
        download = user.get(f"/api/folders/{folder}/export", params={"mode": mode})
        assert answer["format"] == mode and download.content == answer["text"].encode(), mode
        for entry, keyed in zip(answer["entries"], data["entries"], strict=True):
            assert entry["key"] == entry["csl"]["id"] == keyed["key"], mode
            if mode == "csljson":
                assert json.loads(entry["text"]) == [entry["csl"]]
            else:
                assert entry["text"] in download.text, mode
    assert data == user.get("/api/bibliography", params={"folder": folder, "format": "bibtex"}).json()  # the default
    assert user.get("/api/bibliography", params={"folder": folder, "format": "readable"}).status_code == 400


def test_preview_of_a_page_without_metadata_is_empty_not_an_error(user):
    """The dialog shows the page it cannot cite instead of a refusal."""
    page = make_page(user, "Nothing to cite here")
    r = user.get("/api/bibliography", params={"page_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["entries"] == []
    assert r.json()["skipped"][0]["title"] == "Nothing to cite here"


def test_preview_needs_a_target_and_honours_the_share_scope(user, anon):
    assert user.get("/api/bibliography").status_code == 400
    assert user.get("/api/bibliography", params={"page_id": "nope"}).status_code == 404
    token = _share(user, "Bib shared").json()["token"]
    assert anon.get("/api/bibliography", params={"folder": make_folder(user, "Bib shared"), "share": token}).status_code == 200
    assert anon.get("/api/bibliography", params={"folder": make_folder(user, "Bib private"), "share": token}).status_code == 403
    assert anon.get("/api/bibliography", params={"folder": make_folder(user, "Bib shared")}).status_code == 401


# --- the other citation formats ------------------------------------------------

def test_each_citation_mode_downloads_its_own_file(user):
    page = _paper(user, "Analytical engine notes", "Bib modes", meta={"issue": "2", "date": "1843-09"})
    for mode, name, media_type in (("biblatex", ".bib", "application/x-bibtex"),
                                   ("ris", ".ris", "application/x-research-info-systems"),
                                   ("csljson", ".json", "application/vnd.citationstyles.csl+json")):
        r = user.get(f"/api/pages/{page['id']}/export", params={"mode": mode})
        assert r.status_code == 200, r.text
        assert r.headers["content-type"].startswith(media_type), mode
        assert f'{name}"' in r.headers["content-disposition"], mode
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "ris"})
    assert r.content.startswith(b"TY  - JOUR\r\nTI  - Analytical engine notes\r\n")
    assert b"\r\nDA  - 1843/09//\r\n" in r.content and b"\r\nIS  - 2\r\n" in r.content
    item, = user.get(f"/api/pages/{page['id']}/export", params={"mode": "csljson"}).json()
    assert item["id"] == "lovelace1843" and item["issued"] == {"date-parts": [[1843, 9]]}
    r = user.get(f"/api/pages/{page['id']}/export", params={"mode": "biblatex"})
    assert r.text.startswith("% 1 entry, exported from Gamma\n\n@article{lovelace1843,\n")
    assert "journaltitle = {Notes}" in r.text and "date = {1843-09}" in r.text


def test_every_format_carries_the_bib_files_keys(user):
    _paper(user, "Keyed one", "Bib keyed")
    _paper(user, "Keyed two", "Bib keyed")
    _paper(user, "Keyed pinned", "Bib keyed", cite_key="lovelace1843")
    url = _export_url(user, "Bib keyed")
    bib = sorted(key for _, key in _entries(user.get(url, params={"mode": "bibtex"}).text))
    assert bib == ["lovelace1843", "lovelace1843a", "lovelace1843b"]
    biblatex = [line.split("{", 1)[1].rstrip(",") for line in user.get(url, params={"mode": "biblatex"}).text.splitlines()
                if line.startswith("@")]
    ris = [line[6:] for line in user.get(url, params={"mode": "ris"}).text.split("\r\n") if line.startswith("ID  - ")]
    csl = [item["id"] for item in user.get(url, params={"mode": "csljson"}).json()]
    assert sorted(biblatex) == sorted(ris) == sorted(csl) == bib


# --- a selection's bibliography ----------------------------------------------

def test_a_selection_comes_back_in_the_order_asked(user):
    first = _paper(user, "Selected first", "Bib selection")
    second = _paper(user, "Selected second", "Bib selection")
    third = _paper(user, "Selected third", "Bib selection")
    notes = make_page(user, "Selected notes", {"folders": [make_folder(user, "Bib selection")]})
    folder = make_folder(user, "Bib selection")
    in_folder = {e["page_id"]: e["key"] for e in user.get("/api/bibliography", params={"folder": folder}).json()["entries"]}
    asked = [third["id"], notes["id"], first["id"], third["id"]]
    data = user.post("/api/bibliography", json={"page_ids": asked}).json()
    assert [e["title"] for e in data["entries"]] == ["Selected third", "Selected first"]  # a repeat dropped
    assert [s["title"] for s in data["skipped"]] == ["Selected notes"]
    # Keys unique over the two asked, as their own .bib would have them…
    assert sorted(e["key"] for e in data["entries"]) == ["lovelace1843", "lovelace1843a"]
    # The text is their entries in that order, without the .bib's head comment.
    assert data["text"] == "\n\n".join(e["text"] for e in data["entries"]) + "\n"
    # … or, made inside the folder, the folder's .bib's keys, a page asked
    # from outside it keyed around them: one whose title sorts first, and
    # one whose pinned key is a folder page's.
    outside = _paper(user, "Aardvark outside", "Bib selection other")
    pinned = _paper(user, "Pinned outside", "Bib selection other", cite_key="lovelace1843")
    asked = [pinned["id"], third["id"], outside["id"], first["id"]]
    data = user.post("/api/bibliography", json={"page_ids": asked, "key_folder": folder}).json()
    assert [(e["page_id"], e["key"]) for e in data["entries"]] == [
        (pinned["id"], "lovelace1843c"), (third["id"], in_folder[third["id"]]),
        (outside["id"], "lovelace1843d"), (first["id"], in_folder[first["id"]])]
    assert "Selected second" not in data["text"] and data["text"].startswith("@article{lovelace1843c,")
    # Every format holds the entries in that order: what a numeric style numbers by.
    keys = [e["key"] for e in data["entries"]]
    csl = user.post("/api/bibliography", json={"page_ids": asked, "key_folder": folder, "format": "csljson"}).json()
    assert [item["id"] for item in json.loads(csl["text"])] == keys
    assert [item["id"] for e in csl["entries"] for item in json.loads(e["text"])] == keys
    ris = user.post("/api/bibliography", json={"page_ids": asked, "key_folder": folder, "format": "ris"}).json()
    assert [line[6:] for line in ris["text"].split("\r\n") if line.startswith("ID  - ")] == keys
    assert ris["text"] == "\r\n".join(e["text"] for e in ris["entries"])
    assert user.post("/api/bibliography", json={"page_ids": asked, "format": "zotero-rdf"}).status_code == 400


def test_a_selection_is_checked_page_by_page(user, anon):
    page = _paper(user, "Checked paper", "Bib checked")
    other = _paper(user, "Unshared paper", "Bib checked")
    note = user.post("/api/blocks", json={"parent_id": page["id"], "content": "a note"}).json()
    assert user.post("/api/bibliography", json={"page_ids": [page["id"], note["id"]]}).status_code == 404
    assert user.get("/api/bibliography", params={"page_id": note["id"]}).status_code == 404
    assert user.post("/api/bibliography", json={"page_ids": []}).status_code == 400
    assert user.post("/api/bibliography", json={"page_ids": [f"p{n}" for n in range(1001)]}).status_code == 413
    token = user.post(f"/api/share/{page['id']}").json()["token"]
    shared = anon.post(f"/api/bibliography?share={token}", json={"page_ids": [page["id"]]})
    assert shared.status_code == 200, shared.text
    assert [e["title"] for e in shared.json()["entries"]] == ["Checked paper"]
    assert anon.post(f"/api/bibliography?share={token}", json={"page_ids": [page["id"], other["id"]]}).status_code == 403
    assert anon.post(f"/api/bibliography?share={token}",
                     json={"page_ids": [page["id"]], "key_folder": make_folder(user, "Bib checked")}).status_code == 403

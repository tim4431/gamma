"""POST /api/metadata/update — hand-edited paper metadata."""

import json
import sqlite3
from contextlib import closing

from conftest import doc_props, guest_name, make_page, workspace_of


def test_update_saves_meta_and_rebuilds_bibtex(guest):
    page = make_page(guest, "Manual meta page")
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"],
        "meta": {
            "title": "A Hand-Entered Title",
            "authors": "Ada Lovelace, Charles Babbage",
            "venue": "Journal of Testing",
            "year": "2026",
            "volume": "7",
            "pages": "1-10",
            "doi": "10.1234/test.5678",
            "arxiv_id": "",
        },
    })
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["meta"]["title"] == "A Hand-Entered Title"
    assert data["meta"]["authors"] == ["Ada Lovelace", "Charles Babbage"]
    assert data["meta"]["source"] == "manual"
    assert data["meta"]["at"].endswith("Z")  # when it was edited
    assert data["meta"]["at"] not in data["bibtex"]  # a stamp, not a field of the entry
    assert "lovelace2026" in data["bibtex"]
    assert "Journal of Testing" in data["bibtex"]

    # persisted on the block, and a cached fetch returns the edited values
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200
    assert r.json()["cached"] is True
    assert r.json()["meta"]["title"] == "A Hand-Entered Title"


def test_update_invalidates_cached_citation(guest):
    page = make_page(guest, "Cite invalidation page",
                     properties={"meta": {"title": "Old"}, "ppt_cite": "Old cite"})
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"],
        "meta": {"title": "New title", "authors": [], "year": "2026"},
    })
    assert r.status_code == 200
    r = guest.get(f"/api/blocks/{page['id']}")
    props = r.json()["properties"]
    assert props["meta"]["title"] == "New title"
    assert "ppt_cite" not in props


def test_update_all_blank_clears_meta(guest):
    page = make_page(guest, "Clear meta page",
                     properties={"meta": {"title": "Old"}, "bibtex": "@article{x}"})
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {}})
    assert r.status_code == 200
    assert r.json()["meta"] is None
    r = guest.get(f"/api/blocks/{page['id']}")
    props = r.json()["properties"]
    assert "meta" not in props
    assert "bibtex" not in props


def test_update_missing_page_404(guest):
    r = guest.post("/api/metadata/update", json={"block_id": "nope", "meta": {"title": "x"}})
    assert r.status_code == 404


def test_verify_clears_the_flag_and_keeps_the_record(guest):
    meta = {"title": "Read by AI", "authors": ["A. Writer"], "source": "ai", "unverified": True}
    page = make_page(guest, "Verify page", properties={"meta": meta, "bibtex": "@article{x}"})
    r = guest.post("/api/metadata/verify", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["meta"] == {**meta, "unverified": False, "user_verified": True}
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["meta"]["source"] == "ai" and props["meta"]["user_verified"] is True
    assert props["bibtex"] == "@article{x}"
    row = next(p for p in guest.get("/api/metadata/status").json()["papers"] if p["id"] == page["id"])
    assert row["meta_unverified"] is False and row["meta_user_verified"] is True
    # A hand edit is a new record of the user's own: no separate mark.
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {"title": "Edited"}})
    assert "user_verified" not in r.json()["meta"]


def test_verify_needs_a_record(guest):
    page = make_page(guest, "Nothing to verify")
    assert guest.post("/api/metadata/verify", json={"block_id": page["id"]}).status_code == 409
    assert guest.post("/api/metadata/verify", json={"block_id": "nope"}).status_code == 404


def test_fetch_kicks_search_indexing_for_the_paper(guest, monkeypatch):
    """Setting a paper up (metadata fetch) starts indexing its PDF in the
    background, so search and the AI document map don't wait for the first
    search to notice it."""
    from gamma.search import indexer
    kicked = []
    monkeypatch.setattr(indexer, "index_missing_async",
                        lambda user, doc_ids: kicked.append(list(doc_ids)) or True)
    doc_id = "f" * 24
    page = make_page(guest, "Paper without a file yet", doc_props(doc_id))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 404  # no file, no ids, no AI — but the kick happened
    assert kicked == [[doc_id]]
    # A notes page (no doc_id) has nothing to index.
    note = make_page(guest, "Just notes")
    guest.post("/api/metadata/fetch", json={"block_id": note["id"]})
    assert kicked == [[doc_id]]


def test_failed_fetch_is_negative_cached_and_cleared_by_update(guest):
    # No doc_id / source_url and AI unconfigured — the lookup finds nothing.
    page = make_page(guest, "No meta anywhere")
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 404
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["meta_error"]["at"]  # marker clients use to skip auto-retry

    # Hand-editing the metadata settles the failure: the marker is removed.
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": {"title": "Filled by hand", "year": "2026"},
    })
    assert r.status_code == 200
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert "meta_error" not in props
    assert props["meta"]["title"] == "Filled by hand"


# --- a DOI or arXiv id typed by hand -------------------------------------------

# A supplement's lookup kept the DOI of a paper its references cite.
WRONG = {"title": "Geometrically asymmetric optical cavity for strong atom-photon coupling",
         "authors": ["Akio Kawasaki", "Boris Braverman"], "year": "2019", "venue": "Physical Review A",
         "volume": "99", "pages": "013437", "doi": "10.1103/PhysRevA.99.013437", "arxiv_id": "",
         "kind": "journalArticle", "source": "doi", "unverified": True}
PRL = {"title": "Near-Unitary Spin Squeezing in 171Yb", "authors": ["Boris Braverman", "Akio Kawasaki"],
       "year": "2019", "venue": "Physical Review Letters", "volume": "122", "pages": "223203",
       "doi": "10.1103/physrevlett.122.223203", "arxiv_id": "", "kind": "journalArticle", "source": "doi"}
PRL_BIB = "@article{Braverman_2019,\n  title = {Near-Unitary Spin Squeezing in ${}^{171}\\mathrm{Yb}$}\n}"


def _registry(monkeypatch, dois=None, arxiv=None):
    """doi.org and arXiv as dicts; every lookup asked is recorded."""
    from gamma.routers import metadata
    asked = []

    def fetch_doi(doi, with_bibtex=True):
        asked.append(doi)
        rec = (dois or {}).get(doi)
        return (dict(rec), PRL_BIB) if rec else (None, "")

    def fetch_arxiv(aid):
        asked.append(f"arXiv:{aid}")
        rec = (arxiv or {}).get(aid)
        return dict(rec) if rec else None
    monkeypatch.setattr(metadata, "_fetch_doi", fetch_doi)
    monkeypatch.setattr(metadata, "_fetch_arxiv", fetch_arxiv)
    return asked


def _draft(meta, **over):
    """The popover's form: the record's fields, authors comma-joined."""
    keys = ("kind", "title", "venue", "year", "volume", "pages", "doi", "arxiv_id")
    return {**{k: meta.get(k, "") for k in keys}, "authors": ", ".join(meta["authors"]), **over}


def test_a_typed_doi_fills_the_record_from_the_registry(guest, monkeypatch):
    asked = _registry(monkeypatch, {PRL["doi"]: PRL})
    page = make_page(guest, "Supplement", properties={"meta": WRONG, "bibtex": "@article{Kawasaki_2019}"})
    # Pasted as a link: the DOI in it is what is looked up.
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "cite_key": "brav19",
        "meta": _draft(WRONG, doi="https://doi.org/10.1103/PhysRevLett.122.223203")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert asked == [PRL["doi"]]
    assert data["lookup"] == {"registry": "doi", "id": PRL["doi"], "found": True}
    # The registry's fields replace the typed ones, and the record is trusted.
    meta = data["meta"]
    assert meta["title"] == PRL["title"] and meta["venue"] == "Physical Review Letters"
    assert meta["source"] == "doi" and data["source"] == "doi" and meta["unverified"] is False
    assert meta["at"]
    # The registrar's entry, under the pinned key.
    assert data["bibtex"].startswith("@article{brav19,") and "${}^{171}\\mathrm{Yb}$" in data["bibtex"]
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["meta"]["title"] == PRL["title"] and props["cite_key"] == "brav19"


def test_an_edit_that_keeps_the_identifiers_looks_nothing_up(guest, monkeypatch):
    asked = _registry(monkeypatch, {PRL["doi"]: PRL})
    page = make_page(guest, "Title fix", properties={"meta": PRL})
    # The same DOI in another case, and a title corrected by hand.
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"],
        "meta": _draft(PRL, title="Near-unitary spin squeezing in Yb-171", doi="10.1103/PhysRevLett.122.223203")})
    assert r.status_code == 200, r.text
    assert asked == [] and r.json()["lookup"] is None
    assert r.json()["meta"]["source"] == "manual"
    assert r.json()["meta"]["title"] == "Near-unitary spin squeezing in Yb-171"


def test_a_doi_nobody_knows_is_saved_as_typed(guest, monkeypatch):
    asked = _registry(monkeypatch)
    page = make_page(guest, "Unknown DOI", properties={"meta": WRONG})
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _draft(WRONG, doi="10.9999/no-such-paper")})
    assert r.status_code == 200, r.text
    data = r.json()
    assert asked == ["10.9999/no-such-paper"]
    assert data["lookup"] == {"registry": "doi", "id": "10.9999/no-such-paper", "found": False}
    assert data["meta"]["source"] == "manual" and data["meta"]["doi"] == "10.9999/no-such-paper"
    assert data["meta"]["title"] == WRONG["title"]


def test_a_typed_arxiv_id_goes_through_arxiv_and_keeps_it(guest, monkeypatch):
    preprint = {"title": "Near-unitary spin squeezing in 171Yb", "authors": ["Boris Braverman"], "year": "2019",
                "venue": "arXiv:1901.10499", "volume": "", "pages": "", "doi": "", "arxiv_id": "1901.10499",
                "kind": "preprint", "source": "arxiv"}
    asked = _registry(monkeypatch, arxiv={"1901.10499": preprint})
    page = make_page(guest, "Typed arXiv id")  # nothing found before: the form starts empty
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": {"arxiv_id": "arXiv:1901.10499v2"}})
    assert r.status_code == 200, r.text
    data = r.json()
    assert asked == ["arXiv:1901.10499"]
    assert data["lookup"] == {"registry": "arxiv", "id": "1901.10499", "found": True}
    assert data["meta"]["source"] == "arxiv" and data["meta"]["arxiv_id"] == "1901.10499"
    assert data["meta"]["kind"] == "preprint"
    assert data["bibtex"].startswith("@")  # no registrar entry: built from the record


def test_a_typed_doi_keeps_the_arxiv_id_beside_it(guest, monkeypatch):
    preprint = {**PRL, "venue": "arXiv:1901.10499", "doi": "", "arxiv_id": "1901.10499",
                "kind": "preprint", "source": "arxiv"}
    _registry(monkeypatch, {PRL["doi"]: PRL})
    page = make_page(guest, "Preprint, now published", properties={"meta": preprint})
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _draft(preprint, doi=PRL["doi"])})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["venue"] == "Physical Review Letters" and meta["arxiv_id"] == "1901.10499"


# --- the pinned citation key -------------------------------------------------

def _meta(**over):
    return {"title": "Pinned paper", "authors": "Ada Lovelace", "year": "1843", **over}


def test_pinned_key_replaces_the_generated_one(guest):
    page = make_page(guest, "Pin page")
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _meta(), "cite_key": " smith:2020-attention "})
    assert r.status_code == 200, r.text
    assert r.json()["cite_key"] == "smith:2020-attention"
    assert "@misc{smith:2020-attention," in r.json()["bibtex"]
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["cite_key"] == "smith:2020-attention"


def test_a_key_bibtex_cannot_carry_is_cleaned(guest):
    page = make_page(guest, "Dirty key page")
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _meta(), "cite_key": "a b,c{d}"})
    assert r.status_code == 200, r.text
    assert r.json()["cite_key"] == "abcd"


def test_an_unsent_key_leaves_the_pin_alone_and_an_empty_one_unpins(guest):
    page = make_page(guest, "Keep pin page")
    guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _meta(), "cite_key": "mine2026"})
    # A later edit that says nothing about the key keeps it.
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _meta(year="1844")})
    assert r.json()["cite_key"] == "mine2026"
    assert "@misc{mine2026," in r.json()["bibtex"]
    # "" unpins: the key goes back to being generated from the record.
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _meta(year="1844"), "cite_key": ""})
    assert r.json()["cite_key"] == ""
    assert "@misc{lovelace1844," in r.json()["bibtex"]
    assert "cite_key" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_clearing_the_record_unpins_too(guest):
    page = make_page(guest, "Clear pin page")
    guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _meta(), "cite_key": "mine2026"})
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {}})
    assert r.status_code == 200
    assert r.json()["cite_key"] == ""
    assert "cite_key" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


# --- the fields a citation needs (gamma/meta_fields.py) ------------------------

CITED = {**PRL, "issue": "22", "journal_abbr": "Phys. Rev. Lett.", "date": "2019-06-04",
         "url": "https://journals.aps.org/prl/abstract/10.1103/PhysRevLett.122.223203"}


def test_an_edit_from_a_tab_of_before_the_new_fields_keeps_them(guest):
    """A tab whose popover has no Issue, Journal abbr., Date or URL row
    sends none of them: the stored ones stay (the date while the year it
    sends is the date's), and the entry is built with them."""
    page = make_page(guest, "Older tab", properties={"meta": CITED})
    r = guest.post("/api/metadata/update", json={
        "block_id": page["id"], "meta": _draft(CITED, title="Near-unitary spin squeezing")})
    assert r.status_code == 200, r.text
    meta = guest.get(f"/api/blocks/{page['id']}").json()["properties"]["meta"]
    assert meta["title"] == "Near-unitary spin squeezing" and meta["source"] == "manual"
    assert {k: meta.get(k) for k in ("issue", "journal_abbr", "date", "url")} == {
        k: CITED[k] for k in ("issue", "journal_abbr", "date", "url")}
    # The year alone sent, another one: the date of the year before goes, the rest stays.
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(CITED, year="2020")})
    meta = r.json()["meta"]
    assert meta["year"] == "2020" and "date" not in meta and meta["issue"] == "22"


def test_the_date_row_sets_the_year_and_the_date(guest):
    """The Date row takes a year, a month or a day (slashes too) and sets
    the year from it; anything else is the year as typed, with no date.
    A blank field sent clears its value; an address that is no http(s) one
    is not kept."""
    page = make_page(guest, "Dates", properties={"meta": CITED})

    def save(**fields):
        r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(CITED, **fields)})
        assert r.status_code == 200, r.text
        return r.json()["meta"]

    meta = save(date="2021/3/5")
    assert (meta["year"], meta["date"]) == ("2021", "2021-03-05")
    meta = save(date="2021-03")
    assert (meta["year"], meta["date"]) == ("2021", "2021-03")
    meta = save(date="August 1, 2020")  # as a Zotero item dates it (the oracle's 4989025-RCS6CG6Z)
    assert (meta["year"], meta["date"]) == ("2020", "2020-08-01")
    meta = save(date="in press")
    assert meta["year"] == "in press" and "date" not in meta
    meta = save(date="2021", issue="", url="javascript:alert(1)")
    assert meta["year"] == "2021" and "date" not in meta and "issue" not in meta and "url" not in meta
    assert meta["journal_abbr"] == "Phys. Rev. Lett."  # not sent: kept


def test_a_save_keeps_a_last_first_name_whole(guest):
    """The Authors row joins the names with commas. A name kept "Last,
    First" (a Zotero item's "Pereira Campos" / "Jorge Fernando") or an
    organisation's with an "and" in it survives a save that leaves it
    alone, beside a name typed in; one typed so is split as it always was."""
    meta = {**PRL, "authors": ["Pereira Campos, Jorge Fernando", "Dark Energy Survey and Kilo-Degree Survey "
                               "Collaboration", "Den Hengst, Floris"]}
    page = make_page(guest, "Comma names", properties={"meta": meta})
    draft = _draft(meta, title="Renamed")
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": draft})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["authors"] == meta["authors"]
    assert "author = {Pereira Campos, Jorge Fernando and {Dark Energy Survey" in r.json()["bibtex"]
    draft = _draft(meta, authors=draft["authors"].replace("Den Hengst, Floris", "Ada Lovelace, Babbage, Charles"))
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": draft})
    assert r.json()["meta"]["authors"] == [*meta["authors"][:2], "Ada Lovelace", "Babbage", "Charles"]
    # The row as the popover writes it now, "; "-joined: a "Last, First"
    # name typed in is one author.
    draft = _draft(meta, authors="Pereira Campos, Jorge Fernando;  Babbage, Charles; ;Ada Lovelace")
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": draft})
    assert r.json()["meta"]["authors"] == ["Pereira Campos, Jorge Fernando", "Babbage, Charles", "Ada Lovelace"]


def test_an_edit_keeps_the_key_a_stored_comma_name_was_cited_by(guest):
    """A record stored before release 72 with "Den Hengst, Floris" first,
    whose cached entry was keyed "floris2020" by the name's last word: a
    title edit builds the entry again with that key, which a .tex file
    cites (``bibtex.rebuilt_key``); a new first author is keyed anew."""
    meta = {**PRL, "authors": ["Den Hengst, Floris"], "year": "2020"}
    old = "@article{floris2020,\n  title = {Old},\n  author = {Den Hengst, Floris},\n  year = {2020}\n}"
    page = make_page(guest, "Stored", properties={"meta": meta, "bibtex": old})
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(meta, title="Renamed")})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["authors"] == ["Den Hengst, Floris"] and r.json()["bibtex"].startswith("@article{floris2020,")
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(
        meta, authors="Pereira Campos, Jorge Fernando")})
    assert r.json()["bibtex"].startswith("@article{campos2020,")


def test_a_clear_is_judged_on_the_fields_sent_and_takes_the_new_ones(guest):
    """Every sent field blank clears the record, though the stored one
    holds an issue and an address the tab did not send."""
    page = make_page(guest, "Clear cited", properties={"meta": CITED, "bibtex": "@article{x}"})
    blank = {k: "" for k in ("kind", "title", "authors", "venue", "year", "volume", "pages", "doi", "arxiv_id")}
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": blank})
    assert r.status_code == 200 and r.json()["meta"] is None
    assert "meta" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_a_typed_doi_keeps_none_of_the_old_papers_fields(guest, monkeypatch):
    """The registry's record replaces the old paper's: its issue, date and
    address do not ride along onto the new one."""
    _registry(monkeypatch, {PRL["doi"]: PRL})
    old = {**WRONG, "issue": "1", "date": "2019-01-28", "url": "https://journals.aps.org/pra/abstract/x"}
    page = make_page(guest, "Wrong paper", properties={"meta": old})
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(old, doi=PRL["doi"])})
    assert r.status_code == 200, r.text
    meta = r.json()["meta"]
    assert meta["title"] == PRL["title"] and not {"issue", "date", "url"} & set(meta)


def test_the_edit_is_merged_with_what_the_page_holds_when_it_is_written(guest, monkeypatch):
    """A field the edit does not send is taken from the record as it
    stands when the edit is written, not as it was read before the lookup
    the edit waited on: an issue a Zotero pull wrote meanwhile stays."""
    from gamma.db import ws_db_path
    from gamma.routers import metadata

    page = make_page(guest, "Merged late", properties={"meta": {**PRL, "issue": "21"}})
    looked_up = metadata._typed_identifier_record

    def slow_lookup(meta, old):
        with closing(sqlite3.connect(ws_db_path(workspace_of(guest_name()), "pages.db"))) as conn:
            props = json.loads(conn.execute("SELECT properties FROM unified_blocks WHERE id = ?",
                                            (page["id"],)).fetchone()[0])
            props["meta"]["issue"] = "22"
            conn.execute("UPDATE unified_blocks SET properties = ? WHERE id = ?", (json.dumps(props), page["id"]))
            conn.commit()
        return looked_up(meta, old)
    monkeypatch.setattr(metadata, "_typed_identifier_record", slow_lookup)
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": _draft(PRL, title="Edited")})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["issue"] == "22"
    assert guest.get(f"/api/blocks/{page['id']}").json()["properties"]["meta"]["issue"] == "22"

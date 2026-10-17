"""POST /api/metadata/update — hand-edited paper metadata."""

from conftest import make_page


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
    import gamma.routers.search as search_mod
    kicked = []
    monkeypatch.setattr(search_mod, "_index_missing_async",
                        lambda user, doc_ids: kicked.append(list(doc_ids)) or True)
    doc_id = "f" * 24
    page = make_page(guest, "Paper without a file yet", {"doc_id": doc_id})
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

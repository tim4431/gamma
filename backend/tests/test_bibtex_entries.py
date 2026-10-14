"""BibTeX entries as the registries really feed them: Crossref keeps the
publisher's markup and entities in titles and venues (``<i>``, ``<sub>``,
``&amp;``) and names an organisation author by ``name``, a subtitle travels
apart from the title, arXiv's ``journal_ref`` is one free-text line while
the published record sits behind the DOI arXiv names, and the entry type
has to follow the work (a conference paper is an ``@inproceedings``).
"""

import json

from conftest import make_page
from test_metadata_verify import _paper_page

from gamma.bibtex import build_entry, clean_entry, default_key, latex_text, unique_keys
from gamma.routers import metadata
from gamma.routers.metadata import _title_in_text
from gamma.textnorm import plain_text


# --- rendering ---------------------------------------------------------------

def test_latex_text_turns_registry_markup_into_latex():
    assert latex_text("<i>Planck</i> 2018 results") == "\\emph{Planck} 2018 results"
    assert latex_text("CsPbCl<sub>3</sub> and H<sup>+</sup>") == "CsPbCl\\textsubscript{3} and H\\textsuperscript{+}"
    assert latex_text("Astronomy &amp; Astrophysics") == "Astronomy \\& Astrophysics"
    assert latex_text("A \\& B, 5% of #1") == "A \\& B, 5\\% of \\#1"   # already escaped stays escaped
    assert latex_text("<mml:math>x</mml:math> <span class=\"a\">y</span>") == "x y"
    assert latex_text("<i>unclosed") == "unclosed"   # never an unmatched brace


def test_plain_text_reads_like_the_pdf():
    assert plain_text("<i>Planck</i>\n                    2018 results") == "Planck 2018 results"
    assert plain_text("CsPbCl<sub>3</sub> Nanocrystals") == "CsPbCl3 Nanocrystals"
    assert plain_text("Nature Structural &amp; Molecular Biology") == "Nature Structural & Molecular Biology"
    assert plain_text("line<br>break") == "line break"


def test_clean_entry_fixes_a_registrar_rendering():
    raw = "@article{Yan_2013, title={CsPbCl<sub>3</sub>}, journal={Nature Structural &amp; Molecular Biology} }"
    assert clean_entry(raw) == ("@article{Yan_2013, title={CsPbCl\\textsubscript{3}}, "
                                "journal={Nature Structural \\& Molecular Biology} }")
    assert clean_entry("") == ""


def test_build_entry_escapes_values_and_writes_page_ranges():
    bib = build_entry({"title": "Salt & Light", "authors": ["A. B"], "year": "2020",
                       "venue": "J. Phys. A: Math. & Theor.", "pages": "436-444", "doi": "10.1/a&b"})
    assert "title = {Salt \\& Light}" in bib
    assert "journal = {J. Phys. A: Math. \\& Theor.}" in bib
    assert "pages = {436--444}" in bib
    assert "doi = {10.1/a&b}" in bib   # identifiers are verbatim
    assert "pages = {e12345}" in build_entry({"title": "T", "venue": "J", "pages": "e12345"})


def test_build_entry_type_follows_the_item_type():
    base = {"title": "A Title", "authors": ["Kaiming He"], "year": "2016", "doi": "10.1/x"}
    conf = build_entry({**base, "venue": "2016 IEEE CVPR", "pages": "770-778", "publisher": "IEEE",
                        "kind": "conferencePaper"})
    assert conf.startswith("@inproceedings{he2016,")
    assert "booktitle = {2016 IEEE CVPR}" in conf and "publisher = {IEEE}" in conf and "journal" not in conf
    chapter = build_entry({**base, "venue": "Handbook of X", "isbn": "9780000000002", "kind": "bookSection"})
    assert chapter.startswith("@incollection{") and "booktitle = {Handbook of X}" in chapter
    assert "isbn = {9780000000002}" in chapter
    thesis = build_entry({**base, "publisher": "MIT", "kind": "thesis"})
    assert thesis.startswith("@phdthesis{") and "school = {MIT}" in thesis
    # An older thesis record holds its university as the venue.
    assert "school = {Stanford University}" in build_entry({**base, "kind": "thesis", "venue": "Stanford University"})
    report = build_entry({**base, "publisher": "NIST", "kind": "report"})
    assert report.startswith("@techreport{") and "institution = {NIST}" in report
    assert build_entry({**base, "kind": "book", "publisher": "Wiley", "isbn": "1"}).startswith("@book{")
    assert build_entry({**base, "venue": "Nature", "kind": "journalArticle"}).startswith("@article{")
    slides = build_entry({**base, "venue": "APS March Meeting", "kind": "presentation"})
    assert slides.startswith("@misc{") and "howpublished = {APS March Meeting}" in slides
    # Neither a venue nor an arXiv id: @misc, never an @article without a journal.
    assert build_entry(base).startswith("@misc{he2016,")
    assert build_entry({**base, "kind": "journalArticle"}).startswith("@misc{he2016,")


def test_a_published_arxiv_paper_keeps_its_eprint():
    bib = build_entry({"title": "T", "authors": ["B. P. Abbott"], "year": "2016", "venue": "Physical Review Letters",
                       "volume": "116", "arxiv_id": "1602.03837", "doi": "10.1103/x"})
    assert bib.startswith("@article{abbott2016,")
    assert "journal = {Physical Review Letters}" in bib
    assert "eprint = {1602.03837}" in bib and "archivePrefix = {arXiv}" in bib
    assert "arXiv preprint" not in bib


def test_default_key_survives_an_organisation_or_no_author():
    assert default_key({"authors": ["Planck Collaboration"], "year": "2020"}) == "collaboration2020"
    assert default_key({"authors": ["", "N. Aghanim"], "year": "2020"}) == "aghanim2020"
    assert default_key({"authors": [], "year": "2020"}) == "paper2020"
    assert default_key({"authors": ["李"], "year": "1"}) == "paper1"
    assert "author = {N. Aghanim}" in build_entry({"title": "T", "authors": ["", "N. Aghanim"], "year": "2020"})


def test_unique_keys_compare_case_insensitively():
    recs = [{"text": "@article{Smith2020,\n}", "key": "Smith2020", "pinned": True},
            {"text": "@article{smith2020,\n}", "key": "smith2020", "pinned": False}]
    assert [r["key"] for r in unique_keys(recs)] == ["Smith2020", "smith2020a"]


# --- the registry records --------------------------------------------------------

CSL_PLANCK = {
    "type": "journal-article",
    "title": "<i>Planck</i>\n                    2018 results",
    "subtitle": ["VI. Cosmological parameters"],
    "author": [{"name": "Planck Collaboration", "sequence": "first"}, {"given": "N.", "family": "Aghanim"}],
    "container-title": "Astronomy &amp; Astrophysics",
    "volume": "641", "page": "A6",
    "issued": {"date-parts": [[2020, 9]]},
}
PLANCK_BIB = ("@article{2020, title={<i>Planck</i>\n   2018 results: VI. Cosmological parameters}, "
              "journal={Astronomy &amp; Astrophysics}, year={2020} }")


def _fake_doi_org(monkeypatch, csl, bib):
    def fake_get(url, accept="", timeout=20, headers=None):
        assert url.startswith("https://doi.org/")
        if "csl+json" in accept:
            return json.dumps(csl).encode()
        if "x-bibtex" in accept:
            return bib.encode()
        raise AssertionError(accept)
    monkeypatch.setattr(metadata, "_http_get", fake_get)


def test_fetch_doi_cleans_the_registry_record(monkeypatch):
    _fake_doi_org(monkeypatch, CSL_PLANCK, PLANCK_BIB)
    meta, bib = metadata._fetch_doi("10.1051/0004-6361/201833910")
    assert meta["title"] == "Planck 2018 results: VI. Cosmological parameters"
    assert meta["authors"] == ["Planck Collaboration", "N. Aghanim"]
    assert meta["venue"] == "Astronomy & Astrophysics"
    assert meta["year"] == "2020" and meta["kind"] == "journalArticle"
    assert "journal={Astronomy \\& Astrophysics}" in bib and "title={\\emph{Planck}" in bib
    assert build_entry(meta).startswith("@article{collaboration2020,")


def test_fetch_doi_survives_a_missing_date_and_a_list_title(monkeypatch):
    _fake_doi_org(monkeypatch, {"type": "article", "title": ["Attention Is All You Need"],
                                "author": [{"given": "Ashish", "family": "Vaswani"}],
                                "issued": {"date-parts": [[]]}}, "")
    meta, bib = metadata._fetch_doi("10.48550/arXiv.1706.03762")
    assert meta["title"] == "Attention Is All You Need" and meta["year"] == "" and bib == ""
    # DataCite answers in CSL's types: "article" is a preprint.
    assert meta["kind"] == "preprint"
    assert build_entry(meta).startswith("@misc{vaswani,")


def test_fetch_doi_keeps_the_publisher_of_a_conference_paper(monkeypatch):
    csl = {"type": "proceedings-article", "title": "Deep Residual Learning for Image Recognition",
           "author": [{"given": "Kaiming", "family": "He"}],
           "container-title": "2016 IEEE Conference on Computer Vision and Pattern Recognition (CVPR)",
           "publisher": "IEEE", "page": "770-778", "issued": {"date-parts": [[2016, 6]]}}
    _fake_doi_org(monkeypatch, csl, "")
    meta, _ = metadata._fetch_doi("10.1109/CVPR.2016.90")
    assert meta["kind"] == "conferencePaper" and meta["publisher"] == "IEEE"
    bib = build_entry(meta)
    assert bib.startswith("@inproceedings{he2016,")
    assert "booktitle = {2016 IEEE Conference" in bib and "pages = {770--778}" in bib


def test_crossref_search_cleans_titles_and_carries_the_type(monkeypatch):
    items = {"message": {"items": [{
        "DOI": "10.1/x", "type": "proceedings-article", "title": ["CsPbCl<sub>3</sub> &amp; friends"],
        "author": [{"name": "Some Consortium"}], "container-title": ["Proc. X"],
        "issued": {"date-parts": [[2019]]}}]}}
    asked = []
    monkeypatch.setattr(metadata, "_http_get",
                        lambda url, accept="", timeout=20, headers=None: asked.append(url) or json.dumps(items).encode())
    cands = metadata._crossref_search("anything")
    assert "select=DOI,type,title" in asked[0]
    assert cands[0]["title"] == "CsPbCl3 & friends" and cands[0]["authors"] == ["Some Consortium"]
    assert cands[0]["kind"] == "conferencePaper" and cands[0]["year"] == "2019"


def test_title_in_text_matches_a_cleaned_title_and_a_split_subtitle():
    text = "Mn(II)-Doped CsPbCl3 Nanocrystals with a long enough title\n"
    assert _title_in_text("Mn(II)-Doped CsPbCl3 Nanocrystals with a long enough title", text)
    text = "Planck 2018 results\nA&A 641, A6 (2020)\nVI. Cosmological parameters\nPlanck Collaboration"
    assert _title_in_text("Planck 2018 results: VI. Cosmological parameters", text)
    assert not _title_in_text("Planck 2018 results: X. Constraints on inflation", text)
    assert not _title_in_text("Short: VI. Cosmological parameters", text)  # main part too short to count


# --- arXiv → the published record -------------------------------------------------

ARXIV_LIGO = {
    "title": "Observation of Gravitational Waves from a Binary Black Hole Merger",
    "authors": ["The LIGO Scientific Collaboration"], "year": "2016",
    "venue": "Phys. Rev. Lett. 116, 061102 (2016)", "volume": "", "pages": "",
    "doi": "10.1103/PhysRevLett.116.061102", "arxiv_id": "1602.03837", "kind": "journalArticle",
    "source": "arxiv",
}
PRL = {
    "title": "Observation of Gravitational Waves from a Binary Black Hole Merger",
    "authors": ["B. P. Abbott", "R. Abbott"], "year": "2016",
    "venue": "Physical Review Letters", "volume": "116", "pages": "",
    "doi": "10.1103/PhysRevLett.116.061102", "arxiv_id": "", "source": "doi", "kind": "journalArticle",
}
PRL_BIB = "@article{Abbott_2016, journal={Physical Review Letters} }"


def test_published_record_replaces_the_preprints_journal_ref(monkeypatch):
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d, with_bibtex=True: (PRL, PRL_BIB))
    meta, bib = metadata._published_record(ARXIV_LIGO)
    assert meta["venue"] == "Physical Review Letters" and meta["source"] == "doi"
    assert meta["arxiv_id"] == "1602.03837"
    assert bib == PRL_BIB
    built = build_entry(meta)
    assert "journal = {Physical Review Letters}" in built and "eprint = {1602.03837}" in built


def test_published_record_keeps_the_preprint_when_the_doi_disagrees_or_is_arxivs_own(monkeypatch):
    other = dict(PRL, title="A Completely Different Article About Something Else")
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d, with_bibtex=True: (other, "@article{x}"))
    assert metadata._published_record(ARXIV_LIGO) == (ARXIV_LIGO, "")
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d, with_bibtex=True: (None, ""))
    assert metadata._published_record(ARXIV_LIGO) == (ARXIV_LIGO, "")
    asked = []
    monkeypatch.setattr(metadata, "_fetch_doi", lambda d, with_bibtex=True: asked.append(d) or (PRL, ""))
    own = dict(ARXIV_LIGO, doi="10.48550/arXiv.1602.03837")
    assert metadata._published_record(own) == (own, "")
    assert metadata._published_record(dict(ARXIV_LIGO, doi="")) == (dict(ARXIV_LIGO, doi=""), "")
    assert asked == []


def test_fetch_cites_the_published_version_of_an_arxiv_paper(guest, monkeypatch):
    text = ("arXiv:1602.03837v1 [gr-qc] 11 Feb 2016\n"
            "Observation of Gravitational Waves from a Binary Black Hole Merger\nB. P. Abbott et al.\n")
    page = _paper_page(guest, text, monkeypatch)
    monkeypatch.setattr(metadata, "_fetch_arxiv", lambda i: ARXIV_LIGO if i == "1602.03837" else None)
    monkeypatch.setattr(metadata, "_fetch_doi",
                        lambda d, with_bibtex=True: (PRL, PRL_BIB) if d == PRL["doi"] else (None, ""))
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["meta"]["venue"] == "Physical Review Letters" and body["meta"]["arxiv_id"] == "1602.03837"
    assert body["meta"]["unverified"] is False
    assert body["bibtex"] == PRL_BIB
    props = guest.get(f"/api/blocks/{page['id']}").json()["properties"]
    assert props["bibtex"] == PRL_BIB and props["meta"]["source"] == "doi"


# --- what the rest of the app prints --------------------------------------------------

def test_a_hand_edit_keeps_or_changes_the_item_type(guest):
    page = make_page(guest, "CVPR page", properties={"meta": {
        "title": "Old", "authors": ["Kaiming He"], "year": "2016", "venue": "2016 IEEE CVPR",
        "kind": "conferencePaper", "publisher": "IEEE", "source": "doi"}})
    edit = {"title": "Deep Residual Learning", "authors": ["Kaiming He"], "year": "2016",
            "venue": "2016 IEEE CVPR", "publisher": "IEEE"}
    # An edit that sends no type keeps the stored one…
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": edit})
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["kind"] == "conferencePaper"
    assert r.json()["bibtex"].startswith("@inproceedings{he2016,")
    assert "booktitle = {2016 IEEE CVPR}" in r.json()["bibtex"]
    # …the Type row's choice replaces it, and a name that is no type is ignored.
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {**edit, "kind": "journalArticle"}})
    assert r.json()["meta"]["kind"] == "journalArticle" and r.json()["bibtex"].startswith("@article{he2016,")
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {**edit, "kind": "podcast"}})
    assert r.json()["meta"]["kind"] == "journalArticle"


def test_a_type_alone_is_not_a_record(guest):
    page = make_page(guest, "Typed only", properties={"meta": {"title": "T", "kind": "book"}, "bibtex": "@book{x}"})
    r = guest.post("/api/metadata/update", json={"block_id": page["id"], "meta": {"kind": "book"}})
    assert r.status_code == 200, r.text
    assert r.json()["meta"] is None
    assert "meta" not in guest.get(f"/api/blocks/{page['id']}").json()["properties"]


def test_a_cached_fetch_answers_the_entry_the_export_prints(guest):
    page = make_page(guest, "Pinned", properties={
        "meta": {"title": "T", "authors": ["Ada Lovelace"], "year": "1843"},
        "bibtex": "@article{Lovelace_1843, title={T} }", "cite_key": "ada43"})
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is True and r.json()["bibtex"].startswith("@article{ada43,")


def test_markdown_export_renders_an_entry_for_a_record_without_one(guest):
    page = make_page(guest, "Record only", {"meta": {
        "title": "Record only", "authors": ["Grace Hopper"], "year": "1952", "venue": "J. ACM"}})
    r = guest.get(f"/api/pages/{page['id']}/export", params={"mode": "readable"})
    assert r.status_code == 200, r.text
    assert "```bibtex\n@article{hopper1952," in r.text

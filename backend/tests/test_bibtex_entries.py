"""BibTeX entries as the registries really feed them: Crossref keeps the
publisher's markup and entities in titles and venues (``<i>``, ``<sub>``,
``&amp;``) and names an organisation author by ``name``, a subtitle travels
apart from the title, arXiv's ``journal_ref`` is one free-text line while
the published record sits behind the DOI arXiv names, and the entry type
has to follow the work (a conference paper is an ``@inproceedings``).
"""

import json
import re

from conftest import make_page
from test_metadata_verify import _paper_page

from gamma import mathml
from gamma.bibtex import (build_entry, clean_key, default_key, latex_text, normalize_entry, page_entry,
                          unique_keys)
from gamma.routers import metadata
from gamma.routers.metadata import _title_in_text
from gamma.textnorm import plain_text


# --- rendering ---------------------------------------------------------------

def test_latex_text_turns_registry_markup_into_latex():
    assert latex_text("<i>Planck</i> 2018 results") == "\\emph{Planck} 2018 results"
    assert latex_text("CsPbCl<sub>3</sub> and H<sup>+</sup>") == "CsPbCl\\textsubscript{3} and H\\textsuperscript{+}"
    assert latex_text("Astronomy &amp; Astrophysics") == "Astronomy \\& Astrophysics"
    assert latex_text("A \\& B, 5% of #1") == "A \\& B, 5\\% of \\#1"   # already escaped stays escaped
    assert latex_text("<mml:math>x</mml:math> <span class=\"a\">y</span>") == "$x$ y"
    assert latex_text("<i>unclosed") == "unclosed"   # never an unmatched brace


def test_latex_text_writes_unicode_as_ascii_latex():
    assert latex_text("Fösel, Cortiñas, Vuletić, Mølmer, Ł. Wójcik") == \
        'F{\\"o}sel, Corti{\\~n}as, Vuleti{\\\'c}, M{\\o}lmer, {\\L}. W{\\\'o}jcik'
    assert latex_text("Rb–Cs at 87 µK, α ≈ 0.1, T² ≥ 2 K") == \
        "Rb--Cs at 87 $\\mu$K, $\\alpha$ $\\approx$ 0.1, T\\textsuperscript{2} $\\geq$ 2 K"
    assert latex_text("“quoted” and ‘single’ …") == "``quoted'' and `single' \\ldots{}"
    assert latex_text("x_1 costs $5^2 and T<1") == "x\\_1 costs \\$5\\^{}2 and T$<$1"
    assert latex_text("量子") == "量子"  # no LaTeX form: left for a Unicode engine
    # Already LaTeX stays as it is, so converting twice changes nothing.
    for value in ("F{\\\"o}sel $\\alpha$ \\& \\textsubscript{3} spin-$1/2$", "a {b} c"):
        assert latex_text(value) == value
    assert latex_text("a {b c") == "a b c" and latex_text("a } b") == "a b"


def test_mathml_becomes_latex_math_and_plain_text():
    crossref = ("spin-\n   <mml:math xmlns:mml=“http://www.w3.org/1998/Math/MathML” display=“inline”>\n"
                "     <mml:mrow><mml:mn>1</mml:mn><mml:mo>∕</mml:mo><mml:mn>2</mml:mn></mml:mrow>\n"
                "   </mml:math>\n   particles")
    assert latex_text(crossref) == "spin-$1/2$ particles"
    assert plain_text(crossref) == "spin-1/2 particles"
    assert mathml.to_latex("<math><msup><mi/><mn>87</mn></msup><mi>Rb</mi></math>") == "{}^{87}\\mathrm{Rb}"
    assert mathml.to_latex("<math><msub><mi>α</mi><mi>c</mi></msub><mo>×</mo>"
                           "<mfrac><mn>1</mn><mn>2</mn></mfrac></math>") == "\\alpha_c\\times\\frac{1}{2}"
    assert mathml.to_text("<math><msup><mi/><mn>87</mn></msup><mi>Rb</mi></math>") == "87Rb"


# Braverman et al., PRL 122, 223203, as Crossref and doi.org send its title:
# APS writes the isotope as an <mmultiscripts> whose mass number is a
# prescript, after the base in the markup and before it on the page.
CROSSREF_YB = ('Near-Unitary Spin Squeezing in \n<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML" '
               'display="inline"><mml:mrow><mml:mmultiscripts><mml:mrow><mml:mi>Yb</mml:mi></mml:mrow>'
               '<mml:mprescripts/><mml:none/><mml:mrow><mml:mn>171</mml:mn></mml:mrow></mml:mmultiscripts>'
               '</mml:mrow></mml:math>')


def test_isotope_prescripts_read_before_the_element():
    assert plain_text(CROSSREF_YB) == "Near-Unitary Spin Squeezing in 171Yb"
    assert latex_text(CROSSREF_YB) == "Near-Unitary Spin Squeezing in ${}^{171}\\mathrm{Yb}$"
    # The supplement prints the title alone, with no DOI of its own: only
    # the title ties the Crossref hit to it.
    pdf = "[PDF page 1]\nNear-Unitary Spin Squeezing in 171Yb (Supplemental Material)\r\nBoris Braverman,1"
    assert _title_in_text(plain_text(CROSSREF_YB), pdf)
    # Prescripts and postscripts together, each pair sub before sup.
    both = "<math><mmultiscripts><mi>U</mi><mn>2</mn><none/><mprescripts/><mn>92</mn><mn>235</mn></mmultiscripts></math>"
    assert mathml.to_latex(both) == "{}_{92}^{235}U_2"
    assert mathml.to_text(both) == "92235U2"


def test_plain_text_reads_like_the_pdf():
    assert plain_text("<i>Planck</i>\n                    2018 results") == "Planck 2018 results"
    assert plain_text("CsPbCl<sub>3</sub> Nanocrystals") == "CsPbCl3 Nanocrystals"
    assert plain_text("Nature Structural &amp; Molecular Biology") == "Nature Structural & Molecular Biology"
    assert plain_text("line<br>break") == "line break"


# doi.org's own rendering of Chase & Geremia (2008), as it really arrives:
# one line, Crossref's MathML with curly-quoted attributes, a month no style
# defines, the resolver link beside the DOI and no article number.
DOI_ORG_CHASE = (
    "@article{Chase_2008, title={Collective processes of an ensemble of spin-\n"
    "                    <mml:math xmlns:mml=“http://www.w3.org/1998/Math/MathML” display=“inline”>\n"
    "                      <mml:mrow><mml:mn>1</mml:mn><mml:mo>∕</mml:mo><mml:mn>2</mml:mn></mml:mrow>\n"
    "                    </mml:math>\n                    particles}, volume={78}, ISSN={1094-1622}, "
    "url={http://dx.doi.org/10.1103/PhysRevA.78.052101}, DOI={10.1103/physreva.78.052101}, number={5}, "
    "journal={Physical Review A}, publisher={American Physical Society (APS)}, "
    "author={Chase, Bradley A. and Geremia, J. M.}, year={2008}, month=Sept }")


def test_normalize_entry_writes_a_registrar_rendering_as_valid_bibtex():
    out = normalize_entry(DOI_ORG_CHASE, {"pages": "052101"})
    assert out == (
        "@article{Chase_2008,\n"
        "  title = {Collective processes of an ensemble of spin-$1/2$ particles},\n"
        "  author = {Chase, Bradley A. and Geremia, J. M.},\n"
        "  journal = {Physical Review A},\n"
        "  volume = {78},\n"
        "  number = {5},\n"
        "  pages = {052101},\n"
        "  publisher = {American Physical Society (APS)},\n"
        "  issn = {1094-1622},\n"
        "  year = {2008},\n"
        "  month = sep,\n"
        "  doi = {10.1103/physreva.78.052101}\n"
        "}")
    assert normalize_entry(out) == out  # a second pass changes nothing
    assert out.isascii()


def test_normalize_entry_fixes_months_pages_types_and_keys():
    def norm(fields, kind="article", key="k"):
        return normalize_entry(f"@{kind}{{{key}, " + ", ".join(fields) + "}")
    assert "month = jul" in norm(["title={T}", "journal={J}", "month=July"])
    assert "month = feb" in norm(["title={T}", "journal={J}", "month={2}"])
    assert "month = {Spring}" in norm(["title={T}", "journal={J}", "month={Spring}"])
    assert "pages = {614--619}" in norm(["title={T}", "journal={J}", "pages={614–619}"])
    # An @article without a journal fails apsrev4-2; a type no style defines.
    assert norm(["title={T}", "year={2026}"]).startswith("@misc{k,")
    assert norm(["title={T}"], kind="online").startswith("@misc{k,")
    assert norm(["title={T}"], kind="inproceedings").startswith("@misc{k,")
    assert norm(["title={T}", "journal={J}"], key="Müller_2020").startswith("@article{Muller_2020,")
    # Repeated fields keep the first; quoted values and concatenation are read.
    out = norm(['title = "A {B} c"', "title={again}", 'note = "x" # "y"', "journal={J}"])
    assert "title = {A {B} c}" in out and "again" not in out and "note = {xy}" in out
    # A resolver link for another DOI is not the entry's own: it stays.
    assert "url = {https://doi.org/10.1/other}" in norm(["title={T}", "journal={J}", "doi={10.1/x}",
                                                       "url={https://doi.org/10.1/other}"])
    assert normalize_entry("not an entry") == "" and normalize_entry("@article{k, title={open") == ""


def test_page_entry_prints_valid_bibtex_for_any_stored_text():
    meta = {"title": "Collective processes", "authors": ["Bradley A. Chase"], "year": "2008",
            "venue": "Physical Review A", "pages": "052101"}
    assert page_entry({"bibtex": DOI_ORG_CHASE, "meta": meta}).startswith("@article{Chase_2008,\n")
    assert "pages = {052101}" in page_entry({"bibtex": DOI_ORG_CHASE, "meta": meta})
    # A stored text that is no entry: built from the record instead.
    assert page_entry({"bibtex": "@article{broken, title={", "meta": meta}).startswith("@article{chase2008,")
    assert page_entry({"bibtex": DOI_ORG_CHASE, "meta": meta, "cite_key": "chase:spin"}).startswith(
        "@article{chase:spin,")


def test_clean_key_spells_a_key_in_ascii():
    assert clean_key("Fösel_2020") == "Fosel_2020"
    assert clean_key("Mølmer-Sørensen") == "Molmer-Sorensen"
    assert clean_key("量子2020") == "2020"


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
    assert "journal = {Astronomy \\& Astrophysics}" in bib and "title = {\\emph{Planck} 2018 results" in bib
    assert "pages = {A6}" in bib  # the record's page, which doi.org's rendering left out
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
        "bibtex": "@article{Lovelace_1843, title={T}, journal={Notes} }", "cite_key": "ada43"})
    r = guest.post("/api/metadata/fetch", json={"block_id": page["id"]})
    assert r.status_code == 200, r.text
    assert r.json()["cached"] is True and r.json()["bibtex"].startswith("@article{ada43,")


def test_markdown_export_renders_an_entry_for_a_record_without_one(guest):
    page = make_page(guest, "Record only", {"meta": {
        "title": "Record only", "authors": ["Grace Hopper"], "year": "1952", "venue": "J. ACM"}})
    r = guest.get(f"/api/pages/{page['id']}/export", params={"mode": "readable"})
    assert r.status_code == 200, r.text
    assert "```bibtex\n@article{hopper1952," in r.text


# --- every printed entry is valid BibTeX --------------------------------------
#
# The rules checked here are the ones a real toolchain enforces: this corpus
# (and a real 22-entry export) compiled with zero errors under pdflatex with
# plainnat and apsrev4-2 (BibTeX) and with biblatex (biber), TeX Live 2024.

AWKWARD_RECORDS = [
    {"title": "Salt & Pepper: 100% of #1 cases_x with $5 ^ 2 {unbalanced", "authors": ["Ana Ferrández"],
     "year": "2020", "venue": "J. Phys. A: Math. & Theor.", "pages": "436–444", "doi": "10.1/a_b&c"},
    {"title": "<i>Planck</i>: CsPbCl<sub>3</sub> &amp; H<sup>+</sup> at 87 µK, α ≈ 0.1 → ∞",
     "authors": ["Søren Kierkegaard", "Þóra Ægisdóttir", "Nguyễn Thị B"], "year": "2020", "venue": "A&amp;A"},
    {"title": "“Quoted” — dashes – … ﬁne ², 量子", "authors": ["Émilie du Châtelet"], "kind": "book"},
    {"title": "No venue thesis", "authors": ["A Student"], "kind": "thesis"},
    {"title": "Only a title"},
]
AWKWARD_STORED = [
    DOI_ORG_CHASE,
    "@ARTICLE{Weird_Key ü, TITLE = \"A {B} c\", Journal = jpa, month = \"Sept.\", Year = 2001, "
    "url = {https://doi.org/10.1/x}, doi = {10.1/X}, author = {Doe, J. and Roe, R.}, }",
    "@online{web2020, title={A web page}, url={https://example.org/a?b=1&c=%20#frag}, year={2020}}",
    "@inproceedings{conf, title={No booktitle}, author={X, Y}, year={2000}, month=13}",
    "@book(parens, title={Paren delimited}, author={Z}, publisher={P}, year={1999})",
]


def _assert_valid_bibtex(entry):
    from gamma.bibtex import _TYPES, _VENUE, _parse
    parsed = _parse(entry)
    assert parsed, entry
    entry_type, key, fields = parsed
    assert entry_type in _TYPES, entry
    assert key and key.isascii() and clean_key(key) == key, key
    names = dict(fields)
    assert entry_type not in _VENUE or names.get(_VENUE[entry_type]), entry
    for name, value in fields:
        depth = 0
        for c in value:
            depth += {"{": 1, "}": -1}.get(c, 0)
            assert depth >= 0, (name, value)
        assert depth == 0, (name, value)
        if name == "month":
            assert value in {"jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"} \
                or not value.isalpha(), value
        if name not in ("doi", "url", "eprint", "isbn"):
            outside_math = re.sub(r"(?<!\\)\$.*?(?<!\\)\$", "", value)
            assert not re.search(r"(?<!\\)[&%#_]", outside_math), (name, value)
            assert "<" not in outside_math and "&amp;" not in value, (name, value)
    assert normalize_entry(entry) == entry, entry  # stable: a second pass changes nothing


def test_every_built_and_stored_entry_is_valid_bibtex():
    entries = [build_entry(r) for r in AWKWARD_RECORDS] + [normalize_entry(s) for s in AWKWARD_STORED]
    for entry in entries:
        _assert_valid_bibtex(entry)
    # Only what has no LaTeX form at all is left outside ASCII.
    assert all(c.isascii() or "一" <= c <= "鿿" for e in entries for c in e)

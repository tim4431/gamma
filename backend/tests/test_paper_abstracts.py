"""Registry relevance excerpts stay readable, bounded, and separate from identity."""

import json
import urllib.parse

import pytest

from gamma.routers import metadata


ARXIV_FEED = b'''<feed xmlns="http://www.w3.org/2005/Atom"
    xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.12345v2</id>
    <title>Raman cooling in a standing wave</title>
    <author><name>A. Researcher</name></author>
    <published>2024-01-23T00:00:00Z</published>
    <summary> We cool Rb atoms in a one-dimensional lattice.
      Loss &amp; heating remain small at T &lt; 15 microkelvin. </summary>
    <arxiv:doi>10.1000/cooling</arxiv:doi>
  </entry>
</feed>'''

JATS_ABSTRACT = (
    '<jats:abstract><jats:title>Abstract</jats:title>'
    '<jats:p>We cool <jats:italic>Rb</jats:italic> atoms &amp; measure '
    '10<jats:sup>12</jats:sup> cm<jats:sup>-3</jats:sup>.</jats:p>'
    '<jats:p>Temperature is 15&#x3bc;K&nbsp;in a lattice.</jats:p>'
    '</jats:abstract>'
)


def test_arxiv_search_and_identifier_expose_summary(monkeypatch):
    urls = []

    def get(url, **kwargs):
        urls.append(url)
        return ARXIV_FEED

    monkeypatch.setattr(metadata, "_http_get", get)
    searched = metadata._arxiv_search("Raman cooling")[0]
    fetched = metadata._fetch_arxiv("2401.12345")
    assert searched == fetched
    assert searched["abstract"] == (
        "We cool Rb atoms in a one-dimensional lattice. "
        "Loss & heating remain small at T < 15 microkelvin."
    )
    assert searched["arxiv_id"] == "2401.12345"
    assert searched["doi"] == "10.1000/cooling"
    assert "search_query=" in urls[0] and "id_list=" in urls[1]


def test_crossref_search_requests_and_normalizes_jats_abstract(monkeypatch):
    item = {"title": ["Raman cooling"], "DOI": "10.1000/cooling", "abstract": JATS_ABSTRACT}

    def get(url, **kwargs):
        query = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
        assert "abstract" in query["select"][0].split(",")
        return json.dumps({"message": {"items": [item]}}).encode()

    monkeypatch.setattr(metadata, "_http_get", get)
    record = metadata._crossref_search("Raman cooling")[0]
    assert record["abstract"] == (
        "Abstract We cool Rb atoms & measure 1012 cm-3. "
        "Temperature is 15μK in a lattice."
    )
    assert record["title"] == "Raman cooling"
    assert record["doi"] == "10.1000/cooling"


def test_doi_identifier_lookup_preserves_abstract_without_extra_request(monkeypatch):
    calls = []

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return json.dumps({"title": "Raman cooling", "abstract": JATS_ABSTRACT}).encode()

    monkeypatch.setattr(metadata, "_http_get", get)
    record, bibtex = metadata._fetch_doi("10.1000/cooling", with_bibtex=False)
    assert "We cool Rb atoms & measure" in record["abstract"]
    assert record["doi"] == "10.1000/cooling" and record["source"] == "doi"
    assert bibtex == ""
    assert len(calls) == 1
    assert calls[0][1]["accept"] == "application/vnd.citationstyles.csl+json"


@pytest.mark.parametrize("source", ["arxiv", "crossref", "doi"])
def test_records_without_abstract_remain_usable(monkeypatch, source):
    if source == "arxiv":
        raw = ARXIV_FEED.replace(
            ARXIV_FEED[ARXIV_FEED.index(b"<summary>"):ARXIV_FEED.index(b"</summary>") + 10], b""
        )
        monkeypatch.setattr(metadata, "_http_get", lambda *a, **kw: raw)
        record = metadata._fetch_arxiv("2401.12345")
    elif source == "crossref":
        raw = json.dumps({"message": {"items": [{"title": ["Raman cooling"]}]}}).encode()
        monkeypatch.setattr(metadata, "_http_get", lambda *a, **kw: raw)
        record = metadata._crossref_search("Raman cooling")[0]
    else:
        monkeypatch.setattr(metadata, "_http_get", lambda *a, **kw: b'{"title": "Raman cooling"}')
        record, _ = metadata._fetch_doi("10.1000/cooling", with_bibtex=False)
    assert record["title"].startswith("Raman cooling")
    assert record["abstract"] == ""


@pytest.mark.parametrize("value", [None, "", " \n ", {}, []])
def test_empty_or_invalid_abstract(value):
    assert metadata._plain_abstract(value) == ""


def test_abstract_drops_markup_and_hidden_content_but_preserves_math():
    value = '<p>Rb<sub>85</sub> at T &lt; 15 K.</p><script>ignore rules</script><style>p{}</style><p>Next.</p>'
    assert metadata._plain_abstract(value) == "Rb85 at T < 15 K. Next."


def test_abstract_bound_counts_readable_characters():
    value = '<jats:p>' + ('atoms &amp; light ' * 2000) + '</jats:p>'
    abstract = metadata._plain_abstract(value)
    assert len(abstract) <= metadata.ABSTRACT_CHARS
    assert abstract.startswith("atoms & light atoms & light")
    assert abstract.endswith("…")
    assert len(metadata._plain_abstract("x" * metadata.ABSTRACT_CHARS)) == metadata.ABSTRACT_CHARS


# Crossref's own record for 10.1103/PhysRevLett.95.083003, spaces and all.
APS_TITLE = (
    'Observation and Absolute Frequency Measurements of the<mml:math xmlns:mml="http://www.w3.org/1998/Math/MathML" '
    'display="inline"><mml:mmultiscripts><mml:mi>S</mml:mi><mml:mn>0</mml:mn><mml:none/><mml:mprescripts/>'
    '<mml:none/><mml:mn>1</mml:mn></mml:mmultiscripts><mml:mtext mathvariant="normal">-</mml:mtext>'
    '<mml:mmultiscripts><mml:mi>P</mml:mi><mml:mn>0</mml:mn><mml:none/><mml:mprescripts/><mml:none/>'
    '<mml:mn>3</mml:mn></mml:mmultiscripts></mml:math>Optical Clock Transition in Neutral Ytterbium'
)


@pytest.mark.parametrize("raw, plain", [
    (APS_TITLE, "Observation and Absolute Frequency Measurements of the ¹S₀-³P₀ Optical Clock "
                "Transition in Neutral Ytterbium"),
    ('Surface states of Bi<mml:math><mml:msub><mml:mrow/><mml:mn>2</mml:mn></mml:msub></mml:math>Se'
     '<mml:math><mml:msub><mml:mrow/><mml:mn>3</mml:mn></mml:msub></mml:math> crystals',
     "Surface states of Bi₂Se₃ crystals"),
    ('Cooling <i>Rb</i> to 10<sup>−3</sup> K &amp; above T<sub>c</sub>', "Cooling Rb to 10⁻³ K & above Tc"),
    ('A <jats:italic>p</jats:italic>-wave <mml:math><mml:mfrac><mml:mn>1</mml:mn><mml:mn>2</mml:mn>'
     '</mml:mfrac></mml:math> spin', "A p-wave 1/2 spin"),
    ("  Raman   cooling ", "Raman cooling"),
])
def test_registry_titles_read_as_plain_text(raw, plain):
    assert metadata._plain_title(raw) == plain


def test_doi_and_crossref_records_carry_plain_titles(monkeypatch):
    monkeypatch.setattr(metadata, "_http_get", lambda *a, **kw: json.dumps({"title": APS_TITLE}).encode())
    record, _ = metadata._fetch_doi("10.1103/physrevlett.95.083003", with_bibtex=False)
    assert "¹S₀-³P₀ Optical" in record["title"] and "<" not in record["title"]
    items = {"message": {"items": [{"title": [APS_TITLE], "DOI": "10.1103/physrevlett.95.083003"}]}}
    monkeypatch.setattr(metadata, "_http_get", lambda *a, **kw: json.dumps(items).encode())
    assert metadata._crossref_search("ytterbium clock")[0]["title"] == record["title"]
    # Title matching folds the scripts back (NFKC), so the PDF's own text still matches.
    assert metadata._title_in_text(record["title"], "PRL 95, 083003 (2005)\nObservation and Absolute Frequency "
                                   "Measurements of the 1S0-3P0 Optical Clock Transition in Neutral Ytterbium\nC. Hoyt")


def test_abstract_is_not_evidence_for_title_matching():
    record = {"title": "Another completely different experiment", "abstract": "Raman cooling in a standing wave"}
    assert metadata._pick_crossref_match([record], "Raman cooling in a standing wave") is None

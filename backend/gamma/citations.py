"""The citation formats beside BibTeX: CSL JSON, RIS and BibLaTeX, built from
a page's paper record (``properties.meta``, docs/dev/paper_metadata.md).

CSL JSON is what citeproc-js renders every citation style from, in the
browser (frontend/src/citations/), and what Pandoc and Quarto read; RIS is
what EndNote, Mendeley and the journals' sites exchange; BibLaTeX is
biber's dialect of BibTeX. The mappings are Zotero's (its RIS, BibLaTeX and
CSL JSON translators, docs/research/citations.md), so a file Gamma writes
reads in Zotero as Zotero's own would. The BibTeX entry itself stays in
``bibtex`` (``page_entry``): its key is the key every format here is
handed, so a document cites one key from any of the files
(``routers/export.py`` ``_CitationBuilder``).

``record`` is the record these read: the stored one with its full date
only while it agrees with the year and the address a citation links. The
record keeps one string per author; ``names`` splits it, the same way for
every format. A preprint on arXiv is cited as Zotero's arXiv translator
saves one: arXiv its publisher, ``arXiv:<id>`` its number, its abstract
page its address and DataCite's ``10.48550/arXiv.<id>`` its DOI when it
has none of its own. Pure, standard library only.
"""

import json
import re

from . import bibtex_format, item_types, meta_fields, names

# Zotero's item types as CSL's (zotero-schema ``csl.types``); a preprint is
# an ``article``.
_CSL_TYPES = {"journalArticle": "article-journal", "conferencePaper": "paper-conference", "preprint": "article",
              "bookSection": "chapter", "book": "book", "thesis": "thesis", "report": "report",
              "presentation": "speech", "document": "document"}
# ... and as RIS's (Zotero's RIS.js ``exportTypeMap``; a preprint has none
# there and goes out as its default, GEN).
_RIS_TYPES = {"journalArticle": "JOUR", "conferencePaper": "CONF", "preprint": "GEN", "bookSection": "CHAP",
              "book": "BOOK", "thesis": "THES", "report": "RPRT", "presentation": "SLIDE", "document": "GEN"}
# The tags in the order Zotero's RIS export writes them, ID (the citation
# key, as RIS readers take it) before the end of the record.
_RIS_ORDER = ("TY", "TI", "AU", "T2", "C3", "DA", "PY", "DO", "VL", "IS", "SP", "EP", "J2", "PB", "SN", "UR",
              "ID")
_YEAR_RE = re.compile(r"\d{4}")


def _text(value) -> str:
    """A field's value on one line: RIS ends a value at its line, and a
    title pasted with its line breaks reads the same without them."""
    return " ".join(str(value or "").split())


def record(props: dict) -> dict | None:
    """The record a citation reads, from a page's properties: its paper
    record (``properties.meta``) with the full ``date`` only while it
    agrees with the year (``meta_fields.date_agrees``: a date of another
    year is stale), and as ``url`` the record's own address, else the
    page's ``web_url`` (the tab a clip came from), an http(s) address
    only (``meta_fields.clean_url``). None for a page without a record."""
    props = props or {}
    meta = props.get("meta")
    if not isinstance(meta, dict):
        return None
    out = {name: value for name, value in meta.items() if name not in ("date", "url")}
    date = meta_fields.date_agrees(meta)
    url = meta_fields.clean_url(meta.get("url")) or meta_fields.clean_url(props.get("web_url"))
    if date:
        out["date"] = date
    if url:
        out["url"] = url
    return out


def _work(rec: dict) -> dict:
    """What every format cites from a record, each value on one line: its
    item type as cited (``kind``: a journal article whose only venue is
    arXiv is a preprint, as ``bibtex.build_entry`` treats it), the venue
    by what it is for that type (``container``: a journal, proceedings or
    the book a chapter is in; ``event``: a presentation's meeting;
    ``series``: a report's), the publisher (a thesis's university and a
    report's institution, ``item_types.publisher``; a preprint's
    repository), the date's parts and the identifiers."""
    kind = item_types.item_type(rec)
    venue = _text(rec.get("venue"))
    arxiv_id = _text(rec.get("arxiv_id"))
    arxiv_venue = not venue or venue.lower().startswith("arxiv")
    if kind == "journalArticle" and arxiv_id and arxiv_venue:
        kind = "preprint"
    publisher = _text(item_types.publisher(rec))
    work = {"kind": kind, "title": _text(rec.get("title")),
            "authors": [_text(a) for a in rec.get("authors") or [] if _text(a)],
            "container": "", "event": "", "series": "", "publisher": publisher, "number": "",
            "volume": _text(rec.get("volume")), "issue": _text(rec.get("issue")),
            "pages": _text(rec.get("pages")), "abbr": _text(rec.get("journal_abbr")),
            "year": _text(rec.get("year")), "date": meta_fields.date_agrees(rec),
            "doi": _text(rec.get("doi")), "isbn": _text(rec.get("isbn")),
            "url": meta_fields.clean_url(rec.get("url")), "arxiv_id": arxiv_id}
    if kind in ("journalArticle", "conferencePaper", "bookSection", "document"):
        work["container"] = venue
    elif kind == "presentation":
        work["event"] = venue
    elif kind == "report" and str(rec.get("publisher") or "").strip():
        work["series"] = venue  # (without a publisher of its own, the venue is its institution)
    elif kind == "preprint":
        if arxiv_id and arxiv_venue:
            work["publisher"] = work["publisher"] or "arXiv"
            work["number"] = f"arXiv:{arxiv_id}"
            work["url"] = work["url"] or f"https://arxiv.org/abs/{arxiv_id}"
            work["doi"] = work["doi"] or "10.48550/arXiv." + re.sub(r"v\d+$", "", arxiv_id)
        elif not arxiv_venue:
            work["publisher"] = work["publisher"] or venue  # its repository: bioRxiv, SSRN
    # A thesis or a book has no venue of its own: what an older record kept
    # there is its university (``item_types.publisher``) or nothing cited.
    return work


# --- CSL JSON -----------------------------------------------------------------

# citeproc-js's particle patterns (CSL.PARTICLE_FAMILY_REGEXP and
# PARTICLE_GIVEN_REGEXP, the latter matched against the given name
# reversed): a word and what ends it, then the rest.
_FAMILY_PARTICLE_RE = re.compile(r"^([^ ]+(?:-|ʻ|’| |') *)(.+)$", re.S)
_GIVEN_PARTICLE_RE = re.compile(r"^([^ ]+(?:ʻ |’ | |' ) *)(.+)$", re.S)


def _particles(value: str, given: bool = False) -> tuple[str, list[str]]:
    """``(rest, particles)``: the leading particles of a family name, or
    with ``given`` the trailing ones of a given name, as citeproc-js's
    ``parseParticles`` takes them: words whose first letter is lowercase
    ("van der" of "van der Berg", "d'" of "d'Alembert", "van" of "Frank
    van"), up to the first that is not."""
    orig, text = value, value[::-1] if given else value
    rex = _GIVEN_PARTICLE_RE if given else _FAMILY_PARTICLE_RE
    found = []
    m = rex.match(text)
    while m:
        word = m.group(1)[::-1] if given else m.group(1)
        first = re.sub(r"^[-'ʻ’\s]*(.).*$", r"\1", word, flags=re.S)
        if first.upper() == first:
            break
        if given:
            found.append(orig[-len(word):])
            orig = orig[:-len(word)]
        else:
            found.append(orig[:len(word)])
            orig = orig[len(word):]
        text = m.group(2)
        m = rex.match(text)
    if given:
        found.reverse()
        for i in range(1, len(found)):
            if found[i][:1] == " ":
                found[i - 1] += " "
        return orig[:len(text)], [p[1:] if p[:1] == " " else p for p in found]
    return orig[-len(text):], found


def _csl_name(name: str) -> dict:
    """An author as a CSL name: family and given name (``names.name_parts``),
    or a ``literal`` for an organisation or a one-word name, which a
    style prints as it is. Particles are split off as Zotero's CSL JSON
    splits them (``itemToCSLJSON``, which runs citeproc-js's
    ``parseParticles`` on every name with both parts, so the engine reads
    names as given): "van der" of "Jan van der Berg" is the
    ``non-dropping-particle``, which a style may demote or sort under
    "Berg", and a lowercase word ending the given name the
    ``dropping-particle``."""
    last, given, suffix = names.name_parts(name)
    if not given:
        return {"literal": last}
    out = {"family": last, "given": given}
    if last[:1] == '"' and last[-1:] == '"' and len(last) > 1:
        out["family"] = last[1:-1]  # a quoted surname is not parsed (Zotero's rule)
    else:
        out["family"], found = _particles(last)
        ndp = "".join(found)
        ndp = ndp.strip() + (" " if ndp[-1:] == " " and ndp.strip()[-1:] in ("'", "’") else "")
        if not suffix and "," in given:  # "Smith, John, Esq.": what follows the comma
            given, _, suffix = (part.strip() for part in given.partition(","))
        out["given"], found = _particles(given, given=True)
        dp = "".join(found).strip()
        if ndp:
            out["non-dropping-particle"] = ndp
        if dp:
            out["dropping-particle"] = dp
    if suffix:
        out["suffix"] = suffix
    return out


def _csl_date(work: dict) -> dict | None:
    """``issued`` from the agreeing date, else the year; a year that is no
    number ("in press") as the ``literal`` CSL prints verbatim."""
    if work["date"]:
        return {"date-parts": [[int(part) for part in work["date"].split("-")]]}
    if _YEAR_RE.fullmatch(work["year"]):
        return {"date-parts": [[int(work["year"])]]}
    return {"literal": work["year"]} if work["year"] else None


def csl_item(rec: dict, key: str) -> dict:
    """The record as one CSL JSON item (CSL data schema 1.0.2), ``id`` and
    ``citation-key`` the citation key. Every field the record holds is
    kept, its address too: whether a style prints it (Zotero leaves the URL
    of a journal article with pages out) is the renderer's choice, never
    the file's."""
    work = _work(rec)
    pages = bibtex_format.PAGE_RANGE_RE.sub(r"\1-\2", work["pages"])
    # A meeting under both its names: CSL 1.0.2's `event-title` and the
    # `event` it replaced, which older styles still read (APSA and ASA
    # among the bundled ones; citeproc-js 2.4 maps neither to the other).
    item = {"id": key, "type": _CSL_TYPES.get(work["kind"], "document"), "title": work["title"],
            "author": [_csl_name(a) for a in work["authors"]], "container-title": work["container"],
            "container-title-short": work["abbr"] if work["container"] else "",
            "collection-title": work["series"], "event-title": work["event"], "event": work["event"],
            "publisher": work["publisher"],
            "number": work["number"], "volume": work["volume"], "issue": work["issue"], "page": pages,
            "issued": _csl_date(work), "DOI": work["doi"], "ISBN": work["isbn"], "URL": work["url"],
            "citation-key": key}
    return {name: value for name, value in item.items() if value or name in ("id", "type")}


def csl_json(items: list[dict]) -> str:
    """CSL JSON items as the file holds them: one array, in the order given,
    two-space indented, the characters as they are."""
    return json.dumps(list(items), indent=2, ensure_ascii=False) + "\n"


# --- RIS ----------------------------------------------------------------------

def _ris_name(name: str) -> str:
    """An author as RIS writes one, ``Last, First`` (``Last, First, Jr.``
    with a suffix); an organisation as it is."""
    last, given, suffix = names.name_parts(name)
    return ", ".join(part for part in (last, given, suffix) if part)


def ris_entry(rec: dict, key: str) -> str:
    """The record as one RIS record: the tags Zotero's RIS export writes for
    its type (the venue as ``T2``, a conference paper's as its proceedings'
    ``C3``; ``PY`` the year, ``DA`` the date as ``YYYY/MM/DD/``; pages
    as ``SP``/``EP``), ``ID`` the citation key, every value on one line,
    ending ``ER  - ``. Lines end in CR LF, as RIS readers expect."""
    work = _work(rec)
    kind = work["kind"]
    tags = {"TY": [_RIS_TYPES.get(kind, "GEN")], "TI": [work["title"]],
            "AU": [_ris_name(a) for a in work["authors"]]}
    if kind == "conferencePaper":
        tags["C3"] = [work["container"]]
    else:
        tags["T2"] = [work["container"] or work["event"] or work["series"]]
    # DA as Zotero's RIS export writes every date: "2008/11/03/", and
    # "2021///" for a year alone.
    if work["date"]:
        year, month, *day = work["date"].split("-")
        tags["DA"] = [f"{year}/{month}/{day[0] if day else ''}/"]
    if _YEAR_RE.fullmatch(work["year"]):
        tags.setdefault("DA", [f"{work['year']}///"])
        tags["PY"] = [work["year"]]
    elif work["year"]:
        tags["DA"] = [f"///{work['year']}"]  # the date's "other" part: Zotero reads it back as the date
    # A preprint's arXiv number has no tag, as in Zotero's export: RIS
    # readers take M1 for an issue. UR and DO name the preprint.
    first, _, last = bibtex_format.PAGE_RANGE_RE.sub(r"\1\n\2", work["pages"]).partition("\n")
    tags.update(DO=[work["doi"]], VL=[work["volume"]], IS=[work["issue"]], SP=[first], EP=[last],
                J2=[work["abbr"]], PB=[work["publisher"]], SN=[work["isbn"]], UR=[work["url"]], ID=[key])
    lines = [f"{tag}  - {_text(value)}" for tag in _RIS_ORDER for value in tags.get(tag, ()) if _text(value)]
    return "\r\n".join(lines + ["ER  - "]) + "\r\n"


def ris_file(entries: list[str]) -> str:
    """The .ris file: the records one after another, a blank line apart."""
    return "\r\n".join(entries)


# --- BibLaTeX -----------------------------------------------------------------

def _bib_name(name: str) -> str:
    """An author as biber reads one unambiguously, as Zotero's BibLaTeX
    export writes it: ``Last, First``, ``Last, Jr., First`` with a suffix
    (biber would take "King, Jr." for a given name "Jr."), and an
    organisation in braces, one name (else "ATLAS Collaboration" is a
    person, and an "and" inside a name splits it in two). The BibTeX entry
    keeps the record's strings as it always has."""
    last, given, suffix = names.name_parts(name)
    if not given:
        return "{" + last + "}"
    return f"{last}, {suffix}, {given}" if suffix else f"{last}, {given}"


def biblatex_entry(rec: dict, key: str) -> str:
    """The record as a BibLaTeX entry, written by
    ``bibtex_format.format_entry`` with BibLaTeX's own types, venue fields
    and order (``bibtex_format.BIBLATEX``), as Zotero's BibLaTeX export
    maps it: a journal article is an
    ``@article`` (``journaltitle``, its abbreviation ``shortjournal``, the
    issue as ``number``); a preprint on arXiv an ``@online`` with
    ``eprinttype = arxiv``; a thesis and a report ``@thesis`` and
    ``@report`` with their ``type`` and ``institution``; a conference paper,
    a chapter and a book as in BibTeX, with their volume (and ISBN); a
    presentation ``@unpublished`` with its meeting as ``howpublished``;
    documents and other preprints ``@misc`` with the venue, else the
    publisher, as ``howpublished``. The authors are
    split (``_bib_name``); the date is ISO (``date``), a year that is no
    number the ``year`` as written. An arXiv id always adds ``eprint``, as
    ``bibtex.build_entry`` does."""
    work = _work(rec)
    kind = work["kind"]
    fields = {"title": work["title"], "author": " and ".join(_bib_name(a) for a in work["authors"])}
    if kind == "book":
        entry = "book"
        fields.update(volume=work["volume"], publisher=work["publisher"], isbn=work["isbn"])
    elif kind == "conferencePaper":
        entry = "inproceedings"
        fields.update(booktitle=work["container"], volume=work["volume"], pages=work["pages"],
                      publisher=work["publisher"], isbn=work["isbn"])
    elif kind == "bookSection":
        entry = "incollection"
        fields.update(booktitle=work["container"], volume=work["volume"], pages=work["pages"],
                      publisher=work["publisher"], isbn=work["isbn"])
    elif kind == "thesis":
        entry = "thesis"
        fields.update(type="phdthesis", institution=work["publisher"])
    elif kind == "report":
        entry = "report"
        fields.update(type="techreport", institution=work["publisher"], pages=work["pages"])
    elif kind == "journalArticle" and work["container"]:
        entry = "article"
        fields.update(journaltitle=work["container"], shortjournal=work["abbr"], volume=work["volume"],
                      number=work["issue"], pages=work["pages"])
    elif kind == "preprint" and work["number"]:
        entry = "online"
    elif kind == "presentation":
        entry = "unpublished"  # Zotero's type for a talk; the unpublished driver prints its meeting
        fields["howpublished"] = work["event"]
    else:
        entry = "misc"
        # (a preprint's venue is its repository and a document's may be its
        # publisher, ``_work``'s publisher)
        fields["howpublished"] = work["container"] or work["event"] or work["publisher"]
    if work["arxiv_id"]:
        fields.update(eprint=work["arxiv_id"], eprinttype="arxiv")
    if work["date"] or _YEAR_RE.fullmatch(work["year"]):
        fields["date"] = work["date"] or work["year"]
    else:
        fields["year"] = work["year"]
    # The record's own DOI and address: an arXiv preprint's derived ones
    # (``_work``) would print beside its eprint.
    fields.update(doi=_text(rec.get("doi")), url=meta_fields.clean_url(rec.get("url")))
    return bibtex_format.format_entry(entry, key, fields, bibtex_format.BIBLATEX)

"""A page's BibTeX entry, and the .bib file of many.

``build_entry`` writes the entry of a stored metadata record
(``properties.meta``), ``normalize_entry`` writes a stored entry out again
as valid BibTeX, and ``page_entry`` is a page's entry, the one or the
other. ``bibliography`` lays entries out as one `.bib` file.

`routers/metadata.py` builds the single entry cached on a page block
(`properties.bibtex`). `routers/export.py` (`_CitationBuilder`,
`?mode=bibtex`) concatenates the entries of a page or a folder into the
bibliography a LaTeX document cites — see
[import_export.md](../../docs/dev/import_export.md). `ai_tools/library.py`
(`cite`) and the Zotero import (`routers/imports.py`) build entries the
same way. Every surface that prints a page's entry — the .bib export and
its review (which the share popover's Copy BibTeX reads), `cite`, the
Markdown, Obsidian and Notion exports — takes it from `page_entry`, so the
key the chat shows is the key the file carries.

Its parts are modules of their own: ``bibtex_format`` writes an entry in
either dialect (the BibLaTeX entry, ``citations.biblatex_entry``, too),
``bibtex_parse`` reads a stored one, ``bibtex_keys`` makes the key and
keeps a bibliography's keys unique, ``bibtex_names`` writes the name
lists, and ``bibtex_latex`` and ``bibtex_case`` the text values.
"""

from . import item_types, meta_fields
from .bibtex_format import format_entry
from .bibtex_keys import clean_key, default_key, pinned_key, with_key
from .bibtex_names import authors_of, bibtex_name, brace_organisations
from .bibtex_parse import parse_entry


def normalize_entry(text: str, record: dict | None = None) -> str:
    """A stored entry (any layout: doi.org's one-line rendering, an entry an
    older Gamma cached, one restored from an export) written out as valid
    BibTeX by `bibtex_format.format_entry`, its key kept. A field the entry
    lacks but the metadata ``record`` has is added: `pages` (doi.org's
    BibTeX leaves out an APS article number, which the record carries). An
    organisation among the authors or editors is braced whole
    (``bibtex_names.brace_organisations``). "" when the text is not a
    well-formed entry; the caller then builds one from the record."""
    parsed = parse_entry(text)
    if not parsed:
        return ""
    entry_type, key, raw = parsed
    fields: dict = {}
    for name, value in raw:
        fields.setdefault(name.lower(), value)
    if record and not str(fields.get("pages") or "").strip() and str(record.get("pages") or "").strip():
        fields["pages"] = record["pages"]
    for name in ("author", "editor"):
        if fields.get(name):
            fields[name] = brace_organisations(str(fields[name]), (record or {}).get("authors") or ())
    return format_entry(entry_type, key or default_key(record or {}), fields)


def page_entry(props: dict) -> str:
    """The BibTeX entry of a page as every surface prints it — the .bib
    export and its review, the AI ``cite`` tool, the Markdown, Obsidian and
    Notion exports: the cached entry (``properties.bibtex``) normalized, or,
    failing that, one built from the metadata record, keyed by the pinned
    key when there is one. The cached entry may predate the pin or carry a
    registrar's own key, so the pin is applied here rather than trusted to
    be in the text. "" when the page has neither an entry nor a record."""
    props = props or {}
    meta = props.get("meta") if isinstance(props.get("meta"), dict) else None
    pin = pinned_key(props)
    text = normalize_entry(props.get("bibtex") or "", meta) or (build_entry(meta, pin) if meta else "")
    return with_key(text, pin) if pin else text


def build_entry(meta: dict, key: str = "") -> str:
    """The BibTeX entry for a metadata record (`properties.meta`), keyed by
    ``key`` when one is pinned and by ``bibtex_keys.default_key`` otherwise.

    The entry type follows the record's item type (``item_types``), as
    Zotero's BibTeX export maps it: a conference paper is an
    `@inproceedings` and a chapter an `@incollection`, both with the venue as
    `booktitle`; a book is a `@book` with publisher and ISBN; the three with
    their volume (and a conference paper its ISBN); a thesis a
    `@phdthesis` with its `school`; a report a `@techreport` with its
    `institution`. A journal article or a preprint is an `@article`: the
    venue is the journal, or a bare arXiv id makes it
    `arXiv preprint arXiv:<id>` (Zotero writes a preprint as `@misc`; this
    form is kept because every cached arXiv entry already has it). An
    article with neither, slides and other documents are `@misc`, the venue
    (else the publisher) as `howpublished`. An arXiv id always adds
    `eprint`/`archivePrefix`, so a published paper read from arXiv cites
    the journal and keeps the preprint. An article carries its issue as
    `number`; every entry its month while the record's date agrees with
    its year (``meta_fields.date_agrees``); a `@misc`, a thesis and a
    report the record's address as `url`. The authors are the record's
    strings, an organisation braced whole (``bibtex_names.bibtex_name``). A
    record without those fields gives the entry it always gave, byte for
    byte. `bibtex_format.format_entry` writes it."""
    kind = item_types.item_type(meta)
    venue = str(meta.get("venue") or "").strip()
    arxiv_id = str(meta.get("arxiv_id") or "").strip()
    publisher = item_types.publisher(meta)
    pages = meta.get("pages", "")
    isbn = meta.get("isbn", "")
    volume = meta.get("volume", "")
    fields = {"title": meta.get("title", ""), "author": " and ".join(bibtex_name(a) for a in authors_of(meta))}
    if kind == "book":
        entry = "book"
        fields.update(volume=volume, publisher=publisher, isbn=isbn)
    elif kind == "conferencePaper":
        entry = "inproceedings"
        fields.update(booktitle=venue, volume=volume, pages=pages, publisher=publisher, isbn=isbn)
    elif kind == "bookSection":
        entry = "incollection"
        fields.update(booktitle=venue, volume=volume, pages=pages, publisher=publisher, isbn=isbn)
    elif kind == "thesis":
        entry = "phdthesis"
        fields["school"] = publisher
    elif kind == "report":
        entry = "techreport"
        fields.update(institution=publisher, pages=pages)
    elif kind in ("journalArticle", "preprint") and venue and not venue.lower().startswith("arxiv"):
        entry = "article"
        fields.update(journal=venue, volume=volume, number=meta.get("issue", ""), pages=pages)
    elif kind in ("journalArticle", "preprint") and arxiv_id:
        entry = "article"
        fields["journal"] = f"arXiv preprint arXiv:{arxiv_id}"
    else:
        entry = "misc"
        fields["howpublished"] = venue or publisher  # a document's publisher, when it names no venue
    if arxiv_id:
        fields.update(eprint=arxiv_id, archivePrefix="arXiv")
    fields["month"] = meta_fields.date_agrees(meta)[5:7]  # "2020-03-05" → "03" → `mar`
    if entry in ("misc", "phdthesis", "techreport"):
        fields["url"] = meta_fields.clean_url(meta.get("url"))
    fields.update(year=meta.get("year", ""), doi=meta.get("doi", ""))
    return format_entry(entry, clean_key(key) or default_key(meta), fields)


def bibliography(records: list[dict], source: str = "", head: bool = True) -> str:
    """The `.bib` file for ``records`` (``bibtex_keys.unique_keys`` order):
    a comment naming what was exported, then the entries, one blank line
    apart. No timestamp — an unchanged library re-exports byte-identically,
    so a bibliography kept in a repository or refreshed from a link only
    shows a diff when the metadata really changed. Without ``head``, the
    entries alone, as a selection's copy takes them ("" for none)."""
    parts = [r["text"].strip() for r in records]
    if head:
        line = f"% {len(records)} " + ("entry" if len(records) == 1 else "entries")
        if source:
            line += f" from {source}"
        parts.insert(0, line + ", exported from Gamma")
    return "\n\n".join(parts) + "\n" if parts else ""

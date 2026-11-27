"""A BibTeX entry written out, in BibTeX's dialect or BibLaTeX's.

Every entry Gamma prints leaves through ``format_entry``, so what is
printed is valid however its fields were written. The rules: text values
in ASCII LaTeX (``bibtex_latex``) with a title's capitals braced
(``bibtex_case``), identifiers and links verbatim, the month a standard
macro, page ranges with `--`, an entry type the dialect defines whose venue
field is present (else `@misc`), and an ASCII key. A ``Dialect`` names the
entry types, the venue field each needs and the order of the fields.
``bibtex.build_entry`` and ``bibtex.normalize_entry`` write in ``BIBTEX``,
``citations.biblatex_entry`` in ``BIBLATEX``. Pure, standard library only.
"""

import re
import urllib.parse
from typing import NamedTuple

from .bibtex_case import CASE_FIELDS, protect_case
from .bibtex_keys import clean_key
from .bibtex_latex import balanced, latex_text

# A page range, its two ends (\1, \2) apart: "436-444", "436–444", a
# figure dash, a LaTeX "436--444". A BibTeX entry writes it "436--444",
# the range BibTeX styles expect; CSL and RIS (gamma/citations.py) read
# the same ends, as frontend/src/citations/formats.js pageLocator does.
PAGE_RANGE_RE = re.compile(r"^(\w+)\s*[-‐‑‒–—]+\s*(\w+)$")
# Values copied as written: identifiers and links, which LaTeX reads
# verbatim (`\doi{}`, `\url{}`).
_VERBATIM = {"doi", "url", "eprint", "archiveprefix", "primaryclass", "eprinttype", "eprintclass",
             "isbn", "issn", "file", "urldate", "langid"}
_FIELD_CASE = {"archiveprefix": "archivePrefix", "primaryclass": "primaryClass"}
MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
          "october", "november", "december"]
_DOI_URL_RE = re.compile(r"https?://(?:dx\.)?doi\.org/(.+)", re.I)


class Dialect(NamedTuple):
    """What `format_entry` holds an entry to: the entry types the dialect
    defines (any other is written as @misc), the venue field a type cannot
    do without (without it the entry is a @misc), and the order fields are
    written in (the rest follow in the order they came)."""
    types: frozenset
    venue: dict
    order: list


# The entry types every BibTeX style defines (any other is written as
# @misc, which every style also prints); the venue field a type cannot do
# without (apsrev4-2 fails on an @article with no journal, and the
# standard styles warn); the order built entries have always had.
BIBTEX = Dialect(
    frozenset({"article", "book", "booklet", "conference", "inbook", "incollection", "inproceedings",
               "manual", "mastersthesis", "misc", "phdthesis", "proceedings", "techreport", "unpublished"}),
    {"article": "journal", "inproceedings": "booktitle", "conference": "booktitle",
     "incollection": "booktitle", "phdthesis": "school", "mastersthesis": "school",
     "techreport": "institution"},
    ["title", "author", "editor", "journal", "booktitle", "school", "institution", "howpublished",
     "volume", "number", "pages", "publisher", "isbn", "issn", "eprint", "archivePrefix",
     "primaryClass", "year", "month", "doi", "url"])
# BibLaTeX (biber) names a journal `journaltitle` and a thesis's or a
# report's university or institution `institution`, takes a full ISO `date`
# in place of year and month, and has types BibTeX lacks: @thesis and
# @report with a `type`, @online for a preprint (`eprinttype`/`eprint`).
BIBLATEX = Dialect(
    frozenset({"article", "book", "booklet", "collection", "inbook", "incollection", "inproceedings", "manual",
               "misc", "online", "proceedings", "report", "thesis", "unpublished"}),
    {"article": "journaltitle", "inproceedings": "booktitle", "incollection": "booktitle",
     "thesis": "institution", "report": "institution"},
    ["title", "author", "editor", "journaltitle", "shortjournal", "booktitle", "institution", "type",
     "organization", "howpublished", "volume", "number", "pages", "publisher", "isbn", "issn", "eprint",
     "eprinttype", "eprintclass", "date", "year", "note", "doi", "url"])


class _Macro(str):
    """A value written bare, not braced: a month macro (`month = sep`)."""


def month_macro(value: str) -> str:
    """A month as BibTeX's macro (`jan` … `dec`): doi.org writes `Sept` and
    `July`, which no style defines. "" when the value names no month."""
    word = str(value or "").strip().lower().rstrip(".")
    if word.isdigit() and 1 <= int(word) <= 12:
        return MONTHS[int(word) - 1][:3]
    return next((m[:3] for m in MONTHS if len(word) >= 3 and m.startswith(word)), "")


def format_entry(entry_type: str, key: str, fields: dict, dialect: Dialect = BIBTEX) -> str:
    """One entry as valid BibTeX, in Gamma's layout (a field per line,
    braced values). ``fields`` maps field names to raw values: text in any
    form is made LaTeX (`bibtex_latex.latex_text`), identifiers stay
    verbatim, the month becomes a macro, a page range takes `--`, a
    title's capitals inside a word are braced (`bibtex_case.protect_case`);
    empty fields are left out. The entry type, the venue it needs and the
    field order are the ``dialect``'s."""
    entry_type = entry_type.lower() if entry_type.lower() in dialect.types else "misc"
    values = {}
    for name, value in fields.items():
        name = _FIELD_CASE.get(name.lower(), name.lower())
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or name in values:
            continue
        if name == "month":
            month = month_macro(text)
            values[name] = _Macro(month) if month else latex_text(text)
        elif name.lower() in _VERBATIM:
            # Written raw: \url and \doi read their argument verbatim, so a
            # "%20" or "#frag" prints as it is (an escaped \% would print its
            # backslash under the url package and become %5C under biber).
            values[name] = balanced(text)
        else:
            # A bare macro other than a month (`journal = prl`) is defined by
            # an @string this file does not carry: written as its text.
            if name == "pages":
                text = PAGE_RANGE_RE.sub(r"\1--\2", text)
            values[name] = protect_case(latex_text(text)) if name in CASE_FIELDS else latex_text(text)
    # doi.org adds its own resolver link beside the DOI; styles print both.
    doi_url = _DOI_URL_RE.fullmatch(values.get("url", ""))
    if doi_url and urllib.parse.unquote(doi_url.group(1)).lower() == values.get("doi", "").lower():
        del values["url"]
    if dialect.venue.get(entry_type) and not values.get(dialect.venue[entry_type]):
        entry_type = "misc"
    order = sorted(values, key=lambda n: dialect.order.index(n) if n in dialect.order else len(dialect.order))
    lines = [f"  {n} = {values[n]}" if isinstance(values[n], _Macro) else f"  {n} = {{{values[n]}}}"
             for n in order if values[n]]
    return f"@{entry_type}{{{clean_key(key) or 'paper'},\n" + ",\n".join(lines) + "\n}"

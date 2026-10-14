"""BibTeX entries and bibliographies.

One place for everything about the `@article{key, …}` text: building an entry
from a stored metadata record, reading and rewriting its citation key,
turning the markup registries leave in field values into LaTeX, and
assembling many entries into one `.bib` file whose keys are unique.

`routers/metadata.py` builds the single entry cached on a page block
(`properties.bibtex`) and shown by the Share popover's Copy BibTeX.
`routers/export.py` (`_BibtexBuilder`, `?mode=bibtex`) concatenates the
cached entries of a page or a folder into the bibliography a LaTeX document
cites — see [import_export.md](../../docs/dev/import_export.md).
`ai_tools.py` (`cite`) and the Zotero import (`routers/imports.py`) build
entries the same way. Every surface that prints a page's entry — the .bib
export, `cite`, the Markdown and Obsidian exports — takes it from
`page_entry`, so the key the chat shows is the key the file carries.

A citation key is normally generated (first author's surname + year), which
means two papers by the same author in the same year would collide in one
bibliography. `unique_keys` resolves that at export time by suffixing `a`,
`b`, … Better-BibTeX-style, and a key the user pinned on the page
(`properties.cite_key`) is never the one that moves.
"""

import html
import re

# What a citation key may not contain: BibTeX breaks on whitespace, commas,
# braces and `=`, and `( ) \ " # % ~` trip up BibTeX or LaTeX. The rest of
# printable ASCII is in wide use (`smith:2020-attention`, `10.1234/foo`).
_KEY_BAD = re.compile(r"[\s,{}()=\\\"#%~]")
KEY_MAX = 120

# `@article{key,` at the head of an entry — the key is group 2.
_HEAD = re.compile(r"(@\s*[A-Za-z]+\s*\{\s*)([^,\s]*)")

# The markup registries leave in titles and venues — Crossref keeps the
# publisher's JATS/HTML: "<i>Planck</i> 2018 results", "CsPbCl<sub>3</sub>
# Nanocrystals", "Astronomy &amp; Astrophysics" — and the LaTeX macro each
# tag becomes in an entry. A tag not listed here is dropped.
_TAG_MACRO = {"i": "emph", "em": "emph", "b": "textbf", "strong": "textbf",
              "sub": "textsubscript", "sup": "textsuperscript", "scp": "textsc", "sc": "textsc"}
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*)?/?>")
# A bare & % # breaks LaTeX; one already escaped (a hand edit typed `\&`) is
# left alone.
_ESCAPE_RE = re.compile(r"(?<!\\)([&%#])")
# "436-444" / "436–444" → "436--444", the range BibTeX styles expect.
_PAGE_RANGE_RE = re.compile(r"^(\w+)\s*[-‐‑–—]\s*(\w+)$")

# Crossref/CSL work types and the BibTeX entry each becomes. Anything else
# with a venue is an @article; without one, @misc.
_BOOK_TYPES = ("book", "monograph", "edited-book", "reference-book")
_CHAPTER_TYPES = ("book-chapter", "book-section", "book-part")


def latex_text(text: str) -> str:
    """A field value as LaTeX: registry markup becomes macros (`<i>` →
    `\\emph{}`, `<sub>`/`<sup>` → `\\textsubscript{}`/`\\textsuperscript{}`,
    `<b>` → `\\textbf{}`, `<scp>` → `\\textsc{}`; other tags are dropped),
    HTML entities are decoded (`&amp;`), and a bare `& % #` is escaped.
    Unbalanced markup falls back to dropping the tags, so an entry never
    gains an unmatched brace."""
    raw = str(text or "")

    def macro(m):
        name = _TAG_MACRO.get(m.group(2).lower().split(":")[-1])
        if not name:
            return ""
        return "}" if m.group(1) else f"\\{name}{{"
    stripped = _TAG_RE.sub("", raw)
    converted = _TAG_RE.sub(macro, raw)
    if converted.count("{") - converted.count("}") != stripped.count("{") - stripped.count("}"):
        converted = stripped
    return _ESCAPE_RE.sub(r"\\\1", html.unescape(converted))


def clean_entry(text: str) -> str:
    """A registrar's own BibTeX (doi.org's rendering) made typesettable: the
    markup and entities Crossref prints as they are (`&amp;`, `<sub>3</sub>`)
    become LaTeX, as `latex_text` does for a built entry's values."""
    return latex_text(text) if text else text


def clean_key(raw: str) -> str:
    """A user-typed citation key reduced to one BibTeX can carry (the
    characters it breaks on removed), or "" when nothing is left."""
    return _KEY_BAD.sub("", str(raw or "").strip())[:KEY_MAX]


def _authors(meta: dict) -> list[str]:
    return [str(a).strip() for a in (meta.get("authors") or []) if str(a or "").strip()]


def default_key(meta: dict) -> str:
    """The generated key: first author's surname, lowercase letters only,
    plus the year. Unchanged since the first release — entries cached on
    pages carry it, so it must stay stable. A record without a usable
    author is keyed `paper<year>`."""
    authors = _authors(meta)
    surname = re.sub(r"[^a-z]", "", authors[0].split()[-1].lower()) if authors else ""
    return f"{surname or 'paper'}{meta.get('year', '')}"


def entry_key(text: str) -> str:
    """The citation key of an entry, or "" when the text isn't one."""
    m = _HEAD.match((text or "").lstrip())
    return m.group(2) if m else ""


def with_key(text: str, key: str) -> str:
    """``text`` with its citation key replaced — how a pinned key reaches an
    entry that came from a registrar (doi.org renders its own BibTeX, keyed
    its own way) and how ``unique_keys`` suffixes a clash."""
    key = clean_key(key)
    if not key or not text:
        return text
    return _HEAD.sub(lambda m: f"{m.group(1)}{key}", text.lstrip(), count=1)


def pinned_key(props: dict) -> str:
    """The citation key pinned on a page (``properties.cite_key``), cleaned,
    or "" when none is."""
    return clean_key((props or {}).get("cite_key") or "")


def page_entry(props: dict) -> str:
    """The BibTeX entry of a page as every surface prints it — the .bib
    export, the AI ``cite`` tool, the Markdown and Obsidian exports: the
    cached entry (``properties.bibtex``) or, failing that, one built from
    the metadata record, keyed by the pinned key when there is one. The
    cached entry may predate the pin or carry a registrar's own key, so the
    pin is applied here rather than trusted to be in the text. "" when the
    page has neither an entry nor a record."""
    props = props or {}
    meta = props.get("meta") if isinstance(props.get("meta"), dict) else None
    pin = pinned_key(props)
    text = (props.get("bibtex") or "").strip() or (build_entry(meta, pin) if meta else "")
    return with_key(text, pin) if pin else text


def build_entry(meta: dict, key: str = "") -> str:
    """The BibTeX entry for a metadata record (`properties.meta`), keyed by
    ``key`` when one is pinned and by ``default_key`` otherwise.

    The entry type follows the record: the registry's work type when the
    lookup stored one (`meta.work_type`, a Crossref type — a
    `proceedings-article` is an `@inproceedings` with a `booktitle`, a
    `book-chapter` an `@incollection`, a `dissertation` a `@phdthesis`, a
    `report` a `@techreport`), else the document kind (a `book` has its
    publisher and ISBN), else the shape of the record: an arXiv id without
    a venue is the preprint `@article`, a venue makes an `@article`, and a
    record with neither is `@misc` (an `@article` without a journal is a
    BibTeX warning). An arXiv id always adds `eprint`/`archivePrefix`.
    Values go through `latex_text`; a page range is written `--`."""
    authors = _authors(meta)
    venue = str(meta.get("venue") or "").strip()
    arxiv_id = str(meta.get("arxiv_id") or "").strip()
    publisher = str(meta.get("publisher") or "").strip()
    pages = str(meta.get("pages") or "").strip()
    work = str(meta.get("work_type") or "").strip().lower()
    kind = meta.get("kind")
    fields: dict[str, str] = {
        "title": meta.get("title", ""),
        "author": " and ".join(authors),
    }
    if work in _BOOK_TYPES or kind == "book":
        entry = "book"
        fields["publisher"] = publisher
        fields["isbn"] = meta.get("isbn", "")
    elif work == "proceedings-article":
        entry = "inproceedings"
        fields.update(booktitle=venue, pages=pages, publisher=publisher)
    elif work in _CHAPTER_TYPES:
        entry = "incollection"
        fields.update(booktitle=venue, pages=pages, publisher=publisher)
    elif work == "dissertation" or kind == "thesis":
        entry = "phdthesis"
        fields["school"] = publisher or venue
    elif work == "report" or kind == "report":
        entry = "techreport"
        fields["institution"] = publisher or venue
    elif publisher and not venue and not arxiv_id:
        # A publisher and no venue, from a registry that names no type (the
        # book registries): a book.
        entry = "book"
        fields["publisher"] = publisher
        fields["isbn"] = meta.get("isbn", "")
    elif arxiv_id and (not venue or venue.lower().startswith("arxiv")):
        entry = "article"
        fields["journal"] = f"arXiv preprint arXiv:{arxiv_id}"
    elif venue:
        entry = "article"
        fields.update(journal=venue, volume=meta.get("volume", ""), pages=pages)
    else:
        entry = "misc"
    if arxiv_id:
        fields["eprint"] = arxiv_id
        fields["archivePrefix"] = "arXiv"
    fields["year"] = meta.get("year", "")
    fields["doi"] = meta.get("doi", "")
    verbatim = ("doi", "eprint", "archivePrefix", "isbn")
    lines = []
    for k, v in fields.items():
        v = str(v or "").strip()
        if not v:
            continue
        if k == "pages":
            v = _PAGE_RANGE_RE.sub(r"\1--\2", v)
        if k not in verbatim:
            v = latex_text(v)
        lines.append(f"  {k} = {{{v}}}")
    return f"@{entry}{{{clean_key(key) or default_key(meta)},\n" + ",\n".join(lines) + "\n}"


def _suffixes():
    """a, b, … z, aa, ab, … — the postfix a clashing key takes."""
    n = 0
    while True:
        n += 1
        label, rest = "", n
        while rest:
            rest, i = divmod(rest - 1, 26)
            label = chr(ord("a") + i) + label
        yield label


def unique_keys(records: list[dict]) -> list[dict]:
    """``records`` ({"text", "key", "pinned", …}) with every key distinct.

    Pinned keys are assigned first, so a key the user chose on a page is the
    one that survives and a generated clash is what gets suffixed. Within
    each group the input order decides who keeps the bare key, so the result
    only depends on the sort the caller already made: re-exporting an
    unchanged library produces a byte-identical file. Keys are compared
    case-insensitively — classic BibTeX treats `Smith2020` and `smith2020`
    as one entry (biber does not, but a file both can read is the point).
    """
    taken: set[str] = set()
    out = [None] * len(records)
    for pinned in (True, False):
        for i, record in enumerate(records):
            if bool(record.get("pinned")) is not pinned:
                continue
            key = record.get("key") or entry_key(record.get("text", "")) or "paper"
            if key.lower() in taken:
                for suffix in _suffixes():
                    if f"{key}{suffix}".lower() not in taken:
                        key = f"{key}{suffix}"
                        break
            taken.add(key.lower())
            out[i] = {**record, "key": key, "text": with_key(record.get("text", ""), key)}
    return out


def bibliography(records: list[dict], source: str = "") -> str:
    """The `.bib` file for ``records`` (``unique_keys`` order): a comment
    naming what was exported, then the entries, one blank line apart. No
    timestamp — an unchanged library re-exports byte-identically, so a
    bibliography kept in a repository or refreshed from a link only shows a
    diff when the metadata really changed."""
    head = f"% {len(records)} " + ("entry" if len(records) == 1 else "entries")
    if source:
        head += f" from {source}"
    head += ", exported from Gamma"
    return "\n\n".join([head] + [r["text"].strip() for r in records]) + "\n"

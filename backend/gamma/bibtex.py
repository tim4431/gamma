"""BibTeX entries and bibliographies.

One place for everything about the `@article{key, …}` text: building an entry
from a stored metadata record, reading and rewriting its citation key, and
assembling many entries into one `.bib` file whose keys are unique.

`routers/metadata.py` builds the single entry cached on a page block
(`properties.bibtex`) and shown by the Share popover's Copy BibTeX.
`routers/export.py` (`_BibtexBuilder`, `?mode=bibtex`) concatenates the
cached entries of a page or a folder into the bibliography a LaTeX document
cites — see [import_export.md](../../docs/dev/import_export.md).
`ai_tools.py` (`cite`) and the Zotero import (`routers/imports.py`) build
entries the same way.

A citation key is normally generated (first author's surname + year), which
means two papers by the same author in the same year would collide in one
bibliography. `unique_keys` resolves that at export time by suffixing `a`,
`b`, … Better-BibTeX-style, and a key the user pinned on the page
(`properties.cite_key`) is never the one that moves.
"""

import re

# What a citation key may not contain: BibTeX breaks on whitespace, commas,
# braces and `=`, and `( ) \ " # % ~` trip up BibTeX or LaTeX. The rest of
# printable ASCII is in wide use (`smith:2020-attention`, `10.1234/foo`).
_KEY_BAD = re.compile(r"[\s,{}()=\\\"#%~]")
KEY_MAX = 120

# `@article{key,` at the head of an entry — the key is group 2.
_HEAD = re.compile(r"(@\s*[A-Za-z]+\s*\{\s*)([^,\s]*)")


def clean_key(raw: str) -> str:
    """A user-typed citation key reduced to one BibTeX can carry (the
    characters it breaks on removed), or "" when nothing is left."""
    return _KEY_BAD.sub("", str(raw or "").strip())[:KEY_MAX]


def default_key(meta: dict) -> str:
    """The generated key: first author's surname, lowercase letters only,
    plus the year. Unchanged since the first release — entries cached on
    pages carry it, so it must stay stable."""
    authors = meta.get("authors") or []
    surname = re.sub(r"[^a-z]", "", (authors[0].split()[-1] if authors else "paper").lower()) or "paper"
    return f"{surname}{meta.get('year', '')}"


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


def build_entry(meta: dict, key: str = "") -> str:
    """The BibTeX entry for a metadata record (`properties.meta`), keyed by
    ``key`` when one is pinned and by ``default_key`` otherwise. An arXiv
    preprint carries `eprint`/`archivePrefix`, a book becomes `@book` with
    its publisher and ISBN, and anything with a venue is an `@article`."""
    authors = meta.get("authors") or []
    fields: dict[str, str] = {
        "title": meta.get("title", ""),
        "author": " and ".join(authors),
    }
    venue = meta.get("venue", "")
    entry = "article"
    if meta.get("arxiv_id") and (not venue or venue.lower().startswith("arxiv")):
        fields["journal"] = f"arXiv preprint arXiv:{meta['arxiv_id']}"
        fields["eprint"] = meta["arxiv_id"]
        fields["archivePrefix"] = "arXiv"
    elif meta.get("kind") == "book" or (meta.get("publisher") and not venue):
        entry = "book"
        fields["publisher"] = meta.get("publisher", "")
        fields["isbn"] = meta.get("isbn", "")
    elif venue:
        fields["journal"] = venue
        fields["volume"] = meta.get("volume", "")
        fields["pages"] = meta.get("pages", "")
    fields["year"] = meta.get("year", "")
    fields["doi"] = meta.get("doi", "")
    body = ",\n".join(f"  {k} = {{{v}}}" for k, v in fields.items() if v)
    return f"@{entry}{{{clean_key(key) or default_key(meta)},\n{body}\n}}"


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
    unchanged library produces a byte-identical file.
    """
    taken: set[str] = set()
    out = [None] * len(records)
    for pinned in (True, False):
        for i, record in enumerate(records):
            if bool(record.get("pinned")) is not pinned:
                continue
            key = record.get("key") or entry_key(record.get("text", "")) or "paper"
            if key in taken:
                for suffix in _suffixes():
                    if f"{key}{suffix}" not in taken:
                        key = f"{key}{suffix}"
                        break
            taken.add(key)
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

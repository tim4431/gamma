"""Citation keys: generated, cleaned, pinned and made unique.

A key is ASCII and holds none of the characters BibTeX and LaTeX break on
(``clean_key``). It is normally generated, the first author's surname plus
the year (``default_key``), so two papers by the same author in the same
year collide in one bibliography. ``unique_keys`` resolves that at export
time by suffixing `a`, `b`, … Better-BibTeX-style, and a key the user
pinned on the page (``properties.cite_key``, ``pinned_key``) is never the
one that moves. A rebuilt entry keeps the key a .tex file already cites
(``rebuilt_key``). ``entry_key`` and ``with_key`` read and replace the key
at the head of an entry's text. Pure, standard library only.
"""

import re
import unicodedata

from . import names
from .bibtex_names import authors_of

# What a citation key may not contain: BibTeX breaks on whitespace, commas,
# braces and `=`, and `( ) \ " # % ~` trip up BibTeX or LaTeX. The rest of
# printable ASCII is in wide use (`smith:2020-attention`, `10.1234/foo`).
# A key is ASCII: pdflatex cannot `\cite` one with a UTF-8 letter in it.
_KEY_BAD = re.compile(r"[\s,{}()=\\\"#%~]|[^\x21-\x7e]")
KEY_MAX = 120

# `@article{key,` at the head of an entry — the key is group 2.
_HEAD = re.compile(r"(@\s*[A-Za-z]+\s*\{\s*)([^,\s]*)")

# Letters with no decomposition, as a key spells them in ASCII.
_KEY_LETTERS = {"ß": "ss", "æ": "ae", "Æ": "AE", "œ": "oe", "Œ": "OE", "ø": "o", "Ø": "O", "ł": "l",
                "Ł": "L", "ı": "i", "đ": "d", "Đ": "D", "ð": "d", "Ð": "D", "þ": "th", "Þ": "Th"}


def clean_key(raw: str) -> str:
    """A citation key reduced to one BibTeX and LaTeX can carry: letters
    spelled in ASCII ("Fösel" → "Fosel"), the characters they break on
    removed. "" when nothing is left."""
    text = "".join(_KEY_LETTERS.get(c, c) for c in str(raw or "").strip())
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return _KEY_BAD.sub("", text)[:KEY_MAX]


def _key_of(name: str, year) -> str:
    words = name.split()
    surname = re.sub(r"[^a-z]", "", words[-1].lower()) if words else ""
    return f"{surname or 'paper'}{year}"


def last_word_key(meta: dict) -> str:
    """The key ``default_key`` gave every record before release 72: the
    last word of the first author's name as written, lowercase letters
    only, plus the year ("fernando2021" of "Pereira Campos, Jorge
    Fernando")."""
    authors = authors_of(meta)
    return _key_of(authors[0] if authors else "", meta.get("year", ""))


def default_key(meta: dict) -> str:
    """The generated key: the last word of the first author's name,
    lowercase letters only, plus the year; of a name ``names.join_name``
    writes with a comma, the last word of its surname ("Campos" of "Pereira
    Campos, Jorge Fernando" as of "Jorge Fernando Pereira Campos", "King"
    of "King, Jr., Martin Luther"; ``names.joined_family``), so a page
    pulled from Zotero keeps the key it had when the page read "Jorge
    Fernando Pereira Campos". Any other name keys as before ("ada" of a
    typed "Lovelace, Ada", "jr" of "Martin Luther King, Jr."). A name
    stored in join_name's comma form before then keyed by its last word
    ("fernando"): its cached entry keeps that key through a rebuild
    (``rebuilt_key``), and a record with no entry was given one keyed so
    (gamma/migrations.py ``_v72_keyed``). A record without a usable author
    is keyed `paper<year>`."""
    authors = authors_of(meta)
    family = names.joined_family(authors[0]) if authors else None
    if family is None:
        return last_word_key(meta)
    return _key_of(family, meta.get("year", ""))


def rebuilt_key(props: dict, meta: dict, key: str = "") -> str:
    """The key to build a page's entry again with (``bibtex.build_entry``'s
    ``key``) once its record becomes ``meta``: ``key`` (a pinned one) when
    given; else the key the page's cached entry carries while that is the
    one the record was keyed by before release 72 (``last_word_key``, which
    ``default_key`` reads otherwise for a name in ``names.join_name``'s
    comma form) and ``meta`` keeps the first author and the year, so a hand
    edit, a refetch or a Zotero pull never moves the key a .tex file
    cites; else "" (``default_key``)."""
    if key:
        return key
    props = props or {}
    old = props.get("meta") if isinstance(props.get("meta"), dict) else {}
    cached = entry_key(props.get("bibtex") or "")
    if (cached and cached == last_word_key(meta) != default_key(meta)
            and authors_of(old)[:1] == authors_of(meta)[:1]
            and str(old.get("year") or "") == str(meta.get("year") or "")):
        return cached
    return ""


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


def unique_keys(records: list[dict], taken=()) -> list[dict]:
    """``records`` ({"text", "key", "pinned", …}) with every key distinct,
    and none of ``taken`` (lowercase: the keys of entries keyed before
    these, which keep them, against a pinned key here too).

    Pinned keys are assigned first, so a key the user chose on a page is the
    one that survives and a generated clash is what gets suffixed. Within
    each group the input order decides who keeps the bare key, so the result
    only depends on the sort the caller already made: re-exporting an
    unchanged library produces a byte-identical file. Keys are compared
    case-insensitively — classic BibTeX treats `Smith2020` and `smith2020`
    as one entry (biber does not, but a file both can read is the point).
    """
    taken = set(taken)
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

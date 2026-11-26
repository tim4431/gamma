"""BibTeX entries and bibliographies.

One place for everything about the `@article{key, …}` text: building an entry
from a stored metadata record, reading a stored entry back and writing it out
as valid BibTeX, reading and rewriting its citation key, and assembling many
entries into one `.bib` file whose keys are unique.

`routers/metadata.py` builds the single entry cached on a page block
(`properties.bibtex`). `routers/export.py` (`_CitationBuilder`,
`?mode=bibtex`) concatenates the entries of a page or a folder into the
bibliography a LaTeX document cites — see
[import_export.md](../../docs/dev/import_export.md). `ai_tools/library.py` (`cite`)
and the Zotero import (`routers/imports.py`) build entries the same way.
The BibLaTeX entry (`citations.biblatex_entry`) leaves through the same
`format_entry`, with BibLaTeX's own entry types, venue fields and order
(`BIBLATEX`).
Every surface that prints a page's entry — the .bib export and its review
(which the share popover's Copy BibTeX reads), `cite`, the Markdown and
Obsidian exports — takes it from `page_entry`, so the key the chat shows is
the key the file carries.

Every entry leaves through `format_entry`, so what is printed is valid BibTeX
however the stored text was written: a registrar's one-line rendering, an
entry cached by an older Gamma, one restored from a Markdown export. The
rules (`normalize_entry`): field values in ASCII LaTeX (accented letters as
`{\\"o}`, Greek and math symbols in `$…$`, registry markup as macros, MathML
as math), `& % # _ $ ^` escaped outside math, braces balanced, the month a
standard macro, page ranges with `--`, a standard entry type whose venue
field is present (else `@misc`), and an ASCII key.

A citation key is normally generated (first author's surname + year), which
means two papers by the same author in the same year would collide in one
bibliography. `unique_keys` resolves that at export time by suffixing `a`,
`b`, … Better-BibTeX-style, and a key the user pinned on the page
(`properties.cite_key`) is never the one that moves.
"""

import html
import re
import unicodedata
import urllib.parse
from typing import NamedTuple

from . import item_types, mathml, meta_fields, names

# What a citation key may not contain: BibTeX breaks on whitespace, commas,
# braces and `=`, and `( ) \ " # % ~` trip up BibTeX or LaTeX. The rest of
# printable ASCII is in wide use (`smith:2020-attention`, `10.1234/foo`).
# A key is ASCII: pdflatex cannot `\cite` one with a UTF-8 letter in it.
_KEY_BAD = re.compile(r"[\s,{}()=\\\"#%~]|[^\x21-\x7e]")
KEY_MAX = 120

# `@article{key,` at the head of an entry — the key is group 2.
_HEAD = re.compile(r"(@\s*[A-Za-z]+\s*\{\s*)([^,\s]*)")

# --- field values as LaTeX ------------------------------------------------

# The markup registries leave in titles and venues — Crossref keeps the
# publisher's JATS/HTML: "<i>Planck</i> 2018 results", "CsPbCl<sub>3</sub>
# Nanocrystals", "Astronomy &amp; Astrophysics" — and the LaTeX macro each
# tag becomes in an entry. A tag not listed here is dropped.
_TAG_MACRO = {"i": "emph", "em": "emph", "b": "textbf", "strong": "textbf",
              "sub": "textsubscript", "sup": "textsuperscript", "scp": "textsc", "sc": "textsc"}
_TAG_RE = re.compile(r"<(/?)([A-Za-z][\w:.-]*)(?:\s[^<>]*)?/?>")
# Characters LaTeX gives a meaning; one already escaped (a hand edit typed
# `\&`) is left alone. A `$` that pairs up is math and is kept.
_ESCAPE_RE = re.compile(r"(?<!\\)([&%#_$])")
_CARET_RE = re.compile(r"(?<!\\)\^")
_MATH_RE = re.compile(r"(?<!\\)\$(?:[^$\\]|\\.)+?(?<!\\)\$")
# A page range, its two ends (\1, \2) apart: "436-444", "436–444", a
# figure dash, a LaTeX "436--444". A BibTeX entry writes it "436--444",
# the range BibTeX styles expect; CSL and RIS (gamma/citations.py) read
# the same ends, as frontend/src/citations/formats.js pageLocator does.
PAGE_RANGE_RE = re.compile(r"^(\w+)\s*[-‐‑‒–—]+\s*(\w+)$")

# Text-mode LaTeX for the characters outside ASCII that have one and no
# decomposition (accented letters come from `_ACCENTS`; Greek letters and
# math symbols are `mathml.math_command` in `$…$`).
_TEXT_CHARS = {
    "–": "--", "—": "---", "―": "---", "‐": "-", "‑": "-", "‒": "-",
    "‘": "`", "’": "'", "‚": ",", "“": "``", "”": "''", "„": ",,",
    "«": r"{\guillemotleft}", "»": r"{\guillemotright}", "…": r"\ldots{}",
    " ": "~", " ": r"\,", " ": r"\,", " ": " ", " ": " ", "​": "",
    "ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}", "œ": r"{\oe}", "Œ": r"{\OE}", "ø": r"{\o}", "Ø": r"{\O}",
    "ł": r"{\l}", "Ł": r"{\L}", "ı": r"{\i}", "ȷ": r"{\j}", "đ": r"{\dj}", "Đ": r"{\DJ}",
    "ð": r"{\dh}", "Ð": r"{\DH}", "þ": r"{\th}", "Þ": r"{\TH}", "ŋ": r"{\ng}", "Ŋ": r"{\NG}",
    "©": r"{\textcopyright}", "®": r"{\textregistered}", "™": r"{\texttrademark}",
    "§": r"{\S}", "¶": r"{\P}", "£": r"{\pounds}", "€": r"{\texteuro}", "•": r"{\textbullet}",
    "¡": "{!`}", "¿": "{?`}", "‰": r"{\textperthousand}",
}
# Combining marks and the accent command each becomes: a symbol (`\"o`) or
# a letter that takes its argument in braces (`\c{c}`).
_ACCENTS = {"̀": "`", "́": "'", "̂": "^", "̃": "~", "̄": "=", "̆": "u",
            "̇": ".", "̈": '"', "̊": "r", "̋": "H", "̌": "v",
            "̣": "d", "̧": "c", "̨": "k", "̱": "b"}


def _accented(ch: str) -> str:
    """An accented Latin letter as BibTeX's special character
    (`{\\"o}`, `{\\c{c}}`, `{\\={\\"u}}`), "" when it is not one."""
    base, *marks = unicodedata.normalize("NFD", ch)
    if not marks or not base.isascii() or not base.isalpha() or any(m not in _ACCENTS for m in marks):
        return ""
    out = base
    for mark in marks:
        cmd = _ACCENTS[mark]
        out = f"\\{cmd}{out}" if not cmd.isalpha() and len(out) == 1 else f"\\{cmd}{{{out}}}"
    return "{" + out + "}"


def _tex_char(ch: str) -> str:
    """One character as text-mode LaTeX. A character with no LaTeX form
    (CJK, most scripts) stays as it is: the file is UTF-8, and a Unicode
    engine (XeLaTeX, LuaLaTeX with biber) typesets it."""
    if ch.isascii():
        return ch
    if ch in _TEXT_CHARS:
        return _TEXT_CHARS[ch]
    accented = _accented(ch)
    if accented:
        return accented
    name = unicodedata.name(ch, "")
    plain = unicodedata.normalize("NFKD", ch)
    if name.startswith(("SUPERSCRIPT", "SUBSCRIPT")) and plain.isascii():
        return f"\\text{'superscript' if name.startswith('SUPER') else 'subscript'}{{{plain}}}"
    cmd = mathml.math_command(ch)
    if cmd:
        # A Greek capital LaTeX spells with a Latin letter (Alpha is A) is text.
        return cmd if cmd.isalpha() else f"${cmd}$"
    # Ligatures and full-width forms: "ﬁ" is "fi".
    return plain if plain != ch and plain.isascii() else ch


def _balanced(text: str) -> str:
    """``text`` without the braces that pair with nothing: BibTeX counts
    every brace, escaped or not, and one unmatched brace breaks the file."""
    keep, opened = [], []
    for i, c in enumerate(text):
        if c == "{":
            opened.append(i)
        elif c == "}":
            if not opened:
                continue
            opened.pop()
        keep.append((i, c))
    drop = set(opened)
    return "".join(c for i, c in keep if i not in drop)


def latex_text(text: str) -> str:
    """A field value as LaTeX. Registry markup becomes macros (`<i>` →
    `\\emph{}`, `<sub>`/`<sup>` → `\\textsubscript{}`/`\\textsuperscript{}`,
    `<b>` → `\\textbf{}`, `<scp>` → `\\textsc{}`; other tags are dropped),
    a MathML formula becomes `$…$` (`mathml.to_latex`), HTML entities are
    decoded (`&amp;`), whitespace is collapsed, `& % # _ $ ^` outside math
    are escaped, and every other character outside ASCII becomes its LaTeX
    (`_tex_char`). What is already LaTeX (`\\&`, `{\\"o}`, `$x^2$`) is left
    as it is, so a value converted twice reads the same. Unbalanced markup
    falls back to dropping the tags, and an unmatched brace is dropped."""
    maths: list[str] = []

    def hold(latex: str) -> str:
        maths.append(latex)
        return f"\x00{len(maths) - 1}\x00"

    raw = mathml.replace(str(text or ""), lambda fragment: hold(mathml.to_latex(fragment)))

    def macro(m):
        name = _TAG_MACRO.get(m.group(2).lower().split(":")[-1])
        if not name:
            return ""
        return "}" if m.group(1) else f"\\{name}{{"
    stripped = _TAG_RE.sub("", raw)
    converted = _TAG_RE.sub(macro, raw)
    if converted.count("{") - converted.count("}") != stripped.count("{") - stripped.count("}"):
        converted = stripped
    converted = re.sub(r"\s+", " ", html.unescape(converted)).strip()
    converted = _MATH_RE.sub(lambda m: hold(m.group(0)[1:-1]), converted)
    converted = _CARET_RE.sub(r"\\^{}", _ESCAPE_RE.sub(r"\\\1", converted))
    # In LaTeX's default font encoding a text-mode < prints as ¡.
    converted = re.sub(r"[<>]", lambda m: f"${m.group(0)}$", converted)
    converted = "".join(_tex_char(c) for c in converted)
    converted = re.sub("\x00(\\d+)\x00", lambda m: f"${maths[int(m.group(1))]}$", converted)
    return re.sub(r"\s+", " ", _balanced(converted)).strip()


# --- keys -----------------------------------------------------------------

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


def _authors(meta: dict) -> list[str]:
    return [str(a).strip() for a in (meta.get("authors") or []) if str(a or "").strip()]


# The `and` that separates the names of a name list, as BibTeX finds it:
# a word of its own, at the top level, in any case.
_AND_RE = re.compile(r"\s+and\s+", re.I)


def _organisation(name: str) -> bool:
    """Whether a name is an organisation's of more than one word
    (``names.name_parts`` reads it as a surname alone)."""
    last, given, _ = names.name_parts(name)
    return not given and " " in last


def _bibtex_name(name: str) -> str:
    """An author as a BibTeX name list holds one, as Zotero's BibTeX export
    writes it: an organisation braced whole ("{DES Collaboration}"), so
    BibTeX neither reads its last word as a surname nor splits it at an
    "and" of its own; a person as the record writes them (BibTeX splits
    "First von Last" and "Last, First" itself), an "and" inside braced."""
    return "{" + name + "}" if _organisation(name) else _AND_RE.sub(" {and} ", name)


def _name_list(value: str) -> list[str]:
    """A name list's names, split at its top-level `and`s as BibTeX splits
    it (an `and` inside braces is part of a name)."""
    parts, depth, start, i = [], 0, 0, 0
    while i < len(value):
        c = value[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and c.isspace():
            m = _AND_RE.match(value, i)
            if m:
                parts.append(value[start:i])
                start = i = m.end()
                continue
        i += 1
    parts.append(value[start:])
    return [part.strip() for part in parts]


def _closing(text: str, at: int) -> int:
    """The index of the brace that closes the one at ``at``, -1 for none."""
    depth = 0
    for i in range(at, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return i
    return -1


def _brace_organisations(value: str, record_authors=()) -> str:
    """A stored name list (an entry's `author` or `editor`) with every
    organisation in it braced whole, as ``_bibtex_name`` writes one: an
    entry an older Gamma cached wrote them bare. A name of the record's
    with an "and" of its own ("Dark Energy Survey and Kilo-Degree Survey
    Collaboration") is found whole among the names; a name braced already
    or written "Last, First" stays as it is. The value itself when nothing
    is braced."""
    parts = _name_list(value)
    orgs = {" ".join(latex_text(a).split()) for a in record_authors or () if _organisation(str(a))}
    span = max((org.count(" and ") for org in orgs), default=0)
    out, changed, i = [], False, 0
    while i < len(parts):
        whole = next((j for j in range(min(len(parts), i + span + 1), i + 1, -1)
                      if " and ".join(parts[i:j]) in orgs), 0)
        if whole:
            out.append("{" + " and ".join(parts[i:whole]) + "}")
            changed, i = True, whole
            continue
        part = parts[i]
        bare = not (part.startswith("{") and _closing(part, 0) == len(part) - 1)
        if bare and "," not in re.sub(r"\{[^{}]*\}", "", part) and _organisation(part):
            part, changed = "{" + part + "}", True
        out.append(part)
        i += 1
    return " and ".join(out) if changed else value


# --- case ------------------------------------------------------------------

# The fields a style may set in sentence case, whose capitals a word keeps
# only in braces.
_CASE_FIELDS = frozenset({"title", "booktitle", "shorttitle", "series"})
# A command's name, or the one character a control symbol (`\&`) escapes.
_COMMAND_RE = re.compile(r"\\(?:[A-Za-z]+|.)", re.S)


def _special_upper(group: str) -> bool:
    """Whether a special character (`{\\"U}`, `{\\c{C}}`, `{\\L}`) is a
    capital: an accent command and its letter, or a letter's own command."""
    return any(c.isupper() for c in re.sub(r"\\[A-Za-z]+(?=\{)|\\[^A-Za-z]", "", group))


def protect_case(latex: str) -> str:
    """``latex`` (a value as ``latex_text`` writes it) with every word that
    has a capital after its first character braced ("{GI}-type", "{KiDS}",
    "{eBOSS}", "{LLM}s"), as Zotero's BibLaTeX export protects them, so a
    style that sets titles in sentence case keeps them; a word is letters
    and digits, so one of both with a capital is braced too ("{Y3}",
    "{3D}", "{GNN2R}", which the BibLaTeX rule leaves to be lowercased).
    Only the top level is read: what is braced already, math and command
    names stay as they are, so a value protected twice reads the same; an
    accented letter (`{\\'E}`) is a letter of its word."""
    # (text, unit: "A" a capital, "a" a lowercase letter, "9" a digit, ""
    # none; and, for none, whether it ends as a word does: `\_`, a command)
    tokens, i, n = [], 0, len(latex)
    while i < n:
        c = latex[i]
        if c == "{":
            j = _closing(latex, i)
            j = n - 1 if j < 0 else j
            group = latex[i:j + 1]
            special = group.startswith("{\\")
            tokens.append((group, ("A" if _special_upper(group) else "a") if special else "", False))
            i = j + 1
        elif c == "\\":
            command = _COMMAND_RE.match(latex, i).group(0)
            tokens.append((command, "", command[-1:].isalnum() or command[-1:] == "_"))
            i += len(command)
        elif c == "$":
            j = latex.find("$", i + 1)
            while j > 0 and latex[j - 1] == "\\":
                j = latex.find("$", j + 1)
            j = n - 1 if j < 0 else j
            tokens.append((latex[i:j + 1], "", False))
            i = j + 1
        else:
            unit = ("A" if c.isupper() else "a") if c.isalpha() else "9" if c.isdigit() else ""
            tokens.append((c, unit, c == "_"))
            i += 1
    out, k = [], 0
    while k < len(tokens):
        if not tokens[k][1]:
            out.append(tokens[k][0])
            k += 1
            continue
        end = k
        while end < len(tokens) and tokens[end][1]:
            end += 1
        units = "".join(t[1] for t in tokens[k:end])
        word = "".join(t[0] for t in tokens[k:end])
        cased = "A" in units[1:] or ("A" in units and "9" in units)
        out.append("{" + word + "}" if cased and not (k and tokens[k - 1][2]) else word)
        k = end
    return "".join(out)


def _key_of(name: str, year) -> str:
    words = name.split()
    surname = re.sub(r"[^a-z]", "", words[-1].lower()) if words else ""
    return f"{surname or 'paper'}{year}"


def last_word_key(meta: dict) -> str:
    """The key ``default_key`` gave every record before release 72: the
    last word of the first author's name as written, lowercase letters
    only, plus the year ("fernando2021" of "Pereira Campos, Jorge
    Fernando")."""
    authors = _authors(meta)
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
    authors = _authors(meta)
    family = names.joined_family(authors[0]) if authors else None
    if family is None:
        return last_word_key(meta)
    return _key_of(family, meta.get("year", ""))


def rebuilt_key(props: dict, meta: dict, key: str = "") -> str:
    """The key to build a page's entry again with (``build_entry``'s
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
            and _authors(old)[:1] == _authors(meta)[:1]
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


# --- reading a stored entry -------------------------------------------------

# The key runs to the comma; `clean_key` drops what it may not hold.
_ENTRY_HEAD_RE = re.compile(r"@\s*([A-Za-z]+)\s*([{(])\s*([^,{}()=\"]*?)\s*,")
_FIELD_NAME_RE = re.compile(r"([A-Za-z][\w:.+-]*)\s*=\s*")
_BARE_RE = re.compile(r"[\w.:+-]+")


def _skip(s: str, pos: int) -> int:
    while pos < len(s) and s[pos].isspace():
        pos += 1
    return pos


def _delimited(s: str, pos: int) -> tuple[str, int] | None:
    """The braced (``{…}``) or quoted (``"…"``) value starting at ``pos``
    and the position after it, braces counted the way BibTeX counts them."""
    opener, depth, i = s[pos], 0, pos
    while i < len(s):
        c = s[i]
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and opener == "{":
                return s[pos + 1:i], i + 1
            if depth < 0:
                return None
        elif c == '"' and opener == '"' and depth == 0 and i > pos:
            return s[pos + 1:i], i + 1
        i += 1
    return None


def _parse(text: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """The first entry of ``text``: its type, its key and its fields in
    order, each value as text (a bare macro or number as its name). None when the text
    is not a well-formed entry."""
    s = str(text or "")
    head = _ENTRY_HEAD_RE.search(s)
    if not head:
        return None
    close = "}" if head.group(2) == "{" else ")"
    pos, fields = head.end(), []
    while True:
        pos = _skip(s, pos)
        if pos >= len(s):
            return None
        if s[pos] == close:
            break
        name = _FIELD_NAME_RE.match(s, pos)
        if not name:
            return None
        pos, parts = name.end(), []
        while True:
            pos = _skip(s, pos)
            if pos < len(s) and s[pos] in '{"':
                found = _delimited(s, pos)
                if not found:
                    return None
                parts.append(found[0])
                pos = found[1]
            else:
                bare = _BARE_RE.match(s, pos)
                if not bare:
                    return None
                parts.append(bare.group(0))
                pos = bare.end()
            pos = _skip(s, pos)
            if pos < len(s) and s[pos] == "#":  # "a" # b: concatenation
                pos += 1
                continue
            break
        value = parts[0] if len(parts) == 1 else "".join(parts)
        fields.append((name.group(1), value))
        pos = _skip(s, pos)
        if pos < len(s) and s[pos] == ",":
            pos += 1
    return head.group(1).lower(), head.group(3), fields


# --- writing an entry -------------------------------------------------------

# The entry types every BibTeX style defines; anything else is written as
# @misc, which every style also prints.
_TYPES = {"article", "book", "booklet", "conference", "inbook", "incollection", "inproceedings",
          "manual", "mastersthesis", "misc", "phdthesis", "proceedings", "techreport", "unpublished"}
# The venue field a type cannot do without: apsrev4-2 fails on an @article
# with no journal, and the standard styles warn. Without it the entry is a
# @misc.
_VENUE = {"article": "journal", "inproceedings": "booktitle", "conference": "booktitle",
          "incollection": "booktitle", "phdthesis": "school", "mastersthesis": "school",
          "techreport": "institution"}
# Values copied as written: identifiers and links, which LaTeX reads
# verbatim (`\doi{}`, `\url{}`).
_VERBATIM = {"doi", "url", "eprint", "archiveprefix", "primaryclass", "eprinttype", "eprintclass",
             "isbn", "issn", "file", "urldate", "langid"}
_FIELD_CASE = {"archiveprefix": "archivePrefix", "primaryclass": "primaryClass"}
# The order fields are written in (built entries have always had it); the
# rest follow in the order they came.
_ORDER = ["title", "author", "editor", "journal", "booktitle", "school", "institution", "howpublished",
          "volume", "number", "pages", "publisher", "isbn", "issn", "eprint", "archivePrefix",
          "primaryClass", "year", "month", "doi", "url"]
_MONTHS = ["january", "february", "march", "april", "may", "june", "july", "august", "september",
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


BIBTEX = Dialect(frozenset(_TYPES), _VENUE, _ORDER)
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


def _month(value: str) -> str:
    """A month as BibTeX's macro (`jan` … `dec`): doi.org writes `Sept` and
    `July`, which no style defines. "" when the value names no month."""
    word = str(value or "").strip().lower().rstrip(".")
    if word.isdigit() and 1 <= int(word) <= 12:
        return _MONTHS[int(word) - 1][:3]
    return next((m[:3] for m in _MONTHS if len(word) >= 3 and m.startswith(word)), "")


def format_entry(entry_type: str, key: str, fields: dict, dialect: Dialect = BIBTEX) -> str:
    """One entry as valid BibTeX, in Gamma's layout (a field per line,
    braced values). ``fields`` maps field names to raw values: text in any
    form is made LaTeX (`latex_text`), identifiers stay verbatim, the month
    becomes a macro, a page range takes `--`, a title's capitals inside a
    word are braced (`protect_case`); empty fields are left out.
    The entry type, the venue it needs and the field order are the
    ``dialect``'s."""
    entry_type = entry_type.lower() if entry_type.lower() in dialect.types else "misc"
    values = {}
    for name, value in fields.items():
        name = _FIELD_CASE.get(name.lower(), name.lower())
        text = re.sub(r"\s+", " ", str(value or "")).strip()
        if not text or name in values:
            continue
        if name == "month":
            month = _month(text)
            values[name] = _Macro(month) if month else latex_text(text)
        elif name.lower() in _VERBATIM:
            # Written raw: \url and \doi read their argument verbatim, so a
            # "%20" or "#frag" prints as it is (an escaped \% would print its
            # backslash under the url package and become %5C under biber).
            values[name] = _balanced(text)
        else:
            # A bare macro other than a month (`journal = prl`) is defined by
            # an @string this file does not carry: written as its text.
            if name == "pages":
                text = PAGE_RANGE_RE.sub(r"\1--\2", text)
            values[name] = protect_case(latex_text(text)) if name in _CASE_FIELDS else latex_text(text)
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


def normalize_entry(text: str, record: dict | None = None) -> str:
    """A stored entry (any layout: doi.org's one-line rendering, an entry an
    older Gamma cached, one restored from an export) written out as valid
    BibTeX by `format_entry`, its key kept. A field the entry lacks but the
    metadata ``record`` has is added: `pages` (doi.org's BibTeX leaves out an
    APS article number, which the record carries). An organisation among
    the authors or editors is braced whole (``_brace_organisations``). ""
    when the text is not a well-formed entry; the caller then builds one
    from the record."""
    parsed = _parse(text)
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
            fields[name] = _brace_organisations(str(fields[name]), (record or {}).get("authors") or ())
    return format_entry(entry_type, key or default_key(record or {}), fields)


def page_entry(props: dict) -> str:
    """The BibTeX entry of a page as every surface prints it — the .bib
    export and its review, the AI ``cite`` tool, the Markdown and Obsidian
    exports: the cached entry (``properties.bibtex``) normalized, or,
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
    ``key`` when one is pinned and by ``default_key`` otherwise.

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
    strings, an organisation braced whole (``_bibtex_name``). A record
    without those fields gives the entry it always gave, byte for byte.
    `format_entry` writes it."""
    kind = item_types.item_type(meta)
    venue = str(meta.get("venue") or "").strip()
    arxiv_id = str(meta.get("arxiv_id") or "").strip()
    publisher = item_types.publisher(meta)
    pages = meta.get("pages", "")
    isbn = meta.get("isbn", "")
    volume = meta.get("volume", "")
    fields = {"title": meta.get("title", ""), "author": " and ".join(_bibtex_name(a) for a in _authors(meta))}
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


def _plain(value: str) -> str:
    """A short field value of a stored entry (an issue, a month) as plain
    text: its braces gone, a LaTeX dash a dash (``latex_text`` wrote an
    issue "3–4" as `3--4`), escapes undone."""
    text = re.sub(r"[{}]", "", str(value or ""))
    text = text.replace("---", "—").replace("--", "–")
    return " ".join(re.sub(r"\\([&%#_$])", r"\1", text).split())


def record_from_entry(text: str, year: str = "") -> dict:
    """What an `@article` entry holds that a record of before
    ``meta_fields.NEW_FIELDS`` did not: its issue (`number`) and its month,
    as a record keeps them (``{"issue": "3–4", "month": "09"}``), the keys
    it has only. A month counts only from an entry of a year, and with
    ``year`` given only when the entry's is that year: doi.org's rendering
    may date a paper by its print issue while the record has the online
    year. {} for any other entry, or when it holds neither. What migration
    step 72 promotes into the records of pages whose cached entry carries
    them."""
    parsed = _parse(text)
    if not parsed or parsed[0] != "article":
        return {}
    fields: dict = {}
    for name, value in parsed[2]:
        fields.setdefault(name.lower(), value)
    out = {}
    issue = _plain(fields.get("number"))
    if issue:
        out["issue"] = issue
    month = _month(_plain(fields.get("month")))
    entry_year = _plain(fields.get("year"))
    if month and entry_year and (not year or entry_year == str(year).strip()):
        out["month"] = f"{[m[:3] for m in _MONTHS].index(month) + 1:02d}"
    return out


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


def bibliography(records: list[dict], source: str = "", head: bool = True) -> str:
    """The `.bib` file for ``records`` (``unique_keys`` order): a comment
    naming what was exported, then the entries, one blank line apart. No
    timestamp — an unchanged library re-exports byte-identically, so a
    bibliography kept in a repository or refreshed from a link only shows a
    diff when the metadata really changed. Without ``head``, the entries
    alone, as a selection's copy takes them ("" for none)."""
    parts = [r["text"].strip() for r in records]
    if head:
        line = f"% {len(records)} " + ("entry" if len(records) == 1 else "entries")
        if source:
            line += f" from {source}"
        parts.insert(0, line + ", exported from Gamma")
    return "\n\n".join(parts) + "\n" if parts else ""

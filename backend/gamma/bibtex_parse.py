"""A stored BibTeX entry, read as BibTeX reads it.

``parse_entry`` reads the first entry of a text into its type, its key and
its fields in order: braced and quoted values with their braces counted,
bare macros and numbers, `#` concatenation, and `@type(…)` beside
`@type{…}`. ``bibtex.normalize_entry`` writes what it reads out again as
valid BibTeX. ``record_from_entry`` reads an `@article` back into the
record fields migration step 72 promoted from cached entries. Pure,
standard library only.
"""

import re

from .bibtex_format import MONTHS, month_macro

# The key runs to the comma; `bibtex_keys.clean_key` drops what it may not
# hold.
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


def parse_entry(text: str) -> tuple[str, str, list[tuple[str, str]]] | None:
    """The first entry of ``text``: its type, its key and its fields in
    order, each value as text (a bare macro or number as its name). None
    when the text is not a well-formed entry."""
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
        fields.append((name.group(1), "".join(parts)))
        pos = _skip(s, pos)
        if pos < len(s) and s[pos] == ",":
            pos += 1
    return head.group(1).lower(), head.group(3), fields


def _plain(value: str) -> str:
    """A short field value of a stored entry (an issue, a month) as plain
    text: its braces gone, a LaTeX dash a dash (``bibtex_latex.latex_text``
    wrote an issue "3–4" as `3--4`), escapes undone."""
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
    parsed = parse_entry(text)
    if not parsed or parsed[0] != "article":
        return {}
    fields: dict = {}
    for name, value in parsed[2]:
        fields.setdefault(name.lower(), value)
    out = {}
    issue = _plain(fields.get("number"))
    if issue:
        out["issue"] = issue
    month = month_macro(_plain(fields.get("month")))
    entry_year = _plain(fields.get("year"))
    if month and entry_year and (not year or entry_year == str(year).strip()):
        out["month"] = f"{[m[:3] for m in MONTHS].index(month) + 1:02d}"
    return out

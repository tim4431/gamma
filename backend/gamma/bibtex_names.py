"""The authors of an entry as a BibTeX name list.

The record keeps one string per author (``names``), and BibTeX splits a
name list itself: at its top-level `and`s into names, and each name into
its parts. A person goes in as the record writes them, an organisation
braced whole (``bibtex_name``). ``brace_organisations`` braces the
organisations of a stored name list that an older Gamma wrote bare.
Pure, standard library only.
"""

import re

from . import names
from .bibtex_latex import closing_brace, latex_text


def authors_of(meta: dict) -> list[str]:
    """A record's authors, each stripped, the empty ones left out."""
    return [str(a).strip() for a in (meta.get("authors") or []) if str(a or "").strip()]


# The `and` that separates the names of a name list, as BibTeX finds it:
# a word of its own, at the top level, in any case.
_AND_RE = re.compile(r"\s+and\s+", re.I)


def _organisation(name: str) -> bool:
    """Whether a name is an organisation's of more than one word
    (``names.name_parts`` reads it as a surname alone)."""
    last, given, _ = names.name_parts(name)
    return not given and " " in last


def bibtex_name(name: str) -> str:
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


def brace_organisations(value: str, record_authors=()) -> str:
    """A stored name list (an entry's `author` or `editor`) with every
    organisation in it braced whole, as ``bibtex_name`` writes one: an
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
        bare = not (part.startswith("{") and closing_brace(part, 0) == len(part) - 1)
        if bare and "," not in re.sub(r"\{[^{}]*\}", "", part) and _organisation(part):
            part, changed = "{" + part + "}", True
        out.append(part)
        i += 1
    return " and ".join(out) if changed else value

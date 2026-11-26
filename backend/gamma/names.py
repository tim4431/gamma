"""An author's name as the formats that split it want it.

Gamma keeps an author as one string ("Jan van der Berg", "Lovelace, Ada",
"ATLAS Collaboration"), as the registries and the AI hand it over. The
formats that cite it split it: the Zotero RDF export into Zotero's two name
fields (``zotero_export``), RIS into ``Last, First`` and CSL JSON into
``family``/``given`` or a ``literal`` (``citations``). One splitter serves
them all, so a name splits the same way in every file. BibTeX takes the
string as it is and splits it itself (``bibtex``; an organisation goes in
braces), and the default citation key reads the last word of the surname
part (``bibtex.default_key``, ``joined_family``).

A source that keeps a name in two fields (Zotero's first and last name, a
registry's given and family name) is joined by ``join_name``: "First Last"
while the splitter reads that back to the same surname, else "Last, First",
so a surname of two words or one with a capital particle ("Pereira
Campos", "Den Hengst") keeps the split its source gave it. A Zotero sync
compares a name in ``given_first``'s form, which every way of writing one
split reads alike in (docs/dev/zotero_sync.md "Names").

A list of names shown as text is ``author_list``'s: "; " between them, since
a "Last, First" name holds a comma.
"""

import re

# Lowercase particles that start a surname: "Jan van der Berg" is surname
# "van der Berg", given name "Jan".
_PARTICLES = frozenset("van von der de la du da di del della den ter ten le zu bin al".split())
_SUFFIX = r"(?:Jr\.?|Sr\.?|II|III|IV)"
# A generational suffix stays with the surname, its comma too.
_SUFFIX_RE = re.compile(r"(,?\s+)(" + _SUFFIX + r")$")
# Zotero keeps a suffix in the first name after a comma ("Martin Luther,
# Jr." / "King"), which its two fields joined read "Martin Luther, Jr.
# King"; BibTeX writes the same name "King, Jr., Martin Luther".
_SUFFIX_FIRST_RE = re.compile(r"^([^,]+),\s+(" + _SUFFIX + r")\s+([^,]+)$")
_BIBTEX_SUFFIX_RE = re.compile(r"^([^,]+),\s*(" + _SUFFIX + r"),\s*([^,]+)$")
# A first name that ends in a suffix, as Zotero writes one ("Martin Luther, Jr.").
_GIVEN_SUFFIX_RE = re.compile(r"^([^,]+),\s*(" + _SUFFIX + r")$")
# A word that makes a name an organisation's ("International Labor Office",
# "U.S. Census Bureau"). Words that are also surnames (Service, Board,
# Bank) are left out: a person named so would be cited by the whole name.
_ORG_RE = re.compile(
    r"\b(?:Collaboration|Consortium|Group|Team|Committee|Organi[sz]ation|Institute|University|Association|"
    r"Society|Council|Agency|Foundation|Network|Project|Initiative|Cent(?:er|re)|Laboratory|Inc|Ltd|"
    r"Office|Bureau|Administration|Ministry|Department|Commission|Authority|Survey|Observatory|Federation|"
    r"Corporation|Company|Academy|Academies|Programme|Program|Panel|Secretariat|Fund|Nations)\b", re.I)
# ... of which these are surnames too ("Jean Bureau"): one alone before the
# comma of a "Last, First" name is a person's, as a source that gave the
# name in two fields wrote it (``join_name``).
_SURNAME_ORG = frozenset({"bureau", "office", "panel", "fund", "center", "centre"})
# ... and these, which made no organisation's name before release 72, are a
# person's surname after one given name too ("Jean Bureau", as every name
# stored then reads; "U.S. Census Bureau" and "International Labour Office"
# stay organisations). Center and Centre made one then, and still do.
_SURNAME_ORG_NEW = _SURNAME_ORG - {"center", "centre"}


def _squash(text) -> str:
    return " ".join(str(text or "").split())


def _person_at_comma(name: str) -> bool:
    """Whether a "Last, First" name whose surname is one of ``_SURNAME_ORG``
    ("Bureau, Jean") is a person's: one comma, one word before it, and no
    organisation's word after it."""
    last, sep, given = name.partition(",")
    return (bool(sep) and "," not in given and last.strip().lower() in _SURNAME_ORG
            and bool(given.strip()) and not _ORG_RE.search(given))


def _person_of_two(name: str) -> bool:
    """Whether a "Given Surname" of two words whose surname is one of
    ``_SURNAME_ORG_NEW`` ("Jean Bureau") is a person's: no comma, and a
    given name that is no organisation's word nor a particle."""
    words = name.split(" ")
    return (len(words) == 2 and "," not in name and words[1].lower() in _SURNAME_ORG_NEW
            and words[0] not in _PARTICLES and not _ORG_RE.search(words[0]))


def _read(name) -> tuple[str, str, str, bool]:
    """``name_parts`` with whether the suffix stood after a comma."""
    name = _squash(name)
    if " " not in name:
        return name, "", "", False
    m = _BIBTEX_SUFFIX_RE.match(name) or _SUFFIX_FIRST_RE.match(name)
    if m and not _ORG_RE.search(name):
        last, given = (m[1], m[3]) if m.re is _BIBTEX_SUFFIX_RE else (m[3], m[1])
        return last.strip(), given.strip(), m[2], True
    if _ORG_RE.search(name) and not _person_at_comma(name) and not _person_of_two(name):
        return name, "", "", False
    suffix = _SUFFIX_RE.search(name)
    core = name[:suffix.start()] if suffix else name
    if "," in core:
        last, _, given = core.partition(",")
        last, given = last.strip(), given.strip()
        if not last or not given:
            return last or given, "", suffix.group(2) if suffix else "", bool(suffix and "," in suffix.group(1))
    else:
        words = core.split(" ")
        at = next((i for i, w in enumerate(words[:-1]) if w in _PARTICLES), len(words) - 1)
        last, given = " ".join(words[at:]), " ".join(words[:at])
    return last, given, suffix.group(2) if suffix else "", bool(suffix and "," in suffix.group(1))


def name_parts(name: str) -> tuple[str, str, str]:
    """An author as ``(surname, given name, suffix)``: a ``Last, First``
    name at its comma, else the surname from the first lowercase particle
    on ("van der Berg") or the last word, and a trailing Jr., Sr., II, III
    or IV as the suffix, which also reads in Zotero's place for it, the
    first name ("Martin Luther, Jr. King"), and in BibTeX's ("King, Jr.,
    Martin Luther"). An organisation ("ATLAS Collaboration"; "Bureau,
    Jean" and "Jean Bureau" are a person's, ``_SURNAME_ORG``) or a single
    word is a surname alone, with no given name: a name to print as it
    is."""
    return _read(name)[:3]


def split_name(name: str) -> tuple[str, str]:
    """An author as Zotero's two name fields, ``(surname, given name)``
    (``name_parts``), the suffix kept with the surname as Zotero keeps it
    ("King, Jr." when the name had its comma, "King Jr." when not). An
    organisation or a single word is a surname alone, which Zotero reads as
    a single-field name."""
    last, given, suffix, comma = _read(name)
    if suffix:
        last += (", " if comma else " ") + suffix
    return last, given


def given_first(name: str) -> str:
    """A name read given name first as it splits ("Lovelace, Ada" and "Ada
    Lovelace" are "Ada Lovelace", "Pereira Campos, Jorge Fernando" is
    "Jorge Fernando Pereira Campos", and "Martin Luther, Jr. King", the
    two fields Zotero keeps that name in joined, is "Martin Luther King,
    Jr."): the form a Zotero sync compares a creator in, so every way of
    writing one split reads alike (gamma/zotero_fields.py ``project``)."""
    last, given = split_name(name)
    return f"{given} {last}" if given else last


def sent_form(name: str) -> str:
    """A page's name as a Zotero sync compares it: ``given_first`` of the
    two fields ``split_name`` writes it in, joined "First Last" as an
    item's are, so a name compares as the item it makes reads back."""
    last, given = split_name(name)
    return given_first(f"{given} {last}" if given else last)


def join_name(given, family) -> str:
    """A name kept in two fields as the one string Gamma stores: "Given
    Family" while ``split_name`` reads that back to the same surname, or to
    one that ends in it (a particle the source kept with the given name:
    "Frank van" / "Harmelen" is "Frank van Harmelen"), else "Family, Given",
    which every reader splits at its comma ("Pereira Campos, Jorge
    Fernando", "Den Hengst, Floris", and "Bureau, Anna Maria", which
    "Anna Maria Bureau" would read as an organisation's). A suffix Zotero keeps in the
    first name goes where BibTeX reads it ("Martin Luther, Jr." / "King" is
    "King, Jr., Martin Luther"). Either field alone is the name; the fields are
    stripped, not respaced; any other name with a comma of its own is
    "Given Family". Whichever form it takes reads alike in
    ``given_first`` as "Given Family" does."""
    given, family = str(given or "").strip(), str(family or "").strip()
    if not given or not family:
        return family or given
    surname, first = _squash(family), _squash(given)
    suffixed = _GIVEN_SUFFIX_RE.match(given)
    if suffixed and "," not in family:
        moved = f"{family}, {suffixed[2]}, {suffixed[1].strip()}"
        if split_name(moved) == (f"{surname}, {suffixed[2]}", _squash(suffixed[1])):
            return moved
    name = f"{given} {family}"
    if "," in name:
        return name
    last, read_given = split_name(name)
    if read_given and (last == surname or last.endswith(" " + surname)):
        return name
    if not read_given and surname.lower() not in _SURNAME_ORG:
        return name  # an organisation's, as its source wrote it
    flipped = f"{family}, {given}"
    return flipped if split_name(flipped) == (surname, first) else name


def joined_family(name: str) -> str | None:
    """The surname of a name ``join_name`` writes with a comma ("Pereira
    Campos" of "Pereira Campos, Jorge Fernando", "King" of "King, Jr.,
    Martin Luther"); None for any other, which the default citation key reads
    as it always has, by its last word (``bibtex.default_key``). A name
    stored in that form before release 72 ("Pereira Campos, Jorge Fernando"
    typed, keyed "fernando") keeps its key through its cached entry
    (``bibtex.rebuilt_key``, gamma/migrations.py ``_v72_keyed``)."""
    if "," not in str(name or ""):
        return None
    last, given, suffix, comma = _read(name)
    if not given:
        return None
    first = f"{given}, {suffix}" if suffix and comma else given
    family = last if not suffix or comma else f"{last} {suffix}"
    return last if join_name(first, family) == _squash(name) else None


def family_name(name: str) -> str:
    """The surname a reference prints for an author ("Pereira Campos",
    "van der Berg", "Lovelace"), an organisation's whole name: the label a
    person reads, where a key reads one word (``joined_family``)."""
    return name_parts(name)[0]


def author_list(names, limit: int | None = None, more: str = " et al.") -> str:
    """Names as one line of text: "; " between them, since a "Last, First"
    name holds a comma ("Pereira Campos, Jorge Fernando; Den Hengst,
    Floris"); with ``limit``, the first ``limit`` and ``more`` after them
    when there are others."""
    names = [_squash(n) for n in names or () if _squash(n)]
    shown = names if limit is None else names[:limit]
    return "; ".join(shown) + (more if limit is not None and len(names) > limit else "")

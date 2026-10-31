r"""Zotero Web API item data and Gamma's paper record (``properties.meta``),
mapped both ways: the field rules of a Zotero link (docs/dev/zotero_sync.md).

The Web API has the RDF export's item types, but each type names its
fields its own way: a journal's title is ``publicationTitle``, a conference
paper's ``proceedingsTitle``, a chapter's ``bookTitle``; a thesis is
published by a ``university``, a report by an ``institution``.
``meta_from_item`` reads an item into the record ``zotero_import``'s RDF
import gives for the same item, key for key, with its patterns for arXiv
ids and citation keys, so a library lands the same either way. The way
back writes a field only where the item's type has it, since the API
refuses an item holding a field its type lacks. In short:

- title: the type's title field; authors: its primary creators ("First
  Last", or an organisation's single name), written back split at the last
  space, the item's other creators after them; year: the first four digits
  of ``date`` (a fuller date in that year stays); venue: the type's
  ``publicationTitle`` field, a presentation's ``meetingName``; publisher:
  the type's ``publisher`` field (``item_types.publisher``).
- volume, pages, DOI, ISBN (the first, digits only) where the type has the
  field; a DOI with no field is a ``DOI:`` line in Extra.
- arXiv id: from an arXiv URL or DOI, a preprint's archive ID or an
  ``arXiv:`` line in Extra; written as a preprint's archive ID, else that
  Extra line. Citation key: ``citationKey``, or Better BibTeX's
  ``Citation Key:`` line.
- kind: the item type for Gamma's nine (``item_types``); any other type
  reads as ``document`` and keeps its own type until Gamma's kind changes.

A sync compares three versions of each field. ``project`` reads an item
into that comparable form; ``normalized`` sends a page's record out and
back through the same rules, so a value the type cannot hold is absent on
both sides. ``patch_for`` writes only what changed and leaves the rest as
Zotero has it; ``retype`` changes an item's type as Zotero does. Note the
titles: a new item (``fields_from_meta``) is named by the page's title, a
comparison (``normalized``, ``patch_for``) uses the record's.

``zotero_schema.json`` is the part of https://api.zotero.org/schema read
here (version 42): per type its fields with the base field each stands for,
its creator types and the primary one. To refresh it, run in ``backend/``,
then tests/test_zotero_fields.py::

    import json, urllib.request
    s = json.load(urllib.request.urlopen("https://api.zotero.org/schema"))
    types = {t["itemType"]: {"fields": {f["field"]: f.get("baseField") for f in t["fields"]},
                             "creators": [c["creatorType"] for c in t["creatorTypes"]],
                             "primary": next((c["creatorType"] for c in t["creatorTypes"] if c.get("primary")), None)}
             for t in s["itemTypes"]}
    rows = ",\n".join(f"{json.dumps(k)}:{json.dumps(v, separators=(',', ':'))}" for k, v in sorted(types.items()))
    with open("gamma/zotero_schema.json", "w", encoding="utf-8", newline="\n") as f:
        f.write('{"version":%d,"types":{\n%s\n}}\n' % (s["version"], rows))
"""

import copy
import json
import re
from pathlib import Path

from . import item_types
from .bibtex import clean_key
from .zotero_import import _ARXIV_DOI_RE, _ARXIV_LINE_RE, _ARXIV_URL_RE, _CITATION_KEY_RE

_TYPES: dict = json.loads(Path(__file__).with_name("zotero_schema.json").read_text(encoding="utf-8"))["types"]
# Types that are no work (a file, a note, a reader annotation): never what a
# record is written as.
_NOT_WORKS = frozenset({"annotation", "attachment", "note"})
# Every field some type has: what ``retype`` may move or drop. Any other key
# of an item's data (creators, tags, collections, relations, …) is the item's
# whatever its type.
_ALL_FIELDS = frozenset(field for spec in _TYPES.values() for field in spec["fields"])
# Per type, base field → the type's field for it. A field the type has under
# its own name stands for itself.
_BY_BASE = {
    name: {**{base: field for field, base in spec["fields"].items() if base}, **{f: f for f in spec["fields"]}}
    for name, spec in _TYPES.items()
}

# The fields a sync compares, in the record's order: the record's own keys,
# then the page's citation key.
COMPARE_FIELDS = ("title", "authors", "year", "venue", "volume", "pages", "doi", "arxiv_id",
                  "publisher", "isbn", "kind", "cite_key")

# One ISBN of the several an ISBN field may list: ten or thirteen digits,
# hyphens or spaces between them.
_ISBN_RE = re.compile(r"(?:97[89][-\s]?)?(?:[0-9][-\s]?){9}[0-9X]", re.I)
# The "Label: value" lines of Extra written here, by label as written (and
# the DOI line read back for a type without a DOI field).
_LINES = {label: re.compile(rf"^[ \t]*{re.escape(label)}[ \t]*:[ \t]*(.*)$", re.I | re.M)
          for label in ("Citation Key", "DOI", "arXiv")}


# --- the schema ---------------------------------------------------------------

def _spec(item_type) -> dict:
    return _TYPES.get(item_type or "") or {}


def _has(item_type, field) -> bool:
    return field in (_spec(item_type).get("fields") or ())


def field_for(item_type, base: str) -> str | None:
    """The item type's field for a base field: the field itself when the type
    has it, else the one standing for it (a chapter's ``bookTitle`` for
    ``publicationTitle``, a thesis's ``university`` for ``publisher``); None
    when the type has neither."""
    return _BY_BASE.get(item_type or "", {}).get(base)


def primary_creator(item_type) -> str:
    """The creator type an item type's authors have (a presentation's
    ``presenter``); ``author`` for a type the schema does not name."""
    return _spec(item_type).get("primary") or "author"


def _venue_field(item_type) -> str | None:
    """Where an item type keeps Gamma's venue: its ``publicationTitle`` field,
    or a presentation's meeting."""
    return "meetingName" if item_type == "presentation" else field_for(item_type, "publicationTitle")


def _work_type(item_type, meta: dict) -> str:
    """The Zotero type a record is written as: ``item_type`` when it names a
    work type of the schema (a linked item keeps a type outside Gamma's
    nine), else Gamma's kind for it, a page without a record being a
    document, as in the RDF export."""
    if item_type in _TYPES and item_type not in _NOT_WORKS:
        return item_type
    return item_types.normalize(item_type) or (item_types.item_type(meta) if meta else "document")


# --- reading an item ----------------------------------------------------------

def _text(value) -> str:
    return str(value or "").strip()


def _one_line(value) -> str:
    """Whitespace runs collapsed, as the RDF import reads a title or a publisher."""
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _read(data: dict, item_type, base: str) -> str:
    """The value an item holds for a base field; a type the schema does not
    name is read under the base field's own name."""
    return _text(data.get(field_for(item_type, base) or base))


def _year(date) -> str:
    m = re.search(r"\d{4}", str(date or ""))
    return m.group(0) if m else ""


def _isbn(value: str) -> str:
    """The first ISBN a field lists, digits (and a final X) only."""
    m = _ISBN_RE.search(value)
    return re.sub(r"[^0-9X]", "", (m.group(0) if m else value).upper())


def _extra_doi(extra: str) -> str:
    m = _LINES["DOI"].search(extra)
    words = m.group(1).split() if m else []
    return words[0] if words else ""


def _creator_name(creator: dict) -> str:
    """A creator as Gamma lists an author: an organisation's single name, or
    "First Last" (the RDF import joins given name and surname the same way)."""
    name = _text(creator.get("name"))
    return name or " ".join(filter(None, (_text(creator.get("firstName")), _text(creator.get("lastName")))))


def _arxiv_id(data: dict, doi: str, extra: str) -> str:
    """The arXiv id an item names, found as the RDF import finds it: an
    arXiv URL or DOI, a preprint's archive ID, a line of Extra."""
    m = (_ARXIV_URL_RE.search(_text(data.get("url"))) or _ARXIV_DOI_RE.match(doi)
         or _ARXIV_LINE_RE.search(_text(data.get("archiveID"))) or _ARXIV_LINE_RE.search(extra))
    return m.group(1) if m else ""


def meta_from_item(data: dict) -> dict:
    """An item's ``data`` (Web API JSON) as Gamma's paper record: the keys,
    in the order and with the meaning ``zotero_import.parse_zotero_rdf``
    gives them, "" where the item holds nothing, and ``kind`` only for a
    work (a standalone attachment has none)."""
    item_type = data.get("itemType") or ""
    extra = str(data.get("extra") or "")
    doi = _read(data, item_type, "DOI")
    if not doi and not _has(item_type, "DOI"):
        doi = _extra_doi(extra)
    primary = primary_creator(item_type)
    meta = {
        "title": _one_line(data.get(field_for(item_type, "title") or "title")),
        "authors": [name for c in data.get("creators") or []
                    if c.get("creatorType") == primary and (name := _creator_name(c))],
        "year": _year(_read(data, item_type, "date")),
        "venue": _text(data.get(_venue_field(item_type) or "publicationTitle")),
        "volume": _read(data, item_type, "volume"),
        "pages": _read(data, item_type, "pages"),
        "doi": doi,
        "arxiv_id": _arxiv_id(data, doi, extra),
        "publisher": _one_line(data.get(field_for(item_type, "publisher") or "publisher")),
        "isbn": _isbn(_read(data, item_type, "ISBN")),
        "source": "zotero",
    }
    kind = item_types.from_zotero(item_type)
    if kind:
        meta["kind"] = kind
    return meta


def cite_key_from_item(data: dict) -> str:
    """The citation key pinned on an item: its ``citationKey``, else the
    ``Citation Key:`` line Better BibTeX keeps in Extra (read as the RDF
    import reads it), cleaned by ``bibtex.clean_key``; "" for none."""
    key = clean_key(data.get("citationKey") or "")
    m = None if key else _CITATION_KEY_RE.search(str(data.get("extra") or ""))
    return clean_key(m.group(1)) if m else key


def project(data: dict) -> dict:
    """An item in the form a sync compares and keeps as its base:
    ``{"meta": the record without its source, "cite_key": str}``."""
    meta = meta_from_item(data)
    del meta["source"]
    return {"meta": meta, "cite_key": cite_key_from_item(data)}


# --- writing an item ----------------------------------------------------------

def _authors(meta: dict) -> list[str]:
    authors = meta.get("authors")
    return [_one_line(a) for a in authors if _one_line(a)] if isinstance(authors, list) else []


def _person(creator_type: str, name: str) -> dict:
    """A name as a Zotero creator: split at the last space, as the RDF export
    splits one, and whole when it has none."""
    parts = name.split()
    if len(parts) > 1:
        return {"creatorType": creator_type, "firstName": " ".join(parts[:-1]), "lastName": parts[-1]}
    return {"creatorType": creator_type, "name": name}


def _creators(current: list, primary: str, names: list[str]) -> list:
    """Gamma's authors as the item's ``primary`` creators, followed by its
    other creators as they were. An author the item already lists keeps its
    entry, so a name Zotero splits its own way ("Ludwig" / "van Beethoven")
    is not split again."""
    listed = {}
    for c in current:
        if c.get("creatorType") == primary:
            listed.setdefault(_creator_name(c), []).append(c)
    authors = [listed[name].pop(0) if listed.get(name) else _person(primary, name) for name in names]
    return authors + [c for c in current if c.get("creatorType") != primary]


def _publisher(meta: dict, item_type: str) -> str:
    """The publisher Gamma writes into an item type: ``item_types.publisher``
    read as that type (an older thesis record's venue is its university),
    and arXiv for an arXiv preprint that names none."""
    name = item_types.publisher({**meta, "kind": item_types.from_zotero(item_type)})
    if not name and item_type == "preprint" and _text(meta.get("arxiv_id")):
        name = "arXiv"
    return name


def _with_line(extra: str, label: str, value: str, add: bool = True) -> str:
    """Extra with its ``Label:`` line saying ``value``: the first such line
    rewritten in place (or left alone when it already says so), repeats
    dropped, and the line appended when there was none and ``add``; every
    such line removed when ``value`` is ""."""
    pattern, out, placed = _LINES[label], [], False
    for line in extra.split("\n") if extra else []:
        m = pattern.match(line)
        if not m:
            out.append(line)
        elif value and not placed:
            out.append(line if m.group(1).split()[:1] == [value] else f"{label}: {value}")
            placed = True
    if value and add and not placed:
        out.append(f"{label}: {value}")
    return "\n".join(out)


def _archive_id(item: dict) -> str:
    """The arXiv id a preprint's archive ID names, "" for none."""
    m = _ARXIV_LINE_RE.match(_text(item.get("archiveID")))
    return m.group(1) if m else ""


def _writes(item: dict, meta: dict, cite_key: str, title: str, fields) -> dict:
    """The fields that make ``item`` hold the record's values for the
    compare-fields in ``fields``, written into the item's own type and only
    where they differ from what it holds. ``title`` is the title to write."""
    item_type = item.get("itemType") or ""
    work, out = dict(item), {}

    def put(field, value):
        if field and _has(item_type, field) and (work.get(field) or "") != value:
            work[field] = out[field] = value

    def put_line(label, value, add=True):
        put("extra", _with_line(str(work.get("extra") or ""), label, value, add))

    if "title" in fields:
        put(field_for(item_type, "title"), title)
    if "authors" in fields:
        primary = primary_creator(item_type)
        current = work.get("creators") or []
        creators = _creators(current, primary, _authors(meta))
        if primary in _spec(item_type).get("creators", ()) and creators != current:
            work["creators"] = out["creators"] = creators
    if "year" in fields:
        year, date_field = _text(meta.get("year")), field_for(item_type, "date")
        if _year(work.get(date_field)) != _year(year):  # a fuller date in Gamma's year stays
            put(date_field, year)
    if "venue" in fields:
        put(_venue_field(item_type), _text(meta.get("venue")))
    for name in ("volume", "pages"):
        if name in fields:
            put(field_for(item_type, name), _text(meta.get(name)))
    if "doi" in fields:
        doi = _text(meta.get("doi"))
        if _has(item_type, "DOI"):
            put("DOI", doi)
        else:
            put_line("DOI", doi)
    if "isbn" in fields:
        isbn = _text(meta.get("isbn"))
        if _isbn(_text(work.get("ISBN"))) != _isbn(isbn):
            put("ISBN", isbn)
    if "publisher" in fields:
        put(field_for(item_type, "publisher"), _publisher(meta, item_type))
    if "arxiv_id" in fields:
        arxiv = _text(meta.get("arxiv_id"))
        url = _text(work.get("url"))
        named = _ARXIV_URL_RE.search(url)
        if (named and named.group(1) != arxiv) or (not url and arxiv and item_type == "preprint"):
            # An arXiv URL that names another paper follows Gamma's id (or goes).
            put("url", f"https://arxiv.org/abs/{arxiv}" if arxiv else "")
        if item_type == "preprint":
            archive = _archive_id(work)
            if arxiv and archive != arxiv:
                put("archiveID", f"arXiv:{arxiv}")
            elif not arxiv and archive:
                put("archiveID", "")
            put_line("arXiv", arxiv, add=False)
        else:
            put_line("arXiv", arxiv)
    if "cite_key" in fields:
        key = clean_key(cite_key)
        if _has(item_type, "citationKey"):
            put("citationKey", key)
            put_line("Citation Key", key, add=False)  # an older pin in Extra follows it
        else:
            put_line("Citation Key", key)
    return out


def fields_from_meta(meta: dict, cite_key: str = "", item_type: str | None = None, title: str = "") -> dict:
    """The data of a new Zotero item holding a page's record, without key,
    version, tags or collections: ``itemType`` and the fields of that type
    that hold a value. The type is ``item_type`` (a Zotero type, or one of
    Gamma's kinds), else Gamma's kind for the record. ``title`` is the
    page's title, which names a new item as in the RDF export; the record's
    own title stands in when it is ""."""
    meta = meta or {}
    item = {"itemType": _work_type(item_type, meta)}
    item.update(_writes(item, meta, cite_key, _text(title) or _text(meta.get("title")), COMPARE_FIELDS))
    return item


def normalized(meta: dict, cite_key: str = "", item_type: str | None = None, title: str = "") -> dict:
    """A page's record in ``project``'s form: written as a new item and read
    back, so a value its type cannot hold compares as absent, as on the
    item. The record's title is compared, the page's ``title`` only when the
    record has none. ``item_type`` as in ``fields_from_meta``: for a linked
    item outside Gamma's nine whose page still reads ``document``, pass the
    item's own type, so what that type holds compares too."""
    meta = meta or {}
    return project(fields_from_meta(meta, cite_key, item_type, _text(meta.get("title")) or title))


def retype(remote: dict, item_type: str) -> dict:
    """The whole of ``remote`` (an item's data) as an item of ``item_type``,
    for a PUT, changed the way Zotero changes an item's type: a field the
    new type has stays, one it has under another name moves there by base
    field (a journal's ``publicationTitle`` becomes a conference paper's
    ``proceedingsTitle``), Gamma's venue goes to a presentation's
    ``meetingName``, the rest is dropped; a preprint's arXiv archive ID
    becomes an Extra line. The primary creators become the new type's,
    other creators it does not allow contributors. Keys that are no field
    (creators, tags, collections, relations, …) stay. ValueError when
    ``item_type`` names no work type."""
    new = item_type if item_type in _TYPES else item_types.normalize(item_type)
    if not new or new in _NOT_WORKS:
        raise ValueError(f"not a Zotero item type to write: {item_type!r}")
    old = remote.get("itemType") or ""
    old_fields, new_spec = _spec(old).get("fields") or {}, _TYPES[new]
    out = {k: copy.deepcopy(v) for k, v in remote.items() if k not in _ALL_FIELDS or k in new_spec["fields"]}
    out["itemType"] = new

    def move(value, field):
        if value and field and not out.get(field):
            out[field] = value

    old_venue = _venue_field(old)
    move(remote.get(old_venue or ""), _venue_field(new))
    archive = _archive_id(remote)
    for name, value in remote.items():
        if name in out or name not in _ALL_FIELDS or name == old_venue:
            continue
        if name == "archiveID" and archive:
            if "extra" in new_spec["fields"]:
                out["extra"] = _with_line(str(out.get("extra") or ""), "arXiv", archive)
            continue
        move(value, field_for(new, old_fields.get(name) or name))

    old_primary, new_primary = primary_creator(old), new_spec["primary"]
    allowed = new_spec["creators"]

    def creator_type(name):
        if name == old_primary:
            return new_primary
        if name in allowed:
            return name
        return "contributor" if "contributor" in allowed else new_primary

    if remote.get("creators"):
        out["creators"] = [{**c, "creatorType": creator_type(c.get("creatorType"))} for c in remote["creators"]]
    return out


def patch_for(remote: dict, meta: dict, cite_key: str, changed, title: str = "") -> tuple[dict, bool]:
    """``(body, replace)``: what to write so ``remote`` (an item's data)
    holds the page's values for the compare-fields in ``changed``, and
    nothing else changes (the other creators, a full date in Gamma's year,
    the other lines of Extra and every field Gamma does not model stay).
    The record's title is written, the page's ``title`` when it has none.
    ``replace`` False: ``body`` is the changed fields only, a PATCH ({}
    when nothing differs). True when ``kind`` changed to a kind the item
    does not read as (an item outside the nine reads as ``document``):
    ``body`` is the whole item retyped (``retype``) with the other changes
    on it, a PUT."""
    meta = meta or {}
    item, body, replace = remote, {}, False
    if "kind" in changed:
        kind = _work_type(None, meta)
        if item_types.from_zotero(remote.get("itemType")) != kind:
            item = retype(remote, kind)
            body, replace = dict(item), True
    body.update(_writes(item, meta, cite_key, _text(meta.get("title")) or _text(title), changed))
    return body, replace

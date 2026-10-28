"""Parse a Zotero RDF library export into plain dicts the import endpoint acts on.

The source is Zotero's File → Export Library → "Zotero RDF" (with "Export
Files" and "Export Notes"): a .rdf file plus a files/<n>/<name>.pdf tree, which
the user zips and uploads. The RDF is item-centric — bibliographic elements
(bib:Article, bib:Book, rdf:Description for preprints, …) reference
z:Attachment elements (the files, a z:path each, and the web links, a URL
and no z:path) via link:link and bib:Memo elements (the notes) via
dcterms:isReferencedBy; z:Collection elements list their members
(items AND child collections) via dcterms:hasPart. Annotations made in
Zotero's reader are NOT in the RDF: "Include Annotations" embeds them into the
exported PDF copies, where the existing embedded-annotations importer
(routers/imports.py) picks them up.

Collections map onto Gamma's folders (both are trees, both allow an item in
several places; an item's ``folders`` are its collections' paths, each a list
of collection names from the top), Zotero tags onto labels by name. An
item's first PDF becomes its page's document; its other attachments become
file chips and links on that page (``plan_zotero_archive``'s ``extras``).
A note becomes a block tree (``zotero_notes.note_tree``), and a standalone
note, one with no item above it, a page of its own.

``meta`` is Gamma's paper record and holds no more than it does for any
other source. What Zotero holds beyond it (the abstract, the issue, editors,
the full date, Extra, the URL, the names as Zotero split them, fields Gamma
has never heard of) travels in the item's ``rdf``, which the page keeps as
``properties.zotero_rdf`` for a later Zotero RDF export to write back
(``_pass_through``).
"""

import hashlib
import html as html_mod
import json
import posixpath
import re
import unicodedata
import urllib.parse
import xml.etree.ElementTree as ET

from . import item_types
from .bibtex import clean_key
from .note_markup import LINK_PARTS
from .zotero_notes import _elements, _href, note_tree, title_text

_RDF = "{http://www.w3.org/1999/02/22-rdf-syntax-ns#}"
_Z = "{http://www.zotero.org/namespaces/export#}"
_DC = "{http://purl.org/dc/elements/1.1/}"
_DCT = "{http://purl.org/dc/terms/}"
_BIB = "{http://purl.org/net/biblio#}"
_FOAF = "{http://xmlns.com/foaf/0.1/}"
_LINK = "{http://purl.org/rss/1.0/modules/link/}"
_PRISM = "{http://prismstandard.org/namespaces/1.2/basic/}"

# Same id shape as routers/pdf.py's ARXIV_ID (a copy: gamma.* modules don't
# import the routers package at load) — keep the two in sync.
_ARXIV_ID = r"(?:[0-9]{4}\.[0-9]{4,5}|[a-z][a-z.-]*/[0-9]{7})"
_ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/(" + _ARXIV_ID + ")", re.I)
# Zotero records arXiv preprints with a DataCite DOI: 10.48550/arXiv.<id>
_ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.([0-9]{4}\.[0-9]{4,5})$", re.I)
# A preprint's z:archiveID ("arXiv:<id>"), and the "arXiv: <id>" line Zotero's
# arXiv translator writes into the Extra of a paper's published version.
_ARXIV_LINE_RE = re.compile(r"^[ \t]*arxiv:[ \t]*(" + _ARXIV_ID + r")(?:v\d+)?[ \t]*$", re.I | re.M)
# Better BibTeX's pinned key: a line of Zotero's Extra field.
_CITATION_KEY_RE = re.compile(r"^[ \t]*citation key[ \t]*:[ \t]*(\S+)", re.I | re.M)

# An <a href> and its label; the href in either quote, or in none.
_LINK_TAG_RE = re.compile(r"""(?is)<a\b[^>]*?\bhref\s*=\s*(?:"([^"]*)"|'([^']*)'|([^\s"'>]+))[^>]*>(.*?)</a\s*>""")


def _link_md(m) -> str:
    """An <a> as markdown: ``[label](href)``, the target as a note's link
    holds it (``zotero_notes``); the label alone for a script URL, the
    target alone for no label. The target's "&" and "<" stay escaped for
    the unescape after the tags are gone."""
    href = _href(html_mod.unescape(next(g for g in m.groups()[:3] if g is not None)))
    label = m.group(4)
    if not href:
        return label
    href = html_mod.escape(href, quote=False)
    return f"[{label}]({href})" if re.sub(r"<[^>]+>", "", label).strip() else href


def html_note_text(html_text: str) -> str:
    """HTML (a PDF annotation's rich text, a note) → the markdown-ish text
    one Gamma block holds: paragraphs and list items as lines, a nested
    list's items on lines of their own, bold, italic and links as markdown,
    everything else stripped. A Zotero note itself comes in as a block tree
    (``zotero_notes.note_tree``)."""
    s = (html_text or "").replace("\x00", "")
    s = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", "", s)
    s = re.sub(r"(?i)<br\s*/?>", "\n", s)
    s = re.sub(r"(?i)</(p|div|li|h[1-6]|blockquote|tr)>", "\n", s)
    s = re.sub(r"(?i)<li\b[^>]*>", "\x00- ", s)  # starts a line, after an item's own text too
    s = re.sub(r"(?is)<(strong|b)\b[^>]*>(.*?)</\1>", r"**\2**", s)
    s = re.sub(r"(?is)<(em|i)\b[^>]*>(.*?)</\1>", r"*\2*", s)
    s = _LINK_TAG_RE.sub(_link_md, s)
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"\s*\x00", "\n", s)
    s = html_mod.unescape(s)
    s = re.sub(r"[ \t]+\n", "\n", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


# The deepest note tree kept as a tree. What reads one later (the import's
# copy of it, the export's note_html) recurses once or more per level, and
# Python's default limit of 1000 frames fails near 250 levels.
NOTE_DEPTH_MAX = 200


def _depth(tree: dict) -> int:
    deepest, todo = 0, [(tree, 1)]
    while todo:
        node, depth = todo.pop()
        deepest = max(deepest, depth)
        todo.extend((child, depth + 1) for child in node.get("children") or ())
    return deepest


def _note(html_text: str, warnings: list):
    """A note's block tree. One nested deeper than NOTE_DEPTH_MAX (no note
    editor writes one; other tools might) comes in as its text in one block,
    with a warning, rather than failing its item or the library's import."""
    try:
        tree = note_tree(html_text)
        if tree is None or _depth(tree) <= NOTE_DEPTH_MAX:
            return tree
    except RecursionError:
        pass
    text = html_note_text(html_text)
    if not text:
        return None
    warnings.append({"reason": "A note nested too deeply to keep its structure came in as plain text"})
    return {"content": text, "children": []}


def _work_title(title) -> str:
    """A title as two records of one work spell it alike: case, Unicode
    compatibility forms, punctuation and spacing aside."""
    text = unicodedata.normalize("NFKC", str(title or "").casefold())
    return re.sub(r"[\W_]+", " ", text).strip()


def same_work(a: dict, b: dict) -> bool:
    """Whether two records (``meta`` dicts, or ``{"title", "doi"}``) are of
    one work: by DOI when both have one, ignoring case; else by title, which
    both must have (``_work_title``)."""
    a, b = a or {}, b or {}
    doi_a, doi_b = (str(r.get("doi") or "").strip().casefold() for r in (a, b))
    if doi_a and doi_b:
        return doi_a == doi_b
    title = _work_title(a.get("title"))
    return bool(title) and title == _work_title(b.get("title"))


def _identifiers(el) -> list[str]:
    """dc:identifier values: plain text ("DOI 10.…", "ISSN …") and the nested
    dcterms:URI/rdf:value form both occur."""
    out = []
    for ident in el.findall(f"{_DC}identifier"):
        if ident.text and ident.text.strip():
            out.append(ident.text.strip())
        v = ident.find(f"{_DCT}URI/{_RDF}value")
        if v is not None and v.text:
            out.append(v.text.strip())
    return out


def _container_fields(el) -> dict:
    return {
        "venue": (el.findtext(f"{_DC}title") or "").strip(),
        "volume": (el.findtext(f"{_PRISM}volume") or "").strip(),
        "idents": _identifiers(el),
    }


def _container(el, records: dict):
    """The record an item is part of, the journal or book whose title is
    its venue (Zotero puts the item's DOI and ISBN there too), and the
    dcterms:isPartOf naming it: the first whose target, inline or a record
    of its own referenced by rdf:resource, is no bib:Series (a book names
    its series the same way). (None, None) for none."""
    for part in el.findall(f"{_DCT}isPartOf"):
        ref = part.get(f"{_RDF}resource")
        target = records.get(ref) if ref else next(iter(part), None)
        if target is not None and target.tag != f"{_BIB}Series":
            return part, target
    return None, None


def _labelled(idents: list[str], label: str) -> str:
    """The value of a labelled identifier ("DOI 10.…", "ISBN 978…")."""
    head = label.upper() + " "
    return next((i[len(head):].strip() for i in idents if i.upper().startswith(head)), "")


def _publisher(el) -> str:
    """The publisher (a thesis's university, a report's institution): Zotero
    writes ``dc:publisher`` as a ``foaf:Organization`` with a ``foaf:name``;
    a plain-text one is read too."""
    pub = el.find(f"{_DC}publisher")
    if pub is None:
        return ""
    return re.sub(r"\s+", " ", pub.findtext(f".//{_FOAF}name") or pub.text or "").strip()


def _tag(subject) -> str | None:
    """A dc:subject's tag: its text, or the value of the z:AutomaticTag it
    holds. None for a subject that is no tag: a call number (a dcterms:LCC
    node), another typed node, a resource."""
    if len(subject):
        return ((subject.findtext(f"{_Z}AutomaticTag/{_RDF}value") or "").strip()
                if subject[0].tag == f"{_Z}AutomaticTag" else None)
    return None if subject.get(f"{_RDF}resource") else (subject.text or "").strip()


def _tags(el) -> list[str]:
    tags = []
    for subject in el.findall(f"{_DC}subject"):
        tag = _tag(subject)
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _attachment_path(el) -> str:
    """An attachment's file, relative to the .rdf: its z:path (an
    rdf:resource attribute or text), else the rdf:resource element older
    Zotero versions wrote, which Zotero's own importer reads too."""
    for tag in (f"{_Z}path", f"{_RDF}resource"):
        found = el.find(tag)
        path = (found.get(f"{_RDF}resource") or found.text or "").strip() if found is not None else ""
        if path:
            return path
    return ""


# --- the pass-through: what one Zotero export keeps for the next ------------

# An item's elements the import reads into its fields, never passed through
# (nor written back from one: zotero_export); a dc:subject, prism:volume or
# dc:identifier as _pass_through says.
MAPPED = {f"{_Z}itemType", f"{_DC}title", f"{_BIB}authors", f"{_DC}date", f"{_BIB}pages",
          f"{_DC}publisher", f"{_DC}description", f"{_LINK}link", f"{_DCT}isReferencedBy"}
CONTAINER_MAPPED = {f"{_DC}title", f"{_PRISM}volume"}

# The largest pass-through a page keeps, as JSON, and the most of one an
# export writes back.
PASS_THROUGH_BYTES = 64 * 1024
_TOO_LARGE = "Some Zotero fields are too large to keep for a later Zotero export"


def _serialized(el) -> str:
    """An element as XML text, without the whitespace after it."""
    el.tail = None
    return ET.tostring(el, encoding="unicode")


def _regenerated(ident, meta: dict) -> bool:
    """Whether the export writes this dc:identifier back from ``meta``: the
    record's DOI or ISBN, or a preprint's arXiv abstract URL."""
    label, _, value = (ident.text or "").strip().partition(" ")
    if label.upper() == "DOI":
        return bool(meta["doi"]) and value.strip().casefold() == meta["doi"].casefold()
    if label.upper() == "ISBN":
        return bool(meta["isbn"]) and re.sub(r"[^0-9X]", "", value.upper()) == meta["isbn"]
    uri = (ident.findtext(f"{_DCT}URI/{_RDF}value") or "").strip()
    return bool(meta["arxiv_id"]) and uri == f"https://arxiv.org/abs/{meta['arxiv_id']}"


def _pass_through(el, part, container, meta: dict) -> dict:
    """What an item holds that its ``meta`` does not, for a later Zotero RDF
    export to write back. ``work`` is the record it was read with, so a page
    whose record later names another work does not carry it out. Then: the
    item's type and RDF classes when Gamma reads the type as "document" (for
    its own types the export writes the classes Zotero does); the date when
    it says more than the year; the creators as Zotero split them; the
    publisher when it holds more than its name (Zotero's place, a
    ``vcard:adr`` in it); Extra when it holds more than the citation key;
    and, serialized, each element
    of the item (``item``) and of its container (``container``) that the
    import reads into none of the item's fields and the export does not
    write back (the record's DOI and ISBN, a preprint's abstract URL)."""
    rdf = {"v": 1, "work": {"title": meta["title"], "doi": meta["doi"]}}
    item_type = (el.findtext(f"{_Z}itemType") or "").strip()
    if meta.get("kind") != item_type:
        rdf["type"] = item_type
        if el.tag != f"{_RDF}Description":
            rdf["class"] = el.tag
        if container is not None:
            rdf["container_class"] = container.tag
    date = (el.findtext(f"{_DC}date") or "").strip()
    if date and date != meta["year"]:
        rdf["date"] = date
    authors = el.find(f"{_BIB}authors")
    if authors is not None:
        rdf["authors"] = {"names": list(meta["authors"]), "xml": _serialized(authors)}
    publisher = el.find(f"{_DC}publisher")
    if publisher is not None and any(d.tag not in (f"{_FOAF}Organization", f"{_FOAF}name")
                                     for d in publisher.iter() if d is not publisher):
        rdf["publisher"] = {"name": _publisher(el), "xml": _serialized(publisher)}
    extra = el.findtext(f"{_DC}description") or ""
    if _CITATION_KEY_RE.sub("", extra, count=1).strip():
        rdf["extra"] = extra
    # The item's own volume is meta's when its container names none.
    own_volume = container is None or not (container.findtext(f"{_PRISM}volume") or "").strip()
    item = [_serialized(child) for child in el
            if child.tag not in MAPPED and child is not part
            and not (child.tag == f"{_DC}subject" and _tag(child) is not None)
            and not (child.tag == f"{_PRISM}volume" and own_volume)
            and not (child.tag == f"{_DC}identifier" and _regenerated(child, meta))]
    if item:
        rdf["item"] = item
    if container is not None:
        kept = [_serialized(child) for child in container
                if child.tag not in CONTAINER_MAPPED
                and not (child.tag == f"{_DC}identifier" and _regenerated(child, meta))]
        if kept:
            rdf["container"] = kept
    return rdf


def _kept(rdf: dict, title: str, warnings: list) -> dict | None:
    """The pass-through as a page keeps it: None when it holds only ``v``
    and ``work`` (the export writes the rest anyway); else no larger than
    PASS_THROUGH_BYTES as JSON, its largest fields (serialized elements,
    the creators, Extra) dropped until it fits, which warns on the item."""
    size = len(json.dumps(rdf))
    if size > PASS_THROUGH_BYTES:
        fields = [(len(json.dumps(v)), key, i) for key in ("item", "container")
                  for i, v in enumerate(rdf.get(key, ()))]
        fields += [(len(json.dumps(rdf[key])), key, None) for key in ("authors", "publisher", "extra") if key in rdf]
        drop = set()
        for n, key, i in sorted(fields, key=lambda f: f[0], reverse=True):
            if size <= PASS_THROUGH_BYTES:
                break
            drop.add((key, i))
            size -= n
        for key in ("item", "container"):
            kept = [v for i, v in enumerate(rdf.pop(key, ())) if (key, i) not in drop]
            if kept:
                rdf[key] = kept
        for key in ("authors", "publisher", "extra"):
            if (key, None) in drop:
                del rdf[key]
        if not any(w["reason"] == _TOO_LARGE for w in warnings):
            warnings.append({"title": title, "reason": _TOO_LARGE})
    return rdf if set(rdf) - {"v", "work"} else None


# A line no title is made of: a table's delimiter row, a rule, display math's $$.
_NO_TITLE_RE = re.compile(r"[\s|:*_$-]*")


def _note_page(tree: dict) -> tuple[str, list]:
    """A standalone note as its page: the title, the note's first line as
    text, and the blocks, the rest of the note. A first block that is a
    heading or a paragraph is the title by its first line and the page's
    first block by its other lines (``note_tree`` closes a mark at each
    line's end, so the lines part cleanly). Any other first block (a table,
    code, math, a quote), and one whose first line holds a picture or a
    link, which a title as text would lose, stays whole as the page's first
    block, the title its first line of text."""
    content, children = tree["content"], tree["children"]
    first, _, rest = content.partition("\n")
    elements = _elements(content)
    if (elements and elements[0][0] in ("heading", "para") and "![" not in first
            and not LINK_PARTS.search(first)):
        rest = rest.strip("\n")
        return title_text(first) or "Untitled", ([{"content": rest, "children": []}] if rest.strip() else []) + children
    lines = (title_text(re.sub(r"(?<!\\)\|", " ", line)) for line in content.split("\n")
             if not _NO_TITLE_RE.fullmatch(line))
    return next((line for line in lines if line), "Untitled"), [{"content": content, "children": []}] + children


def parse_zotero_rdf(text: str) -> list[dict]:
    """→ one dict per item: {key, title, meta, cite_key, tags, folders,
    attachments, pdf_paths, notes, standalone_note, rdf, warnings}.
    ``attachments`` are the item's, in the export's order: ``{key, path,
    pdf, title, url}``, ``path`` the file's z:path ("" for a web link),
    ``url`` its URL; ``pdf_paths`` the PDFs' paths among them. ``notes``
    are block trees (``zotero_notes.note_tree``): ``{key, text, children}``,
    ``text`` the root block's content; an empty note is none. ``rdf`` is
    the pass-through (``_pass_through``), None when the export would write
    all of it anyway; ``warnings`` the item's own.

    A standalone attachment (one no item links) is an item of its own, with
    no pass-through. So is a standalone note, a bib:Memo no item references
    (in Zotero, a note in a collection with no item above it):
    ``standalone_note`` True, no record (``meta`` {}, no ``cite_key``),
    titled by its first line as text, ``note`` the rest of its tree
    (``_note_page``), its tags and collections its own."""
    root = ET.fromstring(text)
    attachments, memos, records, collections = {}, {}, {}, {}
    raw_items = []
    attachment_elements = {}
    # Attachments can be top-level resources or inline link:link children.
    for el in root.iter():
        if el.tag != f"{_Z}Attachment" and el.findtext(f"{_Z}itemType") != "attachment":
            continue
        about = el.get(f"{_RDF}about") or ""
        path = _attachment_path(el)
        mime = (el.findtext(f"{_LINK}type") or el.findtext(f"{_DCT}type") or "").strip().lower()
        url = next((i for i in _identifiers(el) if i.lower().startswith(("http://", "https://"))), "")
        attachments[about] = {"path": path, "pdf": mime == "application/pdf" or path.lower().endswith(".pdf"),
                              "title": re.sub(r"\s+", " ", el.findtext(f"{_DC}title") or "").strip(), "url": url}
        attachment_elements[about] = el
    for el in root:
        about = el.get(f"{_RDF}about") or ""
        item_type = (el.findtext(f"{_Z}itemType") or "").strip()
        if el.tag == f"{_Z}Collection":
            collections[about] = {
                "title": (el.findtext(f"{_DC}title") or "").strip() or "untitled",
                "parts": [p.get(f"{_RDF}resource") for p in el.findall(f"{_DCT}hasPart")],
            }
        elif el.tag == f"{_Z}Attachment" or item_type == "attachment":
            pass
        elif el.tag == f"{_BIB}Memo" or item_type == "note":
            memos[about] = el
        elif item_type:
            raw_items.append((about, el))
        elif about:
            # records of their own (bib:Journal …), referenced via isPartOf
            records[about] = el

    # Collection tree → path (names from the top) per collection (hasPart
    # links child collections)
    parent_of = {}
    for key, col in collections.items():
        for part in col["parts"]:
            if part in collections:
                parent_of[part] = key

    def col_path(key):
        parts, seen = [], set()
        while key in collections and key not in seen:
            seen.add(key)  # cycle guard — malformed exports shouldn't hang us
            parts.append(collections[key]["title"])
            key = parent_of.get(key)
        return parts[::-1]

    item_folders = {}
    for key, col in collections.items():
        path = col_path(key)
        for part in col["parts"]:
            if part not in collections and path:
                item_folders.setdefault(part, []).append(path)

    def linked_attachments(el):
        for link in el.findall(f"{_LINK}link"):
            ref = link.get(f"{_RDF}resource")
            if ref:
                yield ref
            else:
                for child in link:
                    yield child.get(f"{_RDF}about") or ""

    linked = {key for _, el in raw_items for key in linked_attachments(el)}
    raw_items.extend((key, el) for key, el in attachment_elements.items()
                     if key not in linked and (attachments[key]["path"] or attachments[key]["url"]))
    items, referenced = [], set()
    for about, el in raw_items:
        authors = []
        for person in el.findall(f"{_BIB}authors//{_FOAF}Person"):
            name = " ".join(filter(None, [
                (person.findtext(f"{_FOAF}givenName") or "").strip(),
                (person.findtext(f"{_FOAF}surname") or "").strip(),
            ]))
            if name:
                authors.append(name)

        idents = _identifiers(el)
        url = next((i for i in idents if i.lower().startswith("http")), "")
        if not url and about.lower().startswith("http"):
            url = about
        # venue/volume come from the container record (a journal, the book a
        # chapter is in). Zotero puts the item's DOI and ISBN on that record,
        # not on the item itself.
        part, container_el = _container(el, records)
        container = _container_fields(container_el) if container_el is not None else {}
        idents_all = idents + container.get("idents", [])
        doi = _labelled(idents_all, "DOI")
        isbn = re.sub(r"[^0-9X]", "", _labelled(idents_all, "ISBN").upper())

        year_match = re.search(r"\d{4}", el.findtext(f"{_DC}date") or "")
        arxiv = (_ARXIV_URL_RE.search(url) or _ARXIV_DOI_RE.match(doi)
                 or _ARXIV_LINE_RE.search(el.findtext(f"{_Z}archiveID") or "")
                 or _ARXIV_LINE_RE.search(el.findtext(f"{_DC}description") or ""))

        title = re.sub(r"\s+", " ", el.findtext(f"{_DC}title") or "").strip()
        meta = {
            "title": title,
            "authors": authors,
            "year": year_match.group(0) if year_match else "",
            "venue": container.get("venue", ""),
            "volume": container.get("volume", "") or (el.findtext(f"{_PRISM}volume") or "").strip(),
            "pages": (el.findtext(f"{_BIB}pages") or "").strip(),
            "doi": doi,
            "arxiv_id": arxiv.group(1) if arxiv else "",
            "publisher": _publisher(el),
            "isbn": isbn,
            "source": "zotero",
        }
        kind = item_types.from_zotero(el.findtext(f"{_Z}itemType"))
        if kind:
            meta["kind"] = kind

        found, seen = [], set()
        keys = [about] if about in attachments else list(linked_attachments(el))
        for key in keys:
            att = attachments.get(key)
            where = att and (att["path"] or att["url"])
            if where and where not in seen:
                seen.add(where)
                found.append({"key": key, **att})

        notes, warnings = [], []
        for ref in el.findall(f"{_DCT}isReferencedBy"):
            key = ref.get(f"{_RDF}resource") or ""
            referenced.add(key)
            tree = _note(memos[key].findtext(f"{_RDF}value") or "", warnings) if key in memos else None
            if tree:
                notes.append({"key": key, "text": tree["content"], "children": tree["children"]})

        if not title and about in attachments:  # a standalone file without a title: its name
            own = attachments[about]
            title = posixpath.basename(own["path"].replace("\\", "/")) or own["url"]
        title = title or "Untitled"
        for warning in warnings:
            warning.setdefault("title", title)
        items.append({
            "key": about,
            "title": title,
            "meta": meta,
            "cite_key": _citation_key(el),
            "tags": _tags(el),
            "folders": item_folders.get(about, []),
            "attachments": found,
            "pdf_paths": [a["path"] for a in found if a["path"] and a["pdf"]],
            "notes": notes,
            "standalone_note": False,
            "rdf": None if about in attachments else _kept(_pass_through(el, part, container_el, meta), title, warnings),
            "warnings": warnings,
        })

    for about, memo in memos.items():
        warnings = []
        tree = None if about in referenced else _note(memo.findtext(f"{_RDF}value") or "", warnings)
        if not tree:
            continue
        title, note = _note_page(tree)
        for warning in warnings:
            warning.setdefault("title", title)
        items.append({
            "key": about,
            "title": title,
            "meta": {},
            "cite_key": "",
            "tags": _tags(memo),
            "folders": item_folders.get(about, []),
            "attachments": [],
            "pdf_paths": [],
            "notes": [],
            "standalone_note": True,
            "note": note,
            "rdf": None,
            "warnings": warnings,
        })
    return items


def _citation_key(el) -> str:
    """The citation key Better BibTeX pinned on the item, if any. The plugin
    writes it into Zotero's Extra field as a ``Citation Key: <key>`` line, and
    Zotero RDF carries Extra as ``dc:description`` — so a library whose .tex
    files already cite those keys goes on citing them here."""
    m = _CITATION_KEY_RE.search(el.findtext(f"{_DC}description") or "")
    return clean_key(m.group(1)) if m else ""


def zip_name_map(zf) -> dict:
    """Candidate spelling → real entry name, tolerant of what zippers actually
    produce: Explorer omits the UTF-8 flag so non-ASCII names arrive as
    cp437 mojibake, some tools write backslash separators, and macOS stores
    NFD-decomposed unicode."""
    out = {}
    for zi in zf.infolist():
        cands = {zi.filename}
        if not (zi.flag_bits & 0x800):  # no UTF-8 flag: zipfile decoded cp437
            try:
                cands.add(zi.filename.encode("cp437").decode("utf-8"))
            except (UnicodeEncodeError, UnicodeDecodeError):
                pass
        for cand in list(cands):
            cand = posixpath.normpath(cand.replace("\\", "/"))
            for form in ("NFC", "NFD"):
                out[unicodedata.normalize(form, cand)] = zi.filename
    return out


def find_zip_entry(name_map: dict, base: str, path: str) -> str | None:
    """Resolve a z:path (relative to the .rdf file) to a real zip entry name.
    The path attribute is usually raw, but try percent-decoding too."""
    seen = []
    for cand in (path, urllib.parse.unquote(path)):
        if cand in seen:
            continue
        seen.append(cand)
        full = posixpath.normpath((f"{base}/{cand}" if base else cand).replace("\\", "/"))
        for form in ("NFC", "NFD"):
            real = name_map.get(unicodedata.normalize(form, full))
            if real:
                return real
    return None


def _attachment_folders(base: str, path: str):
    """The files/<attachment-id> folder a z:path names, as a zip name (raw
    and percent-decoded spellings)."""
    for spelling in (path, urllib.parse.unquote(path)):
        parent = posixpath.dirname(posixpath.normpath(spelling.replace("\\", "/")))
        if re.search(r"(?:^|/)files/[^/]+$", parent):
            yield posixpath.normpath(f"{base}/{parent}" if base else parent)


def resolve_entry(name_map: dict, base: str, path: str, pdf: bool = True) -> tuple[str | None, bool]:
    """Exact path first; a renamed file may only match inside its attachment
    folder — a PDF only another PDF there.

    Never guess by basename across the archive: different items often use PDF.pdf.
    A unique file in files/<attachment-id> is safe even after filename truncation
    or a legacy ZIP encoding changed its spelling.
    """
    exact = find_zip_entry(name_map, base, path)
    if exact:
        return exact, False
    candidates = set()
    for target in _attachment_folders(base, path):
        for name, real in name_map.items():
            if posixpath.dirname(name) == target and not real.endswith("/") and (
                    not pdf or name.lower().endswith(".pdf")):
                candidates.add(real)
    return (next(iter(candidates)), True) if len(candidates) == 1 else (None, False)


def unwritten_file(name_map: dict, base: str, path: str) -> bool:
    """The export has its files/ tree but nothing in this attachment's
    folder. Zotero writes a files/<id>/ z:path only for a file it has and is
    copying — after its export reports done (the RDF translator doesn't
    await ``saveFile``; with "Include Annotations" every PDF queues for its
    PDF worker) — so an export zipped or picked too early lacks the PDFs
    still to come, their folders empty. A file Zotero doesn't have gets no
    z:path; a picked folder (transfers/folderZip.js) lists no empty folders."""
    for target in _attachment_folders(base, path):
        files_dir = posixpath.dirname(target) + "/"
        if (not any(n.startswith(target + "/") for n in name_map)
                and any(n.startswith(files_dir) for n in name_map)):
            return True
    return False


def _pdf_digest(zf, name: str) -> str:
    """The stored name's stem (``storage.digest_id``) of the PDF at zip
    entry ``name``, hashed as it is inflated, a chunk at a time, so a
    scanned book is never whole in memory. ValueError when it does not
    start as a PDF."""
    from .storage import SPOOL_CHUNK, digest_id, is_pdf

    digest = hashlib.sha256()
    with zf.open(name) as src:
        chunk = src.read(SPOOL_CHUNK)
        if not is_pdf(chunk):
            raise ValueError("file is not a PDF")
        while chunk:
            digest.update(chunk)
            chunk = src.read(SPOOL_CHUNK)
    return digest_id(digest)


def plan_zotero_archive(zf, digests=None) -> dict:
    """Read-only plan shared by preview and import; file bytes stay in the ZIP.

    Each item gets its document — the first of its PDFs that is in the zip
    and is one (``pdf_entry``, ``digest``) — and its ``extras``: its other
    attachments in the export's order, what its page holds under
    "Attachments". An extra is a file (``{key, entry, name, ext}``, its
    zip entry; a PDF also its ``digest``) or a web link (``{key, title,
    url}``). A PDF that repeats the document's bytes, or an earlier extra's,
    is left out; so is a file Gamma never stores (an executable, a drawing),
    which the entries list as not imported. The document's title, when
    Zotero's is not the "PDF" the export writes, joins the item's ``rdf``
    (``pdf_title``).

    Each PDF is hashed once, as it is inflated. ``digests`` maps a zip
    entry to ``[file_size, crc, digest]`` from an earlier plan of the same
    archive (a review's preview, kept for its commit): an entry that still
    has that size and CRC is not read again. The plan's ``digests`` is that
    map for every PDF it hashed or reused.

    Files no attachment names are warned of one by one, except Zotero's own
    (``.zotero-ft-cache``, its full-text index of a file) and those beside
    an attachment in its files/<id> folder (a web snapshot's page
    resources): one warning per folder."""
    from .storage import display_filename, upload_extension

    names = zip_name_map(zf)
    rdf_names = [n for n in names if n.lower().endswith(".rdf") and not n.startswith("__MACOSX/")]
    if not rdf_names:
        raise ValueError('no .rdf file in the zip — export from Zotero as "Zotero RDF" with "Export Files"')
    rdf_name = min(rdf_names, key=lambda n: (n.count("/"), len(n), n))
    base = posixpath.dirname(rdf_name)
    items = parse_zotero_rdf(zf.read(names[rdf_name]).decode("utf-8-sig"))
    if not items:
        raise ValueError("no importable items in the export")
    known, hashed = digests or {}, {}
    warnings, planned, used = [], [], {names[rdf_name]}
    named = set()   # every entry an attachment names, imported or not
    folders = {}    # an attachment's files/<id> folder → (its item's title, the attachment's file name)
    unwritten = []  # per file Zotero had not written yet: whether it is a PDF
    for item in items:
        problems, found = item["warnings"], []
        document = document_title = None
        for att in item["attachments"]:
            path = att["path"]
            if not path:
                found.append({"key": att["key"], "title": att["title"], "url": att["url"]})
                continue
            what = "PDF" if att["pdf"] else "File"
            real, recovered = resolve_entry(names, base, path, att["pdf"])
            if not real:
                reason = f"{what} missing from ZIP: {path}"
                if unwritten_file(names, base, path):
                    unwritten.append(att["pdf"])
                    reason += " (Zotero had not written it yet)"
                problems.append({"title": item["title"], "path": path, "reason": reason})
                continue
            named.add(real)
            name = display_filename(posixpath.basename(real.replace("\\", "/")), "file")
            for folder in _attachment_folders(base, path):
                folders.setdefault(unicodedata.normalize("NFC", folder), (item["title"], name))
            if att["pdf"]:
                info = zf.getinfo(real)
                cached = hashed.get(real) or known.get(real)
                if (isinstance(cached, (list, tuple)) and len(cached) == 3
                        and list(cached[:2]) == [info.file_size, info.CRC]):
                    digest = cached[2]
                else:
                    try:
                        digest = _pdf_digest(zf, real)
                    except Exception as exc:
                        problems.append({"title": item["title"], "path": real, "reason": f"Cannot import PDF: {exc}"})
                        continue
                hashed[real] = [info.file_size, info.CRC, digest]
                entry = {"key": att["key"], "entry": real, "name": name, "ext": ".pdf", "digest": digest}
                if document is None:  # a standalone attachment's title is its item's own
                    document, document_title = entry, att["title"] if att["key"] != item["key"] else ""
                found.append(entry)
            else:
                try:
                    ext = upload_extension(name)
                except ValueError:  # an executable: listed as not imported
                    continue
                if ext == ".ink":  # a Gamma drawing comes in by its own route only
                    continue
                found.append({"key": att["key"], "entry": real, "name": name, "ext": ext})
            used.add(real)
            if recovered:
                problems.append({"title": item["title"], "path": real,
                                 "reason": f"Filename differs from the export; matched the only {what.lower()} in its attachment folder: {real}"})
        if not document and not item["standalone_note"]:
            problems.append({"title": item["title"], "reason": "No PDF available in this ZIP. New pages carry no PDF; existing PDFs are kept."})
        if document_title and document_title != "PDF":
            meta = item["meta"]
            rdf = item["rdf"] or {"v": 1, "work": {"title": meta["title"], "doi": meta["doi"]}}
            item["rdf"] = _kept({**rdf, "pdf_title": document_title}, item["title"], problems)
        document_digests = {document["digest"]} if document else set()
        extras = []
        for extra in found:
            if extra is document or extra.get("digest") in document_digests:
                continue
            if extra.get("digest"):
                document_digests.add(extra["digest"])
            extras.append(extra)
        item["pdf_entry"] = document["entry"] if document else None
        item["digest"] = document["digest"] if document else None
        item["extras"] = extras
        planned.append(item)
        warnings.extend(problems)
    if unwritten:
        noun = "PDF" if all(unwritten) else "file"
        n = len(unwritten)
        warnings.insert(0, {"title": "Zotero export not finished", "reason": (
            f"{n} {noun}{'s were' if n > 1 else ' was'} not in the export folder yet. "
            "Zotero keeps writing files after its export window closes, PDFs one at a time with "
            "\"Include Annotations\". Wait until the folder stops growing, then import it "
            "again: the pages imported now get their files.")})
    beside = {}  # an entry under an attachment's folder → (the folder, its item's title, the attachment's name)
    for cand, real in names.items() if folders else ():
        parts = cand.split("/")
        for i in range(len(parts) - 1, 0, -1):
            folder = "/".join(parts[:i])
            if folder in folders:
                beside.setdefault(real, (folder, *folders[folder]))
                break
    entries, folded = [], {}
    for zi in zf.infolist():
        path = zi.filename.replace("\\", "/")
        directory = path.endswith("/")
        status = "folder" if directory else "imported" if zi.filename in used else "not_imported"
        entries.append({"path": path, "size": zi.file_size, "directory": directory, "status": status})
        name = posixpath.basename(path)
        # Zotero's own files beside an attachment's (.zotero-ft-cache and
        # .zotero-ft-info, its full-text index) are no one's business.
        if (status != "not_imported" or path.startswith("__MACOSX/") or name == ".DS_Store"
                or name.startswith(".zotero-")):
            continue
        if zi.filename in beside and zi.filename not in named:
            folder, title, attachment = beside[zi.filename]
            if folder not in folded:
                folded[folder] = {"title": title, "path": folder, "files": 0, "beside": attachment}
                warnings.append(folded[folder])
            folded[folder]["files"] += 1
        else:
            warnings.append({"title": name, "path": path,
                             "reason": "File is not imported: unsupported attachment or not linked to an item in the selected export."})
    for warning in folded.values():
        n, attachment = warning.pop("files"), warning.pop("beside")
        warning["reason"] = (f"{n} file{'s' if n > 1 else ''} beside {attachment} {'are' if n > 1 else 'is'} "
                             "not imported (a web snapshot's page resources)")
    for index, item in enumerate(planned):
        item["selection_id"] = f"zotero:{index}"
        for warning in item["warnings"]:
            warning["selection_id"] = item["selection_id"]
    return {"items": planned, "entries": entries, "warnings": warnings, "manifest": rdf_name, "digests": hashed}

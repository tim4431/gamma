"""Render pages as a Zotero RDF library export — the inverse of
``zotero_import``: one ``.rdf`` plus a ``files/<n>/<name>.pdf`` tree, zipped.
Zotero's File → Import reads the ``.rdf`` directly (after unzipping), and
Gamma's own ``/api/import/zotero`` accepts the zip as-is, so a library
round-trips.

The element shapes mirror what Zotero itself writes (its "Zotero RDF"
translator's ``generateItem``, and what ``zotero_import.parse_zotero_rdf``
reads): items are bib classes carrying ``z:itemType``, the page's item type
(``item_types``) under its own name. A journal article's or a conference
paper's venue, volume and DOI live on a standalone ``bib:Journal`` record
referenced via ``dcterms:isPartOf``, a chapter's book title and ISBN on a
``bib:Book``; a publisher (a thesis's university, a report's institution) is
a ``dc:publisher`` ``foaf:Organization``. Authors are ``foaf:Person``s split
into Zotero's two name fields (``split_name``). Attachments are
``z:Attachment`` elements linked by ``link:link`` with the file path in
``z:path``, or a web link's URL (exactly what Zotero's own export writes; an
``rdf:resource`` *element* is an RDF/XML syntax term — Zotero's parser happens to tolerate one,
verified live against Zotero 9, but strict parsers like rdflib reject the
whole file, so it is never emitted), notes are ``bib:Memo`` HTML linked by
``dcterms:isReferencedBy`` (``zotero_notes.note_html``, the exact inverse of
the import's note trees), and folders become a ``z:Collection`` tree. A note
page, a Zotero standalone note the import made a page of, goes out as that
standalone ``bib:Memo`` again.

What Zotero held about a work that ``meta`` does not model rides in the
page's ``zotero_rdf``, the pass-through the import kept: the original item
type and RDF classes (a ``webpage`` is a ``document`` here and goes out a
webpage again), the full date, the original ``bib:authors``, the Extra text,
the PDF attachment's title, and every element the import did not map
(abstract, issue, ISSN, editors, URL, …), written back verbatim. Each part
is used only while the page's record still agrees with it, and none of it
once the record names another work (``zotero_import.same_work``). Anyone who
edits the workspace can write a page's properties, so only what the import
keeps there goes back (``_passed``): fields, never an item, a note or a
file path of the machine that imports the export.

The whole pipeline is verified against a real Zotero via its connector
server's ``/connector/import`` (the same translator code path as the import
wizard). Note Zotero CANNOT read the .zip itself — its wizard reports
"unsupported format" for one; the .rdf must be extracted and picked, which is
why the zip carries a README saying so.

Annotations are deliberately absent here — like Zotero's own "Include
Annotations", they travel embedded inside the exported PDF copies (the
endpoint burns them in with ``pdf_export.annotate_pdf``): highlights, area
notes, handwriting and text boxes, which Zotero reads as its highlight,
image, ink and text annotations. A text box with no PDF copy to carry it is
a note: one placed on a PDF page opens with its page, as a highlight's memo
does. So is a picture, its image embedded in the note with its caption
under it: the copy draws it, but Zotero keeps no annotation of a stamp.

Keys: an item's ``rdf:about`` is the page's Zotero key or ``GAMMA_PAGE_KEY``
+ the page id; a note's is ``GAMMA_NOTE_KEY`` + the block id. Both stay the
same from one export to the next, which is what the import upserts by.
"""

import html as html_mod
import re
import xml.etree.ElementTree as ET

from . import item_types, zotero_import, zotero_notes
from .highlights import page_of
from .picture import is_picture, markdown_of, picture_page
from .text_box import box_page

_NS = {
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "z": "http://www.zotero.org/namespaces/export#",
    "dcterms": "http://purl.org/dc/terms/",
    "bib": "http://purl.org/net/biblio#",
    "foaf": "http://xmlns.com/foaf/0.1/",
    "link": "http://purl.org/rss/1.0/modules/link/",
    "dc": "http://purl.org/dc/elements/1.1/",
    "prism": "http://prismstandard.org/namespaces/1.2/basic/",
    "vcard": "http://nwalsh.com/rdf/vCard#",  # a publisher's place, passed through
}
for _p, _u in _NS.items():
    ET.register_namespace(_p, _u)

# The keys of what Gamma exports: real Zotero names items "#item_<n>",
# "urn:isbn:…" or by URL, so these prefixes never meet one.
GAMMA_PAGE_KEY = "#gamma_page_"
GAMMA_NOTE_KEY = "#gamma_note_"


def _q(prefix: str, tag: str) -> str:
    return f"{{{_NS[prefix]}}}{tag}"


def _sub(parent, prefix, tag, text=None, **attrs):
    el = ET.SubElement(parent, _q(prefix, tag))
    if text is not None:
        el.text = text
    for key, value in attrs.items():
        pfx, name = key.split("_", 1)
        el.set(_q(pfx, name), value)
    return el


# --- notes: block subtrees → Zotero note HTML -------------------------------

# Markdown image refs to Gamma's content-addressed uploads (the image shape of
# markdown_export.UPLOAD_RE).
MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\(/api/uploads/([0-9a-fA-F]+\.[A-Za-z0-9]+)\)")

IMAGE_MIME = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    "bmp": "image/bmp",
}


def strip_image_md(text: str) -> str:
    """Markdown image refs → a readable placeholder. Annotation comments are
    plain text (they come from the PDF's /Contents), so the image itself
    travels embedded in a Zotero note (``highlight_memo_html``) and as an item
    attachment instead."""
    return MD_IMAGE_RE.sub(lambda m: f"(image: {m.group(1)} — see item notes)", text or "")


def _shown(node: dict) -> dict:
    """A block subtree as its note shows it: a picture block's text is its
    image line (``picture.markdown_of``), then its caption as text, since
    the <img> holds the caption only as its alt; every other block's is its
    content."""
    content = node.get("content") or ""
    props = node.get("properties") or {}
    if is_picture(props) and (line := markdown_of(content, props["picture"])):
        content = "\n".join(part for part in (line, content.strip()) if part)
    return {**node, "content": content, "children": [_shown(child) for child in node.get("children") or []]}


def note_html(node, resolve_image=None, resolve_ref=None) -> str:
    """One block subtree (a ``build_tree`` node) → one Zotero note's HTML
    (``zotero_notes.note_html``: the block as its element, its children as
    lists), after its page for a text box or a picture on a PDF page.
    ``resolve_image(filename) -> (mime, base64) | None`` embeds pasted images
    into the note, and a picture block's own; ``resolve_ref(id) -> {content,
    …} | None`` reads a [[ref]] as the text of the block it names."""
    node = _shown(node)
    body = zotero_notes.note_html(node, resolve_image, resolve_ref)
    props = node.get("properties")
    if body and node["content"].strip():
        if page_no := box_page(props):
            return f"<p><strong>Text box on p.{page_no}</strong></p>{body}"
        if page_no := picture_page(props):
            return f"<p><strong>Picture on p.{page_no}</strong></p>{body}"
    return body


def highlight_memo_html(node, resolve_image=None, resolve_ref=None) -> str:
    """A Zotero note for a highlight whose writing carries images. The
    annotation comment can only hold a plain placeholder, so the pictures live
    here — prefixed with the page and quote, so the note stays findable next
    to its annotation."""
    props = node.get("properties") or {}
    page_no = page_of(props)
    quote = re.sub(r"\s+", " ", props.get("quote") or "").strip()
    header = " — ".join(part for part in (
        f"p.{page_no}" if page_no else "",
        f"“{quote[:120]}”" if quote else "",
    ) if part)
    body = note_html(node, resolve_image, resolve_ref)
    if not body:
        return ""
    head = f"<p><strong>{html_mod.escape(header, quote=False)}</strong></p>" if header else ""
    return head + body


def standalone_note_html(title: str, children, resolve_image=None, resolve_ref=None) -> str:
    """A note page (a Zotero standalone note the import made a page of) as
    that note again: the title as its heading, which Zotero shows as the
    note's title, then the page's blocks as the note's body. The import
    reads the heading's text back as the title and the blocks as the
    children they were."""
    return f"<h1>{html_mod.escape(title, quote=False)}</h1>" + zotero_notes.note_html(
        _shown({"content": "", "children": children}), resolve_image, resolve_ref)


# --- names ------------------------------------------------------------------

# Lowercase particles that start a surname: "Jan van der Berg" is surname
# "van der Berg", given name "Jan".
_PARTICLES = frozenset("van von der de la du da di del della den ter ten le zu bin al".split())
# A generational suffix stays with the surname, its comma too.
_SUFFIX_RE = re.compile(r"(,?\s+)(Jr\.?|Sr\.?|II|III|IV)$")
# A word that makes a name an organisation's.
_ORG_RE = re.compile(
    r"\b(?:Collaboration|Consortium|Group|Team|Committee|Organi[sz]ation|Institute|University|Association|"
    r"Society|Council|Agency|Foundation|Network|Project|Initiative|Cent(?:er|re)|Laboratory|Inc|Ltd)\b", re.I)


def split_name(name: str) -> tuple[str, str]:
    """An author as Zotero's two name fields, ``(surname, given name)``: a
    ``Last, First`` name at its comma, else the surname from the first
    lowercase particle on ("van der Berg") or the last word, a trailing
    Jr., Sr., II, III or IV kept with it. An organisation ("ATLAS
    Collaboration") or a single word is a surname alone, which Zotero reads
    as a single-field name. Gamma keeps an author as one string, so this is
    where it becomes Zotero's two."""
    name = " ".join(str(name).split())
    if " " not in name or _ORG_RE.search(name):
        return name, ""
    suffix = _SUFFIX_RE.search(name)
    if suffix:
        name = name[:suffix.start()]
    if "," in name:
        last, _, given = name.partition(",")
        last, given = last.strip(), given.strip()
    else:
        words = name.split(" ")
        at = next((i for i, w in enumerate(words[:-1]) if w in _PARTICLES), len(words) - 1)
        last, given = " ".join(words[at:]), " ".join(words[:at])
    if suffix:
        last += (", " if "," in suffix.group(1) else " ") + suffix.group(2)
    return last, given


def _person(seq, name: str):
    surname, given = split_name(name)
    person = ET.SubElement(ET.SubElement(seq, _q("rdf", "li")), _q("foaf", "Person"))
    _sub(person, "foaf", "surname", surname)
    if given:
        _sub(person, "foaf", "givenName", given)


# --- the RDF document -------------------------------------------------------

# The RDF class Zotero's translator gives each item type (the item itself);
# a type it gives none is a plain rdf:Description — z:itemType is what any
# reader goes by. Zotero files a presentation as bib:ConferenceProceedings.
_ITEM_CLASS = {
    "journalArticle": ("bib", "Article"), "bookSection": ("bib", "BookSection"), "book": ("bib", "Book"),
    "thesis": ("bib", "Thesis"), "report": ("bib", "Report"), "presentation": ("bib", "ConferenceProceedings"),
}
# The record a venue lives on, by item type: a journal (Zotero's choice for
# conference proceedings too) or the book a chapter is in. The item's DOI
# and ISBN go with it, as Zotero writes them.
_CONTAINER_CLASS = {"journalArticle": "Journal", "conferencePaper": "Journal", "bookSection": "Book"}

# An element name in Clark notation, as the pass-through keeps a class.
_CLARK_RE = re.compile(r"\{[^{}\s]+\}[A-Za-z_][\w.-]*")
_YEAR_RE = re.compile(r"\d{4}")
# Better BibTeX's key line in Extra (what zotero_import._citation_key reads).
_KEY_LINE_RE = re.compile(r"\s*citation key\s*:", re.I)


def _clark(value) -> str | None:
    return value if isinstance(value, str) and _CLARK_RE.fullmatch(value) else None


def _shape(kind: str, rdf: dict) -> tuple[str, str, str | None]:
    """An item's ``z:itemType``, its RDF class and its venue record's class
    (Clark names; None: no record): the original ones the pass-through
    kept while they still name the page's kind, else the export's own."""
    item_class = _q(*_ITEM_CLASS[kind]) if kind in _ITEM_CLASS else _q("rdf", "Description")
    container_class = _q("bib", _CONTAINER_CLASS[kind]) if kind in _CONTAINER_CLASS else None
    original = rdf.get("type") if isinstance(rdf.get("type"), str) else ""
    if item_types.from_zotero(original or kind) != kind:
        return kind, item_class, container_class
    return (original or kind, _clark(rdf.get("class")) or item_class,
            _clark(rdf.get("container_class")) or container_class)


# What a field the import passes through never holds: an item of its own
# (Zotero reads an inline node as one, and an attachment's path as a file of
# the importing machine to take in), a link to one, or a note.
_NOT_A_FIELD = {_q("z", "itemType"), _q("z", "Attachment"), _q("z", "path"), _q("rdf", "resource"),
                _q("link", "link"), _q("bib", "Memo"), _q("dcterms", "isReferencedBy")}


def _passed(xmls, mapped=frozenset()) -> list:
    """The pass-through's serialized elements, parsed back: the fields the
    import kept, and nothing else a page's properties may hold instead.
    Left out: one that does not parse; one with a DTD, whose entities would
    expand (``ET.tostring``, which wrote them, writes no ``<!``); an element
    the import maps itself (``mapped``) or one holding what no field holds
    (``_NOT_A_FIELD``); and every one past PASS_THROUGH_BYTES in all."""
    out, size = [], 0
    for text in xmls if isinstance(xmls, list) else []:
        if not isinstance(text, str) or "<!" in text:
            continue
        size += len(text)
        if size > zotero_import.PASS_THROUGH_BYTES:
            break
        try:
            el = ET.fromstring(text)
        except ET.ParseError:
            continue
        if el.tag not in mapped and not any(d.tag in _NOT_A_FIELD for d in el.iter()):
            out.append(el)
    return out


def _extra(original, cite_key: str) -> str:
    """Zotero's Extra text (``dc:description``): the original's lines with
    its Better BibTeX ``Citation Key:`` line made the page's pinned key —
    replaced where it was, added first, or removed."""
    line = f"Citation Key: {cite_key}" if cite_key else ""
    lines = []
    for text in str(original or "").splitlines():
        if not _KEY_LINE_RE.match(text):
            lines.append(text)
        elif line:
            lines.append(line)
            line = ""
    if line:
        lines.insert(0, line)
    return "\n".join(lines).strip()


def _is_url(el) -> bool:
    return el.tag == _q("dc", "identifier") and el.find(f"{_q('dcterms', 'URI')}/{_q('rdf', 'value')}") is not None


def _item(root, n: int, item: dict) -> None:
    """One page's bibliographic item, with its venue record, attachments and
    notes, under ``root``."""
    meta = item.get("meta") or {}
    rdf = item.get("rdf") if isinstance(item.get("rdf"), dict) else {}
    if rdf and not zotero_import.same_work(rdf.get("work") or {}, meta):
        rdf = {}  # it describes the work the page held before
    arxiv = (meta.get("arxiv_id") or "").strip()
    venue = (meta.get("venue") or "").strip()
    # A page with no record is no known work: a plain document.
    kind = item_types.item_type(meta) if meta else "document"
    zotero_type, item_class, container_class = _shape(kind, rdf)

    el = ET.SubElement(root, item_class, {_q("rdf", "about"): item["key"]})
    _sub(el, "z", "itemType", zotero_type)
    # The volume and identifiers sit on the container when there is one, as
    # Zotero's do.
    holder = el
    if container_class and venue:
        container_key = f"#{container_class.rsplit('}', 1)[1].lower()}_{n}"
        _sub(el, "dcterms", "isPartOf", rdf_resource=container_key)
        holder = ET.SubElement(root, container_class, {_q("rdf", "about"): container_key})
        _sub(holder, "dc", "title", venue)
    if meta.get("volume"):
        _sub(holder, "prism", "volume", str(meta["volume"]))
    for label, field in (("DOI", "doi"), ("ISBN", "isbn")):
        value = str(meta.get(field) or "").strip()
        if value:
            _sub(holder, "dc", "identifier", f"{label} {value}")
    if holder is not el:
        holder.extend(_passed(rdf.get("container"), zotero_import.CONTAINER_MAPPED))
    # arXiv is a preprint's repository, as Zotero's own arXiv records name it.
    publisher = item_types.publisher(meta) or ("arXiv" if kind == "preprint" and arxiv else "")
    # Zotero's own publisher element, its place with it, while it names the
    # record's publisher.
    kept = rdf.get("publisher") if isinstance(rdf.get("publisher"), dict) else {}
    original = _passed([kept.get("xml")]) if publisher and kept.get("name") == publisher else []
    if original and original[0].tag == _q("dc", "publisher"):
        el.extend(original)
    elif publisher:
        org = _sub(_sub(el, "dc", "publisher"), "foaf", "Organization")
        _sub(org, "foaf", "name", publisher)

    authors = meta.get("authors")
    stored = rdf.get("authors") if isinstance(rdf.get("authors"), dict) else {}
    original = _passed([stored.get("xml")]) if authors and stored.get("names") == authors else []
    if original and original[0].tag == _q("bib", "authors"):
        el.extend(original)
    elif isinstance(authors, list) and authors:
        seq = ET.SubElement(ET.SubElement(el, _q("bib", "authors")), _q("rdf", "Seq"))
        for name in authors:
            if str(name).strip():
                _person(seq, str(name))

    _sub(el, "dc", "title", item["title"])
    # Zotero's Extra field travels as dc:description; Better BibTeX pins its
    # key there as a "Citation Key:" line, which is what the import reads
    # back (zotero_import._citation_key) — so a library that goes out and
    # comes back keeps citing the same keys.
    extra = _extra(rdf.get("extra"), item.get("cite_key") or "")
    if extra:
        _sub(el, "dc", "description", extra)
    # The original date while it names the record's year (or, like it, none).
    year = str(meta.get("year") or "").strip()
    date = rdf.get("date") if isinstance(rdf.get("date"), str) else ""
    found = _YEAR_RE.search(date)
    if date and (found.group() if found else "") == year:
        _sub(el, "dc", "date", date)
    elif year:
        _sub(el, "dc", "date", year)
    if meta.get("pages"):
        _sub(el, "bib", "pages", str(meta["pages"]))

    passed = _passed(rdf.get("item"), zotero_import.MAPPED)
    if kind == "preprint" and arxiv and not any(p.tag == _q("z", "archiveID") for p in passed):
        _sub(el, "z", "archiveID", f"arXiv:{arxiv}")
    # One URL: the original's, else the arXiv abstract's, else the web page
    # the page was clipped from.
    if not any(_is_url(p) for p in passed):
        url = f"https://arxiv.org/abs/{arxiv}" if arxiv else str(item.get("url") or "").strip()
        if url:
            _sub(_sub(_sub(el, "dc", "identifier"), "dcterms", "URI"), "rdf", "value", url)
    for t in item.get("tags") or []:
        _sub(el, "dc", "subject", t)
    el.extend(passed)

    if item.get("pdf_path"):
        attach_key = f"#attach_{n}"
        _sub(el, "link", "link", rdf_resource=attach_key)
        att = _sub(root, "z", "Attachment", rdf_about=attach_key)
        _sub(att, "z", "itemType", "attachment")
        _sub(att, "z", "path", rdf_resource=item["pdf_path"])
        _sub(att, "dc", "title", str(rdf.get("pdf_title") or "PDF"))
        _sub(att, "link", "type", "application/pdf")

    # Pasted note images ride as further attachments on the item — the
    # counterpart of the "(image: …)" placeholders in annotation comments —
    # and so do the item's other files and its web links, which Zotero
    # makes a linked URL of (an attachment with a URL and no file).
    for k, img in enumerate(item.get("images") or [], 1):
        img_key = f"#img_{n}_{k}"
        _sub(el, "link", "link", rdf_resource=img_key)
        att = _sub(root, "z", "Attachment", rdf_about=img_key)
        _sub(att, "z", "itemType", "attachment")
        _sub(att, "dc", "title", img.get("title") or "Image")
        if img.get("path"):
            _sub(att, "z", "path", rdf_resource=img["path"])
            _sub(att, "link", "type", img.get("mime") or "application/octet-stream")
        else:
            _sub(_sub(_sub(att, "dc", "identifier"), "dcterms", "URI"), "rdf", "value", img["url"])

    for note in item.get("notes") or []:
        _sub(el, "dcterms", "isReferencedBy", rdf_resource=note["key"])
        memo = _sub(root, "bib", "Memo", rdf_about=note["key"])
        _sub(memo, "rdf", "value", note["html"])


def build_rdf(items: list[dict]) -> str:
    """items: one dict per page —
    ``{key, title, meta, cite_key, tags, folders, pdf_path, images, notes,
    rdf, url}`` where ``folders`` are the collection paths it is in (each a
    list of names from the top), ``cite_key`` the citation key pinned on
    the page ("" for none), ``pdf_path`` the zip path relative to the .rdf
    (or None), ``images`` its other attachments (``{path, title, mime}``,
    a web link ``{url, title}``),
    ``notes`` a list of ``{key, html}``, ``rdf`` the page's ``zotero_rdf``
    (None for none) and ``url`` the web page it was clipped from ("" for
    none). A note page is ``{key, title, tags, folders, memo}``, ``memo``
    its note's HTML, and goes out as a standalone note. → the .rdf
    document text."""
    root = ET.Element(_q("rdf", "RDF"))

    folder_paths = set()
    for n, item in enumerate(items, 1):
        if item.get("memo"):
            # A standalone note, as Zotero's translator writes one.
            memo = _sub(root, "bib", "Memo", rdf_about=item["key"])
            _sub(memo, "z", "itemType", "note")
            _sub(memo, "rdf", "value", item["memo"])
            for t in item.get("tags") or []:
                _sub(memo, "dc", "subject", t)
        else:
            _item(root, n, item)
        for path in item.get("folders") or []:
            for i in range(len(path)):
                folder_paths.add(tuple(path[: i + 1]))

    # Folders → the collection tree (hasPart links members AND children).
    ids = {path: f"#collection_{i}" for i, path in enumerate(sorted(folder_paths), 1)}
    for path in sorted(folder_paths):
        col = _sub(root, "z", "Collection", rdf_about=ids[path])
        _sub(col, "dc", "title", path[-1])
        for other in sorted(folder_paths):
            if other[:-1] == path:
                _sub(col, "dcterms", "hasPart", rdf_resource=ids[other])
        for item in items:
            if path in {tuple(p) for p in item.get("folders") or []}:
                _sub(col, "dcterms", "hasPart", rdf_resource=item["key"])

    ET.indent(root)
    return '<?xml version="1.0" encoding="UTF-8"?>\n' + ET.tostring(root, encoding="unicode")

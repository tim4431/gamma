"""A block's document, stored as one property (docs/dev/documents.md):

    doc = {format, name, hash, ext, at, url?, size?, copy?}

in that key order. ``format`` is one of formats.FORMATS, stored and never
re-derived; ``name`` the file's shown name (a display leaf, may be "");
``hash`` the stem its bytes are, or would be, stored under in the
workspace's uploads (a content hash, the hash of the address a PDF was
opened from, or the name a stripped PDF kept); ``ext`` the stored file's
extension, so its stored name is ``hash + ext`` (``stored_name``); ``at``
where the bytes are read from: ``store`` (the uploads), ``path`` (a file on
this computer, gamma/linked_files.py) or ``url`` (an address, which only
this one carries); ``size`` its bytes when a writer knows them; ``copy``
true on a page that shows a stored file it does not own (a duplicated
page): no lookup of "the page of this file" finds it, and the generated
``doc_id`` column (db.BLOCK_HOT_COLUMNS) leaves it out.

``document`` is the one builder of a stored doc and ``check_document`` the
check of one a writer supplies; ``page_document`` is the one read of what a
block carries, a view the predicates below (``own_hash``, ``file_hash``,
``in_place`` …) answer questions of; whether a page is a library document
is gamma/work_folders/library.py's, over its properties. Twin of
frontend/src/documents/pageDocument.js; tests/shared/document.json holds
the two to the same answers.
"""

import hashlib
import re

from ..db import safe_doc_id
from . import formats

# The page properties a document was kept in before it was one property
# (normalize.document_shape rewrites them): no writer stores them.
LEGACY_KEYS = ("doc_id", "source_url", "linked_file")
AT = ("store", "path", "url")
FIELDS = ("format", "name", "hash", "ext", "at", "url", "size", "copy")
FORMAT_EXT = {"pdf": ".pdf"}  # the extension a format implies; any other format names its own

# Conditions on a unified_blocks row, spliced into WHERE clauses: its
# document is a PDF; it is read in place. Which page is a library document
# is the library rule's (gamma/work_folders/library.py SQL_LIBRARY_PDF and
# is_library_pdf, a work folder's files included).
SQL_PDF = "json_extract(properties, '$.doc.format') = 'pdf'"
SQL_IN_PLACE = "json_extract(properties, '$.doc.at') = 'path'"

_UPLOAD_URL_RE = re.compile(r"^/api/uploads/([0-9A-Za-z_-]{1,128})(\.[a-z0-9]{1,12})\Z")


def stored_name(hash: str, ext: str) -> str:
    """The name the file ``hash`` with extension ``ext`` is stored under in
    a workspace's uploads: the one place it is built."""
    return f"{hash}{ext}"


def upload_url(name: str) -> str:
    """Where the stored file ``name`` is served."""
    return f"/api/uploads/{name}"


def parse_upload_url(url: str) -> tuple[str, str] | None:
    """``(stem, ext)`` of an address that is exactly a stored file's
    (``upload_url``), else None: another host's, one with a query, a stem
    longer than 128 characters are other addresses."""
    m = _UPLOAD_URL_RE.match(url or "") if isinstance(url, str) else None
    return (m.group(1), m.group(2)) if m else None


def url_hash(url: str) -> str:
    """The stem a PDF opened from the address ``url`` is cached under (the
    PDF proxy, a clip). Twin: getDocIdForUrl in frontend/src/shared/lib/utils.js."""
    from ..storage import digest_id  # local: storage imports this module

    return digest_id(hashlib.sha256(url.encode()))


def document(hash: str = "", url: str = "", name: str = "", *, at: str | None = None,
             format: str = "pdf", ext: str | None = None, size: int | None = None,
             copy: bool = False) -> dict:
    """The stored doc of a file, in canonical form; ValueError when it
    cannot be one. ``ext`` defaults to the format's own (``FORMAT_EXT``: a
    PDF's is ``.pdf``, any other format must name it). No ``hash`` with
    ``url`` the address of a stored file of that extension is a copy of it,
    stored; ``url`` that is the stored address of ``hash`` itself is no
    address. ``at`` defaults to ``url`` when an address is left, else
    ``store``. A file stored or read in place has a hash and no address; one
    at an address has one; only a stored one is a copy. ``name`` is reduced
    to its display leaf (``storage.display_filename``)."""
    from ..storage import display_filename  # local: storage imports this module

    if format not in formats.FORMATS:
        raise ValueError(f"unknown document format: {format!r}")
    if not ext:
        if format not in FORMAT_EXT:
            raise ValueError(f"a {format} document names its extension")
        ext = FORMAT_EXT[format]
    if not isinstance(ext, str) or not formats.EXTENSION_RE.match(ext):
        raise ValueError(f"invalid document extension: {ext!r}")
    if format in FORMAT_EXT and ext != FORMAT_EXT[format]:
        raise ValueError(f"a {format} document is stored as {FORMAT_EXT[format]}")
    for value in (hash, url, name):
        if value is not None and not isinstance(value, str):
            raise ValueError("a document's hash, url and name are strings")
    hash, url = (hash or "").strip(), (url or "").strip()
    if hash:
        safe_doc_id(hash)
    if not hash and url:
        parsed = parse_upload_url(url)
        if parsed and parsed[1] == ext:
            if at == "path":
                raise ValueError("a file read in place has no address")
            hash, url, copy, at = parsed[0], "", True, "store"
    if hash and url == upload_url(stored_name(hash, ext)):
        url = ""
        if at == "url":
            at = "store"
    if at is None:
        at = "url" if url else "store"
    if at not in AT:
        raise ValueError(f"unknown document place: {at!r}")
    if at == "url":
        if not url:
            raise ValueError("a document at an address needs one")
    elif not hash or url:
        raise ValueError(f"a document {'stored' if at == 'store' else 'read in place'} has a hash and no address")
    if not isinstance(copy, bool):
        raise ValueError("copy is true or false")
    if copy and at != "store":
        raise ValueError("only a stored document is a copy")
    if isinstance(size, float) and size.is_integer():
        size = int(size)  # JSON's 1.0, which a client's JSON.parse reads as 1
    if size is not None and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        raise ValueError("a document's size is a whole number of bytes")
    doc = {"format": format, "name": display_filename(name or ""), "hash": hash, "ext": ext, "at": at}
    if url:
        doc["url"] = url
    if size is not None:
        doc["size"] = size
    if copy:
        doc["copy"] = True
    return doc


def check_document(value) -> dict:
    """A doc a writer supplies (the op path, the routes, page_root_props)
    checked and rebuilt in canonical form through ``document``: an object
    of the eight fields or some of them, of the right types. ValueError
    with the reason."""
    if not isinstance(value, dict):
        raise ValueError("a block's doc is an object")
    unknown = sorted(set(value) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown doc field: {unknown[0]}")
    for key in ("format", "name", "hash", "ext", "at", "url"):
        if key in value and not isinstance(value[key], str):
            raise ValueError(f"a doc's {key} is a string")
    if "copy" in value and not isinstance(value["copy"], bool):
        raise ValueError("a doc's copy is true or false")
    return document(value.get("hash", ""), value.get("url", ""), value.get("name", ""),
                    at=value.get("at"), format=value.get("format", "pdf"), ext=value.get("ext"),
                    size=value.get("size"), copy=value.get("copy", False))


def document_props(props, *, patch: bool = False, refuse: bool = True):
    """Block properties a writer supplies (``patch``: a patch of them) held
    to the one shape of a document, as a new dict: a flat key a document
    was kept in before (``LEGACY_KEYS``) dropped when null (a mirror's base
    or an older client clearing what is not there) and refused with a
    value; ``doc`` rebuilt in canonical form (``check_document``) or
    refused; a null ``doc`` dropped, but kept in a ``patch``, where it
    removes the key. ValueError with the reason; with ``refuse`` False
    what it would refuse is dropped instead. The one rule of every writer
    of properties: the op path, PUT children and the page writers
    (``blocks_store.page_root_props``). What is no dict, or names no
    document key, comes back as it is."""
    if not isinstance(props, dict) or not any(key in props for key in ("doc", *LEGACY_KEYS)):
        return props
    out = dict(props)
    for key in LEGACY_KEYS:
        if key in out:
            if out[key] is not None and refuse:
                raise ValueError(f"{key} is no longer a block property: a block's document is its doc property")
            del out[key]
    if out.get("doc") is not None:
        try:
            out["doc"] = check_document(out["doc"])
        except ValueError:
            if refuse:
                raise
            del out["doc"]
    elif "doc" in out and not patch:
        del out["doc"]
    return out


def page_document(props: dict | None) -> dict | None:
    """What a block carries: the view of its ``doc``, or None when it has
    none or one that does not check. Never raises. The view is
    ``{format, language, name, hash, ext, size, at, url, file, copy}``:
    ``language`` a text document's (formats.language_of), ``size`` None
    when not stored, ``url`` where the document is fetched from (its
    address at ``url``, else its stored file's), ``file`` the stored name
    (``stored_name``, "" without a hash; whether the bytes exist is
    storage's question), ``copy`` a bool."""
    raw = props.get("doc") if isinstance(props, dict) else None
    if raw is None:
        return None
    try:
        doc = check_document(raw)
    except (ValueError, TypeError):
        return None
    file = stored_name(doc["hash"], doc["ext"]) if doc["hash"] else ""
    return {
        "format": doc["format"],
        "language": formats.language_of(doc["name"], doc["ext"]) if doc["format"] == "text" else None,
        "name": doc["name"],
        "hash": doc["hash"],
        "ext": doc["ext"],
        "size": doc.get("size"),
        "at": doc["at"],
        "url": doc["url"] if doc["at"] == "url" else upload_url(file),
        "file": file,
        "copy": bool(doc.get("copy")),
    }


def own_hash(view) -> str:
    """The hash of the document a block owns: "" for none and for a copy.
    What the lookups of "the page of this file" key on."""
    return "" if not view or view["copy"] else view["hash"]


def file_hash(view) -> str:
    """The hash of the stored file a block shows, a copy's included: what
    the readers of the bytes read."""
    return view["hash"] if view else ""


def is_pdf(view) -> bool:
    return view is not None and view["format"] == "pdf"


def in_place(view) -> bool:
    """Whether the document is read where it is on this computer."""
    return view is not None and view["at"] == "path"


def pdf_file_hash(view) -> str:
    """The hash of the stored PDF a block shows (``file_hash``), "" when its
    document is none or no PDF: what the readers of a PDF's bytes read."""
    return file_hash(view) if is_pdf(view) else ""


def source_address(view) -> str:
    """The address a document is shown from, "" for one stored or read in
    place."""
    return view["url"] if view and view["at"] == "url" else ""


def document_at(doc: dict, at: str) -> dict:
    """The stored doc (or a view) of the same file, read from ``store`` or
    ``path``."""
    return document(doc["hash"], "", doc["name"], at=at, format=doc["format"], ext=doc["ext"],
                    size=doc.get("size"))


def copy_document(doc: dict) -> dict:
    """The stored doc a duplicate of a page writes, from its doc (or view):
    a stored file, or one read in place, is shown as a copy of its stored
    file (the original keeps owning it); an address is kept without the
    original's hash (an upload's address making it a copy of that upload).
    Twin of copyDocument in frontend/src/documents/pageDocument.js."""
    if doc["at"] == "url":
        return document("", doc["url"], doc["name"], at="url", format=doc["format"], ext=doc["ext"],
                        size=doc.get("size"))
    return document(doc["hash"], "", doc["name"], at="store", format=doc["format"], ext=doc["ext"],
                    size=doc.get("size"), copy=True)


def document_title(name: str, url: str, hash: str) -> str:
    """The automatic title of a page given a document: its file name, else
    the name its address suggests, else its hash."""
    from ..storage import display_filename, url_filename  # local: storage imports this module

    return display_filename(name or "") or url_filename(url or "") or hash

"""The citation styles an account adds beside the bundled ones.

Gamma renders citations in the browser (frontend/src/citations/, citeproc-js)
in a CSL style: one of the sixteen it carries (frontend/src/citations/styles/,
``BUNDLED``) or one the account added here, from the CSL repository Zotero
serves (https://www.zotero.org/styles, about 10,900 styles) or from a
``.csl`` file. They are rows of users.db ``citation_styles``: an
account's own, at most ``STYLES_MAX`` of them, kept on this server only
(Gamma Cloud carries the account's default style's id, not the style).

A style is XML, checked before it is kept: at most 512 KB, no DOCTYPE or
entity declaration (refused before the parser sees it), a ``style`` root in
the CSL namespace, and a citation layout unless it is a dependent style,
which has none: it names an independent parent and renders with that one's
layout, and its own title, class and default locale. A dependent style's
parent must be a style of the repository (its ``independent-parent`` link
``https?://www.zotero.org/styles/<id>``): a bundled one is named in
``parent``, any other is fetched with it into ``parent_xml``. A style is
kept under its own id, the last segment of its ``<id>``, since the
repository redirects a renamed style (``/styles/vancouver`` gives
``nlm-citation-sequence``).

The repository is searched in its index (``INDEX_URL``, 3 MB, held in
memory for a day) and read through ``net_guard.guarded_urlopen`` (the
admin's proxy, the address checks, every redirect checked again), only
while the server option ``citation_styles`` is on (Settings → Server →
Online services). With it off a file can still be added, unless its parent
would have to be fetched. Every route is the signed-in account's own:
no guest, share link or integration token (``require_personal_user_id``).
Docs: docs/dev/citations.md.
"""

import json
import re
import threading
import time
import urllib.error
import xml.etree.ElementTree as ET
from urllib.request import Request as URLRequest

from fastapi import APIRouter, File, HTTPException, Request, Response, UploadFile
from pydantic import BaseModel

from .. import server_options
from ..auth import require_personal_user_id
from ..db import connect_users_db, page_now
from ..logbuf import log
from ..net_guard import guarded_urlopen

router = APIRouter(prefix="/api/citations", tags=["citations"])

CSL = "{http://purl.org/net/xbiblio/csl}"
STYLE_BYTES_MAX = 512 * 1024
STYLES_MAX = 50
RESULTS_MAX = 50
INDEX_URL = "https://www.zotero.org/styles-files/styles.json"
STYLE_URL = "https://www.zotero.org/styles/{}"
INDEX_BYTES_MAX = 16 * 1024 * 1024
INDEX_TTL_S = 24 * 3600
INDEX_RETRY_S = 60  # how long a failed read of the index is the answer
FETCH_TIMEOUT_S = 30

# The styles every Gamma carries and their class, as
# tests/shared/citationstyles.json lists them for both sides: a dependent
# style whose parent is one of them is kept without the parent, which the
# browser has, and takes the parent's class when it names none.
BUNDLED = {
    "apa": "author-date", "american-chemical-society": "numeric", "american-medical-association": "numeric",
    "american-physics-society": "numeric", "american-political-science-association": "author-date",
    "american-sociological-association": "author-date", "chicago-author-date": "author-date",
    "chicago-notes-bibliography": "note", "chicago-shortened-notes-bibliography": "note",
    "elsevier-harvard": "author-date", "harvard-cite-them-right": "author-date", "ieee": "numeric",
    "mhra-notes": "note", "modern-language-association": "author", "nature": "numeric",
    "nlm-citation-sequence": "numeric"}
KINDS = ("author-date", "numeric", "note", "label", "author")

STYLE_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,120}")
PARENT_LINK = re.compile(r"https?://www\.zotero\.org/styles/([a-z0-9-]+)")
_LOCALE = re.compile(r"[a-z]{2,3}(?:-[A-Za-z0-9]{2,8})*")

NO_GUESTS = "Sign in with your own account to add citation styles."
DOWNLOADS_OFF = ("Downloading citation styles is switched off on this server (Settings → Server → "
                 "Online services). A style's .csl file can still be added.")

# Columns the listing and an added style's answer give (the XML is fetched
# alone, from GET /api/citations/styles/<id>.csl).
_ROW = ("style_id", "title", "short_title", "kind", "locale", "parent", "source", "added_at")


def _row(values) -> dict:
    row = dict(zip(_ROW, values))
    return {"id": row.pop("style_id"), **row}


def _downloads() -> bool:
    return bool(server_options.get("citation_styles"))


def _need_downloads() -> None:
    if not _downloads():
        raise HTTPException(403, DOWNLOADS_OFF)


# --- the repository -------------------------------------------------------------

def _fetch(url: str, limit: int) -> bytes:
    """The body of a guarded GET of ``url``, at most ``limit`` bytes and one
    more (so a caller can tell a body that is too long). urllib's errors
    propagate: an HTTPError with the repository's status, URLError when it
    cannot be reached."""
    req = URLRequest(url, headers={"User-Agent": "gamma-server (citation styles)",
                                   "Accept": "application/vnd.citationstyles.style+xml, application/json"})
    with guarded_urlopen(req, timeout=FETCH_TIMEOUT_S) as resp:
        return resp.read(limit + 1)


_index_lock = threading.Lock()
# The index as last read and until when it is the answer; ``error``, the
# reason a read failed, which is the answer for ``INDEX_RETRY_S``.
_index: dict = {"until": 0.0, "styles": [], "error": ""}


def _repository_index() -> list[dict]:
    """The repository's styles, ``{id, title, short_title, kind, dependent,
    updated}`` each, from its index, fetched once a day; 502 when it cannot
    be read, and for a minute after: the searches queued behind a fetch
    that failed (a repository the network drops waits out the timeout)
    fail at once instead of each waiting out a fetch of its own."""
    with _index_lock:
        if time.monotonic() < _index["until"]:
            if _index["error"]:
                raise HTTPException(502, _index["error"])
            return _index["styles"]
        try:
            entries = json.loads(_fetch(INDEX_URL, INDEX_BYTES_MAX))
        except (OSError, ValueError) as exc:
            log.warning(f"[citations] the style repository's index: {exc}")
            _index.update(until=time.monotonic() + INDEX_RETRY_S,
                          error=f"The style repository could not be read: {exc}")
            raise HTTPException(502, _index["error"]) from None
        styles = []
        for entry in entries if isinstance(entries, list) else ():
            name = str(entry.get("name") or "") if isinstance(entry, dict) else ""
            if not STYLE_ID.fullmatch(name):
                continue
            categories = entry.get("categories")
            kind = str(categories.get("format") or "") if isinstance(categories, dict) else ""
            styles.append({"id": name, "title": str(entry.get("title") or name),
                           "short_title": str(entry.get("titleShort") or ""),
                           "kind": kind if kind in KINDS else "", "dependent": bool(entry.get("dependent")),
                           "updated": str(entry.get("updated") or "")})
        _index.update(until=time.monotonic() + INDEX_TTL_S, styles=styles, error="")
        return styles


def search(styles: list[dict], query: str) -> list[dict]:
    """The styles whose title, short title or id hold every word of
    ``query``, the closest first: an id or a short title that is the query,
    then a title that starts with it, then the rest; within each, the
    independent styles first, then by title. At most ``RESULTS_MAX``;
    none for a blank query."""
    q = " ".join(str(query or "").lower().split())
    words = q.split()
    if not words:
        return []
    found = []
    for style in styles:
        title, short = style["title"].lower(), style["short_title"].lower()
        if all(w in f"{title} {short} {style['id']}" for w in words):
            rank = 0 if q in (style["id"], short) else 1 if title.startswith(q) else 2
            found.append((rank, style["dependent"], title, style))
    found.sort(key=lambda f: f[:3])
    return [f[3] for f in found[:RESULTS_MAX]]


def _repository_style(slug: str, what: str) -> bytes:
    """The XML the repository serves for the style ``slug`` (it follows a
    rename); 404 when it has none (``what`` ends that sentence: why it was
    asked for), 502 when it cannot be reached."""
    try:
        return _fetch(STYLE_URL.format(slug), STYLE_BYTES_MAX)
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise HTTPException(404, f"The style repository has no style {slug!r}{what}.") from None
        raise HTTPException(502, f"The style repository answered {exc.code} for {slug!r}.") from None
    except OSError as exc:
        raise HTTPException(502, f"The style repository could not be reached: {exc}") from None


# --- checking a style -----------------------------------------------------------

def read_style(raw: bytes) -> dict:
    """What a CSL style's XML ``raw`` says of itself: ``{id, title,
    short_title, kind, locale, parent_id, xml}`` (``parent_id`` the
    repository id its ``independent-parent`` link names, "" for an
    independent style; ``kind`` "" when it names no class). 400 with the
    reason when it is not a style this server keeps."""
    if len(raw) > STYLE_BYTES_MAX:
        raise HTTPException(400, "A citation style is 512 KB at most.")
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(400, "This is not a CSL style: it is not UTF-8 text.") from None
    # The parser would expand what a DTD declares (a billion laughs): refused unread.
    if re.search(r"<!\s*(?:DOCTYPE|ENTITY)", text, re.I):
        raise HTTPException(400, "This is not a CSL style: it declares a DOCTYPE or entities.")
    try:
        root = ET.fromstring(text)
    except ET.ParseError as exc:
        raise HTTPException(400, f"This is not a CSL style: it is not well-formed XML ({exc}).") from None
    info = root.find(f"{CSL}info")
    if root.tag != f"{CSL}style" or info is None:
        raise HTTPException(400, "This is not a CSL style: its root is not a CSL style element with its info.")
    links = [link.get("href", "").strip() for link in info.findall(f"{CSL}link")
             if link.get("rel") == "independent-parent"]
    parent_id = ""
    if links:
        parent = PARENT_LINK.fullmatch(links[0])
        if not parent:
            raise HTTPException(400, "This dependent style's parent is not a style of the CSL repository "
                                     "(www.zotero.org/styles), so it cannot be had.")
        parent_id = parent.group(1)
    elif root.find(f"{CSL}citation") is None:
        raise HTTPException(400, "This CSL style has no citation layout.")
    own = (info.findtext(f"{CSL}id") or "").strip().rstrip("/").rsplit("/", 1)[-1].lower()
    style_id = re.sub(r"[^a-z0-9]+", "-", own).strip("-")[:121]
    if not STYLE_ID.fullmatch(style_id):
        raise HTTPException(400, "This CSL style has no id.")
    title = " ".join((info.findtext(f"{CSL}title") or "").split())
    if not title:
        raise HTTPException(400, "This CSL style has no title.")
    kinds = [c.get("citation-format") for c in info.findall(f"{CSL}category") if c.get("citation-format")]
    kind = kinds[0] if kinds and kinds[0] in KINDS else ""
    if not kind and not parent_id:  # a hand-made style that names no class: what its layout shows
        kind = "note" if root.get("class") == "note" else "numeric" if "citation-number" in text else "author-date"
    locale = root.get("default-locale", "").strip()
    return {"id": style_id, "title": title[:300],
            "short_title": " ".join((info.findtext(f"{CSL}title-short") or "").split())[:300],
            "kind": kind, "locale": locale if _LOCALE.fullmatch(locale) else "", "parent_id": parent_id,
            "xml": text}


def _keep(user_id: str, style: dict, source: str) -> dict:
    """Store ``style`` (``read_style``'s) as the account's: a dependent
    style with its parent (bundled: named; else fetched from the
    repository, which needs the downloads on), the class its parent gives
    when it names none. A style of the account's id replaces it; a new one
    past ``STYLES_MAX`` is refused (409), as is one with a bundled id (400).
    Returns the row."""
    if style["id"] in BUNDLED:
        raise HTTPException(400, f"{style['title']} is one of the styles Gamma carries already "
                                 f"(its id is {style['id']}); a copy of your own needs an id of its own.")
    parent, parent_xml, kind = "", "", style["kind"]
    if style["parent_id"] in BUNDLED:
        parent, kind = style["parent_id"], kind or BUNDLED[style["parent_id"]]
    elif style["parent_id"]:
        _need_downloads()
        independent = read_style(_repository_style(style["parent_id"], ", which this style names as its parent"))
        if independent["parent_id"]:
            raise HTTPException(400, "This dependent style's parent is a dependent style too.")
        parent_xml, kind = independent["xml"], kind or independent["kind"]
    row = (user_id, style["id"], style["title"], style["short_title"], kind or "author-date", style["locale"], parent,
           style["xml"], parent_xml, source, page_now())
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the count and the insert as one step: two adds cannot pass 50
        known = conn.execute("SELECT 1 FROM citation_styles WHERE user_id = ? AND style_id = ?",
                             (user_id, style["id"])).fetchone()
        count = conn.execute("SELECT COUNT(*) FROM citation_styles WHERE user_id = ?", (user_id,)).fetchone()[0]
        if not known and count >= STYLES_MAX:
            raise HTTPException(409, f"You have added {STYLES_MAX} styles, the most an account keeps: "
                                     "remove a style to add another.")
        conn.execute("INSERT OR REPLACE INTO citation_styles (user_id, style_id, title, short_title, kind, locale, "
                     "parent, xml, parent_xml, source, added_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", row)
        conn.commit()
    return _row(row[1:7] + row[9:])


# --- the routes -----------------------------------------------------------------

@router.get("/styles")
def list_styles(request: Request):
    """The account's added styles (no XML), by title; ``limit``, the most
    it may add; ``downloads``, whether the repository is open here."""
    user_id = require_personal_user_id(request, NO_GUESTS)
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {', '.join(_ROW)} FROM citation_styles WHERE user_id = ? "
                            "ORDER BY title COLLATE NOCASE, style_id", (user_id,)).fetchall()
    return {"styles": [_row(r) for r in rows], "limit": STYLES_MAX, "downloads": _downloads()}


@router.get("/repository")
def search_repository(request: Request, q: str = ""):
    """The repository's styles that match ``q`` (``search``), the closest
    first; 403 while downloads are off."""
    require_personal_user_id(request, NO_GUESTS)
    _need_downloads()
    return {"results": search(_repository_index(), q[:200])}


class AddStyleRequest(BaseModel):
    id: str  # the style's id in the repository (its address's last segment)


@router.post("/styles")
def add_style(payload: AddStyleRequest, request: Request):
    """Add the repository's style ``id`` (with the parent it needs); the row
    added. A renamed style is kept under the id it has now."""
    user_id = require_personal_user_id(request, NO_GUESTS)
    slug = payload.id.strip().lower()
    if not STYLE_ID.fullmatch(slug):
        raise HTTPException(400, "A style's id is lowercase letters, digits and dashes.")
    _need_downloads()
    if slug in BUNDLED:
        raise HTTPException(400, f"{slug} is one of the styles Gamma carries already.")
    return _keep(user_id, read_style(_repository_style(slug, "")), "repository")


@router.post("/styles/upload")
def upload_style(request: Request, file: UploadFile = File(...)):
    """Add a style from its ``.csl`` file; the row added."""
    user_id = require_personal_user_id(request, NO_GUESTS)
    return _keep(user_id, read_style(file.file.read(STYLE_BYTES_MAX + 1)), "file")


@router.delete("/styles/{style_id}")
def delete_style(style_id: str, request: Request):
    user_id = require_personal_user_id(request, NO_GUESTS)
    with connect_users_db() as conn:
        gone = conn.execute("DELETE FROM citation_styles WHERE user_id = ? AND style_id = ?",
                            (user_id, style_id)).rowcount
        conn.commit()
    if not gone:
        raise HTTPException(404, "You have no such style.")
    return {"ok": True}


@router.get("/styles/{style_id}.csl")
def style_xml(style_id: str, request: Request):
    """The XML the browser renders with: a dependent style's fetched parent
    (with the dependent's default locale in ``X-Csl-Locale``, which
    overrides the parent's), else the style itself, a dependent one naming
    its bundled parent in ``X-Csl-Parent``. An uploaded file is any XML
    with a CSL root, which a browser opening the address would render in
    this origin, an XHTML script in it too (stored XSS): it downloads,
    sandboxed, as uploads.py serves an SVG; the app's fetch() reads it
    as it is."""
    user_id = require_personal_user_id(request, NO_GUESTS)
    with connect_users_db() as conn:
        row = conn.execute("SELECT xml, parent_xml, parent, locale FROM citation_styles WHERE user_id = ? "
                           "AND style_id = ?", (user_id, style_id)).fetchone()
    if not row:
        raise HTTPException(404, "You have no such style.")
    xml, parent_xml, parent, locale = row
    return Response(parent_xml or xml, media_type="application/vnd.citationstyles.style+xml",
                    headers={"X-Csl-Parent": parent, "X-Csl-Locale": locale, "Cache-Control": "no-cache",
                             "Content-Disposition": "attachment",
                             "Content-Security-Policy": "default-src 'none'; sandbox"})

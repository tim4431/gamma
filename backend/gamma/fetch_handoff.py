"""Fetches handed to the user's browser: what the AI chat asks for when a
paper is behind a wall only a person gets past.

``fetch_paper`` opens a request here when it meets a CAPTCHA or bot check, a
sign-in page, a refusal, or an article page whose PDF needs access
(``ai_web.WALLS``). The chat shows a card for it. The user opens the page
through ``/api/ai/handoffs/<id>/go`` and signs in or passes the check; then
Gamma Connector, which recognized that tab by the ``/go`` address, sends the
PDF it can now download (``POST …/pdf``), or the user drops the file on the
card. The PDF's text is kept for the account alone, in memory like any
fetched document, and the next ``fetch_paper`` of the same source reads it.
The PDF itself is held too, so the chat can save the paper to the library
(``POST …/store`` writes it into a workspace only then). Nothing is written
to disk or to a workspace by itself; a restart forgets everything.
"""

import secrets
import threading
import time
from urllib.parse import urlsplit

TTL = 6 * 3600              # a request (with what was delivered for it) lives this long
MAX_PER_ACCOUNT = 20        # requests kept per account; the oldest are dropped first
MAX_DELIVERED_CHARS = 30_000_000  # delivered text kept in all, the oldest dropped first
MAX_HELD_BYTES = 200_000_000      # delivered PDFs held for saving, the oldest let go first
# What the Connector reports doing in the tab, shown on the card: no PDF link
# on the page yet, the page is a bot check or CAPTCHA, the PDF link leads to
# a sign-in, opening one in the tab, no link gave a PDF, the tab shows
# another paper, the user closed the tab (the card offers to open it again).
NOTES = ("looking", "check", "signin", "opening", "refused", "other", "closed")

_requests: dict[str, dict] = {}   # id → request (insertion order = age)
_lock = threading.Lock()


def source_key(source: str) -> str:
    """One key per work however the model spells it: ``doi:…`` / ``arxiv:…``
    for identifiers in any form, else the URL itself."""
    from .ai_web import identifier

    kind, ident = identifier(source)
    return f"{kind}:{ident.lower()}" if kind else (source or "").strip()


def _prune(now: float):
    for rid in [rid for rid, req in _requests.items() if now - req["created"] > TTL]:
        del _requests[rid]


def open_request(user: str, source: str, *, wall: str, url: str, pdf_url: str = "",
                 detail: str = "") -> dict:
    """The waiting request for ``source`` — a new one, or the account's
    request for the same work that is still waiting (a model retrying a
    source it was told to leave gets the same card)."""
    if not url.lower().startswith(("https://", "http://")):
        raise ValueError("a handoff opens an http(s) page")
    now = time.time()
    key = source_key(source)
    with _lock:
        _prune(now)
        mine = [r for r in _requests.values() if r["user"] == user]
        for req in mine:
            if req["key"] == key and req["status"] == "waiting":
                req.update(wall=wall, detail=detail)
                return dict(req)
        for req in mine[:max(0, len(mine) - MAX_PER_ACCOUNT + 1)]:
            del _requests[req["id"]]
        req = {"id": secrets.token_urlsafe(18), "user": user, "key": key, "source": source,
               "url": url, "pdf_url": pdf_url, "host": urlsplit(url).hostname or "",
               "wall": wall, "detail": detail, "status": "waiting", "created": now,
               "watched": 0.0, "note": "", "background": False, "done_at": 0.0, "from_url": "",
               "doc": None, "pdf": None}
        _requests[req["id"]] = req
        return dict(req)


def get(user: str, rid: str) -> dict | None:
    with _lock:
        _prune(time.time())
        req = _requests.get(rid)
        return dict(req) if req and req["user"] == user else None


def target(rid: str) -> dict | None:
    """Where a request's ``/go`` link leads, for anyone holding the link
    (``{"url", "host", "user"}``); the page decides how far to trust it."""
    with _lock:
        req = _requests.get(rid)
        if not req or time.time() - req["created"] > TTL:
            return None
        return {"url": req["url"], "host": req["host"], "user": req["user"]}


def _update(user: str, rid: str, **fields) -> dict | None:
    with _lock:
        req = _requests.get(rid)
        if not req or req["user"] != user:
            return None
        req.update(fields)
        return dict(req)


def watch(user: str, rid: str, note: str = "", background: bool = False) -> dict | None:
    """The Connector took the request's tab, and what it is doing there
    (a NOTES entry; "" when it just took it), in a tab of its own out of
    sight (``background``) or one the user sees: the card says so."""
    return _update(user, rid, watched=time.time(), note=note if note in NOTES else "",
                   background=bool(background))


def dismiss(user: str, rid: str) -> dict | None:
    with _lock:
        req = _requests.get(rid)
        if not req or req["user"] != user:
            return None
        if req["status"] == "waiting":
            req["status"] = "dismissed"
        return dict(req)


class Settled(Exception):
    """The request is no longer waiting (delivered, dismissed or expired)."""


def deliver(user: str, rid: str, data: bytes, from_url: str = "") -> dict | None:
    """Keep the text of the PDF the user got for request ``rid``; raises
    ai_web.FetchError when it is not a readable PDF and Settled when the
    request no longer waits for one. None = no such request."""
    from .ai_web import pdf_document

    req = get(user, rid)
    if req is None:
        return None
    if req["status"] != "waiting":
        raise Settled(req["status"])
    url = from_url or req["pdf_url"] or req["url"]
    doc = pdf_document(url, data, "The user fetched this PDF in their own browser"
                       + (f" from {from_url}" if from_url else "") + " and sent it to the chat.")
    doc.update(delivered=True, request=rid)
    with _lock:
        live = _requests.get(rid)
        if not live or live["user"] != user:
            return None
        if live["status"] != "waiting":  # delivered from elsewhere meanwhile
            raise Settled(live["status"])
        live.update(status="done", doc=doc, pdf=data, from_url=from_url, done_at=time.time())
        # Delivered text is capped like the fetch cache: the oldest goes.
        total = sum(r["doc"]["chars"] for r in _requests.values() if r["doc"])
        for old in [r for r in _requests.values() if r["doc"]]:
            if total <= MAX_DELIVERED_CHARS or old is live:
                break
            total -= old["doc"]["chars"]
            old.update(doc=None, pdf=None, status="expired")
        # So are the PDFs held for saving; a paper let go keeps its text.
        held = sum(len(r["pdf"]) for r in _requests.values() if r["pdf"])
        for old in [r for r in _requests.values() if r["pdf"]]:
            if held <= MAX_HELD_BYTES or old is live:
                break
            held -= len(old["pdf"])
            old["pdf"] = None
        return dict(live)


def held_pdf(user: str, rid: str) -> tuple[bytes, str] | None:
    """The PDF delivered for request ``rid`` and the address it came from,
    while it is still held; None otherwise."""
    with _lock:
        _prune(time.time())
        req = _requests.get(rid)
        if not req or req["user"] != user or not req["pdf"]:
            return None
        return req["pdf"], req["from_url"] or req["pdf_url"] or req["url"]


def delivered(user: str | None, source: str) -> dict | None:
    """The document the user delivered for ``source`` (by any spelling of
    it, or by the URLs its request named), newest first."""
    if not user:
        return None
    key = source_key(source)
    with _lock:
        for req in reversed(_requests.values()):
            if (req["user"] == user and req["doc"]
                    and (req["key"] == key or source in (req["url"], req["pdf_url"]))):
                return req["doc"]
    return None


def public(req: dict) -> dict:
    """What the chat card and the Connector see: never the text or the PDF."""
    doc = req.get("doc") or {}
    return {"id": req["id"], "source": req["source"], "url": req["url"],
            "pdf_url": req["pdf_url"], "host": req["host"], "wall": req["wall"],
            "detail": req["detail"], "status": req["status"],
            "watched": bool(req["watched"]), "note": req.get("note", ""),
            "background": bool(req.get("background")),
            "pages": len(doc.get("pages") or []), "held": bool(req.get("pdf")),
            "from_url": req["from_url"]}


def clear():
    with _lock:
        _requests.clear()

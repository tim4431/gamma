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
Nothing is written to disk or to a workspace; a restart forgets everything.
"""

import secrets
import threading
import time
from urllib.parse import urlsplit

TTL = 6 * 3600              # a request (with what was delivered for it) lives this long
MAX_PER_ACCOUNT = 20        # requests kept per account; the oldest are dropped first
MAX_DELIVERED_CHARS = 30_000_000  # delivered text kept in all, the oldest dropped first
# What the Connector reports doing in the tab, shown on the card: no PDF link
# on the page yet, the PDF link leads to a sign-in, opening one in the tab,
# no link gave a PDF.
NOTES = ("looking", "signin", "opening", "refused")

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
               "watched": 0.0, "note": "", "done_at": 0.0, "from_url": "", "doc": None}
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


def watch(user: str, rid: str, note: str = "") -> dict | None:
    """The Connector took the request's tab, and what it is doing there
    (a NOTES entry; "" when it just took it): the card says so."""
    return _update(user, rid, watched=time.time(), note=note if note in NOTES else "")


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
    doc["delivered"] = True
    with _lock:
        live = _requests.get(rid)
        if not live or live["user"] != user:
            return None
        if live["status"] != "waiting":  # delivered from elsewhere meanwhile
            raise Settled(live["status"])
        live.update(status="done", doc=doc, from_url=from_url, done_at=time.time())
        # Delivered text is capped like the fetch cache: the oldest goes.
        total = sum(r["doc"]["chars"] for r in _requests.values() if r["doc"])
        for old in [r for r in _requests.values() if r["doc"]]:
            if total <= MAX_DELIVERED_CHARS or old is live:
                break
            total -= old["doc"]["chars"]
            old.update(doc=None, status="expired")
        return dict(live)


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
    """What the chat card and the Connector see: never the text."""
    doc = req.get("doc") or {}
    return {"id": req["id"], "source": req["source"], "url": req["url"],
            "pdf_url": req["pdf_url"], "host": req["host"], "wall": req["wall"],
            "detail": req["detail"], "status": req["status"],
            "watched": bool(req["watched"]), "note": req.get("note", ""),
            "pages": len(doc.get("pages") or []),
            "from_url": req["from_url"]}


def clear():
    with _lock:
        _requests.clear()

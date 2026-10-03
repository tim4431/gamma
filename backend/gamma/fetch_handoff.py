"""Fetches handed to the user's browser: what the AI chat asks for when a
paper is behind a wall only a person gets past.

``fetch_paper`` opens a request here when it meets a CAPTCHA or bot check, a
sign-in page, a refusal, or an article page whose PDF needs access
(``ai_web.WALLS``). The chat shows a card for it and, by default, waits on
that card inside the same reply (``ai_agent.PaperWait``): the user opens the
page, signs in or passes the check, and Gamma Connector — which recognized
that tab — sends the PDF back (``POST …/pdf``), or the user drops the file
on the card. The call then simply runs again and reads what arrived, so the
model gets the paper where it asked for it.

A wait that nobody answers gives up (:data:`IDLE_TIMEOUT`), and the request
stays open for six hours: its card remains under the reply, and it is listed
in Background tasks until the PDF arrives or the user lets it go. Skipping
settles it at once and can tell the model what to do instead.

The PDF's text is kept for the account alone, in memory like any fetched
document, and the next ``fetch_paper`` of the same source reads it. The PDF
itself is held too, so the chat can save the paper to the library (``POST
…/store`` writes it into a workspace only then). Nothing is written to disk
or to a workspace by itself; a restart forgets everything.
"""

import secrets
import threading
import time
from urllib.parse import urlsplit

from .db import page_now

TTL = 6 * 3600              # a request (with what was delivered for it) lives this long
MAX_PER_ACCOUNT = 20        # requests kept per account; the oldest are dropped first
MAX_DELIVERED_CHARS = 30_000_000  # delivered text kept in all, the oldest dropped first
MAX_HELD_BYTES = 200_000_000      # delivered PDFs held for saving, the oldest let go first
MAX_NOTE = 2000             # chars of what the user says to do instead, when they skip

# How long a reply waits on a card. IDLE_TIMEOUT is the quiet stretch it
# gives up after — long enough to sign in, short enough that a reply never
# hangs on a user who walked away. Every sign of life (Gamma Connector
# taking the tab, or reporting what it found there) starts it over, up to
# MAX_WAIT in all: a person working through an institutional sign-in keeps
# the reply open, an unanswered card does not.
IDLE_TIMEOUT = 300.0
MAX_WAIT = 900.0
_POLL = 1.0                 # seconds between deadline checks while waiting

# What the Connector reports doing in the tab, shown on the card: no PDF link
# on the page yet, the page is a bot check or CAPTCHA, the PDF link leads to
# a sign-in, opening one in the tab, no link gave a PDF, the tab shows
# another paper, the user closed the tab (the card offers to open it again).
NOTES = ("looking", "check", "signin", "opening", "refused", "other", "closed")

WAITING, DONE, DISMISSED, EXPIRED = "waiting", "done", "dismissed", "expired"

_requests: dict[str, dict] = {}   # id → request (insertion order = age)
_lock = threading.Lock()
# Notified whenever a request settles or shows a sign of life, so a reply
# waiting on its card wakes at once instead of at its next poll.
_changed = threading.Condition(_lock)


def source_key(source: str) -> str:
    """One key per work however the model spells it: ``doi:…`` / ``arxiv:…``
    for identifiers in any form, else the URL itself."""
    from .ai_web import identifier

    kind, ident = identifier(source)
    return f"{kind}:{ident.lower()}" if kind else (source or "").strip()


def _prune(now: float):
    for rid in [rid for rid, req in _requests.items() if now - req["created"] > TTL]:
        del _requests[rid]


def open_request(user_id: str, source: str, *, wall: str, url: str, pdf_url: str = "",
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
        mine = [r for r in _requests.values() if r["user_id"] == user_id]
        for req in mine:
            if req["key"] == key and req["status"] == WAITING:
                req.update(wall=wall, detail=detail)
                return dict(req)
        for req in mine[:max(0, len(mine) - MAX_PER_ACCOUNT + 1)]:
            del _requests[req["id"]]
        req = {"id": secrets.token_urlsafe(18), "user_id": user_id, "key": key, "source": source,
               "url": url, "pdf_url": pdf_url, "host": urlsplit(url).hostname or "",
               "wall": wall, "detail": detail, "status": WAITING, "created": now,
               "created_at": page_now(), "watched": 0.0, "note": "", "background": False,
               "done_at": 0.0, "from_url": "", "skip_note": "", "doc": None, "pdf": None}
        _requests[req["id"]] = req
        return dict(req)


def get(user_id: str, rid: str) -> dict | None:
    with _lock:
        _prune(time.time())
        req = _requests.get(rid)
        return dict(req) if req and req["user_id"] == user_id else None


def waiting(user_id: str) -> list[dict]:
    """The account's requests still waiting for a PDF, oldest first — the
    Background tasks rows (``routers/jobs.py``)."""
    with _lock:
        _prune(time.time())
        return [public(req) for req in _requests.values()
                if req["user_id"] == user_id and req["status"] == WAITING]


def target(rid: str) -> dict | None:
    """Where a request's ``/go`` link leads, for anyone holding the link
    (``{"url", "host", "user_id"}``); the page decides how far to trust it."""
    with _lock:
        req = _requests.get(rid)
        if not req or time.time() - req["created"] > TTL:
            return None
        return {"url": req["url"], "host": req["host"], "user_id": req["user_id"]}


def _update(user_id: str, rid: str, **fields) -> dict | None:
    with _changed:
        req = _requests.get(rid)
        if not req or req["user_id"] != user_id:
            return None
        req.update(fields)
        _changed.notify_all()
        return dict(req)


def watch(user_id: str, rid: str, note: str = "", background: bool = False) -> dict | None:
    """The Connector took the request's tab, and what it is doing there
    (a NOTES entry; "" when it just took it), in a tab of its own out of
    sight (``background``) or one the user sees: the card says so, and a
    reply waiting on it gives the user more time."""
    return _update(user_id, rid, watched=time.time(), note=note if note in NOTES else "",
                   background=bool(background))


def dismiss(user_id: str, rid: str, note: str = "") -> dict | None:
    """Settle a waiting request without a PDF. ``note`` is what the user
    wants the assistant to do instead — only a skip inside a live reply has
    one, and only the model ever reads it."""
    with _changed:
        req = _requests.get(rid)
        if not req or req["user_id"] != user_id:
            return None
        if req["status"] == WAITING:
            req["status"] = DISMISSED
            req["skip_note"] = str(note or "").strip()[:MAX_NOTE]
            _changed.notify_all()
        return dict(req)


class Settled(Exception):
    """The request is no longer waiting (delivered, dismissed or expired)."""


def deliver(user_id: str, rid: str, data: bytes, from_url: str = "") -> dict | None:
    """Keep the text of the PDF the user got for request ``rid``; raises
    ai_web.FetchError when it is not a readable PDF and Settled when the
    request no longer waits for one. None = no such request."""
    from .ai_web import pdf_document

    req = get(user_id, rid)
    if req is None:
        return None
    if req["status"] != WAITING:
        raise Settled(req["status"])
    url = from_url or req["pdf_url"] or req["url"]
    doc = pdf_document(url, data, "The user fetched this PDF in their own browser"
                       + (f" from {from_url}" if from_url else "") + " and sent it to the chat.")
    doc.update(delivered=True, request=rid)
    with _changed:
        live = _requests.get(rid)
        if not live or live["user_id"] != user_id:
            return None
        if live["status"] != WAITING:  # delivered from elsewhere meanwhile
            raise Settled(live["status"])
        live.update(status=DONE, doc=doc, pdf=data, from_url=from_url, done_at=time.time())
        _trim_locked(live)
        _changed.notify_all()
        return dict(live)


def _trim_locked(keep: dict) -> None:
    """Hold the delivered text and PDFs to their caps, oldest first, never
    dropping the delivery that just arrived. A paper let go keeps its text
    when only the held file has to go."""
    total = sum(r["doc"]["chars"] for r in _requests.values() if r["doc"])
    for old in [r for r in _requests.values() if r["doc"]]:
        if total <= MAX_DELIVERED_CHARS or old is keep:
            break
        total -= old["doc"]["chars"]
        old.update(doc=None, pdf=None, status=EXPIRED)
    held = sum(len(r["pdf"]) for r in _requests.values() if r["pdf"])
    for old in [r for r in _requests.values() if r["pdf"]]:
        if held <= MAX_HELD_BYTES or old is keep:
            break
        held -= len(old["pdf"])
        old["pdf"] = None


def wait_for_all(user_id: str, rids: list, *, stopped: threading.Event | None = None) -> dict:
    """Block until every request of ``rids`` has settled, or the wait gives
    up. Returns ``{rid: (outcome, note)}`` — ``"delivered"``, ``"dismissed"``
    (with what the user wants done instead), ``"expired"`` (still waiting
    when the wait ended) or ``"gone"``.

    The wait is patient while somebody is working on it: every report from
    Gamma Connector restarts the :data:`IDLE_TIMEOUT` stretch, up to
    :data:`MAX_WAIT` in all. ``stopped`` (the chat's client left) ends it at
    once.
    """
    started = time.monotonic()
    outcomes = {rid: ("gone", "") for rid in rids}
    with _changed:
        while True:
            pending, latest = [], 0.0
            for rid in rids:
                req = _requests.get(rid)
                if not req or req["user_id"] != user_id:
                    continue
                if req["status"] == WAITING:
                    pending.append(rid)
                    latest = max(latest, req["watched"])
                    continue
                outcomes[rid] = (("delivered", "") if req["status"] == DONE
                                 else (req["status"], req.get("skip_note", "")))
            if not pending or (stopped is not None and stopped.is_set()):
                break
            now = time.monotonic()
            # The quiet stretch starts over on every sign of life, but the
            # whole wait is still capped.
            since = min(now - started, now - _monotonic_of(latest)) if latest else now - started
            left = min(IDLE_TIMEOUT - since, MAX_WAIT - (now - started))
            if left <= 0:
                for rid in pending:
                    outcomes[rid] = ("expired", "")
                break
            _changed.wait(min(left, _POLL))
    return outcomes


def _monotonic_of(wall_clock: float) -> float:
    """``time.time()`` stamp read on the monotonic clock, so a wait is not
    confused by a system clock that moved."""
    return time.monotonic() - max(0.0, time.time() - wall_clock)


def held_pdf(user_id: str, rid: str) -> tuple[bytes, str] | None:
    """The PDF delivered for request ``rid`` and the address it came from,
    while it is still held; None otherwise."""
    with _lock:
        _prune(time.time())
        req = _requests.get(rid)
        if not req or req["user_id"] != user_id or not req["pdf"]:
            return None
        return req["pdf"], req["from_url"] or req["pdf_url"] or req["url"]


def delivered(user_id: str | None, source: str) -> dict | None:
    """The document the user delivered for ``source`` (by any spelling of
    it, or by the URLs its request named), newest first."""
    if not user_id:
        return None
    key = source_key(source)
    with _lock:
        for req in reversed(_requests.values()):
            if (req["user_id"] == user_id and req["doc"]
                    and (req["key"] == key or source in (req["url"], req["pdf_url"]))):
                return req["doc"]
    return None


def public(req: dict) -> dict:
    """What the chat card and the Connector see: never the text, the PDF or
    what the user said when they skipped."""
    doc = req.get("doc") or {}
    return {"id": req["id"], "source": req["source"], "url": req["url"],
            "pdf_url": req["pdf_url"], "host": req["host"], "wall": req["wall"],
            "detail": req["detail"], "status": req["status"],
            "created_at": req.get("created_at", ""),
            "watched": bool(req["watched"]), "note": req.get("note", ""),
            "background": bool(req.get("background")),
            "pages": len(doc.get("pages") or []), "held": bool(req.get("pdf")),
            "from_url": req["from_url"]}


def clear():
    with _changed:
        _requests.clear()
        _changed.notify_all()

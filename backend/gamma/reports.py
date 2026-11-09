"""Reports of content and of AI replies (docs/dev/api.md "Reports"): a
comment from its menu, a shared or published page from the share view's
menu, an AI reply from its actions in the chat.

``file`` reads what is reported where the reporter can read it (a
comment's text and author, a page's title and the share's maker) rather
than taking the browser's word for it, and keeps the report in users.db
``content_reports`` for this server's admins: Settings → Server → Reports
lists them (``listing``), resolves and deletes them, and a notice tells an
admin a new one came (gamma/notices.py). An AI reply's text is the
browser's: the chat keeps no copy the report could name.

On a server with Gamma Cloud sign-in on (the desktop app's and the apps'
own servers, the share host, hosted servers, a server connected to it), the
report also goes to the account server (``POST <issuer>/api/reports``,
docs/dev/cloud.md "Reports"), which mails Gamma Cloud's operator, the
developer: on a thread, so the reporter does not wait for it. The call
carries the reporter's Gamma Cloud access token when they linked their
account here, else this server's client id and secret when it is a
confidential client, else nothing. A report the account server did not
take is tried again by the hourly round (``tick``) for ``SEND_DAYS``.
"""

import base64
import json
import secrets
import threading
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from . import cloud_auth, comments, ratelimit
from .auth import is_token, resolve_ws, share_lookup, share_scope
from .cloud_auth import CloudAuthError
from .db import account_name, connect_pages_db, connect_users_db, format_stamp, page_now
from .logbuf import log

KINDS = ("comment", "page", "ai")
REASONS = ("harassment", "sexual", "violence", "spam", "illegal", "privacy", "harmful", "other")
STATES = ("open", "resolved")
MAX_DETAILS = 2000      # the reporter's own words
MAX_CONTENT = 8000      # an AI reply's text
MAX_NAME = 120
MAX_TITLE = 200
MAX_LINK = 2048
KEEP_DAYS = 180         # a report goes this long after it was made
SEND_DAYS = 7           # a report the account server did not take is tried again this long
SWEEP_INTERVAL_S = 3600
SEND_TIMEOUT = 20
_COLUMNS = ("id, kind, reason, details, content, author, title, workspace_id, page_id, comment_id, link, "
            "reporter_id, reporter, state, created_at, resolved_at, resolved_by, sent_at, send_error")


def _text(value, limit: int, *, lines: bool = False) -> str:
    raw = str(value or "")
    if lines:
        kept = "".join(c if c.isprintable() or c == "\n" else " " for c in raw.replace("\r\n", "\n"))
        return "\n".join(" ".join(line.split()) for line in kept.split("\n")).strip()[:limit]
    return " ".join("".join(c for c in raw if c.isprintable()).split())[:limit]


def _link(value) -> str:
    """A published page's address, as the browser was on it; '' for
    anything else, and for an address carrying a share link's token."""
    value = str(value or "").strip()
    try:
        url = urlsplit(value)
    except ValueError:
        return ""
    if (len(value) > MAX_LINK or url.scheme not in ("http", "https") or not url.hostname or "share=" in url.query
            or any(c.isspace() for c in value)):
        return ""
    return value


def _page(conn, page_id: str, scope) -> str:
    """The title of a page of the library the request can read; 404 / 403
    otherwise."""
    row = conn.execute("SELECT content FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                       (page_id,)).fetchone()
    if not row:
        raise HTTPException(404, "page not found")
    if scope is not None and not scope.allows_page(conn, page_id):
        raise HTTPException(403, "not accessible via this share link")
    return row[0] or "Untitled"


def _subject(request: Request, kind: str, page_id: str, comment_id: str, content: str, model: str) -> dict:
    """What is reported, read where the reporter reads it."""
    if kind == "ai":
        # the chat is an account's, or a saved one a share link shows its visitor
        if not request.state.user_id and (not request.query_params.get("share") or share_scope(request) is None):
            raise HTTPException(401)
        text = _text(content, MAX_CONTENT, lines=True)
        if not text:
            raise HTTPException(400, "the reply's text is required")
        return {"content": text, "author": _text(model, MAX_NAME), "workspace_id": "", "page_id": "",
                "comment_id": "", "title": ""}
    ws = resolve_ws(request)
    scope = share_scope(request)
    with connect_pages_db(ws) as conn:
        if kind == "comment":
            found = comments.reported(conn, comment_id)
            title = _page(conn, found["page_id"], scope)
            return {"content": _text(found["body"], MAX_CONTENT, lines=True), "author": found["author"],
                    "workspace_id": ws, "page_id": found["page_id"], "comment_id": comment_id, "title": title}
        title = _page(conn, page_id, scope)
    author = ""
    token = request.query_params.get("share") or ""
    share = share_lookup(token) if token else None
    if share and share.get("created_by"):
        with connect_users_db() as users:
            author = account_name(users, share["created_by"])
    return {"content": "", "author": author, "workspace_id": ws, "page_id": page_id, "comment_id": "",
            "title": title}


def file(request: Request, *, kind: str, reason: str, details: str = "", page_id: str = "", comment_id: str = "",
         content: str = "", model: str = "", link: str = "") -> dict:
    """Keep a report the request makes, and send it on to Gamma Cloud when
    this server signs people in with it → the stored report. 400 for a kind
    or reason it does not know, 403 for an integration token, 404 / 403
    for a comment or page the request cannot read, 429 past 10 in 10
    minutes per account or, for a visitor without one, per address."""
    if kind not in KINDS:
        raise HTTPException(400, "kind must be " + ", ".join(KINDS))
    if reason not in REASONS:
        raise HTTPException(400, "reason must be " + ", ".join(REASONS))
    if is_token(request):
        raise HTTPException(403, "an integration token cannot report — sign in")
    if kind == "comment" and not comment_id or kind == "page" and not page_id:
        raise HTTPException(400, "a comment report names the comment, a page report the page")
    who = request.state.user_id if request.state.user_id and not request.state.is_guest else ""
    ratelimit.check(f"reports:{who or ratelimit.client_ip(request)}", 10, 600)
    subject = _subject(request, kind, page_id, comment_id, content, model)
    row = {"id": "rep_" + secrets.token_urlsafe(12), "kind": kind, "reason": reason,
           "details": _text(details, MAX_DETAILS, lines=True), **subject,
           "link": _link(link) if kind == "page" else "", "reporter_id": who,
           "reporter": (request.state.user or "") if who else "", "state": "open", "created_at": page_now(),
           "resolved_at": "", "resolved_by": "", "sent_at": "", "send_error": ""}
    with connect_users_db() as conn:
        conn.execute(f"INSERT INTO content_reports ({_COLUMNS}) VALUES ({', '.join('?' * 19)})",
                     tuple(row[c] for c in _COLUMNS.split(", ")))
    if forwards():
        cloud_auth.server_url(request)  # a sidecar's loopback address, remembered for the send
        _background(lambda: send(row["id"]))
    log.info(f"[reports] {kind} reported ({reason}) by {row['reporter'] or 'a visitor'}")
    return row


def forwards() -> bool:
    """Whether reports go on to Gamma Cloud: its sign-in is on here."""
    return cloud_auth.settings()["enabled"]


def _background(fn) -> None:
    """Run ``fn`` on a daemon thread (tests run it inline)."""
    threading.Thread(target=fn, name="report-send", daemon=True).start()


def _get(report_id: str) -> dict | None:
    with connect_users_db() as conn:
        found = conn.execute(f"SELECT {_COLUMNS} FROM content_reports WHERE id = ?", (report_id,)).fetchone()
    return dict(zip(_COLUMNS.split(", "), found)) if found else None


def _auth(row: dict, cfg: dict) -> tuple[dict, str]:
    """The call's Authorization header and the subject whose token it
    carries (''): the reporter's grant, else this server's client secret,
    else none."""
    if row["reporter_id"]:
        subject = cloud_auth.grant_of(row["reporter_id"])[0]
        token = cloud_auth.access_token_for(row["reporter_id"]) if subject else None
        if token:
            return {"Authorization": f"Bearer {token}"}, subject
    secret = cloud_auth.client_secret()
    if secret:
        pair = base64.b64encode(f"{cfg['client_id']}:{secret}".encode()).decode()
        return {"Authorization": f"Basic {pair}"}, ""
    return {}, ""


def send(report_id: str) -> bool:
    """Hand one report to the account server; whether it took it. A failure
    is a warning and the row's ``send_error``, never the reporter's."""
    row = _get(report_id)
    if not row or row["sent_at"] or not forwards():
        return bool(row and row["sent_at"])
    cfg = cloud_auth.settings()
    url = cloud_auth.server_url()
    body = {"report_id": row["id"], "server_url": url, "server_name": cloud_auth.server_name(url) if url else "",
            **{k: row[k] for k in ("kind", "reason", "details", "content", "author", "title", "page_id",
                                   "comment_id", "link", "reporter")}}
    headers, subject = _auth(row, cfg)
    try:
        try:
            cloud_auth._http(cfg["issuer"] + "/api/reports", data=json.dumps(body).encode(), timeout=SEND_TIMEOUT,
                             headers={"Content-Type": "application/json", **headers})
        except CloudAuthError as e:
            if e.status != 401 or not subject:
                raise
            cloud_auth.forget_access(subject)  # the reporter's token died: send it as the server does
            headers = _auth({**row, "reporter_id": ""}, cfg)[0]
            cloud_auth._http(cfg["issuer"] + "/api/reports", data=json.dumps(body).encode(), timeout=SEND_TIMEOUT,
                             headers={"Content-Type": "application/json", **headers})
    except CloudAuthError as e:
        log.warning(f"[reports] Gamma Cloud did not take report {report_id}: {e}")
        with connect_users_db() as conn:
            conn.execute("UPDATE content_reports SET send_error = ? WHERE id = ?", (str(e)[:300], report_id))
        return False
    with connect_users_db() as conn:
        conn.execute("UPDATE content_reports SET sent_at = ?, send_error = '' WHERE id = ?", (page_now(), report_id))
    return True


def tick() -> dict:
    """The hourly round: reports older than ``KEEP_DAYS`` go, and those of
    the last ``SEND_DAYS`` Gamma Cloud has not taken are sent again."""
    now = datetime.now(timezone.utc)
    with connect_users_db() as conn:
        dropped = conn.execute("DELETE FROM content_reports WHERE created_at < ?",
                               (format_stamp(now - timedelta(days=KEEP_DAYS)),)).rowcount
        unsent = [r[0] for r in conn.execute(
            "SELECT id FROM content_reports WHERE sent_at = '' AND created_at >= ? ORDER BY created_at",
            (format_stamp(now - timedelta(days=SEND_DAYS)),))] if forwards() else []
    sent = sum(send(rid) for rid in unsent)
    return {"dropped": dropped, "sent": sent, "unsent": len(unsent) - sent}


# --- this server's admins -----------------------------------------------------------

def listing(state: str = "all") -> dict:
    """``{reports, open, forwards}``: the reports newest first (``state``
    open, resolved or all), how many are open, and whether this server
    sends them on to Gamma Cloud."""
    if state not in (*STATES, "all"):
        raise HTTPException(400, "state must be open, resolved or all")
    where, args = ("", ()) if state == "all" else ("WHERE state = ?", (state,))
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM content_reports {where} ORDER BY created_at DESC LIMIT 500",
                            args).fetchall()
        count = conn.execute("SELECT COUNT(*) FROM content_reports WHERE state = 'open'").fetchone()[0]
    return {"reports": [dict(zip(_COLUMNS.split(", "), r)) for r in rows], "open": count, "forwards": forwards()}


def set_state(report_id: str, state: str, by: str) -> dict:
    if state not in STATES:
        raise HTTPException(400, "state must be open or resolved")
    resolved = state == "resolved"
    with connect_users_db() as conn:
        if not conn.execute("UPDATE content_reports SET state = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
                            (state, page_now() if resolved else "", by if resolved else "", report_id)).rowcount:
            raise HTTPException(404, "no such report")
    return _get(report_id)


def delete(report_id: str) -> None:
    with connect_users_db() as conn:
        if not conn.execute("DELETE FROM content_reports WHERE id = ?", (report_id,)).rowcount:
            raise HTTPException(404, "no such report")


def waiting(admin_id: str) -> tuple[int, str]:
    """(count, newest id) of the open reports someone other than
    ``admin_id`` made, for the admins' notice."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT COUNT(*), MAX(created_at || id) FROM content_reports "
                           "WHERE state = 'open' AND reporter_id != ?", (admin_id,)).fetchone()
    return row[0], row[1] or ""

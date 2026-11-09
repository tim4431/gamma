"""Reports that reach the operator (docs/dev/cloud.md "Reports"): a
comment, a shared or published page, or an AI reply that someone on a Gamma
server reported from its menu. The Gamma server forwards it here
(``POST /api/reports``, ``routers/reports.py``) and the Admin page's Reports
tab lists it; each open report is an alert until it is resolved
(``alerts.py``), so it is mailed to the operator once.

What a report says is the Gamma server's word: the text reported, who wrote
it and the page it is on as that server names them. What this server
checks is who sent it: the reporter's own Gamma Cloud account when the
server sent their access token (``account_id``), the server's sign-in
client when it sent that token or the client's secret (``client_id``), or
neither, a report from a server that signs no one in with Gamma Cloud or
from a visitor with no account. The server's own id for a report
(``report_id``) makes a retry store nothing twice.
"""

import re
from urllib.parse import urlsplit

from . import db
from .accounts import Problem
from .db import audit, new_id, now

KINDS = ("comment", "page", "ai")
REASONS = ("harassment", "sexual", "violence", "spam", "illegal", "privacy", "harmful", "other")
STATES = ("open", "resolved")
KIND_LABEL = {"comment": "a comment", "page": "a page", "ai": "an AI reply"}
REASON_LABEL = {"harassment": "harassment or hate", "sexual": "sexual content", "violence": "violence or threats",
                "spam": "spam or a scam", "illegal": "illegal content or a copyright breach",
                "privacy": "someone's private information", "harmful": "harmful or dangerous content",
                "other": "something else"}
MAX_DETAILS = 2000       # the reporter's own words
MAX_CONTENT = 8000       # the text reported
MAX_NAME = 120           # the author, the reporter, the server's name
MAX_TITLE = 200          # the page it is on
MAX_LINK = 2048
KEEP_DAYS = 180          # a resolved report goes this long after it was resolved
ALERTED = 50             # the newest open reports that are alerts at once
_REF = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _text(value, limit: int, *, lines: bool = False) -> str:
    """``value`` as stored: printable characters (and line breaks where
    ``lines``), runs of blanks folded on one line, cut to ``limit``."""
    raw = str(value or "")
    if lines:
        kept = "".join(c if c.isprintable() or c == "\n" else " " for c in raw.replace("\r\n", "\n"))
        return "\n".join(" ".join(line.split()) for line in kept.split("\n")).strip()[:limit]
    return " ".join("".join(c for c in raw if c.isprintable()).split())[:limit]


def _ref(value, what: str) -> str:
    value = str(value or "")
    if value and not _REF.match(value):
        raise Problem(400, f"{what} is 1 to 64 letters, digits, - and _.")
    return value


def _link(value) -> str:
    """A public address of what was reported (a published page), '' for
    anything that is not an http(s) URL."""
    value = str(value or "").strip()
    try:
        url = urlsplit(value)
    except ValueError:
        return ""
    if len(value) > MAX_LINK or url.scheme not in ("http", "https") or not url.hostname or re.search(r"\s", value):
        return ""
    return value


def check(*, kind, reason, report_id, details="", content="", author="", title="", page_id="", comment_id="",
          link="", reporter="") -> dict:
    """The fields of a report as stored, or ``Problem(400)`` for the first
    that is wrong."""
    if kind not in KINDS:
        raise Problem(400, "kind must be " + ", ".join(KINDS))
    if reason not in REASONS:
        raise Problem(400, "reason must be " + ", ".join(REASONS))
    report_id = _ref(report_id, "report_id")
    if not report_id:
        raise Problem(400, "report_id is required")
    return {"kind": kind, "reason": reason, "report_id": report_id,
            "details": _text(details, MAX_DETAILS, lines=True), "content": _text(content, MAX_CONTENT, lines=True),
            "author": _text(author, MAX_NAME), "title": _text(title, MAX_TITLE),
            "page_id": _ref(page_id, "page_id"), "comment_id": _ref(comment_id, "comment_id"),
            "link": _link(link), "reporter": _text(reporter, MAX_NAME)}


def store(conn, fields: dict, *, server_url: str, server_name: str, account_id: str = "", client_id: str = "",
          ip: str = "") -> tuple[dict, bool]:
    """Keep a report: ``(row, created)``. The same ``report_id`` from the
    same server again is the row stored the first time, unchanged."""
    db.begin_write(conn)
    found = conn.execute("SELECT * FROM reports WHERE server_url = ? AND report_id = ?",
                         (server_url, fields["report_id"])).fetchone()
    if found:
        return dict(found), False
    rid = new_id()
    conn.execute("INSERT INTO reports (id, report_id, server_url, server_name, kind, reason, details, content, author, "
                 "title, page_id, comment_id, link, reporter, account_id, client_id, ip, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                 (rid, fields["report_id"], server_url, _text(server_name, MAX_NAME), fields["kind"], fields["reason"],
                  fields["details"], fields["content"], fields["author"], fields["title"], fields["page_id"],
                  fields["comment_id"], fields["link"], fields["reporter"], account_id, client_id, ip[:64], now()))
    return dict(conn.execute("SELECT * FROM reports WHERE id = ?", (rid,)).fetchone()), True


def _public(row) -> dict:
    out = dict(row)
    out.pop("ip", None)
    return out


def listing(conn, state: str = "open", limit: int = 200) -> list[dict]:
    """The reports in ``state`` (``open``, ``resolved`` or ``all``), newest
    first, with the reporter's username where an account sent it."""
    if state not in (*STATES, "all"):
        raise Problem(400, "state must be open, resolved or all")
    where, args = ("", ()) if state == "all" else ("WHERE r.state = ?", (state,))
    rows = conn.execute(f"SELECT r.*, a.username AS account_username FROM reports r "
                        f"LEFT JOIN accounts a ON a.id = r.account_id {where} "
                        f"ORDER BY r.created_at DESC, r.id LIMIT ?", (*args, max(1, min(limit, 1000)))).fetchall()
    return [{**_public(r), "account_username": r["account_username"] or ""} for r in rows]


def count_open(conn) -> int:
    return conn.execute("SELECT COUNT(*) FROM reports WHERE state = 'open'").fetchone()[0]


def set_state(conn, report_id: str, state: str, actor: str) -> dict:
    """Resolve a report or open it again; ``LookupError`` for no such
    report."""
    if state not in STATES:
        raise Problem(400, "state must be open or resolved")
    row = conn.execute("SELECT state FROM reports WHERE id = ?", (report_id,)).fetchone()
    if not row:
        raise LookupError(report_id)
    if row["state"] != state:
        resolved = state == "resolved"
        conn.execute("UPDATE reports SET state = ?, resolved_at = ?, resolved_by = ? WHERE id = ?",
                     (state, now() if resolved else None, actor if resolved else "", report_id))
        audit(conn, "report.resolve" if resolved else "report.reopen", actor=actor, detail=report_id)
    return _public(conn.execute("SELECT * FROM reports WHERE id = ?", (report_id,)).fetchone())


def delete(conn, report_id: str, actor: str) -> bool:
    if not conn.execute("DELETE FROM reports WHERE id = ?", (report_id,)).rowcount:
        return False
    audit(conn, "report.delete", actor=actor, detail=report_id)
    return True


def alert_rows(conn) -> list[dict]:
    """The newest ``ALERTED`` open reports, for the alerts (``alerts.py``)."""
    return [dict(r) for r in conn.execute("SELECT id, kind, reason, server_url, server_name FROM reports "
                                          "WHERE state = 'open' ORDER BY created_at DESC LIMIT ?", (ALERTED,))]


def summary(row) -> str:
    """One line for an alert: what was reported, where and why."""
    where = row["server_name"] or urlsplit(row["server_url"]).netloc or "a Gamma server"
    what, why = KIND_LABEL.get(row["kind"], row["kind"]), REASON_LABEL.get(row["reason"], row["reason"])
    return f"Report of {what} on {where}: {why}"


def purge(conn, days: int = KEEP_DAYS) -> None:
    """Resolved reports older than ``days`` go (the hourly purge)."""
    conn.execute("DELETE FROM reports WHERE state = 'resolved' AND resolved_at < ?", (db.after(-days * 86400),))


def purge_account(conn, account_id: str) -> None:
    """A purged account's reports stay, without the account they came from."""
    conn.execute("UPDATE reports SET account_id = '' WHERE account_id = ?", (account_id,))

"""Requests to join this server from people who sign in with Gamma Cloud
(docs/dev/cloud_accounts.md "Requests to join").

Under the ``invited`` sign-in policy (gamma/cloud_auth.py) a Gamma Cloud
account with no account here gets one only when something admits it: an
invitation waiting for it, or its request to join approved. Anyone else's
sign-in is refused (``cloud_auth.NotInvited``), and that refusal asks to
join: ``ask`` keeps one row per cloud subject in ``join_requests``, with the
username and e-mail address it signed in with and the share link it came
from, if any (the sign-in's ``next``). The browser goes back to that page
with ``?cloud_join=`` saying what became of it.

Who decides:

- the people who manage that share (its workspace's editors and owners;
  whoever made it and the workspace's owners hear of it in their Inbox, a
  ``join_request`` item from ``cloud:<subject>``). Approve makes it a
  waiting invitation of the share (gamma/shares.py ``invite_cloud`` by
  subject), which admits the person at their next sign-in and opens the
  page for them; Decline marks it declined;
- an admin, for any request (Settings → Server → Sign-in, behind the
  ``join-requests`` notice). Approve admits the person (``approved``: their
  next sign-in gets them an account, and whatever is shared with every
  signed-in account), Decline marks it declined, Remove deletes the row.

A declined person's sign-ins ask nothing more until an admin removes the
row. The row goes when the person's sign-in is admitted (``joined``),
however that came about.
"""

import json
from urllib.parse import parse_qsl, urlsplit

from .auth import share_lookup
from .db import account_names, connect_users_db, page_now
from .logbuf import log

STATUSES = ("pending", "approved", "declined")
MAX_PENDING = 200          # requests waiting at once; past it a refused sign-in asks nothing
ACTOR_PREFIX = "cloud:"    # the actor of a ``join_request`` Inbox item: the asking cloud subject
# What a sign-in the desktop app started is told (a browser gets ``?cloud_join=`` and words it itself).
MESSAGES = {
    "sent": "Asked to join this server. Sign in again once your request is approved.",
    "waiting": "Your request to join this server is still waiting. Sign in again once it is approved.",
    "declined": "Your request to join this server was declined.",
}


def share_of(next_path: str) -> dict | None:
    """The share a sign-in's ``next`` path opens (``?share=<token>``), if
    that share exists."""
    token = dict(parse_qsl(urlsplit(next_path or "").query)).get("share", "")
    return share_lookup(token) if token else None


def ask(claims: dict, next_path: str) -> str:
    """The request to join of a sign-in the ``invited`` policy refused —
    ``claims``, its verified ID token's — that was going on to
    ``next_path``. Returns what became of it: ``sent`` (a new request),
    ``waiting`` (one waits already; it moves to this sign-in's share and
    time), ``declined`` (it asks nothing), or "" when ``MAX_PENDING``
    requests wait already. A new request, or one that moved to another
    share, is told to that share's managers."""
    subject = claims["sub"]
    username = str(claims.get("preferred_username") or "").lower()
    email = str(claims.get("email") or "")
    share = share_of(next_path)
    token = share["token"] if share else ""
    now = page_now()
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute("SELECT status, share_token FROM join_requests WHERE subject = ?", (subject,)).fetchone()
        if row and row[0] == "declined":
            conn.rollback()
            return "declined"
        if row:
            conn.execute("UPDATE join_requests SET username = ?, email = ?, share_token = ?, updated_at = ? "
                         "WHERE subject = ?", (username, email, token or row[1], now, subject))
            state, moved = "waiting", bool(token) and token != row[1]
        else:
            if conn.execute("SELECT COUNT(*) FROM join_requests WHERE status = 'pending'").fetchone()[0] >= MAX_PENDING:
                conn.rollback()
                return ""
            conn.execute("INSERT INTO join_requests (subject, username, email, share_token, status, created_at, "
                         "updated_at) VALUES (?, ?, ?, ?, 'pending', ?, ?)", (subject, username, email, token, now, now))
            state, moved = "sent", bool(token)
        conn.commit()
    log.info(f"[join] {username} asks to join this server" + (" through a share" if token else "")
             + (" (again)" if state == "waiting" else ""))
    if moved:
        _tell(share, subject, username)
    return state


def _tell(share: dict, subject: str, username: str) -> None:
    """A ``join_request`` Inbox item for the people who manage ``share``
    and should hear of it: whoever made it and the workspace's owners,
    while they edit there (gamma/inbox.py; never raises)."""
    from . import inbox, workspaces  # local: both import modules that import this one

    ws = share["workspace_id"]
    with connect_users_db() as conn:
        owners = [r[0] for r in conn.execute(
            "SELECT user_id FROM workspace_members WHERE workspace_id = ? AND role = 'owner'", (ws,))]
    managers = [u for u in dict.fromkeys([share["created_by"], *owners])
                if workspaces.role_of(ws, u) in ("editor", "owner")]
    inbox.emit("join_request", managers, ACTOR_PREFIX + subject, ws, page_id=share["page_id"],
               block_id=share["folder"], snippet=username)


def for_share(token: str) -> list[dict]:
    """The requests waiting that came through the share ``token``, oldest
    first: ``[{subject, username, email, created_at, updated_at}]``."""
    with connect_users_db() as conn:
        rows = conn.execute("SELECT subject, username, email, created_at, updated_at FROM join_requests "
                            "WHERE share_token = ? AND status = 'pending' ORDER BY created_at", (token,)).fetchall()
    return [{"subject": r[0], "username": r[1], "email": r[2], "created_at": r[3], "updated_at": r[4]} for r in rows]


def decline(token: str, subject: str, by: str) -> None:
    """The request of ``subject`` that came through the share ``token`` is
    declined by ``by`` (an id), one of the share's managers. ValueError when
    no such request waits."""
    with connect_users_db() as conn:
        done = conn.execute("UPDATE join_requests SET status = 'declined', decided_by = ?, updated_at = ? "
                            "WHERE subject = ? AND share_token = ? AND status = 'pending'",
                            (by, page_now(), subject, token)).rowcount
        conn.commit()
    if not done:
        raise ValueError("no such request")


def listing() -> list[dict]:
    """Every request, for the admins: the waiting ones first, newest first:
    ``[{subject, username, email, status, from_share, shared_by,
    decided_by, created_at, updated_at}]``. ``from_share``: it came through
    a share link, not the login page; ``shared_by`` is the username of
    whoever made that share ("" once it is gone) — the page itself an admin
    may not read; ``decided_by`` the username of who approved or declined
    it."""
    with connect_users_db() as conn:
        rows = conn.execute("SELECT subject, username, email, share_token, status, decided_by, created_at, updated_at "
                            "FROM join_requests ORDER BY status != 'pending', updated_at DESC").fetchall()
    shares = {t: share_lookup(t) for t in {r[3] for r in rows if r[3]}}
    with connect_users_db() as conn:
        names = account_names(conn, {r[5] for r in rows} | {s["created_by"] for s in shares.values() if s})
    out = []
    for subject, username, email, token, status, decided_by, created_at, updated_at in rows:
        share = shares.get(token)
        out.append({"subject": subject, "username": username, "email": email, "status": status,
                    "from_share": bool(token), "shared_by": names.get(share["created_by"], "") if share else "",
                    "decided_by": names.get(decided_by, ""), "created_at": created_at, "updated_at": updated_at})
    return out


def decide(subject: str, status: str, by: str) -> None:
    """An admin (``by``, an id) approves or declines the request of
    ``subject``, whatever it was. ValueError for another status, or when
    there is no such request."""
    if status not in ("approved", "declined"):
        raise ValueError("status must be approved or declined")
    with connect_users_db() as conn:
        done = conn.execute("UPDATE join_requests SET status = ?, decided_by = ?, updated_at = ? WHERE subject = ?",
                            (status, by, page_now(), subject)).rowcount
        conn.commit()
    if not done:
        raise ValueError("no such request")


def remove(subject: str) -> None:
    """An admin removes the request of ``subject``: a declined person may
    ask again, an approval not used yet is withdrawn. ValueError when there
    is no such request."""
    with connect_users_db() as conn:
        done = conn.execute("DELETE FROM join_requests WHERE subject = ?", (subject,)).rowcount
        conn.commit()
    if not done:
        raise ValueError("no such request")


def joined(subject: str) -> None:
    """``subject``'s sign-in was admitted: its request, answered, goes."""
    with connect_users_db() as conn:
        conn.execute("DELETE FROM join_requests WHERE subject = ?", (subject,))
        conn.commit()


def approved(conn, subject: str) -> bool:
    """Whether an admin approved the request of ``subject`` (the ``invited``
    sign-in policy admits it). ``conn``: an open users.db connection."""
    return conn.execute("SELECT 1 FROM join_requests WHERE subject = ? AND status = 'approved'",
                        (subject,)).fetchone() is not None


def asking(subjects) -> dict[str, tuple[str, str, str]]:
    """``{subject: (workspace id, page id, folder id)}``: what the waiting
    requests among ``subjects`` ask to open, through a share that still
    exists (the Inbox settles a ``join_request`` item whose request is no
    longer this)."""
    wanted = sorted(set(subjects))
    if not wanted:
        return {}
    with connect_users_db() as conn:
        rows = conn.execute("SELECT subject, share_token FROM join_requests WHERE status = 'pending' "
                            "AND share_token != '' AND subject IN (SELECT value FROM json_each(?))",
                            (json.dumps(wanted),)).fetchall()
    out = {}
    for subject, token in rows:
        share = share_lookup(token)
        if share:
            out[subject] = (share["workspace_id"], share["page_id"], share["folder"])
    return out


def waiting() -> tuple[int, str]:
    """How many requests wait, and when the newest of them was made ("" for
    none): the admins' notice and its fingerprint."""
    with connect_users_db() as conn:
        count, newest = conn.execute("SELECT COUNT(*), MAX(created_at) FROM join_requests "
                                     "WHERE status = 'pending'").fetchone()
    return count, newest or ""

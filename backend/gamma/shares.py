"""A share's invitations by Gamma Cloud username, and the Inbox item that
tells someone a page or folder was shared with them.

A share (gamma/routers/shares.py, docs/dev/api.md "Shares") invites this
server's accounts by username (``share_users``). On a server that signs
people in with Gamma Cloud it can also invite someone who has no account
here yet, by their Gamma Cloud username (``invite_cloud``): the share's twin
of a shared workspace's pending membership (gamma/workspaces.py). The
account server answers username → subject (gamma/cloud_auth.py
``lookup_username``), and the invitation waits in ``pending_share_users``,
keyed by the share's token and that subject, until the person's first cloud
sign-in creates or links their account here (``claim_pending``, called by
``cloud_auth.resolve_account``); it then becomes a ``share_users`` row with
its role. A waiting invitation is also what admits a newcomer under the
``invited`` sign-in policy (``waiting``), and approving a request to join
that came through the share (gamma/join_requests.py) makes one. The rows go
with their share (``db.delete_shares``). A share's invitations by e-mail
address are gamma/email_invites.py's.
"""

import sqlite3

from . import inbox
from .auth import ShareScope, share_lookup
from .db import account_name, connect_pages_db, connect_users_db, page_now
from .logbuf import log

ROLES = ("view", "edit")
MAX_PENDING_PER_SHARE = 100


def invite_cloud(share: dict, by: str, *, role: str = "view", name: str = "", subject: str = "") -> dict:
    """Invite a Gamma Cloud account to ``share`` (``auth.share_lookup``'s
    dict) with ``role``, on behalf of the account ``by`` (an id). The person
    is named by ``name``, their cloud username, which the account server
    looks up on ``by``'s own grant; or by ``subject``, with no lookup:
    someone the share invited already (the role changes) or whose request
    to join came through it and waits (it is approved).

    When their cloud identity is linked to an account here, that account is
    invited at once, as by username: ``{"user": <its username>, "username":
    <the cloud username>}``. Otherwise the invitation waits for their first
    sign-in: ``{"pending": {subject, username, role, invited_by,
    created_at}}``; inviting again changes its role. Either way their
    request to join through this share, if any, is answered and goes.

    ValueError for a bad role or username, no such cloud account, ``by``'s
    own cloud account, a subject the share does not know, a share stopped
    meanwhile or one with too many invitations waiting;
    cloud_auth.CloudLookupError when the account server cannot be asked."""
    from . import cloud_auth  # local: cloud_auth imports this module

    if role not in ROLES:
        raise ValueError("a person's role must be view or edit")
    token = share["token"]
    if subject:
        username = _known(token, subject)
        if not username:
            raise ValueError("no such invitation or request")
    else:
        name = cloud_auth.clean_username(name)
        found = cloud_auth.lookup_username(name, by=by)
        if not found:
            raise ValueError(f"no Gamma Cloud account is named {name}")
        subject, username = found["sub"], found["username"]
    now = page_now()
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        if not conn.execute("SELECT 1 FROM shares WHERE token = ?", (token,)).fetchone():
            raise ValueError("it is not shared any more")
        linked = conn.execute(
            "SELECT i.user_id, u.username FROM identities i JOIN users u ON u.id = i.user_id "
            "WHERE i.provider = ? AND i.subject = ? AND u.is_guest = 0", (cloud_auth.PROVIDER, subject)).fetchone()
        if linked and linked[0] == by:
            raise ValueError("that is your own Gamma Cloud account")
        if not linked and conn.execute(
                "SELECT COUNT(*) FROM pending_share_users WHERE token = ? AND subject != ?",
                (token, subject)).fetchone()[0] >= MAX_PENDING_PER_SHARE:
            raise ValueError("too many invitations are waiting on this share")
        conn.execute("DELETE FROM join_requests WHERE subject = ? AND share_token = ?", (subject, token))
        if linked:
            user_id, local = linked
            had = conn.execute("SELECT 1 FROM share_users WHERE token = ? AND user_id = ?",
                               (token, user_id)).fetchone()
            conn.execute("INSERT INTO share_users (token, user_id, role, added_at) VALUES (?, ?, ?, ?) "
                         "ON CONFLICT(token, user_id) DO UPDATE SET role = excluded.role", (token, user_id, role, now))
            conn.execute("DELETE FROM pending_share_users WHERE token = ? AND subject = ?", (token, subject))
        else:
            conn.execute(
                "INSERT INTO pending_share_users (token, subject, username, role, invited_by, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(token, subject) DO UPDATE SET "
                "username = excluded.username, role = excluded.role, invited_by = excluded.invited_by",
                (token, subject, username, role, by, now))
            inviter = account_name(conn, by)
        conn.commit()
    if linked:
        if not had:
            tell(share["workspace_id"], ShareScope.of(share), token, by, [user_id])
        return {"user": local, "username": username}
    return {"pending": {"subject": subject, "username": username, "role": role, "invited_by": inviter,
                        "created_at": now}}


def _known(token: str, subject: str) -> str:
    """The cloud username ``subject`` goes by on the share ``token``: its
    waiting invitation's, else its waiting request to join through the
    share's; "" for neither."""
    with connect_users_db() as conn:
        row = (conn.execute("SELECT username FROM pending_share_users WHERE token = ? AND subject = ?",
                            (token, subject)).fetchone()
               or conn.execute("SELECT username FROM join_requests WHERE subject = ? AND share_token = ? "
                               "AND status = 'pending'", (subject, token)).fetchone())
    return row[0] if row else ""


def pending(token: str) -> list[dict]:
    """The share's invitations waiting for a first cloud sign-in, by
    username: ``[{subject, username, role, invited_by, created_at}]`` —
    the cloud username invited, and the inviter's username here."""
    with connect_users_db() as conn:
        rows = conn.execute(
            "SELECT p.subject, p.username, p.role, COALESCE(u.username, ''), p.created_at FROM pending_share_users p "
            "LEFT JOIN users u ON u.id = p.invited_by WHERE p.token = ? ORDER BY p.username", (token,)).fetchall()
    return [{"subject": r[0], "username": r[1], "role": r[2], "invited_by": r[3], "created_at": r[4]} for r in rows]


def cancel(token: str, subject: str) -> None:
    """Withdraw the share's invitation waiting for ``subject``; ValueError
    when there is none."""
    with connect_users_db() as conn:
        gone = conn.execute("DELETE FROM pending_share_users WHERE token = ? AND subject = ?",
                            (token, subject)).rowcount
        conn.commit()
    if not gone:
        raise ValueError("no such invitation")


def waiting(conn, subject: str) -> bool:
    """Whether a share's invitation waits for ``subject`` here (the
    ``invited`` sign-in policy admits it). ``conn``: an open users.db
    connection."""
    return conn.execute("SELECT 1 FROM pending_share_users p JOIN shares s ON s.token = p.token "
                        "WHERE p.subject = ? LIMIT 1", (subject,)).fetchone() is not None


def claim_pending(user_id: str, subject: str) -> list[str]:
    """The cloud identity ``subject`` now signs in as the account ``user_id``
    (created, claimed or linked): every share invitation waiting for it
    becomes a ``share_users`` row with its role, and the waiting rows go.
    An invitation the account holds already keeps its own role. Each share
    joined tells the account in its Inbox, from the inviter. Returns the
    shares' tokens. Cheap when nothing waits — it runs on every cloud
    sign-in."""
    with connect_users_db() as conn:
        rows = conn.execute("SELECT p.token, p.role, p.invited_by FROM pending_share_users p "
                            "JOIN shares s ON s.token = p.token WHERE p.subject = ?", (subject,)).fetchall()
        if not rows:
            return []
        account = conn.execute("SELECT is_guest, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not account or account[0]:
            return []
        now = page_now()
        joined = []
        for token, role, by in rows:
            if role in ROLES and conn.execute(
                    "INSERT INTO share_users (token, user_id, role, added_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(token, user_id) DO NOTHING", (token, user_id, role, now)).rowcount:
                joined.append((token, by))
        conn.execute("DELETE FROM pending_share_users WHERE subject = ?", (subject,))
        conn.commit()
    for token, by in joined:
        share = share_lookup(token)
        if share:
            tell(share["workspace_id"], ShareScope.of(share), token, by, [user_id])
    if joined:
        log.info(f"[shares] {account[1]} joined {len(joined)} share(s) they were invited to by cloud username")
    return [token for token, _ in joined]


def target_name(ws: str, target: ShareScope) -> str:
    """The shared page's title (the first line of its content) or the shared
    folder's name; "" when it cannot be read, which is logged."""
    try:
        with connect_pages_db(ws) as conn:
            row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?",
                               (target.page or target.folder,)).fetchone()
    except (sqlite3.Error, ValueError) as e:
        log.warning(f"[shares] the name of shared {target.page or target.folder} in workspace {ws} "
                    f"could not be read: {e}")
        return ""
    return (row[0] or "").split("\n", 1)[0] if row else ""


def tell(ws: str, target: ShareScope, token: str, actor: str, invited) -> None:
    """A ``shared`` Inbox item for each account of ``invited`` (ids), from
    ``actor``, with the share's ``token`` as their way in (gamma/inbox.py;
    never raises). The snippet is the page's title or the folder's name
    (``target_name``); the items go out without one that cannot be read."""
    if not invited:
        return
    inbox.emit("shared", invited, actor, ws, page_id=target.page, block_id=target.folder,
               snippet=target_name(ws, target), via=token)

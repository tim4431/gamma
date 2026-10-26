"""Invitations by e-mail address (docs/dev/cloud_accounts.md): someone is
invited by their address, to this server (``server``, an admin's, from
Settings → Users, for someone who has no account here), to a shared
workspace (``workspace``, its owners') or to a page or folder share
(``share``, its managers'). The Gamma server never sends mail itself. It
keeps the invitation in ``email_invites`` (users.db) and asks the account
server, on the inviter's own Gamma Cloud grant, to mail it
(``cloud_auth.send_invitation``). The mail's link lets the person make a
Gamma Cloud account or sign in with theirs, and comes back through this
server's cloud sign-in with the invitation's link id (``cloud_auth.begin``'s
``invite``).

Only that link admits: the person's own click on the link they were mailed
is the one way an invitation turns into access. The address is never
matched against the accounts here or a sign-in's e-mail, so a workspace's
or a share's invitation is mailed whoever the address belongs to, and its
answer tells the inviter nothing about that (an admin's invitation to this
server alone is refused for an address an account here is linked under).
Someone who has an account here already accepts like anyone else, and a
membership or a share entry they have keeps its role.

The link id is a secret of its own, apart from the ``id`` the lists show
and the people who manage the invitation name it by: whoever holds it is
admitted with the invitation's role, so only the account server is told it
(as its ``invite_id``), and no entry, answer or log line carries it. The
entries themselves, addresses being personal data, go only to the people
who manage them (the routers): a workspace's owners and the admins, a
share's managers signed in with a session, and the admins those to this
server.

The link admits the sign-in (``admits``, from ``cloud_auth._resolve``): a
``server`` invitation under every sign-in policy, a workspace's or a
share's only under the policies that take new accounts from members'
invitations (``MEMBER_POLICIES``); under ``refuse`` and ``claim`` only an
admin brings someone new in. Once the account is known, the claim
(``claim``, from ``cloud_auth.resolve_account``) turns the invitation into
a membership or a share entry with the usual Inbox item, and the row goes.

Inviting an address to the same target again keeps the invitation's id and
link id, changes its role and mails it again (the account server replaces
its link). Invitations expire after ``TTL_DAYS`` on both sides (``sweep``,
hourly) and go with their workspace (deleted, or made personal) or their
share (``db.delete_shares``). A row withdrawn, claimed or gone with its
target has its link dropped at the account server too (``cancel_links``),
so the mail leads to its "not valid any more" page. The account server
keeps a link under this server's address the mail went out with, which a
server without a confirmed public URL takes from the request (the
inviter's origin), so the row records it (``server_url``) and the link is
dropped there.

Only where a mailed link can reach this server (``available``).
"""

import re
import secrets
import threading
from datetime import datetime, timedelta, timezone

from . import shares, workspaces
from .auth import ShareScope, share_lookup
from .db import account_name, account_names, connect_users_db, delete_email_invites, format_stamp, page_now
from .logbuf import log

TTL_DAYS = 14
SWEEP_INTERVAL_S = 3600
MAX_PER_TARGET = 100  # a workspace's or a share's e-mail invitations waiting at once
MAX_SERVER = 200      # the server's own
SENDS_PER_HOUR = 20   # invitations one account sends or resends by e-mail (the routers' rate limit)
KINDS = ("server", "workspace", "share")
# The sign-in policies (cloud_auth.POLICIES) under which a workspace's or a
# share's invitation admits a newcomer; a server invitation admits under all.
MEMBER_POLICIES = ("provision", "invited")
# An invitation's link id as the mailed link carries it back (cloud_auth.begin
# keeps only one of this shape).
LINK_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
MAX_EMAIL_LEN = 254
_EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")
# What each kind may grant: a workspace's editor or viewer, a share's view or edit, nothing for the server.
_ROLES = {"server": ("",), "workspace": workspaces.INVITE_ROLES, "share": shares.ROLES}
_GONE = {"workspace": "the workspace is not shared any more", "share": "it is not shared any more"}
_FULL = "Too many invitations are waiting here. Withdraw some first."
_TARGET = "kind = ? AND workspace_id = ? AND share_token = ?"
# What an entry shows: never the link id, which admits whoever holds it.
_COLUMNS = "id, email, role, invited_by, created_at, mailed_at, expires_at"


class NoSuchInvitation(ValueError):
    """No invitation of that id waits for that target."""

    def __init__(self):
        super().__init__("no such invitation")


def available() -> dict:
    """Whether invitations by e-mail can go out from here, worked out with
    no network: ``{server, members, reason}``. ``server`` (an admin's
    invitation to this server) needs cloud sign-in on, this server
    connected, and a client of its own: a server signing in through the
    desktop app's public client sits on one computer, where a mailed link
    could not reach it. ``members`` (a workspace's or a share's) needs that
    and a policy that takes new accounts from invitations. ``reason`` says
    what stops the first of the two that is false, in words a person can
    act on; "" when both are true."""
    from . import cloud_auth  # local: cloud_auth imports this module

    cfg = cloud_auth.settings()
    if not cfg["enabled"]:
        reason = "Gamma Cloud sign-in is off on this server."
    elif cloud_auth.needs_connect():
        reason = "This server is not connected to Gamma Cloud yet."
    elif cfg["client_id"] == cloud_auth.DEFAULT_CLIENT_ID:
        reason = "This server signs in through the desktop app's client, so a mailed link could not reach it."
    else:
        reason = ""
    server = not reason
    members = server and cfg["policy"] in MEMBER_POLICIES
    if server and not members:
        reason = ("While this server refuses new Gamma Cloud accounts, only an admin invites by e-mail, "
                  "from Settings → Users.")
    return {"server": server, "members": members, "reason": reason}


def clean_email(raw) -> str:
    """An e-mail address as typed, trimmed: one ``@``, a dot in the domain,
    no whitespace, at most ``MAX_EMAIL_LEN`` characters; else ValueError."""
    email = str(raw or "").strip()
    if len(email) > MAX_EMAIL_LEN or not _EMAIL_RE.fullmatch(email):
        raise ValueError("That is not an e-mail address.")
    return email


def _check(kind: str, role: str) -> None:
    if kind not in KINDS:
        raise ValueError("an invitation is to this server, a workspace or a share")
    if role not in _ROLES[kind]:
        raise ValueError("an invitation to this server grants no role" if kind == "server"
                         else f"the role must be {' or '.join(_ROLES[kind])}")


def _open_for(kind: str) -> None:
    """ValueError, with the reason, unless ``kind`` can be sent from here."""
    open_ = available()
    if not open_["server" if kind == "server" else "members"]:
        raise ValueError(open_["reason"])


def _target(kind: str, ws: str, share: dict | None) -> tuple[str, str]:
    """The row's (workspace_id, share_token) for a target: the workspace's
    id for a workspace, the share's token for a share, ('', '') for the
    server."""
    if kind == "workspace":
        return ws, ""
    if kind == "share":
        return "", share["token"]
    return "", ""


def _live(conn, kind: str, ws: str, token: str) -> bool:
    """Whether an invitation's target is still there: a workspace still
    shared, a share not stopped (the server always is)."""
    if kind == "workspace":
        return conn.execute("SELECT 1 FROM workspaces WHERE id = ? AND kind = 'shared'",
                            (ws,)).fetchone() is not None
    if kind == "share":
        return conn.execute("SELECT 1 FROM shares WHERE token = ?", (token,)).fetchone() is not None
    return True


def _full(conn, kind: str, ws: str, token: str, norm: str) -> bool:
    """Whether as many invitations wait for the target as it may hold
    (``MAX_PER_TARGET``, ``MAX_SERVER`` for the server's), not counting the
    address ``norm``'s."""
    waiting = conn.execute(f"SELECT COUNT(*) FROM email_invites WHERE {_TARGET} AND email_norm != ? "
                           "AND expires_at > ?", (kind, ws, token, norm, page_now())).fetchone()[0]
    return waiting >= (MAX_SERVER if kind == "server" else MAX_PER_TARGET)


def _told(kind: str, ws: str, share: dict | None) -> tuple[str, str, str]:
    """What the account server is told of the target, for the mail and the
    link: (its kind there, what it is called, the path here to land on)."""
    if kind == "workspace":
        info = workspaces.get(ws)
        return "workspace", info["name"] if info else "", f"/?ws={ws}"
    if kind == "share":
        target = ShareScope.of(share)
        name = shares.target_name(share["workspace_id"], target)[:200]
        return ("page" if target.page else "folder"), name, f"/?share={share['token']}"
    return "server", "", "/"


def _expiry() -> str:
    return format_stamp(datetime.now(timezone.utc) + timedelta(days=TTL_DAYS))


def _entries(conn, rows) -> list[dict]:
    """Rows of ``_COLUMNS`` as the lists show them, the inviter by username
    ("" once that account is gone)."""
    names = account_names(conn, [r[3] for r in rows])
    return [{"id": r[0], "email": r[1], "role": r[2], "invited_by": names.get(r[3], ""), "created_at": r[4],
             "mailed_at": r[5], "expires_at": r[6]} for r in rows]


def _entry(conn, invite_id: str) -> dict:
    rows = conn.execute(f"SELECT {_COLUMNS} FROM email_invites WHERE id = ?", (invite_id,)).fetchall()
    return _entries(conn, rows)[0]


def _mail(by: str, *, server_url: str, email: str, kind: str, ws: str, share: dict | None, link_id: str) -> str:
    """Ask the account server, on ``by``'s grant, to mail ``email`` the
    invitation of ``kind`` to the workspace ``ws`` or the share ``share``
    whose link id is ``link_id``, at the address ``server_url``; the kind
    the account server was told. A call that got no answer may have mailed
    it all the same: its link is then withdrawn unless a row here carries
    it at that address (a first send stored nothing, a row withdrawn while
    the call hung is gone, and one whose mail went out at another address
    leaves this copy unrecorded), since a row that does admits through
    that link whichever of the account server's rows for it survived."""
    from . import cloud_auth  # local: cloud_auth imports this module

    told, what, next_path = _told(kind, ws, share)
    try:
        cloud_auth.send_invitation(by, server_url=server_url, email=email, kind=told, what=what, invite_id=link_id,
                                   next=next_path)
    except cloud_auth.InvitationError as e:
        if e.unanswered:
            with connect_users_db() as conn:
                # a row that recorded no address has its link dropped at the caller's: it counts as this one's
                carried = conn.execute("SELECT 1 FROM email_invites WHERE link_id = ? AND server_url IN ('', ?)",
                                       (link_id, server_url)).fetchone()
            if not carried:
                cancel_links([(link_id, by, server_url)])
        raise
    return told


def invite(kind: str, email, by: str, *, server_url: str, ws: str = "", share: dict | None = None,
           role: str = "") -> dict:
    """Invite the address ``email`` to the target of ``kind`` — the
    workspace ``ws``, the share ``share`` (``auth.share_lookup``'s dict) or
    this server — with ``role``, on behalf of the account ``by`` (an id).
    ``server_url`` is this server's address for the mailed link
    (``cloud_auth.callback_base``), which the row records.

    The account server mails it and the invitation waits for its link:
    ``{"pending": entry}``, whoever the address belongs to. Only a server
    invitation, an admin's, is refused for an address an account here is
    linked under, whose owner signs in already. Inviting the address to
    the target again keeps the id and the link id, changes the role and
    mails it again; a mail before that went out at another address has
    its link dropped there.

    ValueError for a bad kind, role or address, invitations by e-mail not
    available here (the reason), a target gone or with too many invitations
    waiting; ``cloud_auth.InvitationError`` when the account server does
    not mail it."""
    from . import cloud_auth  # local: cloud_auth imports this module

    _check(kind, role)
    email = clean_email(email)
    norm = email.lower()
    _open_for(kind)
    ws, token = _target(kind, ws, share)
    with connect_users_db() as conn:
        if not _live(conn, kind, ws, token):
            raise ValueError(_GONE[kind])
        if kind == "server":
            linked = conn.execute(
                "SELECT u.username FROM identities i JOIN users u ON u.id = i.user_id "
                "WHERE i.provider = ? AND LOWER(i.email) = ? AND u.is_guest = 0 ORDER BY i.last_login_at DESC LIMIT 1",
                (cloud_auth.PROVIDER, norm)).fetchone()
            if linked:
                raise ValueError(f"{email} already has an account here: {linked[0]}.")
        row = conn.execute(f"SELECT id, link_id FROM email_invites WHERE {_TARGET} AND email_norm = ?",
                           (kind, ws, token, norm)).fetchone()
        if _full(conn, kind, ws, token, norm):
            raise ValueError(_FULL)

    invite_id, link_id = row if row else (secrets.token_urlsafe(16), secrets.token_urlsafe(24))
    told = _mail(by, server_url=server_url, email=email, kind=kind, ws=ws, share=share, link_id=link_id)
    now = page_now()
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        before = conn.execute(f"SELECT link_id, invited_by, server_url FROM email_invites WHERE {_TARGET} "
                              "AND email_norm = ?", (kind, ws, token, norm)).fetchone()
        # Checked again where the row is written: while the mail went, the target may have been stopped or
        # deleted, or other requests may have filled it with new addresses. The mailed link then goes.
        gone, full = not _live(conn, kind, ws, token), not before and _full(conn, kind, ws, token, norm)
        if gone or full:
            conn.rollback()  # the write lock let go before the account server is asked
            cancel_links([(link_id, by, server_url)])
            raise ValueError(_GONE[kind] if gone else _FULL)
        conn.execute(
            "INSERT INTO email_invites (id, link_id, email, email_norm, kind, workspace_id, share_token, role, "
            "invited_by, server_url, created_at, mailed_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(kind, workspace_id, share_token, email_norm) DO UPDATE SET id = excluded.id, "
            "link_id = excluded.link_id, role = excluded.role, invited_by = excluded.invited_by, "
            "server_url = excluded.server_url, mailed_at = excluded.mailed_at, expires_at = excluded.expires_at",
            (invite_id, link_id, email, norm, kind, ws, token, role, by, server_url, now, now, _expiry()))
        conn.commit()
        entry = _entry(conn, invite_id)
        inviter = account_name(conn, by)
    if before and _superseded(before, link_id, server_url):
        # Another request mailed this address meanwhile, or the mail before went out at another address:
        # the newest link stands, the other one goes.
        cancel_links([before], by=by, server_url=server_url)
    log.info(f"[invites] {inviter} sent an invitation by e-mail ({told})")
    return {"pending": entry}


def resend(invite_id: str, by: str, *, server_url: str, kind: str, ws: str = "", share: dict | None = None) -> dict:
    """Mail the invitation ``invite_id`` of the target again, with the same
    link id, on behalf of ``by``, who becomes its inviter (the mail names
    them); its 14 days start over. The row records ``server_url``, the
    address it went out with, and a mail before that went out at another
    address has its link dropped there. The entry; NoSuchInvitation when
    the target has no invitation of that id, ValueError when invitations
    by e-mail are not available here, ``cloud_auth.InvitationError`` when
    the account server does not mail it (the invitation then stays as it
    was)."""
    _open_for(kind)
    ws, token = _target(kind, ws, share)
    with connect_users_db() as conn:
        row = conn.execute(f"SELECT email, link_id FROM email_invites WHERE id = ? AND {_TARGET}",
                           (invite_id, kind, ws, token)).fetchone()
    if not row:
        raise NoSuchInvitation()
    email, link_id = row
    _mail(by, server_url=server_url, email=email, kind=kind, ws=ws, share=share, link_id=link_id)
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")  # the address before read and the new one written as one step
        before = conn.execute(f"SELECT link_id, invited_by, server_url FROM email_invites WHERE id = ? AND {_TARGET}",
                              (invite_id, kind, ws, token)).fetchone()
        if before:
            conn.execute("UPDATE email_invites SET invited_by = ?, server_url = ?, mailed_at = ?, expires_at = ? "
                         "WHERE id = ?", (by, server_url, page_now(), _expiry(), invite_id))
        conn.commit()
        entry = _entry(conn, invite_id) if before else None
    if not entry:  # withdrawn while the mail went
        cancel_links([(link_id, by, server_url)])
        raise NoSuchInvitation()
    if _superseded(before, link_id, server_url):
        cancel_links([before], by=by, server_url=server_url)
    return entry


def _superseded(row, link_id: str, server_url: str) -> bool:
    """Whether the link a row carried, ``row`` its ``(link_id, invited_by,
    server_url)``, is another one than ``link_id`` just mailed at
    ``server_url``: another link, or the same one kept at the account
    server under another address. A row that recorded no address counts
    as this one's."""
    return (row[0], row[2] or server_url) != (link_id, server_url)


def set_role(invite_id: str, role: str, *, kind: str, ws: str = "", token: str = "") -> dict:
    """Change the role an invitation waiting for the target grants (no
    mail goes). The entry; NoSuchInvitation, or ValueError for a bad role."""
    _check(kind, role)
    with connect_users_db() as conn:
        if not conn.execute(f"UPDATE email_invites SET role = ? WHERE id = ? AND {_TARGET}",
                            (role, invite_id, kind, ws, token)).rowcount:
            raise NoSuchInvitation()
        conn.commit()
        return _entry(conn, invite_id)


def withdraw(invite_id: str, by: str, *, server_url: str, kind: str, ws: str = "", token: str = "") -> None:
    """Withdraw an invitation waiting for the target, ``by`` the account
    withdrawing it, and have its mailed link dropped at the account server
    (``cancel_links``). NoSuchInvitation when there is none."""
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")  # its link read and the row deleted as one step
        links = delete_email_invites(conn, f"id = ? AND {_TARGET}", (invite_id, kind, ws, token))
        conn.commit()
    if not links:
        raise NoSuchInvitation()
    cancel_links(links, by=by, server_url=server_url)


def cancel_links(links, *, by: str = "", server_url: str = "", wait: bool = False) -> None:
    """Ask the account server to drop the mailed links ``links``
    (``[(link_id, invited_by, server_url)]``) of invitations no row here
    admits any more — withdrawn, claimed, gone with their target, or never
    kept — so a link leads to the account server's "not valid any more"
    page, and an invite code it carried that nobody used goes with it.
    Each on the first Gamma Cloud grant this server holds among ``by``
    (whoever acts), ``invited_by`` and the admins' (``_grantee``): the
    account server only checks that the token is this server's client's.
    Each at its own ``server_url``, the address its mail went out with,
    which the account server keeps it under; for a link with none, at the
    caller's ``server_url``, else ``cloud_auth.server_url()``. Best effort,
    on one background thread, or here before returning with ``wait`` (a
    CLI process that exits right after); nothing while cloud sign-in is off
    or for a link with no address at all."""
    links = list(links)
    if not links:
        return
    if wait:
        _cancel_links(links, by, server_url)
    else:
        threading.Thread(target=_cancel_links, args=(links, by, server_url), name="cloud-invite-cancel",
                         daemon=True).start()


def _cancel_links(links: list, by: str, server_url: str) -> None:
    from . import cloud_auth  # local: cloud_auth imports this module

    if not cloud_auth.settings()["enabled"]:
        return
    fallback = server_url or cloud_auth.server_url()
    for link_id, invited_by, sent_at in links:
        url = sent_at or fallback
        if not url:
            continue
        grantee = _grantee(by, invited_by)
        if grantee:
            cloud_auth.cancel_invitation(grantee, link_id, url)
        else:
            log.warning("cloud: could not withdraw an invitation by e-mail at the account server: no account here "
                        "holds a Gamma Cloud grant to ask with")


def _grantee(*accounts: str) -> str:
    """The first of ``accounts`` (ids; "" skipped) whose Gamma Cloud grant
    this server holds, else the admin's who signed in with Gamma Cloud
    last; "" when nobody's."""
    from . import cloud_auth  # local: cloud_auth imports this module

    with connect_users_db() as conn:
        for user_id in accounts:
            if user_id and conn.execute("SELECT 1 FROM identities WHERE provider = ? AND user_id = ? "
                                        "AND refresh_token != ''", (cloud_auth.PROVIDER, user_id)).fetchone():
                return user_id
        row = conn.execute("SELECT i.user_id FROM identities i JOIN users u ON u.id = i.user_id "
                           "WHERE i.provider = ? AND i.refresh_token != '' AND u.is_admin = 1 "
                           "ORDER BY i.last_login_at DESC LIMIT 1", (cloud_auth.PROVIDER,)).fetchone()
    return row[0] if row else ""


def listing(kind: str, ws: str = "", token: str = "") -> list[dict]:
    """The target's invitations still waiting, by address: ``[{id, email,
    role, invited_by, created_at, mailed_at, expires_at}]``."""
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM email_invites WHERE {_TARGET} AND expires_at > ? "
                            "ORDER BY email_norm", (kind, ws, token, page_now())).fetchall()
        return _entries(conn, rows)


def _named(conn, link_id) -> tuple | None:
    """The unexpired invitation the link id ``link_id`` names (the one a
    mailed link carried; never the ``id`` the lists show): ``(id, kind,
    workspace_id, share_token, role, invited_by, server_url)``, or None."""
    if not link_id:
        return None
    return conn.execute("SELECT id, kind, workspace_id, share_token, role, invited_by, server_url FROM email_invites "
                        "WHERE link_id = ? AND expires_at > ?", (link_id, page_now())).fetchone()


def admits(conn, link_id, policy: str) -> bool:
    """Whether the invitation whose link id a cloud sign-in carried
    (``link_id``; "" or None for none) admits it under the sign-in
    ``policy``: one unexpired whose target is still there, a server
    invitation under any policy, a workspace's or a share's under
    ``MEMBER_POLICIES``. ``conn``: an open users.db connection."""
    row = _named(conn, link_id)
    if not row:
        return False
    _id, kind, ws, token, _role, _by, _url = row
    return (kind == "server" or policy in MEMBER_POLICIES) and _live(conn, kind, ws, token)


def claim(user_id: str, link_id: str, server_url: str = "") -> str:
    """The account ``user_id`` signed in with Gamma Cloud through a mailed
    link that carried the link id ``link_id`` ("" for none): the unexpired
    invitation it names becomes a membership of its workspace (still
    shared) or an entry of its share (not stopped) with its role, the row
    goes and the link at the account server with it (a used link stops
    working), at the address the row recorded; a server invitation did its
    work when it admitted the sign-in. ``server_url``: the address the
    sign-in came back on (``cloud_auth.callback_base``), for a row that
    recorded none ("" for ``cloud_auth.server_url()``). A membership or an
    entry the account had already keeps its own role. A new one tells the
    account in its Inbox, from the inviter. Nothing for a guest. Returns
    the id of the invitation taken, "" for none. Cheap without a link: it
    runs on every cloud sign-in."""
    if not link_id:
        return ""
    with connect_users_db() as conn:
        account = conn.execute("SELECT is_guest, username FROM users WHERE id = ?", (user_id,)).fetchone()
        if not account or account[0]:
            return ""
        conn.execute("BEGIN IMMEDIATE")  # the target checked and written as one step
        row = _named(conn, link_id)
        if not row:
            return ""
        invite_id, kind, ws, token, role, by, sent_at = row
        added = False
        if role in _ROLES[kind] and _live(conn, kind, ws, token):
            if kind == "workspace":
                added = conn.execute(
                    "INSERT INTO workspace_members (workspace_id, user_id, role, added_by, added_at) "
                    "VALUES (?, ?, ?, ?, ?) ON CONFLICT(workspace_id, user_id) DO NOTHING",
                    (ws, user_id, role, by, page_now())).rowcount > 0
            elif kind == "share":
                added = conn.execute(
                    "INSERT INTO share_users (token, user_id, role, added_at) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(token, user_id) DO NOTHING", (token, user_id, role, page_now())).rowcount > 0
        conn.execute("DELETE FROM email_invites WHERE id = ?", (invite_id,))
        conn.commit()
    cancel_links([(link_id, by, sent_at)], by=user_id, server_url=server_url)
    if not added:
        return invite_id
    if kind == "workspace":
        workspaces._tell_invited(ws, user_id, by)
    else:
        share = share_lookup(token)
        if share:
            shares.tell(share["workspace_id"], ShareScope.of(share), token, by, [user_id])
    log.info(f"[invites] {account[1]} joined the {kind} they were invited to by e-mail")
    return invite_id


def sweep() -> int:
    """Delete the invitations past their expiry; how many went. The app
    lifespan runs it at startup and every ``SWEEP_INTERVAL_S``."""
    with connect_users_db() as conn:
        gone = conn.execute("DELETE FROM email_invites WHERE expires_at <= ?", (page_now(),)).rowcount
        conn.commit()
    if gone:
        log.info(f"[invites] {gone} expired e-mail invitation(s) removed")
    return gone

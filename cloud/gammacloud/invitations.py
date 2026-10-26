"""E-mail invitations a Gamma server sends through the account server.

A Gamma server never sends mail itself. When someone there invites a
person by e-mail, the server asks here, on the inviter's own access token
(``POST /api/invitations``, ``routers/profile.py``), to mail a link to
``/invite?token=``. That page says who invited whom to what and takes the
person through creating a Gamma Cloud account or signing in, then
*Continue* sends the browser to the server's cloud sign-in with the
server's own invitation id (``continue_url``), which is what admits them
there.

One row per (server, the server's invitation id), owned by the sign-in
client that sent it: sending again through that client replaces it with a
new link, and the old one stops working. Another client registered for the
same server can neither replace nor cancel it.

An invitation carries a one-use free invite code only where the code is
the operator's to give (``_carries_code``): while sign-up needs one, for an
address with no account here, and from a server the operator runs, or from
any connected server while ``settings.invitation_signup`` allows it. The
code lives as long as its invitation: a re-send to the same inbox keeps it
while it can still be used and moves its expiry along. A code nobody
registered with goes with its invitation (withdrawn, expired, its mail
failed, its inviter purged); a spent one stays, since it names the account
that came in through it. Such codes are marked (``invites.invitation``)
and stay out of the operator's list.

The Gamma server is never told whether an address has an account here:
it gets the same answer either way, and only the mail differs.

Like ``accounts.py``, everything takes an open connection and commits
nothing.
"""

import re
from urllib.parse import urlencode, urlsplit

from . import accounts, config, mail, settings
from .accounts import Problem
from .db import after, audit, begin_write, new_token, now, token_hash

KINDS = ("server", "workspace", "page", "folder")
INVITE_ID_RE = re.compile(r"[A-Za-z0-9_-]{16,64}")
MAX_WHAT = 200
MAX_NEXT = 2048
TTL_DAYS = config.INVITATION_TTL // 86400
OTHER_CLIENT = "This invitation was sent through another sign-in client of this server."


def check(*, email: str, kind: str, what: str, invite_id: str, next_path: str) -> dict:
    """What ``POST /api/invitations`` sent, as it is stored: the address
    normalized, ``what`` cut to printable text ('' for the server itself,
    which ``server_name`` names); Problem 400 for the first field that is
    wrong. ``next_path`` is a path on the Gamma server the browser is sent
    back to, so it starts with one ``/`` and holds nothing a browser would
    read as another host (a backslash, whitespace)."""
    email = accounts.norm_email(email)
    if kind not in KINDS:
        raise Problem(400, "An invitation's kind is one of " + ", ".join(KINDS) + ".")
    what = " ".join("".join(c for c in what if c.isprintable()).split())[:MAX_WHAT] if kind != "server" else ""
    if not INVITE_ID_RE.fullmatch(invite_id or ""):
        raise Problem(400, "An invitation id is 16 to 64 letters, digits, hyphens and underscores.")
    if (not next_path.startswith("/") or next_path.startswith("//") or len(next_path) > MAX_NEXT
            or re.search(r"[\s\\\x00-\x1f\x7f]", next_path)):
        raise Problem(400, "next is a path on the server, starting with a single /.")
    return {"email": email, "kind": kind, "what": what, "invite_id": invite_id, "next_path": next_path}


def live_code(conn, code: str) -> str:
    """The invitation's code while it can still register an account, else ''."""
    row = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone() if code else None
    return code if row and accounts.invite_state(row) == "active" else ""


def _host(row) -> str:
    return urlsplit(row["server_url"]).netloc


def self_connected(client) -> bool:
    """Whether ``client`` (a token's) is a server someone connected
    themselves (a ``server`` client with an owner), not one the operator
    runs (the share host, a hosted server, a client made on the Admin
    page)."""
    return client["kind"] == "server" and bool(client.get("owner_account_id"))


def _carries_code(client, has_account: bool) -> bool:
    """Whether an invitation through ``client`` (the token's) carries an
    invite code. Never for an address that has an account, and only while
    sign-up needs a code: not in ``open`` mode (none is needed) or
    ``closed`` (no account is made), and not while the allowed mail domains
    are set, since an address at one of them registers without a code and
    any other is one the operator kept out. Then a server the operator runs
    may give one; a server someone connected themselves
    (``self_connected``) only while ``settings.invitation_signup`` is
    ``any``."""
    if has_account or settings.registration() != "invite" or settings.allowed_email_domains():
        return False
    return not self_connected(client) or settings.invitation_signup() == "any"


def send(conn, inviter, client, url: str, server_name: str, *, email: str, kind: str, what: str, invite_id: str,
         next_path: str) -> tuple[dict, dict | None, str, bool]:
    """Store an invitation from ``inviter`` (an account row) through
    ``client`` (the token's) for the server at ``url``, replacing the one
    that client sent before under the same id; Problem 409 when another
    client sent it. Returns (the row, the row it replaced or None, its link
    token, whether the address has an account here): ``invitation_mail``
    takes the row, the token and the flag once the caller has committed,
    and ``take_back`` the two rows when that mail fails.

    ``server_name`` is the name the server gives itself
    (``servers.norm_name``). Its hostname alone, which a Gamma server with
    no name of its own sends, counts as no name: it is stored as the host
    with its port, as an empty name is, so the mail names the place once,
    by its host, which it never marks as a link (``invitation_mail``).

    The replaced invitation's code goes on only to the same inbox: carried
    to another address, it would tell whoever sent both whether the first
    one has an account here."""
    begin_write(conn)
    old = conn.execute("SELECT * FROM server_invitations WHERE server_url = ? AND invite_id = ?",
                       (url, invite_id)).fetchone()
    if old and old["client_id"] != client["client_id"]:
        raise Problem(409, OTHER_CLIENT)
    has_account = accounts.by_email(conn, email) is not None
    carries = _carries_code(client, has_account)
    canon = accounts.email_canon(email)
    code = live_code(conn, old["code"]) if carries and old and old["email_canon"] == canon else ""
    if old:
        conn.execute("DELETE FROM server_invitations WHERE token_hash = ?", (old["token_hash"],))
        if old["code"] != code:
            _release(conn, old["code"])
    host = urlsplit(url).netloc
    if server_name.lower() == urlsplit(url).hostname:
        server_name = host
    if carries and not code:
        code = accounts.make_invite(conn, uses=1, plan="free", note=f"invitation to {host}", created_by=inviter["id"],
                                    expires_days=TTL_DAYS, invitation=True)["code"]
    token = new_token(32)
    row = {"token_hash": token_hash(token), "server_url": url, "invite_id": invite_id,
           "client_id": client["client_id"], "server_name": server_name, "kind": kind, "what": what,
           "next": next_path, "email": email, "email_canon": canon, "inviter_account_id": inviter["id"], "code": code,
           "created_at": now(), "expires_at": after(config.INVITATION_TTL), "accepted_at": None}
    if code:  # the code ends with its invitation, so a re-send moves both
        conn.execute("UPDATE invites SET expires_at = ? WHERE code = ?", (row["expires_at"], code))
    _insert(conn, row)
    audit(conn, "invitation.send", account_id=inviter["id"], actor=inviter["id"], detail=f"{kind} {host}")
    return row, dict(old) if old else None, token, has_account


def _insert(conn, row: dict, verb: str = "INSERT") -> int:
    """Write an invitation row; the number of rows written."""
    return conn.execute(f"{verb} INTO server_invitations ({', '.join(row)}) VALUES ({', '.join('?' for _ in row)})",
                        tuple(row.values())).rowcount


def take_back(conn, row: dict, replaced: dict | None) -> None:
    """Undo a ``send`` whose mail could not go out: its row goes, and the
    invitation it replaced comes back as it was, the link already in the
    person's inbox working again with its code and that code's expiry
    (unless another send took the id meanwhile). Only a code this send
    minted is released. A code the send released itself, one no longer
    usable or meant for another address, does not come back."""
    begin_write(conn)
    conn.execute("DELETE FROM server_invitations WHERE token_hash = ?", (row["token_hash"],))
    old_code = replaced["code"] if replaced else ""
    if replaced and _insert(conn, replaced, "INSERT OR IGNORE") and old_code and old_code == row["code"]:
        conn.execute("UPDATE invites SET expires_at = ? WHERE code = ?", (replaced["expires_at"], old_code))
    if row["code"] != old_code:
        _release(conn, row["code"])


def _release(conn, code: str) -> None:
    """An invitation's code once the invitation is gone: deleted unless
    someone registered with it (or another invitation took it over), so no
    unused code outlives its invitation."""
    if code:
        conn.execute("DELETE FROM invites WHERE code = ? AND uses_left = uses_total "
                     "AND code NOT IN (SELECT code FROM server_invitations)", (code,))


def drop(conn, row) -> None:
    """Take an invitation back with its unused code: a withdrawal or an
    expiry."""
    conn.execute("DELETE FROM server_invitations WHERE token_hash = ?", (row["token_hash"],))
    _release(conn, row["code"])


def cancel(conn, client, url: str, invite_id: str, actor: str) -> bool:
    """The server withdrew its invitation, through ``client`` (the
    token's); False when there was none, Problem 409 when another client
    sent it."""
    begin_write(conn)
    row = conn.execute("SELECT * FROM server_invitations WHERE server_url = ? AND invite_id = ?",
                       (url, invite_id)).fetchone()
    if not row:
        return False
    if row["client_id"] != client["client_id"]:
        raise Problem(409, OTHER_CLIENT)
    drop(conn, row)
    audit(conn, "invitation.cancel", account_id=actor, actor=actor, detail=f"{row['kind']} {_host(row)}")
    return True


def lookup(conn, token: str) -> dict | None:
    """The live invitation a mailed link names, with ``inviter``, its
    sender's label; None when the link is unknown, replaced by a newer
    one, withdrawn or expired."""
    row = conn.execute("SELECT i.*, a.username, a.display_name FROM server_invitations i "
                       "JOIN accounts a ON a.id = i.inviter_account_id WHERE i.token_hash = ? AND i.expires_at > ?",
                       (token_hash(token or ""), now())).fetchone()
    return {**dict(row), "inviter": inviter_label(row)} if row else None


def accept(conn, row) -> None:
    """The person continued to the server; the first time is kept."""
    conn.execute("UPDATE server_invitations SET accepted_at = ? WHERE token_hash = ? AND accepted_at IS NULL",
                 (now(), row["token_hash"]))


def continue_url(row) -> str:
    """The server's cloud sign-in, carrying its invitation id and where to
    land: the sign-in admits the person by that id."""
    return f"{row['server_url']}/api/auth/cloud/start?" + urlencode({"next": row["next"], "invite": row["invite_id"]})


def purge_expired(conn) -> int:
    """Drop every invitation past its expiry (the hourly ``app.purge``);
    returns the count."""
    rows = conn.execute("SELECT token_hash, code FROM server_invitations WHERE expires_at <= ?", (now(),)).fetchall()
    for row in rows:
        drop(conn, row)
    return len(rows)


def purge_account(conn, account_id: str) -> None:
    """The invitations a purged account sent (``accounts.purge``)."""
    for row in conn.execute("SELECT token_hash, code FROM server_invitations WHERE inviter_account_id = ?",
                            (account_id,)).fetchall():
        drop(conn, row)


# --- the words ----------------------------------------------------------------

def way_in(email: str, code: str, has_account: bool) -> str:
    """How the person invited comes to an account to accept with, which
    the mail and the signed-out invitation page tell: ``account`` (they
    have one: sign in), ``closed`` (no new accounts), ``create`` (the
    invitation's ``code`` still works, or the address registers without
    one), else ``code`` (a new account needs an invite code the invitation
    does not carry)."""
    if has_account:
        return "account"
    if settings.registration() == "closed":
        return "closed"
    return "create" if code or accounts.registers_without_code(email) else "code"


HOW = {"account": "Sign in with your Gamma Cloud account to accept.",
       "closed": "Accepting takes a Gamma Cloud account, and Gamma Cloud is not taking new ones right now. If you "
                 "already have an account, sign in with it.",
       "create": "Accepting takes a free Gamma Cloud account: create one with this address, or another one. If you "
                 "already have an account, sign in with it.",
       "code": "Accepting takes a Gamma Cloud account, and new ones need an invite code right now. If you already "
               "have an account, sign in with it."}

# What a mail client would turn into a link: a scheme's URL, a www. name, a
# dotted name with a path ("evil.example/restore"), or a dotted name whose
# last part is two or more letters, as every top-level domain's is (``last``).
# New top-level domains keep coming, so there is no list of them; instead a
# name ending in a file extension common in page titles stays ("Node.js",
# "attention.pdf"), even where the extension is a domain too (.md, .zip).
_LINKISH = re.compile(r"\b[a-z][a-z0-9+.-]*://\S*|\bwww\.\S*|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*"
                      r"|\b(?:[a-z0-9-]+\.)+(?P<last>[a-z]{2,})\b", re.IGNORECASE)
FILE_EXTENSIONS = frozenset(
    "pdf txt md tex bib doc docx odt rtf ppt pptx xls xlsx csv tsv png jpg jpeg gif svg webp tif tiff eps html htm "
    "json xml yaml yml zip gz tar epub py js ts ipynb".split())


def unlinked(text: str) -> str:
    """``text`` with every link-like word (``_LINKISH``) as "[link]"."""
    return _LINKISH.sub(lambda m: m[0] if (m["last"] or "").lower() in FILE_EXTENSIONS else "[link]", text)


def inviter_label(account) -> str:
    """``Display Name (@username)``, or ``@username`` without a display
    name. The name is cut to one line: it goes into a mail's subject."""
    name = " ".join(account["display_name"].split())
    return f"{name} (@{account['username']})" if name else f"@{account['username']}"


def place(row) -> str:
    """The server as people know it: its name and host, or the host alone."""
    host = _host(row)
    return f"{row['server_name']} ({host})" if row["server_name"] and row["server_name"] != host else host


def what_of(row) -> str:
    """What the invitation is to: "the page “Notes”", "a folder" when it has
    no name, "a Gamma server" for the server itself."""
    if row["kind"] == "server":
        return "a Gamma server"
    return f"the {row['kind']} “{row['what']}”" if row["what"] else f"a {row['kind']}"


def invitation_mail(inviter, row, token: str, has_account: bool) -> tuple[str, str, str]:
    """(subject, text, html) of an invitation to ``row["email"]``. Every
    part of the subject is one line, as a header must be: the inviter's
    label (``inviter_label``) and what ``check`` and ``servers.norm_name``
    left of the server's words. Those words, the inviter's display name,
    what the invitation is to and the name the server gives itself, lose
    any link (``unlinked``): the mail comes from this domain, and a link in
    it would borrow that trust. The server's host stays, since it names
    where the invitation leads (``place`` puts it after the name)."""
    who = inviter_label({**dict(inviter), "display_name": unlinked(inviter["display_name"])})
    name = row["server_name"]   # the host itself when the server gave no name (``send``)
    row = {**row, "what": unlinked(row["what"]), "server_name": name if name == _host(row) else unlinked(name)}
    where = place(row)
    if row["kind"] == "server":
        subject, first = f"{who} invited you to join {where}", f"{who} invited you to join the Gamma server {where}."
    else:
        subject = f"{who} invited you to join {what_of(row)}"
        first = f"{who} invited you to join {what_of(row)} on the Gamma server {where}."
    how = HOW[way_in(row["email"], row["code"], has_account)]
    return (subject, *mail.compose(
        "Hello,", [first, how], ("Accept the invitation", accounts.link("/invite", token)),
        f"The invitation works for {TTL_DAYS} days. If you do not know {who}, ignore this mail."))

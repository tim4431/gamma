"""Connecting a self-hosted Gamma server: how a server at a public address
gets the confidential client it signs people in with, without anyone
copying a client id and secret by hand.

1. The server's admin presses *Connect* in Gamma's Settings; the browser
   comes to ``/connect-server?server=<origin>&state=…&code_challenge=…``.
2. A signed-in person with a confirmed e-mail approves it on that page:
   a one-time code is stored (``server_connects``, AUTH_CODE_TTL) and the
   browser goes back to ``<origin>/api/auth/cloud/connect/callback``.
3. The server trades the code, its PKCE verifier and its own address for
   ``{client_id, client_secret}`` at ``/api/servers/connect/token``.

The code only ever travels to the address being connected, so finishing
the flow proves control of that address, which is what an admin checked
when creating a client by hand. The client (kind ``server``) is registered
for ``<origin>/api/auth/cloud/callback`` and belongs to the approving
account: its Devices page lists it with *Disconnect*. Connecting the same
address again from the same account keeps the client id and rotates the
secret, so people signed in there stay signed in. An account connects at
most MAX_CLIENTS servers.
"""

from urllib.parse import urlsplit

from . import config, oidc
from .accounts import Problem
from .db import after, audit, begin_write, new_token, now, token_hash
from .servers import is_local, norm_url

SIGN_IN_PATH = oidc.DESKTOP_CALLBACK_PATH   # every Gamma server's sign-in callback
RETURN_PATH = "/api/auth/cloud/connect/callback"
MAX_CLIENTS = 10


def check(server: str, state: str, challenge: str) -> str:
    """The normalized address of a connect request, or Problem 400."""
    origin = norm_url(server)
    if is_local(origin):
        raise Problem(400, "A Gamma server on this computer needs no connection: it signs in as the desktop app.")
    if not oidc.CHALLENGE_RE.match(challenge or ""):
        raise Problem(400, "The request carries no PKCE code challenge. Start again from the server.")
    if not state or len(state) > 512:
        raise Problem(400, "The request carries no state. Start again from the server.")
    return origin


def return_url(origin: str, params: dict) -> str:
    return oidc.redirect_with(origin + RETURN_PATH, params)


def approve(conn, account, origin: str, state: str, challenge: str) -> str:
    """The person approved: store the code; the URL to send the browser to."""
    code = new_token(32)
    conn.execute("INSERT INTO server_connects (code_hash, account_id, server, code_challenge, expires_at) "
                 "VALUES (?, ?, ?, ?, ?)", (token_hash(code), account["id"], origin, challenge, after(config.AUTH_CODE_TTL)))
    audit(conn, "server.connect", account["id"], account["id"], origin)
    return return_url(origin, {"code": code, "state": state})


def _owned(conn, account_id: str, callback: str):
    for row in conn.execute("SELECT client_id, redirect_uris FROM oauth_clients WHERE owner_account_id = ? "
                            "AND kind = 'server'", (account_id,)).fetchall():
        if oidc.get_client(conn, row["client_id"])["redirect_uris"] == [callback]:
            return row["client_id"]
    return None


def exchange(conn, code: str, verifier: str, server: str) -> dict:
    """The server fetches its client: ``{client_id, client_secret}``, or
    Problem 400 for a code that is unknown, used, expired, for another
    address or whose verifier does not match."""
    begin_write(conn)
    row = conn.execute("SELECT * FROM server_connects WHERE code_hash = ?", (token_hash(code or ""),)).fetchone()
    if not row:
        raise Problem(400, "unknown or used code")
    conn.execute("DELETE FROM server_connects WHERE code_hash = ?", (row["code_hash"],))
    try:
        origin = norm_url(server)
    except Problem:
        origin = ""
    if row["expires_at"] <= now() or origin != row["server"] or not oidc._pkce_ok(verifier, row["code_challenge"]):
        raise Problem(400, "the code is expired or does not belong to this server")
    account = conn.execute("SELECT * FROM accounts WHERE id = ? AND deleted_at IS NULL", (row["account_id"],)).fetchone()
    if not account or not account["email_verified_at"]:
        raise Problem(400, "the account that approved this connection cannot sign in any more")
    callback = origin + SIGN_IN_PATH
    client_id = _owned(conn, account["id"], callback)
    if client_id:
        secret = oidc.rotate_secret(conn, client_id, actor=account["id"])
    else:
        if conn.execute("SELECT COUNT(*) FROM oauth_clients WHERE owner_account_id = ?",
                        (account["id"],)).fetchone()[0] >= MAX_CLIENTS:
            raise Problem(400, f"An account connects at most {MAX_CLIENTS} servers. Disconnect one on its Devices page.")
        client_id, secret = oidc.create_client(conn, name=urlsplit(origin).netloc, kind="server",
                                               redirect_uris=[callback], actor=account["id"], owner=account["id"])
    return {"client_id": client_id, "client_secret": secret}


def of_account(conn, account_id: str) -> list[dict]:
    """The servers an account connected: the portal's list."""
    rows = conn.execute("SELECT client_id, name, redirect_uris, created_at FROM oauth_clients "
                        "WHERE owner_account_id = ? ORDER BY created_at DESC", (account_id,)).fetchall()
    out = []
    for r in rows:
        uri = (oidc.get_client(conn, r["client_id"])["redirect_uris"] or [""])[0]
        url = urlsplit(uri)
        out.append({"client_id": r["client_id"], "name": r["name"], "url": f"{url.scheme}://{url.netloc}",
                    "created_at": r["created_at"]})
    return out


def disconnect(conn, account_id: str, client_id: str) -> bool:
    """Delete a server client the account owns (its grants go with it)."""
    row = conn.execute("SELECT 1 FROM oauth_clients WHERE client_id = ? AND owner_account_id = ?",
                       (client_id, account_id)).fetchone()
    return bool(row) and oidc.delete_client(conn, client_id, actor=account_id)

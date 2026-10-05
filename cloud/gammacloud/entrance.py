"""Where a signed-in person's Gamma is (docs/dev/cloud_accounts.md "The
entrance").

People start at one address, the shared server's (``app.gammapdf.com``).
Most of them live there. A Pro account has a server of its own, and anyone
may be a member of somebody else's. Routing is done by the account server,
at the moment the shared server asks it to sign the person in: it is the
one place that knows every account's plan and servers.

``destinations`` lists where an account's libraries are; ``decide`` is the
rule the authorize step follows; ``after_sign_in`` is the same rule for a
person who has just typed a password (or come back from Google or GitHub)
on the authorize page. ``/open`` on the portal (``routers/portal.py``) uses
the same list for its Open Gamma button.
"""

from urllib.parse import quote

from . import config, db, hosted, oidc, servers

# A hosted server in one of these states answers at its address.
OPEN_STATES = ("running", "grace", "read_only", "suspended")


def shared_home(account_id: str) -> str:
    """The shared server this account's library lives on ("" = none is
    configured). One server today; with a second, this is where an account
    is told apart, and nothing else asks ``config.APP_URL``."""
    return config.APP_URL


def start_url(server_url: str) -> str:
    """The address that signs the browser in on a Gamma server and lands on
    its home page: its own cloud sign-in, which comes back here."""
    return f"{server_url.rstrip('/')}/api/auth/cloud/start?next=/"


def destinations(conn, account_id: str) -> list[dict]:
    """The Gamma Cloud servers the account has a library on, the one to
    open first at the head; each ``{url, kind, name}``:

    - ``own``: its hosted server, while that answers;
    - ``shared``: the shared server, for an account with no server of its
      own, and for one with a server that also signed in there (the
      library it had before Pro);
    - ``team``: other people's hosted servers it has signed in to.

    Servers people run themselves are not listed: they are reached at
    their own addresses, and the Devices page has them."""
    rows = servers.of_account(conn, account_id)
    out = []
    own = next((r for r in rows if r["hosted"]), None)
    if own and own["state"] in OPEN_STATES:
        out.append({"url": own["url"], "kind": "own", "name": "Your server"})
    linked = [r for r in rows if not r["hosted"] and not r["local"]]
    shared = shared_home(account_id)
    if shared and (not out or any(r["url"] == shared for r in linked)):
        out.append({"url": shared, "kind": "shared", "name": "Your library on Gamma Cloud"})
    for r in linked:
        if r["url"] != shared and hosted.is_server_url(conn, r["url"]):
            out.append({"url": r["url"], "kind": "team", "name": r["name"]})
    return out


def _signed_in_before(conn, account_id: str, client_id: str) -> bool:
    """The account holds a live grant for the client: it has signed in to
    that server before and has not signed it out."""
    return conn.execute("SELECT 1 FROM grants WHERE account_id = ? AND client_id = ? AND revoked_at IS NULL "
                        "AND expires_at > ?", (account_id, client_id, db.now())).fetchone() is not None


def decide(conn, req: dict, account) -> tuple[str, object]:
    """What a signed-in, verified account gets when the client of ``req``
    asks to sign it in:

    - ``("finish", None)``: the code, with no page in between;
    - ``("forward", url)``: its own server instead of the shared one;
    - ``("choose", destinations)``: a card listing its servers;
    - ``("confirm", None)``: the card that names the server and asks.

    The shared server is Gamma Cloud's own, so nothing is asked there; it
    is also the entrance, so an account whose library is elsewhere is sent
    on (a server of its own) or shown the choice (several). A hosted
    server signs its owner in at once, and so anyone who has signed in
    there before. Everything else (the desktop app, a server someone runs
    themselves, a first visit to another person's hosted server) keeps the
    confirm card: the address asking is not ours to vouch for."""
    client = req["client"]
    if client["kind"] == "share-host":
        places = destinations(conn, account["id"])
        if len(places) > 1:
            return "choose", places
        if places and places[0]["kind"] == "own":
            return "forward", start_url(places[0]["url"])
        return "finish", None
    if client["kind"] == "container" and (client["owner_account_id"] == account["id"]
                                          or _signed_in_before(conn, account["id"], client["client_id"])):
        return "finish", None
    return "confirm", None


def after_sign_in(conn, req: dict, account) -> str:
    """Where the browser goes once a verified person has just signed in on
    the authorize page: signing in there is the confirmation, so the
    request finishes, unless ``decide`` sends the person elsewhere."""
    what, value = decide(conn, req, account)
    if what == "forward":
        oidc.drop(conn, req)
        return value
    if what == "choose":
        return f"/authorize/resume?request_id={quote(req['id'])}"
    return oidc.finish(conn, req, account)

"""The other Gamma servers this computer's links name (docs/dev/https.md):
on the desktop app's own server only (``config.folder_links_enabled``),
what an address over plain http may be used for, and the move of every
link of one account from a server's old address to its new one.

**Plain http** (``plain``, ``refuse_plain_far``, ``check``). A link to
another server carries a token of it; over http:// that token travels in
clear. Where the host is (``host_scope``) decides: a new link to a host
on the internet over http:// is refused before any request (the vault's
``folder_links._remote``, the channel's ``channel.make``, the mirror's
``sync_engine._check_remote``, a move's ``to``); a link that exists at
that address keeps its upkeep (a new token, a channel re-tokened, a
mirror linked again at the same address) with the warning's words
instead. Lists say it without DNS (``plain``); the shell asks ``check``
before each token it mints, and asks the person once per origin on the
local network.

**The move** (``plan``, ``move``): the account's synced vaults, this
computer's channels and the offline copies (users.db ``folder_links``,
``computer_links``, ``mirrors``) whose address is ``frm`` take ``to``, in
one transaction, once ``to`` showed that it is that server: first a
request with no credential (``/api/health``, redirects refused, the
certificate verified or pinned), then each row's token, one at a time,
the channel's first (it opens nothing but the channel), stopping at the
first that fails, which moves nothing. Those checks catch a mistake (an
honest other server, a revoked token, another workspace); only the
certificate proves who ``to`` is, so the shell asks the person first,
naming the tokens that go there, but for the same host name moving to
https:// (``kind`` ``upgrade``). Only this server's rows move: the
shell runs the move on its host alone, so the offline copies another
local server keeps stay at the old address.

**This computer's id** for a server (``channel.computer_id``) is computed
from the address it was first shown at (users.db ``server_addresses``,
``id_origin``), kept across moves, so the server sees the same computer
after one; an address the links left is never used for an id again, and
a new link at it gets an id of its own (``fresh_id_origin``)."""

import json
import secrets
from urllib.parse import urlsplit

from . import gamma_sync, host_scope
from .db import connect_users_db, page_now


class MoveError(Exception):
    """A move refused, with why: ``status`` the route's answer (400 an
    address that does not do, 409 a token refused or another workspace
    there, 502 the new address not reached as that server)."""

    def __init__(self, status: int, words: str):
        super().__init__(words)
        self.status = status


# --- addresses ------------------------------------------------------------------------------

def origin_of(url: str) -> str:
    """``scheme://host[:port]``, lowercased, as ``channel.computer_id``
    reads an address."""
    parts = urlsplit(url)
    return f"{parts.scheme.lower()}://{parts.netloc.lower()}"


def _host(url: str) -> str:
    """The address's host as words name it (IPv6 in brackets)."""
    host = urlsplit(url).hostname or url
    return f"[{host}]" if ":" in host else host


def id_origin(remote_url: str) -> str:
    """What this computer's id for the server at ``remote_url`` is
    computed from: the first address that server was shown at, when this
    one is current (a ``server_addresses`` row whose ``moved_to`` is
    empty), else the address's own origin, as before any move."""
    origin = origin_of(remote_url)
    with connect_users_db() as conn:
        r = conn.execute("SELECT id_origin, moved_to FROM server_addresses WHERE origin = ?", (origin,)).fetchone()
    return r[0] if r and not r[1] else origin


def fresh_id_origin(remote_url: str) -> None:
    """A new channel (not a move) at an address this computer's links left:
    the address current again, with an id of its own, so another server now
    at that address never gets the id of the one that left it."""
    origin = origin_of(remote_url)
    with connect_users_db() as conn:
        if conn.execute("SELECT 1 FROM server_addresses WHERE origin = ? AND moved_to != ''", (origin,)).fetchone():
            conn.execute("INSERT INTO server_addresses (origin, id_origin, moved_to) VALUES (?, ?, '') "
                         "ON CONFLICT(origin) DO UPDATE SET id_origin = excluded.id_origin, moved_to = ''",
                         (origin, f"{origin}#{secrets.token_hex(4)}"))


def redirected(e: gamma_sync.Redirected, then: str) -> str:
    """What making a link says of a server that answered with a redirect
    (``e``): the same host name over https:// (``upgrade``) is named, to
    open it there and ``then``; another address is only told, never
    recommended, for anyone in the way may write a redirect."""
    if e.upgrade:
        return f"{e}: open {e.to} in Gamma and {then}."
    return f"That server redirects to another address ({e.to}). If you moved it, change its address in the launcher."


# --- plain http -----------------------------------------------------------------------------

def _plain_http(url: str) -> bool:
    return urlsplit(url).scheme.lower() == "http"


def plain(url: str) -> str:
    """Whether a link's address is plain http, without DNS (for lists):
    "" for https:// (or none) and a loopback host, "lan" for a host of the
    local network by its address or name, "name" for any other (a dotted
    name, which DNS may place anywhere, or a public address)."""
    if not url or not _plain_http(url):
        return ""
    scope = host_scope.literal(urlsplit(url).hostname or "")
    return {host_scope.LOOPBACK: "", host_scope.LAN: "lan"}.get(scope, "name")


def _refusal(url: str) -> str:
    host = _host(url)
    return (f"{host} is on the internet and this address is http://: Gamma sends its tokens there only over "
            f"https://. Use https://{host}.")


def _far_warning(url: str) -> str:
    return (f"{_host(url)} is on the internet and is reached over http://: the new token travels in clear. "
            "Change its address to https:// in the launcher.")


def _lan_warning(url: str) -> str:
    host = _host(url)
    return (f"{host} is reached over http://: someone on your network could read what is sent, the token Gamma "
            f"makes for it included, and pose as {host}. Use https:// to avoid this.")


def refuse_plain_far(url: str, *, existing: bool = False) -> str:
    """ValueError with the refusal's words for a new link over http:// to a
    host on the internet (``host_scope.resolved``); for a link that exists
    at that address (``existing``) the warning's words instead, never a
    refusal; "" otherwise."""
    if not _plain_http(url):
        return ""
    scope, _unsure = host_scope.resolved(urlsplit(url).hostname or "")
    if scope != host_scope.FAR:
        return ""
    if existing:
        return _far_warning(url)
    raise ValueError(_refusal(url))


def _existing(url: str, user_id: str) -> bool:
    """Whether the account has a link of any kind at that address."""
    with connect_users_db() as conn:
        return any(conn.execute(sql, (url, user_id)).fetchone() for sql in (
            "SELECT 1 FROM folder_links WHERE remote_url = ? AND created_by = ?",
            "SELECT 1 FROM computer_links WHERE remote_url = ? AND created_by = ?",
            "SELECT 1 FROM mirrors WHERE remote_url = ? AND owner = ?"))


def _kept(url: str, user_id: str, vault: str, workspace: str) -> bool:
    """Whether the token about to be minted is the upkeep of a link that
    exists at that address, as the route that takes it will count it: the
    vault ``vault`` there (a renewed token: ``folder_links._check_token``),
    or a channel there in ``workspace`` (``channel.make``). Anything else
    is a new link (a vault, an offline copy)."""
    with connect_users_db() as conn:
        if vault:
            r = conn.execute("SELECT remote_url FROM folder_links WHERE id = ? AND created_by = ?",
                             (vault, user_id)).fetchone()
            return bool(r and r[0] and origin_of(r[0]) == origin_of(url))
        if workspace:
            return bool(conn.execute("SELECT 1 FROM computer_links WHERE remote_url = ? AND workspace_id = ? "
                                     "AND created_by = ?", (url, workspace, user_id)).fetchone())
    return False


def check(url: str, user_id: str, *, vault: str = "", workspace: str = "") -> dict:
    """What the shell asks before it mints a token of the server at ``url``
    for the account ``user_id``: ``{origin, scope ("loopback" | "lan" |
    "far"), plain, existing, unsure, refused, warning}``. ``plain`` for
    http:// to a host that is not this computer; ``existing`` whether the
    account has a link of any kind at that address (the shell's question
    then counts as answered on the local network); ``refused`` the words of
    a new link's refusal (http:// to the internet, ``refuse_plain_far``),
    unless the token is the upkeep of a link there, as the route that takes
    it counts one (``_kept``: the renewed ``vault``'s id, or the channel's
    ``workspace``), so no token is minted in clear for a link that route
    then refuses; ``warning`` what the shell's question says of it (the
    local network's words, or the internet's for that upkeep); ``unsure``
    when the host's name did not resolve in time (the shell adds its line).
    DNS runs for http:// alone; an https:// address's scope is its literal
    one. ValueError for an address that does not do."""
    from .computers import channel  # local: it imports this module

    url = channel.clean_url(url)
    host = urlsplit(url).hostname or ""
    existing, unsure = _existing(url, user_id), False
    if _plain_http(url):
        scope, unsure = host_scope.resolved(host)
    else:
        scope = host_scope.literal(host)
    scope = scope if scope in (host_scope.LOOPBACK, host_scope.LAN) else host_scope.FAR
    is_plain = _plain_http(url) and scope != host_scope.LOOPBACK
    refused = warning = ""
    if is_plain and scope == host_scope.FAR:
        if _kept(url, user_id, vault, workspace):
            warning = _far_warning(url)
        else:
            refused = _refusal(url)
    elif is_plain:
        warning = _lan_warning(url)
    return {"origin": origin_of(url), "scope": scope, "plain": is_plain, "existing": existing, "unsure": unsure,
            "refused": refused, "warning": warning}


# --- the move -------------------------------------------------------------------------------

def _kind(frm: str, to: str) -> str:
    a, b = urlsplit(frm), urlsplit(to)
    if (a.scheme, b.scheme) == ("http", "https") and (a.hostname or "") == (b.hostname or ""):
        return "upgrade"
    return "downgrade" if (a.scheme, b.scheme) == ("https", "http") else "other"


def _rows(frm: str, user_id: str) -> dict:
    """The account's rows at ``frm``: ``{vaults, computers, mirrors}``, ids
    (a mirror's by its workspace)."""
    with connect_users_db() as conn:
        return {"vaults": [r[0] for r in conn.execute(
                    "SELECT id FROM folder_links WHERE remote_url = ? AND created_by = ? ORDER BY created_at",
                    (frm, user_id))],
                "computers": [r[0] for r in conn.execute(
                    "SELECT id FROM computer_links WHERE remote_url = ? AND created_by = ? ORDER BY created_at",
                    (frm, user_id))],
                "mirrors": [r[0] for r in conn.execute(
                    "SELECT workspace_id FROM mirrors WHERE remote_url = ? AND owner = ? ORDER BY created_at",
                    (frm, user_id))]}


def plan(frm: str, to: str, user_id: str) -> dict:
    """The dry run of ``move``: ``{from, to, kind, plain, moved: {vaults,
    computers, mirrors}}``, the counts of the account's rows at ``frm``;
    no request is made. ``kind`` is ``upgrade`` (the same host name, http://
    to https://, any port), ``downgrade`` (https:// to http://) or
    ``other``; ``plain`` whether ``to`` is http:// to a host that is not
    this computer. MoveError 400 for an address that does not do, the same
    address, and http:// to a host on the internet."""
    from .computers import channel  # local: it imports this module

    try:
        frm, to = channel.clean_url(frm), channel.clean_url(to)
        if frm == to:
            raise ValueError("The new address is the address the links have.")
        refuse_plain_far(to)
    except ValueError as e:
        raise MoveError(400, str(e)) from e
    rows = _rows(frm, user_id)
    is_plain = _plain_http(to) and host_scope.literal(urlsplit(to).hostname or "") != host_scope.LOOPBACK
    return {"from": frm, "to": to, "kind": _kind(frm, to), "plain": is_plain,
            "moved": {key: len(ids) for key, ids in rows.items()}}


def _unreached(to: str, e) -> MoveError:
    return MoveError(502, f"Gamma could not reach {to} ({e}): nothing was moved.")


def _refused(frm: str, to: str, what: str, *, other_workspace: bool = False) -> MoveError:
    if other_workspace:
        return MoveError(409, f"{to} opens another workspace than {frm} for {what}: it is another server. "
                              "Nothing was moved.")
    return MoveError(409, f"{to} refused the token of {what}: it is another server, or the token was revoked "
                          "there. Nothing was moved.")


def _probe(to: str) -> None:
    """``to`` answers as a Gamma server, asked with no credential
    (``/api/health``): redirects refused, the certificate verified or
    pinned. MoveError 502 otherwise, before any token goes there."""
    from . import folder_links  # local: it imports this module

    try:
        answer = gamma_sync.Server(to, "", timeout=folder_links.ASK_TIMEOUT_S).get_json("/api/health")
    except gamma_sync.SyncError as e:
        raise _unreached(to, e) from e
    if not isinstance(answer, dict) or answer.get("ok") is not True:
        raise _unreached(to, "it did not answer as a Gamma server")


def _check_rows(frm: str, to: str, rows: dict) -> None:
    """Each row's token at ``to``, one at a time, stopping at the first
    that fails (MoveError): the channels first (their token opens only the
    channel), then the vaults, then the offline copies. A row whose token
    no longer opens (the data directory's key changed) is not asked: it
    has nothing to send, and fails at either address."""
    from . import folder_links, sync_engine, workspaces  # local: folder_links and sync_engine import this module
    from .computers import channel

    for link_id in rows["computers"]:
        link = channel.get(link_id, with_token=True)
        if not link or not link["token"]:
            continue
        server = gamma_sync.Server(to, link["token"], timeout=folder_links.ASK_TIMEOUT_S)
        try:
            # what the channel announces right after the move (this computer's id as it stays, the row's
            # at ``frm``, and its vaults): at the same server (an upgrade) this token's row there is
            # updated, and one naming no vault would end its work folders' agents and their queued turns
            answer = channel._call(server, "POST", "/api/computers/announce", channel.state_of(link))
        except gamma_sync.Denied as e:
            raise _refused(frm, to, "this computer") from e
        except gamma_sync.SyncError as e:
            raise _unreached(to, e) from e
        account = str((answer.get("account") or {}).get("id") or "")
        if (answer.get("workspace") or {}).get("id") != link["workspace_id"] or (link["account"] and account != link["account"]):
            raise _refused(frm, to, "this computer", other_workspace=True)
    for link_id in rows["vaults"]:
        try:
            link = folder_links.get_link(link_id, with_token=True)
        except folder_links.LinkError:
            continue
        if not link or not link["token"]:
            continue
        what = f"the synced vault “{folder_links.vault_name(link)}”"
        try:
            who = gamma_sync.Server(to, link["token"], timeout=folder_links.ASK_TIMEOUT_S).get_json("/api/sync/whoami")
        except gamma_sync.Denied as e:
            raise _refused(frm, to, what) from e
        except gamma_sync.SyncError as e:
            raise _unreached(to, e) from e
        if (who.get("workspace") or {}).get("id") != link["workspace_id"]:
            raise _refused(frm, to, what, other_workspace=True)
    for ws in rows["mirrors"]:
        try:
            mirror = sync_engine.get_mirror(ws, with_token=True)
        except Exception:  # noqa: BLE001 — a sealed token that no longer opens (InvalidToken, ValueError)
            continue
        if not mirror or not mirror["token"]:
            continue
        what = f"the offline copy “{(workspaces.get(ws) or {}).get('name') or mirror['remote_name']}”"
        try:
            me = sync_engine.whoami(sync_engine.Remote(to, "", mirror["token"]))
        except sync_engine.RemoteError as e:
            raise (_refused(frm, to, what) if e.status in (401, 403) else _unreached(to, e)) from e
        if ((me or {}).get("workspace") or {}).get("id") != mirror["remote_ws"]:
            raise _refused(frm, to, what, other_workspace=True)


def _after_move(text: str) -> dict:
    """A moved vault's or copy's status: without what the failure at the
    old address left (``gamma_sync.STATUS_KEYS``, and ``failed_at``, so no
    retry wait holds its next round)."""
    try:
        status = json.loads(text or "{}")
    except ValueError:
        status = {}
    drop = (*gamma_sync.STATUS_KEYS, "failed_at")
    return {k: v for k, v in status.items() if k not in drop} if isinstance(status, dict) else {}


def move(frm: str, to: str, user_id: str, *, plain_ok: bool = False) -> dict:
    """The account ``user_id``'s links at ``frm`` moved to ``to`` (the
    module doc): ``{from, to, kind, moved: {vaults, computers, mirrors},
    revoke: [{workspace_id, token_id}]}``, ``revoke`` the tokens of the
    channels that were at ``to`` already, whose place the moved ones take
    (the desktop app revokes them at ``to``). None at ``frm`` moves nothing
    and asks ``to`` nothing. MoveError as ``plan``'s, 400 too for http://
    to the local network without ``plain_ok`` (the shell's question
    answered), then the probe's and the rows' checks. A row made at
    ``frm`` while the checks ran stays there; one removed meanwhile is not
    counted."""
    from . import folder_links  # local: it imports this module
    from .computers import channel

    planned = plan(frm, to, user_id)
    frm, to = planned["from"], planned["to"]
    done = {"from": frm, "to": to, "kind": planned["kind"], "revoke": []}
    rows = _rows(frm, user_id)
    if not any(rows.values()):
        return {**done, "moved": {key: 0 for key in rows}}
    if planned["plain"] and not plain_ok:
        raise MoveError(400, "Moving to http:// sends the links' tokens without encryption: the desktop app asks "
                             "you first.")
    _probe(to)
    _check_rows(frm, to, rows)
    ido, moved, merged = id_origin(frm), {"vaults": [], "computers": [], "mirrors": []}, []
    upsert = ("INSERT INTO server_addresses (origin, id_origin, moved_to) VALUES (?, ?, ?) ON CONFLICT(origin) "
              "DO UPDATE SET id_origin = excluded.id_origin, moved_to = excluded.moved_to")
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        # a vault's and a copy's status keep nothing of the old address's failure: the next round runs at once
        for table, key, ids in (("folder_links", "id", rows["vaults"]), ("mirrors", "workspace_id", rows["mirrors"])):
            for row_id in ids:
                r = conn.execute(f"SELECT status FROM {table} WHERE {key} = ? AND remote_url = ?", (row_id, frm)).fetchone()
                if r:
                    conn.execute(f"UPDATE {table} SET remote_url = ?, status = ? WHERE {key} = ?",
                                 (to, json.dumps(_after_move(r[0])), row_id))
                    moved["vaults" if table == "folder_links" else "mirrors"].append(row_id)
        for link_id in rows["computers"]:
            r = conn.execute("SELECT workspace_id FROM computer_links WHERE id = ? AND remote_url = ?",
                             (link_id, frm)).fetchone()
            if not r:
                continue
            there = conn.execute("SELECT id, token_id FROM computer_links WHERE remote_url = ? AND workspace_id = ? "
                                 "AND created_by = ?", (to, r[0], user_id)).fetchone()
            if there:   # this computer shown at the new address first: the moved row takes its place
                conn.execute("DELETE FROM computer_links WHERE id = ?", (there[0],))
                merged.append(there[0])
                if there[1]:
                    done["revoke"].append({"workspace_id": r[0], "token_id": there[1]})
            conn.execute("UPDATE computer_links SET remote_url = ?, status = ? WHERE id = ?",
                         (to, json.dumps({"state": "connecting", "error": "", "at": page_now()}), link_id))
            moved["computers"].append(link_id)
        conn.execute(upsert, (origin_of(to), ido, ""))
        if origin_of(frm) != origin_of(to):
            conn.execute(upsert, (origin_of(frm), ido, to))
    for link_id in merged:
        channel.stop(link_id)
    for link_id in moved["computers"]:
        channel.stop(link_id)
        channel.start(link_id)
    folder_links._vaults_changed()
    return {**done, "moved": {key: len(ids) for key, ids in moved.items()}}

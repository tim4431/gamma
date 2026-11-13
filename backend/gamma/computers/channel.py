"""The computer's side of its channel (gamma/computers/__init__.py), on the
desktop app's own server only (``config.folder_links_enabled``): this
computer announces itself to every other Gamma server its vaults name, and
holds a long-poll open to it, so that server can show the vaults' maker
where they are kept.

The credential is a token of that server of the ``computer`` scope, which
opens the channel and nothing else, minted there with the window's session
and handed over by the desktop app with the shell's word
(routers/computer_links.py): one users.db ``computer_links`` row per server,
workspace there and local account, the token sealed
(``publisher_sessions.seal``, as a vault's is), the account there the
server named, and the channel's ``status``, ``{state, error, at}``:
``connecting`` until an announce succeeds (``connected``, while the polls
do), then
``refused`` (the token: retried after ``REFUSED_S``), ``older`` (a server
without the channel: after ``OLDER_S``) or ``unreachable`` (after
``RETRY_FIRST_S``, doubling to ``RETRY_MAX_S``). It is written only when it
changes.

One daemon thread per row (``_Channel``), started when the row is made and
checked by ``folder_links.tick`` (``check``: the missing ones start, the
ones whose row went stop), stopped at shutdown (``stop_all``). Each turn
builds the state (``state_of``: this computer's id for that server and its
name, the app's version, the vaults of that server, workspace and account
with whether each is paused, and the assistants as the catalog last found
them, ``catalog.known``: a probe only when none was ever made), announces it when it differs from what was last
announced or the server asked, then polls (held up to ``POLL_S``). A vault
added, paused or removed nudges the threads (``nudge``), which see it at
the end of the poll they hold, at most ``POLL_S`` later. Never a path.
"""

import base64
import hashlib
import hmac
import json
import re
import threading
import time
import uuid
from urllib.parse import urlencode, urlsplit

from cryptography.fernet import InvalidToken

from .. import config, folder_links, gamma_sync, version
from ..cloud_auth import device
from ..db import connect_users_db, page_now
from ..logbuf import log
from ..publisher_sessions import seal, unseal

POLL_S = 25              # the hold each poll asks for (the server's longest)
PAST_HOLD_S = 15         # how long past the hold a poll's answer may take
REFUSED_S = 600          # a refused token is tried again after this long
OLDER_S = 3600           # a server without the channel is asked again after this long
RETRY_FIRST_S = 5        # an unreachable server is tried again after this, doubling
RETRY_MAX_S = 300        # up to this
QUICK_S = 1.0            # a poll answered at once with nothing: the next waits this long
STATES = ("connecting", "connected", "refused", "older", "unreachable")

_COLS = "id, remote_url, workspace_id, created_by, token, token_id, account, username, created_at, status"


class Older(gamma_sync.SyncError):
    """The server has no channel routes: a Gamma from before computers
    announced themselves."""


# --- the rows -------------------------------------------------------------------------------

def _row(r, with_token: bool = False) -> dict:
    link = {"id": r[0], "remote_url": r[1], "workspace_id": r[2], "created_by": r[3], "token_id": r[5],
            "account": r[6], "username": r[7], "created_at": r[8], "status": folder_links._object_of(r[9])}
    if with_token:
        try:
            link["token"] = unseal(r[4])
        except (InvalidToken, ValueError):   # the data directory's key changed: the server refuses it
            link["token"] = ""
    return link


def links(user_id: str = "") -> list[dict]:
    """Every channel kept here, or the account ``user_id``'s; never a token."""
    with connect_users_db() as conn:
        rows = conn.execute(f"SELECT {_COLS} FROM computer_links WHERE ? = '' OR created_by = ? ORDER BY created_at",
                            (user_id, user_id)).fetchall()
    return [_row(r) for r in rows]


def get(link_id: str, *, with_token: bool = False) -> dict | None:
    with connect_users_db() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM computer_links WHERE id = ?", (link_id,)).fetchone()
    return _row(r, with_token) if r else None


def clean_url(url: str) -> str:
    """The other server's address as a vault of it keeps it
    (``folder_links._remote``): no trailing slash. ValueError for one that
    is not http(s)://host[:port][/path]."""
    url = str(url or "").strip().rstrip("/")
    if not re.match(r"^https?://[^\s/]+(/\S*)?$", url):
        raise ValueError("Give the other server's address as http(s)://host[:port].")
    return url


def make(url: str, workspace: str, token: str, token_id: str, user_id: str) -> tuple[dict, dict | None]:
    """The channel of the account ``user_id`` to the server at ``url``, for
    its workspace ``workspace``, with ``token`` (a ``computer`` token of
    it), once one announce with it succeeds; it starts at once. One already
    kept for that server, workspace and account takes the new token, if it
    is a token of the same account there (ValueError otherwise):
    ``(link, {token_id} | None)``, the token it held (the desktop app's to
    revoke there) when another. ``gamma_sync.Denied`` for a refused token,
    ``Older`` for a server without the channel, ``gamma_sync.SyncError``
    for one that cannot be reached, ValueError for an address or a
    workspace that does not do."""
    url = clean_url(url)
    if not token:
        raise ValueError("A computer's token of the other server is needed.")
    server = gamma_sync.Server(url, token, timeout=folder_links.ASK_TIMEOUT_S)
    # Checked with no vault named: whose token it is decides first whether it may name them (the thread does).
    answer = _call(server, "POST", "/api/computers/announce",
                   {**state_of({"remote_url": url, "workspace_id": workspace, "created_by": user_id}), "vaults": []})
    if (answer.get("workspace") or {}).get("id") != workspace:
        raise ValueError("The token belongs to another workspace of that server.")
    account = answer.get("account") or {}
    status = json.dumps({"state": "connecting", "error": "", "at": page_now()})
    with connect_users_db() as conn:
        conn.execute("BEGIN IMMEDIATE")
        old = conn.execute("SELECT id, token_id, account, username FROM computer_links WHERE remote_url = ? AND "
                           "workspace_id = ? AND created_by = ?", (url, workspace, user_id)).fetchone()
        if old and old[2] and str(account.get("id") or "") != old[2]:
            # The vaults it announces were made with that account's tokens: never shown to another.
            raise ValueError(f"This computer is shown on that server to another account there ({old[3] or old[2]}): "
                             "stop showing it first.")
        link_id = old[0] if old else uuid.uuid4().hex
        if old:
            conn.execute("UPDATE computer_links SET token = ?, token_id = ?, account = ?, username = ?, status = ? "
                         "WHERE id = ?", (seal(token), token_id, str(account.get("id") or ""),
                                          str(account.get("username") or ""), status, link_id))
        else:
            conn.execute(f"INSERT INTO computer_links ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                         (link_id, url, workspace, user_id, seal(token), token_id, str(account.get("id") or ""),
                          str(account.get("username") or ""), page_now(), status))
    stop(link_id)
    start(link_id)
    return get(link_id), {"token_id": old[1]} if old and old[1] != token_id else None


def delete(link_id: str, user_id: str) -> str | None:
    """The account's channel ``link_id`` stops and is forgotten: its token's
    id there (the desktop app revokes it), None when it is no channel of
    that account."""
    link = get(link_id)
    if link is None or link["created_by"] != user_id:
        return None
    stop(link_id)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE id = ?", (link_id,))
    return link["token_id"]


# --- what this computer announces -------------------------------------------------------------

def computer_id(remote_url: str) -> str:
    """This computer's id for the server at ``remote_url``: the HMAC of the
    install's id (``cloud_auth.device``) and the server's origin, so two
    servers cannot match one computer by its id."""
    parts = urlsplit(remote_url)
    origin = f"{parts.scheme.lower()}://{parts.netloc.lower()}"
    digest = hmac.new(device()[0].encode(), origin.encode(), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]


def _code(name: str, found: dict) -> str:
    """An assistant's state as a code, never the catalog's words."""
    from ..local_agents import catalog

    if found["found"] is None or found["ready"] is None:
        return "checking"
    if not found["found"]:
        return "missing"
    if found["ready"]:
        return ""
    return "signed_out" if found["problem"] == catalog.NOT_SIGNED_IN.get(name) else "not_ready"


def assistants() -> list[dict]:
    """The agents on this computer as the catalog last found them
    (``catalog.known``: one probe when none was ever made, never one to
    refresh it, so a channel keeps no agent probed), ``[]`` where agents do not run
    (``local_agents.enabled``)."""
    from .. import local_agents
    from ..local_agents import catalog

    if not local_agents.enabled():
        return []
    there = []
    for name in catalog.AGENTS:
        found = catalog.known(name)
        there.append({"name": name, "label": found["label"], "found": found["found"], "ready": found["ready"],
                      "version": (found["version"] or "")[:64], "code": _code(name, found)})
    return there


def state_of(link: dict) -> dict:
    """What this computer announces on the channel ``link``: its id for
    that server and its name, the app's version, the vaults of that server,
    workspace and account (``{kind, folder_id | page_id, paused}``, the
    pause as the sync panel shows it) and the assistants."""
    vaults = []
    for vault in folder_links.all_links():
        if (vault["remote_url"], vault["workspace_id"], vault["created_by"]) != \
                (link["remote_url"], link["workspace_id"], link["created_by"]):
            continue
        key, target = ("folder_id", vault["folder_id"]) if vault["kind"] == "folder" else ("page_id", vault["page_id"])
        vaults.append({"kind": vault["kind"], key: target, "paused": bool(vault["status"].get(folder_links.PAUSED))})
    name = device()[1].strip()[:64]
    return {"computer": {"id": computer_id(link["remote_url"]), "name": name if name.isprintable() and name
                         else "This computer"},
            "app": version.label()[:64], "vaults": vaults, "assistants": assistants()}


def _call(server: gamma_sync.Server, method: str, path: str, obj=None) -> dict:
    """The server's JSON answer to one channel request. ``gamma_sync.Denied``
    for 401/403, ``Older`` for 404/405 (no such route there), else
    ``gamma_sync.Unreachable`` for anything but a JSON object."""
    status, data = server.ask(method, path, obj)
    if status in (401, 403):
        raise gamma_sync.Denied("that server refused this computer's token")
    if status in (404, 405):
        raise Older("that server is older than this app: it cannot show this computer")
    if not 200 <= status < 300:
        raise gamma_sync.Unreachable(f"{path.split('?')[0]}: {status}")
    try:
        answer = json.loads(data.decode("utf-8")) if data else {}
    except ValueError as e:
        raise gamma_sync.Unreachable(f"{path.split('?')[0]}: the server did not answer JSON") from e
    if not isinstance(answer, dict):
        raise gamma_sync.Unreachable(f"{path.split('?')[0]}: the server did not answer JSON")
    return answer


# --- the threads ----------------------------------------------------------------------------

class _Channel:
    """One channel's loop: ``once`` until stopped, resting between turns as
    ``once`` says (a nudge ends a rest)."""

    def __init__(self, link_id: str):
        self.link_id = link_id
        self.stopped = threading.Event()
        self.nudged = threading.Event()
        self.announced = None       # the state last announced, None to announce again
        self.version = ""           # that announce's version, which the polls name
        self.backoff = RETRY_FIRST_S
        self.thread: threading.Thread | None = None

    def run(self) -> None:
        while not self.stopped.is_set():
            try:
                rest = self.once()
            except Exception:  # noqa: BLE001 — the channel never stops the server
                log.exception(f"[computers] the channel {self.link_id} failed a turn")
                rest = RETRY_MAX_S
            if rest and not self.stopped.is_set():
                self.nudged.wait(rest)

    def once(self) -> float:
        """One turn: announce when the state changed or the server asked,
        then one poll. The seconds to rest before the next."""
        self.nudged.clear()
        link = get(self.link_id, with_token=True)
        if link is None:
            self.stopped.set()
            return 0
        server = gamma_sync.Server(link["remote_url"], link["token"], timeout=POLL_S + PAST_HOLD_S)
        try:
            state = state_of(link)
            if state != self.announced:
                answer = _call(server, "POST", "/api/computers/announce", state)
                self.announced, self.version = state, str(answer.get("version") or "")
                self._account(link, answer.get("account") or {})
                self._said(link, "connected", "", 0)   # shown there now, before the poll's hold
            started = time.monotonic()
            got = _call(server, "GET", "/api/computers/poll?" + urlencode({"v": self.version, "wait": POLL_S}))
        except gamma_sync.Denied as e:
            self.announced = None
            return self._said(link, "refused", str(e), REFUSED_S)
        except Older as e:
            self.announced = None
            return self._said(link, "older", str(e), OLDER_S)
        except gamma_sync.SyncError as e:
            self.announced, rest = None, self.backoff
            self.backoff = min(self.backoff * 2, RETRY_MAX_S)
            return self._said(link, "unreachable", str(e), rest)
        self.backoff = RETRY_FIRST_S
        self._said(link, "connected", "", 0)
        if got.get("announce"):
            self.announced = None
        # A poll answered at once (nothing, or announce again: a server that always asks so) rests a moment.
        return max(0.0, QUICK_S - (time.monotonic() - started))

    def _said(self, link: dict, state: str, error: str, rest: float) -> float:
        """The channel's status, written when it changed (and while the
        channel runs); ``rest`` passed through."""
        said = (link["status"].get("state"), link["status"].get("error", ""))
        if not self.stopped.is_set() and said != (state, error):
            link["status"] = {"state": state, "error": error, "at": page_now()}
            with connect_users_db() as conn:
                conn.execute("UPDATE computer_links SET status = ? WHERE id = ?",
                             (json.dumps(link["status"]), link["id"]))
        return rest

    def _account(self, link: dict, account: dict) -> None:
        """The account there the server named, kept when it changed."""
        found = (str(account.get("id") or ""), str(account.get("username") or ""))
        if found != (link["account"], link["username"]) and not self.stopped.is_set():
            with connect_users_db() as conn:
                conn.execute("UPDATE computer_links SET account = ?, username = ? WHERE id = ?", (*found, link["id"]))


_channels: dict[str, _Channel] = {}
_guard = threading.Lock()


def start(link_id: str) -> None:
    """The channel's thread, unless one runs."""
    with _guard:
        running = _channels.get(link_id)
        if running is not None and running.thread is not None and running.thread.is_alive() \
                and not running.stopped.is_set():
            return
        channel = _channels[link_id] = _Channel(link_id)
        channel.thread = threading.Thread(target=channel.run, daemon=True, name=f"computer-channel-{link_id[:8]}")
        channel.thread.start()


def stop(link_id: str) -> None:
    """The channel's thread stops: at once from a rest, after its poll
    otherwise, writing nothing more."""
    with _guard:
        channel = _channels.pop(link_id, None)
    if channel is not None:
        channel.stopped.set()
        channel.nudged.set()


def check() -> None:
    """``folder_links.tick``'s: a thread for every channel kept here, none
    for one that went. Nothing where folders are not kept."""
    if not config.folder_links_enabled():
        return
    kept = {link["id"] for link in links()}
    with _guard:
        running = set(_channels)
    for gone in running - kept:
        stop(gone)
    for link_id in kept:
        start(link_id)


def stop_all() -> None:
    """Every channel's thread stops (the server's shutdown)."""
    with _guard:
        running = list(_channels)
    for link_id in running:
        stop(link_id)


def nudge() -> None:
    """A vault was added, paused, resumed or removed: each channel builds
    its state again once its poll ends."""
    with _guard:
        running = list(_channels.values())
    for channel in running:
        channel.nudged.set()

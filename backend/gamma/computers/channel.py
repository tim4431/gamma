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
server named, and the channel's ``status``, ``{state, error, at}`` and,
for a server not reached as that server, what was in the way
(``gamma_sync.status_fields``: ``error_kind`` and its fields):
``connecting`` until an announce succeeds (``connected``, while the polls
do), then
``refused`` (the token: retried after ``REFUSED_S``), ``older`` (a server
without the channel: after ``OLDER_S``) or ``unreachable`` (after
``RETRY_FIRST_S``, doubling to ``RETRY_MAX_S``; a redirect, a certificate
or no HTTPS, which do not go away by themselves, after ``RETRY_MAX_S``).
Refused and older are that server's own answers
(``gamma_sync.is_gamma_refusal``): a sign-in or proxy page in its way is
``unreachable``, ``blocked``. It is written only when it changes.

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

A work folder of that server kept here with an agent on (step 4c,
``agent_folders``) is announced with its ``agent``, and its Sends run here:
after each announce, on a poll answered ``{wake: ["sends"]}``, when a vault
takes Sends again (``ask_sends``) and at least every ``SENDS_EVERY_S``, the
thread asks that server for the Sends that wait (``_sends``), queues each for
its vault (gamma/local_agents/runner.py ``queue_remote``) and stops the runs
whose request was withdrawn there; each run speaks to that server with this
channel's token (``of_vault``, gamma/local_agents/home.py). Each announce
that succeeds fails there the runs of its vaults left open while that server
was not reached (``_settle``); a channel deleted turns their agents off.

The chat's computer side on a page of such a work folder (step 4d) runs here
too: after each announce, on a poll answered ``{wake: ["chat"]}`` and at
least every ``CHATS_EVERY_S``, the thread asks that server for the turns
that wait (``_chats``), queues each for its vault
(``runner.queue_remote_chat``) and stops the ones stopped there; the
announce's assistants name the models each offers (``models``), which that
server offers the chat's model chip and checks a turn's against.
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

from .. import config, folder_links, gamma_sync, remote_servers, version
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
SENDS_EVERY_S = 180      # the Sends that wait there are asked for at least this often (a wake lost)
CHATS_EVERY_S = 180      # and the chat turns that wait there
MAX_MODELS = 32          # models an assistant's announce names
_TURN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # a chat turn's id there (``blocks_store.new_block_id``'s)
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


def of_vault(vault: dict) -> dict | None:
    """The channel, with its token, that the vault ``vault`` of another
    server is announced on (its server, workspace and local account's),
    None for none: what a run of its agent speaks to that server with
    (gamma/local_agents/home.py)."""
    with connect_users_db() as conn:
        r = conn.execute(f"SELECT {_COLS} FROM computer_links WHERE remote_url = ? AND workspace_id = ? AND "
                         "created_by = ?", (vault["remote_url"], vault["workspace_id"], vault["created_by"])).fetchone()
    return _row(r, True) if r else None


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
    for one that cannot be reached (``Redirected``, ``CertificateRefused``,
    ``NotHttps``, ``Blocked`` among them), ValueError for an address or a
    workspace that does not do, or plain http to a host on the internet for
    a new channel (``remote_servers.refuse_plain_far``; one kept there takes
    its new token)."""
    url = clean_url(url)
    if not token:
        raise ValueError("A computer's token of the other server is needed.")
    with connect_users_db() as conn:
        kept = conn.execute("SELECT 1 FROM computer_links WHERE remote_url = ? AND workspace_id = ? AND created_by = ?",
                            (url, workspace, user_id)).fetchone() is not None
    remote_servers.refuse_plain_far(url, existing=kept)
    if not kept:
        remote_servers.fresh_id_origin(url)   # an address a server left: this computer's id there is a new one
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
    """The account's channel ``link_id`` stops and is forgotten, and the
    agent of each work folder it announced is turned off (its run stopped),
    so showing this computer there again, maybe as another account there,
    never brings an agent back without the shell's question: its token's
    id there (the desktop app revokes it), None when it is no channel of
    that account."""
    from ..local_agents import store

    link = get(link_id)
    if link is None or link["created_by"] != user_id:
        return None
    stop(link_id)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE id = ?", (link_id,))
    vaults = _vaults_of(link)
    for vault_id in store.agents_of([vault["id"] for vault in vaults]):
        folder_links.set_agent(vault_id, "", by=user_id)
    return link["token_id"]


# --- what this computer announces -------------------------------------------------------------

def computer_id(remote_url: str) -> str:
    """This computer's id for the server at ``remote_url``: the HMAC of the
    install's id (``cloud_auth.device``) and the server's origin, so two
    servers cannot match one computer by its id; for a server whose links
    moved, the origin it was first shown at (``remote_servers.id_origin``),
    so that server sees the same computer at its new address."""
    origin = remote_servers.id_origin(remote_url)
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
    refresh it, so a channel keeps no agent probed), each with the models
    it offers (``[{id, label, efforts, effort, default}]``, at most
    ``MAX_MODELS``: what a chat turn there may ask for), ``[]`` where agents
    do not run (``local_agents.enabled``)."""
    from .. import local_agents
    from ..local_agents import catalog

    if not local_agents.enabled():
        return []
    there = []
    for name in catalog.AGENTS:
        found = catalog.known(name)
        there.append({"name": name, "label": found["label"], "found": found["found"], "ready": found["ready"],
                      "version": (found["version"] or "")[:64], "code": _code(name, found),
                      "models": list(found.get("models") or [])[:MAX_MODELS]})
    return there


def _vaults_of(link: dict) -> list[dict]:
    """The vaults of the channel ``link``'s server, workspace and account."""
    return [vault for vault in folder_links.all_links()
            if (vault["remote_url"], vault["workspace_id"], vault["created_by"])
            == (link["remote_url"], link["workspace_id"], link["created_by"])]


def agent_folders(link: dict, vaults: list[dict] | None = None) -> dict[str, tuple[dict, str]]:
    """``{folder id: (vault, agent)}``: the work folders of the channel
    ``link``'s server that a vault here keeps with an agent on
    (``folder_links.work_vault``, ``local_agents.store``), paused or not;
    none where agents do not run (``local_agents.enabled``)."""
    from .. import local_agents
    from ..local_agents import store

    if not local_agents.enabled():
        return {}
    work = [vault for vault in (_vaults_of(link) if vaults is None else vaults)
            if vault["remote_url"] and folder_links.work_vault(vault)]
    rows = store.agents_of([vault["id"] for vault in work]) if work else {}
    return {vault["folder_id"]: (vault, rows[vault["id"]]["agent"]) for vault in work if vault["id"] in rows}


def state_of(link: dict) -> dict:
    """What this computer announces on the channel ``link``: its id for
    that server and its name, the app's version, the vaults of that server,
    workspace and account (``{kind, folder_id | page_id, paused}``, the
    pause as the sync panel shows it; a folder's ``agent``, the one that
    answers its Sends from this computer, ``codex`` or ``claude``, null for
    none: ``agent_folders``) and the assistants."""
    vaults, kept = [], _vaults_of(link)
    agents = {vault["id"]: agent for vault, agent in agent_folders(link, kept).values()}
    for vault in kept:
        key, target = ("folder_id", vault["folder_id"]) if vault["kind"] == "folder" else ("page_id", vault["page_id"])
        vaults.append({"kind": vault["kind"], key: target, "paused": bool(vault["status"].get(folder_links.PAUSED))})
        if vault["kind"] == "folder":
            vaults[-1]["agent"] = agents.get(vault["id"])
    name = device()[1].strip()[:64]
    return {"computer": {"id": computer_id(link["remote_url"]), "name": name if name.isprintable() and name
                         else "This computer"},
            "app": version.label()[:64], "vaults": vaults, "assistants": assistants()}


def _call(server: gamma_sync.Server, method: str, path: str, obj=None) -> dict:
    """The server's JSON answer to one channel request. ``gamma_sync.Denied``
    for its own 401/403, ``Older`` for its own 404/405 (no such route there;
    ``gamma_sync.is_gamma_refusal``), ``gamma_sync.Blocked`` for a refusal
    by something in front of it (``gamma_sync.blocked``), else
    ``gamma_sync.Unreachable`` for anything but a JSON object."""
    status, data = server.ask(method, path, obj)
    kind = gamma_sync.blocked(status, server.last_headers, data)
    if kind:
        raise gamma_sync.Blocked(server.host, status, kind)
    own = gamma_sync.is_gamma_refusal(data)
    if status in (401, 403) and own:
        raise gamma_sync.Denied("that server refused this computer's token")
    if status in (404, 405) and own:
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
        self.sends_due = True       # the Sends that wait there are asked for at the next turn (``_sends``)
        self.sends_at = 0.0         # time.monotonic() when they were last asked for
        self.chats_due = True       # the chat turns that wait there are asked for at the next turn (``_chats``)
        self.chats_at = 0.0
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
                self.sends_due = self.chats_due = True  # what waited for this computer while it was away
                self._settle(link)
            if self.sends_due or time.monotonic() - self.sends_at >= SENDS_EVERY_S:
                self._sends(server, link)
            if self.chats_due or time.monotonic() - self.chats_at >= CHATS_EVERY_S:
                self._chats(server, link)
            started = time.monotonic()
            got = _call(server, "GET", "/api/computers/poll?" + urlencode({"v": self.version, "wait": POLL_S}))
        except gamma_sync.Denied as e:
            self.announced = None
            return self._said(link, "refused", str(e), REFUSED_S)
        except Older as e:
            self.announced = None
            return self._said(link, "older", str(e), OLDER_S)
        except (gamma_sync.Redirected, gamma_sync.CertificateRefused, gamma_sync.NotHttps) as e:
            # none goes away by itself: tried again after the longest rest, the status saying what it was
            self.announced = None
            return self._said(link, "unreachable", str(e), RETRY_MAX_S, **gamma_sync.status_fields(e))
        except gamma_sync.Blocked as e:
            self.announced, rest = None, self.backoff
            self.backoff = min(self.backoff * 2, RETRY_MAX_S)
            return self._said(link, "unreachable", str(e), rest, **gamma_sync.status_fields(e))
        except gamma_sync.SyncError as e:
            self.announced, rest = None, self.backoff
            self.backoff = min(self.backoff * 2, RETRY_MAX_S)
            return self._said(link, "unreachable", str(e), rest)
        self.backoff = RETRY_FIRST_S
        self._said(link, "connected", "", 0)
        if got.get("announce"):
            self.announced = None
        elif isinstance(got.get("wake"), list):
            if "sends" in got["wake"]:
                self._sends(server, link)
            if "chat" in got["wake"]:
                self._chats(server, link)
        # A poll answered at once (nothing, or announce again: a server that always asks so) rests a moment.
        return max(0.0, QUICK_S - (time.monotonic() - started))

    def _sends(self, server: gamma_sync.Server, link: dict) -> None:
        """The Sends that wait there for this computer's agents (``GET
        /api/computers/sends``: that server decides whose they are), each
        queued for the vault of its work folder unless it is paused or
        choosing its files (``runner.queue_remote``), and the runs whose
        request was withdrawn there meanwhile stopped
        (``runner.withdraw_remote``). Asked only where a vault of that
        server has an agent; a server without the route (older), or one not
        reached, is asked again at a later turn. Never raises."""
        from ..local_agents import runner

        self.sends_due, self.sends_at = False, time.monotonic()
        folders = agent_folders(link)
        if not folders:
            return
        try:
            got = _call(server, "GET", "/api/computers/sends")
        except (Older, gamma_sync.Denied):
            # A server from before agents ran there (404, or 403: its
            # channel opens only the announce and the poll): asked again
            # after SENDS_EVERY_S; a token it refuses fails the poll anyway.
            return
        except gamma_sync.SyncError as e:
            self.sends_due = True
            log.info(f"[computers] the Sends of channel {link['id']}: {e}")
            return
        try:
            for send in got.get("sends") if isinstance(got.get("sends"), list) else []:
                kept = folders.get(send.get("folder_id")) if isinstance(send, dict) else None
                if kept is None or not isinstance(send.get("id"), str) or not send["id"]:
                    continue
                vault = kept[0]
                if not vault["status"].get(folder_links.PAUSED) and not folder_links.choosing(vault):
                    runner.queue_remote(vault["id"], send["id"])
            ids = [vault["id"] for vault, _agent in folders.values()]
            for gone in got.get("withdrawn") if isinstance(got.get("withdrawn"), list) else []:
                if isinstance(gone, dict) and isinstance(gone.get("id"), str) and gone["id"]:
                    runner.withdraw_remote(ids, gone["id"], str(gone.get("run") or ""))
        except Exception:  # noqa: BLE001 — a Send never stops the channel
            log.exception(f"[computers] queueing the Sends of channel {link['id']}")

    def _chats(self, server: gamma_sync.Server, link: dict) -> None:
        """The chat turns that wait there for this computer's agents (``GET
        /api/computers/chats``: that server decides whose they are), each
        queued for the vault of its work folder (``runner.queue_remote_chat``:
        the turn finds the vault as it is when it runs, and fails with why
        when it is paused, as a turn here does), and the turns stopped there
        meanwhile, or no longer queued or running there, stopped
        (``runner.stop_remote_chat``); one stopped there that no vault here
        queues or runs (left open: its claim or its end never answered)
        ends stopped there and here (``runner.end_stopped_remote``), so its
        Stop never waits for the next announce. Asked only where a vault of that
        server has an agent; a server without the route (older), or one not
        reached, is asked again at a later turn. Never raises."""
        from ..local_agents import runner

        self.chats_due, self.chats_at = False, time.monotonic()
        folders = agent_folders(link)
        if not folders:
            return
        try:
            got = _call(server, "GET", "/api/computers/chats")
        except (Older, gamma_sync.Denied):
            return   # a server from before the chat ran there: asked again after CHATS_EVERY_S
        except gamma_sync.SyncError as e:
            self.chats_due = True
            log.info(f"[computers] the chat turns of channel {link['id']}: {e}")
            return
        try:
            for turn in got.get("turns") if isinstance(got.get("turns"), list) else []:
                kept = folders.get(turn.get("folder_id")) if isinstance(turn, dict) else None
                if kept is None or not isinstance(turn.get("id"), str) or not _TURN_ID.match(turn["id"]):
                    continue
                runner.queue_remote_chat(kept[0]["id"], turn["id"], agent=kept[1],
                                         conversation_id=str(turn.get("conversation_id") or ""),
                                         page_id=str(turn.get("page_id") or ""))
            ids = [vault["id"] for vault, _agent in folders.values()]
            everywhere = [vault["id"] for vault in _vaults_of(link)]
            for gone in got.get("stops") if isinstance(got.get("stops"), list) else []:
                if isinstance(gone, dict) and isinstance(gone.get("id"), str) and _TURN_ID.match(gone["id"]):
                    run = str(gone.get("run") or "")
                    if not runner.stop_remote_chat(ids, gone["id"], run):
                        runner.end_stopped_remote(everywhere, gone["id"], run)
        except Exception:  # noqa: BLE001 — a chat turn never stops the channel
            log.exception(f"[computers] queueing the chat turns of channel {link['id']}")

    def _settle(self, link: dict) -> None:
        """That server reached again: the runs of its vaults left open while
        it was not (an end or a claim never answered) fail there now
        (``runner.fail_remote_of``), not only at the next start. Never
        raises."""
        from .. import local_agents
        from ..local_agents import runner

        if not local_agents.enabled():
            return
        try:
            runner.fail_remote_of([vault["id"] for vault in _vaults_of(link)])
        except Exception:  # noqa: BLE001 — a run left open never stops the channel
            log.exception(f"[computers] the runs left open of channel {link['id']}")

    def _said(self, link: dict, state: str, error: str, rest: float, **fields) -> float:
        """The channel's status, with ``fields`` (``gamma_sync.status_fields``:
        what was in the way), written when it changed (and while the channel
        runs); ``rest`` passed through."""
        said = (link["status"].get("state"), link["status"].get("error", ""), link["status"].get("error_kind"))
        if not self.stopped.is_set() and said != (state, error, fields.get("error_kind")):
            link["status"] = {"state": state, "error": error, "at": page_now(), **fields}
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


def ask_sends() -> None:
    """A work folder of another server takes Sends again here (its agent
    turned on, the vault resumed or its files chosen): each channel asks
    its server for the Sends that wait once its poll ends."""
    with _guard:
        running = list(_channels.values())
    for channel in running:
        channel.sends_due = True
        channel.nudged.set()

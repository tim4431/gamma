"""The computer's side of its channel (gamma/computers/__init__.py), on the
desktop app's own server only (``config.folder_links_enabled``): this
computer announces itself to every other Gamma server its vaults name, and
holds a long-poll open to it, so that server can show the vaults' maker
where they are kept.

The credential is a token of that server of the ``computer`` scope, which
opens the channel and nothing else, minted there with the window's session
and handed over by the desktop app with the shell's word
(routers/computer_links.py), when a vault of that server is made or from
Settings → AI → Agents: one users.db ``computer_links`` row per server,
workspace there and local account, the token sealed
(``publisher_sessions.seal``, as a vault's is), the account there and the
workspace's name as the server named them, whether that server may answer
what this computer's agents ask (``asks``, ``set_asks``: off until the
shell's word turns it on, and announced), and the channel's ``status``,
``{state, error, at}`` and,
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
them, ``catalog.known``: a probe only when none was ever made, each with
whether it is on for the channel's account, what it may run and the people
of that workspace its owner lets use it, ``people``, and the directories
offered them, by id and name, ``dirs``), announces it when it differs from
what was last announced or the server asked, then polls (held up to
``POLL_S``). A vault added, paused or removed, an agent turned on or off, or
the directories offered changed, nudges the threads (``nudge``), which see
it at the end of the poll they hold, at most ``POLL_S`` later. Never a path.

The agents on here for the channel's account are that account's on every
bucket of that server's workspace (step 4.2 of
docs/research/agents-as-participants-plan.md): after each announce, on a
poll answered ``{wake: ["chat"]}`` and at least every ``CHATS_EVERY_S``,
the thread asks that server for the turns that wait for them (``_chats``),
queues each where it works here (gamma/local_agents/runner.py
``queue_remote_chat``: the vault of that server's work folder, a directory
offered, one of its own, each named by an id), stops the ones stopped
there and hands the running ones the answers given there to what they ask
(where ``asks`` lets that server answer: step 4.3); each turn speaks to
that server with this channel's token
(gamma/local_agents/home.py ``HttpChatHome``). The announce's assistants
name the models each offers (``models``), which that server offers the
chat's model chip and checks a turn's against. Each announce that succeeds
fails there the turns of this channel left open while that server was not
reached (``_settle``).

That server asks of the sessions of this computer's agents too (step 5 of
the plan: History's *On {computer}* there): woken ``{wake: ["sessions"]}``,
the thread takes the asks (``_sessions``) and answers each in a thread of
its own (``_answer_session``, gamma/local_agents/sessions.py
``answer_far``): the sessions of a directory named by an id, and one's
turns to open it there, whose conversation is then the session's here. The
announce says it answers them (``sessions``); a release before this step
never does.

A work folder of that server detached there (gamma/work_folders/detach.py)
wakes the poll with ``{wake: ["let_go"]}``: each work folder of that server
kept here has a round at once (``_let_go``), which finds the mark gone and
records it (``folder_links.LET_GO_THERE``); the desktop app forgets such a
vault at its next look. A computer offline then finds it at its next round.
"""

import base64
import hashlib
import hmac
import json
import re
import socket
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
CHATS_EVERY_S = 180      # the turns that wait there are asked for at least this often (a wake lost)
MAX_MODELS = 32          # models an assistant's announce names
_TURN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # a chat turn's id there (``blocks_store.new_block_id``'s)
_ANSWER = ("id", "run", "ask", "decision")        # an answer's fields there (``chats.answers_of``), and its note
_ASK_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")    # an ask's id there (gamma/computers/sessions.py)
STATES = ("connecting", "connected", "refused", "older", "unreachable")

_COLS = ("id, remote_url, workspace_id, created_by, token, token_id, account, username, created_at, status, asks, "
         "workspace_name")


class Older(gamma_sync.SyncError):
    """The server has no channel routes: a Gamma from before computers
    announced themselves."""


# --- the rows -------------------------------------------------------------------------------

def _row(r, with_token: bool = False) -> dict:
    link = {"id": r[0], "remote_url": r[1], "workspace_id": r[2], "created_by": r[3], "token_id": r[5],
            "account": r[6], "username": r[7], "created_at": r[8], "status": folder_links._object_of(r[9]),
            "asks": bool(r[10]), "workspace_name": r[11]}
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
    # Checked with no vault nor directory named and nothing answered there: whose token it is decides first
    # whether it may be told them (the thread does).
    answer = _call(server, "POST", "/api/computers/announce",
                   {**state_of({"remote_url": url, "workspace_id": workspace, "created_by": user_id}), "vaults": [],
                    "dirs": [], "asks": False})
    if (answer.get("workspace") or {}).get("id") != workspace:
        raise ValueError("The token belongs to another workspace of that server.")
    account = answer.get("account") or {}
    named = _workspace_name(answer)
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
            conn.execute("UPDATE computer_links SET token = ?, token_id = ?, account = ?, username = ?, status = ?, "
                         "workspace_name = ? WHERE id = ?",
                         (seal(token), token_id, str(account.get("id") or ""), str(account.get("username") or ""),
                          status, named, link_id))
        else:
            conn.execute(f"INSERT INTO computer_links ({_COLS}) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                         (link_id, url, workspace, user_id, seal(token), token_id, str(account.get("id") or ""),
                          str(account.get("username") or ""), page_now(), status, named))
    stop(link_id)
    start(link_id)
    return get(link_id), {"token_id": old[1]} if old and old[1] != token_id else None


def _workspace_name(answer: dict) -> str:
    """The workspace's name an announce's answer gives (``{workspace: {id,
    name}}``), "" from a server that names none."""
    name = (answer.get("workspace") or {}).get("name")
    return name.strip()[:200] if isinstance(name, str) else ""


def delete(link_id: str, user_id: str) -> str | None:
    """The account's channel ``link_id`` stops and is forgotten: its token's
    id there (the desktop app revokes it, and that server then fails what
    the token's runs held: registry.py ``_fail_held``), None when it is no
    channel of that account. The turns of that server run here stop
    (``unlinked``), since no write of theirs reaches it any more, and the
    directories of their own its conversations had here go
    (``home.remove_far_dirs``). The agents stay the computer's: that
    server, no longer told of this computer, gives it no work."""
    from .. import local_agents
    from ..local_agents import home, runner

    link = get(link_id)
    if link is None or link["created_by"] != user_id:
        return None
    stop(link_id)
    if local_agents.enabled():
        runner.stop_channel(link_id, "unlinked")
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE id = ?", (link_id,))
    home.remove_far_dirs(link_id)
    return link["token_id"]


def set_asks(link_id: str, user_id: str, asks: bool) -> dict | None:
    """Whether the server of the account's channel ``link_id`` may answer
    what this computer's agents ask (decision 7 of
    docs/research/agents-as-participants-plan.md): kept, announced at the
    channel's next turn (``nudge``), and the messages there of its turns
    running here written again with what they ask or without it
    (``runner.relays_changed``) → the link, None when it is no channel of
    that account."""
    from .. import local_agents
    from ..local_agents import runner

    link = get(link_id)
    if link is None or link["created_by"] != user_id:
        return None
    if link["asks"] != asks:
        with connect_users_db() as conn:
            conn.execute("UPDATE computer_links SET asks = ? WHERE id = ?", (int(asks), link_id))
        nudge()
        if local_agents.enabled():
            runner.relays_changed(link_id)
    return get(link_id)


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


def assistants(link: dict) -> list[dict]:
    """The agents on this computer as the catalog last found them
    (``catalog.known``: one probe when none was ever made, never one to
    refresh it, so a channel keeps no agent probed), each with the models
    it offers (``[{id, label, efforts, effort, default, speeds, speed}]``, at most
    ``MAX_MODELS``: what a chat turn there may ask for), whether it is on
    for the channel ``link``'s account (``enabled``: on, and that account
    turned it on; another account's agent is not that server's to use),
    what it may run then (``runs``, ``never`` or ``ask``) and the people of
    that server's workspace its owner lets use it (``people``, their account
    ids there: ``store.people``); ``[]`` where agents do not run
    (``local_agents.enabled``)."""
    from .. import local_agents
    from ..local_agents import catalog, store

    if not local_agents.enabled():
        return []
    rows = store.enabled_agents()
    there = []
    for name in catalog.AGENTS:
        found = catalog.known(name)
        on = name in rows and rows[name]["set_by"] == link["created_by"]
        people = [p["account"] for p in store.people(name)
                  if (p["server"], p["workspace_id"]) == (link["remote_url"], link["workspace_id"])] if on else []
        there.append({"name": name, "label": found["label"], "found": found["found"], "ready": found["ready"],
                      "version": (found["version"] or "")[:64], "code": catalog.code(name, found),
                      "models": list(found.get("models") or [])[:MAX_MODELS], "enabled": on,
                      "runs": rows[name]["runs"] if on else "never", "people": people})
    return there


def members(link: dict) -> list[dict]:
    """The people of the channel ``link``'s workspace there who may use an
    agent of its account's (``GET /api/computers/people``: editors and
    owners, the account itself left out): ``[{id, username, role}]``.
    ``gamma_sync.SyncError`` (``Older`` for a server without the route)
    when that server does not answer it."""
    found = get(link["id"], with_token=True)
    if found is None:
        raise gamma_sync.Unreachable("this computer is no longer shown there")
    server = gamma_sync.Server(found["remote_url"], found["token"], timeout=folder_links.ASK_TIMEOUT_S)
    answer = _call(server, "GET", "/api/computers/people")
    people = answer.get("people") if isinstance(answer.get("people"), list) else []
    return [{"id": p["id"], "username": str(p.get("username") or "")[:64], "role": str(p.get("role") or "")}
            for p in people if isinstance(p, dict) and isinstance(p.get("id"), str) and p["id"]]


def vaults_of(link: dict) -> list[dict]:
    """The vaults of the channel ``link``'s server, workspace and account."""
    return [vault for vault in folder_links.all_links()
            if (vault["remote_url"], vault["workspace_id"], vault["created_by"])
            == (link["remote_url"], link["workspace_id"], link["created_by"])]


def agent_folders(link: dict) -> dict[str, tuple[dict, str]]:
    """``{folder id: (vault, agent)}``: the work folders of the channel
    ``link``'s server that a vault here keeps (``folder_links.work_vault``),
    paused or not, each with the first of the channel's account's agents on
    here (``store.agent_for``), which the vault names for an older server
    (``state_of``); none while it has none, and where agents do not run
    (``local_agents.enabled``)."""
    from .. import local_agents
    from ..local_agents import store

    if not local_agents.enabled():
        return {}
    agent = store.agent_for(link["created_by"])
    if agent is None:
        return {}
    return {vault["folder_id"]: (vault, agent["agent"]) for vault in vaults_of(link)
            if vault["remote_url"] and folder_links.work_vault(vault)}


def state_of(link: dict) -> dict:
    """What this computer announces on the channel ``link``: its id for
    that server and its name, the app's version, the vaults of that server,
    workspace and account (``{kind, folder_id | page_id, paused}``, the
    pause as the sync panel shows it), the assistants, each with
    whether it is on for the channel's account (``assistants``): that
    server offers them to that account on its buckets
    (gamma/computers/registry.py), whether that server may answer what
    they ask (``asks``, the link's), the directories offered them
    (``dirs``, ``offered``), and that it answers what that server asks of
    its agents' sessions (``sessions``, ``_sessions``). A work folder's
    vault also names its agent (``agent``) for an older server, which reads
    it from there."""
    vaults, agents = [], agent_folders(link)
    for vault in vaults_of(link):
        key, target = ("folder_id", vault["folder_id"]) if vault["kind"] == "folder" else ("page_id", vault["page_id"])
        vaults.append({"kind": vault["kind"], key: target, "paused": bool(vault["status"].get(folder_links.PAUSED))})
        if vault["kind"] == "folder" and vault["folder_id"] in agents:
            vaults[-1]["agent"] = agents[vault["folder_id"]][1]
    return {"computer": {"id": computer_id(link["remote_url"]), "name": computer_name()},
            "app": version.label()[:64], "vaults": vaults, "assistants": assistants(link),
            "asks": bool(link.get("asks")), "dirs": offered(link), "sessions": True}


def offered(link: dict) -> list[dict]:
    """The directories this computer offers its agents (``store.dirs``), by
    id and name, never a path, while the channel ``link``'s account has one
    on here: what a conversation there may work in besides a vault
    (gamma/local_agents/home.py ``far_place``); ``[]`` otherwise, and where
    agents do not run."""
    from .. import local_agents
    from ..local_agents import store

    if not local_agents.enabled() or store.agent_for(link["created_by"]) is None:
        return []
    return [{"id": entry["id"], "name": entry["name"]} for entry in store.dirs()]


def computer_name() -> str:
    """This computer's name as every server sees it: its host name, or
    "This computer" for one that cannot be shown. Reads no setting, so a
    users.db step names it too (gamma/migrations.py)."""
    name = socket.gethostname()[:64].strip()
    return name if name.isprintable() and name else "This computer"


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
        self.chats_due = True       # the turns that wait there are asked for at the next turn (``_chats``)
        self.chats_at = 0.0         # time.monotonic() when they were last asked for
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
                self._account(link, answer)
                self._said(link, "connected", "", 0)   # shown there now, before the poll's hold
                self.chats_due = True       # what waited for this computer while it was away
                self._settle(link)
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
            if "chat" in got["wake"]:
                self._chats(server, link)
            if "let_go" in got["wake"]:
                self._let_go(link)
            if "sessions" in got["wake"]:
                self._sessions(server, link)
        # A poll answered at once (nothing, or announce again: a server that always asks so) rests a moment.
        return max(0.0, QUICK_S - (time.monotonic() - started))

    def _chats(self, server: gamma_sync.Server, link: dict) -> None:
        """The turns that wait there for this computer's agents (``GET
        /api/computers/chats``: that server decides whose they are), each
        of an agent of the catalog queued where it works here
        (``runner.queue_remote_chat``, with the id the listing names it by:
        the turn finds the place as it is when it runs, and fails with why
        when it is no longer kept or offered, or paused, as a turn here
        does), and the turns stopped there meanwhile, or no longer queued or
        running there, stopped (``runner.stop_remote_chat``); one stopped
        there that this channel neither queues nor runs (left open: its
        claim or its end never answered) ends stopped there and here
        (``runner.end_stopped_remote``), so its Stop never waits for the
        next announce; the answers that server keeps for what the turns
        running here ask (``answers``, where this channel relays asks)
        handed to them (``runner.answer_relayed``: the first answer is the
        one); the messages steered into them (``steers``), handed to their
        agents (``runner.steer_relayed``). A server from before the agents' buckets lists a turn by its
        work folder alone: it is queued in the vault here that keeps that
        folder, with the agent the vault names (``agent_folders``). Asked
        only while the channel's account has an agent
        on here; a server without the route (older), or one not reached, is
        asked again at a later turn. Never raises."""
        from .. import local_agents
        from ..local_agents import catalog, chats, runner, store

        self.chats_due, self.chats_at = False, time.monotonic()
        if not local_agents.enabled() or store.agent_for(link["created_by"]) is None:
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
            folders = agent_folders(link)
            for turn in got.get("turns") if isinstance(got.get("turns"), list) else []:
                if not isinstance(turn, dict) or not isinstance(turn.get("id"), str) or not _TURN_ID.match(turn["id"]):
                    continue
                agent, bucket, dir = turn.get("agent"), turn.get("bucket"), turn.get("dir")
                if "agent" not in turn:   # a server from before the agents' buckets: {id, conversation_id, page_id, folder_id, at}
                    kept = folders.get(turn.get("folder_id"))
                    if kept is None:
                        continue
                    agent, bucket, dir = kept[1], turn.get("page_id"), {"kind": "vault", "id": turn["folder_id"]}
                if agent not in catalog.AGENTS:
                    continue
                runner.queue_remote_chat(link["id"], turn["id"], agent=agent,
                                         conversation_id=str(turn.get("conversation_id") or ""),
                                         bucket=str(bucket or ""), dir=dir)
            for gone in got.get("stops") if isinstance(got.get("stops"), list) else []:
                if isinstance(gone, dict) and isinstance(gone.get("id"), str) and _TURN_ID.match(gone["id"]):
                    run = str(gone.get("run") or "")
                    if not runner.stop_remote_chat(link["id"], gone["id"], run):
                        runner.end_stopped_remote(link["id"], gone["id"], run)
            for said in got.get("answers") if isinstance(got.get("answers"), list) else []:
                if isinstance(said, dict) and all(isinstance(said.get(k), str) for k in _ANSWER):
                    runner.answer_relayed(link["id"], said["id"], said["run"], said["ask"], said["decision"],
                                          said["note"] if isinstance(said.get("note"), str) else "")
            for sent in got.get("steers") if isinstance(got.get("steers"), list) else []:
                steer = chats.steer_of(sent["steer"]) if isinstance(sent, dict) and isinstance(
                    sent.get("steer"), dict) and all(isinstance(sent.get(k), str) for k in ("id", "run")) else None
                if steer:
                    runner.steer_relayed(link["id"], sent["id"], sent["run"], steer)
        except Exception:  # noqa: BLE001 — a chat turn never stops the channel
            log.exception(f"[computers] queueing the chat turns of channel {link['id']}")

    def _sessions(self, server: gamma_sync.Server, link: dict) -> None:
        """What that server asks of the sessions of this computer's agents
        (``{wake: ["sessions"]}``: ``GET /api/computers/sessions``,
        History's *On {computer}* there), each answered in a thread of its
        own (``_answer_session``), so a slow listing never holds the poll.
        Nothing where agents do not run; never raises."""
        from .. import local_agents

        if not local_agents.enabled():
            return
        try:
            got = _call(server, "GET", "/api/computers/sessions")
        except gamma_sync.SyncError as e:
            log.info(f"[computers] the session asks of channel {link['id']}: {e}")
            return
        for ask in got.get("asks") if isinstance(got.get("asks"), list) else []:
            if isinstance(ask, dict) and isinstance(ask.get("id"), str) and _ASK_ID.match(ask["id"]):
                threading.Thread(target=_answer_session, args=(link["id"], ask), daemon=True,
                                 name=f"computer-sessions-{link['id'][:8]}").start()

    def _let_go(self, link: dict) -> None:
        """That server detached a work folder a vault here keeps (``{wake:
        ["let_go"]}``): each work folder of that server kept here, unless
        paused, has a round now, in the background, which finds its mark
        gone there (``folder_links.LET_GO_THERE``) and stops, its agent's run
        too; the desktop app then forgets that vault as Detach… does. A paused one
        finds it at its next round, as the tick holds. Never raises."""
        try:
            for vault in vaults_of(link):
                if folder_links.work_vault(vault) and not vault["status"].get(folder_links.PAUSED):
                    folder_links.run_in_background(vault["id"])
        except Exception:  # noqa: BLE001 — a round never stops the channel
            log.exception(f"[computers] the rounds of channel {link['id']} after a detach there")

    def _settle(self, link: dict) -> None:
        """That server reached again: the turns of this channel left open
        while it was not (an end or a claim never answered) fail there now
        (``runner.fail_remote_of``), not only at the next start. Never
        raises."""
        from .. import local_agents
        from ..local_agents import runner

        if not local_agents.enabled():
            return
        try:
            runner.fail_remote_of(link["id"])
        except Exception:  # noqa: BLE001 — a turn left open never stops the channel
            log.exception(f"[computers] the turns left open of channel {link['id']}")

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

    def _account(self, link: dict, answer: dict) -> None:
        """The account there and the workspace's name an announce's
        ``answer`` named, kept when they changed (a server that names no
        workspace keeps the name it had)."""
        account = answer.get("account") or {}
        found = (str(account.get("id") or ""), str(account.get("username") or ""),
                 _workspace_name(answer) or link["workspace_name"])
        if found != (link["account"], link["username"], link["workspace_name"]) and not self.stopped.is_set():
            with connect_users_db() as conn:
                conn.execute("UPDATE computer_links SET account = ?, username = ?, workspace_name = ? WHERE id = ?",
                             (*found, link["id"]))


def _answer_session(link_id: str, ask: dict) -> None:
    """This computer's answer to an ask of its channel ``link_id``'s server
    of its agent's sessions (gamma/local_agents/sessions.py ``answer_far``:
    for the channel's account alone, in a directory named by an id),
    written there (``POST /api/computers/sessions/{id}``). Never raises."""
    from ..local_agents import sessions

    link = get(link_id, with_token=True)
    if link is None or not link["token"]:
        return
    try:
        answer = sessions.answer_far(link, ask)
    except Exception:  # noqa: BLE001 — that server is told, and the channel goes on
        log.exception(f"[computers] answering a session ask of channel {link_id}")
        answer = {"error": "This computer could not read its sessions.", "status": 409}
    server = gamma_sync.Server(link["remote_url"], link["token"], timeout=folder_links.ASK_TIMEOUT_S)
    try:
        _call(server, "POST", f"/api/computers/sessions/{ask['id']}", answer)
    except gamma_sync.SyncError as e:
        log.info(f"[computers] the answer to a session ask of channel {link_id}: {e}")


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

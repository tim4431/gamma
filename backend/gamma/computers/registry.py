"""The server's side of a computer's channel, on every Gamma server
(gamma/computers/__init__.py): what each computer announced and whether it
is online now.

A computer speaks with an integration token of the ``computer`` scope
(gamma/integrations.py), which opens the channel's routes and nothing
else (``auth.CHANNEL_PREFIX``). What it last announced is one users.db
``computers`` row per token: its id (one per server: the computer's HMAC
of its install id and this server's origin, so two servers cannot match
one computer by it), its name, the folders and pages of the token's
workspace it keeps (ids only, ``vaults``, each paused or not), the
assistants there (``assistants``: name, label, found, ready, version, a
problem's code, never its words, the models it offers, whether it is on
for the token's account and what it may run, and the people of the token's
workspace its owner lets use it, by their account ids here: ``people``),
the app's version, when it was last seen (``seen_at``), so "offline
since 14:02" survives a restart, whether the computer lets this server
answer what its agents ask (``asks``, its link's switch there), and the
directories it offers its agents there (``dirs``: ids and names). Never a
path. The agents a computer has on for the token's account are that
account's on every bucket of the workspace (``seats``, step 4.2 of
docs/research/agents-as-participants-plan.md), working in the vault of the
work folder the bucket is in where the computer keeps it, else in a
directory it offers, else in one of its own; a co-author's is the first
assistant ``enabled`` (``_agent``), on a work folder's files alone.

Whether it is online is in memory (``_live``): a poll is held now, or the
last one ended (or the computer announced) less than ``GRACE_S`` ago. A
poll that ends arms a timer; a new poll cancels it; when it fires the
computer is offline, ``seen_at`` is written and the account's windows are
told (``{t: "computer"}``, content-free, to that account's sockets alone:
``collab.publish_account``). The same nudge goes when an announce changes
what a window shows. A poll is answered ``{announce: true}`` when the
computer is unknown here (a restart) or announced another version; held
polls are woken by ``wake``: to announce again, ``chat`` when a turn of its
account's agent there (a chat's, a comment's sent to it) is begun or asked
to stop (step 4d), ``let_go`` when a work folder
it keeps is detached here (gamma/work_folders/detach.py, ``keepers``), or
``sessions`` when this server asks of its agents' sessions
(gamma/computers/sessions.py, which only a computer that says it answers
is asked: ``answers``, in memory), a wake while no poll is held answering
the next.

Only the token's account sees its computer (``of_page``, ``of_folders``,
``of_account``), and a person its agent names, who sees that agent alone
and the work folders it keeps (``_lent_rows``: a co-author, decision 13 of
docs/research/agents-as-participants-plan.md); a revoked or expired token's
row goes (``forget``, and when the rows are read).
"""

import asyncio
import json
import os
import re
import secrets
import threading
import time

from .. import collab, workspaces
from ..blocks_store import folder_chain, page_in_folder, page_top
from ..db import connect_pages_db, connect_users_db, page_now
from ..folder_sync import ROOT
from ..logbuf import log
from ..work_folders.marks import Tree

POLL_S = 25            # the longest a poll is held: under a proxy's idle limit (nginx 60 s, Cloudflare 100 s)
GRACE_S = float(os.environ.get("GAMMA_COMPUTER_GRACE_S") or 45)  # online this long after the last poll ends
SEEN_EVERY_S = 600     # while online, ``seen_at`` is written at most this often
MAX_VAULTS = 500       # folders and pages one announce names
MAX_TEXT = 64          # a name, a label, a version, the app's version
MAX_ID = 128           # a folder's or a page's id
MAX_MODELS = 32        # models one assistant announces
MAX_EFFORTS = 8        # efforts one model announces
MAX_PEOPLE = 50        # people one assistant names (local_agents.store.MAX_PEOPLE)
MAX_DIRS = 50          # directories one announce offers (local_agents.store.MAX_DIRS)
MAX_DIR_NAME = 80      # characters of an offered directory's name (local_agents.store.MAX_DIR_NAME)
AGENTS = ("codex", "claude")                     # the catalog's, in its order
RUNS = ("never", "ask")                          # what an assistant may run (local_agents.store.RUNS)
CODES = ("", "missing", "signed_out", "not_ready", "checking")   # an assistant's problem, as a code
NUDGE = {"t": "computer"}
_COMPUTER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
DIR_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")   # an offered directory's id (local_agents.store.dirs)


class _Live:
    """What the server holds of a computer while it runs."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.version = ""          # the last announce's: a poll naming another is asked to announce
        self.holding = 0           # polls held now
        self.last = None           # time.monotonic() when the last poll ended, or the last announce
        self.ended = ""            # that moment, as ``seen_at`` keeps it
        self.waiters = []          # the held polls: (their loop, their asyncio.Event, the reasons they were woken)
        self.pending = []          # the reasons of a wake while no poll was held: the next one answers them at once
        self.timer = None          # the grace timer, armed while no poll is held
        self.armed = 0             # the timer's generation: one that fires after a newer one was armed does nothing
        self.written = 0.0         # time.monotonic() when ``seen_at`` was last written
        self.shown = False         # whether the account's windows were last told it is online
        self.sessions = False      # whether its last announce says it answers the asks of its sessions
        self.skills = False        # whether it says it runs a turn's skill (the instructions with the claim)


_live: dict[str, _Live] = {}
_lock = threading.Lock()


def online(live: _Live | None) -> bool:
    return live is not None and (live.holding > 0 or live.last is not None
                                 and time.monotonic() - live.last < GRACE_S)


def _text(value, field: str, *, required: bool = False) -> str:
    if value is None and not required:
        return ""
    if not isinstance(value, str):
        raise ValueError(f"{field} is text.")
    value = value.strip()[:MAX_TEXT]
    if required and not value:
        raise ValueError(f"{field} is missing.")
    return value


def _flag(value, field: str) -> bool | None:
    if value is not None and not isinstance(value, bool):
        raise ValueError(f"{field} is true, false or null.")
    return value


def _models(value) -> list[dict]:
    """An assistant's ``models`` as kept (step 4d, the chat's model chip):
    ``[{id, label, efforts, effort, default, speeds, speed}]``
    (``events.model``: the speeds those it knows), at most ``MAX_MODELS``
    with ``MAX_EFFORTS`` efforts each, one without an id left out (an older
    computer announces no speeds: its models offer none). ValueError for
    another shape."""
    from ..local_agents import events  # local: the agents' package is loaded where an agent is named

    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_MODELS:
        raise ValueError(f"An assistant's models are a list of at most {MAX_MODELS}.")
    kept = []
    for item in value:
        if not isinstance(item, dict):
            raise ValueError("A model is an object.")
        efforts = item.get("efforts")
        found = events.model(item.get("id"), item.get("label"), efforts[:MAX_EFFORTS] if isinstance(efforts, list)
                             else [], item.get("effort"), item.get("default"), item.get("speeds"), item.get("speed"))
        if found is not None and all(m["id"] != found["id"] for m in kept):
            kept.append(found)
    return kept


def _people_of(value) -> list[str]:
    """An assistant's ``people`` as kept: the account ids of the token's
    workspace its owner lets use it, at most ``MAX_PEOPLE``, each once.
    ValueError for another shape."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_PEOPLE:
        raise ValueError(f"An assistant's people are a list of at most {MAX_PEOPLE}.")
    if not all(isinstance(v, str) and 1 <= len(v) <= MAX_ID for v in value):
        raise ValueError("A person is named by an account's id.")
    return list(dict.fromkeys(value))


def _dirs_of(value) -> list[dict]:
    """The directories the computer offers its agents there, as kept:
    ``[{id, name}]``, at most ``MAX_DIRS``, each id once, the name on one
    line and cut: an id and a name, never a path. ValueError for another
    shape."""
    if value is None:
        return []
    if not isinstance(value, list) or len(value) > MAX_DIRS:
        raise ValueError(f"The offered directories are a list of at most {MAX_DIRS}.")
    kept: dict[str, dict] = {}
    for item in value:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str) or not DIR_ID.match(item["id"]):
            raise ValueError("An offered directory is named by its id: 1 to 64 letters, digits, - or _.")
        if not isinstance(item.get("name"), str):
            raise ValueError("An offered directory's name is text.")
        kept.setdefault(item["id"], {"id": item["id"], "name": " ".join(item["name"].split())[:MAX_DIR_NAME]})
    return list(kept.values())


def normalise(body) -> dict:
    """The announce ``body`` as kept: ``{computer: {id, name}, app, vaults,
    assistants, asks, dirs}``, each string capped, the vaults deduplicated, an
    assistant this server does not know of dropped, an unknown problem code
    read as ``not_ready``, each with the ``models`` it offers there
    (``_models``), whether it is on (``enabled``), what it may run then
    (``runs``, ``never`` or ``ask``; anything else ``never``) and the people
    it serves beside its owner (``people``, ``_people_of``; none while it is
    off), whether the computer lets this server answer what its agents
    ask (``asks``, false from an app that says nothing of it) and the
    directories it offers them (``dirs``, ``_dirs_of``; none while no
    agent is on), and whether it answers what this server asks of its
    agents' sessions (``sessions``: gamma/computers/sessions.py; false from
    an app before that) and whether it runs a turn's skill (``skills``:
    the instructions the claim carries, ``far_turns.claim``; false from an
    app before that, which would run the arguments alone). An older app's announce, whose assistants carry no
    ``enabled``, has on the agent its folder vaults name. ValueError for a
    body of another shape, and for this server's own computer id (a far
    turn's token would then write as this computer's agent)."""
    if not isinstance(body, dict) or not isinstance(body.get("computer"), dict):
        raise ValueError("The announce names its computer.")
    computer_id, name = body["computer"].get("id"), body["computer"].get("name")
    if not isinstance(computer_id, str) or not _COMPUTER_ID.match(computer_id):
        raise ValueError("The computer's id is 1 to 64 letters, digits, - or _.")
    from .. import agents  # local: agents names a far token's computer through this module

    if computer_id == agents.here_id():
        raise ValueError("The computer's id is this server's own.")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_TEXT or not name.strip().isprintable():
        raise ValueError("The computer's name is 1 to 64 printable characters.")
    vaults, assistants = body.get("vaults", []), body.get("assistants", [])
    if not isinstance(vaults, list) or len(vaults) > MAX_VAULTS:
        raise ValueError(f"The vaults are a list of at most {MAX_VAULTS}.")
    if not isinstance(assistants, list) or len(assistants) > 2 * len(AGENTS):
        raise ValueError("The assistants are a short list.")
    # An older app names no assistant ``enabled`` and puts its agent on the
    # folder vaults it works in instead: that agent is on, never asking.
    legacy = not any(isinstance(a, dict) and "enabled" in a for a in assistants)
    named = {v.get("agent") for v in vaults if isinstance(v, dict) and v.get("kind") == "folder"} if legacy else set()
    old = next((name for name in AGENTS if name in named), None)
    kept, seen = [], set()
    for vault in vaults:
        if not isinstance(vault, dict) or vault.get("kind") not in ("folder", "file"):
            raise ValueError("A vault is a folder or a file.")
        key = "folder_id" if vault["kind"] == "folder" else "page_id"
        target = vault.get(key)
        if not isinstance(target, str) or not 1 <= len(target) <= MAX_ID:
            raise ValueError(f"A {vault['kind']} vault names its {key}.")
        if (vault["kind"], target) not in seen:
            seen.add((vault["kind"], target))
            kept.append({"kind": vault["kind"], key: target, "paused": bool(_flag(vault.get("paused"), "paused"))})
    there = []
    for agent in assistants:
        if not isinstance(agent, dict):
            raise ValueError("An assistant is an object.")
        if agent.get("name") not in AGENTS or any(a["name"] == agent["name"] for a in there):
            continue
        code = agent.get("code") or ""
        on = agent["name"] == old if legacy else bool(_flag(agent.get("enabled"), "enabled"))
        there.append({"name": agent["name"], "label": _text(agent.get("label"), "label") or agent["name"],
                      "found": _flag(agent.get("found"), "found"), "ready": _flag(agent.get("ready"), "ready"),
                      "version": _text(agent.get("version"), "version"),
                      "code": code if code in CODES else "not_ready", "models": _models(agent.get("models")),
                      "enabled": on, "runs": agent.get("runs") if on and agent.get("runs") in RUNS else "never",
                      "people": _people_of(agent.get("people")) if on else []})
    dirs = _dirs_of(body.get("dirs"))
    return {"computer": {"id": computer_id, "name": name.strip()}, "app": _text(body.get("app"), "app"),
            "vaults": kept, "assistants": there, "asks": bool(_flag(body.get("asks"), "asks")),
            "dirs": dirs if any(a["enabled"] for a in there) else [],
            "sessions": bool(_flag(body.get("sessions"), "sessions")),
            "skills": bool(_flag(body.get("skills"), "skills"))}


def _kept_here(ws: str, vaults: list[dict]) -> list[dict]:
    """The vaults of ``vaults`` the workspace has: a live folder (or the
    whole library), a page of the library."""
    with connect_pages_db(ws) as conn:
        def here(vault: dict) -> bool:
            if vault["kind"] == "folder":
                return vault["folder_id"] == ROOT or bool(conn.execute(
                    "SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = 'folders'",
                    (vault["folder_id"],)).fetchone())
            row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (vault["page_id"],)).fetchone()
            return bool(row) and row[0] == "root"
        return [vault for vault in vaults if here(vault)]


def _arm(token_id: str, live: _Live) -> None:
    """The grace timer from now, unless a poll is held. Under ``_lock``."""
    live.armed += 1
    if live.timer is not None:
        live.timer.cancel()
        live.timer = None
    if live.holding:
        return
    live.timer = threading.Timer(GRACE_S, _expire, (token_id, live, live.armed))
    live.timer.daemon = True
    live.timer.start()


def _beat(token_id: str, live: _Live) -> bool:
    """The computer was heard from now: whether it turned online by it.
    Under ``_lock``."""
    live.last, live.ended = time.monotonic(), page_now()
    _arm(token_id, live)
    came, live.shown = not live.shown, True
    return came


def _expire(token_id: str, live: _Live, armed: int) -> None:
    """The grace timer fired: the computer is offline since its last poll
    ended, unless a poll came meanwhile."""
    with _lock:
        if _live.get(token_id) is not live or live.armed != armed or live.holding or not live.shown:
            return
        live.shown, live.timer = False, None
        ended = live.ended
    _write_seen(token_id, ended)
    collab.publish_account(live.user_id, NUDGE)


def _write_seen(token_id: str, at: str) -> None:
    if at:
        with connect_users_db() as conn:
            conn.execute("UPDATE computers SET seen_at = ? WHERE token_id = ?", (at, token_id))


def announce(user_id: str, username: str, ws: str, token_id: str, body) -> dict:
    """What the computer behind ``token_id`` (the account ``user_id``'s, of
    the workspace ``ws``) announced, kept: ``normalise``'s, of the vaults
    only those the workspace has. The account's windows are told when what
    they show changed (online now, the name, the vaults, the assistants,
    whether this server may answer what they ask, the directories offered).
    ``{account: {id, username}, computer: {id, name}, workspace: {id,
    name}, version, wait}``: ``name`` the workspace's, which the computer's
    Settings shows, ``version`` what its polls name, ``wait`` the longest
    hold. The turns queued for that computer that it would no longer take
    (its agent turned off, a person it no longer serves, a work folder or a
    directory it no longer keeps) fail (``_end_orphaned``). ValueError for
    a malformed body."""
    found = normalise(body)
    found["vaults"] = _kept_here(ws, found["vaults"])
    shown = (found["computer"]["name"], json.dumps(found["vaults"]), json.dumps(found["assistants"]),
             int(found["asks"]), json.dumps(found["dirs"]))
    now = page_now()
    with connect_users_db() as conn:
        before = conn.execute("SELECT name, vaults, assistants, asks, dirs FROM computers WHERE token_id = ?",
                              (token_id,)).fetchone()
        conn.execute("INSERT INTO computers (token_id, user_id, workspace_id, computer, name, vaults, assistants, asks, "
                     "dirs, app, seen_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(token_id) "
                     "DO UPDATE SET user_id = excluded.user_id, workspace_id = excluded.workspace_id, "
                     "computer = excluded.computer, name = excluded.name, vaults = excluded.vaults, "
                     "assistants = excluded.assistants, asks = excluded.asks, dirs = excluded.dirs, "
                     "app = excluded.app, seen_at = excluded.seen_at",
                     (token_id, user_id, ws, found["computer"]["id"], *shown, found["app"], now, now))
    with _lock:
        live = _live.get(token_id)
        if live is None:
            live = _live[token_id] = _Live(user_id)
        live.version, live.written = secrets.token_urlsafe(8), time.monotonic()
        live.sessions, live.skills = found["sessions"], found["skills"]
        live.pending = [reason for reason in live.pending if reason != "announce"]
        version = live.version
        came = _beat(token_id, live)
    if came or before is None or tuple(before) != shown:
        collab.publish_account(user_id, NUDGE)
        for person in _people(found["assistants"]) | (_people(_loads(before[2])) if before is not None else set()):
            collab.publish_account(person, NUDGE)
    if before is not None and tuple(before) != shown:
        _end_orphaned(ws, user_id, found["computer"]["id"])
    return {"account": {"id": user_id, "username": username}, "computer": found["computer"],
            "workspace": {"id": ws, "name": (workspaces.get(ws) or {}).get("name") or ""},
            "version": version, "wait": POLL_S}


def hold(token_id: str, version: str):
    """A poll begins, on the running event loop: ``(live, waiter)`` to wait
    on (``waiter`` is ``(loop, event, reasons)``) and release, else None
    when the computer is unknown here or ``version`` is not its announce's.
    The account's windows are told when the computer turned online by it."""
    loop = asyncio.get_running_loop()
    with _lock:
        live = _live.get(token_id)
        if live is None or not version or version != live.version:
            return None
        waiter = (loop, asyncio.Event(), live.pending)
        live.pending = []
        if waiter[2]:
            waiter[1].set()    # woken while no poll was held: answered at once
        live.waiters.append(waiter)
        live.holding += 1
        _arm(token_id, live)   # a hold disarms it
        came, live.shown = not live.shown, True
    if came:
        collab.publish_account(live.user_id, NUDGE)
    return live, waiter


def release(token_id: str, live: _Live, waiter) -> bool:
    """The poll ended: the grace timer runs from now once no poll is held.
    Whether ``seen_at`` is due to be written (``seen``)."""
    with _lock:
        if waiter in live.waiters:
            live.waiters.remove(waiter)
        live.holding = max(0, live.holding - 1)
        _beat(token_id, live)
        due = live.last - live.written >= SEEN_EVERY_S
        if due:
            live.written = live.last
        return due


def seen(token_id: str) -> None:
    """``seen_at`` written now, while the computer is online."""
    _write_seen(token_id, page_now())


def answer(reasons: list) -> dict:
    """A held poll's answer: ``{announce: true}`` when woken for it,
    ``{wake: [...]}`` for the rest, ``{}`` when nothing woke it."""
    if "announce" in reasons or "gone" in reasons:
        return {"announce": True}
    return {"wake": list(dict.fromkeys(reasons))} if reasons else {}


def wake(token_id: str, reason: str) -> None:
    """Answer the computer's held polls now, with ``reason``: ``announce``
    to have it announce again, ``chat`` for its turns, ``sessions`` for
    what this server asks of its agents' sessions, or ``let_go`` for a
    work folder it keeps
    that was detached; while none is held, its next poll is answered at
    once. Safe from any thread."""
    with _lock:
        live = _live.get(token_id)
        waiters = list(live.waiters) if live else []
        if live is not None and not waiters and reason != "gone" and reason not in live.pending:
            live.pending.append(reason)
    for loop, event, reasons in waiters:
        reasons.append(reason)
        try:
            loop.call_soon_threadsafe(event.set)
        except RuntimeError:  # the loop closed
            pass


def forget(token_id: str) -> None:
    """The computer behind ``token_id`` goes: its row, its presence; its
    held polls end (and are refused, the token being gone), the turns its
    runs still hold fail (``_fail_held``: no run of it can write them any
    more), the turns queued for that computer fail unless another token
    of it there takes them (``_end_orphaned``: the shell's new token for a
    link announces before the old one is revoked), and the account's
    windows are told."""
    wake(token_id, "gone")
    with _lock:
        live = _live.pop(token_id, None)
        if live is not None and live.timer is not None:
            live.timer.cancel()
    with connect_users_db() as conn:
        row = conn.execute("SELECT user_id, workspace_id, computer, assistants FROM computers WHERE token_id = ?",
                           (token_id,)).fetchone()
        conn.execute("DELETE FROM computers WHERE token_id = ?", (token_id,))
    if row:
        _fail_held(row[1], token_id)
        _end_orphaned(row[1], row[0], row[2])
        for person in _people(_loads(row[3])):
            collab.publish_account(person, NUDGE)
    if row or live is not None:
        collab.publish_account(row[0] if row else live.user_id, NUDGE)


def _fail_held(ws: str, token_id: str) -> None:
    """The turns of ``ws`` the runs of the computer behind ``token_id`` hold
    running fail (``far_turns.fail_held``): its channel was taken back (the
    token revoked, replaced or expired). Never raises."""
    from ..local_agents import far_turns

    def words(agent: str) -> str:
        label = _label(agent) if agent in AGENTS else "the agent"
        return f"The computer's channel was taken back before {label} finished."

    try:
        far_turns.fail_held(ws, token_id, words)
    except Exception as e:  # noqa: BLE001 — the computer goes all the same
        log.warning(f"[computers] the chat turns held by computer token {token_id} in workspace {ws}: {e}")


def _agent(assistants: list) -> str | None:
    """The agent a co-author the computer's owner named may use: the first
    of its ``assistants`` it announced on for the token's account, in the
    catalog's order; None for none."""
    on = _on(assistants)
    return next((name for name in AGENTS if name in on), None)


def _on(assistants: list) -> list[str]:
    """The agents of ``assistants`` announced on for the token's account,
    in the catalog's order."""
    on = {a.get("name") for a in assistants if isinstance(a, dict) and a.get("enabled") is True}
    return [name for name in AGENTS if name in on]


def _people(assistants: list, agent: str | None = None) -> set[str]:
    """The people the computer's agent ``agent`` (else its first agent on,
    ``_agent``) serves beside its owner, as announced."""
    agent = agent or _agent(assistants)
    said = next((a for a in assistants if isinstance(a, dict) and a.get("name") == agent and a.get("enabled")), {})
    people = said.get("people")
    return {p for p in people if isinstance(p, str)} if isinstance(people, list) else set()


def _lends(assistants: list, agent: str, person: str) -> bool:
    """Whether the computer lets ``person`` use its agent ``agent``: its
    first agent on (``_agent``) alone, while it names them (``_people``)."""
    return agent == _agent(assistants) and person in _people(assistants, agent)


def _folder_ids(vaults: list) -> set[str]:
    """The folders the computer's folder vaults of ``vaults`` keep."""
    return {v["folder_id"] for v in vaults if isinstance(v, dict) and v.get("kind") == "folder"
            and isinstance(v.get("folder_id"), str)}


def _refusal(row, owner: str):
    """Why the computer of the ``computers`` row ``row`` (its ``name,
    vaults, assistants, dirs``; None for one no longer shown here) would not
    take a turn of the account ``owner``'s agent there: ``(agent, person,
    kind, dir) → words`` ("" when it would), ``person`` the account the turn
    is for, ``kind`` and ``dir`` where its conversation works
    (``far_turns.dir_of``)."""
    def refused(agent: str, person: str, kind: str, dir: str) -> str:
        label = _label(agent) if agent in AGENTS else "the agent"
        if row is None:
            return f"The computer's channel was taken back before {label} finished."
        name, vaults, assistants, dirs = row[0], _loads(row[1]), _loads(row[2]), _loads(row[3])
        if agent not in _on(assistants):
            return f"{label[:1].upper()}{label[1:]} was turned off on {name}."
        if person != owner and not _lends(assistants, agent, person):
            return f"The owner of {label} on {name} no longer lets you use it."
        if kind == "vault" and dir not in _folder_ids(vaults):
            return f"{name} no longer keeps this synced folder."
        if kind == "offered" and dir not in {d.get("id") for d in dirs if isinstance(d, dict)}:
            return f"{name} no longer offers that directory."
        return ""
    return refused


def _end_orphaned(ws: str, user_id: str, computer_id: str) -> None:
    """The turns queued in ``ws`` for the computer ``computer_id`` of the
    account ``user_id`` (its own and its co-authors') that it would not take
    as it last announced there, a valid token's row (``_refusal``), or every
    one where it is shown no more, fail with why (``far_turns.fail_queued``):
    none would take them, and an agent turned on there again later must not
    run them unasked. Never raises."""
    from ..local_agents import far_turns

    try:
        with connect_users_db() as conn:
            row = conn.execute(
                "SELECT c.name, c.vaults, c.assistants, c.dirs FROM computers c JOIN integration_tokens t "
                "ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? WHERE c.workspace_id = ? "
                "AND c.user_id = ? AND c.computer = ? ORDER BY c.seen_at DESC LIMIT 1",
                (int(time.time()), ws, user_id, computer_id)).fetchone()
        far_turns.fail_queued(ws, user_id, computer_id, _refusal(row, user_id))
    except Exception as e:  # noqa: BLE001 — the announce and the computer's end go on all the same
        log.warning(f"[computers] the chat turns queued in workspace {ws} for {user_id}: {e}")


def drop(token_ids) -> None:
    """The computers behind ``token_ids`` leave memory, their rows having
    gone with their workspace or account in the caller's transaction: their
    held polls end (refused: the tokens went too), their timers are
    cancelled. Touches no database, so a transaction may call it."""
    for token_id in token_ids:
        wake(token_id, "gone")
        with _lock:
            live = _live.pop(token_id, None)
            if live is not None and live.timer is not None:
                live.timer.cancel()


def _covers(conn, vault: dict, page_id: str) -> bool:
    if vault["kind"] == "file":
        return vault["page_id"] == page_id
    return vault["folder_id"] == ROOT or page_in_folder(conn, page_id, vault["folder_id"])


def _holder(conn, vaults: list, page_id: str) -> dict | None:
    """The vault of ``vaults`` that keeps the page, as the computer's own
    rule has it (``folder_links.vault_of_page``): a vault of that file,
    else the deepest folder vault it is filed in (a vault of the whole
    library keeps a page last); None when none covers it."""
    covering = [v for v in vaults if isinstance(v, dict) and _covers(conn, v, page_id)]
    if not covering:
        return None
    def depth(vault: dict) -> int:
        if vault["kind"] == "file":
            return 1 << 30
        return 0 if vault["folder_id"] == ROOT else len(folder_chain(conn, vault["folder_id"]))
    return max(covering, key=depth)


def _vault_name(conn, ws: str, vault: dict) -> str:
    """The folder's name or the page's title as this server has it; the
    workspace's name for the whole library."""
    if vault["kind"] == "folder" and vault["folder_id"] == ROOT:
        return (workspaces.get(ws) or {}).get("name") or ""
    row = conn.execute("SELECT content FROM unified_blocks WHERE id = ?",
                       (vault["folder_id"] if vault["kind"] == "folder" else vault["page_id"],)).fetchone()
    return (row[0] or "") if row else ""


def _loads(text: str) -> list:
    try:
        value = json.loads(text or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def _account_rows(user_id: str, ws: str) -> list[tuple]:
    """The account's ``computers`` rows of the workspace ``ws`` whose token
    still opens the channel: ``(token_id, computer, name, vaults,
    assistants, seen_at, asks, dirs)``. A row whose token was revoked or
    expired goes here (``forget``)."""
    with connect_users_db() as conn:
        rows = conn.execute(
            "SELECT c.token_id, c.computer, c.name, c.vaults, c.assistants, c.seen_at, c.asks, c.dirs, t.id "
            "FROM computers c LEFT JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id "
            "AND t.expires_at > ? WHERE c.user_id = ? AND c.workspace_id = ?",
            (int(time.time()), user_id, ws)).fetchall()
    for gone in [r[0] for r in rows if r[8] is None]:
        forget(gone)
    return [(*r[:6], bool(r[6]), r[7]) for r in rows if r[8] is not None]


def _lent_rows(user_id: str, ws: str) -> list[tuple]:
    """The ``computers`` rows of the workspace ``ws`` of other accounts
    whose first agent on (``_agent``) serves the account ``user_id``, while
    both may edit ``ws``: ``(token_id, computer, name, vaults, assistants,
    seen_at, owner, the owner's username)``, ``assistants`` that agent's
    alone, on for the account, never asking (a far turn of a co-author asks
    nothing) and naming nobody, so no person learns whom else it serves."""
    if not user_id or not _may_send(ws, user_id):
        return []
    from ..db import account_names

    with connect_users_db() as conn:
        rows = conn.execute(
            "SELECT c.token_id, c.computer, c.name, c.vaults, c.assistants, c.seen_at, c.user_id FROM computers c "
            "JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? "
            "WHERE c.workspace_id = ? AND c.user_id != ? AND c.assistants LIKE ?",
            (int(time.time()), ws, user_id, f'%"{user_id}"%')).fetchall()
        names = account_names(conn, {row[6] for row in rows})
    out = []
    for token_id, computer, name, vaults, assistants, seen_at, owner in rows:
        said = _loads(assistants)
        agent = _agent(said)
        if agent is None or not _lends(said, agent, user_id) or not _may_send(ws, owner):
            continue
        lent = [{**a, "runs": "never", "people": []} for a in said if isinstance(a, dict) and a.get("name") == agent]
        out.append((token_id, computer, name, vaults, json.dumps(lent), seen_at, owner, names.get(owner, "")))
    return out


def _rows_of(user_id: str, ws: str) -> list[tuple]:
    """The account's rows (``_account_rows``) then the lent ones
    (``_lent_rows``): ``(token_id, computer, name, vaults, assistants,
    seen_at, asks, dirs, owner, lent_by)``, ``owner`` the computer's
    account, ``lent_by`` its username for a lent one, "" for the account's
    own; a lent one never asks (a co-author's far turn asks nothing) and
    offers no directory (a co-author's turn works in a synced folder
    alone)."""
    return [(*row, user_id, "") for row in _account_rows(user_id, ws)] + [
        (*row[:6], False, "[]", row[6], row[7]) for row in _lent_rows(user_id, ws)]


def _ordered(found: list[tuple[dict, str, bool]]) -> list[dict]:
    """The entries of ``found`` (``(entry, seen_at, keeps)``) in the order a
    page's agents are offered: those whose computer keeps the work folder
    (``keeps``) first, the account's own before the lent ones (``lent_by``),
    online ones first, then the most recently seen."""
    found.sort(key=lambda item: item[1], reverse=True)
    found.sort(key=lambda item: (not item[2], bool(item[0].get("lent_by")), not item[0]["online"]))
    return [entry for entry, _seen, _keeps in found]


def of_account(user_id: str, ws: str) -> list[dict]:
    """The account's computers shown to the workspace ``ws`` (Settings → AI
    on this server, and a chat with no page open): ``[{id, name, online,
    since, assistants, asks, dirs, lent_by?}]``, as ``of_page`` says them,
    without a vault, then the other
    accounts' whose agent serves it (``_lent_rows``: ``lent_by`` the owner's
    username, that agent alone); online ones first, then the most recently
    seen."""
    found = []
    for token_id, computer, name, _vaults, assistants, seen_at, asks, dirs, _owner, lent_by in _rows_of(user_id, ws):
        with _lock:
            now = online(_live.get(token_id))
        found.append(({"id": computer, "name": name, "online": now, "since": None if now else seen_at,
                       "assistants": _loads(assistants), "asks": asks, "dirs": _loads(dirs),
                       **({"lent_by": lent_by} if lent_by else {})},
                      seen_at, True))
    return _ordered(found)


def of_page(user_id: str, ws: str, page_id: str) -> list[dict]:
    """The account's computers for the page ``page_id`` of the workspace
    ``ws``: those that keep it (a file vault of that page, a folder vault
    the page is filed in or below) and those with an agent on for the
    account, whose agents it uses on any page (``seats``): ``[{id, name,
    online, since, vault: {kind, name, paused, work} | null, assistants,
    asks, dirs, lent_by?}]``, ``since`` the time it went offline (null
    while online), ``vault`` the one that keeps the page (null for none),
    ``work`` whether it keeps a work folder of the workspace, which the
    account may edit (its agents work there), ``asks`` whether the computer
    lets this server answer what its agents ask, ``dirs`` the directories
    it offers them (``[{id, name}]``); then those of the other accounts'
    computers whose agent serves it (``_lent_rows``) that keep the page in a
    work folder, ``lent_by`` the owner's username; in ``_ordered``'s order,
    as ``seats`` takes them (so the chat names the computer its turns go
    to), but that among the others a computer keeping the page comes before
    one that keeps none (the chip names the first that keeps it). A row
    whose token was revoked or expired goes here (``forget``)."""
    rows = _rows_of(user_id, ws)
    if not rows:
        return []
    found: list[tuple[dict, str, bool]] = []
    with connect_pages_db(ws) as conn:
        top = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_top(conn, page_id),)).fetchone()
        if not top or top[0] != "root":
            return []
        marks = Tree.read(conn).marks if _may_send(ws, user_id) else {}
        for token_id, computer, name, vaults, assistants, seen_at, asks, dirs, _owner, lent_by in rows:
            vault = _holder(conn, _loads(vaults), page_id)
            work = vault is not None and vault.get("folder_id") in marks
            if lent_by and not work:
                continue   # a co-author is shown the computer's agent of a work folder alone
            if vault is None and not _on(_loads(assistants)):
                continue
            with _lock:
                now = online(_live.get(token_id))
            shown = {"kind": vault["kind"], "name": _vault_name(conn, ws, vault), "paused": bool(vault.get("paused")),
                     "work": work} if vault is not None else None
            found.append(({"id": computer, "name": name, "online": now, "since": None if now else seen_at,
                           "vault": shown, "assistants": _loads(assistants), "asks": asks, "dirs": _loads(dirs),
                           **({"lent_by": lent_by} if lent_by else {})}, seen_at, work))
    return sorted(_ordered(found), key=lambda entry: entry["vault"] is None)


def seats(ws: str, bucket: str, user_id: str) -> list[dict]:
    """The agents of other computers the account ``user_id`` talks to on
    the bucket ``bucket`` of ``ws`` (a page's id, a folder's, ``home``;
    step 4.2 of docs/research/agents-as-participants-plan.md): each agent
    on for it of each of its computers shown to ``ws``, and the agent of
    another account's computer that serves it (``_lent_rows``) where that
    computer keeps the work folder the bucket is in, as announced:
    ``[{token_id, user_id, computer_id, computer, agent, label, online,
    since, paused, ready, code, lent_by, folder_id, dirs}]``, ``user_id``
    the computer's account (the agent's owner), ``computer`` its name,
    ``ready`` the agent's as it last said (true, false or null), ``code``
    its problem's (``CODES``), ``folder_id`` the work folder W the bucket is
    in where that computer keeps it (its agents work in its vault there,
    ``paused`` as that vault is), else "", ``dirs`` the directories it
    offers them; in ``_ordered``'s order, each computer's agents in the
    catalog's. None while the account may not edit the workspace, and for
    no such bucket. Reads the rows of a computer away too: the caller
    refuses a turn for one offline."""
    from ..local_agents import chats  # local: the chats reach this module where a far turn is named

    if not user_id or not _may_send(ws, user_id):
        return []
    with connect_pages_db(ws) as conn:
        about = chats.about(conn, bucket)
    if about is None:
        return []
    w = about["w"]
    found: list[tuple[dict, str, bool]] = []
    for token_id, computer, name, vaults, assistants, seen_at, _asks, dirs, owner, lent_by in _rows_of(user_id, ws):
        said = _loads(assistants)
        vault = next((v for v in _loads(vaults) if isinstance(v, dict) and v.get("kind") == "folder"
                      and v.get("folder_id") == w), None) if w else None
        if lent_by and vault is None:
            continue
        with _lock:
            now = online(_live.get(token_id))
        for agent in _on(said):
            one = next(a for a in said if isinstance(a, dict) and a.get("name") == agent)
            found.append(({"token_id": token_id, "user_id": owner, "computer_id": computer, "computer": name,
                           "agent": agent, "label": _label(agent), "online": now,
                           "since": None if now else (seen_at or None),
                           "paused": bool(vault and vault.get("paused")), "ready": one.get("ready"),
                           "code": one.get("code") if one.get("code") in CODES else "", "lent_by": lent_by,
                           "folder_id": w if vault is not None else "", "dirs": _loads(dirs)},
                          seen_at, vault is not None))
    return _ordered(found)


def seat_of(ws: str, bucket: str, user_id: str, computer_id: str = "", agent: str = "") -> dict | None:
    """The first of ``seats`` that is the computer ``computer_id``'s and
    the agent ``agent``'s (either "" for any): the far seat of a turn the
    account begins there; None for none."""
    return next((seat for seat in seats(ws, bucket, user_id)
                 if (not computer_id or seat["computer_id"] == computer_id) and (not agent or seat["agent"] == agent)),
                None)


# --- the turns a computer takes (step 4d) -------------------------------------------------------
# The account's turns with an agent of its computer (a conversation's
# ``computer``, chosen with ``seats``) run on that computer, taken over the
# channel (gamma/routers/computers.py), and their messages are written here.

def _label(agent: str) -> str:
    from ..local_agents import catalog  # local: the agents' package is loaded where an agent is named

    return catalog.label(agent)


def _valid_rows(conn, where: str, args: tuple) -> list:
    """The ``computers`` rows ``where`` names whose token still opens the
    channel: ``(token_id, user_id, name, vaults, assistants, seen_at)``."""
    return conn.execute(
        "SELECT c.token_id, c.user_id, c.name, c.vaults, c.assistants, c.seen_at FROM computers c "
        "JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? "
        f"WHERE {where}", (int(time.time()), *args)).fetchall()


def _may_send(ws: str, user_id: str) -> bool:
    """Whether the account ``user_id`` may edit ``ws``: only such an
    account talks to an agent there."""
    return workspaces.at_least(workspaces.role_of(ws, user_id), "editor")


def keepers(ws: str, folder_id: str) -> list[dict]:
    """The computers that announced a folder vault of ``folder_id`` of the
    workspace ``ws``, whosever they are, as a valid token's: ``[{token_id,
    user_id, name, online}]``, online ones first, then the most recently
    seen. Who may detach a work folder and whom its detach wakes
    (gamma/work_folders/detach.py)."""
    with connect_users_db() as conn:
        rows = _valid_rows(conn, "c.workspace_id = ? AND c.vaults LIKE ?", (ws, f'%"{folder_id}"%'))
    found = []
    for token_id, user_id, name, vaults, _assistants, seen_at in rows:
        if not any(isinstance(v, dict) and v.get("kind") == "folder" and v.get("folder_id") == folder_id
                   for v in _loads(vaults)):
            continue
        with _lock:
            now = online(_live.get(token_id))
        found.append(({"token_id": token_id, "user_id": user_id, "name": name, "online": now}, seen_at or ""))
    found.sort(key=lambda pair: pair[1], reverse=True)
    found.sort(key=lambda pair: (bool(pair[0].get("lent_by")), not pair[0]["online"]))
    return [computer for computer, _seen in found]


def of_folders(user_id: str, ws: str) -> dict[str, dict]:
    """The account's computers that keep a folder vault of the workspace
    ``ws``, by its folder: ``{folder_id: {name, online, since, paused}}``,
    each folder's first computer (online ones first, then the most recently
    seen), ``since`` when it went offline (null while online), ``paused``
    whether its vault is paused there. What a synced folder's card, row and
    Files window row say of the computer that keeps it. A row whose token
    was revoked or expired goes here (``_account_rows``)."""
    kept: dict[str, tuple[dict, str]] = {}
    for token_id, _computer, name, vaults, _assistants, seen_at, _asks, _dirs in _account_rows(user_id, ws):
        with _lock:
            now = online(_live.get(token_id))
        for vault in _loads(vaults):
            folder = vault.get("folder_id") if isinstance(vault, dict) and vault.get("kind") == "folder" else None
            if not isinstance(folder, str) or folder == ROOT:
                continue
            entry = ({"name": name, "online": now, "since": None if now else seen_at, "paused": bool(vault.get("paused"))},
                     seen_at or "")
            held = kept.get(folder)
            if held is None or (now, entry[1]) > (held[0]["online"], held[1]):
                kept[folder] = entry
    return {folder: computer for folder, (computer, _seen) in kept.items()}


def computer_name(token_id: str) -> str:
    """The name the computer behind ``token_id`` announced, "" for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT name FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return row[0] if row else ""


def computer_of(token_id: str) -> tuple[str, str] | None:
    """``(id, name)`` of the computer behind ``token_id`` as it announced
    them, None for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT computer, name FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return (row[0], row[1]) if row else None


def names_of(keys, ws: str) -> dict[tuple[str, str, str], str]:
    """``{(account id, computer id, owner): name}`` of the computers
    ``keys`` (``(account id, computer id, owner)``, ``owner`` the username
    of the account whose agent wrote for the account, "" for its own): an
    account's own as each last announced its name here for that account (a
    computer shown to several workspaces has a row each: the most recently
    seen names it); a co-author's as a computer of the workspace ``ws``
    whose account is called ``owner`` last announced it while its folders'
    agent (``_agent``) served the account and both may edit ``ws``, as
    ``_lent_rows`` reads it; keys none announced left out. What an agent's
    comment says of its computer (gamma/agents.py): a computer's id is what
    its channel says, so only the account's own rows name it, or a row of
    the owner kept with the comment that names the account."""
    from ..db import account_names

    keys = sorted(set(keys))
    own = [(user, computer) for user, computer, owner in keys if not owner]
    lent = [key for key in keys if key[2]]
    out = {}
    with connect_users_db() as conn:
        if own:
            rows = conn.execute("SELECT user_id, computer, name FROM computers WHERE (user_id, computer) IN "
                                "(SELECT json_extract(value, '$[0]'), json_extract(value, '$[1]') FROM json_each(?)) "
                                "ORDER BY seen_at", (json.dumps(own),)).fetchall()
            out.update({(user, computer, ""): name for user, computer, name in rows})
        rows = []
        if lent and ws:
            rows = conn.execute(
                "SELECT c.user_id, c.computer, c.name, c.assistants FROM computers c "
                "JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? "
                "WHERE c.workspace_id = ? AND c.computer IN (SELECT value FROM json_each(?)) ORDER BY c.seen_at",
                (int(time.time()), ws, json.dumps(sorted({computer for _u, computer, _o in lent})))).fetchall()
        owners = account_names(conn, {row[0] for row in rows})
    for owner_id, computer, name, assistants in rows:
        said = _loads(assistants)
        for user, wanted, owner in lent:
            if (wanted == computer and user != owner_id and owners.get(owner_id) == owner
                    and user in _people(said, _agent(said)) and _may_send(ws, owner_id) and _may_send(ws, user)):
                out[user, computer, owner] = name
    return out


def far_name(ws: str, user_id: str, computer_id: str) -> tuple[str, str]:
    """``(name, owner)`` of the computer ``computer_id`` whose agent acts
    for the account ``user_id`` in ``ws`` (a far turn's token,
    gamma/agents.py ``of_token``): the name the account's own computer last
    announced there, owner "", else the name of another account's whose
    agent serves it (``_lent_rows``) and that owner's username; ``("", "")``
    for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT name FROM computers WHERE workspace_id = ? AND user_id = ? AND computer = ? "
                           "ORDER BY seen_at DESC LIMIT 1", (ws, user_id, computer_id)).fetchone()
    if row:
        return row[0], ""
    lent = next((row for row in _lent_rows(user_id, ws) if row[1] == computer_id), None)
    return (lent[2], lent[7]) if lent else ("", "")


def is_online(token_id: str) -> bool:
    """Whether the computer behind ``token_id`` is online now."""
    with _lock:
        return online(_live.get(token_id))


def answers(token_id: str) -> bool:
    """Whether the computer behind ``token_id`` answers what this server
    asks of its agents' sessions, as its announce since it was last online
    said (gamma/computers/sessions.py)."""
    with _lock:
        live = _live.get(token_id)
        return live is not None and live.sessions


def runs_skills(token_id: str) -> bool:
    """Whether the computer behind ``token_id`` runs a turn's skill, as its
    announce since it was last online said: a turn that ran one is
    refused for a computer that does not (routers/local_agents.py
    ``_far_turn_started``)."""
    with _lock:
        live = _live.get(token_id)
        return live is not None and live.skills


def own_computer(user_id: str, ws: str, computer_id: str) -> dict | None:
    """The account's own computer ``computer_id`` shown to ``ws`` (never
    another account's whose agent serves it), as ``taking`` says it, with
    its ``token_id`` and whether it is ``online`` (the online one's row
    where it has several, else the most recently seen): what History's
    *On {computer}* asks (gamma/computers/sessions.py); None for
    none, and while the account may not edit ``ws``."""
    rows = sorted((row for row in _account_rows(user_id, ws) if row[1] == computer_id),
                  key=lambda row: row[5] or "", reverse=True)
    with _lock:
        rows.sort(key=lambda row: not online(_live.get(row[0])))
    for token_id, *_rest in rows:
        found = taking(token_id, ws)
        if found is not None:
            return {**found, "token_id": token_id, "online": is_online(token_id)}
    return None


def said_of(token_id: str) -> tuple[list, str]:
    """``(the assistants, seen_at)`` the computer behind ``token_id`` last
    announced (``normalise``'s, each with its ``models``) and when it was
    last seen; ``([], "")`` for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT assistants, seen_at FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return (_loads(row[0]), row[1] or "") if row else ([], "")


def workspaces_of(user_id: str) -> list[str]:
    """The workspaces a computer of the account ``user_id`` has an agent on
    for it in, or another account's whose agent serves it (``_lends``), as
    announced: where its far turns may be (Background tasks)."""
    with connect_users_db() as conn:
        rows = conn.execute("SELECT workspace_id, assistants, user_id FROM computers WHERE user_id = ? "
                            "OR assistants LIKE ? ORDER BY workspace_id", (user_id, f'%"{user_id}"%')).fetchall()
    return list(dict.fromkeys(ws for ws, assistants, owner in rows
                              if (owner == user_id and _on(_loads(assistants)))
                              or (owner != user_id and user_id in _people(_loads(assistants)))))


def taking(token_id: str, ws: str) -> dict | None:
    """What the computer behind ``token_id`` takes in ``ws``, as it last
    announced it: ``{user_id, computer_id, name, agents, assistants,
    folders, dirs}``, ``agents`` those on for its account (catalog order),
    ``folders`` ``{W: paused}`` of the work folders' roots of ``ws`` it
    keeps, ``dirs`` the ids of the directories it offers; None for no
    valid token's row, and while its account may not edit ``ws``."""
    with connect_users_db() as conn:
        row = conn.execute(
            "SELECT c.user_id, c.computer, c.name, c.vaults, c.assistants, c.dirs FROM computers c "
            "JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? "
            "WHERE c.token_id = ? AND c.workspace_id = ?", (int(time.time()), token_id, ws)).fetchone()
    if row is None or not _may_send(ws, row[0]):
        return None
    vaults, assistants = _loads(row[3]), _loads(row[4])
    with connect_pages_db(ws) as conn:
        marks = Tree.read(conn).marks
    return {"user_id": row[0], "computer_id": row[1], "name": row[2], "agents": _on(assistants),
            "assistants": assistants,
            "folders": {v["folder_id"]: bool(v.get("paused")) for v in vaults if isinstance(v, dict)
                        and v.get("kind") == "folder" and v.get("folder_id") in marks},
            "dirs": {d["id"] for d in _loads(row[5]) if isinstance(d, dict) and isinstance(d.get("id"), str)}}


def people_of(token_id: str) -> set[str]:
    """The people the first agent on of the computer behind ``token_id``
    serves beside its owner, as it last announced them (``_people``)."""
    return _people(said_of(token_id)[0])


def serves(token_id: str, ws: str, person: str, agent: str) -> bool:
    """Whether the computer behind ``token_id``, of the workspace ``ws``,
    runs a turn of its agent ``agent`` for the account ``person``: its own
    account's, or one that agent serves (``_lends``), while ``person`` may
    edit ``ws``."""
    with connect_users_db() as conn:
        rows = _valid_rows(conn, "c.token_id = ? AND c.workspace_id = ?", (token_id, ws))
    if not rows or not person or not _may_send(ws, person):
        return False
    return rows[0][1] == person or _lends(_loads(rows[0][4]), agent, person)


def reset() -> None:
    """Forget every computer's presence (a restart; tests): the rows stay."""
    with _lock:
        for live in _live.values():
            if live.timer is not None:
                live.timer.cancel()
        _live.clear()

"""The server's side of a computer's channel, on every Gamma server
(gamma/computers/__init__.py): what each computer announced and whether it
is online now.

A computer speaks with an integration token of the ``computer`` scope
(gamma/integrations.py), which opens the channel's routes and nothing
else (``auth.CHANNEL_PREFIX``). What it last announced is one users.db
``computers`` row per token: its id (one per server: the computer's HMAC
of its install id and this server's origin, so two servers cannot match
one computer by it), its name, the folders and pages of the token's
workspace it keeps (ids only, ``vaults``, each paused or not, a work
folder's with its agent when that is on there: ``agent_of``), the
assistants there (``assistants``: name, label, found, ready, version and a
problem's code, never its words), the app's version, and when it was last
seen (``seen_at``), so "offline since 14:02" survives a restart. Never a
path.

Whether it is online is in memory (``_live``): a poll is held now, or the
last one ended (or the computer announced) less than ``GRACE_S`` ago. A
poll that ends arms a timer; a new poll cancels it; when it fires the
computer is offline, ``seen_at`` is written and the account's windows are
told (``{t: "computer"}``, content-free, to that account's sockets alone:
``collab.publish_account``). The same nudge goes when an announce changes
what a window shows. A poll is answered ``{announce: true}`` when the
computer is unknown here (a restart) or announced another version; held
polls are woken by ``wake``: to announce again, ``sends`` when its
maker's Send in a work folder it keeps with an agent is made or withdrawn
(``heard``, step 4c), or ``chat`` when a turn of its maker's chat on a page
there is begun or asked to stop (step 4d), a wake while no poll is held
answering the next.

Only the token's account sees its computer (``of_page``); a revoked or
expired token's row goes (``forget``, and when the rows are read).
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
AGENTS = ("codex", "claude")
CODES = ("", "missing", "signed_out", "not_ready", "checking")   # an assistant's problem, as a code
NUDGE = {"t": "computer"}
_COMPUTER_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


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
    ``[{id, label, efforts, effort, default}]`` (``events.model``), at most
    ``MAX_MODELS`` with ``MAX_EFFORTS`` efforts each, one without an id
    left out. ValueError for another shape."""
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
                             else [], item.get("effort"), item.get("default"))
        if found is not None and all(m["id"] != found["id"] for m in kept):
            kept.append(found)
    return kept


def normalise(body) -> dict:
    """The announce ``body`` as kept: ``{computer: {id, name}, app, vaults,
    assistants}``, each string capped, the vaults deduplicated (a folder
    vault's ``agent``, ``codex`` or ``claude``, kept when it has one: a work
    folder of this server whose Sends run on the computer; another name, or
    on a file vault, is none), an assistant this server does not know of
    dropped, an unknown problem code read as ``not_ready``, each with the
    ``models`` it offers there (``_models``). ValueError for a body of
    another shape."""
    if not isinstance(body, dict) or not isinstance(body.get("computer"), dict):
        raise ValueError("The announce names its computer.")
    computer_id, name = body["computer"].get("id"), body["computer"].get("name")
    if not isinstance(computer_id, str) or not _COMPUTER_ID.match(computer_id):
        raise ValueError("The computer's id is 1 to 64 letters, digits, - or _.")
    if not isinstance(name, str) or not 1 <= len(name.strip()) <= MAX_TEXT or not name.strip().isprintable():
        raise ValueError("The computer's name is 1 to 64 printable characters.")
    vaults, assistants = body.get("vaults", []), body.get("assistants", [])
    if not isinstance(vaults, list) or len(vaults) > MAX_VAULTS:
        raise ValueError(f"The vaults are a list of at most {MAX_VAULTS}.")
    if not isinstance(assistants, list) or len(assistants) > 2 * len(AGENTS):
        raise ValueError("The assistants are a short list.")
    kept, seen = [], set()
    for vault in vaults:
        if not isinstance(vault, dict) or vault.get("kind") not in ("folder", "file"):
            raise ValueError("A vault is a folder or a file.")
        key = "folder_id" if vault["kind"] == "folder" else "page_id"
        target = vault.get(key)
        if not isinstance(target, str) or not 1 <= len(target) <= MAX_ID:
            raise ValueError(f"A {vault['kind']} vault names its {key}.")
        agent = vault.get("agent") if vault["kind"] == "folder" and vault.get("agent") in AGENTS else None
        if (vault["kind"], target) not in seen:
            seen.add((vault["kind"], target))
            kept.append({"kind": vault["kind"], key: target, "paused": bool(_flag(vault.get("paused"), "paused")),
                         **({"agent": agent} if agent else {})})
    there = []
    for agent in assistants:
        if not isinstance(agent, dict):
            raise ValueError("An assistant is an object.")
        if agent.get("name") not in AGENTS or any(a["name"] == agent["name"] for a in there):
            continue
        code = agent.get("code") or ""
        there.append({"name": agent["name"], "label": _text(agent.get("label"), "label") or agent["name"],
                      "found": _flag(agent.get("found"), "found"), "ready": _flag(agent.get("ready"), "ready"),
                      "version": _text(agent.get("version"), "version"),
                      "code": code if code in CODES else "not_ready", "models": _models(agent.get("models"))})
    return {"computer": {"id": computer_id, "name": name.strip()}, "app": _text(body.get("app"), "app"),
            "vaults": kept, "assistants": there}


def _kept_here(ws: str, user_id: str, vaults: list[dict]) -> list[dict]:
    """The vaults of ``vaults`` the workspace has: a live folder (or the
    whole library), a page of the library. A folder vault keeps its
    ``agent`` only when the folder itself carries a work folder's mark and
    the account ``user_id`` may edit the workspace (``_may_send``)."""
    with connect_pages_db(ws) as conn:
        def here(vault: dict) -> bool:
            if vault["kind"] == "folder":
                return vault["folder_id"] == ROOT or bool(conn.execute(
                    "SELECT 1 FROM unified_blocks WHERE id = ? AND page_id = 'folders'",
                    (vault["folder_id"],)).fetchone())
            row = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (vault["page_id"],)).fetchone()
            return bool(row) and row[0] == "root"
        kept = [vault for vault in vaults if here(vault)]
        if not any(vault.get("agent") for vault in kept):
            return kept
        roots = _work_roots(conn, [v["folder_id"] for v in kept if v.get("agent")]) if _may_send(ws, user_id) else set()
    return [vault if not vault.get("agent") or vault["folder_id"] in roots
            else {k: v for k, v in vault.items() if k != "agent"} for vault in kept]


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
    they show changed (online now, the name, the vaults, the assistants).
    ``{account: {id, username}, computer: {id, name}, workspace: {id},
    version, wait}``: ``version`` what its polls name, ``wait`` the
    longest hold. A work folder it kept with an agent and no longer does
    (turned off, another agent, gone) has the account's chat turns
    queued there that no computer of it would take fail
    (``_end_orphaned``). ValueError for a malformed body."""
    found = normalise(body)
    found["vaults"] = _kept_here(ws, user_id, found["vaults"])
    shown = (found["computer"]["name"], json.dumps(found["vaults"]), json.dumps(found["assistants"]))
    now = page_now()
    with connect_users_db() as conn:
        before = conn.execute("SELECT name, vaults, assistants FROM computers WHERE token_id = ?",
                              (token_id,)).fetchone()
        conn.execute("INSERT INTO computers (token_id, user_id, workspace_id, computer, name, vaults, assistants, app, "
                     "seen_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(token_id) DO UPDATE SET "
                     "user_id = excluded.user_id, workspace_id = excluded.workspace_id, computer = excluded.computer, "
                     "name = excluded.name, vaults = excluded.vaults, assistants = excluded.assistants, "
                     "app = excluded.app, seen_at = excluded.seen_at",
                     (token_id, user_id, ws, found["computer"]["id"], *shown, found["app"], now, now))
    with _lock:
        live = _live.get(token_id)
        if live is None:
            live = _live[token_id] = _Live(user_id)
        live.version, live.written = secrets.token_urlsafe(8), time.monotonic()
        live.pending = [reason for reason in live.pending if reason != "announce"]
        version = live.version
        came = _beat(token_id, live)
    if came or before is None or tuple(before) != shown:
        collab.publish_account(user_id, NUDGE)
    if before is not None:
        now_kept = _agent_vaults(found["vaults"])
        _end_orphaned(ws, user_id, [w for w, agent in _agent_vaults(_loads(before[1])).items()
                                    if now_kept.get(w) != agent], _turned_off)
    return {"account": {"id": user_id, "username": username}, "computer": found["computer"], "workspace": {"id": ws},
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
    to have it announce again, ``sends`` for its Sends (``heard``), or
    ``chat`` for its chat turns; while none is held, its next poll is answered at once.
    Safe from any thread."""
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
    held polls end (and are refused, the token being gone), the Sends its
    runs still hold fail (``_fail_held``: no run of it can write them any
    more), the chat turns queued in its work folders that no other
    computer of the account would take fail (``_end_orphaned``), and the
    account's windows are told."""
    wake(token_id, "gone")
    with _lock:
        live = _live.pop(token_id, None)
        if live is not None and live.timer is not None:
            live.timer.cancel()
    with connect_users_db() as conn:
        row = conn.execute("SELECT user_id, workspace_id, vaults FROM computers WHERE token_id = ?",
                           (token_id,)).fetchone()
        conn.execute("DELETE FROM computers WHERE token_id = ?", (token_id,))
    if row:
        _fail_held(row[1], token_id)
        _end_orphaned(row[1], row[0], list(_agent_vaults(_loads(row[2]))), _taken_back)
    if row or live is not None:
        collab.publish_account(row[0] if row else live.user_id, NUDGE)


def _fail_held(ws: str, token_id: str) -> None:
    """The requests of ``ws`` the runs of the computer behind ``token_id``
    hold fail, those runs stopped (``requests.fail_runs``), and so do the
    chat turns they hold running (``chats.fail_held``): its channel was
    taken back (the token revoked, replaced or expired). Never raises."""
    from ..feedback import requests  # local: the requests' listeners reach this module
    from ..local_agents import chats

    def words(agent: str) -> tuple[str, str]:
        label = _label(agent) if agent in AGENTS else "the agent"
        return label, f"The computer's channel was taken back before {label} finished."

    try:
        with connect_pages_db(ws) as conn:
            requests.fail_runs(ws, conn, token_id, words=words)
    except Exception as e:  # noqa: BLE001 — the computer goes all the same
        log.warning(f"[computers] the Sends held by computer token {token_id} in workspace {ws}: {e}")
    try:
        chats.fail_held(ws, token_id, lambda agent: words(agent)[1])
    except Exception as e:  # noqa: BLE001 — the computer goes all the same
        log.warning(f"[computers] the chat turns held by computer token {token_id} in workspace {ws}: {e}")


def _agent_vaults(vaults: list) -> dict[str, str]:
    """``{folder id: agent}`` of the folder vaults of ``vaults`` kept with an agent."""
    return {v["folder_id"]: v["agent"] for v in vaults
            if isinstance(v, dict) and v.get("kind") == "folder" and v.get("agent") in AGENTS}


def _turned_off(agent: str, agents) -> str:
    label = _label(agent) if agent in AGENTS else "The agent"
    return f"{label} is no longer this folder's agent." if agents else f"{label} was turned off for this folder."


def _taken_back(agent: str, _agents) -> str:
    label = _label(agent) if agent in AGENTS else "the agent"
    return f"The computer's channel was taken back before {label} finished."


def _end_orphaned(ws: str, user_id: str, folders: list[str], words) -> None:
    """The account's chat turns queued in pages of ``folders`` whose agent
    no computer of the account keeps the folder with now (as announced, a
    valid token's) fail, ``words(agent, the agents it is kept with)`` their
    error (``chats.fail_queued``): none would take them, and an agent
    turned on there again later must not run them unasked. Never raises."""
    from ..local_agents import chats

    if not folders:
        return
    try:
        with connect_users_db() as conn:
            rows = _valid_rows(conn, "c.workspace_id = ? AND c.user_id = ?", (ws, user_id))
        kept = {w: set() for w in folders}
        for row in rows:
            for w, agent in _agent_vaults(_loads(row[3])).items():
                if w in kept:
                    kept[w].add(agent)
        chats.fail_queued(ws, user_id, kept, words)
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


def of_page(user_id: str, ws: str, page_id: str) -> list[dict]:
    """The account's computers that keep the page ``page_id`` of the
    workspace ``ws`` (a file vault of that page, a folder vault the page is
    filed in or below): ``[{id, name, online, since, vault: {kind, name,
    paused}, assistants}]``, ``since`` the time it went offline (null while
    online), online ones first, then the most recently seen. A row whose
    token was revoked or expired goes here (``forget``)."""
    with connect_users_db() as conn:
        rows = conn.execute(
            "SELECT c.token_id, c.computer, c.name, c.vaults, c.assistants, c.seen_at, t.id FROM computers c "
            "LEFT JOIN integration_tokens t ON t.id = c.token_id AND t.user_id = c.user_id AND t.expires_at > ? "
            "WHERE c.user_id = ? AND c.workspace_id = ?", (int(time.time()), user_id, ws)).fetchall()
    for gone in [r[0] for r in rows if r[6] is None]:
        forget(gone)
    rows = [r for r in rows if r[6] is not None]
    if not rows:
        return []
    found: list[tuple[dict, str]] = []
    with connect_pages_db(ws) as conn:
        top = conn.execute("SELECT parent_id FROM unified_blocks WHERE id = ?", (page_top(conn, page_id),)).fetchone()
        if not top or top[0] != "root":
            return []
        for token_id, computer, name, vaults, assistants, seen_at, _token in rows:
            vault = _holder(conn, _loads(vaults), page_id)
            if vault is None:
                continue
            with _lock:
                now = online(_live.get(token_id))
            agent = vault.get("agent") if vault.get("agent") in AGENTS else None
            found.append(({"id": computer, "name": name, "online": now, "since": None if now else seen_at,
                           "vault": {"kind": vault["kind"], "name": _vault_name(conn, ws, vault),
                                     "paused": bool(vault.get("paused")),
                                     "agent": {"name": agent, "label": _label(agent)} if agent else None},
                           "assistants": _loads(assistants)}, seen_at))
    found.sort(key=lambda pair: pair[1], reverse=True)
    found.sort(key=lambda pair: not pair[0]["online"])
    return [computer for computer, _seen in found]


# --- the work folders a computer keeps with an agent (step 4c) ---------------------------------
# A work folder of this server kept on a computer whose agent is on there
# (the announce's folder vault with ``agent``): its maker's Sends made in
# Gamma run on that computer, taken over the channel
# (gamma/routers/computers.py), and this server writes the answers.

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


def _work_roots(conn, folder_ids) -> set:
    """Those of ``folder_ids`` that are a work folder's root here: the
    folder itself carries the mark."""
    tree = Tree.read(conn)
    return {f for f in folder_ids if f in tree.marks}


def _may_send(ws: str, user_id: str) -> bool:
    """Whether the account ``user_id`` may edit ``ws``: only such an
    account's computer keeps a work folder of it with an agent."""
    return workspaces.at_least(workspaces.role_of(ws, user_id), "editor")


def computer_name(token_id: str) -> str:
    """The name the computer behind ``token_id`` announced, "" for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT name FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return row[0] if row else ""


def is_online(token_id: str) -> bool:
    """Whether the computer behind ``token_id`` is online now."""
    with _lock:
        return online(_live.get(token_id))


def said_of(token_id: str) -> tuple[list, str]:
    """``(the assistants, seen_at)`` the computer behind ``token_id`` last
    announced (``normalise``'s, each with its ``models``) and when it was
    last seen; ``([], "")`` for none."""
    with connect_users_db() as conn:
        row = conn.execute("SELECT assistants, seen_at FROM computers WHERE token_id = ?", (token_id,)).fetchone()
    return (_loads(row[0]), row[1] or "") if row else ([], "")


def workspaces_of(user_id: str) -> list[str]:
    """The workspaces a computer of the account ``user_id`` keeps a work
    folder of with an agent, as announced."""
    with connect_users_db() as conn:
        return [r[0] for r in conn.execute("SELECT DISTINCT workspace_id FROM computers WHERE user_id = ? "
                                           "AND vaults LIKE '%\"agent\"%' ORDER BY workspace_id", (user_id,))]


def agent_folders(token_id: str, ws: str) -> dict[str, dict]:
    """The work folders of ``ws`` the computer behind ``token_id`` keeps
    with an agent, as it last announced them: ``{folder id: {agent,
    paused}}``; ``{}`` for none (a folder no longer a work folder's root
    here is none, and every folder while the token's account may not edit
    the workspace)."""
    with connect_users_db() as conn:
        rows = _valid_rows(conn, "c.token_id = ? AND c.workspace_id = ?", (token_id, ws))
    found = {v["folder_id"]: {"agent": v["agent"], "paused": bool(v.get("paused"))}
             for row in rows for v in _loads(row[3])
             if isinstance(v, dict) and v.get("kind") == "folder" and v.get("agent") in AGENTS}
    if not found or not _may_send(ws, rows[0][1]):
        return {}
    with connect_pages_db(ws) as conn:
        roots = _work_roots(conn, found)
    return {w: found[w] for w in sorted(roots)}


def agent_of(ws: str, folder_id: str, user_id: str) -> dict | None:
    """The computer of the account ``user_id`` (the one mentioning,
    sending or reading) that keeps the work folder ``folder_id`` of ``ws``
    is (or is below) with an agent, as announced: ``{token_id, user_id,
    agent, label, computer, online, paused, ready, code}`` (``computer`` its
    name, ``ready`` the agent's as the computer last said: true, false or
    null, ``code`` its problem's, ``CODES``), an online one first, then the
    most recently seen; None for none, and while that account may not edit
    the workspace. Another account's computer never answers for it. Reads
    the rows even while the computer is away, so its Sends wait for it."""
    if not user_id or not _may_send(ws, user_id):
        return None
    with connect_pages_db(ws) as conn:
        tree = Tree.read(conn)
    w = tree.work_of(folder_id) if folder_id in tree else ""
    if not w:
        return None
    with connect_users_db() as conn:
        rows = _valid_rows(conn, "c.workspace_id = ? AND c.user_id = ? AND c.vaults LIKE '%\"agent\"%'",
                           (ws, user_id))
    found = []
    for token_id, user_id, name, vaults, assistants, seen_at in rows:
        vault = next((v for v in _loads(vaults) if isinstance(v, dict) and v.get("kind") == "folder"
                      and v.get("folder_id") == w and v.get("agent") in AGENTS), None)
        if vault is None:
            continue
        said = next((a for a in _loads(assistants) if isinstance(a, dict) and a.get("name") == vault["agent"]), {})
        with _lock:
            now = online(_live.get(token_id))
        found.append(({"token_id": token_id, "user_id": user_id, "agent": vault["agent"],
                       "label": _label(vault["agent"]), "computer": name, "online": now,
                       "paused": bool(vault.get("paused")), "ready": said.get("ready"),
                       "code": said.get("code") if said.get("code") in CODES else ""}, seen_at))
    if not found:
        return None
    found.sort(key=lambda pair: pair[1], reverse=True)
    found.sort(key=lambda pair: not pair[0]["online"])
    return found[0][0]


def heard(ws: str, request_id: str) -> None:
    """A request's write (``feedback.requests.change_listeners``): a Send
    made, or one withdrawn (``failed``), in a work folder its requester's
    computer keeps with an agent wakes that computer's poll (``sends``).
    Nothing is read while no computer is known here."""
    with _lock:
        if not _live:
            return
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT by, state, work_folder FROM feedback_requests WHERE id = ?",
                           (request_id,)).fetchone()
    if not row or row[1] not in ("sent", "failed"):
        return
    by, _state, w = row
    with connect_users_db() as conn:
        rows = _valid_rows(conn, "c.workspace_id = ? AND c.user_id = ? AND c.vaults LIKE '%\"agent\"%'", (ws, by))
    for token_id, _user_id, _name, vaults, _assistants, _seen_at in rows:
        if any(isinstance(v, dict) and v.get("kind") == "folder" and v.get("folder_id") == w
               and v.get("agent") in AGENTS for v in _loads(vaults)):
            wake(token_id, "sends")


def reset() -> None:
    """Forget every computer's presence (a restart; tests): the rows stay."""
    with _lock:
        for live in _live.values():
            if live.timer is not None:
                live.timer.cancel()
        _live.clear()

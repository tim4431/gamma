"""Running the turns of the agents on this computer (docs/dev/local_agents.md
"The chat's computer side", "A comment sent to an agent").

A turn of the chat's computer side (gamma/local_agents/chats.py,
``queue_chat``), and a comment's thread's (gamma/local_agents/threads.py),
waits in the queue of the directory its agent works in (``chats.place_of``:
a vault's, an offered one, one of its own), keyed ``(ws, turn id)``. The
queues are kept by directory (``_place``): one worker thread a directory
takes its queue in order, at most ``MAX_RUNS`` turns at once on this server,
so a directory never has two agents in it. A ``_ChatTurn`` checks again what
its route checked (where it works, the vault not paused, the turn's agent on
and the person's, the person an editor, the agent ready, writes taken),
makes a directory of its own the first time, resumes the conversation's
session in that directory as it is now (``store.conversation_of``; a
refused resume starts afresh once, said ``fresh``; one another process
holds, a terminal's or an editor's, fails the turn and is never started
afresh: ``sessions.held``, Codex's own refusal), and writes the agent's
message as the turn goes: its text, its steps (relative paths, a command's
program), what changed, its end. Its agent streams (``delta``): the
message's last paragraph grows as the agent writes it, and the whole
message, when it comes, takes its place. It runs the model and effort its
turn asked for (``queue_chat``). A turn runs through ``_Turning``, which
gives a turn of this server Gamma's tools (``_tools``: a token of the
person's for the workspace, gamma/local_agents/tools.py), deleted as the
turn ends, however it ends; a watchdog thread stops it on the person's
Stop, its agent turned off (``stop_agent``), the vault paused or removed,
the server closing, or ``RUN_TIMEOUT_S`` after the turn began.
``chat_turns`` lists an account's turns that are queued or running, for
Background tasks.

A turn of another server's conversation with this computer's agent (step
4d of docs/research/agent-collab.md, step 4.2 of
docs/research/agents-as-participants-plan.md) runs the same way, its
conversation's reads and writes through its home
(gamma/local_agents/home.py ``HttpChatHome``, a local one's
``LocalChatHome``): that server keeps the conversation and decides whose
turn it is, and the channel (gamma/computers/channel.py) queues each where
it works here (``queue_remote_chat``: ``home.far_place`` of the id that
server named), keyed ``(the channel's id, turn id)``, so it never reads as
a turn of a workspace here, and stops the ones stopped there
(``stop_remote_chat``). The turn is claimed there for this computer's run
of it, with Gamma's tools of that server where their address is that
server's own (``home.HttpChatHome.gamma``; that server mints and deletes
their token), its message written there at most every
``home.WRITE_EVERY_S`` (the last tried again while that server cannot be
reached, a lost one by the next flush), and a turn there asks on this
computer and, where the channel lets it, there too (below).

A turn whose agent may ask (``ask_refusal``, read at the turn's start:
``store.runs_of`` ``ask``) starts its agent with ``ask``. Each ask the agent
makes is held for the turn under an id of Gamma's and written in the
message (``asks``, its step ``asked``) until the person answers it
(``answer_chat``: the step ``allowed`` or ``declined``), the agent withdraws
it, ``ASK_TIMEOUT_S`` passes (Gamma declines it) or the turn stops (its
agent declines what waits). A command the person allowed in this chat
(``store.grant``) is answered at once when the agent asks exactly it again,
in the same directory and to the same network host, never shown. What may
run set back to never (``runs_never``) declines what waits, and whatever
the turn asks after. The time an ask waits counts toward neither the turn's
deadline nor its agent's silence. A turn holds at most ``MAX_ASKS`` asks
and, after ``MAX_DECLINES`` declines of the person's in a row, asks no
more; a file change asking to write below a root for the rest of the
session (Codex's ``grantRoot``) is declined unseen.

A co-author's turn (decision 13 of
docs/research/agents-as-participants-plan.md: a person the agent's owner
named on this computer, ``store.people``) runs as its owner's does, for that
person: their conversation, their token, their name in the prompt, in the
directory of the synced folder its page is a file of alone. What it asks is
allowed once at most (``once`` on the ask; no grant is looked up or kept),
answered by that person in the chat or by the owner on this computer (its
ask a card of ``computer_asks`` too); another server's co-author's turn asks
only where its channel relays asks (below), once at most there too. The
person no longer named stops their turn (``stop_person``) and fails the
next one when it is taken.

A turn of another server's asks on this computer (step 5b of
docs/research/agent-collab.md): its agent's ``ask``, that server reached
securely (``remote_servers.plain`` "") and the desktop app's secret set,
each checked again at every ask. The desktop app reads the asks of the
account's channels (``computer_asks``, held while nothing changes:
``wait_asks``) and answers them in its own window (``answer_computer``).
Where the channel lets that server answer what this computer's agents ask
(its ``asks``, decision 7 of docs/research/agents-as-participants-plan.md,
step 4.3; ``_relays``) the ask is relayed too: written in that server's
message as a chat turn's is (``asks``, each ``once``: no grant is looked
up or kept for a turn there), answered there by the turn's person or the
agent's owner, whose answer comes back over the channel
(``answer_relayed``); whichever answer comes first is the one, and the
other card closes. Without it that server's message learns only
``waiting`` and the step's status, its step ids none of this computer's,
and an ask comes only while the app has read the asks lately
(``SHELL_SEEN_S``, else it waits ``SHELL_GRACE_S`` for its first read). One
this computer cannot show whole (``FAR_MAX_COMMAND``, ``FAR_MAX_LINES``,
``FAR_MAX_PATHS``, a cut diff, a host that does not read as one) is
declined unseen either way. Such a turn's waiting moves its deadline by
``FAR_WAIT_S`` in all, and its writes after an ask or an answer go to the
flush thread, so an answer never waits on that server.

Locking: ``_lock`` guards the queues, the workers, the active turns and
their stop events, and nothing else; no call into ``store``, ``catalog``,
``chats`` or a driver is made while it is held.
"""

import atexit
import collections
import os
import re
import shlex
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from .. import agents, config, integrations, remote_servers
from ..blocks_store import new_block_id
from ..db import format_stamp, page_now
from ..logbuf import log
from . import catalog, chats, enabled, events, home, procs, prompt, sessions, store, threads, tools

RUN_TIMEOUT_S = 1800              # env GAMMA_AGENT_RUN_TIMEOUT: a turn running this long is stopped
MAX_RUNS = 2                      # turns at once on this server, at most one per directory
SNAPSHOT_FILES = 20_000           # files a snapshot of the folder notes at most
SNAPSHOT_S = 3.0                  # and how long it may take
SKIPPED_DIRS = {".git", ".gamma", "node_modules", "__pycache__", ".venv", "venv"}
# What a build writes beside the sources: a change to one of these is the
# build's, not the agent's edit.
BUILD_OUTPUTS = (".pdf", ".aux", ".log", ".synctex.gz", ".synctex", ".fls", ".fdb_latexmk", ".out", ".toc", ".bbl",
                 ".blg", ".bcf", ".run.xml", ".xdv", ".dvi", ".nav", ".snm", ".lof", ".lot", ".idx", ".ind", ".ilg",
                 ".bak")
MAX_ERROR = 16_000                # characters of a turn's error record
CHAT_EVERY_S = 0.5                # a turn's message is written at most this often
ASK_TIMEOUT_S = 600               # an ask nobody answers this long is declined
TOKEN_SLACK_S = 60                # a turn's token, renewed while its deadline moves, lives this much past it
ASK_EXPIRED = "No answer within 10 minutes."
RUNS_NEVER = "The owner no longer lets you ask to run commands in this folder."
MAX_ASKS = 20                     # asks one turn may hold for the person; the rest are declined
MAX_DECLINES = 3                  # declines in a row after which a turn's asks are declined unseen
SHELL_SEEN_S = 60                 # an ask on this computer is held while the desktop app read the asks this recently
SHELL_GRACE_S = 15                # else it waits this long for the app's first read
FAR_WAIT_S = 600                  # a remote turn's deadline moves at most this much in all while it waits
FAR_MAX_COMMAND = 8000            # characters of a command a remote ask may hold
FAR_MAX_LINES = 200               # lines of it
FAR_MAX_PATHS = 20                # paths of a remote files ask
COMPUTER_DECISIONS = ("once", "deny")   # what the desktop app answers an ask on this computer
ASKED_TOO_OFTEN = ("This turn asked 20 times: Gamma declines the rest of its asks. Finish without them, or say "
                   "what you still need.")
DECLINED_OFTEN = ("The owner declined three asks in a row: Gamma declines the rest of this turn's asks. Finish "
                  "without them, or say what you still need.")
NOT_SECURE = "Gamma asks on this computer only for a server reached over https."
NOBODY_HERE = "Nobody can answer on this computer now."
TOO_LONG = "This ask is too long to read on this computer."
HOST_UNREAD = "Gamma could not read the network address this command reaches."
ONE_AT_A_TIME = "Gamma allows file changes one at a time."
FAR_ASKS = "This is answered on the computer that runs the agent."
NO_ASK_HERE = "No question on this computer has that id."
ONCE_ONLY = "A co-author's turn is allowed once at most."
UNNAMED = "The owner of {agent} on this computer no longer lets you use it."
LENT_FOLDER = "{agent} works for {person} only on the files of a synced folder."

# Why a turn's stop was set: the words it fails with. "link" is decided when
# the turn ends, from what the vault is then (``_link_reason``), unless the
# stop said it (``stop_link``'s ``why``, a key of ``STOPPED``); "person" is
# the person's Stop (it ends ``stopped``).
CLOSING, LINK, PERSON = "closing", "link", "person"
STOPPED = {"off": "Stopped: the agent was turned off on this computer.",
           "unnamed": "Stopped: the agent's owner no longer lets you use it.",
           "unoffered": "Stopped: the directory is no longer offered on this computer.",
           "paused": "Stopped: the synced folder was paused.",
           "removed": "Stopped: the synced folder is no longer kept on this computer.",
           "detached": "Stopped: the synced folder was detached.",
           "changed": "Stopped: the synced folder changed on this computer.",
           "unlinked": "Stopped: this computer no longer answers that server.",
           CLOSING: "Stopped: Gamma is closing."}

_lock = threading.Lock()
_queues: dict[str, collections.deque] = {}     # directory (``_place``) -> (ws, turn id) waiting, oldest first
_workers: dict[str, threading.Thread] = {}     # directory -> the thread taking its queue
_queued: dict[tuple[str, str], str] = {}       # (ws, turn id) in some queue -> its vault's id ("" for none)
# (ws, turn id) -> {link_id, dir, stop, reason, claimed, agent, slot, detail, chat, since}
_active: dict[tuple[str, str], dict] = {}
# a turn's id -> {ws, conversation_id, thread_id, bucket, dir, agent, user_id, owner, model, effort, at}, until it ends
_chats: dict[str, dict] = {}
_slots = threading.BoundedSemaphore(MAX_RUNS)
_registered = False
# The asks waiting on this computer (remote turns'): a version that rises at
# each change, which the desktop app's read waits on, and when the app last
# read them (monotonic; never yet: monotonic counts from boot on Windows and
# Linux, so 0.0 would read as just after one).
_asks_cond = threading.Condition()
_asks_version = 0
_shell_seen = float("-inf")


def _run_timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("GAMMA_AGENT_RUN_TIMEOUT") or RUN_TIMEOUT_S))
    except ValueError:
        return RUN_TIMEOUT_S


def register() -> None:
    """``shutdown`` runs at exit; once."""
    global _registered
    if not _registered:
        _registered = True
        atexit.register(shutdown)


# --- the queues -------------------------------------------------------------------------------

def _place(path: str) -> str:
    """The key of the queue of the directory ``path``: the path as this
    computer's file system compares it (case folded on Windows)."""
    return os.path.normcase(os.path.normpath(str(path)))


def _enqueue(place: str, key: tuple, link_id: str) -> None:
    """``key`` (of the vault ``link_id``, "" for none) at the end of the
    queue of the directory ``place``, its worker started when it has none.
    Under ``_lock``."""
    _queued[key] = link_id
    _queues.setdefault(place, collections.deque()).append(key)
    if place not in _workers:
        worker = threading.Thread(target=_work, args=(place,), name=f"agent-{link_id or key[1]}", daemon=True)
        _workers[place] = worker
        worker.start()


def queue_chat(ws: str, place: dict, turn_id: str, *, conversation_id: str, bucket: str, agent: str,
               user_id: str = "", owner: str = "", thread_id: str = "", model: str = "", effort: str = "") -> None:
    """A turn of ``user_id`` (``chats.begin`` wrote it ``queued``) with the
    agent of ``owner`` (the account itself but for a co-author's), in the
    conversation of the comment thread ``thread_id`` ("" for a chat's),
    asking for ``model`` and ``effort`` ("" for the agent's default), waits
    in the queue of the directory of its ``place`` (``chats.place_of``),
    behind what is there."""
    path = place["path"] if place["kind"] != "own" else str(chats.own_dir(ws, conversation_id))
    with _lock:
        _chats[turn_id] = {"ws": ws, "conversation_id": conversation_id, "thread_id": thread_id, "bucket": bucket,
                           "dir": place["dir"], "agent": agent, "user_id": user_id, "owner": owner or user_id,
                           "model": model, "effort": effort, "at": page_now()}
        _enqueue(_place(path), (ws, turn_id), (place["link"] or {}).get("id", ""))


def queue_remote_chat(link_id: str, turn_id: str, *, conversation_id: str, bucket: str, agent: str, dir) -> bool:
    """A turn of the agent ``agent`` that the server of the channel
    ``link_id`` gave this computer (the channel's ``GET
    /api/computers/chats``), at the end of the queue of the directory it
    works in (``home.far_place`` of the id that server named, ``dir``; one
    it refuses waits in a queue of its own, to fail with why once taken)
    unless it is there or runs → whether it is queued or runs now. The turn
    claims it there before it runs, with its place and its agent as they
    are then; no account here is its (Background tasks list it there). One
    left open here that ``_fail_remote`` ends there now is not queued
    (False): that server lists it again once it is settled, so the end of
    an old claim never lands on a new one under the same run's id."""
    from ..computers import channel  # local: it imports folder_links, which imports this package

    if not enabled():
        return False
    link = channel.get(link_id)
    try:
        place = home.far_place(link, dir, conversation_id) if link is not None else None
    except ValueError:
        place = None
    key = (link_id, turn_id)
    with _lock:
        if turn_id in _settling:
            return False
        if key in _queued or key in _active:
            return True
        _chats[turn_id] = {"ws": link_id, "channel": link_id, "conversation_id": conversation_id, "thread_id": "",
                           "bucket": bucket, "dir": dir, "agent": agent, "user_id": "", "owner": "", "model": "",
                           "effort": "", "at": page_now()}
        _enqueue(_place(place["path"]) if place else f"{link_id}:{turn_id}", key,
                 (place["link"] or {}).get("id", "") if place else "")
    return True


def stop_remote_chat(link_id: str, turn_id: str, run_id: str = "") -> bool:
    """The turn ``turn_id`` of the channel ``link_id``'s server was stopped
    there (or is no longer queued or running there): one queued leaves its
    queue, one that runs (as ``run_id``, when that server names it) stops
    as a Stop does → whether one did."""
    key = (link_id, turn_id)
    with _lock:
        run = _active.get(key)
        if run is not None:
            if run["stop"].is_set() or (run_id and run_id != home.chat_run_id(turn_id)):
                return False
            run["reason"] = PERSON
            run["stop"].set()
            return True
        if key in _queued:
            _unqueue(key)
            _chats.pop(turn_id, None)
            return True
    return False


def _unqueue(key: tuple) -> None:
    """``key`` out of the queue it waits in. Under ``_lock``."""
    _queued.pop(key, None)
    for queue in _queues.values():
        if key in queue:
            queue.remove(key)


def end_stopped_remote(link_id: str, turn_id: str, run_id: str = "") -> bool:
    """The turn ``turn_id`` of the channel ``link_id``'s server was stopped
    there while this computer's run held it, and this computer neither
    queues nor runs it (``stop_remote_chat`` found none): a turn left open
    here, its claim or its end never answered while the channel stayed
    connected. It ends ``stopped`` there and here, in the background
    (``_fail_remote``), so that server's Stop never waits for the channel's
    next announce → whether it is being ended. A turn of another run
    (``run_id``) is not this computer's to end."""
    if not enabled() or (run_id and run_id != home.chat_run_id(turn_id)):
        return False
    with _lock:
        if turn_id in _settling or any(key_id == turn_id for _, key_id in (*_active, *_queued)):
            return False
    turn = next((turn for turn in store.open_turns() if turn["id"] == turn_id and turn["ws"] == turn["link_id"]
                 and turn["link_id"] == link_id), None)
    if turn is None:
        turn = {"id": turn_id, "link_id": link_id, "agent": ""}   # held there, never recorded here
    threading.Thread(target=_fail_remote, args=([turn],), kwargs={"state": "stopped"},
                     name="agent-end-stopped", daemon=True).start()
    return True


# --- what the routes ask ----------------------------------------------------------------

def at_work(link_ids) -> dict[str, dict]:
    """``{link id: {ws, id, agent, detail}}`` of the vaults among
    ``link_ids`` where an agent works now: a turn that runs, ``detail``
    what its step shows a member (``_said``)."""
    wanted = set(link_ids)
    with _lock:
        return {run["link_id"]: {"ws": ws, "id": key_id, "agent": run.get("agent") or "",
                                 "detail": run.get("detail", "")}
                for (ws, key_id), run in _active.items() if run["link_id"] in wanted and run["claimed"]}


def chat_state(ws: str, turn_id: str) -> dict | None:
    """Where the turn ``turn_id`` stands in the runner: ``{turn_id, state:
    "queued" | "running", ahead}``, ``ahead`` whether another turn goes
    first (of its directory, or holding the slots); None for a turn the
    runner does not hold."""
    key = (ws, turn_id)
    with _lock:
        run = _active.get(key)
        if run is not None:
            if run["claimed"]:
                return {"turn_id": turn_id, "state": "running", "ahead": False}
            return {"turn_id": turn_id, "state": "queued", "ahead": not run["slot"]}
        if key not in _queued:
            return None
        for place, queue in _queues.items():
            if key in queue:
                ahead = queue.index(key) > 0 or any(r["dir"] == place for r in _active.values())
                return {"turn_id": turn_id, "state": "queued", "ahead": ahead}
    return None


def chat_turns(user_id: str) -> list[dict]:
    """The turns this server holds, queued or running, of ``user_id`` and
    of the co-authors its agents serve (``chats.readable``), oldest first:
    ``{ws, turn_id, conversation_id, thread_id, bucket, agent, user_id,
    state: "queued" | "running", asking, stopping, queued_at,
    started_at}``, ``user_id`` the account whose turn it is, ``asking``
    whether an ask of it waits for an answer, ``stopping`` whether its stop
    is set (Background tasks' rows, routers/jobs.py)."""
    out = []
    with _lock:
        for turn_id, chat in _chats.items():
            if not user_id or user_id not in (chat.get("user_id"), chat.get("owner")):
                continue
            run = _active.get((chat["ws"], turn_id))
            running = run is not None and run["claimed"]
            turning = run.get("chat") if run is not None else None
            out.append({"ws": chat["ws"], "turn_id": turn_id, "conversation_id": chat["conversation_id"],
                        "thread_id": chat.get("thread_id") or "", "bucket": chat["bucket"], "agent": chat["agent"],
                        "user_id": chat["user_id"],
                        "state": "running" if running else "queued",
                        "asking": bool(turning is not None and turning.asks),
                        "stopping": bool(run is not None and run["stop"].is_set()),
                        "queued_at": chat.get("at") or "", "started_at": (run or {}).get("since", "")})
    return sorted(out, key=lambda t: t["queued_at"])


def stop_chat(ws: str, turn_id: str, conversation_id: str) -> bool:
    """The person's Stop of a turn of the conversation: a running one's
    stop is set (it ends ``stopped``), a queued one leaves its queue and
    ends ``stopped``, and so does one whose message still reads queued or
    running though the runner does not hold it (not queued yet, or left by
    a server that stopped) → whether there was one to stop. One its worker
    holds while it waits for a slot gets its stop and ends ``stopped`` now,
    its message too while it reads queued, so the conversation takes the
    next message at once: given its slot, it finds its message ended and
    writes nothing more (``_ChatTurn._work``)."""
    key = (ws, turn_id)
    with _lock:
        run, stopping = _active.get(key), False
        if run is not None:
            if not run["stop"].is_set():
                run["reason"] = PERSON
                run["stop"].set()
                stopping = True
            if run["slot"]:
                return True
        else:
            _unqueue(key)
            _chats.pop(turn_id, None)
    _, reply = chats.reply_of(ws, conversation_id, turn_id)
    # One held without a slot is ended here while its message reads queued;
    # given its slot meanwhile, the turn ends it itself.
    if reply is None or reply.get("state") not in (chats.BUSY if run is None else ("queued",)):
        return stopping
    # Its message first: a turn queued in the meantime then finds it ended.
    chats.write_reply(ws, conversation_id, turn_id, {"state": "stopped", "ended_at": page_now()})
    store.end_turn(turn_id, state="stopped")
    return True


def answer_chat(ws: str, turn_id: str, ask_id: str, decision: str, note: str = "") -> None:
    """The person's answer to the ask ``ask_id`` of the turn ``turn_id``
    (``_ChatTurn.answer``). ChatError 409 for a turn that holds no ask any
    more (it ended, or waits for its slot), and as ``_ChatTurn.answer``
    says."""
    with _lock:
        run = _active.get((ws, turn_id))
        turning = run.get("chat") if run is not None else None
    if turning is None:
        raise chats.ChatError(409, "This turn has ended.")
    turning.answer(ask_id, decision, note)


def runs_never(agent: str) -> None:
    """What ``agent`` may run was set back to never: each turn of it
    running declines the asks that wait (``_ChatTurn.decline_all``) and
    goes on; what it asks next is declined unseen (``_ChatTurn._asked``)."""
    with _lock:
        turnings = [run["chat"] for run in _active.values()
                    if run["agent"] == agent and run.get("chat") is not None]
    for turning in turnings:
        try:
            turning.decline_all(RUNS_NEVER)
        except Exception as e:  # noqa: BLE001 — what it may run is set; the turn's end takes its cards
            log.warning(f"[agents] declining the asks of chat turn {turning.turn_id}: {e}")


# --- asking on this computer -------------------------------------------------------------------

def ask_refusal(server: str, remote: bool, agent: str, relays: bool = False) -> str:
    """Why a turn of ``agent`` may not ask now, "" when it may: the agent's
    ``ask`` (``store.runs_of``); for a turn of another server (``remote``,
    ``server`` its address, "" for a channel gone) also that server reached
    securely (https, or this computer) and, unless that server answers too
    (``relays``, the channel's ``asks``), the desktop app's secret set
    (without it there is no window to ask in). The app's presence is each
    ask's (``_ChatTurn._asked``)."""
    if store.runs_of(agent) != "ask":
        return RUNS_NEVER
    if not remote:
        return ""
    if not server:
        return RUNS_NEVER
    if remote_servers.plain(server) != "":
        return NOT_SECURE
    if not relays and not config.shell_secret():
        return NOBODY_HERE
    return ""


def _asks_changed() -> None:
    """The asks waiting on this computer gained or lost one: the version
    rises, and the desktop app's read waiting on it answers."""
    global _asks_version
    with _asks_cond:
        _asks_version += 1
        _asks_cond.notify_all()


def asks_version() -> int:
    with _asks_cond:
        return _asks_version


def shell_listening() -> bool:
    """Whether the desktop app read the asks within ``SHELL_SEEN_S``."""
    return time.monotonic() - _shell_seen <= SHELL_SEEN_S


def wait_shell(timeout: float) -> bool:
    """Whether the desktop app reads the asks within ``timeout`` seconds
    (at once when it read them lately)."""
    end = time.monotonic() + timeout
    with _asks_cond:
        while not shell_listening():
            left = end - time.monotonic()
            if left <= 0:
                return False
            _asks_cond.wait(left)
        return True


def wait_asks(after: int, timeout: float) -> int:
    """The desktop app reads the asks: it is heard (``_shell_seen``, which
    wakes an ask waiting for it), and the read is held while the version
    is ``after``, at most ``timeout`` seconds (20 at most) → the version."""
    global _shell_seen
    end = time.monotonic() + max(0.0, min(float(timeout), 20.0))
    with _asks_cond:
        _shell_seen = time.monotonic()
        _asks_cond.notify_all()
        while _asks_version == after:
            left = end - time.monotonic()
            if left <= 0:
                break
            _asks_cond.wait(left)
        _shell_seen = time.monotonic()
        return _asks_version


def _computer_turnings() -> list:
    """The turns running here whose asks are answered on this computer:
    those of other servers (answered there too where the channel relays
    them), and the co-authors' turns of this server's (answered in their
    chat too)."""
    with _lock:
        return [run["chat"] for run in _active.values()
                if run.get("chat") is not None and (run["chat"].home.remote or run["chat"].lent)]


def computer_asks(user_id: str) -> list[dict]:
    """The asks waiting on remote turns of the channels and vaults
    ``user_id`` made, and on co-authors' turns of the agents it owns, oldest
    first, each as the desktop app draws it (``_ChatTurn._card``)."""
    held = []
    for turning in _computer_turnings():
        if turning.maker != user_id:
            continue
        with turning._asking:
            held += [(ask["since"], ask["card"]) for ask in turning.asks.values() if ask.get("card")]
    return [dict(card) for _, card in sorted(held, key=lambda pair: pair[0])]


def answer_computer(ask_id: str, decision: str, user_id: str) -> int:
    """The desktop app's answer to the ask ``ask_id`` of a remote turn of a
    channel ``user_id`` made, or of a co-author's turn of its agent: ``once``
    or ``deny`` (``_ChatTurn._answer_here``) → the asks' version once the
    agent has it. ChatError 404 for no such ask (or another account's), 400
    for another decision, 409 as ``_answer_here`` says, or for a turn that
    ended."""
    for turning in _computer_turnings():
        with turning._asking:
            found = ask_id in turning.asks or ask_id in turning.answered
        if found and turning.maker == user_id:
            break
    else:
        raise chats.ChatError(404, NO_ASK_HERE)
    if decision not in COMPUTER_DECISIONS:
        raise chats.ChatError(400, "On this computer an ask is allowed once or declined.")
    if turning._ended:
        raise chats.ChatError(409, "This turn has ended.")
    turning._answer_here(ask_id, decision)
    return asks_version()


def answer_relayed(link_id: str, turn_id: str, run: str, ask_id: str, decision: str, note: str = "") -> bool:
    """The answer the server of the channel ``link_id`` kept for the ask
    ``ask_id`` of its turn ``turn_id`` (``chats.answers_of`` there, the
    listing's ``answers``), given to the agent while this computer runs that
    turn under the run ``run``, the ask waits, the decision is ``once`` or
    ``deny`` and the channel still lets that server answer (``_relays``) →
    whether the agent took it. One answered on this computer first, or
    withdrawn, declined by Gamma, of a turn that ended, changes nothing:
    the first answer is the one."""
    with _lock:
        active = _active.get((link_id, turn_id))
        turning = active.get("chat") if active is not None else None
    if (turning is None or not turning.home.remote or turning.home.run_id != run or turning._ended
            or decision not in COMPUTER_DECISIONS or not turning._relays()):
        return False
    return turning._decide(ask_id, decision, note)


def relays_changed(link_id: str) -> None:
    """The channel ``link_id`` lets its server answer what this computer's
    agents ask, or no longer does (``channel.set_asks``): the message there
    of each of its turns running here is written again, its asks with it or
    without them (``_ChatTurn._progress``)."""
    with _lock:
        turnings = [run["chat"] for (ws, _turn_id), run in _active.items()
                    if ws == link_id and run.get("chat") is not None]
    for turning in turnings:
        turning._show_later()


def stop_link(link_id: str, why: str = "") -> None:
    """Stop the turn of the vault ``link_id``, if one runs: the vault was
    paused, relinked, removed or its push lowered (what fits is read when
    the turn ends), or ``why`` says it (a key of ``STOPPED``): ``removed``
    for a vault being removed, whose row may outlast the turn's end,
    ``detached`` for a work folder its other server detached. The turns
    queued for it stay: each, taken, finds the vault as it is now and fails
    with why."""
    with _lock:
        for run in _active.values():
            if run["link_id"] == link_id and not run["stop"].is_set():
                run["reason"] = why or LINK
                run["stop"].set()


def stop_channel(link_id: str, why: str) -> None:
    """Stop the turns of the channel ``link_id``'s server that run here,
    ``why`` a key of ``STOPPED`` (``unlinked``: this computer no longer
    answers that server). One queued fails when it is taken (its channel
    gone)."""
    with _lock:
        for (ws, _turn_id), run in _active.items():
            if ws == link_id and not run["stop"].is_set():
                run["reason"] = why
                run["stop"].set()


def stop_dirs(paths) -> None:
    """Stop the turns that work in the directories ``paths``, no longer
    offered (``store.set_dirs``): each fails with why. One queued there
    fails when it is taken (``_ChatTurn._work``: its place is no longer
    that directory)."""
    gone = {_place(path) for path in paths}
    with _lock:
        for run in _active.values():
            if not run["link_id"] and run["dir"] in gone and not run["stop"].is_set():
                run["reason"] = LINK
                run["stop"].set()


def stop_person(agent: str, server: str, ws: str, account: str) -> None:
    """The account ``account`` of the workspace ``ws`` on ``server`` (""
    this one) may no longer use ``agent``: its co-author's turns of it that
    run here stop, failed with why (``unnamed``); one queued fails when it
    is taken (``_ChatTurn._work``)."""
    from ..computers import channel  # local: it imports folder_links, which imports this package

    with _lock:
        turnings = [(run, run["chat"]) for run in _active.values()
                    if run.get("chat") is not None and run["chat"].lent
                    and run["chat"].agent == agent and run["chat"].person == account]
    for run, turning in turnings:
        if turning.home.remote:
            link = channel.get(turning.ws)
            here = link is not None and (link["remote_url"], link["workspace_id"]) == (server, ws)
        else:
            here = not server and turning.ws == ws
        if here:
            with _lock:
                if not run["stop"].is_set():
                    run["reason"] = "unnamed"
                    run["stop"].set()


def stop_agent(agent: str) -> None:
    """Stop every turn of ``agent``, which was turned off (why is read when
    each ends: ``_Turning._link_reason``). What waits in the queues stays:
    each, taken, fails with why."""
    with _lock:
        for run in _active.values():
            if run["agent"] == agent and not run["stop"].is_set():
                run["reason"] = LINK
                run["stop"].set()


def release_account(user_id: str) -> None:
    """The agents of the account ``user_id``, which is deleted, turned off
    (``store.release_account``): their turns stop, and the open pages and
    the other servers hear it."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    released = store.release_account(user_id)
    for agent in released:
        stop_agent(agent)
    if released:
        folder_links.agents_changed()


def shutdown(timeout: float = 5.0) -> None:
    """Stop every turn (Gamma is closing), then every agent's process."""
    with _lock:
        for run in _active.values():
            if not run["stop"].is_set():
                run["reason"] = CLOSING
                run["stop"].set()
        for queue in _queues.values():
            queue.clear()
        _queued.clear()
    procs.stop_all(timeout)
    _asks_changed()   # what the desktop app reads goes with the turns


def wait_idle(timeout: float = 30) -> bool:
    """Whether every queue and turn ends within ``timeout`` seconds (tests)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _workers and not _active:
                return True
        time.sleep(0.05)
    with _lock:
        return not _workers and not _active


def reset() -> None:
    """Stop and forget every queue, worker and turn, and that the desktop
    app read the asks (tests)."""
    global _slots, _shell_seen
    with _lock:
        for run in _active.values():
            run["reason"] = run["reason"] or CLOSING
            run["stop"].set()
        for queue in _queues.values():
            queue.clear()
        _queued.clear()
    wait_idle(30)
    with _lock:
        _queues.clear()
        _workers.clear()
        _active.clear()
        _chats.clear()
        _slots = threading.BoundedSemaphore(MAX_RUNS)
    _shell_seen = float("-inf")
    _asks_changed()


# --- the startup -------------------------------------------------------------------------

RECOVER_WAIT_S = 600   # how long the startup waits for a workspace behind on its migrations to be upgraded
RECOVER_POLL_S = 5


def recover() -> None:
    """At startup: the agents the vaults had before agents were the
    computer's become its own, once (``store.carry_over``); the tokens of
    the turns a server that stopped left are deleted
    (``integrations.drop_turns``: no turn runs yet); what this computer
    kept of vaults gone meanwhile is forgotten; then, in the background,
    the turns it left open fail, each its message too, and the directories
    of their own of conversations gone meanwhile are removed
    (``chats.sweep_dirs``). A workspace behind on its migrations is waited
    for (the background walk upgrades it, ``migrations.warming``), at most
    ``RECOVER_WAIT_S``: a turn not failed by then stays open for the next
    start. A turn of a work folder of another server fails there, in the
    background (``_fail_remote``); the turns that wait there are the
    channel's to hear once it announces."""
    from .. import folder_links, migrations  # local: folder_links imports this package's modules inside functions
    from ..computers import channel

    store.carry_over()
    dropped = integrations.drop_turns()
    if dropped:
        log.info(f"[agents] deleted {dropped} token(s) of turns a stopped server left")
    links = {link["id"] for link in channel.links()}
    store.forget_links({link["id"] for link in folder_links.all_links()} | links)
    # A remote turn's row names its channel in place of the workspace.
    jobs, turns = [], []
    for turn in store.open_turns():
        if turn["ws"] == turn["link_id"]:
            turns.append(turn)
            continue
        jobs.append((turn["ws"], f"the chat turn {turn['id']} left open", lambda turn=turn: _fail_left_open(turn)))
    if config.AGENTS_DIR.is_dir():   # a channel's directories (home.far_dir) stay while it is kept
        jobs += [(top.name, f"the directories of conversations gone in workspace {top.name}",
                  lambda ws=top.name: chats.sweep_dirs(ws)) for top in config.AGENTS_DIR.iterdir()
                 if top.is_dir() and top.name not in links]
    if turns:
        threading.Thread(target=_fail_remote, args=(turns,), name="agent-recover-remote", daemon=True).start()

    def when_upgraded():
        left, end = jobs, time.monotonic() + RECOVER_WAIT_S
        while left:
            behind = []
            for job in left:
                ws, what, do = job
                try:
                    if migrations.is_behind(ws):
                        behind.append(job)
                    else:
                        do()
                except Exception as e:  # noqa: BLE001 — one turn or workspace never keeps the others
                    log.warning(f"[agents] {what}: {e}")
            left = behind
            if left and time.monotonic() >= end:
                log.warning(f"[agents] {len(left)} workspace job(s) not upgraded in time: open chat turns wait for "
                            "the next start")
                return
            if left:
                time.sleep(RECOVER_POLL_S)

    if jobs:
        threading.Thread(target=when_upgraded, name="agent-recover", daemon=True).start()


_settling: set[str] = set()   # the ids of the turns left open that a ``_fail_remote`` fails now


def _fail_remote(turns: list[dict], *, state: str = "failed") -> None:
    """The turns of work folders of other servers a server that stopped left
    open fail there, each its message (``HttpChatHome.fail_left_open``;
    ``state`` stopped ends them stopped, ``end_stopped_remote``); one whose
    server is not reached stays open for the next start, or the next
    announce of its channel (``fail_remote_of``). A turn queued or running
    here by the time its write goes is skipped: claimed again under the
    same run's id, it is this computer's live run there
    (``queue_remote_chat`` queues none while it is settled)."""
    with _lock:
        turns = [turn for turn in turns if turn["id"] not in _settling]
        _settling.update(turn["id"] for turn in turns)
    try:
        for turn in turns:
            key = (turn["link_id"], turn["id"])
            with _lock:
                if key in _active or key in _queued:
                    continue
            try:
                settled = home.HttpChatHome(turn["link_id"], turn["id"]).fail_left_open(turn, state=state)
            except Exception as e:  # noqa: BLE001 — one turn never keeps the others
                log.warning(f"[agents] ending the chat turn {turn['id']} left open of vault {turn['link_id']} "
                            f"there: {e}")
                continue
            if settled:
                store.end_turn(turn["id"], state=state,
                               error="Gamma stopped while the turn was open." if state == "failed" else "")
    finally:
        with _lock:
            _settling.difference_update(turn["id"] for turn in turns)


def fail_remote_of(link_id: str) -> None:
    """The channel ``link_id`` announced this computer to its server again
    (gamma/computers/channel.py): its turns left open while that server
    could not be reached (their end or their claim unanswered), none of
    them running now, fail there in the background (``_fail_remote``), not
    only at the next start."""
    with _lock:
        turning = {key_id for _, key_id in _active}
    turns = [turn for turn in store.open_turns() if turn["link_id"] == link_id and turn["ws"] == turn["link_id"]
             and turn["id"] not in turning]
    if turns:
        threading.Thread(target=_fail_remote, args=(turns,), name="agent-fail-remote", daemon=True).start()


def _fail_left_open(turn: dict) -> None:
    """A turn a server that stopped left open fails, its message too when
    it still reads queued or running."""
    label = catalog.label(turn["agent"])
    _, reply = chats.reply_of(turn["ws"], turn["conversation_id"], turn["id"])
    if reply is not None and reply.get("state") in chats.BUSY:
        chats.write_reply(turn["ws"], turn["conversation_id"], turn["id"],
                          {"state": "failed", "error": f"Gamma stopped while {label} worked on this.",
                           "asks": [], "ended_at": page_now()})  # an ask left waiting goes: nobody answers it now
    store.end_turn(turn["id"], state="failed", error="Gamma stopped while the turn was open.")


# --- the workers ---------------------------------------------------------------------------

def _work(place: str) -> None:
    """The directory's worker: its queue's turns in order, one at a time;
    it leaves (and forgets itself) in the same step that finds the queue
    empty."""
    while True:
        with _lock:
            queue = _queues.get(place)
            if not queue:
                _queues.pop(place, None)
                if _workers.get(place) is threading.current_thread():
                    del _workers[place]
                return
            key = queue.popleft()
            link_id = _queued.pop(key, "")
            chat = _chats.get(key[1])
            run = {"link_id": link_id, "dir": place, "stop": threading.Event(), "reason": "", "claimed": False,
                   "agent": (chat or {}).get("agent") or "", "slot": False, "detail": ""}
            _active[key] = run
            slots = _slots
        ws, turn_id = key
        try:
            with slots:
                with _lock:
                    run["slot"] = True
                if chat is not None:
                    _ChatTurn(ws, turn_id, link_id, run, chat).go()
        except Exception:  # noqa: BLE001 — a _ChatTurn never raises; a worker never dies of one turn
            log.exception(f"[agents] the chat turn {turn_id} in workspace {ws}")
        finally:
            with _lock:
                if _active.get(key) is run:
                    del _active[key]
                _chats.pop(turn_id, None)


def _snapshot(root: Path) -> tuple[dict, bool]:
    """``({path: (size, mtime_ns)}, whole)`` of the folder's files, its
    relative paths with ``/``, leaving out ``SKIPPED_DIRS`` and every
    directory whose name starts with a dot; at most ``SNAPSHOT_FILES``
    files and ``SNAPSHOT_S`` seconds, ``whole`` false when either cut the
    walk short."""
    out: dict = {}
    end = time.monotonic() + SNAPSHOT_S
    for top, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS and not d.startswith(".")]
        rel_top = Path(top).relative_to(root).as_posix()
        for name in names:
            try:
                st = os.stat(os.path.join(top, name))
            except OSError:
                continue
            out[name if rel_top == "." else f"{rel_top}/{name}"] = (st.st_size, st.st_mtime_ns)
            if len(out) >= SNAPSHOT_FILES:
                return out, False
        if time.monotonic() >= end:
            return out, False
    return out, True


def _changed_files(before: tuple[dict, bool], after: tuple[dict, bool]) -> tuple[list[str], bool]:
    """``(sources, built)``: the source files added, changed or deleted
    between two snapshots, by path (every one: a caller keeps at most
    ``store.MAX_CHANGES``), and whether a build output changed. When either
    walk was cut short, a file one of them did not reach is no change: only
    the files both saw are compared."""
    (old, whole_before), (new, whole_after) = before, after
    if whole_before and whole_after:
        paths = old.keys() | new.keys()
    else:
        paths = old.keys() & new.keys()
    touched = sorted({p for p in paths if old.get(p) != new.get(p)}, key=str.casefold)
    sources = [p for p in touched if not p.lower().endswith(BUILD_OUTPUTS)]
    return sources, len(sources) < len(touched)


def _inside(path: str) -> bool:
    """Whether a driver's path (relative when inside the folder) is the
    folder's."""
    return bool(path) and not (Path(path).is_absolute() or re.match(r"^[A-Za-z]:", path)
                               or path.startswith(("/", "\\", "../", "..\\")) or path == "..")


def _named(path: str, tracked: set[str] | None) -> str:
    """How a turn's detail names a file the agent touched: by its path when
    members can see it already (the vault tracks it), else "a file in the
    folder" or "a file outside the folder", so a file the person left out of
    Gamma is never named to the workspace. ``tracked`` None names every file
    inside the folder."""
    if not _inside(path):
        return "a file outside the folder"
    if tracked is not None and path.casefold() not in tracked:
        return "a file in the folder"
    return events.one_line(path, 200)


# A shell a CLI wraps its commands in (Codex on Windows runs each through
# PowerShell): the program a member is told of is the one inside it.
_WRAPPERS = {"powershell", "pwsh", "cmd", "bash", "sh", "zsh"}
_WRAPPER_FLAGS = re.compile(r"^(?:-(?:command|c|lc|ic|noprofile|nologo|noninteractive|executionpolicy)|/[cdsk])$",
                            re.I)


def _program(command: str) -> str:
    """The name of the program a command line runs (``latexmk`` of
    ``latexmk -pdf main.tex``, and of ``powershell.exe -Command 'latexmk
    -pdf main.tex'``), never its arguments: the inner command of a shell
    wrapper first (``events.unwrap_shell``, what an ask shows), its own
    reading of a wrapper only where that gives none."""
    def words(line: str) -> list[str]:
        try:
            return shlex.split(line, posix=True)
        except ValueError:
            return line.split()

    unwrapped = events.unwrap_shell(command)
    if unwrapped:
        command = unwrapped[1]
    parts = words(command.replace("\\", "/")) if command.strip() else []
    for _ in range(3):  # a wrapper inside a wrapper, at most
        if not parts:
            return ""
        name = re.sub(r"\.(exe|cmd|bat|com)$", "", parts[0].strip("\"'").rsplit("/", 1)[-1], flags=re.I)
        if name.lower() not in _WRAPPERS:
            return events.one_line(name, 60)
        rest = [p for p in parts[1:] if not _WRAPPER_FLAGS.match(p) and p.lower() not in ("bypass", "unrestricted")]
        parts = words(rest[0]) if len(rest) == 1 else rest
    return ""


def _said(event: dict, tracked: set[str] | None = None) -> str:
    """What a step shows a member: ``Reading main.tex``, ``Editing
    sections/intro.tex``, ``Running latexmk``, ``Searching the sources``,
    ``Reading Gamma: read_page``, ``Working``; a file the vault does not
    track is named as no file (``_named``)."""
    kind = event.get("kind")
    if kind in ("read", "edit"):
        what = _named(event.get("path") or "", tracked)
        return f"{'Reading' if kind == 'read' else 'Editing'} {what}"
    if kind == "run":
        program = _program(event.get("command") or "")
        return f"Running {program}" if program else "Running a command"
    if kind == "search":
        return "Searching the sources"
    if kind == "gamma":
        return events.gamma_words(event.get("tool") or "", now=True)
    return "Working"


# An absolute path left in an error once the folder's and the home
# directory's are rewritten: a drive's, a share's, a POSIX root's.
_ABSOLUTE = re.compile(r"(?<![\w.~/:\\])(?:[A-Za-z]:[\\/]|\\\\[^\s\\]+\\|/(?=[A-Za-z]))[^\s\"'`<>|,;]*")


# Where a base path ends in a text: a separator (then a path below it), a
# quote, whitespace, a closing bracket or the text's end; never inside a
# longer name (the folder paper is not paper-old, the home Tim not Timothy).
_BASE_END = r"""(?:(?P<sep>[\\/])|(?=["'`\s)\]}>]|$))"""


def _relative(text: str, root: Path | str, home: str | None = None) -> str:
    """``text`` with the paths below the folder made relative to it and the
    home directory (``home``, this user's by default) written ``~``, each
    only where it is a whole path or a path's start (``_BASE_END``); any
    other path as it is."""
    out = text or ""
    for base, instead in ((str(root), ""), (str(Path.home()) if home is None else home, "~")):
        for form in dict.fromkeys((base.rstrip("\\/"), base.rstrip("\\/").replace("\\", "/"))):
            if len(form) <= 3:  # a drive or the filesystem's root names nothing of the user's
                continue
            out = re.sub(r"(?<![\w.~/\\:-])" + re.escape(form) + _BASE_END,
                         lambda m, instead=instead: (f"{instead}/" if instead else "") if m.group("sep")
                         else (instead or "."), out)
    return out


def _redact(text: str, root: Path | str, home: str | None = None) -> str:
    """``text`` with the paths below the folder made relative to it, the
    home directory written ``~`` (``_relative``), and any other absolute
    path "a file outside the folder": what a member may read of an
    error."""
    return _ABSOLUTE.sub("a file outside the folder", _relative(text, root, home))


def _span(seconds: float) -> str:
    if seconds >= 120:
        return f"{round(seconds / 60)} minutes"
    if seconds >= 60:
        return "1 minute"
    whole = max(1, round(seconds))
    return f"{whole} second{'' if whole == 1 else 's'}"


class _Stop(Exception):
    """The turn ends here; its state and the words were set."""


def _cannot_write(ws: str) -> str:
    """Why this server or the workspace takes no writes now, "" when they
    do: what fails a turn before its agent edits anything."""
    from .. import hosted, workspaces

    refused = hosted.read_only()
    if not refused:
        try:
            workspaces.refuse_writes(ws)
        except HTTPException as e:
            refused = str(e.detail)
    return refused


class _Turning:
    """One turn of an agent in its directory (``_turn``), watched (a stop,
    the deadline) and its events followed (``_heard``), and why a stop the
    vault set was set (``_stop_words``)."""

    def __init__(self, ws: str, name: str, link_id: str, run: dict):
        self.ws, self.name, self.link_id, self.run = ws, name, link_id, run
        self.stop = run["stop"]
        self.agent, self.label = "", "the agent"
        self.owner = ""                 # the account that turned the agent on, whose agent works
        self.person = ""                # the account the turn is for: the owner, or a co-author it serves
        self.turn = None
        self.timed_out = False
        self.steps: list[dict] = []
        self._step_at: dict[str, int] = {}
        self.gamma: events.Gamma | None = None   # Gamma's tools for the turn (``_tools``)
        self._token = ""                          # their token's id, deleted at the end (``_drop_tools``)
        self._token_until = 0.0                   # when it expires, or a failed renewal is tried again (monotonic)
        self._tooled = False                      # ``_tools`` ran

    def _tools(self) -> None:
        """Gamma's tools for a turn of this server (``tools.mint``), once:
        the token of the account the turn is for (its person: the owner, or
        a co-author's) of the workspace, named with the agent's id,
        living the turn's timeout and ``TOKEN_SLACK_S``. A turn mints them
        before its prompt, which says whether it has them."""
        if self._tooled:
            return
        self._tooled = True
        minted = tools.mint(self.person or self.owner, self.ws, self.agent, _run_timeout() + TOKEN_SLACK_S)
        if minted:
            self.gamma, self._token = minted
            self._token_until = time.monotonic() + _run_timeout() + TOKEN_SLACK_S
            log.info(f"[agents] Gamma's tools for {self.label} on {self.name}")

    def _keep_tools(self, deadline: float) -> None:
        """The turn's deadline is ``deadline`` (monotonic): its token lives
        that long, renewed at most once a minute (``TOKEN_SLACK_S`` past the
        deadline); a renewal that failed is tried again a minute later."""
        if self._token and deadline > self._token_until:
            self._token_until = deadline + TOKEN_SLACK_S
            try:
                integrations.renew_turn(self._token, self._token_until - time.monotonic())
            except Exception as e:  # noqa: BLE001 — the watch goes on
                log.warning(f"[agents] renewing the token of {self.name}: {e}")

    def _drop_tools(self) -> None:
        """The turn ended, however it ended: its token is deleted, so a call
        of the agent's after it is refused. Never raises: an expired token
        reads nothing either, and the next start deletes what is left."""
        if not self._token:
            return
        try:
            integrations.drop(self._token)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[agents] deleting the token of {self.name}: {e}")
        else:
            log.info(f"[agents] the token of {self.name} deleted")
        self._token, self.gamma = "", None

    def _turn(self, argv: list[str], root: Path, text: str, session: str, *, images=(), ask: bool = False,
              stream: bool = False, model: str = "", effort: str = "", title: str = "") -> dict:
        """One turn of the agent on ``text`` (``session`` one to resume;
        ``ask``, it may ask; ``stream``, its text as it is written; its
        ``model`` and ``effort``; ``title`` a new session's name) with
        Gamma's tools (``_tools``), watched, its events followed → its done.
        A turn before it is closed first. The tools live as long as this
        turn's deadline, counted from now (``_keep_tools``: what came
        between the mint and the start, a snapshot of a large folder, or a
        turn before it)."""
        if self.turn is not None:
            self.turn.close()
        self._tools()
        self._keep_tools(time.monotonic() + _run_timeout())
        self.turn = events.start(self.agent, argv=argv, cwd=str(root), prompt=text, images=list(images),
                                 session=session, ask=ask, stream=stream, model=model, effort=effort,
                                 gamma=self.gamma, title=title)
        if self.turn.pid:
            self._started(self.turn.pid)
        ended = threading.Event()
        threading.Thread(target=self._watch, args=(self.turn, ended), name=f"agent-watch-{self.name}",
                         daemon=True).start()
        try:
            return self._follow()
        finally:
            ended.set()

    def _resume_refused(self, done: dict, session: str) -> bool:
        """Whether a turn that resumed ``session`` failed as one whose
        session this computer no longer has: before any step, neither
        stopped nor out of time, and not because Gamma's tools did not start
        (``gamma_down``: a session afresh would fail alike, and the
        conversation's would be lost) nor because another process holds it
        (``busy``: afresh would fork it)."""
        return bool(session and not done["ok"] and not done.get("gamma_down") and not done.get("busy")
                    and not self.steps and not self.stop.is_set() and not self.timed_out)

    def _started(self, pid: int) -> None:
        """The turn's process is ``pid``: what the record keeps."""

    def _record(self, done: dict) -> str:
        """This computer's record of the turn: its error and the agent's
        stderr tail."""
        parts = [done.get("error") or ""]
        tail = self.turn.stderr_tail() if self.turn is not None else ""
        if tail:
            parts.append(f"stderr:\n{tail}")
        return "\n".join(p for p in parts if p)

    def _watch(self, turn, ended: threading.Event) -> None:
        """Stop the turn on its stop or at its deadline, counted from
        the turn's start (not from the queue's wait, nor while an ask waits
        for the person, ``_waiting``); between, ``_tick``."""
        deadline = last = time.monotonic()
        deadline += _run_timeout()
        while not ended.is_set():
            now = time.monotonic()
            if self._waiting():
                deadline += now - last
                self._waited(now - last)
                self._keep_tools(deadline)
            last = now
            if self.stop.is_set():
                turn.stop()
                self._stopping()
                return
            if now >= deadline:
                self.timed_out = True
                turn.stop()
                self._stopping()
                return
            self._tick()
            ended.wait(0.1)

    def _waiting(self) -> bool:
        """Whether the turn waits for the person now (an ask): its deadline
        moves meanwhile."""
        return False

    def _waited(self, seconds: float) -> None:
        """The turn waited ``seconds`` more for the person (``_waiting``)."""

    def _stopping(self) -> None:
        """Called on the watching thread once it stopped the turn."""

    def _tick(self) -> None:
        """Called on the watching thread every tenth of a second while the
        turn runs."""

    def _follow(self) -> dict:
        """The turn's events, each heard (``_heard``) → its done."""
        done = None
        stream = self.turn.events()
        try:
            for event in stream:
                done = self._heard(event) or done
        finally:
            stream.close()  # a turn left early still ends its process (events.Turn)
        return done or events.done(ok=False, error=f"{self.label} stopped unexpectedly.")

    def _heard(self, event: dict) -> dict | None:
        """One event of the turn → itself when it is the done."""
        raise NotImplementedError

    def _stop_words(self) -> str:
        reason = self.run["reason"]
        return STOPPED[reason if reason in STOPPED else self._link_reason()]

    def _push(self) -> None:
        """A work folder of another server pushes what the turn changed now,
        not at the next round (no hot round runs for it); a vault paused
        (its maker keeps the edits off that server), choosing its files or
        gone pushes nothing: the next round after it resumes does."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        try:
            link = folder_links.get_link(self.link_id)
            if link is None or link["status"].get(folder_links.PAUSED) or folder_links.choosing(link):
                return
            folder_links.run_in_background(self.link_id, direction="push")
        except Exception as e:  # noqa: BLE001 — the next round pushes it
            log.warning(f"[agents] pushing what {self.name} of vault {self.link_id} changed: {e}")

    def _link_reason(self) -> str:
        """Why the vault stopped its turn, from what it is now."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None:
            return "removed"
        if not store.owns(self.owner, self.agent):
            return "off"
        if link["status"].get(folder_links.PAUSED):
            return "paused"
        return "changed"


# --- a chat turn -------------------------------------------------------------------------------

class _ChatTurn(_Turning):
    """One turn of a conversation (the module doc): the person's message
    worked by one turn of its agent where the conversation works
    (``chats.place_of``; ``link_id`` its vault, "" for none), the agent's message
    written as the turn goes (at most every ``CHAT_EVERY_S``) and at its
    end. ``go`` never raises and always ends the turn: its message (unless
    it is gone) and its row, which keeps this computer's record of a
    failure; a message that cannot be written leaves the row open for
    ``recover`` (another server's, for the channel's next announce there
    too). Where the conversation lives is its home's
    (``home.chat_home_for``)."""

    def __init__(self, ws: str, turn_id: str, link_id: str, run: dict, chat: dict):
        super().__init__(ws, turn_id, link_id, run)
        self.turn_id = turn_id
        # Where the conversation lives: this server's pages.db, or another
        # server's (a remote turn's key names its channel).
        self.home = home.chat_home_for(ws, turn_id, chat)
        run["remote"] = self.home.remote
        self.preface = ""               # what the message says before the agent's text (no tools, and why)
        self.left_open = False          # a claim never answered: the row stays open, for that server to be told
        self.conversation_id, self.bucket, self.dir = chat["conversation_id"], chat["bucket"], chat["dir"]
        self.agent, self.label = chat["agent"], catalog.label(chat["agent"])
        self.model, self.effort = chat.get("model") or "", chat.get("effort") or ""
        self.root: Path | None = None
        self.asked = ""                 # the session the turn asked to resume
        self.fresh = False              # a follow-up whose agent remembers none of the earlier turns
        self.paragraphs: list[str] = []
        self.live = False               # the last paragraph is the message its deltas build
        self.changes: list[str] = []
        self.changed = 0
        self.built = False
        self.tracked: set[str] = set()  # the vault's files, by path folded: what the vault's detail may name
        self.end = {"state": "failed", "error": "", "record": ""}
        self.gone = False               # its message is gone: nothing more is written in it
        self.ask = False                # its agent may ask (what it may run at the turn's start)
        self.asks: dict[str, dict] = {}  # Gamma's id -> an ask that waits: {key, kind, command, cwd, host, step,
        #                                   since, shown, card (a remote turn's: what the desktop app draws)}
        self.answered: set[str] = set()  # the asks that waited and wait no more
        self.held = 0                   # asks held for the person
        self.declines = 0               # the person's declines in a row
        self.waited = 0.0               # seconds a remote turn waited for the person (its deadline moved so)
        self.file = ""                  # the page's path from this computer's vault rows ("" for none)
        self.maker = ""                 # who answers its asks on this computer: the vault's maker, else the owner
        self.person_name = ""           # its username
        self.lent = False               # a co-author's turn (``_work``)
        self._io = threading.Lock()     # one write of the message at a time, the newest last
        self._asking = threading.Lock()  # the asks and the steps, which the person's answer changes too
        self._written_at = 0.0
        self._dirty = False
        self._ended = False             # its end is written (or being): no progress after it
        self._flushing = threading.Lock()  # held while ``_flush`` writes another server's message

    def go(self) -> None:
        with _lock:
            self.run["chat"] = self  # what answer_chat answers through
        try:
            self._work()
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001 — a turn never stays queued or running
            log.exception(f"[agents] {self.label} on chat turn {self.turn_id} in workspace {self.ws}")
            self.end = {"state": "failed", "error": f"Gamma could not run {self.label}.",
                        "record": f"{e!r}\n{traceback.format_exc()}"}
        finally:
            if self.turn is not None:
                self.turn.close()  # a turn whose events were never read still ends its process
            self._drop_tools()
            self._stopping()
            self._close()
            if self.home.remote and (self.changes or self.built):
                self._push()

    def _close(self) -> None:
        """The turn's end written: its message (with the threads its agent
        opened meanwhile, ``threads.opened``: a turn of this server's), then
        its row."""
        if self.left_open:
            return
        try:
            with self._io:
                self._ended = True
                if not self.gone:
                    opened = {} if self.home.remote else {"threads": self._opened()}
                    self.gone = not self.home.write(self, {
                        **self._progress(), "state": self.end["state"], "error": self.end["error"],
                        "changes": self.changes, "changed": self.changed, "built": self.built,
                        **opened, "ended_at": page_now()}, last=True)
        except Exception as e:  # noqa: BLE001 — the row stays open: the next start fails the message
            log.warning(f"[agents] chat turn {self.turn_id} of workspace {self.ws} left open; the next start "
                        f"fails it: {e}")
            return
        store.end_turn(self.turn_id, state=self.end["state"], error=(self.end["record"] or "")[-MAX_ERROR:])

    def _tools(self) -> None:
        """A turn of this server mints Gamma's tools (``_Turning._tools``);
        another server's takes those its claim handed it, once, where their
        address is that server's own (``home.HttpChatHome.gamma``), and its
        message says why it runs without them (``preface``)."""
        if not self.home.remote:
            super()._tools()
            return
        if self._tooled:
            return
        self._tooled = True
        self.gamma, why = self.home.gamma()
        if self.gamma is not None:
            log.info(f"[agents] Gamma's tools of the other server for {self.label} on {self.name}")
        self.preface = why

    def _opened(self) -> list[dict]:
        """The threads its agent opened for the turn's owner since the turn
        began running (``threads.opened``), what its answer's card lists;
        none for a turn that never ran, or when they cannot be read."""
        try:
            return threads.opened(self.ws, agents.here(self.agent).id, self.person, self.run.get("since") or "")
        except Exception as e:  # noqa: BLE001 — the end is written without them
            log.warning(f"[agents] the threads chat turn {self.turn_id} opened: {e}")
            return []

    def _fail(self, words: str, record: str = "") -> None:
        """The turn failed: ``words`` its message says, ``record`` this
        computer's."""
        self.end = {"state": "failed", "error": words, "record": record}
        raise _Stop

    def _check_stop(self) -> None:
        if self.stop.is_set():
            self._stopped()

    def _stopped(self, record: str = "") -> None:
        """The turn's stop was set: the person's Stop (or its message gone)
        ends it ``stopped``, the vault's or Gamma closing fails it with
        why."""
        if self.run["reason"] == PERSON or self.gone:
            self.end = {"state": "stopped", "error": "", "record": record}
            raise _Stop
        self._fail(self._stop_words(), record)

    # --- the steps ----------------------------------------------------------------------

    def _work(self) -> None:
        from .. import folder_links
        from ..vault import store as vault_store

        context = self.home.claim(self)
        if context is None:
            # Gone, or ended meanwhile (a Stop while it waited for a slot
            # ended its message and its row), or not this computer's to run
            # (another server's turn taken elsewhere): nothing to write. A
            # claim that server never answered stays open, to be failed
            # there once it is reached.
            self.gone = True
            self.end = {"state": "stopped", "error": "", "record": ""}
            self.left_open = self.home.unsure
            return
        self._check_stop()  # stopped while it waited for a slot

        # 1. what its route checked, checked again
        place = self.home.place(self, context)
        if place["path"] and _place(place["path"]) != self.run["dir"]:   # moved while it waited: not its queue
            self._fail("The directory this conversation works in moved on this computer: send this again.")
        link = place["link"]
        if link is not None and (link["status"].get(folder_links.PAUSED) or folder_links.choosing(link)):
            self._fail("The synced folder is paused on this computer: send this again once it syncs.")
        self.owner = self.home.whose(link, context)
        self.person, self.person_name = self.home.person(context)
        self.lent = self.home.lent(context)
        if not store.owns(self.owner, self.agent):
            self._fail(f"{self.label} was turned off on this computer.")
        if self.lent and not self.home.serves(self.agent, self.person):
            self._fail(UNNAMED.format(agent=self.label))
        if self.lent and not self.home.editor(self.owner):
            self._fail(f"The owner of {self.label} on this computer can no longer edit this workspace.")
        if self.lent and place["kind"] != "vault":
            self._fail(LENT_FOLDER.format(agent=self.label, person=self.person_name or "you"))
        if not self.home.editor(self.person):
            self._fail(f"Only an owner or an editor of this workspace can talk to {self.label}.")
        found = catalog.probe(self.agent, fresh=True, models=False)
        self._check_stop()
        if not found["ready"]:
            self._fail(found["problem"] or f"{self.label} is not ready on this computer.")
        if self.home.remote:   # that server checked them against what this computer announced: again here
            refusal = catalog.refused_choice(self.agent, found.get("models") or [], self.model, self.effort)
            if refusal:
                self._fail(refusal)
        refused = self.home.cannot_write()
        if refused:
            self._fail(refused)
        argv = catalog.command(self.agent)
        if argv is None:
            self._fail(f"{self.label} is not on this computer.")
        # Another server's turn asks on this computer, and there where the channel relays asks
        # (``ask_refusal``), each ask checked again; a co-author's there only where it relays them.
        relays = self._relays()
        self.ask = not ask_refusal(self._server(), self.home.remote, self.agent, relays) and not (
            self.lent and self.home.remote and not relays)
        self.maker = link["created_by"] if link is not None and not self.lent else self.owner

        # 2. running
        self.root = Path(place["path"])
        if place["kind"] == "own":
            self.root.mkdir(parents=True, exist_ok=True)   # its first turn makes it
        self.tracked = {path.casefold() for path in vault_store.files(link["id"])} if link is not None else set()
        with _lock:
            self.run["claimed"] = True
            self.run["since"] = page_now()
        store.turn_running(self.turn_id)
        self._show(force=True, state="running")
        self._check_stop()

        # 3. the prompt and the session: the person's words are the instruction
        owner = self.person_name or "the owner"
        about, selections = self.home.where(self, place, context)
        # what an ask names: never the other server's word
        self.file = self.home.vault_file(self, link) if link is not None else ""
        earlier = context["earlier"]
        session = self._session(place)
        held = sessions.held(self.agent, session) if session else ""
        if held:   # open in a terminal or an editor: two writers would fork it
            self._fail(held)
        self.fresh = bool(earlier) and not session
        self._tools()   # the prompt says whether the turn has them
        thread, images, pictures = None, [], None
        if context.get("thread_id"):
            # a thread's turn: the thread as it is now, its pictures where a vault's files hold them
            thread = self.home.thread(self, place, context)
            if thread is None:
                self._fail("That comment thread is gone.")
            pictures = self.root if place["kind"] == "vault" else None
            images = prompt.picture_paths([thread], pictures) if pictures else []

        def told(with_earlier: bool) -> str:
            if thread is not None:   # the thread holds what was said before: no earlier messages
                return prompt.build_thread(agent=self.agent, owner=owner, about=about, place=place,
                                           message=context["text"], thread=thread, root=pictures, ask=self.ask,
                                           tools=self.gamma is not None)
            return prompt.build_chat(agent=self.agent, owner=owner, about=about, place=place,
                                     message=context["text"], selections=selections,
                                     earlier=earlier if with_earlier else None, ask=self.ask,
                                     tools=self.gamma is not None)

        # 4. the turn
        before = _snapshot(self.root)
        self._check_stop()
        chosen = {"ask": self.ask, "stream": True, "model": self.model, "effort": self.effort,
                  "title": context.get("title") or "", "images": images}
        done = self._turn(argv, self.root, told(not session), session, **chosen)
        if self._resume_refused(done, session):
            # A session this computer no longer has (Claude Code has no
            # fallback of its own): afresh, with the earlier messages.
            log.info(f"[agents] {self.label} could not resume the session of chat turn {self.turn_id}: "
                     "starting a new one")
            with self._asking:
                self.asked, self.fresh, self.paragraphs, self.live = "", bool(earlier), [], False
            done = self._turn(argv, self.root, told(True), "", **chosen)

        # 5. what it did, and its end
        changes, self.built = _changed_files(before, _snapshot(self.root))
        self.changes, self.changed = changes[:store.MAX_CHANGES], len(changes)
        record = self._record(done)
        if self.stop.is_set() and not self.timed_out:
            self._stopped(record)
        if not done["ok"]:
            if self.timed_out:
                self._fail(f"{self.label} took longer than {_span(_run_timeout())} and was stopped.", record)
            if done.get("busy"):
                self._fail(done["error"], record)
            first = events.one_line(_redact(done.get("error") or "", self.root), 500)
            self._fail(f"{self.label} could not finish: {first or 'it stopped unexpectedly.'}", record)
        if not any(p.strip() for p in self.paragraphs) and (done.get("text") or "").strip():
            with self._asking:
                self.paragraphs, self.live = [done["text"].strip()], False  # a reply its done alone carries
        self.end = {"state": "done", "error": "", "record": record}

    def _channel(self) -> dict | None:
        """The channel of a turn of another server's while it is kept, None
        otherwise."""
        from ..computers import channel  # local: it imports folder_links, which imports this package

        return channel.get(self.ws) if self.home.remote else None

    def _server(self) -> str:
        """The address of the server of a turn of another server's while its
        channel is kept, "" otherwise."""
        link = self._channel()
        return link["remote_url"] if link is not None else ""

    def _relays(self) -> bool:
        """Whether the server of a turn of another server's may answer what
        it asks now: its channel kept and its ``asks`` on (Settings → AI →
        Agents on this computer)."""
        link = self._channel()
        return link is not None and link["asks"]

    def _session(self, place: dict) -> str:
        """The session to resume: the one the conversation's agent ran in
        last, when it is this turn's agent in this place's directory as it
        is now; "" for a new one."""
        row = store.conversation_of(self.conversation_id)
        if (row is None or row["agent"] != self.agent or row["link_id"] != self.link_id
                or row["cwd"] != place["path"] or not row["session"]):
            return ""
        self.asked = row["session"]
        return self.asked

    def _link_reason(self) -> str:
        """Why its stop was set, a turn of no vault's: its agent turned off,
        else its directory no longer offered (``stop_dirs``)."""
        if self.link_id:
            return super()._link_reason()
        return "off" if not store.owns(self.owner, self.agent) else "unoffered"

    def _started(self, pid: int) -> None:
        store.turn_running(self.turn_id, pid)

    def _heard(self, event: dict) -> dict | None:
        """One event of the turn: its session kept, its text and steps
        written in its message → itself when it is the done."""
        kind = event.get("type")
        if kind == "done":
            return event
        if kind == "session":
            if self.asked and event["id"] != self.asked:
                self.fresh = True  # Codex starts a new thread of its own when it cannot resume one
            try:
                store.ran_conversation(self.conversation_id, link_id=self.link_id, agent=self.agent,
                                       session=event["id"], cwd=str(self.root))
            except Exception as e:  # noqa: BLE001 — the next turn starts a new session
                log.warning(f"[agents] recording the session of chat turn {self.turn_id}: {e}")
        elif kind == "delta":
            with self._asking:
                if not self.live:
                    self.paragraphs.append("")
                    self.live = True
                self.paragraphs[-1] += str(event.get("text") or "")
        elif kind == "text":
            # The whole message, in place of what its deltas built.
            text = str(event.get("text") or "").strip()
            with self._asking:
                if self.live:
                    self.paragraphs[-1], self.live = text, False
                elif text:
                    self.paragraphs.append(text)
        elif kind == "tool":
            step = step_of(event)
            with self._asking:
                at = self._step_at.get(step["id"])
                if at is not None:
                    if self.steps[at]["status"] != "declined":  # what the agent says of it then changes nothing
                        self.steps[at]["status"] = step["status"]
                elif len(self.steps) < store.MAX_STEPS:
                    self._step_at[step["id"]] = len(self.steps)
                    self.steps.append(step)
            if event.get("status") == "started":
                with _lock:
                    self.run["detail"] = _said(event, self.tracked)
        elif kind == "ask":
            if self._asked(event):
                if self.home.remote:
                    _asks_changed()
                    self._show_later()
                else:
                    if self.lent:
                        _asks_changed()
                    self._show(force=True)
                return None
        elif kind == "ask_gone":
            with self._asking:
                ask_id = next((i for i, held in self.asks.items() if held["key"] == event.get("id")), None)
                held = self.asks.pop(ask_id) if ask_id else None
                if ask_id:
                    self.answered.add(ask_id)
            if held is not None:
                if self.home.remote or self.lent:
                    _asks_changed()
                self._settle(held, "failed")
        self._show()
        return None

    # --- the asks ----------------------------------------------------------------------------

    def _shown_path(self, path: str) -> str:
        """A path as an ask shows it to the person who answers it: relative
        inside the folder, under ``~`` in the home directory, else whole (an
        ask is the maker's alone and leaves the message once answered, and
        consent needs to see where)."""
        if _inside(path):
            return events.one_line(path, 500)
        return events.one_line(_relative(path, self.root), 500)

    def _grant_cwd(self, cwd: str) -> str:
        """An ask's directory as a grant keys it: relative to the folder
        (the folder itself, by its path or as ``.``, is ``.``), else
        whole."""
        return events.rel_path(str(cwd or "").strip() or ".", str(self.root)) or "."

    def _asked(self, event: dict) -> bool:
        """The agent asks. Gamma declines it, never shown, in this order:
        while the turn's agent may no longer ask (``ask_refusal``: set back
        to never, a remote turn's server no longer reached securely or,
        where it does not relay asks, the desktop app's secret gone; a
        remote co-author's turn once its channel relays them no more), a
        remote turn's ask of another kind than a command or a file change, a
        file change asking to write below a root for the rest of the session
        (every turn), a remote ask this computer cannot show whole or whose
        host does not read as one, a turn past ``MAX_ASKS`` or
        ``MAX_DECLINES``, and a remote ask its server does not answer that
        the desktop app is not there to answer either (``SHELL_SEEN_S``,
        waiting up to ``SHELL_GRACE_S`` for its first read). A command the
        person allowed in a local conversation, exactly it in the same
        directory and to the same network host, is answered at once, never
        shown; a remote turn and a co-author's look up no grant. Any other
        ask is held, in the message (``asks``, a remote turn's and a
        co-author's marked ``once``: Allow once and Decline alone) with its
        step ``asked``, a remote turn's for the desktop app (``card``) and,
        where its channel relays asks, its server's message (``_progress``),
        a co-author's for the desktop app too → whether it was held."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        remote = self.home.remote
        relays = remote and self._relays()
        kind = event.get("kind") if event.get("kind") in events.ASK_KINDS else "tool"
        raw = str(event.get("command") or "")
        command = raw.strip()
        cwd, host = self._grant_cwd(event.get("cwd") or "."), str(event.get("host") or "").strip()
        link = folder_links.get_link(self.link_id) if self.link_id else None
        refusal = RUNS_NEVER if self.link_id and link is None else ask_refusal(self._server(), remote, self.agent,
                                                                               relays)
        if not refusal and remote and (kind not in ("command", "files") or self.lent and not relays):
            refusal = RUNS_NEVER
        if not refusal and event.get("grant_root"):
            refusal = ONE_AT_A_TIME
        if not refusal and remote:
            if kind == "command" and (len(raw) > FAR_MAX_COMMAND or raw.count("\n") + 1 > FAR_MAX_LINES):
                refusal = TOO_LONG
            elif event.get("diff_cut") or len(event.get("paths") or []) > FAR_MAX_PATHS:
                refusal = TOO_LONG
            elif host and not events.host_ok(host):
                refusal = HOST_UNREAD
        if refusal:
            self.turn.answer(event["id"], "deny", refusal, by="gamma")
            return False
        if not remote and not self.lent and kind == "command" and command and store.granted(
                self.conversation_id, command, dir=str(self.root), cwd=cwd, host=host):
            self.turn.answer(event["id"], "once")
            return False
        if self.held >= MAX_ASKS or self.declines >= MAX_DECLINES:
            self.turn.answer(event["id"], "deny", ASKED_TOO_OFTEN if self.held >= MAX_ASKS else DECLINED_OFTEN,
                             by="gamma")
            return False
        if remote and not relays and not shell_listening() and not wait_shell(SHELL_GRACE_S):
            self.turn.answer(event["id"], "deny", NOBODY_HERE, by="gamma")
            return False
        ask_id = new_block_id()
        shown = {"id": ask_id, "kind": kind}
        if "command" in event:
            # exactly as the agent gave it, nothing rewritten: the person sees
            # what runs before it does (what would not show marked beside it)
            shown["command"] = raw
            marked, count = events.shown_text(raw)
            if count:
                shown["command_shown"], shown["marked"] = marked, count
            if raw.count("\n"):
                shown["lines"] = raw.count("\n") + 1
            unwrapped = events.unwrap_shell(raw) if not count else None
            if unwrapped:
                shown["shell"], shown["inner"] = unwrapped
        if "cwd" in event:
            shown["cwd"] = self._shown_path(str(event["cwd"] or "."))
        if host:
            shown["host"] = events.one_line(host, 500)
        if "paths" in event:
            shown["paths"] = [self._shown_path(str(p)) for p in event["paths"]]
        if "diff" in event:
            shown["diff"] = events.cut_diff(str(event["diff"] or ""))
        if event.get("reason"):
            shown["reason"] = _relative(str(event["reason"]), self.root)[:events.MAX_REASON]
        if kind == "tool":
            shown["tool"] = events.one_line(str(event.get("tool") or ""), 100)
        if event.get("background"):
            shown["background"] = True
        if self.lent or remote:
            shown["once"] = True
        now = datetime.now(timezone.utc)
        shown["at"], shown["expires_at"] = format_stamp(now), format_stamp(now + timedelta(seconds=ASK_TIMEOUT_S))
        # a step's id never the ask's: a remote turn's steps reach the other server
        step_id = str(event.get("step") or "") or f"ask:{new_block_id()}"
        if kind == "command":
            step = step_of({"id": step_id, "kind": "run", "command": command, "status": "asked"})
        elif kind == "files":
            step = step_of({"id": step_id, "kind": "edit", "path": (event.get("paths") or [""])[0], "status": "asked"})
        else:
            step = step_of({"id": step_id, "kind": "other", "status": "asked"})
        card = self._card(event, shown, link) if remote or self.lent else None
        with self._asking:
            self.held += 1
            self.asks[ask_id] = {"key": event["id"], "kind": kind, "command": command, "cwd": cwd, "host": host,
                                 "step": step_id, "since": time.monotonic(), "shown": shown, "card": card}
            at = self._step_at.get(step_id)
            if at is not None:
                self.steps[at]["status"] = "asked"
            elif len(self.steps) < store.MAX_STEPS:
                self._step_at[step_id] = len(self.steps)
                self.steps.append(step)
        return True

    def _card(self, event: dict, shown: dict, link: dict | None) -> dict:
        """A remote turn's ask as the desktop app draws it: every string
        through ``events.shown_text`` (how many it marked in all,
        ``marked``), paths absolute on this computer, the agent's reason one
        line, the server by its origin and the account there, the directory
        it works in (the vault's, ``link``, else its own), the page's file
        only as this computer's vault rows name it. Nothing in it is that
        server's word but the account's name."""
        count = 0

        def mark(text: str) -> str:
            nonlocal count
            out, n = events.shown_text(text)
            count += n
            return out

        def where(path: str) -> str:
            return os.path.normpath(path if Path(path).is_absolute() or re.match(r"^[A-Za-z]:", path)
                                    else os.path.join(str(self.root), path))

        card = {"id": shown["id"], "turn_id": self.turn_id, "link_id": self.link_id, "agent": self.agent,
                "label": self.label, "kind": shown["kind"]}
        if "command" in shown:
            card["command"] = mark(shown["command"])
            for key in ("lines", "inner", "shell"):
                if key in shown:
                    card[key] = shown[key]
        if shown.get("background"):
            card["background"] = True
        card.update(dir=mark(where(str(event.get("cwd") or "."))), folder=mark(str(link["path"] if link else self.root)),
                    file=mark(self.file))
        if shown.get("host"):
            card["host"] = mark(shown["host"])
        if "paths" in event:
            card["paths"] = [mark(where(str(p))) for p in event["paths"]]
        if "diff" in shown:
            card["diff"] = mark(shown["diff"])
        if event.get("reason"):
            card["reason"] = mark(events.one_line(str(event["reason"]), 300))
        server = self._server()
        card["server"] = {"origin": remote_servers.origin_of(server) if server else "",
                          "account": mark(events.one_line(self.home.username if self.home.remote else "", 64))}
        if self.lent:
            card["for"] = mark(events.one_line(self.person_name, 64))
        if count:
            card["marked"] = count
        card["at"], card["expires_at"] = shown["at"], shown["expires_at"]
        return card

    def answer(self, ask_id: str, decision: str, note: str = "") -> None:
        """The person's answer in the chat (``answer_chat``): a remote
        turn's asks are answered on this computer (``answer_computer``) and,
        relayed, on its server (``answer_relayed``), never through this
        server's chat, 409 here; else ``_answer_here``."""
        if self.home.remote:
            raise chats.ChatError(409, FAR_ASKS)
        self._answer_here(ask_id, decision, note)

    def _answer_here(self, ask_id: str, decision: str, note: str = "") -> None:
        """The person's answer to the ask ``ask_id`` (``events.DECISIONS``),
        ``note`` what a decline tells the agent: it leaves the message, its
        step reads allowed or declined, and Allow in this chat on a command
        records the grant. ChatError 404 for an ask the turn never had, 409
        for one that waits no more (answered, withdrawn, declined by Gamma)
        or that the agent no longer takes, 400 for Allow in this chat on
        anything but a command, and in a co-author's turn (``ONCE_ONLY``)."""
        with self._asking:
            held = self.asks.get(ask_id)
            if held is None:
                raise chats.ChatError(409 if ask_id in self.answered else 404,
                                      f"{self.label} no longer waits for that." if ask_id in self.answered
                                      else "No question of this turn has that id.")
            if decision == "chat" and self.lent:
                raise chats.ChatError(400, ONCE_ONLY)
            if decision == "chat" and held["kind"] != "command":
                raise chats.ChatError(400, "Only a command can be allowed in this chat.")
        if not self._decide(ask_id, decision, note):
            raise chats.ChatError(409, f"{self.label} no longer waits for that.")

    def _decide(self, ask_id: str, decision: str, note: str, by: str = "owner") -> bool:
        """The ask answered with ``decision``, once, by the person (``by``
        ``owner``: a decline counts toward ``MAX_DECLINES``, an allow sets
        the count back) or by Gamma → whether the agent took the answer.
        Allow in this chat is kept (``store.grant``: the command, the
        directory the turn works in, the folder below it, its host) only
        while the turn's agent is still on and may still ask. A remote turn's message is written by the
        flush, so an answer, the desktop app's or its server's, never waits on that server."""
        answered = "declined" if decision == "deny" else "allowed"
        with self._asking:
            held = self.asks.pop(ask_id, None)
            self.answered.add(ask_id)
            if held is None:
                return False
            if by == "owner":
                self.declines = self.declines + 1 if decision == "deny" else 0
            # The step reads the answer before the agent hears it: what the
            # agent then says of a declined step changes nothing (the tool
            # event's guard), whichever thread comes first.
            self._mark(held, "asked", answered)
        written = self.turn is not None and self.turn.answer(held["key"], decision, note, by=by)
        if written and decision == "chat" and not self.lent and held["kind"] == "command" and held["command"]:
            try:
                if store.runs_of(self.agent) == "ask" and store.owns(self.owner, self.agent):
                    store.grant(self.conversation_id, held["command"], agent=self.agent, dir=str(self.root),
                                cwd=held["cwd"], host=held["host"])
            except Exception as e:  # noqa: BLE001 — this call is allowed; the next one asks again
                log.warning(f"[agents] keeping a grant of chat turn {self.turn_id}: {e}")
        if not written:
            with self._asking:
                self._mark(held, answered, "failed")
        if self.home.remote:
            _asks_changed()
            self._show_later()
        else:
            if self.lent:
                _asks_changed()
            self._show(force=True)
        return written

    def decline_all(self, note: str) -> None:
        """Every ask that waits declined by Gamma with ``note`` (what may
        run was set back to never), the turn going on: the cards leave the
        message, their steps declined."""
        with self._asking:
            waiting = list(self.asks)
        for ask_id in waiting:
            self._decide(ask_id, "deny", note, by="gamma")

    def _settle(self, held: dict, status: str) -> None:
        """An ask that waits no more: its step ``status``, the message
        written."""
        with self._asking:
            self._mark(held, "asked", status)
        self._show(force=True)

    def _mark(self, held: dict, was: str, status: str) -> None:
        """The ask's step ``status`` while it reads ``was`` (``_asking``
        held)."""
        at = self._step_at.get(held["step"])
        if at is not None and self.steps[at]["status"] == was:
            self.steps[at]["status"] = status

    def _waiting(self) -> bool:
        """An ask waits; a remote turn's deadline moves by ``FAR_WAIT_S`` in
        all, so it holds a slot of this computer's at most that much past
        its own."""
        if self.home.remote:
            return bool(self.asks) and self.waited < FAR_WAIT_S
        return bool(self.asks)

    def _waited(self, seconds: float) -> None:
        if self.home.remote:
            self.waited += seconds

    def _stopping(self) -> None:
        """The turn stops or ended: its agent declines what waits (the
        driver's stop), so the asks leave the message, their steps
        declined."""
        with self._asking:
            left, self.asks = list(self.asks.values()), {}
            self.answered.update(held["shown"]["id"] for held in left)
            for held in left:
                at = self._step_at.get(held["step"])
                if at is not None and self.steps[at]["status"] == "asked":
                    self.steps[at]["status"] = "declined"
        if left:
            if self.home.remote or self.lent:
                _asks_changed()
            try:
                self._show(force=True)
            except Exception as e:  # noqa: BLE001 — the turn's end writes it
                log.warning(f"[agents] writing chat turn {self.turn_id}: {e}")

    def _tick(self) -> None:
        """What the turn heard within ``CHAT_EVERY_S`` of a write is written
        once that passed, though nothing more comes (another server's by a
        thread of its own, ``_flush``: a request there never holds up the
        watch of the stop and the deadline); an ask nobody answered within
        ``ASK_TIMEOUT_S`` is declined."""
        now = time.monotonic()
        with self._asking:
            expired = [ask_id for ask_id, held in self.asks.items() if now - held["since"] >= ASK_TIMEOUT_S]
        try:
            for ask_id in expired:
                self._decide(ask_id, "deny", ASK_EXPIRED, by="gamma")
            if self._dirty and not self.home.remote:
                self._show()
            elif (self._dirty and now - self._written_at >= home.WRITE_EVERY_S
                  and self._flushing.acquire(blocking=False)):
                threading.Thread(target=self._flush, name=f"agent-write-{self.turn_id}", daemon=True).start()
        except Exception as e:  # noqa: BLE001 — the watch goes on: the next event or the end writes it
            log.warning(f"[agents] writing chat turn {self.turn_id}: {e}")

    def _flush(self) -> None:
        """``_tick``'s write of another server's message, on a thread of its
        own; one at a time (``_flushing``, released here)."""
        try:
            self._show()
        except Exception as e:  # noqa: BLE001 — the next event or the end writes it
            log.warning(f"[agents] writing chat turn {self.turn_id}: {e}")
        finally:
            self._flushing.release()

    def _progress(self) -> dict:
        """The message as the turn has it: its text (its paragraphs, the last
        one growing with its deltas, or the done's own, without this
        computer's paths and ``prompt.inert``), its
        steps, the asks that wait (``_asked``; another server's message
        takes them only while its channel relays asks, ``_relays``, and
        says ``waiting`` while one waits for an answer on this computer
        alone) and ``fresh``."""
        relays = self.home.remote and self._relays()
        with self._asking:
            paragraphs = list(self.paragraphs)
            steps = [dict(s) for s in self.steps]
            asks = [dict(held["shown"]) for held in self.asks.values()]
        said = [self.preface] if self.preface else []
        text = reply_text(said + paragraphs, self.root) if self.root else ""
        out = {"text": text, "steps": steps, "asks": asks, "fresh": self.fresh}
        if self.home.remote:
            out["asks"], out["waiting"] = (asks, False) if relays else ([], bool(asks))
        return out

    def _show_later(self) -> None:
        """Another server's message written by the flush thread within a
        tick (``_tick``), not on this one: what an ask or an answer
        changed. Takes no lock: a write that hangs there holds ``_io``, and
        the change is the next write's whatever this one carries."""
        self._dirty, self._written_at = True, float("-inf")

    def _show(self, *, force: bool = False, **more) -> None:
        """The message's text and steps (and ``more``) written, at most every
        ``CHAT_EVERY_S`` (another server's, ``home.WRITE_EVERY_S``) unless
        ``force``; a message gone stops the turn. A write that did not reach
        another server (``home.missed``) leaves the message to write again
        (the flush, ``_tick``), as does a change made while it went."""
        every = home.WRITE_EVERY_S if self.home.remote else CHAT_EVERY_S
        with self._io:
            if self._ended:   # a write of the progress that waited behind the end
                return
            now = time.monotonic()
            if self.gone or (not force and now - self._written_at < every):
                self._dirty = not self.gone
                return
            self._written_at, self._dirty = now, False
            if self.home.write(self, {**self._progress(), **more}):
                self._dirty = self._dirty or self.home.missed
                return
            self.gone = True
        with _lock:
            if not self.stop.is_set():
                self.run["reason"] = PERSON
                self.stop.set()


def reply_text(paragraphs: list[str], root: Path | str) -> str:
    """An agent's paragraphs as its message keeps them: the empty left out,
    this computer's paths made relative to the directory ``root`` or
    named outside it (``_redact``), loading nothing where they are drawn
    (``prompt.inert``), within ``chats.MAX_TEXT``."""
    return prompt.inert(_redact("\n\n".join(p.strip() for p in paragraphs if p.strip()), root))[:chats.MAX_TEXT]


def step_of(event: dict) -> dict:
    """A tool event as the chat's message keeps it: ``{id, kind, status}``
    with ``path`` for a read or an edit (relative to the folder, "outside"
    for a file outside it), ``program`` for a command (its name alone,
    ``_program``) and ``tool`` for a call of Gamma's tools; worded by the
    client."""
    kind = event.get("kind") if event.get("kind") in events.KINDS else "other"
    step = {"id": str(event.get("id") or ""), "kind": kind, "status": event.get("status") or ""}
    if kind in ("read", "edit"):
        path = event.get("path") or ""
        step["path"] = events.one_line(path, 200) if _inside(path) else "outside"
    elif kind == "run":
        step["program"] = _program(event.get("command") or "")
    elif kind == "gamma":
        step["tool"] = event.get("tool") if events.GAMMA_TOOL.match(str(event.get("tool") or "")) else ""
    return step

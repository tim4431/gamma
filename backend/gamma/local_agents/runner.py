"""Claiming the Sends an agent on this computer answers, and running them
(docs/dev/local_agents.md "Claiming and the run").

Every write of a request reaches ``on_request`` (a ``requests.change_listeners``
entry, ``register``). A Send of a work folder whose vault here has an agent
(``store.agent_of``), made in Gamma (``via`` ``session``: never a token's) by
the vault's maker, is queued for that vault; a withdraw of one that runs
sets its stop. One worker thread a vault takes its
queue in order, at most ``MAX_RUNS`` runs at once on this server, so a folder
never has two agents in its directory. A run (``_Run``):

1. claims the request (``requests.claim``: ``running``; of two takers, or a
   taker and a withdraw, one wins) after checking again that the vault, its
   agent and its maker still hold, and records the run (``store.start_run``);
2. probes the agent afresh: one not ready fails the request with its problem;
3. reads the request's entries and keeps those with something new to answer
   (``prompt.pending``), at most ``MAX_THREADS``, noting when it read them
   (``seen_at``) and which comments it read (``read``): a person's word it
   did not read is the next run's;
4. notes the folder's files (``_snapshot``), starts one turn of the agent in
   the vault's directory with ``prompt.build`` (gamma/local_agents/events.py),
   resuming the session that answered its threads last when they share one
   (``_session``; a resume that fails before any step starts afresh once),
   and shows its steps as the request's detail, at most every
   ``DETAIL_EVERY_S``, never naming a path outside the folder or a whole
   command; a watchdog thread stops the turn on a withdraw, an agent turned
   off, the vault paused or removed, the server closing, or
   ``RUN_TIMEOUT_S`` after the turn began;
5. once the turn is done, compares the files, writes each answer as the
   agent's reply signed with its label (``agent_id`` ``local:<name>``, so the
   comment reads as an agent on this computer's) and records the thread's
   session (``store.answered_thread``), resolves the fixed ones in which a
   person said nothing the run did not read, and sets the request ``done`` with
   the summary and the files changed, or
   ``failed`` with a sentence a member may read; the agent's whole error and
   its stderr stay in the run's row on this computer.

A withdraw meanwhile writes nothing more in Gamma: the run ends ``stopped``.
Every write of the runner expects the request ``running`` (``only_from``), so
a withdraw that lands between two of them is never overwritten.

A turn of the chat's computer side (gamma/local_agents/chats.py,
``queue_chat``) waits in the same queue of its vault and takes the same
slots, keyed ``(ws, "chat", turn id)`` beside a Send's ``(ws, "send",
request id)``, so a Send and a chat turn never work one directory at once.
A ``_ChatTurn`` checks again what its route checked (the vault, its agent
the turn's, its maker an editor, the agent ready, writes taken), resumes
the conversation's session in the vault's directory as it is now
(``store.conversation_of``; a refused resume starts afresh once, said
``fresh``), and writes the agent's message as the turn goes: its text, its
steps (relative paths, a command's program), what changed, its end. Its
agent streams (``delta``): the message's last paragraph grows as the agent
writes it, and the whole message, when it comes, takes its place. It runs
the model and effort its turn asked for (``queue_chat``). Both kinds run
their turn through ``_Turning``. ``chat_turns`` lists an account's turns
that are queued or running, for Background tasks.

A chat turn of a vault whose agent may ask (``store.runs_of`` ``ask``, read
at the turn's start; a Send never asks) starts its agent with ``ask``. Each
ask the agent makes is held for the turn under an id of Gamma's and written
in the message (``asks``, its step ``asked``) until the person answers it
(``answer_chat``: the step ``allowed`` or ``declined``), the agent
withdraws it, ``ASK_TIMEOUT_S`` passes (Gamma declines it) or the turn
stops (its agent declines what waits). A command the person allowed in
this chat (``store.grant``) is answered at once when the agent asks exactly
it again, in the same directory and to the same network host, never shown.
What may run set back to never (``runs_never``) declines what waits, and
whatever the turn asks after. The time an ask waits counts toward neither the
turn's deadline nor its agent's silence.

Locking: ``_lock`` guards the queues, the workers, the active runs and their
stop events, and nothing else; no call into ``requests``, ``store``,
``catalog``, ``chats`` or a driver is made while it is held (the runner's
own writes come back through ``on_request`` on the same thread).
"""

import atexit
import collections
import json
import os
import re
import shlex
import threading
import time
import traceback
from pathlib import Path

from fastapi import HTTPException

from ..blocks_store import new_block_id
from ..db import account_names, connect_pages_db, connect_users_db, page_now
from ..feedback import requests
from ..feedback.entries import FeedbackError
from ..logbuf import log
from . import catalog, chats, enabled, events, procs, prompt, store

RUN_TIMEOUT_S = 1800              # env GAMMA_AGENT_RUN_TIMEOUT: a turn running this long is stopped
MAX_RUNS = 2                      # runs at once on this server, at most one per folder
MAX_THREADS = 100                 # comments one run takes
DETAIL_EVERY_S = 3                # the request's progress is written at most this often
SNAPSHOT_FILES = 20_000           # files a snapshot of the folder notes at most
SNAPSHOT_S = 3.0                  # and how long it may take
CHANGES_NAMED = 5                 # source files the done detail names
SKIPPED_DIRS = {".git", ".gamma", "node_modules", "__pycache__", ".venv", "venv"}
# What a build writes beside the sources: a change to one of these is the
# build's, not the agent's edit.
BUILD_OUTPUTS = (".pdf", ".aux", ".log", ".synctex.gz", ".synctex", ".fls", ".fdb_latexmk", ".out", ".toc", ".bbl",
                 ".blg", ".bcf", ".run.xml", ".xdv", ".dvi", ".nav", ".snm", ".lof", ".lot", ".idx", ".ind", ".ilg",
                 ".bak")
MAX_ERROR = 16_000                # characters of a run's error record
CHAT_EVERY_S = 0.5                # a chat turn's message is written at most this often
ASK_TIMEOUT_S = 600               # an ask nobody answers this long is declined
ASK_EXPIRED = "No answer within 10 minutes."
RUNS_NEVER = "The owner no longer lets you ask to run commands in this folder."

# The kinds of work a vault's queue holds: a Send's run, a chat's turn.
SEND, CHAT = "send", "chat"

# Why a run's stop was set: the words its request is failed with (a withdraw
# writes nothing). "link" is decided when the run ends, from what the vault
# is then (``_link_reason``); "person" is a chat turn's Stop (it ends
# ``stopped``).
WITHDRAWN, CLOSING, LINK, PERSON = "withdrawn", "closing", "link", "person"
STOPPED = {"off": "Stopped: the agent was turned off for this folder.",
           "paused": "Stopped: the work folder was paused.",
           "removed": "Stopped: the work folder is no longer kept on this computer.",
           "changed": "Stopped: the work folder changed on this computer.",
           CLOSING: "Stopped: Gamma is closing."}

_lock = threading.Lock()
_queues: dict[str, collections.deque] = {}     # link id -> (ws, kind, id) waiting, oldest first
_workers: dict[str, threading.Thread] = {}     # link id -> the thread taking its queue
_queued: set[tuple[str, str, str]] = set()     # (ws, kind, id) in some queue
# (ws, kind, id) -> {link_id, stop, reason, claimed, agent, slot, detail}, a chat turn's also {chat, since}
_active: dict[tuple[str, str, str], dict] = {}
# a chat turn's id -> {ws, conversation_id, page_id, agent, user_id, model, effort, at}, until it ends
_chats: dict[str, dict] = {}
_slots = threading.BoundedSemaphore(MAX_RUNS)
_registered = False


def _run_timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("GAMMA_AGENT_RUN_TIMEOUT") or RUN_TIMEOUT_S))
    except ValueError:
        return RUN_TIMEOUT_S


# --- hearing the requests ------------------------------------------------------------

def register() -> None:
    """``on_request`` hears every request's write, and ``shutdown`` runs at
    exit; once."""
    global _registered
    if on_request not in requests.change_listeners:
        requests.change_listeners.append(on_request)
    if not _registered:
        _registered = True
        atexit.register(shutdown)


def on_request(ws: str, request_id: str) -> None:
    """A request was written: a Send this computer's agent answers is
    queued, a withdraw of one that runs stops it. Cheap (a few reads, no
    process), never blocks on a run, never raises."""
    try:
        if enabled():
            _consider(ws, request_id)
    except Exception as e:  # noqa: BLE001 — a listener never fails the write it hears of
        log.warning(f"[agents] hearing request {request_id} of workspace {ws}: {e}")


def _request_row(ws: str, request_id: str) -> tuple | None:
    """``(state, detail, requester, work folder, via)`` of the request, None
    for none."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT state, detail, by, work_folder, via FROM feedback_requests WHERE id = ?",
                            (request_id,)).fetchone()


def _ours(link: dict | None, requester: str, via: str) -> bool:
    """Whether a Send by ``requester``, made ``via``, is the agent's of the
    vault ``link``: made in Gamma by the vault's maker, the vault having an
    agent (a Send a token made, or one from before Sends said how they
    were made, never is)."""
    return (link is not None and via == requests.SESSION and requester == link["created_by"]
            and store.agent_of(link["id"]) is not None)


def _consider(ws: str, request_id: str) -> bool:
    """Whether the request is this computer's to answer, queued now or
    already."""
    row = _request_row(ws, request_id)
    if row is None:
        return False
    state, detail, requester, w, via = row
    key = (ws, SEND, request_id)
    if state == "failed":
        with _lock:
            run = _active.get(key)
            if run is not None and not run["stop"].is_set():
                run["reason"] = WITHDRAWN if detail == requests.WITHDRAWN else "failed"
                run["stop"].set()
        return False
    if state != "sent":
        return False
    link = requests._local_link(ws, w)
    if not _ours(link, requester, via):
        return False
    with _lock:
        run = _active.get(key)
        # A run that waits for a slot and was stopped meanwhile (its vault
        # paused and resumed, its agent changed) drops the Send unclaimed:
        # queue it again for the vault as it is now.
        if key in _queued or (run is not None and not (run["stop"].is_set() and not run["claimed"])):
            return True
        _enqueue(link["id"], key)
    return True


def _enqueue(link_id: str, key: tuple) -> None:
    """``key`` at the end of the vault's queue, its worker started when it
    has none. Under ``_lock``."""
    _queued.add(key)
    _queues.setdefault(link_id, collections.deque()).append(key)
    if link_id not in _workers:
        worker = threading.Thread(target=_work, args=(link_id,), name=f"agent-{link_id}", daemon=True)
        _workers[link_id] = worker
        worker.start()


def queue_chat(ws: str, link_id: str, turn_id: str, *, conversation_id: str, page_id: str, agent: str,
               user_id: str = "", model: str = "", effort: str = "") -> None:
    """A chat turn of ``user_id`` (``chats.begin`` wrote it ``queued``),
    asking for ``model`` and ``effort`` ("" for the agent's default), waits
    in the vault ``link_id``'s queue, behind what is there."""
    with _lock:
        _chats[turn_id] = {"ws": ws, "conversation_id": conversation_id, "page_id": page_id, "agent": agent,
                           "user_id": user_id, "model": model, "effort": effort, "at": page_now()}
        _enqueue(link_id, (ws, CHAT, turn_id))


def waiting(ws: str, link: dict) -> list[str]:
    """The Sends of the vault ``link``'s work folder that wait (``sent``)
    and are its agent's to take (``_ours``, the vault's agent aside), oldest
    first: what turning the agent on hands it, and what the panel's
    question counts."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT id, by, via FROM feedback_requests WHERE work_folder = ? AND state = 'sent' "
                            "ORDER BY rev", (link["folder_id"],)).fetchall()
    return [request_id for request_id, by, via in rows
            if via == requests.SESSION and by == link["created_by"]]


def queue_folder(ws: str, link_id: str) -> int:
    """Hand the agent of the vault ``link_id`` its work folder's Sends that
    wait (``waiting``) → how many it takes."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    if not enabled():
        return 0
    link = folder_links.get_link(link_id)
    if link is None or link["workspace_id"] != ws:
        return 0
    count = 0
    for request_id in waiting(ws, link):
        try:
            count += _consider(ws, request_id)
        except Exception as e:  # noqa: BLE001 — one request never keeps the others
            log.warning(f"[agents] queueing request {request_id} of workspace {ws}: {e}")
    return count


# --- what the routes ask ----------------------------------------------------------------

def folder_agent(ws: str, w: str) -> dict | None:
    """The agent of the vault here that keeps the work folder ``w`` of
    ``ws``, paused or not: ``{name, label, link_id, maker, set_by, set_at,
    writable}`` (``maker`` the account that made the vault, ``writable``
    whether the vault may write its directory now: not paused, not choosing
    its files), None when no vault here keeps it or it has no agent. Runs
    no process."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = [link for link in folder_links.all_links()
             if link["workspace_id"] == ws and link["folder_id"] == w and folder_links.work_folder_link(link)]
    agents = store.agents_of([link["id"] for link in links])
    for link in links:
        row = agents.get(link["id"])
        if row:
            return {"name": row["agent"], "label": catalog.label(row["agent"]), "link_id": link["id"],
                    "maker": link["created_by"], "set_by": row["set_by"], "set_at": row["set_at"],
                    "writable": not link["status"].get(folder_links.PAUSED) and not folder_links.choosing(link)}
    return None


def running(ws: str, request_id: str) -> bool:
    """Whether an agent on this computer has claimed the request and works
    on it now."""
    with _lock:
        run = _active.get((ws, SEND, request_id))
        return bool(run and run["claimed"])


def at_work(link_ids) -> dict[str, dict]:
    """``{link id: {kind, ws, id, detail}}`` of the vaults among
    ``link_ids`` whose agent works now: a Send it claimed (``detail`` "":
    the request's own is read), a chat turn that runs (``detail`` what its
    step shows a member, ``_said``)."""
    wanted = set(link_ids)
    with _lock:
        return {run["link_id"]: {"kind": kind, "ws": ws, "id": key_id, "detail": run.get("detail", "")}
                for (ws, kind, key_id), run in _active.items() if run["link_id"] in wanted and run["claimed"]}


def chat_state(ws: str, turn_id: str) -> dict | None:
    """Where the chat turn ``turn_id`` stands in the runner: ``{turn_id,
    state: "queued" | "running", ahead}``, ``ahead`` whether another run
    goes first (of its vault, or holding the slots); None for a turn the
    runner does not hold."""
    key = (ws, CHAT, turn_id)
    with _lock:
        run = _active.get(key)
        if run is not None:
            if run["claimed"]:
                return {"turn_id": turn_id, "state": "running", "ahead": False}
            return {"turn_id": turn_id, "state": "queued", "ahead": not run["slot"]}
        if key not in _queued:
            return None
        for link_id, queue in _queues.items():
            if key in queue:
                ahead = queue.index(key) > 0 or any(r["link_id"] == link_id for r in _active.values())
                return {"turn_id": turn_id, "state": "queued", "ahead": ahead}
    return None


def chat_turns(user_id: str) -> list[dict]:
    """The chat turns of ``user_id`` this server holds, queued or running,
    oldest first: ``{ws, turn_id, conversation_id, page_id, agent, state:
    "queued" | "running", asking, stopping, queued_at, started_at}``,
    ``asking`` whether an ask of it waits for the person, ``stopping``
    whether its stop is set (Background tasks' rows, routers/jobs.py)."""
    out = []
    with _lock:
        for turn_id, chat in _chats.items():
            if chat.get("user_id") != user_id:
                continue
            run = _active.get((chat["ws"], CHAT, turn_id))
            running = run is not None and run["claimed"]
            turning = run.get("chat") if run is not None else None
            out.append({"ws": chat["ws"], "turn_id": turn_id, "conversation_id": chat["conversation_id"],
                        "page_id": chat["page_id"], "agent": chat["agent"],
                        "state": "running" if running else "queued",
                        "asking": bool(turning is not None and turning.asks),
                        "stopping": bool(run is not None and run["stop"].is_set()),
                        "queued_at": chat.get("at") or "", "started_at": (run or {}).get("since", "")})
    return sorted(out, key=lambda t: t["queued_at"])


def stop_chat(ws: str, turn_id: str, conversation_id: str) -> bool:
    """The person's Stop of a chat turn of the conversation: a running one's
    stop is set (it ends ``stopped``), a queued one leaves its queue and
    ends ``stopped``, and so does one whose message still reads queued or
    running though the runner does not hold it (not queued yet, or left by
    a server that stopped) → whether there was one to stop. One its worker
    holds while it waits for a slot gets its stop and ends ``stopped`` now,
    its message too while it reads queued, so the conversation takes the
    next message at once: given its slot, it finds its message ended and
    writes nothing more (``_ChatTurn._work``)."""
    key = (ws, CHAT, turn_id)
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
            _queued.discard(key)
            for queue in _queues.values():
                if key in queue:
                    queue.remove(key)
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
    """The person's answer to the ask ``ask_id`` of the chat turn
    ``turn_id`` (``_ChatTurn.answer``). ChatError 409 for a turn that holds
    no ask any more (it ended, or waits for its slot), and as
    ``_ChatTurn.answer`` says."""
    with _lock:
        run = _active.get((ws, CHAT, turn_id))
        turning = run.get("chat") if run is not None else None
    if turning is None:
        raise chats.ChatError(409, "This turn has ended.")
    turning.answer(ask_id, decision, note)


def runs_never(link_id: str) -> None:
    """What the vault ``link_id``'s agent may run was set back to never:
    each chat turn of it running declines the asks that wait
    (``_ChatTurn.decline_all``) and goes on; what it asks next is declined
    unseen (``_ChatTurn._asked``)."""
    with _lock:
        turnings = [run["chat"] for (_, kind, _), run in _active.items()
                    if kind == CHAT and run["link_id"] == link_id and run.get("chat") is not None]
    for turning in turnings:
        try:
            turning.decline_all(RUNS_NEVER)
        except Exception as e:  # noqa: BLE001 — the policy is set; the turn's end takes its cards
            log.warning(f"[agents] declining the asks of chat turn {turning.turn_id}: {e}")


def stop_link(link_id: str) -> None:
    """Stop the run of the vault ``link_id``, if one runs: its agent was
    turned off or changed, the vault paused, relinked, removed or its push
    lowered (what fits is read when the run ends). The Sends still queued
    for it are dropped from the queue: a vault that takes them again
    (``queue_folder``) finds them sent. Its chat turns stay: each, taken,
    finds the vault as it is now and fails with why."""
    with _lock:
        for run in _active.values():
            if run["link_id"] == link_id and not run["stop"].is_set():
                run["reason"] = LINK
                run["stop"].set()
        queue = _queues.get(link_id)
        if queue:
            for key in [key for key in queue if key[1] == SEND]:
                _queued.discard(key)
                queue.remove(key)


def shutdown(timeout: float = 5.0) -> None:
    """Stop every run (Gamma is closing), then every agent's process."""
    with _lock:
        for run in _active.values():
            if not run["stop"].is_set():
                run["reason"] = CLOSING
                run["stop"].set()
        for queue in _queues.values():
            queue.clear()
        _queued.clear()
    procs.stop_all(timeout)


def wait_idle(timeout: float = 30) -> bool:
    """Whether every queue and run ends within ``timeout`` seconds (tests)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _workers and not _active:
                return True
        time.sleep(0.05)
    with _lock:
        return not _workers and not _active


def reset() -> None:
    """Stop and forget every queue, worker and run (tests)."""
    global _slots
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


# --- the startup -------------------------------------------------------------------------

RECOVER_WAIT_S = 600   # how long the startup waits for a workspace behind on its migrations to be upgraded
RECOVER_POLL_S = 5


def recover() -> None:
    """At startup: the runs a server that stopped left open fail, each its
    request too ("Send again"); the agents of vaults gone meanwhile are
    forgotten; then, in the background, the chat turns it left open fail,
    each its message too, and each vault with an agent takes the Sends that
    still wait for it (``waiting``), as turning it on does. A workspace
    behind on its migrations is waited for (the background walk upgrades
    it, ``migrations.warming``), at most ``RECOVER_WAIT_S``: a chat turn
    not failed by then stays open for the next start."""
    from .. import folder_links, migrations  # local: folder_links imports this package's modules inside functions

    for run in store.open_runs():
        label = catalog.label(run["agent"])
        try:
            with connect_pages_db(run["ws"]) as conn:
                requests.set_state(run["ws"], conn, run["request_id"], "failed",
                                   detail=f"Gamma stopped while {label} worked on this. Send again.",
                                   by_name=label, writer="agent", only_from=("running",))
        except FeedbackError:
            pass  # withdrawn, or gone: nothing to say
        except Exception as e:  # noqa: BLE001 — one workspace never keeps the others
            log.warning(f"[agents] failing the run {run['id']} left open in workspace {run['ws']}: {e}")
        store.end_run(run["id"], state="failed", error="Gamma stopped while the run was open.")
    links = {link["id"]: link for link in folder_links.all_links()}
    store.forget_links(set(links))
    agents = store.agents_of(links)
    # The chat turns first: a vault's queue may hold none of them again.
    jobs = [(turn["ws"], f"the chat turn {turn['id']} left open", lambda turn=turn: _fail_left_open(turn))
            for turn in store.open_turns()]
    jobs += [(links[link_id]["workspace_id"], f"the waiting Sends of vault {link_id}",
              lambda link_id=link_id: queue_folder(links[link_id]["workspace_id"], link_id)) for link_id in agents]

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
                except Exception as e:  # noqa: BLE001 — one vault or turn never keeps the others
                    log.warning(f"[agents] {what}: {e}")
            left = behind
            if left and time.monotonic() >= end:
                log.warning(f"[agents] {len(left)} workspace job(s) not upgraded in time: waiting Sends wait for the "
                            "next Send, pause or start, open chat turns for the next start")
                return
            if left:
                time.sleep(RECOVER_POLL_S)

    if jobs:
        threading.Thread(target=when_upgraded, name="agent-recover", daemon=True).start()


def _fail_left_open(turn: dict) -> None:
    """A chat turn a server that stopped left open fails, its message too
    when it still reads queued or running."""
    label = catalog.label(turn["agent"])
    _, reply = chats.reply_of(turn["ws"], turn["conversation_id"], turn["id"])
    if reply is not None and reply.get("state") in chats.BUSY:
        chats.write_reply(turn["ws"], turn["conversation_id"], turn["id"],
                          {"state": "failed", "error": f"Gamma stopped while {label} worked on this.",
                           "asks": [], "ended_at": page_now()})  # an ask left waiting goes: nobody answers it now
    store.end_turn(turn["id"], state="failed", error="Gamma stopped while the turn was open.")


# --- the workers ---------------------------------------------------------------------------

def _work(link_id: str) -> None:
    """The vault's worker: its queue's runs in order, one at a time; it
    leaves (and forgets itself) in the same step that finds the queue
    empty."""
    while True:
        with _lock:
            queue = _queues.get(link_id)
            if not queue:
                _queues.pop(link_id, None)
                if _workers.get(link_id) is threading.current_thread():
                    del _workers[link_id]
                return
            key = queue.popleft()
            _queued.discard(key)
            run = {"link_id": link_id, "stop": threading.Event(), "reason": "", "claimed": False, "agent": "",
                   "slot": False, "detail": ""}
            _active[key] = run
            chat = _chats.get(key[2]) if key[1] == CHAT else None
            slots = _slots
        ws, kind, key_id = key
        try:
            with slots:
                with _lock:
                    run["slot"] = True
                if kind == CHAT:
                    if chat is not None:
                        _ChatTurn(ws, key_id, link_id, run, chat).go()
                else:
                    _Run(ws, key_id, link_id, run).go()
        except Exception:  # noqa: BLE001 — _Run and _ChatTurn never raise; a worker never dies of one run
            log.exception(f"[agents] the {kind} {key_id} in workspace {ws}")
        finally:
            with _lock:
                if _active.get(key) is run:
                    del _active[key]
                if kind == CHAT:
                    _chats.pop(key_id, None)


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
    """How the request's words name a file the agent touched: by its path
    when members can see it already (the vault tracks it, or an entry of
    the request names it as its source), else "a file in the folder" or "a
    file outside the folder", so a file the person left out of Gamma is
    never named to the workspace. ``tracked`` None names every file inside
    the folder (this computer's own record of the run)."""
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
    -pdf main.tex'``), never its arguments."""
    def words(line: str) -> list[str]:
        try:
            return shlex.split(line, posix=True)
        except ValueError:
            return line.split()

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
    ``Working``; a file the vault does not track is named as no file
    (``_named``)."""
    kind = event.get("kind")
    if kind in ("read", "edit"):
        what = _named(event.get("path") or "", tracked)
        return f"{'Reading' if kind == 'read' else 'Editing'} {what}"
    if kind == "run":
        program = _program(event.get("command") or "")
        return f"Running {program}" if program else "Running a command"
    if kind == "search":
        return "Searching the sources"
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


def _cut(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= requests.MAX_DETAIL else text[:requests.MAX_DETAIL - 1] + "…"


def _spoken_after(ws: str, since: dict[str, tuple[str, set[str]]]) -> set[str]:
    """The threads of ``since`` (``{thread id: (when a run read it, the ids
    of the comments it read)}``) in which a person (a session: no token, no
    agent) has a live comment that run did not read, or edited one after it
    read: words it never saw. By the ids, not the times, since a comment is
    stamped before its commit. One query."""
    since = {thread_id: (at, read) for thread_id, (at, read) in since.items() if at}
    if not since:
        return set()
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT thread_id, id, updated_at FROM comments "
                            "WHERE thread_id IN (SELECT value FROM json_each(?)) AND agent_id = '' "
                            "AND deleted_at IS NULL AND orphaned_at IS NULL",
                            (json.dumps(sorted(since)),)).fetchall()
    return {thread_id for thread_id, comment_id, updated in rows
            if comment_id not in since[thread_id][1] or updated > since[thread_id][0]}


class _Stop(Exception):
    """The run ends here; its state and the words were set."""


def _cannot_write(ws: str) -> str:
    """Why this server or the workspace takes no writes now, "" when they
    do: what fails a run before its agent edits anything."""
    from .. import hosted, workspaces

    refused = hosted.read_only()
    if not refused:
        try:
            workspaces.refuse_writes(ws)
        except HTTPException as e:
            refused = str(e.detail)
    return refused


class _Turning:
    """What a Send's run and a chat's turn share: one turn of the vault's
    agent in its directory (``_turn``), watched (a stop, the deadline) and
    its events followed (``_heard``, each kind's own), and why a stop the
    vault set was set (``_stop_words``)."""

    def __init__(self, ws: str, name: str, link_id: str, run: dict):
        self.ws, self.name, self.link_id, self.run = ws, name, link_id, run
        self.stop = run["stop"]
        self.agent, self.label = "", "the agent"
        self.turn = None
        self.timed_out = False
        self.steps: list[dict] = []
        self._step_at: dict[str, int] = {}

    def _turn(self, argv: list[str], root: Path, text: str, session: str, *, schema: dict | None = None,
              images=(), ask: bool = False, stream: bool = False, model: str = "", effort: str = "") -> dict:
        """One turn of the agent on ``text`` (``session`` one to resume;
        ``ask``, it may ask; ``stream``, its text as it is written; its
        ``model`` and ``effort``), watched, its events followed → its done.
        A turn before it is closed first."""
        if self.turn is not None:
            self.turn.close()
        self.turn = events.start(self.agent, argv=argv, cwd=str(root), prompt=text, schema=schema,
                                 images=list(images), session=session, ask=ask, stream=stream, model=model,
                                 effort=effort)
        if self.turn.pid:
            self._started(self.turn.pid)
        ended = threading.Event()
        threading.Thread(target=self._watch, args=(self.turn, ended), name=f"agent-watch-{self.name}",
                         daemon=True).start()
        try:
            return self._follow()
        finally:
            ended.set()

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
        """Stop the turn on the run's stop or at its deadline, counted from
        the turn's start (not from the queue's wait, nor while an ask waits
        for the person, ``_waiting``); between, ``_tick``."""
        deadline = last = time.monotonic()
        deadline += _run_timeout()
        while not ended.is_set():
            now = time.monotonic()
            if self._waiting():
                deadline += now - last
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
        """Whether the turn waits for the person now (an ask)."""
        return False

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
        if reason == CLOSING:
            return STOPPED[CLOSING]
        return STOPPED[self._link_reason()]

    def _link_reason(self) -> str:
        """Why the vault stopped its run, from what it is now."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None:
            return "removed"
        row = store.agent_of(self.link_id)
        if row is None or row["agent"] != self.agent:
            return "off"
        if link["status"].get(folder_links.PAUSED):
            return "paused"
        return "changed"


class _Run(_Turning):
    """One request worked by one turn of the vault's agent (the module doc's
    steps). ``go`` never raises and always ends the run it recorded."""

    def __init__(self, ws: str, request_id: str, link_id: str, run: dict):
        super().__init__(ws, request_id, link_id, run)
        self.request_id = request_id
        self.link: dict | None = None
        self.record: dict | None = None
        self.maker, self.maker_name = "", ""
        self.run_id = ""
        self.end = {"state": "failed", "detail": "", "error": ""}
        self.changes: list[str] = []
        self.built = False
        self.unwritten = False  # the request's last write failed: the run's row stays open for ``recover``
        self.tracked: set[str] = set()  # the vault's files, by path folded: what the request's words may name
        self.seen_at = ""  # when the run read its entries: the requests made since are the next run's
        self.read: dict[str, list[str]] = {}  # the ids of the comments it read, by thread
        self._shown, self._shown_at = "", 0.0

    # --- the request ---------------------------------------------------------------

    def _write(self, state: str, detail: str) -> bool:
        """The request ``state`` with ``detail``, as the agent, only while
        it is running → whether it was written (False: withdrawn
        meanwhile)."""
        try:
            with connect_pages_db(self.ws) as conn:
                requests.set_state(self.ws, conn, self.request_id, state, detail=_cut(detail), by_name=self.label,
                                   writer="agent", only_from=("running",))
            return True
        except FeedbackError as e:
            if e.status == 409 or e.status == 404:
                return False
            raise

    def _fail(self, detail: str, error: str = "") -> None:
        """The request failed with ``detail`` (what members read), the run
        with ``error`` (this computer's record); stopped when the request
        was withdrawn first."""
        if self._withdrawn() or not self._write("failed", detail):
            self.end = {"state": "stopped", "detail": "", "error": error}
        else:
            self.end = {"state": "failed", "detail": _cut(detail), "error": error}
        raise _Stop

    def _withdrawn(self) -> bool:
        """Whether the request's requester withdrew it (or it is no longer
        running for any other reason): nothing more is written in Gamma."""
        if self.run["reason"] == WITHDRAWN:
            return True
        row = _request_row(self.ws, self.request_id)
        return row is None or row[0] != "running"

    def _check_stop(self) -> None:
        """At a step's end: a run stopped meanwhile ends here."""
        if self.stop.is_set():
            self._stopped()

    def _stopped(self, error: str = "") -> None:
        """The run's stop was set: a withdrawn request is left as it is
        (the run ``stopped``), any other fails with why."""
        if self._withdrawn():
            self.end = {"state": "stopped", "detail": "", "error": error}
            raise _Stop
        self._fail(self._stop_words(), error)

    # --- the steps ----------------------------------------------------------------------

    def go(self) -> None:
        try:
            if not self._claim():
                return
            self._work()
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001 — a run never leaves its request running
            log.exception(f"[agents] {self.label} on request {self.request_id} in workspace {self.ws}")
            error = f"{e!r}\n{traceback.format_exc()}"
            try:
                self._fail(f"Gamma could not run {self.label}.", error)
            except _Stop:
                pass
            except Exception:  # noqa: BLE001 — the request could not be written either: recover fails it
                self.end = {"state": "failed", "detail": "", "error": error}
                self.unwritten = True
        finally:
            if self.turn is not None:
                self.turn.close()  # a turn whose events were never read still ends its process
            if self.run_id and self.unwritten:
                # The request could not be told how the run ended (its
                # database refused): the run stays open, so the next start
                # fails the request rather than leave it "running".
                log.warning(f"[agents] request {self.request_id} of workspace {self.ws} left running; "
                            "the next start fails it")
            elif self.run_id:
                store.end_run(self.run_id, state=self.end["state"], detail=self.end.get("detail", ""),
                              error=(self.end.get("error") or "")[-MAX_ERROR:],
                              session=getattr(self.turn, "session", "") or "", changes=self.changes,
                              built=self.built, steps=self.steps, pid=getattr(self.turn, "pid", 0) or 0)

    def _claim(self) -> bool:
        """Step 1: the vault, its agent and its maker checked again, the
        request taken and the run recorded → whether it is this run's."""
        from .. import workspaces

        if self.stop.is_set():
            return False  # stopped while it waited for a slot: the Send stays sent (``_consider`` queues it again)
        row = _request_row(self.ws, self.request_id)
        if row is None or row[0] != "sent":
            return False
        link = requests._local_link(self.ws, row[3])
        if link is None or link["id"] != self.link_id or not _ours(link, row[2], row[4]):
            return False
        agent = store.agent_of(link["id"])
        if agent is None or agent["agent"] not in catalog.AGENTS:
            return False
        if not workspaces.at_least(workspaces.role_of(self.ws, link["created_by"]), "editor"):
            return False
        self.link, self.agent, self.label = link, agent["agent"], catalog.label(agent["agent"])
        self.maker = link["created_by"]
        with connect_users_db() as users:
            self.maker_name = account_names(users, {self.maker}).get(self.maker, "")
        self.run["agent"] = self.agent
        with connect_pages_db(self.ws) as conn:
            self.record = requests.claim(self.ws, conn, self.request_id, by_name=self.label,
                                         detail=f"Starting {self.label}")
        if self.record is None:
            return False  # someone else took it, or it was withdrawn
        with _lock:
            self.run["claimed"] = True
        self.run_id = store.start_run(ws=self.ws, link_id=self.link_id, request_id=self.request_id, agent=self.agent)
        return True

    def _work(self) -> None:
        from ..feedback import entries
        from ..vault import store as vault_store

        # 2. the agent, asked afresh, and whether this server writes
        found = catalog.probe(self.agent, fresh=True, models=False)
        self._check_stop()
        if not found["ready"]:
            self._fail(found["problem"] or f"{self.label} is not ready on this computer.")
        refused = _cannot_write(self.ws)
        if refused:
            self._fail(refused)

        # 3. what there is to answer
        files = vault_store.files(self.link_id)
        self.tracked = {path.casefold() for path in files}
        self.seen_at = page_now()
        with connect_pages_db(self.ws) as conn:
            found_entries = requests.entries_of(self.ws, conn, self.record, paths=entries.paths_of(files))
        self.read = {e["id"]: [c["id"] for c in e.get("comments") or []] for e in found_entries}
        # A thread an earlier run answered here while a person spoke in it
        # unread (a mention made during that run) is still to answer.
        last = store.threads_of(self.link_id, [e["id"] for e in found_entries])
        spoken = _spoken_after(self.ws, {t: (row["seen_at"], set(row["read"])) for t, row in last.items()
                                         if row["comment_id"]})
        todo = prompt.pending(found_entries, {t: last[t]["comment_id"] for t in spoken})
        self.tracked |= {e["source"]["file"].casefold() for e in todo if (e.get("source") or {}).get("file")}
        if not todo:
            nothing = "Nothing new since the last answers."
            self.end = {"state": "done" if self._write("done", nothing) else "stopped",
                        "detail": nothing, "error": ""}
            return
        if len(todo) > MAX_THREADS:
            self._fail(f"{self.label} takes at most {MAX_THREADS} comments at a time: send a file or a directory of "
                       "this work folder.")
        self._show(f"Reading {len(todo)} comment{'' if len(todo) == 1 else 's'}", force=True)

        # 4. the turn
        root = Path(self.link["path"])
        before = _snapshot(root)
        self._check_stop()
        argv = catalog.command(self.agent)
        if argv is None:
            self._fail(f"{self.label} is not on this computer.")
        text = prompt.build(agent=self.agent, owner=self.maker_name, work_folder=self.record["work_folder"]["name"],
                            note=self.record["note"], entries=todo, root=root)
        session = self._session(todo, last)
        schema, images = prompt.ANSWER_SCHEMA, prompt.picture_paths(todo, root)
        done = self._turn(argv, root, text, session, schema=schema, images=images)
        if session and not done["ok"] and not self.steps and not self.stop.is_set() and not self.timed_out:
            # A session this computer no longer has (Claude Code has no
            # fallback of its own): the same prompt once more, afresh.
            log.info(f"[agents] {self.label} could not resume its session on request {self.request_id}: "
                     "starting a new one")
            done = self._turn(argv, root, text, "", schema=schema, images=images)

        # 5. what it did, and its answers
        changes, self.built = _changed_files(before, _snapshot(root))
        self.changes = changes[:store.MAX_CHANGES]
        record = self._record(done)
        if self.stop.is_set() and not self.timed_out:
            self._stopped(record)
        if not done["ok"]:
            if self.timed_out:
                self._fail(f"{self.label} took longer than {_span(_run_timeout())} and was stopped.", record)
            first = events.one_line(_redact(done.get("error") or "", root), requests.MAX_DETAIL)
            self._fail(f"{self.label} could not finish: {first or 'it stopped unexpectedly.'}", record)
        valid, summary = prompt.answers(done.get("answer"), {e["id"] for e in todo})
        if not valid:
            self._fail(f"{self.label} finished without answering in the form asked for.", record)
        if self._withdrawn():
            self.end = {"state": "stopped", "detail": "", "error": record}
            return
        written, refusal = self._answer(valid)
        if self.stop.is_set():
            self._stopped(record)
        if not written:
            self._fail(refusal or f"{self.label}'s answers could not be written.", record)
        detail = summary or f"Answered {written} of {len(todo)} comment{'' if len(todo) == 1 else 's'}"
        if not detail.endswith((".", "!", "?")):
            detail += "."
        if self.changes:  # by name only the files the vault tracks (``_named``)
            named = [p for p in self.changes if p.casefold() in self.tracked][:CHANGES_NAMED]
            more = len(self.changes) - len(named)
            if named:
                detail += f" Changed {', '.join(named)}" + (f" and {more} more." if more > 0 else ".")
            else:
                detail += f" Changed {more} file{'' if more == 1 else 's'}."
        if self.built:
            detail += " The PDF was rebuilt."
        if not self._write("done", detail):
            self.end = {"state": "stopped", "detail": "", "error": record}
            return
        self.end = {"state": "done", "detail": _cut(detail), "error": record}

    def _session(self, todo: list[dict], answered: dict[str, dict]) -> str:
        """The session to resume: the one this agent answered every thread
        of ``todo`` in last, in the vault's directory as it is now
        (``answered``: ``store.threads_of``); "" for a new one."""
        sessions = set()
        for entry in todo:
            row = answered.get(entry["id"])
            if not row or row["agent"] != self.agent or row["cwd"] != self.link["path"] or not row["session"]:
                return ""
            sessions.add(row["session"])
        return sessions.pop() if len(sessions) == 1 else ""

    def _started(self, pid: int) -> None:
        store.update_run(self.run_id, pid=pid)

    def _heard(self, event: dict) -> dict | None:
        """One event of the turn, its session and steps kept, its progress
        shown → itself when it is the done."""
        kind = event.get("type")
        if kind == "done":
            return event
        if kind == "session":
            store.update_run(self.run_id, session=event["id"])
        elif kind == "tool":
            # The run's own steps (this computer's record) name every file
            # in the folder; the request's words only those members see.
            said = _said(event, self.tracked)
            at = self._step_at.get(event.get("id") or "")
            if at is not None:
                self.steps[at]["status"] = event.get("status") or ""
            elif len(self.steps) < store.MAX_STEPS:
                self._step_at[event.get("id") or ""] = len(self.steps)
                self.steps.append({"kind": event.get("kind") or "other", "title": _said(event),
                                   "status": event.get("status") or ""})
            if event.get("status") == "started":
                self._show(said)
        return None

    def _show(self, detail: str, *, force: bool = False) -> None:
        """The request's progress ``detail``, at most every
        ``DETAIL_EVERY_S`` and only when it changes; a refusal (the request
        withdrawn) stops the turn."""
        now = time.monotonic()
        if self.stop.is_set() or detail == self._shown or (not force and now - self._shown_at < DETAIL_EVERY_S):
            return
        self._shown, self._shown_at = detail, now
        if not self._write("running", detail):
            with _lock:
                if not self.stop.is_set():
                    self.run["reason"] = WITHDRAWN
                    self.stop.set()

    def _answer(self, valid: list[dict]) -> tuple[int, str]:
        """Each answer written as the agent's reply and its thread's session
        recorded, the fixed ones resolved unless a person said in them what
        the run did not read (the agent never saw that word: the thread
        stays open for it), until a withdraw (the run's stop) ends the
        writing → ``(replies written, the first refusal's words)``. Each
        write says when the run read the thread (``seen``), so a request
        made since is not answered by it."""
        from .. import comments
        from ..blocks_store import new_block_id
        from ..feedback import agent as feedback_agent
        from ..integrations import Connection

        # Signed as the agent's label; its id ``local:<name>`` makes the
        # comment read as an agent on this computer's (comments.LOCAL_AGENT).
        connection = Connection(user_id=self.maker, workspaces=[self.ws], scope="feedback",
                                token_id=f"{comments.LOCAL_AGENT}{self.agent}", name=self.label,
                                username=self.maker_name)
        written, refusal = 0, ""
        for item in valid:
            if self.stop.is_set():  # withdrawn meanwhile: nothing more is written in Gamma
                break
            thread_id = item["thread_id"]
            try:
                _, out = feedback_agent.reply(connection, self.ws, "", {"thread_id": thread_id, "body": item["reply"],
                                                                        "id": new_block_id()}, seen=self.seen_at)
                written += 1
                self._answered(thread_id, out["comment_id"])
                unread = _spoken_after(self.ws, {thread_id: (self.seen_at, set(self.read.get(thread_id, ())))})
                if item["outcome"] == "fixed" and not unread:
                    feedback_agent.resolve(connection, self.ws, "", {"thread_id": thread_id}, seen=self.seen_at)
            except ValueError as e:
                refusal = refusal or str(e)
        return written, refusal

    def _answered(self, thread_id: str, comment_id: str) -> None:
        """The thread's row: this agent answered it with ``comment_id`` in
        its turn's session, in the vault's directory. Never raises: the
        reply is written, and a follow-up without the row starts a new
        session."""
        try:
            store.answered_thread(self.link_id, thread_id, agent=self.agent,
                                  session=getattr(self.turn, "session", "") or "", cwd=self.link["path"],
                                  seen_at=self.seen_at, read=self.read.get(thread_id, []), comment_id=comment_id,
                                  request_id=self.request_id)
        except Exception as e:  # noqa: BLE001
            log.warning(f"[agents] recording the session of thread {thread_id} in workspace {self.ws}: {e}")


# --- a chat turn -------------------------------------------------------------------------------

class _ChatTurn(_Turning):
    """One turn of the chat's computer side (the module doc): the person's
    message worked by one turn of the vault's agent, the agent's message
    written as the turn goes (at most every ``CHAT_EVERY_S``) and at its
    end. ``go`` never raises and always ends the turn: its message (unless
    it is gone) and its row, which keeps this computer's record of a
    failure; a message that cannot be written leaves the row open for
    ``recover``."""

    def __init__(self, ws: str, turn_id: str, link_id: str, run: dict, chat: dict):
        super().__init__(ws, turn_id, link_id, run)
        self.turn_id = turn_id
        self.conversation_id, self.page_id = chat["conversation_id"], chat["page_id"]
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
        self.ask = False                # its agent may ask (the vault's policy at its start)
        self.asks: dict[str, dict] = {}  # Gamma's id -> an ask that waits: {key, kind, command, cwd, host, step,
        #                                   since, shown}
        self.answered: set[str] = set()  # the asks that waited and wait no more
        self._io = threading.Lock()     # one write of the message at a time, the newest last
        self._asking = threading.Lock()  # the asks and the steps, which the person's answer changes too
        self._written_at = 0.0
        self._dirty = False

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
            self._stopping()
            self._close()

    def _close(self) -> None:
        """The turn's end written: its message, then its row."""
        try:
            with self._io:
                if not self.gone:
                    self.gone = not chats.write_reply(self.ws, self.conversation_id, self.turn_id, {
                        **self._progress(), "state": self.end["state"], "error": self.end["error"],
                        "changes": self.changes, "changed": self.changed, "built": self.built,
                        "ended_at": page_now()})
        except Exception as e:  # noqa: BLE001 — the row stays open: the next start fails the message
            log.warning(f"[agents] chat turn {self.turn_id} of workspace {self.ws} left open; the next start "
                        f"fails it: {e}")
            return
        store.end_turn(self.turn_id, state=self.end["state"], error=(self.end["record"] or "")[-MAX_ERROR:])

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
        from .. import folder_links, workspaces
        from ..vault import store as vault_store

        conversation, reply = chats.reply_of(self.ws, self.conversation_id, self.turn_id)
        if reply is None or reply.get("state") != "queued":
            # Gone, or ended meanwhile (a Stop while it waited for a slot
            # ended its message and its row): nothing to write.
            self.gone = True
            self.end = {"state": "stopped", "error": "", "record": ""}
            return
        self._check_stop()  # stopped while it waited for a slot

        # 1. what its route checked, checked again
        link = folder_links.get_link(self.link_id)
        if link is None or not folder_links.work_folder_link(link):
            self._fail("The work folder is no longer kept on this computer.")
        if link["status"].get(folder_links.PAUSED) or folder_links.choosing(link):
            self._fail("The work folder is paused on this computer: send this again once it syncs.")
        if link["created_by"] != conversation["user_id"]:
            self._fail(f"Only the person who keeps this folder on this computer can talk to {self.label}.")
        row = store.agent_of(self.link_id)
        if row is None:
            self._fail(f"{self.label} was turned off for this folder.")
        if row["agent"] != self.agent:
            self._fail(f"{self.label} is no longer this folder's agent.")
        if not workspaces.at_least(workspaces.role_of(self.ws, link["created_by"]), "editor"):
            self._fail(f"Only an owner or an editor of this workspace can talk to {self.label}.")
        found = catalog.probe(self.agent, fresh=True, models=False)
        self._check_stop()
        if not found["ready"]:
            self._fail(found["problem"] or f"{self.label} is not ready on this computer.")
        refused = _cannot_write(self.ws)
        if refused:
            self._fail(refused)
        argv = catalog.command(self.agent)
        if argv is None:
            self._fail(f"{self.label} is not on this computer.")
        self.ask = store.runs_of(self.link_id) == "ask"

        # 2. running
        self.root = Path(link["path"])
        self.tracked = {path.casefold() for path in vault_store.files(self.link_id)}
        with _lock:
            self.run["claimed"] = True
            self.run["since"] = page_now()
        store.turn_running(self.turn_id)
        self._show(force=True, state="running")
        self._check_stop()

        # 3. the prompt and the session
        messages = conversation["messages"]
        at = next(i for i, m in enumerate(messages) if m.get("turn_id") == self.turn_id)
        person = next((m for m in reversed(messages[:at]) if m.get("role") == "user"), None) or {}
        with connect_users_db() as users:
            owner = account_names(users, {link["created_by"]}).get(link["created_by"], "") or "the owner"
        file, folder, selections = self._where(link, person)
        earlier = [{"role": "owner" if m.get("role") == "user" else "agent", "text": m.get("text") or ""}
                   for m in messages[:at] if m is not person and (m.get("text") or "").strip()][-6:]
        session = self._session(link)
        self.fresh = bool(earlier) and not session

        def told(with_earlier: bool) -> str:
            return prompt.build_chat(agent=self.agent, owner=owner, work_folder=folder, file=file,
                                     message=person.get("text") or "", selections=selections,
                                     earlier=earlier if with_earlier else None, ask=self.ask)

        # 4. the turn
        before = _snapshot(self.root)
        self._check_stop()
        chosen = {"ask": self.ask, "stream": True, "model": self.model, "effort": self.effort}
        done = self._turn(argv, self.root, told(not session), session, **chosen)
        if session and not done["ok"] and not self.steps and not self.stop.is_set() and not self.timed_out:
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
            first = events.one_line(_redact(done.get("error") or "", self.root), 500)
            self._fail(f"{self.label} could not finish: {first or 'it stopped unexpectedly.'}", record)
        if not any(p.strip() for p in self.paragraphs) and (done.get("text") or "").strip():
            with self._asking:
                self.paragraphs, self.live = [done["text"].strip()], False  # a reply its done alone carries
        self.end = {"state": "done", "error": "", "record": record}

    def _where(self, link: dict, person: dict) -> tuple[str, str, list[dict]]:
        """``(the page's path in the work folder, the work folder's name,
        the person's selections for the prompt)``, each selection ``{page,
        quote, source}``, ``source`` the line its passage maps to in the
        sources (``feedback.sources.source_for``) when its box is known, else
        None."""
        from ..feedback import entries
        from ..vault import store as vault_store
        from ..work_folders.marks import Tree

        with connect_pages_db(self.ws) as conn:
            tree = Tree.read(conn)
            try:
                scope = entries.resolve_scope(conn, self.page_id, tree)
            except FeedbackError:
                self._fail("This page is no longer a file of the work folder.")
            chosen = person.get("selections") or []
            selections = [{"page": c.get("page"), "quote": c.get("text") or "", "source": source}
                          for c, source in zip(chosen, chats.sources_of(self.ws, conn, self.page_id, chosen))]
        file = entries.paths_of(vault_store.files(link["id"])).get(self.page_id) or scope.name
        return file, tree.names.get(scope.work_folder, "") or Path(link["path"]).name, selections

    def _session(self, link: dict) -> str:
        """The session to resume: the one the conversation's agent ran in
        last, when it is this turn's agent in the vault's directory as it is
        now; "" for a new one."""
        row = store.conversation_of(self.conversation_id)
        if row is None or row["agent"] != self.agent or row["cwd"] != link["path"] or not row["session"]:
            return ""
        self.asked = row["session"]
        return self.asked

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
            step = _step(event)
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
                self._show(force=True)
                return None
        elif kind == "ask_gone":
            with self._asking:
                ask_id = next((i for i, held in self.asks.items() if held["key"] == event.get("id")), None)
                held = self.asks.pop(ask_id) if ask_id else None
                if ask_id:
                    self.answered.add(ask_id)
            if held is not None:
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
        """The agent asks: while the vault's agent may no longer ask
        (``store.runs_of`` set back to never), Gamma declines it, never
        shown; a command the person allowed in this conversation, exactly
        it in the same directory and to the same network host, is answered
        at once, never shown; any other ask is held, in the message
        (``asks``) with its step ``asked`` → whether it was held."""
        kind = event.get("kind") if event.get("kind") in events.ASK_KINDS else "tool"
        command = str(event.get("command") or "").strip()
        cwd, host = self._grant_cwd(event.get("cwd") or "."), str(event.get("host") or "").strip()
        if store.runs_of(self.link_id) != "ask":
            self.turn.answer(event["id"], "deny", RUNS_NEVER)
            return False
        if kind == "command" and command and store.granted(self.conversation_id, command, link_id=self.link_id,
                                                           cwd=cwd, host=host):
            self.turn.answer(event["id"], "once")
            return False
        ask_id = new_block_id()
        shown = {"id": ask_id, "kind": kind}
        if "command" in event:
            # exactly as the agent gave it, nothing rewritten: the person sees
            # what runs before it does
            shown["command"] = str(event["command"] or "")
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
        shown["at"] = page_now()
        step_id = str(event.get("step") or "") or f"ask:{ask_id}"
        if kind == "command":
            step = _step({"id": step_id, "kind": "run", "command": command, "status": "asked"})
        elif kind == "files":
            step = _step({"id": step_id, "kind": "edit", "path": (event.get("paths") or [""])[0], "status": "asked"})
        else:
            step = _step({"id": step_id, "kind": "other", "status": "asked"})
        with self._asking:
            self.asks[ask_id] = {"key": event["id"], "kind": kind, "command": command, "cwd": cwd, "host": host,
                                 "step": step_id, "since": time.monotonic(), "shown": shown}
            at = self._step_at.get(step_id)
            if at is not None:
                self.steps[at]["status"] = "asked"
            elif len(self.steps) < store.MAX_STEPS:
                self._step_at[step_id] = len(self.steps)
                self.steps.append(step)
        return True

    def answer(self, ask_id: str, decision: str, note: str = "") -> None:
        """The person's answer to the ask ``ask_id`` (``events.DECISIONS``),
        ``note`` what a decline tells the agent: it leaves the message, its
        step reads allowed or declined, and Allow in this chat on a command
        records the grant. ChatError 404 for an ask the turn never had, 409
        for one that waits no more (answered, withdrawn, declined by Gamma)
        or that the agent no longer takes, 400 for Allow in this chat on
        anything but a command."""
        with self._asking:
            held = self.asks.get(ask_id)
            if held is None:
                raise chats.ChatError(409 if ask_id in self.answered else 404,
                                      f"{self.label} no longer waits for that." if ask_id in self.answered
                                      else "No question of this turn has that id.")
            if decision == "chat" and held["kind"] != "command":
                raise chats.ChatError(400, "Only a command can be allowed in this chat.")
        if not self._decide(ask_id, decision, note):
            raise chats.ChatError(409, f"{self.label} no longer waits for that.")

    def _decide(self, ask_id: str, decision: str, note: str) -> bool:
        """The ask answered with ``decision``, once → whether the agent took
        the answer. Allow in this chat is kept (``store.grant``: the
        command, its directory, its host) only while the vault's agent may
        still ask and is still this turn's."""
        with self._asking:
            held = self.asks.pop(ask_id, None)
            self.answered.add(ask_id)
        if held is None:
            return False
        written = self.turn is not None and self.turn.answer(held["key"], decision, note)
        if written and decision == "chat" and held["kind"] == "command" and held["command"]:
            try:
                if (store.runs_of(self.link_id) == "ask"
                        and (store.agent_of(self.link_id) or {}).get("agent") == self.agent):
                    store.grant(self.conversation_id, held["command"], link_id=self.link_id, cwd=held["cwd"],
                                host=held["host"])
            except Exception as e:  # noqa: BLE001 — this call is allowed; the next one asks again
                log.warning(f"[agents] keeping a grant of chat turn {self.turn_id}: {e}")
        self._settle(held, ("declined" if decision == "deny" else "allowed") if written else "failed")
        return written

    def decline_all(self, note: str) -> None:
        """Every ask that waits declined with ``note`` (what may run was set
        back to never), the turn going on: the cards leave the message,
        their steps declined."""
        with self._asking:
            waiting = list(self.asks)
        for ask_id in waiting:
            self._decide(ask_id, "deny", note)

    def _settle(self, held: dict, status: str) -> None:
        """An ask that waits no more: its step ``status``, the message
        written."""
        with self._asking:
            at = self._step_at.get(held["step"])
            if at is not None and self.steps[at]["status"] == "asked":
                self.steps[at]["status"] = status
        self._show(force=True)

    def _waiting(self) -> bool:
        return bool(self.asks)

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
            try:
                self._show(force=True)
            except Exception as e:  # noqa: BLE001 — the turn's end writes it
                log.warning(f"[agents] writing chat turn {self.turn_id}: {e}")

    def _tick(self) -> None:
        """What the turn heard within ``CHAT_EVERY_S`` of a write is written
        once that passed, though nothing more comes; an ask nobody answered
        within ``ASK_TIMEOUT_S`` is declined."""
        now = time.monotonic()
        with self._asking:
            expired = [ask_id for ask_id, held in self.asks.items() if now - held["since"] >= ASK_TIMEOUT_S]
        try:
            for ask_id in expired:
                self._decide(ask_id, "deny", ASK_EXPIRED)
            if self._dirty:
                self._show()
        except Exception as e:  # noqa: BLE001 — the watch goes on: the next event or the end writes it
            log.warning(f"[agents] writing chat turn {self.turn_id}: {e}")

    def _progress(self) -> dict:
        """The message as the turn has it: its text (its paragraphs, the last
        one growing with its deltas, or the done's own, without this
        computer's paths and ``prompt.inert``), its
        steps, the asks that wait (``_asked``) and ``fresh``."""
        with self._asking:
            paragraphs = [p.strip() for p in self.paragraphs]
            steps = [dict(s) for s in self.steps]
            asks = [dict(held["shown"]) for held in self.asks.values()]
        text = prompt.inert(_redact("\n\n".join(p for p in paragraphs if p), self.root)) if self.root else ""
        return {"text": text[:chats.MAX_TEXT], "steps": steps, "asks": asks, "fresh": self.fresh}

    def _show(self, *, force: bool = False, **more) -> None:
        """The message's text and steps (and ``more``) written, at most every
        ``CHAT_EVERY_S`` unless ``force``; a message gone stops the turn."""
        with self._io:
            now = time.monotonic()
            if self.gone or (not force and now - self._written_at < CHAT_EVERY_S):
                self._dirty = not self.gone
                return
            self._written_at, self._dirty = now, False
            if chats.write_reply(self.ws, self.conversation_id, self.turn_id, {**self._progress(), **more}):
                return
            self.gone = True
        with _lock:
            if not self.stop.is_set():
                self.run["reason"] = PERSON
                self.stop.set()


def _step(event: dict) -> dict:
    """A tool event as the chat's message keeps it: ``{id, kind, status}``
    with ``path`` for a read or an edit (relative to the folder, "outside"
    for a file outside it) and ``program`` for a command (its name alone,
    ``_program``); worded by the client."""
    kind = event.get("kind") if event.get("kind") in events.KINDS else "other"
    step = {"id": str(event.get("id") or ""), "kind": kind, "status": event.get("status") or ""}
    if kind in ("read", "edit"):
        path = event.get("path") or ""
        step["path"] = events.one_line(path, 200) if _inside(path) else "outside"
    elif kind == "run":
        step["program"] = _program(event.get("command") or "")
    return step

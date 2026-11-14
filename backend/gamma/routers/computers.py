"""A computer's channel to this server, and the computers a page is kept on
(gamma/computers/registry.py; docs/research/agent-collab.md "How the server
sees a vault"). Every Gamma server answers.

The channel's routes take a token of the ``computer`` scope and nothing
else (a session or any other token: 403), and such a token opens nothing
but them (``auth.CHANNEL_PREFIX``). ``POST /announce`` says what the
computer is and keeps; ``GET /poll`` is held up to ``registry.POLL_S``, on
the event loop without a worker thread, and is the computer's heartbeat: a
GET, so a read-only hosted server lets it through. The page's route is
the signed-in account's alone: a session, never a token, a guest or a
share, and only that account's computers.

The Sends (step 4c): a work folder of this server the computer keeps with
an agent there (its announce's folder vault with ``agent``,
``registry.agent_folders``) has its maker's Sends made in Gamma run on
that computer. ``GET /sends`` lists the ones waiting and the runs
withdrawn meanwhile; a run takes one with ``/claim``, keyed by the id the
computer minted for it (a retry is told it holds it; another taker 409),
and gets its entries, its threads' last speakers and ``seen``, this
server's clock at the read; it writes ``/progress`` and, at its end,
``/answers``, which this server writes itself as the agent's
(``feedback.agent.answer_run``: signed ``local:<agent>`` with its label, as
an agent on this computer's are, each reply under the id the computer
minted for it), only while that run holds the request running, so a
withdraw is never overwritten. ``GET /threads/{id}/picture`` is a region's
picture for a thread of a request it holds. Every route answers only for
the token's own account's requests; none ever names a path of the
computer but the relative ones a run's answer names. Each is a sync
``def``: it reads the pages.db off the event loop.

The chat turns (step 4d): a page of such a work folder has its maker's
chat with that agent here (gamma/routers/local_agents.py), its turns run
on the computer. ``GET /chats`` lists the turns waiting and the runs asked
to stop; a run takes one with ``/chats/{turn}/claim``, keyed by its id as a
Send's is, and gets the message, the earlier ones and the page's path; it
writes the agent's message with ``/chats/{turn}/write``, each write
numbered (``seq``), so this server takes only the newest one of the run
that holds the turn running (gamma/local_agents/chats.py). What a turn asks
never comes here: the message says ``waiting`` while an ask waits for an
answer on the computer (step 5b)."""

import asyncio
import json
import re
from contextlib import contextmanager
from pathlib import PurePosixPath

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import comments, workspaces
from ..auth import _api_bearer, _off_loop, is_token, read_body, require_personal_user_id, require_ws
from ..computers import registry
from ..db import connect_pages_db, page_now
from ..feedback import requests
from ..feedback.entries import FeedbackError
from ..integrations import Connection, resolve_token_scope

router = APIRouter(prefix="/api", tags=["computers"])

CHANNEL_ONLY = "Only a computer's token opens its channel."
MAX_ANNOUNCE = 128 * 1024  # bytes of one announce: the vaults and the models each assistant offers
MAX_PROGRESS = 300         # characters of a run's progress
MAX_PATH = 1024            # characters of a changed file's relative path
MAX_CHANGED = 10_000       # changed files one answer names
CHANGES_NAMED = 5          # changed files the request's end names
_RUN_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
NO_REQUEST = "no such request"


def _computer(request: Request) -> tuple[str, str, str, str]:
    """``(user_id, username, workspace, token_id)`` of the computer's token
    the request came with; 403 for a session or another token."""
    if not is_token(request) or request.state.token_scope != "computer":
        raise HTTPException(status_code=403, detail=CHANNEL_ONLY)
    return request.state.user_id, request.state.user, request.state.token_ws, request.state.token_id


@router.post("/computers/announce")
async def announce(request: Request):
    """``{computer: {id, name}, app, vaults: [{kind: "folder", folder_id,
    paused, agent?} | {kind: "file", page_id, paused}], assistants: [{name,
    label, found, ready, version, code, models?: [{id, label, efforts,
    effort, default}]}]}`` → ``{account: {id, username},
    computer: {id, name}, workspace: {id}, version, wait}``
    (``registry.announce``): what its polls name as ``v``, and the longest
    hold. 400 for a malformed body (more than ``registry.MAX_MODELS`` models
    an assistant), 413 past ``MAX_ANNOUNCE``."""
    user_id, username, ws, token_id = _computer(request)
    raw = await read_body(request, MAX_ANNOUNCE, "An announce is at most 128 KiB.")
    try:
        body = json.loads(raw or b"null")
    except ValueError:
        raise HTTPException(status_code=400, detail="The announce is no JSON.")
    try:
        return await asyncio.to_thread(registry.announce, user_id, username, ws, token_id, body)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/computers/poll")
async def poll(request: Request, v: str = "", wait: float = registry.POLL_S):
    """Held up to ``wait`` seconds (at most ``registry.POLL_S``; 0 answers
    at once): ``{announce: true}`` at once when this server does not know
    the computer or ``v`` is not its last announce's version, or when it is
    woken for it; ``{wake: [...]}`` when woken for work (4c, 4d); ``{}``
    when the hold ends. After the hold the token is read again, off the
    event loop: one revoked meanwhile, or whose account lost the workspace,
    is 401 and the computer is forgotten here."""
    _user_id, _username, _ws, token_id = _computer(request)
    held = registry.hold(token_id, v)
    if held is None:
        return {"announce": True}
    live, waiter = held
    try:
        await asyncio.wait_for(waiter[1].wait(), timeout=max(0.0, min(wait, registry.POLL_S)))
    except asyncio.TimeoutError:
        pass
    finally:
        due = registry.release(token_id, live, waiter)
    if due:
        await asyncio.to_thread(registry.seen, token_id)
    found = await _off_loop(resolve_token_scope, _api_bearer(request))
    if not found or found.scope != "computer" or found.token_id != token_id:
        await asyncio.to_thread(registry.forget, token_id)
        raise HTTPException(status_code=401, detail="invalid or expired token")
    return registry.answer(waiter[2])


@router.get("/pages/{page_id}/computer")
def page_computers(page_id: str, request: Request):
    """``{computers: [{id, name, online, since, vault: {kind, name, paused},
    assistants: [{name, label, found, ready, version, code}]}]}``: the
    signed-in account's computers that keep the page (``registry.of_page``),
    ``[]`` for none. A share's reader 404, a token or a guest 403."""
    if request.query_params.get("share"):
        raise HTTPException(status_code=404, detail="Not found")
    user_id = require_personal_user_id(request, "Sign in with your own account to see the computers you keep this on.")
    return {"computers": registry.of_page(user_id, require_ws(request), page_id)}


# --- the Sends a computer runs (step 4c) ----------------------------------------------------------

class SendClaim(BaseModel):
    run: str      # the id the computer minted for its run: a retry names it again
    agent: str    # the folder's agent there (``codex``, ``claude``)


class SendProgress(BaseModel):
    run: str
    detail: str = ""


class SendReply(BaseModel):
    id: str            # the reply's comment id, minted on the computer once per (run, thread)
    thread_id: str
    text: str
    resolve: bool = False


class SendAnswers(BaseModel):
    run: str
    replies: list[SendReply] = []
    state: str                 # done | failed
    summary: str = ""          # the agent's summary (done)
    changed: list[str] = []    # the files the run changed, by their paths in the folder
    error: str = ""            # what members read of a failure (failed)


@contextmanager
def _refusals():
    try:
        yield
    except FeedbackError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


def _run_id(run: str) -> str:
    if not _RUN_ID.match(run or ""):
        raise HTTPException(status_code=400, detail="A run's id is 1 to 64 letters, digits, - or _.")
    return run


def _label(agent: str) -> str:
    from ..local_agents import catalog  # local: the agents' package is loaded where an agent is named

    return catalog.label(agent)


def _held(conn, request_id: str, user_id: str, token_id: str) -> dict:
    """The run of this computer that holds the request or held it, a
    request of the token's account (``requests.run_of``); 404 otherwise."""
    run = requests.run_of(conn, request_id)
    if run is None or run["holder"] != token_id or requests.requester(conn, request_id) != user_id:
        raise FeedbackError(404, NO_REQUEST)
    return run


@router.get("/computers/sends")
def waiting_sends(request: Request):
    """``{sends: [{id, folder_id, page_id?, at}], withdrawn: [{id, run}]}``:
    the Sends the token's account made in Gamma that wait in the work
    folders this computer keeps with an agent (not paused there; oldest
    first, at most ``requests.MAX_WAITING``; ``page_id`` for a file's;
    none while the account may not edit), and the requests its runs hold
    that were withdrawn meanwhile or are no longer running, each listed
    once: those runs stop."""
    user_id, _username, ws, token_id = _computer(request)
    folders = registry.agent_folders(token_id, ws)   # none while the account may not edit
    taking = [w for w, folder in folders.items() if not folder["paused"]]
    with connect_pages_db(ws) as conn:
        sends = requests.waiting_for(conn, user_id, taking) if taking else []
        withdrawn = requests.withdrawn_runs(conn, token_id)
    return {"sends": sends, "withdrawn": withdrawn}


@router.post("/computers/sends/{request_id}/claim")
def claim_send(request_id: str, payload: SendClaim, request: Request):
    """``{run, agent}`` → ``{request, entries, seen, threads}``: the Send
    taken for the run ``run`` (``running``, set by the agent's label) in one
    write, or held by that run already (a retry: answered again, the
    entries read again). ``entries`` its threads' as they are now
    (``requests.entries_of``, each on a PDF page with its ``place``: page,
    box, rects, doc_id), ``seen`` this server's clock when they were read,
    ``threads`` who spoke last in each (``requests.last_speakers``). 400
    for a run's id of another shape; 403 while the account may not edit;
    404 for no Send of the token's account made in Gamma in a work folder
    this computer keeps with an agent; 409 when another run holds it, it is
    no longer sent, the folder's agent there is another or the folder is
    paused there; 423 while a delete holds the workspace."""
    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    folders = registry.agent_folders(token_id, ws)
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        row = conn.execute("SELECT by, via, work_folder FROM feedback_requests WHERE id = ?",
                           (request_id,)).fetchone()
        if not row or row[0] != user_id or row[1] != requests.SESSION:
            raise FeedbackError(404, NO_REQUEST)
        if not workspaces.at_least(workspaces.role_of(ws, user_id), "editor"):   # agent_folders then has none
            raise FeedbackError(403, f"Only an owner or an editor of this workspace can send comments to "
                                     f"{_label(payload.agent) if payload.agent in registry.AGENTS else 'an agent'}.")
        if row[2] not in folders:
            raise FeedbackError(404, NO_REQUEST)
        folder = folders[row[2]]
        label = _label(folder["agent"])
        if payload.agent != folder["agent"]:
            raise FeedbackError(409, f"This work folder's agent is {label}.")
        if folder["paused"]:
            raise FeedbackError(409, f"The work folder is paused: {label} starts once it syncs again.")
        seen = page_now()
        found = requests.entries_of(ws, conn, requests.get(conn, request_id), places=True)
        read = {e["id"]: [c["id"] for c in e["comments"]] for e in found}
        record = requests.claim_run(ws, conn, request_id, holder=token_id, run=run, agent=folder["agent"],
                                    by_name=label, detail=f"Starting {label}", seen=seen, read=read)
        threads = requests.last_speakers(conn, [t["id"] for t in record["threads"]])
    return {"request": record, "entries": found, "seen": seen, "threads": threads}


@router.post("/computers/sends/{request_id}/progress")
def send_progress(request_id: str, payload: SendProgress, request: Request):
    """``{run, detail}`` → ``{ok}``: the request's progress (one line, at
    most ``MAX_PROGRESS`` characters) while the run ``run`` holds it
    running. 404 for no request this computer holds or held; 409 when
    another run holds it or it is no longer running (withdrawn: the run
    stops)."""
    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        held = _held(conn, request_id, user_id, token_id)
        requests.run_state(ws, conn, request_id, holder=token_id, run=run, state="running",
                           detail=" ".join(payload.detail.split())[:MAX_PROGRESS], by_name=_label(held["agent"]))
    return {"ok": True}


@router.get("/computers/threads/{thread_id}/picture")
def thread_picture(thread_id: str, request: Request, at: str = "now"):
    """The JPEG of a thread's region (``at`` now or before), as ``GET
    /api/feedback/threads/{id}/picture`` answers it, for a thread of a
    request a run of this computer holds; 404 for any other."""
    from .feedback import picture_of  # local: the feedback routes reach the agents' package

    _user_id, _username, ws, token_id = _computer(request)
    if at not in ("now", "before"):
        raise HTTPException(status_code=400, detail="at is now or before")
    with connect_pages_db(ws) as conn:
        if not requests.held_by(conn, token_id, thread_id):
            raise HTTPException(status_code=404, detail="no feedback item has that id")
    return picture_of(ws, thread_id, at)


def _relative(path: str) -> str:
    """A changed file as an answer names it: its path in the folder; 400
    for anything else."""
    parts = PurePosixPath(path)
    if not 0 < len(path) <= MAX_PATH or "\\" in path or ":" in path or parts.is_absolute() or ".." in parts.parts:
        raise HTTPException(status_code=400, detail="A changed file is named by its path in the folder.")
    return path


def _is_relative(path: str) -> bool:
    try:
        _relative(path)
    except HTTPException:
        return False
    return True


def _sentence(text: str) -> str:
    text = " ".join((text or "").split())
    return text if not text or text.endswith((".", "!", "?")) else text + "."


def _end(payload: SendAnswers, changed: list[str], label: str, written: int, asked: int,
         refusal: str) -> tuple[str, str]:
    """``(state, detail)`` the request ends with: ``done`` with the agent's
    summary (else how many it answered) and the files it changed, the
    first ``CHANGES_NAMED`` by name; ``failed`` with the failure's words. A
    run none of whose replies could be written fails with the first
    refusal, as an agent on this computer's does."""
    if payload.state == "failed":
        return "failed", _sentence(payload.error or payload.summary) or f"{label} could not finish."
    if asked and not written:
        return "failed", refusal or f"{label}'s answers could not be written."
    detail = _sentence(payload.summary) or f"Answered {written} of {asked} comment{'' if asked == 1 else 's'}."
    if changed:
        more = len(changed) - CHANGES_NAMED
        detail += f" Changed {', '.join(changed[:CHANGES_NAMED])}" + (f" and {more} more." if more > 0 else ".")
    return "done", detail


@router.post("/computers/sends/{request_id}/answers")
def send_answers(request_id: str, payload: SendAnswers, request: Request):
    """``{run, replies: [{id, thread_id, text, resolve}], state: "done" |
    "failed", summary, changed, error?}`` → ``{ok, written}``: the run's
    answers, written here as its agent's while that run holds the request
    running (``feedback.agent.answer_run``: each reply under its id, so a
    retry writes none twice; a thread asked resolved unless a person said
    in it what the run did not read; the text made inert as the computer
    made it, ``prompt.inert``), then the request's end (``_end``).
    ``written`` the ids of the replies that stand written. The same
    answers again once the end is written (a retry) write nothing and
    answer the same. 400 for another state, a reply to a thread the
    request does not name or a changed file named by more than its path in
    the folder; 404 for no request this computer holds or held; 409 when
    another run holds it or it is no longer running (withdrawn: nothing is
    written, the run stops)."""
    from ..feedback import agent as feedback_agent
    from ..local_agents import prompt  # local: the agents' package is loaded where an agent's words are stored

    user_id, username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    if payload.state not in ("done", "failed"):
        raise HTTPException(status_code=400, detail="state is done or failed")
    changed = [_relative(path) for path in payload.changed[:MAX_CHANGED]]
    workspaces.refuse_writes(ws)
    with connect_pages_db(ws) as conn, _refusals():
        held = _held(conn, request_id, user_id, token_id)
        if held["run"] != run:
            raise FeedbackError(409, "Another run holds this request.")
        record = requests.get(conn, request_id)
        named, replies = {t["id"] for t in record["threads"]}, {}
        for item in payload.replies:
            if item.thread_id not in named:
                raise FeedbackError(400, "A reply answers a thread of the request.")
            replies.setdefault(item.thread_id, {"id": item.id, "thread_id": item.thread_id, "resolve": item.resolve,
                                                "text": prompt.inert(item.text.strip())[:comments.MAX_BODY]})
        again = bool(held["ended"]) and held["ended"] == record["state"]
        if again:
            stored = {r[0] for r in conn.execute("SELECT id FROM comments WHERE id IN (SELECT value FROM json_each(?))",
                                                 (json.dumps([r["id"] for r in replies.values()]),))}
            return {"ok": True, "written": [r["id"] for r in replies.values() if r["id"] in stored]}
        if record["state"] != "running" or held["ended"]:   # withdrawn: refused, the run stopped from then on
            requests.run_state(ws, conn, request_id, holder=token_id, run=run, state=payload.state, by_name="")
    label = _label(held["agent"])
    connection = Connection(user_id=user_id, workspaces=[ws], scope="feedback",
                            token_id=f"{comments.LOCAL_AGENT}{held['agent']}", name=label, username=username)
    written, refusal = feedback_agent.answer_run(connection, ws, list(replies.values()), seen=held["seen_at"],
                                                 read=held["read"])
    state, detail = _end(payload, changed, label, len(written), len(replies), refusal)
    with connect_pages_db(ws) as conn, _refusals():
        requests.run_state(ws, conn, request_id, holder=token_id, run=run, state=state, detail=detail, by_name=label)
    return {"ok": True, "written": written}


# --- the chat turns a computer runs (step 4d) -------------------------------------------------------

MAX_STEP_TEXT = 200    # characters of a step's path, program or status
MAX_ERROR = 2000       # characters of a turn's failure
TURN_STATES = ("running", "done", "failed", "stopped")


class ChatClaim(BaseModel):
    run: str      # the id the computer minted for its run: a retry names it again
    agent: str    # the folder's agent there (``codex``, ``claude``)


class ChatWrite(BaseModel):
    run: str
    seq: int      # above the last write taken from that run, else nothing is written
    fields: dict


@contextmanager
def _chat_refusals():
    from ..local_agents import chats  # local: the agents' package is loaded where an agent is named

    try:
        yield
    except chats.ChatError as e:
        raise HTTPException(status_code=e.status, detail=e.detail) from None


@router.get("/computers/chats")
def waiting_chats(request: Request):
    """``{turns: [{id, conversation_id, page_id, folder_id, at}], stops:
    [{id, run}]}``: the turns of the token's account's chat that wait in
    pages of the work folders this computer keeps with an agent (not paused
    there; each a turn of that agent's; oldest first, at most
    ``chats.MAX_WAITING``; none while the account may not edit,
    ``registry.agent_folders``), and the runs of it
    whose turns were asked to stop (listed until the run writes its end)."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    folders = registry.agent_folders(token_id, ws)
    taking = {w: folder["agent"] for w, folder in folders.items() if not folder["paused"]}
    with connect_pages_db(ws) as conn:
        turns = chats.waiting_turns(conn, user_id, taking) if taking else []
        stops = chats.stops_of(conn, token_id)
    return {"turns": turns, "stops": stops}


@router.post("/computers/chats/{turn_id}/claim")
def claim_chat(turn_id: str, payload: ChatClaim, request: Request):
    """``{run, agent}`` → ``{turn: {id, conversation_id, page_id, text,
    selections, model, effort}, history, page: {path, title}, seen}``
    (``chats.claim``): the turn taken for the run ``run`` (it and its
    message ``running``) in one write, or held by that run already (a
    retry: answered again). 400 for a run's id of another shape; 403 while
    the account may not edit; 404 for no turn of the token's account's in a
    page of a work folder this computer keeps with an agent; 409 when
    another run holds it, it is no longer queued, the folder's agent there
    is another, the turn is another agent's (left queued) or the folder is
    paused there; 423 while a delete holds the workspace."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    folders = registry.agent_folders(token_id, ws)
    workspaces.refuse_writes(ws)
    with _chat_refusals():
        with connect_pages_db(ws) as conn:
            row = chats.run_of(conn, turn_id)
            page = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                                (row["page_id"],)).fetchone() if row else None
        if row is None or row["user_id"] != user_id:
            raise chats.ChatError(404, chats.NO_TURN)
        if not workspaces.at_least(workspaces.role_of(ws, user_id), "editor"):   # agent_folders then has none
            raise chats.ChatError(403, f"Only an owner or an editor of this workspace can talk to "
                                       f"{_label(payload.agent) if payload.agent in registry.AGENTS else 'an agent'}.")
        if not page or page[0] not in folders:
            raise chats.ChatError(404, chats.NO_TURN)
        folder = folders[page[0]]
        label = _label(folder["agent"])
        if payload.agent != folder["agent"]:
            raise chats.ChatError(409, f"This work folder's agent is {label}.")
        if folder["paused"]:
            raise chats.ChatError(409, f"The work folder is paused: {label} starts once it syncs again.")
        return chats.claim(ws, turn_id, user_id=user_id, holder=token_id, run=run, agent=folder["agent"],
                           computer=registry.computer_name(token_id))


def _step(raw) -> dict:
    """A step of the agent's message as a run writes it, kept as the
    chat's messages keep one (runner._step): ``{id, kind, status}``, with
    ``path`` (its path in the folder, else "outside") for a read or an
    edit, ``program`` for a command. 400 for another shape."""
    from ..local_agents import events

    if not isinstance(raw, dict):
        raise HTTPException(status_code=400, detail="A step is an object.")
    kind = raw.get("kind") if raw.get("kind") in events.KINDS else "other"
    step = {"id": events.one_line(str(raw.get("id") or ""), 64), "kind": kind,
            "status": events.one_line(str(raw.get("status") or ""), MAX_STEP_TEXT)}
    if kind in ("read", "edit"):
        path = str(raw.get("path") or "")
        try:
            step["path"] = events.one_line(_relative(path), MAX_STEP_TEXT) if path != "outside" else "outside"
        except HTTPException:
            step["path"] = "outside"
    elif kind == "run":
        step["program"] = events.one_line(str(raw.get("program") or ""), MAX_STEP_TEXT)
    return step


def _turn_fields(raw: dict) -> dict:
    """The agent message's fields a run writes, as kept: ``text`` (made
    inert again, ``prompt.inert``, at most ``chats.MAX_TEXT``), ``steps``,
    ``changes`` (paths in the folder: one named otherwise, which the
    computer's own file system may allow, left out), ``changed``, ``built``, ``error``,
    ``fresh``, ``waiting`` (an ask of the turn waits for an answer on the
    computer, which alone shows it: never ``asks``), ``state`` (running or
    an end), ``model``, ``effort``; ``ended_at`` is this server's clock
    (``chats.write_run``), any other field left out. 400 for one of another
    shape."""
    from ..local_agents import chats, prompt, store

    out: dict = {}

    def text(key: str, limit: int) -> None:
        if key in raw:
            if not isinstance(raw[key], str):
                raise HTTPException(status_code=400, detail=f"{key} is text.")
            out[key] = prompt.inert(raw[key].strip())[:limit]

    def flag(key: str) -> None:
        if key in raw:
            if not isinstance(raw[key], bool):
                raise HTTPException(status_code=400, detail=f"{key} is true or false.")
            out[key] = raw[key]

    def choice(key: str, limit: int) -> None:
        if key in raw:
            if raw[key] is not None and not isinstance(raw[key], str):
                raise HTTPException(status_code=400, detail=f"{key} is text or null.")
            out[key] = (raw[key] or "").strip()[:limit] or None

    text("text", chats.MAX_TEXT)
    text("error", MAX_ERROR)
    flag("built")
    flag("fresh")
    flag("waiting")
    choice("model", 100)
    choice("effort", 40)
    if "steps" in raw:
        if not isinstance(raw["steps"], list):
            raise HTTPException(status_code=400, detail="steps is a list.")
        out["steps"] = [_step(step) for step in raw["steps"][:store.MAX_STEPS]]
    if "changes" in raw:
        if not isinstance(raw["changes"], list) or not all(isinstance(p, str) for p in raw["changes"]):
            raise HTTPException(status_code=400, detail="changes is a list of paths.")
        out["changes"] = [path for path in raw["changes"][:store.MAX_CHANGES] if _is_relative(path)]
    if "changed" in raw:
        if not isinstance(raw["changed"], int) or isinstance(raw["changed"], bool) or raw["changed"] < 0:
            raise HTTPException(status_code=400, detail="changed is a count.")
        out["changed"] = raw["changed"]
    if "state" in raw:
        if raw["state"] not in TURN_STATES:
            raise HTTPException(status_code=400, detail="state is running, done, failed or stopped")
        out["state"] = raw["state"]
    return out


@router.post("/computers/chats/{turn_id}/write")
def write_chat(turn_id: str, payload: ChatWrite, request: Request):
    """``{run, seq, fields}`` → ``{ok}``: the agent message's fields
    (``_turn_fields``) written by the run ``run`` that holds the turn
    running, when ``seq`` is above the last one written for that run (else
    ``{ok, stale: true}``, nothing written: a retry or a late write); an
    end (``state`` done, failed or stopped) ends the turn. 400 for a
    field of another shape; 404 for no turn this computer holds or held; 409 when
    another run holds it, it was stopped here or its conversation is gone
    (the run stops)."""
    from ..local_agents import chats

    user_id, _username, ws, token_id = _computer(request)
    run = _run_id(payload.run)
    fields = _turn_fields(payload.fields)
    workspaces.refuse_writes(ws)
    with _chat_refusals():
        written = chats.write_run(ws, turn_id, user_id=user_id, holder=token_id, run=run, seq=payload.seq,
                                  fields=fields)
    return {"ok": True} if written else {"ok": True, "stale": True}

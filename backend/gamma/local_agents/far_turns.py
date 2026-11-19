"""A conversation with the agent of another computer of the person's, or of
another account's whose agent serves the person (a co-author's, ``lent``;
gamma/computers/registry.py ``seats``): the conversation stays here, that
computer (its ``computer``) takes its turns over its channel
(gamma/routers/computers.py) and writes the agent's message back. Each
turn is a ``computer_chat_runs`` row: ``queued`` until a run of that
computer claims it (``holder`` its token, ``run`` the id it minted),
``running``, then its end; ``seq`` the last write taken from that run, so
a retried or late write never goes back; ``stop`` the person's Stop of a
running turn, which the computer is told (a second Stop ends it here);
``token`` the turn's token of this server (Gamma's tools for that agent,
``keep_token``), renewed by its writes and deleted at its end however it
ends; ``answers`` the answers given here to what its agent asks, where
that computer relays its asks (``answer_run``), which the computer takes
from its listing (``answers_of``). Only the run that holds a turn writes
it, and only while it runs.
The claim names where its agent works by an id, never a path (``dir_of``):
the work folder the bucket is in, whose vault that computer keeps, a
directory it offers, or one of its own. A turn queued that the computer
would no longer take (its agent turned off, a person it no longer serves,
the folder or the directory no longer kept, the computer gone) fails
(``fail_queued``, from the announce and a token's end), so none runs
later unasked.

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

import json
import threading

from ..blocks_store import write_lock
from ..db import connect_pages_db, page_now
from ..logbuf import log
from . import chats

MAX_WAITING = 20          # turns one listing gives a computer at most
NO_TURN = "No turn has that id."
ANSWERED = "That was answered already."
DETACHED = "The synced folder was detached before its computer took this turn."
OFFLINE = "{computer} is offline: send this again once it connects."
# A far turn's token lives this long from its claim and, renewed by the
# run's writes at most once a minute, from its last write: the computer's
# turn timeout and the slack of one (runner.RUN_TIMEOUT_S, TOKEN_SLACK_S).
TOKEN_S = 1800 + 60
TOKEN_RENEW_S = 60

_RUN_COLUMNS = ("turn_id", "conversation_id", "bucket", "user_id", "agent", "computer", "holder", "run", "seq",
                "stop", "state", "queued_at", "started_at", "token", "answers")


def remote_of(ws: str, bucket: str, user_id: str, computer_id: str = "", agent: str = "") -> dict | None:
    """The far seat of ``user_id``'s on the bucket (``registry.seat_of``):
    the agent ``agent`` of its computer ``computer_id`` (either "" for the
    first: a computer that keeps the work folder the bucket is in first),
    or of another account's whose agent serves ``user_id`` on a work
    folder's files (``lent_by`` its owner's username), ``{token_id,
    user_id, computer_id, computer, agent, label, online, since, paused,
    ready, code, lent_by, folder_id, dirs}``: the side's gate for a bucket
    whose agent runs on another computer; None for none."""
    from ..computers import registry  # local: registry reaches this package where an agent is named

    if not bucket:
        return None
    return registry.seat_of(ws, bucket, user_id, computer_id, agent)


def far_problem(found: dict) -> str:
    """Why the online computer's agent (a far seat, ``remote_of``) cannot
    take a turn now, in the words the @ menu says it; "" while it is ready
    or not known to be otherwise."""
    if found["ready"] is not False:
        return ""
    label, computer = found["label"], found["computer"]
    if found["code"] == "signed_out":
        return f"{label} is not signed in on {computer}."
    if found["code"] == "missing":
        return f"{label} is not on {computer}."
    return f"{label} is not ready on {computer}."


def far_dir(far: dict, wanted: str | None, conversation: dict | None) -> str:
    """Where a far turn's agent works, the ``dir`` its conversation keeps
    (``dir_of``): the work folder the bucket is in where that computer keeps
    it (``far``'s ``folder_id``), else the directory ``wanted`` names among
    those it offers (None: the ``conversation``'s, while it talks to that
    computer's agent; "" one of its own). ChatError 400 for one it does not
    offer, 409 for a co-author's turn outside a synced folder (decision 13
    of docs/research/agents-as-participants-plan.md). A conversation's
    directory that is no longer kept or offered gives "": ``begin`` archives
    that conversation and a new one starts."""
    from . import catalog

    if far["folder_id"]:
        return far["folder_id"]
    if far["lent_by"]:
        raise chats.ChatError(409, f"{catalog.label(far['agent'])} works for you only on the files of a synced folder.")
    offered = {d.get("id") for d in far["dirs"] if isinstance(d, dict)}
    if wanted is None:
        same = conversation is not None and (conversation["computer"], conversation["agent"]) == (
            far["computer_id"], far["agent"])
        kept = conversation["dir"] if same else ""
        return kept if kept in offered else ""
    if wanted and wanted not in offered:
        raise chats.ChatError(400, f"No directory offered on {far['computer']} has that id.")
    return wanted


def dir_of(tree, conversation: dict) -> dict:
    """Where a far conversation's agent works, as the claim names it to its
    computer: ``{kind: "vault", id}`` for one whose ``dir`` is a folder of
    the ``tree`` (the work folder its bucket is in, whose vault that
    computer keeps), ``{kind: "offered", id}`` for a directory that computer
    offers, ``{kind: "own", id: ""}`` for one of its own; an id, never a
    path."""
    dir = conversation["dir"]
    if not dir:
        return {"kind": "own", "id": ""}
    return {"kind": "vault" if dir in tree else "offered", "id": dir}


def run_of(conn, turn_id: str) -> dict | None:
    """The turn's ``computer_chat_runs`` row with its conversation's
    ``owner`` (whose computer runs it), ``computer_id`` (that computer) and
    ``dir`` (where its agent works there, ``dir_of``), None for a turn that
    runs on no other computer."""
    row = conn.execute(f"SELECT {', '.join('r.' + c for c in _RUN_COLUMNS)}, COALESCE(NULLIF(c.owner, ''), "
                       "r.user_id), COALESCE(c.computer, ''), COALESCE(c.dir, '') FROM computer_chat_runs r "
                       "LEFT JOIN computer_chats c ON c.id = r.conversation_id WHERE r.turn_id = ?",
                       (turn_id,)).fetchone()
    return dict(zip((*_RUN_COLUMNS, "owner", "computer_id", "dir"), row)) if row else None


def holds_thread(conn, holder: str, owner: str, thread_id: str) -> bool:
    """Whether a run of the computer ``holder`` holds running a turn of the
    thread ``thread_id``'s conversation whose agent is ``owner``'s: what
    lets that computer fetch the thread's pictures
    (gamma/routers/computers.py)."""
    return bool(conn.execute(
        "SELECT 1 FROM computer_chat_runs r JOIN computer_chats c ON c.id = r.conversation_id "
        "WHERE r.holder = ? AND r.state = 'running' AND c.thread_id = ? "
        "AND COALESCE(NULLIF(c.owner, ''), r.user_id) = ? LIMIT 1", (holder, thread_id, owner)).fetchone())


def _close(conn, row: dict, state: str) -> str:
    """The turn's row ends ``state`` and lets go of its token and of the
    answers kept for its computer → the token's id ("" for none), the
    caller's to delete once it committed (``_drop_tokens``). Inside the
    caller's write lock."""
    conn.execute("UPDATE computer_chat_runs SET state = ?, token = '', answers = '{}' WHERE turn_id = ?",
                 (state, row["turn_id"]))
    return row.get("token") or ""


def _drop_tokens(tokens) -> None:
    """The far turns' tokens ``tokens`` deleted, their turns ended: a call
    of their agent after it is refused. Never raises: an expired token reads
    nothing either."""
    from .. import integrations  # local: integrations imports the workspaces

    for token in tokens:
        if token:
            try:
                integrations.drop(token)
            except Exception as e:  # noqa: BLE001 — it expires all the same
                log.warning(f"[agents] deleting the token {token} of a far turn: {e}")


def _end_run(conn, row: dict, state: str, fields: dict | None = None, ended: list | None = None) -> str:
    """The turn's row and its message (when it still reads queued or
    running) end ``state``, ``fields`` written with it; a thread's turn
    ended joins ``ended``, to be answered in its thread once the caller
    committed (``_answered``) → its token's id (``_close``). Inside the
    caller's write lock."""
    token = _close(conn, row, state)
    conversation = chats.get(conn, row["conversation_id"])
    at = chats._message_at(conversation, row["turn_id"])
    if at is not None and conversation["messages"][at].get("state") in chats.BUSY:
        messages = conversation["messages"]
        message = {**messages[at], **(fields or {}), "state": state, "asks": [], "waiting": False,
                   "ended_at": page_now()}
        chats._save(conn, conversation, [*messages[:at], message, *messages[at + 1:]])
        if conversation["thread_id"] and ended is not None:
            ended.append((conversation["id"], row["turn_id"]))
    return token


def waiting_turns(conn, user_id: str, computer_id: str, people=(), paused=()) -> list[dict]:
    """The turns that wait for the computer ``computer_id`` of ``user_id``'s:
    of its conversations with that computer's agent, of ``user_id``'s own
    or of one of the ``people`` its agent serves, none in a work folder
    paused there (``paused``) nor of a bucket gone (a page in Recently
    deleted), oldest first, at most ``MAX_WAITING``: ``[{id,
    conversation_id, bucket, agent, dir: {kind, id}, at}]``, ``dir`` where
    its agent works (``dir_of``)."""
    from ..work_folders.marks import Tree

    rows = conn.execute(
        "SELECT r.turn_id, r.conversation_id, r.bucket, COALESCE(NULLIF(r.agent, ''), c.agent), c.dir, r.queued_at "
        "FROM computer_chat_runs r JOIN computer_chats c ON c.id = r.conversation_id "
        "WHERE r.state = 'queued' AND c.computer = ? AND COALESCE(NULLIF(c.owner, ''), r.user_id) = ? "
        "AND (r.bucket = 'home' OR EXISTS (SELECT 1 FROM unified_blocks b WHERE b.id = r.bucket "
        "AND (b.parent_id = 'root' OR b.page_id = 'folders'))) "
        "AND (r.user_id = ? OR r.user_id IN (SELECT value FROM json_each(?))) "
        "AND c.dir NOT IN (SELECT value FROM json_each(?)) ORDER BY r.queued_at, r.turn_id LIMIT ?",
        (computer_id, user_id, user_id, json.dumps(sorted(people)), json.dumps(sorted(paused)),
         MAX_WAITING)).fetchall()
    tree = Tree.read(conn) if rows else None
    return [{"id": turn_id, "conversation_id": conversation_id, "bucket": bucket, "agent": agent,
             "dir": dir_of(tree, {"dir": dir}), "at": at}
            for turn_id, conversation_id, bucket, agent, dir, at in rows]


def stops_of(conn, holder: str) -> list[dict]:
    """The runs of the computer ``holder`` asked to stop: ``[{id, run}]``,
    the turns they hold running with their stop set (listed until the run
    writes its end)."""
    rows = conn.execute("SELECT turn_id, run FROM computer_chat_runs WHERE holder = ? AND state = 'running' "
                        "AND stop != 0 ORDER BY turn_id", (holder,)).fetchall()
    return [{"id": turn_id, "run": run} for turn_id, run in rows]


def answers_of(conn, holder: str) -> list[dict]:
    """The answers kept here for what the turns the computer ``holder``
    runs ask (``answer_run``): ``[{id, run, ask, decision, note}]``, listed
    until a write of the run no longer names the ask (``write_run``) or the
    turn ends."""
    rows = conn.execute("SELECT turn_id, run, answers FROM computer_chat_runs WHERE holder = ? AND state = 'running' "
                        "AND answers != '{}' ORDER BY turn_id", (holder,)).fetchall()
    return [{"id": turn_id, "run": run, "ask": ask, "decision": said["decision"], "note": said.get("note") or ""}
            for turn_id, run, answers in rows for ask, said in _answers(answers).items()]


def _answers(raw: str) -> dict:
    try:
        found = json.loads(raw or "{}")
    except ValueError:
        return {}
    return {k: v for k, v in found.items() if isinstance(v, dict)} if isinstance(found, dict) else {}


def answer_run(ws: str, turn_id: str, ask_id: str, decision: str, note: str, by: str) -> str:
    """The answer of ``by`` (the turn's person or its agent's owner: the
    route checks it) to the ask ``ask_id`` of a turn that runs on another
    computer, which relays what its agent asks (its message's ``asks``):
    ``decision`` (``once`` or ``deny``, the route's check) and ``note`` kept
    with the turn's run (``answers``, for that computer to take:
    ``answers_of``), the ask out of the message at once → the token of the
    computer that runs it, the caller's to wake. ChatError 409 for a turn no
    longer running, an ask answered here already, and one its message no
    longer holds (answered on that computer, withdrawn, declined by Gamma,
    or never asked)."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None:
            raise chats.ChatError(404, NO_TURN)
        if row["state"] != "running":
            raise chats.ChatError(409, "This turn has ended." if row["state"] in chats.ENDED
                                  else "No question of this turn waits.")
        answers = _answers(row["answers"])
        if ask_id in answers:
            raise chats.ChatError(409, ANSWERED)
        conversation = chats.get(conn, row["conversation_id"])
        at = chats._message_at(conversation, turn_id)
        reply = conversation["messages"][at] if at is not None else {}
        asks = [a for a in reply.get("asks") or [] if isinstance(a, dict)]
        if not any(a.get("id") == ask_id for a in asks):
            raise chats.ChatError(409, f"{reply.get('label') or 'The agent'} no longer waits for that.")
        answers[ask_id] = {"decision": decision, "note": note, "by": by, "at": page_now()}
        conn.execute("UPDATE computer_chat_runs SET answers = ? WHERE turn_id = ?", (json.dumps(answers), turn_id))
        messages = conversation["messages"]
        message = {**reply, "asks": [a for a in asks if a.get("id") != ask_id]}
        chats._save(conn, conversation, [*messages[:at], message, *messages[at + 1:]])
    return row["holder"]


# The messages steered into turns that run on another computer, until its
# listing hands them over (``steers_of``): turn id -> {holder, run, items}.
# Kept in memory, as Gamma AI's are (gamma/ai_steer.py): one a restart
# loses, or one the agent never takes, goes as the next turn from the chat.
_far_steers: dict[str, dict] = {}
_far_lock = threading.Lock()


def steer_run(ws: str, turn_id: str, item: dict) -> str:
    """A message steered into a turn that runs on another computer
    (``steer_of``'s), kept for that computer to take (``steers_of``) → the
    token of the computer that runs it, the caller's to wake. ChatError
    404 for no such turn, 409 for one not running."""
    with connect_pages_db(ws) as conn:
        row = run_of(conn, turn_id)
    if row is None:
        raise chats.ChatError(404, NO_TURN)
    if row["state"] != "running" or not row["holder"]:
        raise chats.ChatError(409, chats.NOT_STEERED)
    with _far_lock:
        kept = _far_steers.get(turn_id)
        if kept is None or kept["run"] != row["run"]:
            kept = _far_steers[turn_id] = {"holder": row["holder"], "run": row["run"], "items": []}
        if len(kept["items"]) >= chats.MAX_STEERS:
            raise chats.ChatError(409, chats.NOT_STEERED)
        kept["items"].append(item)
    return row["holder"]


def steers_of(holder: str) -> list[dict]:
    """The messages steered into the turns the computer ``holder`` runs
    (``steer_run``), each handed out once: ``[{id, run, steer: {id, text,
    selections}}]``."""
    with _far_lock:
        mine = [turn_id for turn_id, kept in _far_steers.items() if kept["holder"] == holder]
        taken = [_far_steers.pop(turn_id) | {"id": turn_id} for turn_id in mine]
    return [{"id": kept["id"], "run": kept["run"], "steer": item} for kept in taken for item in kept["items"]]


def claim(ws: str, turn_id: str, *, user_id: str, holder: str, computer_id: str, run: str, agent: str,
          computer: str = "") -> dict:
    """The run ``run`` of the computer ``holder`` (a computer of
    ``user_id``'s, of id ``computer_id``, named ``computer``) takes the
    queued turn of its agent ``agent`` in a conversation of ``user_id``'s
    agent with that computer: the row and its message ``running``, in one
    step under the write lock → ``{turn: {id, conversation_id, bucket,
    title, text, selections, model, effort, speed, for: {id, username},
    thread_id?, comment_id?}, history, about, dir, seen, thread?}``:
    ``for`` the account the turn is for (``user_id`` itself, or a
    co-author: the route checks it is one the agent serves), the
    conversation's title, the person's message, the conversation's earlier
    messages as the turn's prompt takes them (``[{role: owner | agent,
    text}]``, the last ``EARLIER``), what it is about (``about``: its path
    in the work folder where it is in one), where its agent works
    (``dir_of``), and this server's clock; a thread's turn also names its
    thread and the comment it is, and gives the thread's entry (``thread``:
    ``threads.entry_of``, with its PDF place, from which the computer finds
    its source line). The same run taking it again (a retry) is answered
    again. A turn whose bucket is gone, or is no longer in the work folder
    its conversation works in, fails. ChatError 404 for no turn of that
    computer's, 409 when another run holds it, it is no longer queued, or it
    is another agent's (left queued)."""
    from ..work_folders.marks import Tree
    from . import threads  # local: threads reads the conversations through this module

    from ..db import account_names, connect_users_db

    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None or row["owner"] != user_id or row["computer_id"] != computer_id:
            raise chats.ChatError(404, NO_TURN)
        conversation = chats.get(conn, row["conversation_id"])
        at = chats._message_at(conversation, turn_id)
        if at is None:
            if row["state"] in chats.BUSY:
                token = _close(conn, row, "stopped")
                conn.commit()
                _drop_tokens([token])
            raise chats.ChatError(404, NO_TURN)
        messages = conversation["messages"]
        reply = messages[at]
        if reply.get("state") not in chats.BUSY and row["state"] in chats.BUSY:
            # its message ended otherwise (a row restored from a backup): never back to running
            token = _close(conn, row, reply.get("state") if reply.get("state") in chats.ENDED else "stopped")
            conn.commit()
            _drop_tokens([token])
            row = run_of(conn, turn_id)
        again = (row["holder"], row["run"]) == (holder, run) and row["state"] == "running"
        if not again and row["state"] != "queued":
            raise chats.ChatError(409, "Another run holds this turn." if row["state"] == "running"
                            else f"The turn is {row['state']} now.")
        if (row["agent"] or reply.get("agent")) != agent:
            raise chats.ChatError(409, f"This turn is for {reply.get('label') or 'another agent'}.")
        found = chats.about(conn, row["bucket"])
        where = dir_of(Tree.read(conn), conversation)
        if found is None:
            refusal = "What this conversation is about is gone."
        elif where["kind"] == "vault" and found["w"] != where["id"]:
            refusal = ("This page is no longer a file of the synced folder." if found["kind"] == "page"
                       else "This folder is no longer in the synced folder.")
        else:
            refusal = ""
        if refusal:
            ended = []
            token = _end_run(conn, row, "failed", {"error": refusal}, ended)
            conn.commit()
            _drop_tokens([token])
            chats._answered(ws, ended)
            raise chats.ChatError(409, refusal)
        if not again:
            now = page_now()
            conn.execute("UPDATE computer_chat_runs SET holder = ?, run = ?, seq = 0, state = 'running', "
                         "started_at = ?, computer = CASE WHEN ? != '' THEN ? ELSE computer END WHERE turn_id = ?",
                         (holder, run, now, computer, computer, turn_id))
            reply = {**reply, "state": "running"}
            chats._save(conn, conversation, [*messages[:at], reply, *messages[at + 1:]])
        person = next((m for m in reversed(messages[:at]) if m.get("role") == "user"), None) or {}
        earlier = chats.earlier_of(messages, at, person)
        thread = threads.entry_of(ws, conn, conversation["thread_id"], places=True) if conversation["thread_id"] else None
    with connect_users_db() as users:
        username = account_names(users, {row["user_id"]}).get(row["user_id"], "")
    turn = {"id": turn_id, "conversation_id": row["conversation_id"], "bucket": row["bucket"],
            "title": conversation["title"], "text": person.get("text") or "", "selections": person.get("selections") or [],
            "model": reply.get("model") or "", "effort": reply.get("effort") or "", "speed": reply.get("speed") or "",
            "for": {"id": row["user_id"], "username": username}}
    out = {"turn": turn, "history": earlier, "about": found, "dir": where, "seen": page_now()}
    if conversation["thread_id"]:
        turn.update(thread_id=conversation["thread_id"], comment_id=person.get("comment_id") or "")
        out["thread"] = thread
    return out


def keep_token(ws: str, turn_id: str, run: str, token_id: str) -> str:
    """The turn's token of this server is ``token_id`` (Gamma's tools for
    the far agent, ``integrations.mint_turn``, minted for the run ``run``
    that holds it running) → the id of the one it had ("" for none), the
    caller's to delete. ChatError 409 when that run no longer holds it
    running: the new one is then the caller's to delete too."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None or row["run"] != run or row["state"] != "running":
            raise chats.ChatError(409, "The turn is no longer this run's.")
        conn.execute("UPDATE computer_chat_runs SET token = ? WHERE turn_id = ?", (token_id, turn_id))
    return row["token"]


def write_run(ws: str, turn_id: str, *, user_id: str, holder: str, run: str, seq: int, fields: dict) -> bool:
    """What the run ``run`` of the computer ``holder`` writes of the turn
    it holds: ``fields`` (as the channel's route keeps them) into its
    message, when ``seq`` is above the last one taken from it (else
    nothing: a retried or late write) → whether it was written. A ``state``
    of ``ENDED`` ends the row too, the message's ``ended_at`` this
    server's clock, and deletes the turn's token and the answers kept for
    it; any other write keeps the token alive (``TOKEN_S`` from now,
    renewed at most every ``TOKEN_RENEW_S``), and its ``asks`` (what the
    computer relays) leave out those answered here (``answer_run``), whose
    answers are kept while the write still names them. ChatError 404 for no turn of an agent of
    ``user_id``'s this computer holds or held, 409 when another run holds
    it, it is no longer running (stopped here) or its message is gone (the
    run stops)."""
    from .. import integrations  # local: integrations imports the workspaces

    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None or row["owner"] != user_id or row["holder"] != holder:
            raise chats.ChatError(404, NO_TURN)
        if row["run"] != run:
            raise chats.ChatError(409, "Another run holds this turn.")
        if seq <= row["seq"]:
            return False
        if row["state"] != "running":
            raise chats.ChatError(409, f"The turn is {row['state']} now.")
        conversation = chats.get(conn, row["conversation_id"])
        at = chats._message_at(conversation, turn_id)
        if at is None or conversation["messages"][at].get("state") not in chats.BUSY:
            token = _close(conn, row, "stopped")
            conn.commit()
            _drop_tokens([token])
            raise chats.ChatError(409, "The conversation is gone." if at is None else "The turn is stopped now.")
        state = fields.get("state")
        answers = _answers(row["answers"])
        if state in chats.ENDED:   # nothing waits past the end
            fields, answers = {**fields, "ended_at": page_now(), "asks": [], "waiting": False}, {}
        elif "asks" in fields:
            # an ask answered here stays out of the message, its answer kept while the run still names it
            asked = {a["id"] for a in fields["asks"]}
            answers = {k: v for k, v in answers.items() if k in asked}
            fields = {**fields, "asks": [a for a in fields["asks"] if a["id"] not in answers]}
        messages = conversation["messages"]
        chats._save(conn, conversation, [*messages[:at], {**messages[at], **fields}, *messages[at + 1:]])
        conn.execute("UPDATE computer_chat_runs SET seq = ?, state = ?, token = CASE WHEN ? THEN '' ELSE token END, "
                     "answers = ? WHERE turn_id = ?",
                     (seq, state if state in chats.ENDED else "running", state in chats.ENDED,
                      json.dumps(answers) if answers else "{}", turn_id))
    if state in chats.ENDED:
        _drop_tokens([row["token"]])
        if conversation["thread_id"]:
            chats._answered(ws, [(conversation["id"], turn_id)])
    elif row["token"]:
        try:
            integrations.renew_turn(row["token"], TOKEN_S, within=TOKEN_S - TOKEN_RENEW_S)
        except Exception as e:  # noqa: BLE001 — the write stands; the next one renews it
            log.warning(f"[agents] renewing the token of far turn {turn_id}: {e}")
    return True


def stop_run(ws: str, turn_id: str, *, online) -> bool:
    """The person's Stop of a turn that runs on another computer: a running
    one whose computer is online (``online(holder)``) has its stop set (the
    computer is told, its run ends it ``stopped``); a queued one, one
    whose computer is away, and a running one Stopped again (its stop set
    already: its computer may no longer run it) end ``stopped`` now, its
    token deleted (a run that comes back is refused at its next write) →
    whether there was one to stop."""
    ended = []
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None:
            return False
        if row["state"] == "running" and online(row["holder"]) and not row["stop"]:
            conn.execute("UPDATE computer_chat_runs SET stop = 1 WHERE turn_id = ?", (row["turn_id"],))
            return True
        conversation = chats.get(conn, row["conversation_id"])
        at = chats._message_at(conversation, row["turn_id"])
        if row["state"] not in chats.BUSY and (
                at is None or conversation["messages"][at].get("state") not in chats.BUSY):
            return False
        token = _end_run(conn, row, "stopped", ended=ended)
    _drop_tokens([token])
    chats._answered(ws, ended)
    return True


def fail_held(ws: str, holder: str, words) -> list[str]:
    """The computer ``holder`` went (its token revoked, replaced or
    expired), so none of its runs can write again: each turn it holds
    running fails, ``words(agent)`` its message's error, its token deleted
    → the turns failed."""
    ended, tokens = [], []
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = [dict(zip(_RUN_COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM computer_chat_runs WHERE holder = ? AND state = 'running'",
            (holder,)).fetchall()]
        for row in rows:
            conversation = chats.get(conn, row["conversation_id"])
            at = chats._message_at(conversation, row["turn_id"])
            tokens.append(_end_run(conn, row, "failed", {
                "error": words(conversation["messages"][at].get("agent") if at is not None else "")}, ended))
    _drop_tokens(tokens)
    chats._answered(ws, ended)
    return [row["turn_id"] for row in rows]


def fail_queued(ws: str, owner: str, computer_id: str, refusal) -> list[str]:
    """The turns queued for the computer ``computer_id`` of ``owner``'s (of
    its own conversations and of its co-authors') that it would not take
    fail, ``refusal(agent, person, kind, dir)`` their message's error (""
    keeps it: registry.py ``_refusal``; ``person`` the account the turn is
    for, ``kind`` and ``dir`` where its conversation works, ``dir_of``): no
    computer would take them, and one turned on again later must not run
    them unasked → the turns failed."""
    from ..work_folders.marks import Tree

    ended, failed = [], []
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = [(dict(zip(_RUN_COLUMNS, r[:-1])), r[-1]) for r in conn.execute(
            f"SELECT {', '.join('r.' + c for c in _RUN_COLUMNS)}, c.dir FROM computer_chat_runs r "
            "JOIN computer_chats c ON c.id = r.conversation_id WHERE r.state = 'queued' AND c.computer = ? "
            "AND COALESCE(NULLIF(c.owner, ''), r.user_id) = ?", (computer_id, owner)).fetchall()]
        tree = Tree.read(conn) if rows else None
        for row, dir in rows:
            where = dir_of(tree, {"dir": dir})
            words = refusal(row["agent"], row["user_id"], where["kind"], where["id"])
            if words:
                _end_run(conn, row, "failed", {"error": words}, ended)
                failed.append(row["turn_id"])
    chats._answered(ws, ended)
    return failed


def fail_detached(ws: str, folder_id: str) -> list[str]:
    """The turns queued for another computer in conversations that work in
    the work folder ``folder_id``, just detached (gamma/work_folders/detach.py),
    fail (``DETACHED``): once it is no work folder, no computer is given
    them. One running there stops on that computer, whose round finds the
    folder detached (gamma/folder_links.py ``LET_GO_THERE``) → the turns
    failed."""
    ended = []
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = [dict(zip(_RUN_COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join('r.' + c for c in _RUN_COLUMNS)} FROM computer_chat_runs r "
            "JOIN computer_chats c ON c.id = r.conversation_id WHERE r.state = 'queued' AND c.dir = ? "
            "AND c.computer != ''", (folder_id,))]
        for row in rows:
            _end_run(conn, row, "failed", {"error": DETACHED}, ended)
    chats._answered(ws, ended)
    return [row["turn_id"] for row in rows]


def remote_turns(ws: str, user_id: str) -> list[dict]:
    """The turns in ``ws`` queued or running on another computer of
    ``user_id`` and of the co-authors its agents serve (``readable``),
    oldest first: ``{ws, turn_id, conversation_id, thread_id, bucket,
    agent, computer_id, user_id, title, computer, state, asking, there,
    stopping, queued_at, started_at}`` (Background tasks' rows,
    routers/jobs.py), ``agent`` the turn's own, ``computer_id`` its
    conversation's computer, ``user_id`` the account whose turn it is,
    ``title`` the bucket's, ``computer`` the name of the computer that holds
    it, else of the one picked for it (the row's ``computer``), ``asking``
    whether an ask of its agent waits for an answer, ``there`` whether on
    that computer alone (its message's ``waiting``; else it is in the
    message, ``asks``, answered in the chat)."""
    from ..computers import registry  # local: the registry reaches the workspaces

    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT r.turn_id, r.conversation_id, c.thread_id, r.bucket, COALESCE(NULLIF(r.agent, ''), c.agent), "
            "r.user_id, b.content, r.state, r.stop, r.queued_at, r.started_at, r.holder, r.computer, c.computer, "
            "(SELECT json_extract(m.value, '$.waiting') FROM json_each(c.messages) m "
            " WHERE json_extract(m.value, '$.role') = 'agent' AND json_extract(m.value, '$.turn_id') = r.turn_id), "
            "(SELECT json_array_length(m.value, '$.asks') FROM json_each(c.messages) m "
            " WHERE json_extract(m.value, '$.role') = 'agent' AND json_extract(m.value, '$.turn_id') = r.turn_id) "
            "FROM computer_chat_runs r JOIN computer_chats c ON c.id = r.conversation_id "
            "LEFT JOIN unified_blocks b ON b.id = r.bucket WHERE (r.user_id = ? OR c.owner = ?) "
            "AND r.state IN ('queued', 'running') ORDER BY r.queued_at, r.turn_id", (user_id, user_id)).fetchall()
    return [{"ws": ws, "turn_id": turn_id, "conversation_id": conversation_id, "thread_id": thread_id or "",
             "bucket": bucket, "agent": agent, "computer_id": computer_id, "user_id": person, "title": title or "",
             "computer": (registry.computer_name(holder) if holder else "") or known or "", "state": state,
             "asking": state == "running" and bool(waiting or asks), "there": state == "running" and bool(waiting),
             "stopping": bool(stop), "queued_at": queued_at, "started_at": started_at}
            for turn_id, conversation_id, thread_id, bucket, agent, person, title, state, stop, queued_at, started_at,
            holder, known, computer_id, waiting, asks in rows]

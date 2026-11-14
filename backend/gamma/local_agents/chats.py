"""The chat's computer side (docs/dev/local_agents.md "The chat's computer
side"): a page's conversations with the agent on this computer, kept in the
workspace's pages.db (``computer_chats``, db.COMPUTER_CHATS_SCHEMA) for its
one reader, the maker of the vault of the work folder the page is a file of;
on any server, also a page whose work folder a computer of the reader's
keeps with an agent there, its turns run on that computer (step 4d, "a turn
run on another computer" below).

A conversation is the page's active one for that account (``archived_at``
NULL) or an archived one (History). Its messages are the person's
(``{id, role: "user", text, selections: [{text, page, box}], at}``) and the
agent's (``{id, role: "agent", agent, label, turn_id, state, text, steps, asks,
changes, changed, built, error, fresh, model, effort, at, ended_at}``,
``asks`` what its agent asks the person and waits for, ``model`` and
``effort`` what the turn asked for, null for the agent's default; its
``text`` grows as the agent writes it), the agent's written by the runner as
its turn goes (gamma/local_agents/runner.py ``_ChatTurn``), relative paths
only. Every write takes the workspace's
write lock and sets ``version`` above every version of the page's
conversations of that account, and never below the clock
(``_next_version``), so a client that polls with the version it holds
learns of any change, an opened conversation's included.

These are not the AI chats (gamma/routers/chats.py): no member, share,
export, backup, mirror or read_chats reads them (ws_backup.PRIVATE_TABLES),
and they go with their page deleted for good (ops._purge).

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

import json
import re
import time

from ..blocks_store import new_block_id, write_lock
from ..db import connect_pages_db, page_now
from . import store

MAX_TEXT = 20_000        # characters of a message: the person's, the agent's reply
MAX_SELECTIONS = 20      # selections a message carries
MAX_QUOTE = 2000         # characters of a selection's text
TITLE_CHARS = 80
HISTORY_CAP = 200        # archived conversations listed
BUSY = ("queued", "running")
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

_COLUMNS = ("id", "page_id", "user_id", "agent", "title", "messages", "version", "created_at", "updated_at",
            "archived_at")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM computer_chats"


class ChatError(Exception):
    """A refusal: ``status`` and an English ``detail``."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def vault_of(ws: str, page_id: str, user_id: str) -> dict | None:
    """The vault here of the work folder the page is a live file of, made
    by ``user_id`` (the one with an agent first, when two keep it): the
    one gate of the side, its link; None for any other page or account."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    if not page_id:
        return None
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
    if not row or not row[0]:
        return None
    links = [link for link in folder_links.all_links()
             if link["workspace_id"] == ws and link["folder_id"] == row[0] and link["created_by"] == user_id
             and folder_links.work_folder_link(link)]
    agents = store.agents_of([link["id"] for link in links]) if links else {}
    return next((link for link in links if link["id"] in agents), links[0] if links else None)


def _conversation(row) -> dict:
    found = dict(zip(_COLUMNS, row))
    try:
        found["messages"] = json.loads(found["messages"] or "[]")
    except ValueError:
        found["messages"] = []
    return found


def shown(conversation: dict | None) -> dict | None:
    """What the routes answer of a conversation: ``{id, title, agent,
    messages, version}``."""
    if conversation is None:
        return None
    return {k: conversation[k] for k in ("id", "title", "agent", "messages", "version")}


def busy(conversation: dict | None) -> dict | None:
    """The agent's message of the conversation's turn that is queued or
    running, None for none."""
    for message in reversed((conversation or {}).get("messages") or []):
        if message.get("role") == "agent":
            return message if message.get("state") in BUSY else None
    return None


def active(conn, page_id: str, user_id: str) -> dict | None:
    """The page's active conversation of the account, None for none."""
    row = conn.execute(f"{_SELECT} WHERE page_id = ? AND user_id = ? AND archived_at IS NULL "
                       "ORDER BY updated_at DESC LIMIT 1", (page_id, user_id)).fetchone()
    return _conversation(row) if row else None


def get(conn, conversation_id: str) -> dict | None:
    row = conn.execute(f"{_SELECT} WHERE id = ?", (conversation_id,)).fetchone()
    return _conversation(row) if row else None


def _next_version(conn, page_id: str, user_id: str) -> int:
    """A version above every one of the page's conversations of the account
    and never below the clock in microseconds: deleting an archived
    conversation can take the highest version away, and the next write must
    still not repeat a version a client holds (a poll with it would read
    another conversation as the same). A write takes far longer than a
    microsecond, so the clock has passed any version handed out before."""
    row = conn.execute("SELECT MAX(version) FROM computer_chats WHERE page_id = ? AND user_id = ?",
                       (page_id, user_id)).fetchone()
    return max(int(row[0] or 0) + 1, time.time_ns() // 1000)


def _save(conn, conversation: dict, messages: list, **more) -> dict:
    """The conversation's ``messages`` (and ``more`` columns) written with
    its next version → it as written. Inside the caller's write lock."""
    version = _next_version(conn, conversation["page_id"], conversation["user_id"])
    now = page_now()
    sets = {"messages": json.dumps(messages, ensure_ascii=False), "version": version, "updated_at": now, **more}
    conn.execute(f"UPDATE computer_chats SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                 (*sets.values(), conversation["id"]))
    return {**conversation, **sets, "messages": messages}


def selections_of(raw) -> list[dict]:
    """The selections a message carries, as kept: ``{text, page, box}``,
    the text cut to ``MAX_QUOTE``, ``page`` a page number or None, ``box``
    four fractions of the page or None; at most ``MAX_SELECTIONS``, the
    malformed left out."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict) or not isinstance(item.get("text"), str) or not item["text"].strip():
            continue
        page = item.get("page")
        page = page if isinstance(page, int) and not isinstance(page, bool) and page >= 1 else None
        box = item.get("box")
        if not (isinstance(box, list) and len(box) == 4
                and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in box)):
            box = None
        out.append({"text": item["text"].strip()[:MAX_QUOTE], "page": page, "box": box})
        if len(out) >= MAX_SELECTIONS:
            break
    return out


def sources_of(ws: str, conn, page_id: str, selections: list[dict | None]) -> list[dict | None]:
    """The line each of ``selections`` (as ``selections_of`` keeps one,
    None for one that is not) maps to in the work folder's sources
    (``feedback.sources.source_for``: ``{file, line, end?}``), None where
    its page or box is not known or nothing answers. Reads the synctex the
    build left; runs no process."""
    from ..blocks_store import load_props
    from ..feedback import sources
    from ..logbuf import log

    row = conn.execute("SELECT properties FROM unified_blocks WHERE id = ?", (page_id,)).fetchone()
    props = load_props(row[0]) if row else {}
    out = []
    for chosen in selections:
        source = None
        if chosen and chosen.get("page") and chosen.get("box"):
            try:
                source = sources.source_for(ws, conn, page_id, props, {
                    "kind": "passage", "page": chosen["page"], "rects": [chosen["box"]]})
            except Exception as e:  # noqa: BLE001 — a selection without its line is searched for
                log.warning(f"[agents] the source of a selection on page {page_id}: {e}")
        out.append(source)
    return out


def title_of(text: str) -> str:
    """A conversation's title: its first message's first line, cut."""
    line = next((line.strip() for line in (text or "").splitlines() if line.strip()), "")
    return line if len(line) <= TITLE_CHARS else line[:TITLE_CHARS - 1] + "…"


def _turn_of(conversation: dict, message_id: str) -> dict | None:
    """The agent's message that answers the person's message
    ``message_id``, None when the conversation has none."""
    messages = conversation["messages"]
    for i, message in enumerate(messages):
        if message.get("id") == message_id and message.get("role") == "user":
            return next((m for m in messages[i + 1:] if m.get("role") == "agent"), None)
    return None


def begin(ws: str, *, page_id: str, user_id: str, link_id: str, agent: str, label: str, text: str,
          selections: list[dict], message_id: str = "", model: str = "", effort: str = "",
          remote: bool = False, computer: str = "") -> tuple[dict, dict, bool]:
    """A turn begins: the person's message and the agent's ``queued`` one
    (with the ``model`` and ``effort`` the turn asks for, None for the
    agent's default) written in the page's active conversation (one is
    started, titled by the message, when it has none) and the turn
    recorded on this computer
    (``store.add_turn``), or for one that runs on another computer
    (``remote``, the vault ``link_id`` none) here (``computer_chat_runs``,
    ``queued``, with its ``agent`` and the ``computer`` that keeps the
    folder), in one step under the write lock → ``(the
    conversation, the agent's message, whether the turn is new)``. A
    ``message_id`` the active conversation has already answers the turn it
    made (a retried POST). ChatError 409 while a turn of the conversation
    is queued or running."""
    now = page_now()
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conversation = active(conn, page_id, user_id)
        if conversation is not None and message_id:
            made = _turn_of(conversation, message_id)
            if made is not None:
                return conversation, made, False
        if busy(conversation) is not None:
            raise ChatError(409, f"{label} is still working on the last message: wait for it, or stop it.")
        if conversation is None:
            conversation = {"id": new_block_id(), "page_id": page_id, "user_id": user_id, "agent": agent,
                            "title": title_of(text), "messages": [], "version": 0, "created_at": now,
                            "updated_at": now, "archived_at": None}
            conn.execute("INSERT INTO computer_chats (id, page_id, user_id, agent, title, messages, version, "
                         "created_at, updated_at) VALUES (?, ?, ?, ?, ?, '[]', 0, ?, ?)",
                         (conversation["id"], page_id, user_id, agent, conversation["title"], now, now))
        turn_id = new_block_id()
        person = {"id": message_id or new_block_id(), "role": "user", "text": text, "selections": selections,
                  "at": now}
        reply = {"id": new_block_id(), "role": "agent", "agent": agent, "label": label, "turn_id": turn_id,
                 "state": "queued", "text": "", "steps": [], "asks": [], "changes": [], "changed": 0, "built": False,
                 "error": "", "fresh": False, "model": model or None, "effort": effort or None, "at": now,
                 "ended_at": None}
        conversation = _save(conn, conversation, [*conversation["messages"], person, reply])
        if remote:
            conn.execute("INSERT INTO computer_chat_runs (turn_id, conversation_id, page_id, user_id, agent, computer, "
                         "queued_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (turn_id, conversation["id"], page_id, user_id, agent, computer, now))
            return conversation, reply, True
        # Recorded before the commit: a server that stops between the two
        # finds the turn open at its start (runner.recover) and fails it.
        store.add_turn(turn_id, ws=ws, link_id=link_id, conversation_id=conversation["id"], page_id=page_id,
                       agent=agent)
    return conversation, reply, True


def write_reply(ws: str, conversation_id: str, turn_id: str, fields: dict) -> bool:
    """The agent's message of the turn ``turn_id`` takes ``fields`` →
    whether it was written (False: the conversation or the message is
    gone)."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conversation = get(conn, conversation_id)
        if conversation is None:
            return False
        messages = conversation["messages"]
        at = next((i for i, m in enumerate(messages) if m.get("role") == "agent" and m.get("turn_id") == turn_id),
                  None)
        if at is None:
            return False
        messages = [*messages[:at], {**messages[at], **fields}, *messages[at + 1:]]
        _save(conn, conversation, messages)
    return True


def reply_of(ws: str, conversation_id: str, turn_id: str) -> tuple[dict | None, dict | None]:
    """``(the conversation, the agent's message of the turn)``, None for
    either that is gone."""
    with connect_pages_db(ws) as conn:
        conversation = get(conn, conversation_id)
    if conversation is None:
        return None, None
    reply = next((m for m in conversation["messages"] if m.get("role") == "agent" and m.get("turn_id") == turn_id),
                 None)
    return conversation, reply


def _archive(conn, conversation: dict | None) -> None:
    if conversation is not None:
        _save(conn, conversation, conversation["messages"], archived_at=page_now())


def new(ws: str, page_id: str, user_id: str, label: str) -> None:
    """New chat: the page's active conversation of the account archived.
    ChatError 409 while its turn is queued or running."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conversation = active(conn, page_id, user_id)
        if busy(conversation) is not None:
            raise ChatError(409, f"{label} is still working: stop it first.")
        _archive(conn, conversation)


def history(ws: str, page_id: str, user_id: str) -> list[dict]:
    """The page's archived conversations of the account, newest first:
    ``{id, title, agent, updated_at, turns}``."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"{_SELECT} WHERE page_id = ? AND user_id = ? AND archived_at IS NOT NULL "
                            "ORDER BY updated_at DESC LIMIT ?", (page_id, user_id, HISTORY_CAP)).fetchall()
    out = []
    for row in rows:
        found = _conversation(row)
        out.append({"id": found["id"], "title": found["title"], "agent": found["agent"],
                    "updated_at": found["updated_at"],
                    "turns": sum(1 for m in found["messages"] if m.get("role") == "agent")})
    return out


def open_one(ws: str, conversation: dict, label: str) -> dict:
    """The archived ``conversation`` becomes its page's active one, the
    active one archived → it as written. ChatError 409 while the active
    one's turn is queued or running, 404 for one that is gone or not
    archived."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = get(conn, conversation["id"])
        if found is None or found["archived_at"] is None:
            raise ChatError(404, "No earlier conversation has that id.")
        current = active(conn, found["page_id"], found["user_id"])
        if busy(current) is not None:
            raise ChatError(409, f"{label} is still working: stop it first.")
        _archive(conn, current)
        return _save(conn, found, found["messages"], archived_at=None)


def delete(ws: str, conversation: dict, *, here: bool = True) -> None:
    """An archived conversation deleted, its turns run on another computer
    with it, and with ``here`` (its agent runs on this computer) the
    sessions this computer kept of it. ChatError 409 for the active one."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = get(conn, conversation["id"])
        if found is None:
            raise ChatError(404, "No earlier conversation has that id.")
        if found["archived_at"] is None:
            raise ChatError(409, "This is the page's conversation now: start a new chat first.")
        conn.execute("DELETE FROM computer_chats WHERE id = ?", (found["id"],))
        conn.execute("DELETE FROM computer_chat_runs WHERE conversation_id = ?", (found["id"],))
    if here:
        store.forget_conversation(conversation["id"])


# --- a turn run on another computer (step 4d) ----------------------------------------------------
# A page of a work folder of this server kept on a computer of its maker's
# with an agent there (gamma/computers/registry.py ``agent_of``): the
# conversation stays here, that computer takes its turns over its channel
# (gamma/routers/computers.py) and writes the agent's message back. Each
# turn is a ``computer_chat_runs`` row: ``queued`` until a run of a
# computer of the account's claims it (``holder`` its token, ``run`` the
# id it minted), ``running``, then its end; ``seq`` the last write taken
# from that run, so a retried or late write never goes back; ``stop`` the
# person's Stop of a running turn, which the computer is told (a second
# Stop ends it here). Only the run that holds a turn writes it, and only
# while it runs. A turn is its ``agent``'s: only a computer keeping the
# folder with that agent is given it, and one queued when no computer of
# the account keeps its folder with that agent any more fails
# (``fail_queued``, from the announce and a token's end), so none runs
# later unasked.

ENDED = ("done", "failed", "stopped")
MAX_WAITING = 20          # turns one listing gives a computer at most
EARLIER = 6               # earlier messages a turn's prompt takes (runner._ChatTurn._work)
NO_TURN = "No turn has that id."

_RUN_COLUMNS = ("turn_id", "conversation_id", "page_id", "user_id", "agent", "computer", "holder", "run", "seq",
                "stop", "state", "queued_at", "started_at")


def remote_of(ws: str, page_id: str, user_id: str) -> dict | None:
    """The computer of ``user_id``'s that keeps the work folder the page is
    a live file of with an agent there (``registry.agent_of``, with the
    work folder's ``folder_id``): the side's gate for a page whose agent
    runs on another computer; None for any other page or account."""
    from ..computers import registry  # local: registry reaches this package where an agent is named

    if not page_id:
        return None
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
    if not row or not row[0]:
        return None
    found = registry.agent_of(ws, row[0], user_id)
    return {**found, "folder_id": row[0]} if found is not None else None


def run_of(conn, turn_id: str) -> dict | None:
    """The turn's ``computer_chat_runs`` row, None for a turn that runs on
    no other computer."""
    row = conn.execute(f"SELECT {', '.join(_RUN_COLUMNS)} FROM computer_chat_runs WHERE turn_id = ?",
                       (turn_id,)).fetchone()
    return dict(zip(_RUN_COLUMNS, row)) if row else None


def _message_at(conversation: dict | None, turn_id: str) -> int | None:
    """Where the turn's agent message is in the conversation's messages."""
    messages = (conversation or {}).get("messages") or []
    return next((i for i, m in enumerate(messages) if m.get("role") == "agent" and m.get("turn_id") == turn_id),
                None)


def _end_run(conn, row: dict, state: str, fields: dict | None = None) -> None:
    """The turn's row and its message (when it still reads queued or
    running) end ``state``, ``fields`` written with it. Inside the caller's
    write lock."""
    conn.execute("UPDATE computer_chat_runs SET state = ? WHERE turn_id = ?", (state, row["turn_id"]))
    conversation = get(conn, row["conversation_id"])
    at = _message_at(conversation, row["turn_id"])
    if at is not None and conversation["messages"][at].get("state") in BUSY:
        messages = conversation["messages"]
        ended = {**messages[at], **(fields or {}), "state": state, "asks": [], "ended_at": page_now()}
        _save(conn, conversation, [*messages[:at], ended, *messages[at + 1:]])


def waiting_turns(conn, user_id: str, folders: dict[str, str]) -> list[dict]:
    """The turns of ``user_id``'s that wait for a computer in pages of the
    work folders ``folders`` (``{folder id: its agent there}``), each the
    turn of that folder's agent there, oldest first, at most
    ``MAX_WAITING``: ``[{id, conversation_id, page_id, folder_id, at}]``."""
    rows = conn.execute(
        "SELECT r.turn_id, r.conversation_id, r.page_id, b.work_folder, r.queued_at FROM computer_chat_runs r "
        "JOIN unified_blocks b ON b.id = r.page_id AND b.parent_id = 'root' "
        "JOIN computer_chats c ON c.id = r.conversation_id "
        "JOIN json_each(?) f ON f.key = b.work_folder AND f.value = r.agent "
        "WHERE r.state = 'queued' AND r.user_id = ? "
        "ORDER BY r.queued_at, r.turn_id LIMIT ?", (json.dumps(folders), user_id, MAX_WAITING)).fetchall()
    return [{"id": turn_id, "conversation_id": conversation_id, "page_id": page_id, "folder_id": w, "at": at}
            for turn_id, conversation_id, page_id, w, at in rows]


def stops_of(conn, holder: str) -> list[dict]:
    """The runs of the computer ``holder`` asked to stop: ``[{id, run}]``,
    the turns they hold running with their stop set (listed until the run
    writes its end)."""
    rows = conn.execute("SELECT turn_id, run FROM computer_chat_runs WHERE holder = ? AND state = 'running' "
                        "AND stop != 0 ORDER BY turn_id", (holder,)).fetchall()
    return [{"id": turn_id, "run": run} for turn_id, run in rows]


def claim(ws: str, turn_id: str, *, user_id: str, holder: str, run: str, agent: str, computer: str = "") -> dict:
    """The run ``run`` of the computer ``holder`` (a computer of
    ``user_id``'s named ``computer``, whose work folder agent is
    ``agent``) takes the queued
    turn: the row and its message ``running``, in one step under the write
    lock → ``{turn: {id, conversation_id, page_id, text, selections, model,
    effort}, history, page: {path, title}, seen}``: the person's message,
    the conversation's earlier messages as the turn's prompt takes them
    (``[{role: owner | agent, text}]``, the last ``EARLIER``), the page's
    path in the work folder and its title, and this server's clock. The
    same run taking it again (a retry) is answered again. A turn whose
    page is no longer a file of the work folder fails. ChatError 404 for
    no turn of ``user_id``'s, 409 when another run holds it, it is no
    longer queued, or it is another agent's (left queued, for a computer
    keeping the folder with that one)."""
    from ..feedback import entries
    from ..feedback.entries import FeedbackError

    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None or row["user_id"] != user_id:
            raise ChatError(404, NO_TURN)
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, turn_id)
        if at is None:
            if row["state"] in BUSY:
                conn.execute("UPDATE computer_chat_runs SET state = 'stopped' WHERE turn_id = ?", (turn_id,))
                conn.commit()
            raise ChatError(404, NO_TURN)
        messages = conversation["messages"]
        reply = messages[at]
        if reply.get("state") not in BUSY and row["state"] in BUSY:
            # its message ended otherwise (a row restored from a backup): never back to running
            conn.execute("UPDATE computer_chat_runs SET state = ? WHERE turn_id = ?",
                         (reply.get("state") if reply.get("state") in ENDED else "stopped", turn_id))
            conn.commit()
            row = run_of(conn, turn_id)
        again = (row["holder"], row["run"]) == (holder, run) and row["state"] == "running"
        if not again and row["state"] != "queued":
            raise ChatError(409, "Another run holds this turn." if row["state"] == "running"
                            else f"The turn is {row['state']} now.")
        if (row["agent"] or reply.get("agent")) != agent:
            raise ChatError(409, f"This turn is for {reply.get('label') or 'another agent'}.")
        try:
            scope = entries.resolve_scope(conn, row["page_id"])
        except FeedbackError:
            refusal = "This page is no longer a file of the work folder."
            _end_run(conn, row, "failed", {"error": refusal})
            conn.commit()
            raise ChatError(409, refusal) from None
        if not again:
            now = page_now()
            conn.execute("UPDATE computer_chat_runs SET holder = ?, run = ?, seq = 0, state = 'running', "
                         "started_at = ?, computer = CASE WHEN ? != '' THEN ? ELSE computer END WHERE turn_id = ?",
                         (holder, run, now, computer, computer, turn_id))
            reply = {**reply, "state": "running"}
            _save(conn, conversation, [*messages[:at], reply, *messages[at + 1:]])
        person = next((m for m in reversed(messages[:at]) if m.get("role") == "user"), None) or {}
        earlier = [{"role": "owner" if m.get("role") == "user" else "agent", "text": m.get("text") or ""}
                   for m in messages[:at] if m is not person and (m.get("text") or "").strip()][-EARLIER:]
        title = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (row["page_id"],)).fetchone()
    return {"turn": {"id": turn_id, "conversation_id": row["conversation_id"], "page_id": row["page_id"],
                     "text": person.get("text") or "", "selections": person.get("selections") or [],
                     "model": reply.get("model") or "", "effort": reply.get("effort") or ""},
            "history": earlier, "page": {"path": scope.name, "title": (title[0] if title else "") or ""},
            "seen": page_now()}


def write_run(ws: str, turn_id: str, *, user_id: str, holder: str, run: str, seq: int, fields: dict) -> bool:
    """What the run ``run`` of the computer ``holder`` writes of the turn
    it holds: ``fields`` (as the channel's route keeps them) into its
    message, when ``seq`` is above the last one taken from it (else
    nothing: a retried or late write) → whether it was written. A ``state``
    of ``ENDED`` ends the row too, the message's ``ended_at`` this
    server's clock. ChatError 404 for no turn of ``user_id``'s this
    computer holds or held, 409 when another run holds it, it is no longer
    running (stopped here) or its message is gone (the run stops)."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None or row["user_id"] != user_id or row["holder"] != holder:
            raise ChatError(404, NO_TURN)
        if row["run"] != run:
            raise ChatError(409, "Another run holds this turn.")
        if seq <= row["seq"]:
            return False
        if row["state"] != "running":
            raise ChatError(409, f"The turn is {row['state']} now.")
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, turn_id)
        if at is None or conversation["messages"][at].get("state") not in BUSY:
            conn.execute("UPDATE computer_chat_runs SET state = 'stopped' WHERE turn_id = ?", (turn_id,))
            conn.commit()
            raise ChatError(409, "The conversation is gone." if at is None else "The turn is stopped now.")
        state = fields.get("state")
        if state in ENDED:
            fields = {**fields, "ended_at": page_now(), "asks": []}
        messages = conversation["messages"]
        _save(conn, conversation, [*messages[:at], {**messages[at], **fields}, *messages[at + 1:]])
        conn.execute("UPDATE computer_chat_runs SET seq = ?, state = ? WHERE turn_id = ?",
                     (seq, state if state in ENDED else "running", turn_id))
    return True


def stop_run(ws: str, turn_id: str, *, online) -> bool:
    """The person's Stop of a turn that runs on another computer: a running
    one whose computer is online (``online(holder)``) has its stop set (the
    computer is told, its run ends it ``stopped``); a queued one, one
    whose computer is away, and a running one Stopped again (its stop set
    already: its computer may no longer run it) end ``stopped`` now (a run
    that comes back is refused at its next write) → whether there was one
    to stop."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        row = run_of(conn, turn_id)
        if row is None:
            return False
        if row["state"] == "running" and online(row["holder"]) and not row["stop"]:
            conn.execute("UPDATE computer_chat_runs SET stop = 1 WHERE turn_id = ?", (row["turn_id"],))
            return True
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, row["turn_id"])
        if row["state"] not in BUSY and (at is None or conversation["messages"][at].get("state") not in BUSY):
            return False
        _end_run(conn, row, "stopped")
    return True


def fail_held(ws: str, holder: str, words) -> list[str]:
    """The computer ``holder`` went (its token revoked, replaced or
    expired), so none of its runs can write again: each turn it holds
    running fails, ``words(agent)`` its message's error → the turns
    failed."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = [dict(zip(_RUN_COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join(_RUN_COLUMNS)} FROM computer_chat_runs WHERE holder = ? AND state = 'running'",
            (holder,)).fetchall()]
        for row in rows:
            conversation = get(conn, row["conversation_id"])
            at = _message_at(conversation, row["turn_id"])
            _end_run(conn, row, "failed",
                     {"error": words(conversation["messages"][at].get("agent") if at is not None else "")})
    return [row["turn_id"] for row in rows]


def fail_queued(ws: str, user_id: str, kept: dict[str, set], words) -> list[str]:
    """The turns of ``user_id``'s queued in pages of the work folders of
    ``kept`` (``{folder id: the agents the account's computers keep it with
    now}``) whose agent is none of them fail, ``words(agent, agents)``
    their message's error: no computer would take them, and one turned on
    again later must not run them unasked → the turns failed."""
    if not kept:
        return []
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        rows = [(dict(zip(_RUN_COLUMNS, r[:-1])), r[-1]) for r in conn.execute(
            f"SELECT {', '.join('r.' + c for c in _RUN_COLUMNS)}, b.work_folder FROM computer_chat_runs r "
            "JOIN unified_blocks b ON b.id = r.page_id WHERE r.state = 'queued' AND r.user_id = ? "
            "AND b.work_folder IN (SELECT value FROM json_each(?))", (user_id, json.dumps(sorted(kept)))).fetchall()]
        failed = []
        for row, w in rows:
            if row["agent"] not in kept[w]:
                _end_run(conn, row, "failed", {"error": words(row["agent"], kept[w])})
                failed.append(row["turn_id"])
    return failed


def remote_turns(ws: str, user_id: str) -> list[dict]:
    """The turns of ``user_id``'s in ``ws`` queued or running on another
    computer, oldest first: ``{ws, turn_id, conversation_id, page_id,
    agent, title, computer, state, stopping, queued_at, started_at}``
    (Background tasks' rows, routers/jobs.py), ``agent`` the turn's own,
    ``title`` the page's, ``computer`` the name of the computer that holds
    it, or, while it waits, of the one that keeps its work folder for the
    account, else of the one last known to (the row's ``computer``)."""
    from ..computers import registry  # local: the registry reaches the workspaces

    with connect_pages_db(ws) as conn:
        rows = conn.execute(
            "SELECT r.turn_id, r.conversation_id, r.page_id, COALESCE(NULLIF(r.agent, ''), c.agent), b.content, "
            "r.state, r.stop, r.queued_at, r.started_at, r.holder, b.work_folder, r.computer "
            "FROM computer_chat_runs r JOIN computer_chats c ON c.id = r.conversation_id "
            "LEFT JOIN unified_blocks b ON b.id = r.page_id WHERE r.user_id = ? AND r.state IN ('queued', 'running') "
            "ORDER BY r.queued_at, r.turn_id", (user_id,)).fetchall()

    def computer(holder: str, folder: str, known: str) -> str:
        if holder:
            return registry.computer_name(holder) or known
        found = registry.agent_of(ws, folder, user_id) if folder else None
        return found["computer"] if found else known

    return [{"ws": ws, "turn_id": turn_id, "conversation_id": conversation_id, "page_id": page_id, "agent": agent,
             "title": title or "", "computer": computer(holder or "", folder or "", known or ""), "state": state,
             "stopping": bool(stop), "queued_at": queued_at, "started_at": started_at}
            for turn_id, conversation_id, page_id, agent, title, state, stop, queued_at, started_at, holder, folder,
            known in rows]

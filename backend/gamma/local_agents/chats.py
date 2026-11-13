"""The chat's computer side (docs/dev/local_agents.md "The chat's computer
side"): a page's conversations with the agent on this computer, kept in the
workspace's pages.db (``computer_chats``, db.COMPUTER_CHATS_SCHEMA) for its
one reader, the maker of the vault of the work folder the page is a file of.

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
          selections: list[dict], message_id: str = "", model: str = "",
          effort: str = "") -> tuple[dict, dict, bool]:
    """A turn begins: the person's message and the agent's ``queued`` one
    (with the ``model`` and ``effort`` the turn asks for, None for the
    agent's default) written in the page's active conversation (one is
    started, titled by the message, when it has none) and the turn
    recorded on this computer
    (``store.add_turn``), in one step under the write lock → ``(the
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


def delete(ws: str, conversation: dict) -> None:
    """An archived conversation deleted. ChatError 409 for the active
    one."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = get(conn, conversation["id"])
        if found is None:
            raise ChatError(404, "No earlier conversation has that id.")
        if found["archived_at"] is None:
            raise ChatError(409, "This is the page's conversation now: start a new chat first.")
        conn.execute("DELETE FROM computer_chats WHERE id = ?", (found["id"],))
    store.forget_conversation(conversation["id"])

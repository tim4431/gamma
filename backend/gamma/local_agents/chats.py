"""The chat's computer side (docs/dev/local_agents.md "The chat's computer
side"): the conversations of a chat with a computer's agent, kept in the
workspace's pages.db (``computer_chats``, db.COMPUTER_CHATS_SCHEMA) for the
account that talks to the agent, and read by the agent's ``owner`` too
where that is another account (a co-author's, ``readable``; decision 12 of
docs/research/agents-as-participants-plan.md). A conversation is about a
bucket, as an AI chat is (a page's id, a folder's, or ``home``, the
library), and its agent works in one directory (``place_of``): the vault's
of the work folder the bucket is in, else a directory this computer offers
its agents (``store.dirs``), else an empty one of its own (``own_dir``). On
any server, a conversation with the agent of another computer of the
reader's (``computer``, picked among ``registry.seats``) has its turns run
on that computer (step 4d, "a turn run on another computer" below).

A conversation is the bucket's active one for that account (``archived_at``
NULL) or an archived one (History; a co-author's both are in the History
of its agent's owner too, ``lent_history``), or a comment thread's (``thread_id``,
gamma/local_agents/threads.py: its bucket the thread's page, never the
bucket's active one nor in History), and names where its agent works
(``dir``) and the other computer it runs on (``computer``, "" for this
server's own agent), chosen when it starts: a turn in another directory or
on another computer starts a new conversation (``begin``), as another agent
does. Its messages are the
person's (``{id, role: "user", text, selections: [{text, page, box}],
at}``) and the agent's (``{id, role: "agent", agent, label, turn_id, state,
text, steps, asks, changes, changed, built, error, fresh, model, effort, at,
ended_at, waiting?}``, ``asks`` what its agent asks the person and waits
for (a turn run on another computer's where that computer relays its asks,
answered here through its run, ``answer_run``), ``waiting`` such a turn's
ask that waits for an answer on that computer alone (false at its end),
``model`` and
``effort`` what the turn asked for, null for the agent's default; its
``text`` grows as the agent writes it), the agent's written by the runner as
its turn goes (gamma/local_agents/runner.py ``_ChatTurn``), relative paths
only. Every write takes the workspace's write lock and sets ``version``
above every version of the bucket's conversations of that account, and
never below the clock (``_next_version``), so a client that polls with the
version it holds learns of any change, an opened conversation's included.

These are not the AI chats (gamma/routers/chats.py): no member, share,
export, backup, mirror or read_chats reads them (ws_backup.PRIVATE_TABLES),
they go with their page deleted for good (ops._purge), and a deleted
folder's are filed into the library's (``file_into_home``).

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

import json
import re
import shutil
import time
from pathlib import Path

from .. import config
from ..blocks_store import load_props, new_block_id, write_lock
from ..db import connect_pages_db, page_now
from ..logbuf import log
from . import store

MAX_TEXT = 20_000        # characters of a message: the person's, the agent's reply
MAX_SELECTIONS = 20      # selections a message carries
MAX_QUOTE = 2000         # characters of a selection's text
TITLE_CHARS = 80
HISTORY_CAP = 200        # archived conversations listed
BUSY = ("queued", "running")
MESSAGE_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
HOME = "home"            # the library's bucket, as the AI chats name it

_COLUMNS = ("id", "bucket", "dir", "user_id", "agent", "title", "messages", "version", "created_at", "updated_at",
            "archived_at", "thread_id", "owner", "computer")
_SELECT = f"SELECT {', '.join(_COLUMNS)} FROM computer_chats"


class ChatError(Exception):
    """A refusal: ``status`` and an English ``detail``."""

    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status, self.detail = status, detail


def about(conn, bucket: str) -> dict | None:
    """What a conversation of ``bucket`` is about, None for no such bucket:
    ``{kind: "library"}``; ``{kind: "folder", id, name, w}``; ``{kind:
    "page", id, title, format, w}``, ``format`` its document's (``note``
    for a page without one); ``w`` the work folder it is in ("" for none),
    with ``work_folder`` its name and ``path`` the bucket's path in it (a
    directory's, a file's; "" for the work folder itself) when it is in
    one. A page in Recently deleted is none."""
    from ..annotations import entries
    from ..annotations.entries import AnnotationError
    from ..work_folders.marks import Tree

    if bucket == HOME:
        return {"kind": "library", "w": ""}
    if not bucket:
        return None
    tree = Tree.read(conn)
    if bucket in tree:
        found = {"kind": "folder", "id": bucket, "name": tree.names.get(bucket, ""), "w": tree.work_of(bucket)}
    else:
        row = conn.execute("SELECT content, properties FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (bucket,)).fetchone()
        if row is None:
            return None
        props = load_props(row[1])
        doc = props.get("doc") if isinstance(props.get("doc"), dict) else {}
        w = props.get("work_folder") if isinstance(props.get("work_folder"), str) else ""
        found = {"kind": "page", "id": bucket, "title": row[0] or "", "format": doc.get("format") or "note",
                 "w": w if w in tree else ""}
    if found["w"]:
        try:
            scope = entries.work_scope(conn, bucket, tree)
        except AnnotationError:
            found["w"] = ""
        else:
            found.update(work_folder=tree.names.get(found["w"], ""),
                         path="" if scope.kind == "work_folder" else scope.name)
    return found


def own_dir(ws: str, conversation_id: str) -> Path:
    """The empty directory of its own a conversation's agent works in when
    its bucket is in no work folder kept here and it names no offered
    directory: below the data directory, made at its first turn (the
    runner) and removed with the conversation (``delete``,
    ``sweep_dirs``)."""
    return config.AGENTS_DIR / ws / conversation_id


def place_of(ws: str, bucket: str, user_id: str, dir: str = "", conversation_id: str = "") -> dict | None:
    """Where ``user_id``'s agent works for a conversation of ``bucket`` that
    names ``dir`` (decision 14 of docs/research/agents-as-participants-plan.md),
    the one gate of the side: ``{kind, dir, path, name, link, about}``,
    ``about`` what the bucket is (``about``), ``dir`` what the conversation
    keeps of the place.

    - ``vault``: the bucket is in a work folder a vault here keeps (a file
      of it, a folder in it): that vault's directory, ``dir`` the work
      folder's id, ``link`` the vault (the one the account made first,
      when two keep it), ``name`` the work folder's;
    - ``offered``: else ``dir`` names a directory this computer offers its
      agents (``store.dirs``): its ``path`` and ``name``;
    - ``own``: else an empty directory of its own (``own_dir`` of
      ``conversation_id``; ``path`` "" before the conversation is made),
      ``dir`` "".

    The account must have an agent on here (``store.agent_for``), or have
    made the vault (it reads its conversations with none on); a co-author
    an agent here serves in ``ws`` (``store.lent_to``) has the ``vault``
    place alone, never an offered directory or one of its own (decision 13);
    None for any other account, and for no such bucket."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    if not user_id:
        return None
    with connect_pages_db(ws) as conn:
        found = about(conn, bucket)
    if found is None:
        return None
    mine = store.agent_for(user_id) is not None
    if found["w"]:
        links = [link for link in folder_links.all_links() if link["workspace_id"] == ws
                 and link["folder_id"] == found["w"] and folder_links.work_folder_link(link)]
        links.sort(key=lambda link: link["created_by"] != user_id)
        if links and (mine or links[0]["created_by"] == user_id or store.lent_to(ws, user_id)):
            return {"kind": "vault", "dir": found["w"], "path": links[0]["path"],
                    "name": found["work_folder"] or folder_links.dest_of(links[0]).name, "link": links[0],
                    "about": found}
    if not mine:
        return None
    offered = next((entry for entry in store.dirs() if entry["id"] == dir), None) if dir else None
    if offered is not None:
        return {"kind": "offered", "dir": offered["id"], "path": offered["path"], "name": offered["name"],
                "link": None, "about": found}
    return {"kind": "own", "dir": "", "path": str(own_dir(ws, conversation_id)) if conversation_id else "",
            "name": "", "link": None, "about": found}


def _conversation(row) -> dict:
    found = dict(zip(_COLUMNS, row))
    try:
        found["messages"] = json.loads(found["messages"] or "[]")
    except ValueError:
        found["messages"] = []
    return found


def readable(conversation: dict | None, user_id: str) -> bool:
    """Whether the account ``user_id`` reads the conversation: the account
    that talks in it, or the owner of its agent (decision 12)."""
    return conversation is not None and bool(user_id) and user_id in (conversation["user_id"],
                                                                        conversation.get("owner") or "")


def lent(conversation: dict | None) -> bool:
    """Whether the conversation is a co-author's: its agent another
    account's."""
    return conversation is not None and (conversation.get("owner") or conversation["user_id"]) != conversation[
        "user_id"]


def agent_id_of(conversation: dict) -> str:
    """The id of the agent the conversation talks to (gamma/agents.py
    ``computer:<computer>:<name>``): its other computer's, else this
    computer's (``agents.here_id``)."""
    from .. import agents  # local: agents reaches the computers, which reach this package

    return agents.computer_agent(conversation.get("computer") or agents.here_id(), conversation["agent"]).id


def shown(conversation: dict | None) -> dict | None:
    """What the routes answer of a conversation: ``{id, title, agent,
    agent_id, thread_id, messages, version}``, ``agent_id`` its agent's id
    (``agent_id_of``), ``thread_id`` "" for a chat's."""
    if conversation is None:
        return None
    return {**{k: conversation[k] for k in ("id", "title", "agent", "thread_id", "messages", "version")},
            "agent_id": agent_id_of(conversation)}


def busy(conversation: dict | None) -> dict | None:
    """The agent's message of the conversation's turn that is queued or
    running, None for none."""
    for message in reversed((conversation or {}).get("messages") or []):
        if message.get("role") == "agent":
            return message if message.get("state") in BUSY else None
    return None


def active(conn, bucket: str, user_id: str) -> dict | None:
    """The bucket's active conversation of the account, None for none (a
    thread's is none: ``of_thread``)."""
    row = conn.execute(f"{_SELECT} WHERE bucket = ? AND user_id = ? AND archived_at IS NULL AND thread_id = '' "
                       "ORDER BY updated_at DESC LIMIT 1", (bucket, user_id)).fetchone()
    return _conversation(row) if row else None


def lent_one(conn, conversation_id: str, owner: str) -> dict | None:
    """The chat conversation ``conversation_id`` of a co-author the agent of
    ``owner`` serves (``lent_history``), None for any other (one of
    ``owner``'s own, a thread's, none)."""
    found = get(conn, conversation_id)
    if found is None or found["thread_id"] or not lent(found) or found["owner"] != owner:
        return None
    return found


def of_thread(conn, thread_id: str, user_id: str, *, owner: bool = False) -> dict | None:
    """The account's conversation of the comment thread ``thread_id``, None
    for none (one of another agent, or of another directory, was archived
    when the next began); with ``owner``, where the account has none, the
    newest of a co-author's there with the account's agent (``readable``)."""
    row = conn.execute(f"{_SELECT} WHERE thread_id = ? AND user_id = ? AND archived_at IS NULL "
                       "ORDER BY updated_at DESC LIMIT 1", (thread_id, user_id)).fetchone()
    if row is None and owner:
        row = conn.execute(f"{_SELECT} WHERE thread_id = ? AND owner = ? AND archived_at IS NULL "
                           "ORDER BY updated_at DESC LIMIT 1", (thread_id, user_id)).fetchone()
    return _conversation(row) if row else None


def get(conn, conversation_id: str) -> dict | None:
    row = conn.execute(f"{_SELECT} WHERE id = ?", (conversation_id,)).fetchone()
    return _conversation(row) if row else None


def _next_version(conn, bucket: str, user_id: str) -> int:
    """A version above every one of the bucket's conversations of the
    account and never below the clock in microseconds: deleting an archived
    conversation can take the highest version away, and the next write must
    still not repeat a version a client holds (a poll with it would read
    another conversation as the same). A write takes far longer than a
    microsecond, so the clock has passed any version handed out before."""
    row = conn.execute("SELECT MAX(version) FROM computer_chats WHERE bucket = ? AND user_id = ?",
                       (bucket, user_id)).fetchone()
    return max(int(row[0] or 0) + 1, time.time_ns() // 1000)


def _save(conn, conversation: dict, messages: list, **more) -> dict:
    """The conversation's ``messages`` (and ``more`` columns) written with
    its next version → it as written. Inside the caller's write lock."""
    version = _next_version(conn, conversation["bucket"], conversation["user_id"])
    now = page_now()
    sets = {"messages": json.dumps(messages, ensure_ascii=False), "version": version, "updated_at": now, **more}
    conn.execute(f"UPDATE computer_chats SET {', '.join(f'{k} = ?' for k in sets)} WHERE id = ?",
                 (*sets.values(), conversation["id"]))
    return {**conversation, **sets, "messages": messages}


def _answered(ws: str, ended) -> None:
    """The turns ``ended`` (``(conversation id, turn id)``) ended: a
    thread's is answered in its thread (``threads.settle``), after the
    write that ended it is committed. Never raises."""
    from . import threads  # local: threads reads the conversations through this module

    for conversation_id, turn_id in ended:
        threads.settle(ws, conversation_id, turn_id)


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
    (``annotations.sources.source_for``: ``{file, line, end?}``), None where
    its page or box is not known or nothing answers. Reads the synctex the
    build left; runs no process."""
    from ..blocks_store import load_props
    from ..annotations import sources
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


def begin(ws: str, *, bucket: str, dir: str, user_id: str, link_id: str, agent: str, label: str, text: str,
          selections: list[dict], message_id: str = "", model: str = "", effort: str = "",
          remote: bool = False, computer: str = "", computer_id: str = "", thread_id: str = "",
          comment_id: str = "", agent_id: str = "", owner: str = "", resume: str = "") -> tuple[dict, dict, bool]:
    """A turn begins: the person's message and the agent's ``queued`` one
    (with the ``model`` and ``effort`` the turn asks for, None for the
    agent's default) written in the bucket's active conversation, or with
    ``thread_id`` in the account's conversation of that comment thread
    (``of_thread``; the person's message then names the ``comment_id`` it
    is, the agent's the ``agent_id`` that answers in the thread), and the
    turn recorded on this computer (``store.add_turn``, ``link_id`` the
    vault it works in, "" for none), or for one that runs on another
    computer (``remote``, that computer's id ``computer_id`` and its name
    ``computer``) here (``computer_chat_runs``, ``queued``, with its
    ``agent``), in one step under the write lock → ``(the conversation, the
    agent's message, whether the turn is new)``. A conversation is started,
    titled by the message, when the bucket (or the thread) has none, or
    when its own works elsewhere than ``dir`` (``place_of``'s, or a far
    seat's) or with another ``agent`` on another computer, or another
    ``owner``'s (the account whose agent it is, the turn's own but for a
    co-author's): that one is archived, so a conversation never changes its
    directory, its agent, its computer or whose agent it is. A thread's
    conversation started so takes the session of the
    account's conversation ``resume`` (the one whose turn opened the
    thread, ``threads.opener_of``: ``store.conversation_of``'s row copied)
    while that one waits on no turn and has the same directory, agent and
    owner, so its agent resumes where it asked. A ``message_id`` the
    conversation has already answers the turn it made (a retried POST).
    ChatError 409 while a turn of the conversation is queued or running."""
    now, owner = page_now(), owner or user_id
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conversation = of_thread(conn, thread_id, user_id) if thread_id else active(conn, bucket, user_id)
        if conversation is not None and message_id:
            made = _turn_of(conversation, message_id)
            if made is not None:
                return conversation, made, False
        working = busy(conversation)
        if working is not None:
            raise ChatError(409, f"{working.get('label') or label} is still working on the last message: "
                                 "wait for it, or stop it.")
        if conversation is not None and (conversation["dir"] != dir or conversation["agent"] != agent
                                         or conversation["computer"] != computer_id
                                         or (conversation["owner"] or conversation["user_id"]) != owner):
            _archive(conn, conversation)
            conversation = None
        if conversation is None:
            conversation = {"id": new_block_id(), "bucket": bucket, "dir": dir, "user_id": user_id, "agent": agent,
                            "title": title_of(text), "messages": [], "version": 0, "created_at": now,
                            "updated_at": now, "archived_at": None, "thread_id": thread_id, "owner": owner,
                            "computer": computer_id}
            conn.execute("INSERT INTO computer_chats (id, bucket, dir, user_id, agent, title, messages, version, "
                         "created_at, updated_at, thread_id, owner, computer) "
                         "VALUES (?, ?, ?, ?, ?, ?, '[]', 0, ?, ?, ?, ?, ?)",
                         (conversation["id"], bucket, dir, user_id, agent, conversation["title"], now, now, thread_id,
                          owner, computer_id))
            opener = get(conn, resume) if resume and thread_id else None
            session = store.conversation_of(resume) if opener is not None else None
            if (session is not None and busy(opener) is None and opener["user_id"] == user_id
                    and (opener["dir"], opener["agent"], opener["owner"] or opener["user_id"], opener["computer"])
                    == (dir, agent, owner, computer_id)):
                store.ran_conversation(conversation["id"], link_id=session["link_id"], agent=session["agent"],
                                       session=session["session"], cwd=session["cwd"])
        turn_id = new_block_id()
        person = {"id": message_id or new_block_id(), "role": "user", "text": text, "selections": selections,
                  "at": now, **({"comment_id": comment_id} if thread_id else {})}
        reply = {"id": new_block_id(), "role": "agent", "agent": agent, "label": label, "turn_id": turn_id,
                 "state": "queued", "text": "", "steps": [], "asks": [], "changes": [], "changed": 0, "built": False,
                 "error": "", "fresh": False, "model": model or None, "effort": effort or None, "at": now,
                 "ended_at": None, **({"agent_id": agent_id} if thread_id else {})}
        conversation = _save(conn, conversation, [*conversation["messages"], person, reply])
        if remote:
            conn.execute("INSERT INTO computer_chat_runs (turn_id, conversation_id, bucket, user_id, agent, computer, "
                         "queued_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                         (turn_id, conversation["id"], bucket, user_id, agent, computer, now))
            return conversation, reply, True
        # Recorded before the commit: a server that stops between the two
        # finds the turn open at its start (runner.recover) and fails it.
        store.add_turn(turn_id, ws=ws, link_id=link_id, conversation_id=conversation["id"], page_id=bucket,
                       agent=agent)
    return conversation, reply, True


def write_reply(ws: str, conversation_id: str, turn_id: str, fields: dict) -> bool:
    """The agent's message of the turn ``turn_id`` takes ``fields`` →
    whether it was written (False: the conversation or the message is
    gone). A ``state`` of ``ENDED`` answers a thread's turn in its thread
    (``_answered``)."""
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
    if conversation["thread_id"] and fields.get("state") in ENDED:
        _answered(ws, [(conversation_id, turn_id)])
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


def new(ws: str, bucket: str, user_id: str, label: str) -> None:
    """New chat: the bucket's active conversation of the account archived.
    ChatError 409 while its turn is queued or running."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        conversation = active(conn, bucket, user_id)
        if busy(conversation) is not None:
            raise ChatError(409, f"{label} is still working: stop it first.")
        _archive(conn, conversation)


def listed(conversation: dict | None) -> dict | None:
    """A conversation as History lists it: ``{id, title, agent, agent_id,
    updated_at, turns}``, ``agent_id`` its agent's id (``agent_id_of``);
    None for none."""
    if conversation is None:
        return None
    return {"id": conversation["id"], "title": conversation["title"], "agent": conversation["agent"],
            "agent_id": agent_id_of(conversation), "updated_at": conversation["updated_at"],
            "turns": sum(1 for m in conversation["messages"] if m.get("role") == "agent")}


def history(ws: str, bucket: str, user_id: str) -> list[dict]:
    """The bucket's archived conversations of the account, newest first,
    as ``listed``; a thread's never (they are read from their thread)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"{_SELECT} WHERE bucket = ? AND user_id = ? AND archived_at IS NOT NULL "
                            "AND thread_id = '' ORDER BY updated_at DESC LIMIT ?",
                            (bucket, user_id, HISTORY_CAP)).fetchall()
    return [listed(_conversation(row)) for row in rows]


def lent_history(ws: str, bucket: str, owner: str) -> list[dict]:
    """The bucket's chat conversations, active or archived, of the
    co-authors the agents of ``owner`` serve (``readable``, ``lent``),
    newest first, as ``listed`` with ``user_id``, whose they are; a thread's
    never. The owner reads, stops, answers and exports them, never opens one
    as its own or deletes it."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"{_SELECT} WHERE bucket = ? AND owner = ? AND user_id != ? AND thread_id = '' "
                            "ORDER BY updated_at DESC LIMIT ?", (bucket, owner, owner, HISTORY_CAP)).fetchall()
    return [{**listed(found), "user_id": found["user_id"]} for found in map(_conversation, rows)]


def open_one(ws: str, conversation: dict, label: str) -> dict:
    """The archived ``conversation`` becomes its bucket's active one, the
    active one archived → it as written. ChatError 409 while the active
    one's turn is queued or running, 404 for one that is gone or not
    archived."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = get(conn, conversation["id"])
        if found is None or found["archived_at"] is None or found["thread_id"]:
            raise ChatError(404, "No earlier conversation has that id.")
        current = active(conn, found["bucket"], found["user_id"])
        if busy(current) is not None:
            raise ChatError(409, f"{label} is still working: stop it first.")
        _archive(conn, current)
        return _save(conn, found, found["messages"], archived_at=None)


def delete(ws: str, conversation: dict, *, here: bool = True) -> None:
    """An archived conversation deleted, its turns run on another computer
    with it, and with ``here`` (its agent runs on this computer) the
    session and the grants this computer kept of it and its directory of
    its own. ChatError 409 for the active one."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        found = get(conn, conversation["id"])
        if found is None:
            raise ChatError(404, "No earlier conversation has that id.")
        if found["archived_at"] is None:
            raise ChatError(409, "This is the conversation shown now: start a new chat first.")
        conn.execute("DELETE FROM computer_chats WHERE id = ?", (found["id"],))
        conn.execute("DELETE FROM computer_chat_runs WHERE conversation_id = ?", (found["id"],))
    if here:
        store.forget_conversation(conversation["id"])
        _remove_dir(own_dir(ws, conversation["id"]))


def file_into_home(conn, buckets) -> int:
    """The conversations of the folders ``buckets``, deleted, filed into
    the library's History (archived, their bucket ``home``), in the
    caller's transaction (ops' tree delete, as the AI chats'
    ``routers.chats.file_into_home``) → how many. Each keeps where its
    agent works."""
    return conn.execute("UPDATE computer_chats SET bucket = ?, archived_at = COALESCE(archived_at, ?) "
                        "WHERE bucket IN (SELECT value FROM json_each(?))",
                        (HOME, page_now(), json.dumps(sorted(buckets)))).rowcount


def _remove_dir(path: Path) -> None:
    if path.is_dir():
        try:
            shutil.rmtree(path)
        except OSError as e:
            log.warning(f"[agents] removing the directory of a conversation gone, {path.name}: {e}")


def sweep_dirs(ws: str) -> None:
    """The directories of its own (``own_dir``) of the workspace's
    conversations gone some other way than their delete (a page deleted for
    good, an account no longer a member, a backup restored) removed, every
    one of a workspace gone: at start (``runner.recover``)."""
    top = config.AGENTS_DIR / ws
    if not top.is_dir():
        return
    if not (config.WORKSPACES_DIR / ws / "pages.db").is_file():
        _remove_dir(top)
        return
    with connect_pages_db(ws) as conn:
        kept = {row[0] for row in conn.execute("SELECT id FROM computer_chats WHERE dir = ''")}
    for entry in top.iterdir():
        if entry.is_dir() and entry.name not in kept:
            _remove_dir(entry)


# --- a turn run on another computer (step 4d) ----------------------------------------------------
# A conversation with the agent of another computer of the person's, or of
# another account's whose agent serves the person (a co-author's, ``lent``;
# gamma/computers/registry.py ``seats``): the conversation stays here, that
# computer (its ``computer``) takes its turns over its channel
# (gamma/routers/computers.py) and writes the agent's message back. Each
# turn is a ``computer_chat_runs`` row: ``queued`` until a run of that
# computer claims it (``holder`` its token, ``run`` the id it minted),
# ``running``, then its end; ``seq`` the last write taken from that run, so
# a retried or late write never goes back; ``stop`` the person's Stop of a
# running turn, which the computer is told (a second Stop ends it here);
# ``token`` the turn's token of this server (Gamma's tools for that agent,
# ``keep_token``), renewed by its writes and deleted at its end however it
# ends; ``answers`` the answers given here to what its agent asks, where
# that computer relays its asks (``answer_run``), which the computer takes
# from its listing (``answers_of``). Only the run that holds a turn writes
# it, and only while it runs.
# The claim names where its agent works by an id, never a path (``dir_of``):
# the work folder the bucket is in, whose vault that computer keeps, a
# directory it offers, or one of its own. A turn queued that the computer
# would no longer take (its agent turned off, a person it no longer serves,
# the folder or the directory no longer kept, the computer gone) fails
# (``fail_queued``, from the announce and a token's end), so none runs
# later unasked.

ENDED = ("done", "failed", "stopped")
MAX_WAITING = 20          # turns one listing gives a computer at most
EARLIER = 6               # earlier messages a turn's prompt takes (runner._ChatTurn._work)
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
        raise ChatError(409, f"{catalog.label(far['agent'])} works for you only on the files of a synced folder.")
    offered = {d.get("id") for d in far["dirs"] if isinstance(d, dict)}
    if wanted is None:
        same = conversation is not None and (conversation["computer"], conversation["agent"]) == (
            far["computer_id"], far["agent"])
        kept = conversation["dir"] if same else ""
        return kept if kept in offered else ""
    if wanted and wanted not in offered:
        raise ChatError(400, f"No directory offered on {far['computer']} has that id.")
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


def _message_at(conversation: dict | None, turn_id: str) -> int | None:
    """Where the turn's agent message is in the conversation's messages."""
    messages = (conversation or {}).get("messages") or []
    return next((i for i, m in enumerate(messages) if m.get("role") == "agent" and m.get("turn_id") == turn_id),
                None)


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
    conversation = get(conn, row["conversation_id"])
    at = _message_at(conversation, row["turn_id"])
    if at is not None and conversation["messages"][at].get("state") in BUSY:
        messages = conversation["messages"]
        message = {**messages[at], **(fields or {}), "state": state, "asks": [], "waiting": False,
                   "ended_at": page_now()}
        _save(conn, conversation, [*messages[:at], message, *messages[at + 1:]])
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
            raise ChatError(404, NO_TURN)
        if row["state"] != "running":
            raise ChatError(409, "This turn has ended." if row["state"] in ENDED else "No question of this turn waits.")
        answers = _answers(row["answers"])
        if ask_id in answers:
            raise ChatError(409, ANSWERED)
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, turn_id)
        reply = conversation["messages"][at] if at is not None else {}
        asks = [a for a in reply.get("asks") or [] if isinstance(a, dict)]
        if not any(a.get("id") == ask_id for a in asks):
            raise ChatError(409, f"{reply.get('label') or 'The agent'} no longer waits for that.")
        answers[ask_id] = {"decision": decision, "note": note, "by": by, "at": page_now()}
        conn.execute("UPDATE computer_chat_runs SET answers = ? WHERE turn_id = ?", (json.dumps(answers), turn_id))
        messages = conversation["messages"]
        message = {**reply, "asks": [a for a in asks if a.get("id") != ask_id]}
        _save(conn, conversation, [*messages[:at], message, *messages[at + 1:]])
    return row["holder"]


def claim(ws: str, turn_id: str, *, user_id: str, holder: str, computer_id: str, run: str, agent: str,
          computer: str = "") -> dict:
    """The run ``run`` of the computer ``holder`` (a computer of
    ``user_id``'s, of id ``computer_id``, named ``computer``) takes the
    queued turn of its agent ``agent`` in a conversation of ``user_id``'s
    agent with that computer: the row and its message ``running``, in one
    step under the write lock → ``{turn: {id, conversation_id, bucket,
    title, text, selections, model, effort, for: {id, username},
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
            raise ChatError(404, NO_TURN)
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, turn_id)
        if at is None:
            if row["state"] in BUSY:
                token = _close(conn, row, "stopped")
                conn.commit()
                _drop_tokens([token])
            raise ChatError(404, NO_TURN)
        messages = conversation["messages"]
        reply = messages[at]
        if reply.get("state") not in BUSY and row["state"] in BUSY:
            # its message ended otherwise (a row restored from a backup): never back to running
            token = _close(conn, row, reply.get("state") if reply.get("state") in ENDED else "stopped")
            conn.commit()
            _drop_tokens([token])
            row = run_of(conn, turn_id)
        again = (row["holder"], row["run"]) == (holder, run) and row["state"] == "running"
        if not again and row["state"] != "queued":
            raise ChatError(409, "Another run holds this turn." if row["state"] == "running"
                            else f"The turn is {row['state']} now.")
        if (row["agent"] or reply.get("agent")) != agent:
            raise ChatError(409, f"This turn is for {reply.get('label') or 'another agent'}.")
        found = about(conn, row["bucket"])
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
            _answered(ws, ended)
            raise ChatError(409, refusal)
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
        thread = threads.entry_of(ws, conn, conversation["thread_id"], places=True) if conversation["thread_id"] else None
    with connect_users_db() as users:
        username = account_names(users, {row["user_id"]}).get(row["user_id"], "")
    turn = {"id": turn_id, "conversation_id": row["conversation_id"], "bucket": row["bucket"],
            "title": conversation["title"], "text": person.get("text") or "", "selections": person.get("selections") or [],
            "model": reply.get("model") or "", "effort": reply.get("effort") or "",
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
            raise ChatError(409, "The turn is no longer this run's.")
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
            token = _close(conn, row, "stopped")
            conn.commit()
            _drop_tokens([token])
            raise ChatError(409, "The conversation is gone." if at is None else "The turn is stopped now.")
        state = fields.get("state")
        answers = _answers(row["answers"])
        if state in ENDED:   # nothing waits past the end
            fields, answers = {**fields, "ended_at": page_now(), "asks": [], "waiting": False}, {}
        elif "asks" in fields:
            # an ask answered here stays out of the message, its answer kept while the run still names it
            asked = {a["id"] for a in fields["asks"]}
            answers = {k: v for k, v in answers.items() if k in asked}
            fields = {**fields, "asks": [a for a in fields["asks"] if a["id"] not in answers]}
        messages = conversation["messages"]
        _save(conn, conversation, [*messages[:at], {**messages[at], **fields}, *messages[at + 1:]])
        conn.execute("UPDATE computer_chat_runs SET seq = ?, state = ?, token = CASE WHEN ? THEN '' ELSE token END, "
                     "answers = ? WHERE turn_id = ?", (seq, state if state in ENDED else "running", state in ENDED,
                                                       json.dumps(answers) if answers else "{}", turn_id))
    if state in ENDED:
        _drop_tokens([row["token"]])
        if conversation["thread_id"]:
            _answered(ws, [(conversation["id"], turn_id)])
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
        conversation = get(conn, row["conversation_id"])
        at = _message_at(conversation, row["turn_id"])
        if row["state"] not in BUSY and (at is None or conversation["messages"][at].get("state") not in BUSY):
            return False
        token = _end_run(conn, row, "stopped", ended=ended)
    _drop_tokens([token])
    _answered(ws, ended)
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
            conversation = get(conn, row["conversation_id"])
            at = _message_at(conversation, row["turn_id"])
            tokens.append(_end_run(conn, row, "failed", {
                "error": words(conversation["messages"][at].get("agent") if at is not None else "")}, ended))
    _drop_tokens(tokens)
    _answered(ws, ended)
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
    _answered(ws, ended)
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
    _answered(ws, ended)
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

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
on that computer (step 4d, gamma/local_agents/far_turns.py).

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
text, steps, asks, changes, changed, built, error, fresh, model, effort, speed,
at, ended_at, waiting?}``, ``asks`` what its agent asks the person and waits
for (a turn run on another computer's where that computer relays its asks,
answered here through its run, ``far_turns.answer_run``), ``waiting`` such a turn's
ask that waits for an answer on that computer alone (false at its end),
``model``,
``effort`` and ``speed`` what the turn asked for, null for the agent's default; its
``text`` grows as the agent writes it), the agent's written by the runner as
its turn goes (gamma/local_agents/runner.py ``_ChatTurn``), relative paths
only. Every write takes the workspace's write lock and sets ``version``
above every version of the bucket's conversations of that account, and
never below the clock (``_next_version``), so a client that polls with the
version it holds learns of any change, an opened conversation's included.

A conversation can also begin as a session its agent's CLI keeps, started
in a terminal or an editor (gamma/local_agents/sessions.py), on this
computer or on another one of the reader's (gamma/computers/sessions.py):
``adopt`` writes the session's turns as its first messages (each
``from_session``, the CLI's own history, which no turn's prompt repeats,
``earlier_of``), and the session is bound to it where its agent runs, so
its next turn resumes that session.

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
MAX_STEERS = 20          # messages steered into one turn
NOT_STEERED = "The agent is not taking messages now: this one goes as the next one."
ENDED = ("done", "failed", "stopped")
EARLIER = 6               # earlier messages a turn's prompt takes (runner._ChatTurn._work)

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


def _keeping(ws: str, w: str, user_id: str) -> list[dict]:
    """The vaults here that keep the work folder ``w`` of ``ws``, the ones
    ``user_id`` made first."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = [link for link in folder_links.all_links() if link["workspace_id"] == ws
             and link["folder_id"] == w and folder_links.work_folder_link(link)]
    links.sort(key=lambda link: link["created_by"] != user_id)
    return links


def place_named(ws: str, user_id: str, dir: str) -> dict | None:
    """The directory ``dir`` names as the chat's picker does, where the
    account's agent works and keeps sessions of its CLI's
    (gamma/local_agents/sessions.py): a work folder's id (W) whose vault here
    the account made, or a directory this computer offers its agents
    (``store.dirs``) → ``{kind: vault | offered, dir, path, link}``; None for
    any other (a vault another account made is its maker's to name, as the
    terminal's is; a directory of its own holds Gamma's alone)."""
    offered = next((entry for entry in store.dirs() if entry["id"] == dir), None) if dir else None
    if offered is not None:
        return {"kind": "offered", "dir": offered["id"], "path": offered["path"], "link": None}
    links = [link for link in _keeping(ws, dir, user_id) if link["created_by"] == user_id] if dir else []
    return {"kind": "vault", "dir": dir, "path": links[0]["path"], "link": links[0]} if links else None


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
        links = _keeping(ws, found["w"], user_id)
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


def steer_of(raw: dict) -> dict | None:
    """A message steered into a turn as the person sent it (``{id, text,
    selections}``), as kept: the text stripped, the selections as a
    message's (``selections_of``); None when its id is not one Gamma makes
    or it has no text or too long a one."""
    text = raw.get("text") if isinstance(raw.get("text"), str) else ""
    if not isinstance(raw.get("id"), str) or not MESSAGE_ID.match(raw["id"]) or not text.strip() \
            or len(text.strip()) > MAX_TEXT:
        return None
    return {"id": raw["id"], "text": text.strip(), "selections": selections_of(raw.get("selections"))}


def steered_of(raw) -> list[dict]:
    """The messages a turn's agent took in while it worked, as its message
    keeps them (``runner._ChatTurn._progress``; another computer's run
    writes them too): ``{id, text, selections, at, place: {text, steps}}``,
    ``place`` where the reply stood (characters of its text, its steps);
    at most ``MAX_STEERS``, the malformed left out."""
    out = []
    for item in raw[:MAX_STEERS] if isinstance(raw, list) else []:
        steer = steer_of(item) if isinstance(item, dict) else None
        place = item.get("place") if steer and isinstance(item.get("place"), dict) else None
        counts = [place.get(k) for k in ("text", "steps")] if place else []
        if steer and all(isinstance(n, int) and not isinstance(n, bool) and n >= 0 for n in counts) and counts:
            at = item.get("at") if isinstance(item.get("at"), str) else ""
            out.append({**steer, "at": at[:40], "place": {"text": counts[0], "steps": counts[1]}})
    return out


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


def earlier_of(messages: list[dict], at: int, person: dict) -> list[dict]:
    """The conversation's last ``EARLIER`` messages before the turn's
    (``at``), the person's own aside, as the prompt takes them: ``{role:
    owner | agent, text}``; never one read from the CLI's session
    (``from_session``, its own history: ``adopt``)."""
    return [{"role": "owner" if m.get("role") == "user" else "agent", "text": m.get("text") or ""}
            for m in messages[:at] if m is not person and not m.get("from_session")
            and (m.get("text") or "").strip()][-EARLIER:]


def _turn_of(conversation: dict, message_id: str) -> dict | None:
    """The agent's message that answers the person's message
    ``message_id``, None when the conversation has none."""
    messages = conversation["messages"]
    for i, message in enumerate(messages):
        if message.get("id") == message_id and message.get("role") == "user":
            return next((m for m in messages[i + 1:] if m.get("role") == "agent"), None)
    return None


def begin(ws: str, *, bucket: str, dir: str, user_id: str, link_id: str, agent: str, label: str, text: str,
          selections: list[dict], message_id: str = "", model: str = "", effort: str = "", speed: str = "",
          remote: bool = False, computer: str = "", computer_id: str = "", thread_id: str = "",
          comment_id: str = "", agent_id: str = "", owner: str = "", resume: str = "") -> tuple[dict, dict, bool]:
    """A turn begins: the person's message and the agent's ``queued`` one
    (with the ``model``, ``effort`` and ``speed`` the turn asks for, None
    for the agent's default) written in the bucket's active conversation, or with
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
            conversation = _insert(conn, bucket=bucket, dir=dir, user_id=user_id, agent=agent, title=title_of(text),
                                   now=now, thread_id=thread_id, owner=owner, computer=computer_id)
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
                 "error": "", "fresh": False, "model": model or None, "effort": effort or None,
                 "speed": speed or None, "at": now,
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


def _insert(conn, *, bucket: str, dir: str, user_id: str, agent: str, title: str, now: str, owner: str,
            thread_id: str = "", computer: str = "", conversation_id: str = "") -> dict:
    """A conversation begun, with no messages yet, of the id
    ``conversation_id`` ("" for a new one) → it as written. Inside the
    caller's write lock."""
    conversation = {"id": conversation_id or new_block_id(), "bucket": bucket, "dir": dir, "user_id": user_id,
                    "agent": agent,
                    "title": title, "messages": [], "version": 0, "created_at": now, "updated_at": now,
                    "archived_at": None, "thread_id": thread_id, "owner": owner, "computer": computer}
    conn.execute("INSERT INTO computer_chats (id, bucket, dir, user_id, agent, title, messages, version, created_at, "
                 "updated_at, thread_id, owner, computer) VALUES (?, ?, ?, ?, ?, ?, '[]', 0, ?, ?, ?, ?, ?)",
                 (conversation["id"], bucket, dir, user_id, agent, title, now, now, thread_id, owner, computer))
    return conversation


def adopt(ws: str, *, bucket: str, dir: str, user_id: str, agent: str, label: str, title: str, messages: list[dict],
          conversation_id: str = "", computer: str = "") -> dict:
    """A session the agent's CLI keeps, opened: a new conversation of the
    bucket for the account (of the id ``conversation_id``, "" for a new
    one), its agent's own (``dir`` where it works, as ``place_of`` keeps
    it, or ``far_turns.far_dir`` for the agent of another computer, ``computer``),
    titled ``title``, holding ``messages`` (gamma/local_agents/sessions.py
    ``messages``), the bucket's active one archived → the conversation,
    which the caller binds to the session (``store.ran_conversation``; for
    another computer's, that computer did when it read the session), so its
    next turn resumes it. ChatError 409 while the active one's turn is
    queued or running."""
    with connect_pages_db(ws) as conn:
        write_lock(conn)
        current = active(conn, bucket, user_id)
        if busy(current) is not None:
            raise ChatError(409, f"{label} is still working: stop it first.")
        _archive(conn, current)
        conversation = _insert(conn, bucket=bucket, dir=dir, user_id=user_id, agent=agent, title=title or label,
                               now=page_now(), owner=user_id, computer=computer, conversation_id=conversation_id)
        return _save(conn, conversation, messages)


def titles(ws: str, conversation_ids: list[str], *, user_id: str = "", computer: str = "",
           agent: str = "") -> dict[str, str]:
    """The titles of the conversations of ``ws`` among ``conversation_ids``,
    by id; with ``user_id``, of that account's with the agent ``agent`` of
    the computer ``computer`` alone (a far conversation's: "" this
    server's own)."""
    if not conversation_ids:
        return {}
    where, args = "", ()
    if user_id:
        where, args = " AND user_id = ? AND computer = ? AND agent = ?", (user_id, computer, agent)
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT id, title FROM computer_chats WHERE id IN (SELECT value FROM json_each(?))"
                            + where, (json.dumps(sorted(set(conversation_ids))), *args)).fetchall()
    return dict(rows)


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


def _message_at(conversation: dict | None, turn_id: str) -> int | None:
    """Where the turn's agent message is in the conversation's messages."""
    messages = (conversation or {}).get("messages") or []
    return next((i for i, m in enumerate(messages) if m.get("role") == "agent" and m.get("turn_id") == turn_id),
                None)


def reply_of(ws: str, conversation_id: str, turn_id: str) -> tuple[dict | None, dict | None]:
    """``(the conversation, the agent's message of the turn)``, None for
    either that is gone."""
    with connect_pages_db(ws) as conn:
        conversation = get(conn, conversation_id)
    if conversation is None:
        return None, None
    at = _message_at(conversation, turn_id)
    return conversation, conversation["messages"][at] if at is not None else None


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

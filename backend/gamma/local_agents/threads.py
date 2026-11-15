"""A comment sent to a computer's agent (docs/dev/local_agents.md "A comment
sent to an agent", "The interaction" of
docs/research/agents-as-participants-plan.md): a person's comment whose
``to`` names an agent they may use on the page (gamma/agents.py: a
computer's agent, ``computer:<computer>:<name>``) is stored and is also the
instruction of a turn of a conversation of its thread. The thread's
conversation is the account's alone, a ``computer_chats`` row whose
``thread_id`` names the thread (gamma/local_agents/chats.py ``of_thread``),
its bucket the thread's page, where its agent works chosen as a chat's
(``chats.place_of``), so the next ticked reply resumes the same
conversation and its agent's session; the first of a thread the agent
opened in a turn of this computer (to ask a decision) resumes the session
of the conversation that opened it (``opener_of``). Its turns run as a
chat's do: queued
for this computer's runner (gamma/local_agents/runner.py ``_ChatTurn``), or
for the computer that keeps the page's work folder with that agent, which
claims it over its channel (``chats.claim``). A co-author the agent's owner
named on the computer (decision 13 of
docs/research/agents-as-participants-plan.md) sends comments to it as the
owner does, on the files of a synced folder alone; the conversation is the
co-author's, read by the owner too (``chats.readable``).

``start`` makes the turn or says why not, once, the comment stored either
way. ``entry_of`` is the thread as the turn's prompt names it
(gamma/annotations/entries.py's entry). ``settle`` is the end-of-turn rule:
a turn that ended with no reply of its agent in the thread has its last
message written there under the agent's name (gamma/annotations/hand.py),
once, or for a failed or stopped turn a short note saying so, so a thread
sent is a thread answered. ``opened`` lists the threads an agent opened in
a turn (the chat's answer card); ``chats_of`` what the comment routes add to
a thread for its reader (``agent_chat``: the conversation and its turn's
state, the thread's chip); ``agent_named`` the agent a person's ``@name``
named in a thread (gamma/inbox.py: a member of that name is told nothing).

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

import json

from fastapi import HTTPException

from .. import agents, comments, hosted, mentions, workspaces
from ..blocks_store import new_block_id
from ..db import account_names, connect_pages_db, connect_users_db
from ..logbuf import log
from . import catalog, chats, enabled, home, store

# Why a comment's ``to`` started no turn, as the client words it (comments.js
# ``turnRefusal``), each with an English ``why``.
REFUSALS = ("closed", "not_newest", "viewer", "writes", "off", "offline", "paused", "not_ready", "busy", "elsewhere",
            "folder", "other")
MAX_OPENED = 20          # threads a turn's answer card lists
OPENED_CHARS = 160       # characters of each one's first comment


def name_of(to: str) -> str:
    """The agent's name (``codex``, ``claude``) a computer agent's id names,
    "" for another id or none."""
    if agents.kind_of(to) != "computer":
        return ""
    name = to.rpartition(":")[2]
    return name if name in catalog.AGENTS else ""


def refused(name: str, code: str, why: str, computer: str = "") -> dict:
    """``{agent, refused, why, computer?}``: the answer of a ``to`` that
    started no turn, ``agent`` the agent's label, ``computer`` the name of
    the other computer it names (``offline``, ``paused`` there)."""
    return {"agent": catalog.label(name) if name else "the agent", "refused": code, "why": why,
            **({"computer": computer} if computer else {})}


def start(ws: str, event: dict, to: str, *, by_name: str) -> dict:
    """The turn a person's comment write (``comments.written``'s event: a
    new thread, a reply or an edit of the thread's newest comment) sent to
    the agent ``to`` makes → ``{agent, conversation_id, turn_id, state}``,
    or ``refused``'s answer (``REFUSALS``; ``why`` an English sentence the
    client words itself by the code). The instruction is the comment's
    text, the agent's ``@name`` taken out of its prose; a new comment's turn
    takes the comment's id for its message, so a retry makes none twice.
    Never raises."""
    name = name_of(to)
    if not name:
        return refused("", "other", "Gamma knows no agent of that id.")
    try:
        return _start(ws, event, to, name, by_name)
    except chats.ChatError as e:
        return refused(name, "busy" if e.status == 409 else "other", e.detail)
    except Exception as e:  # noqa: BLE001 — the comment is stored; its turn never fails it
        log.warning(f"[agents] the turn of comment {event.get('comment_id')} in workspace {ws}: {e}")
        return refused(name, "other", f"Gamma could not send this to {catalog.label(name)}.")


def _start(ws: str, event: dict, to: str, name: str, by_name: str) -> dict:
    from ..computers import registry  # local: the registry reaches the workspaces
    from . import runner

    label = catalog.label(name)
    actor, page_id = event["actor"], event["page_id"]
    with connect_pages_db(ws) as conn:
        thread = comments.thread_of(conn, event["thread_id"])
        if thread is None:
            return refused(name, "other", "That thread is gone.")
        if thread["resolved_at"]:
            return refused(name, "closed", f"Reopen this thread to ask {label} again.")
        if event["kind"] == "edit":
            said = [c for c in thread["comments"] if not c["deleted"]]
            if not said or said[-1]["id"] != event["comment_id"]:
                return refused(name, "not_newest", f"Reply to ask {label} again: only the newest comment is sent.")
        mine = chats.of_thread(conn, thread["id"], actor)
        opener = opener_of(conn, thread["id"], actor, name) if mine is None else None
        chat = chats.active(conn, page_id, actor)
    if not workspaces.at_least(workspaces.role_of(ws, actor), "editor"):
        return refused(name, "viewer", f"Only an owner or an editor of this workspace can send comments to {label}.")
    refusal = hosted.read_only()
    if not refusal:
        try:
            workspaces.refuse_writes(ws)
        except HTTPException as e:
            refusal = str(e.detail)
    if refusal:
        return refused(name, "writes", refusal)
    text = mentions.without(event.get("body") or "", {name}).strip() or (event.get("body") or "").strip()
    begun = {"bucket": page_id, "user_id": actor, "agent": name, "label": label, "text": text[:chats.MAX_TEXT],
             "selections": [], "thread_id": thread["id"], "comment_id": event["comment_id"],
             "message_id": event["comment_id"] if event["kind"] != "edit" else new_block_id()}
    computer = to[len(agents.COMPUTER):].rpartition(":")[0]
    if enabled() and computer == agents.here_id():
        row = store.usable(actor, ws, name)
        if row is None:
            return refused(name, "off", f"Turn on {label} in Settings → AI first.")
        lent = row["set_by"] != actor
        # where the thread's conversation works, else the one that opened the thread, else the page's chat
        dir = next((c["dir"] for c in (mine, opener, chat) if c is not None and c["agent"] == name), "")
        place = chats.place_of(ws, page_id, actor, dir)
        if lent and (place is None or place["kind"] != "vault"):
            return refused(name, "folder", f"{label} works for you only on the files of a synced folder.")
        if place is None:
            return refused(name, "other", "This page is gone.")
        link = place["link"]
        if link is not None:
            from .. import folder_links  # local: folder_links imports this package's modules inside functions

            if link["status"].get(folder_links.PAUSED) or folder_links.choosing(link):
                return refused(name, "paused", f"The synced folder is paused on this computer: {label} starts "
                                               "once it syncs again.")
        found = catalog.cached(name)
        if found["ready"] is False:
            return refused(name, "not_ready", found["problem"] or f"{label} is not ready on this computer.")
        conversation, reply, new = chats.begin(ws, dir=place["dir"], link_id=link["id"] if link is not None else "",
                                               agent_id=agents.here(name).id, owner=row["set_by"],
                                               resume=opener["id"] if opener is not None else "", **begun)
        if new:
            runner.queue_chat(ws, place, reply["turn_id"], conversation_id=conversation["id"], bucket=page_id,
                              agent=name, user_id=actor)
    else:
        far = chats.remote_of(ws, page_id, actor)
        held = registry.computer_of(far["token_id"]) if far is not None else None
        if far is None or far["agent"] != name or held is None or held[0] != computer:
            return refused(name, "elsewhere", f"{label} on that computer does not work on this page.")
        if not far["online"]:
            return refused(name, "offline", f"{far['computer']} is offline: send this again once it connects.",
                           far["computer"])
        if far["paused"]:
            return refused(name, "paused", f"The synced folder is paused on {far['computer']}: {label} starts once "
                                           "it syncs again.", far["computer"])
        if far["ready"] is False:
            return refused(name, "not_ready", chats.far_problem(far))
        conversation, reply, new = chats.begin(ws, dir=far["folder_id"], link_id="", remote=True,
                                               computer=far["computer"], owner=far["user_id"],
                                               agent_id=agents.of_channel(far["token_id"], name).id, **begun)
        if new:
            registry.wake(far["token_id"], "chat")
    return {"agent": label, "conversation_id": conversation["id"], "turn_id": reply["turn_id"],
            "state": reply["state"]}


def opener_of(conn, thread_id: str, user_id: str, agent: str) -> dict | None:
    """The account's newest conversation with ``agent`` whose turn opened
    the thread ``thread_id`` (an agent message's ``threads``, what a turn
    of this computer records), None for none: the thread's first
    conversation resumes its session (``chats.begin``'s ``resume``), so a
    reply to what the agent asked is heard by the session that asked it."""
    row = conn.execute(
        f"{chats._SELECT} WHERE id = (SELECT c.id FROM computer_chats c, json_each(c.messages) m, "
        "json_each(m.value, '$.threads') t WHERE c.user_id = ? AND c.agent = ? AND c.archived_at IS NULL "
        "AND json_extract(t.value, '$.id') = ? ORDER BY c.updated_at DESC LIMIT 1)",
        (user_id, agent, thread_id)).fetchone()
    return chats._conversation(row) if row else None


def entry_of(ws: str, conn, thread_id: str, *, paths: dict | None = None, places: bool = False) -> dict | None:
    """The thread ``thread_id`` as the turn's prompt names it: its entry
    (gamma/annotations/entries.py ``entry``: its file, place, quote, source
    line, picture and comments with their authors), named by ``paths``
    (``entries.paths_of`` of a vault's rows); with ``places`` its PDF place
    and its recorded source alone, for a computer that finds the line on its
    own disk (a far turn's claim). None for a thread gone or hidden."""
    from ..annotations import entries

    found = entries.entry(ws, conn, thread_id, paths=paths, sources=not places, places=places)
    return found if found is not None and found["type"] == "thread" else None


def _writer(ws: str, conversation: dict, agent_id: str, label: str):
    """Who the thread's answer is written as: the conversation's account,
    signed as the turn's agent as its computer is called now
    (``agents.read_all``), with its owner where it is another account's (a
    co-author's conversation, ``chats.lent``)."""
    from ..annotations.hand import Writer

    user_id, owner = conversation["user_id"], conversation.get("owner") or ""
    with connect_users_db() as users:
        names = account_names(users, {user_id, owner} - {""})
    key = (user_id, agent_id, names.get(owner, "") if chats.lent(conversation) else "")
    return Writer(user_id, names.get(user_id, ""), agents.read_all({key: label}, ws).get(key))


def _said(reply: dict) -> str:
    """What the thread is told of the turn: its last message, or a short
    note of a turn that ended without one, failed or was stopped."""
    label = reply.get("label") or catalog.label(reply.get("agent") or "")
    if reply.get("state") == "stopped":
        return f"Stopped before {label} answered."
    if reply.get("state") == "failed":
        return reply.get("error") or f"{label} could not finish."
    return (reply.get("text") or "").strip()[:comments.MAX_BODY] or f"{label} finished without a reply."


def settle(ws: str, conversation_id: str, turn_id: str) -> None:
    """The end-of-turn rule: the thread conversation's turn ``turn_id``
    ended, and its agent wrote no reply in the thread since the turn began
    (through the ``comment`` tool and its turn's token, signed as that
    agent for the account): Gamma writes the turn's last message there as
    the agent's reply (``_said``; a note of a turn failed or stopped), under
    an id of the turn's, so it is written once however often its end is.
    Never raises."""
    try:
        _settle(ws, conversation_id, turn_id)
    except Exception as e:  # noqa: BLE001 — the turn's end stands; the thread stays unanswered
        log.warning(f"[agents] answering the thread of turn {turn_id} in workspace {ws}: {e}")


def _settle(ws: str, conversation_id: str, turn_id: str) -> None:
    from ..annotations import hand

    with connect_pages_db(ws) as conn:
        conversation = chats.get(conn, conversation_id)
        at = chats._message_at(conversation, turn_id)
        reply = conversation["messages"][at] if at is not None else None
        if reply is None or not conversation["thread_id"] or reply.get("state") not in chats.ENDED:
            return
        thread_id, user_id = conversation["thread_id"], conversation["user_id"]
        agent_id = reply.get("agent_id") or ""
        if not agent_id or conn.execute(
                "SELECT 1 FROM comments WHERE thread_id = ? AND agent_id = ? AND author = ? AND created_at >= ? "
                "AND deleted_at IS NULL LIMIT 1", (thread_id, agent_id, user_id, reply.get("at") or "")).fetchone():
            return
        writer = _writer(ws, conversation, agent_id, reply.get("label") or "")
        try:
            hand.reply(conn, writer, ws, thread_id, _said(reply), comment_id=home.reply_id(turn_id, thread_id))
        except ValueError as e:
            log.info(f"[agents] the thread of turn {turn_id} not answered: {e}")


def opened(ws: str, agent_id: str, user_id: str, since: str) -> list[dict]:
    """The threads the agent ``agent_id`` opened for the account since
    ``since`` (a turn's start): ``[{id, page_id, block_id, text}]``, oldest
    first, at most ``MAX_OPENED``, ``text`` the first comment on one line,
    cut. What a turn's answer card lists (``threads``)."""
    if not since:
        return []
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT c.id, b.page_id, c.block_id, c.body FROM comments c "
                            "JOIN unified_blocks b ON b.id = c.block_id WHERE c.agent_id = ? AND c.author = ? "
                            "AND c.id = c.thread_id AND c.created_at >= ? AND c.deleted_at IS NULL "
                            "AND c.orphaned_at IS NULL ORDER BY c.created_at, c.id LIMIT ?",
                            (agent_id, user_id, since, MAX_OPENED)).fetchall()
    return [{"id": thread_id, "page_id": page_id, "block_id": block_id,
             "text": " ".join((body or "").split())[:OPENED_CHARS]} for thread_id, page_id, block_id, body in rows]


def chats_of(ws: str, user_id: str, thread_ids) -> dict[str, dict]:
    """``{thread id: {conversation_id, agent, label, turn, for?}}``: the
    account's conversations of the threads ``thread_ids``, else those of a
    co-author its agent talks in (``for`` the co-author's username: the
    owner reads them, ``chats.readable``); ``turn`` its turn still to end,
    ``{turn_id, state, asking, waiting, computer}``: ``asking`` while an ask
    of it waits for the person in the chat, ``waiting`` while one waits on
    the other computer that runs it, ``computer`` that computer's name, ""
    for this one; None for none. What the comment routes add to a thread
    for its reader (``agent_chat``)."""
    ids = sorted(set(thread_ids))
    if not user_id or not ids:
        return {}
    out = {}
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"{chats._SELECT} WHERE (user_id = ? OR owner = ?) AND archived_at IS NULL AND thread_id "
                            "IN (SELECT value FROM json_each(?)) ORDER BY user_id = ?, updated_at",
                            (user_id, user_id, json.dumps(ids), user_id)).fetchall()
        others = {chats._conversation(r)["user_id"] for r in rows} - {user_id}
        names = {}
        if others:
            with connect_users_db() as users:
                names = account_names(users, others)
        for row in rows:
            conversation = chats._conversation(row)
            busy = chats.busy(conversation)
            turn = None
            if busy is not None:
                run = chats.run_of(conn, busy["turn_id"])
                turn = {"turn_id": busy["turn_id"], "state": busy.get("state"), "asking": bool(busy.get("asks")),
                        "waiting": bool(busy.get("waiting")), "computer": run["computer"] if run else ""}
            out[conversation["thread_id"]] = {"conversation_id": conversation["id"], "agent": conversation["agent"],
                                              "label": catalog.label(conversation["agent"]), "turn": turn,
                                              **({"for": names.get(conversation["user_id"], "")}
                                                 if conversation["user_id"] != user_id else {})}
    return out


def agent_named(ws: str, thread_id: str, user_id: str) -> str:
    """The agent (``codex``, ``claude``) of the account's conversations of
    the thread, the newest's, "" for none: in the account's own comments
    there, that ``@name`` is the agent, not a member of that name."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT agent FROM computer_chats WHERE thread_id = ? AND user_id = ? "
                           "ORDER BY updated_at DESC LIMIT 1", (thread_id, user_id)).fetchone()
    return row[0] if row else ""

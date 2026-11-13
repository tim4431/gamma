"""A mention of a work folder's agent in a comment (docs/dev/local_agents.md
"A mention in a comment"): the vault's maker writes ``@codex`` (or
``@claude``) in a comment on a file of a work folder whose agent on this
computer is that one, and the comment is a Send of its thread to the agent,
or, written in the page's own thread, of the file's open comments. On a
server that runs no agent for the folder (a NAS), the computer that keeps
that work folder there with an agent stands for it (step 4c): the Send is
made here the same way, and that computer takes it over its channel.

``agent_named`` is the rule, the one place it is decided: the comment
routes ask it once per write, before ``comments.written``, and put the name
on the event (``agent_named``), so the Inbox reads the same answer (an
account of that name is not told of it); ``agent_of_page`` is the same
lookup for the Inbox's earlier comments. ``send`` then makes the Send
(``requests.create``, made in Gamma: ``via`` session, so the runner takes it
as any Send of the maker's) or says why not. Everywhere else ``@codex`` is
what it was: plain text, or a member named codex.

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

from fastapi import HTTPException

from .. import comments, hosted, mentions, workspaces
from ..blocks_store import new_block_id
from ..db import connect_pages_db
from ..feedback import entries, requests
from ..feedback.entries import FeedbackError
from ..logbuf import log
from . import catalog, enabled, runner

KINDS = ("create", "reply", "edit")
REFUSALS = ("closed", "not_newest", "viewer", "writes", "too_many", "nothing", "other")


def agent_named(ws: str, event: dict, *, via: str) -> str:
    """The agent (``codex``, ``claude``) a committed comment write names, ""
    for none: a person's own comment (no token's, no agent's; ``via`` the
    share it came through, none) made, replied or edited, newly mentioning
    (for an edit, not in its text before) the agent of the work folder its
    page is a file of (``agent_of_page``: on this computer, or on the
    computer that keeps that folder of this server), by that vault's
    maker. A comment that names no agent reads nothing."""
    if event.get("kind") not in KINDS or event.get("agent") or event.get("agent_id") or via:
        return ""
    names = mentions.people_in(event.get("body") or "")
    if event["kind"] == "edit":
        names -= mentions.people_in(event.get("before") or "")
    names &= set(catalog.AGENTS)
    if not names:
        return ""
    agent = agent_of_page(ws, event["page_id"], event.get("actor") or "")
    if agent is None or agent["name"] not in names or agent["maker"] != event.get("actor"):
        return ""
    return agent["name"]


def agent_of_page(ws: str, page_id: str, user_id: str = "") -> dict | None:
    """``{name, label, maker}`` of the agent of the work folder the page is
    a live file of: the one on this computer (``runner.folder_agent``, on a
    server that runs agents), else the one of the account ``user_id``'s
    computer that keeps that folder of this server with an agent
    (gamma/computers/registry.py ``agent_of``: ``maker`` that account, whose
    Send it then takes over its channel; another account's computer never
    answers, and without ``user_id`` none does); None for none."""
    from ..computers import registry  # local: the registry reaches the workspaces

    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT work_folder FROM unified_blocks WHERE id = ? AND parent_id = 'root'",
                           (page_id,)).fetchone()
    if not row or not row[0]:
        return None
    found = runner.folder_agent(ws, row[0]) if enabled() else None
    if found is not None and found["name"] in catalog.AGENTS:
        return {"name": found["name"], "label": found["label"], "maker": found["maker"]}
    far = registry.agent_of(ws, row[0], user_id)
    if far is None:
        return None
    return {"name": far["agent"], "label": far["label"], "maker": far["user_id"]}


def _refused(label: str, code: str, why: str) -> dict:
    return {"agent": label, "refused": code, "why": why}


def send(ws: str, event: dict, name: str, *, by_name: str) -> dict:
    """The Send a comment that names the agent ``name`` (``agent_named``)
    makes, after ``comments.written``: of its thread, or with the comment in
    the page's own thread of every open thread of the file, the comment's
    other words as its instruction → ``{agent, request_id, scope: "thread" |
    "file", threads}``, or ``{agent, refused, why}`` (``REFUSALS``; ``why``
    an English sentence, which the client words itself by the code). A
    new comment's Send takes the comment's id, so a retry makes none twice.
    Never raises."""
    label = catalog.label(name)
    try:
        return _send(ws, event, name, label, by_name)
    except FeedbackError as e:
        return _refused(label, "other", e.detail)
    except Exception as e:  # noqa: BLE001 — the comment is stored; its Send never fails it
        log.warning(f"[agents] the Send of comment {event.get('comment_id')} in workspace {ws}: {e}")
        return _refused(label, "other", f"Gamma could not send this to {label}.")


def _send(ws: str, event: dict, name: str, label: str, by_name: str) -> dict:
    actor, page_id = event["actor"], event["page_id"]
    whole = event["block_id"] == page_id  # the page's own thread: the file's open comments
    with connect_pages_db(ws) as conn:
        thread = comments.thread_of(conn, event["thread_id"])
        if thread is None:
            return _refused(label, "other", "That thread is gone.")
        if thread["resolved_at"]:
            return _refused(label, "closed", f"Reopen this thread to send it to {label} again.")
        if event["kind"] == "edit":
            said = [c for c in thread["comments"] if not c["deleted"]]
            if not said or said[-1]["id"] != event["comment_id"]:
                return _refused(label, "not_newest", f"Mention {label} in a new reply to send this thread again.")
    if not workspaces.at_least(workspaces.role_of(ws, actor), "editor"):
        return _refused(label, "viewer", f"Only an owner or an editor of this workspace can send comments to {label}.")
    refused = hosted.read_only()
    if not refused:
        try:
            workspaces.refuse_writes(ws)
        except HTTPException as e:
            refused = str(e.detail)
    if refused:
        return _refused(label, "writes", refused)
    with connect_pages_db(ws) as conn:
        scope = entries.resolve_scope(conn, page_id)
        if whole:
            count = len(entries.thread_ids(conn, scope))
            if not count:
                return _refused(label, "nothing", "Nothing to send: no open comments here.")
            if count > runner.MAX_THREADS:
                return _refused(label, "too_many", f"{label} takes at most {runner.MAX_THREADS} comments at a time: "
                                                   "mention it in the threads you want answered.")
        note = mentions.without(event.get("body") or "", {name}).strip()[:requests.MAX_NOTE]
        out = requests.create(ws, conn, scope, by=actor, by_name=by_name, note=note,
                              threads=None if whole else [event["thread_id"]],
                              request_id=new_block_id() if event["kind"] == "edit" else event["comment_id"],
                              via=requests.SESSION)
    record = out["request"]
    return {"agent": label, "request_id": record["id"], "scope": "file" if whole else "thread",
            "threads": len(record["threads"])}

"""The changes agents make to a workspace's notes, kept until the person
reviews them (docs/dev/ai.md "Reviewing an agent's changes"): Gamma AI's
in the chat and an MCP connection's alike, in the workspace's
``agent_changes`` table (db.PAGES_SCHEMA). SDK-free, like ``mcp_export``.

Every changing tool's action carries a revert record, the note as it was
before the call and as the call left it (docs/dev/ai_tools.md "Reverting
a note change"). ``ai_tools.registry.run_agent_tool`` journals each one
whose kind ``ai_revert`` takes back (``KINDS``) under an id its action
names (``change_id``). That one row is what every surface draws and
decides on: the note's tracked changes and its review strip, the reply's
rows in the chat, the connection's Changes in Settings and MCP's own
``list_changes`` and ``undo_change``.

A change waits for review (``open``) until the person keeps it
(``kept``) or undoes it (``undone``); a redo of an undone one keeps it. A
change the person allowed on a card or in a question before it was made
is kept as it is written: they saw it then. A kept change still marks its
note, as a diff's gutter does, until the person clears the mark
(``cleared_at``) or the mark's time is up (the account's choice, kept by
the client: ``marked_on``'s ``kept_since``); the mark unfolds to the
change, which can still be undone. Undoing is ``ai_revert``'s
revert, a merge into the notes as they are now, which stops (``filled``,
``changed``, ``moved``; forceable) when someone's work since would be
lost. Each write tells the account's open tabs (``{t: "ai_changes"}``)
so every note that draws the change draws it as it is now. The newest
KEEP_PER_CONNECTION per account and connection are kept, none older than
KEEP_DAYS: a change past them is no longer drawn and stays as written.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import ai_revert, collab
from .db import connect_pages_db, format_stamp, page_now, parse_stamp

KEEP_DAYS = 30
KEEP_PER_CONNECTION = 500
# The revert kinds a note or page tool's record is taken back by
# (ai_revert._PLANS; frontend chat/aiRevert.js ``revertKind``).
KINDS = ("edit", "create", "move", "delete", "made")
OPEN, KEPT, UNDONE = "open", "kept", "undone"
# The most ids one read of their states or one Keep all names.
MAX_IDS = 500

LIST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "how many, newest first (default 20)"}},
}
LIST_DESCRIPTION = (
    "List the changes this connection made to the notes where it edits, newest first: each one's change_id, "
    "when, what it did and where, and whether the user kept it, undid it or has yet to review it. Every "
    "change's own answer names its change_id too; use this when you no longer have it.")
UNDO_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {
        "change_id": {"type": "string", "minLength": 1, "maxLength": 64,
                      "description": "the change to take back, from its answer or list_changes"},
        "force": {"type": "boolean",
                  "description": "take it back even though someone changed it since, losing that; only when the "
                                 "user says so after you told them (default false)"},
    },
    "required": ["change_id"],
}
UNDO_DESCRIPTION = (
    "Take back one change this connection made: an edit goes back to the text before it (what anyone typed "
    "since stays), a paste, a drawing or a page made goes, a move goes back, a deletion comes back. Only this "
    "connection's own changes, each once. When someone changed it since, it stops and says what undoing it "
    "anyway would lose: tell the user, and call again with force: true only if they agree. Undo the latest "
    "change first when several touch the same note.")


def revert_kind(tool: str, action: dict) -> str:
    """The revert kind a tool's action is taken back by: a page made by
    create_page's ``made``, a highlight by a new block's, a note tool's its
    action's kind."""
    return "made" if tool == "create_page" else "create" if tool == "highlight" else action.get("kind") or ""


def nudge(account: str, ws: str, page_id: str) -> None:
    """Tell the account's open tabs that a change of this page moved on, or
    a question about one (gamma/agent_asks.py): content-free, a tab reads
    the page's changes and asks again over HTTP."""
    collab.publish_account(account, {"t": "ai_changes", "ws": ws, "page_id": page_id})


def record(ws: str, *, account: str, source: str, token_id: str, agent: dict, tool: str, action: dict,
           kept: bool = False) -> str:
    """Journal one change ``account``'s agent made (``source`` ``chat`` or
    ``mcp``; ``token_id`` the connection, '' for a person's chat), with the
    stamp it made it under, when its action carries a revert record: its
    change_id, else "" (nothing to take back by). ``kept``: the person
    allowed this very change before it was made, so it waits for no
    review. A change made for no account (a share link's visitor) is not
    journaled: nobody could review it."""
    revert, kind = action.get("revert"), revert_kind(tool, action)
    if not account or not isinstance(revert, dict) or kind not in KINDS or not action.get("block_id"):
        return ""
    change_id = secrets.token_urlsafe(9)
    now = datetime.now(timezone.utc)
    at = format_stamp(now)
    page_id = action.get("page_id") or ""
    with connect_pages_db(ws) as conn:
        conn.execute("INSERT INTO agent_changes (id, account, source, token_id, agent, at, tool, kind, block_id, "
                     "page_id, summary, revert, kept_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (change_id, account, source, token_id, json.dumps(agent or {}), at, tool, kind,
                      action["block_id"], page_id, action.get("summary") or tool, json.dumps(revert),
                      at if kept else ""))
        conn.execute("DELETE FROM agent_changes WHERE at < ?", (format_stamp(now - timedelta(days=KEEP_DAYS)),))
        conn.execute("DELETE FROM agent_changes WHERE account = ? AND token_id = ? AND id NOT IN (SELECT id FROM "
                     "agent_changes WHERE account = ? AND token_id = ? ORDER BY at DESC LIMIT ?)",
                     (account, token_id, account, token_id, KEEP_PER_CONNECTION))
        conn.commit()
    nudge(account, ws, page_id)
    return change_id


_COLUMNS = ("c.id, c.token_id, c.at, c.tool, c.kind, c.block_id, c.page_id, c.summary, c.revert, c.undone_at, "
            "p.content, c.account, c.source, c.kept_at, c.agent")


def state_of(undone_at: str, kept_at: str) -> str:
    return UNDONE if undone_at else KEPT if kept_at else OPEN


def _change(row) -> dict:
    state = state_of(row[9], row[13])
    return {"id": row[0], "token_id": row[1], "at": row[2], "tool": row[3], "kind": row[4], "block_id": row[5],
            "page_id": row[6], "summary": row[7], "revert": json.loads(row[8]), "undone": state == UNDONE,
            "state": state, "page_title": row[10] or "", "account": row[11], "source": row[12],
            "agent": json.loads(row[14] or "{}"), "kept_at": row[13]}


def _select(where: str) -> str:
    return (f"SELECT {_COLUMNS} FROM agent_changes c LEFT JOIN unified_blocks p ON p.id = c.page_id "
            f"WHERE {where}")


def listed(ws: str, token_id: str, limit: int = 20) -> list[dict]:
    """The connection's changes in ``ws``, newest first, with their page's
    title (empty when the page is gone)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(_select("c.token_id = ? ORDER BY c.at DESC LIMIT ?"), (token_id, limit)).fetchall()
    return [_change(r) for r in rows]


def marked_on(ws: str, account: str, page_id: str, kept_since: float = 0) -> list[dict]:
    """The changes made for ``account`` on the page that its note draws,
    oldest first: those that wait for review, and the kept ones whose mark
    was not cleared — with ``kept_since`` (seconds), only those kept that
    recently (the account's marks last that long). A kept one carries
    ``kept_ago``, the seconds since it was kept by this server's clock, so a
    client times its mark out on its own clock."""
    now = datetime.now(timezone.utc)
    where = "c.account = ? AND c.page_id = ? AND c.undone_at = '' AND c.cleared_at = ''"
    args = [account, page_id]
    if kept_since > 0:
        where += " AND (c.kept_at = '' OR c.kept_at >= ?)"
        args.append(format_stamp(now - timedelta(seconds=kept_since)))
    with connect_pages_db(ws) as conn:
        rows = conn.execute(_select(where + " ORDER BY c.at"), args).fetchall()
    out = []
    for change in map(_change, rows):
        kept = parse_stamp(change["kept_at"]) if change["state"] == KEPT else None
        out.append({**change, **({"kept_ago": max(0.0, round((now - kept).total_seconds(), 1))} if kept else {})})
    return out


def _wanted(ids) -> list[str]:
    """The change ids a call names, once each and at most MAX_IDS."""
    return [i for i in dict.fromkeys(ids) if isinstance(i, str) and i][:MAX_IDS]


def states(ws: str, account: str, ids) -> dict:
    """``{change_id: state}`` of ``account``'s changes among ``ids`` (at most
    MAX_IDS); an id that is gone, or another account's, is left out."""
    wanted = _wanted(ids)
    if not account or not wanted:
        return {}
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"SELECT id, undone_at, kept_at FROM agent_changes WHERE account = ? AND id IN "
                            f"({', '.join('?' * len(wanted))})", (account, *wanted)).fetchall()
    return {r[0]: state_of(r[1], r[2]) for r in rows}


def find(ws: str, change_id: str) -> dict | None:
    with connect_pages_db(ws) as conn:
        row = conn.execute(_select("c.id = ?"), (change_id,)).fetchone()
    return _change(row) if row else None


def _stamp(ws: str, account: str, ids, column: str, condition: str) -> list[str]:
    """Stamp ``column`` now on ``account``'s changes among ``ids`` that meet
    ``condition``, and tell the tabs of each page they are on: the ids
    stamped."""
    wanted = _wanted(ids)
    if not account or not wanted:
        return []
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"SELECT id, page_id FROM agent_changes WHERE account = ? AND {condition} AND id IN "
                            f"({', '.join('?' * len(wanted))})", (account, *wanted)).fetchall()
        if rows:
            conn.execute(f"UPDATE agent_changes SET {column} = ? WHERE id IN ({', '.join('?' * len(rows))})",
                         (page_now(), *[r[0] for r in rows]))
            conn.commit()
    for page_id in {r[1] for r in rows}:
        nudge(account, ws, page_id)
    return [r[0] for r in rows]


def keep(ws: str, account: str, ids) -> list[str]:
    """Keep ``account``'s changes among ``ids`` that wait for review: the
    ids kept now."""
    return _stamp(ws, account, ids, "kept_at", "kept_at = '' AND undone_at = ''")


def clear(ws: str, account: str, ids) -> list[str]:
    """Clear the marks of ``account``'s kept changes among ``ids`` (a page's
    Clear marks): the ids cleared now. One that waits for review is not a
    mark to clear: it is kept or undone first."""
    return _stamp(ws, account, ids, "cleared_at", "kept_at != '' AND cleared_at = '' AND undone_at = ''")


def undo(ws: str, change: dict, *, actor: str, force: bool = False, redo: bool = False) -> dict:
    """Take ``change`` back (one ``find`` answered), or with ``redo`` put an
    undone one back, written as ``actor``: ``ai_revert.revert_change``'s
    answer, and the change marked undone, or kept again. Raises
    ``ai_revert.RevertError`` when it cannot (409 with what forcing would
    do, 404 when what it changed is gone)."""
    result = ai_revert.revert_change(ws, change["kind"], change["block_id"], change["revert"], force=force,
                                     redo=redo, actor=actor)
    with connect_pages_db(ws) as conn:
        if redo:
            conn.execute("UPDATE agent_changes SET undone_at = '', kept_at = ?, cleared_at = '' WHERE id = ?",
                         (page_now(), change["id"]))
        else:
            conn.execute("UPDATE agent_changes SET undone_at = ? WHERE id = ? AND undone_at = ''",
                         (page_now(), change["id"]))
        conn.commit()
    if change.get("account"):
        nudge(change["account"], ws, change["page_id"])
    return result


def mark_history(ws: str, account: str, history) -> None:
    """Mark the replayed actions of a conversation's ``history`` that name a
    change of the journal by what the person decided since: ``reverted``
    while it is undone, wherever they undid it (its row, the note, the
    dialog), so the model never builds on a change that is gone. An action
    without a ``change_id``, or whose row is gone, keeps the mark the chat
    saved with it."""
    actions = [a for item in history or () if isinstance(item, dict)
               for a in item.get("actions") or () if isinstance(a, dict) and a.get("change_id")]
    if not actions:
        return
    found = states(ws, account, [a["change_id"] for a in actions[-MAX_IDS:]])
    for a in actions:
        state = found.get(a["change_id"])
        if state:
            a["reverted"] = state == UNDONE


def when(change: dict) -> str:
    """When a change was made, as its lines say it: ``2026-11-20 10:02 UTC``."""
    return change["at"][:16].replace("T", " ") + " UTC"

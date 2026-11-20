"""The changes an MCP connection makes, kept so they can be taken back:
the connection's own ``undo_change`` and ``list_changes`` tools
(gamma/mcp_server.py), and the person's list under the connection's row
in Settings (routers/integrations.py). SDK-free, like ``mcp_export``.

Gamma's chat keeps each change's revert record with its reply, where the
person takes it back; MCP has no reply, so every change a connection
makes with a record (an edit, a paste, a move, a deletion, a drawing, a
page made) is journaled in the workspace's ``agent_changes`` table
(db.PAGES_SCHEMA) under an id its answer names. Taking one back is the
chat's revert (``ai_revert.revert_change``): a merge into the notes as
they are now, which stops (``filled``, ``changed``, forceable) when
someone's work since would be lost. A change is undone once; a redo is
the change made again. The newest KEEP_PER_CONNECTION per connection are
kept, none older than KEEP_DAYS.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import ai_revert
from .db import connect_pages_db, format_stamp, page_now

KEEP_DAYS = 30
KEEP_PER_CONNECTION = 500
# The revert kinds a note or page tool's record is taken back by
# (ai_revert._PLANS; frontend chat/aiRevert.js ``revertKind``).
KINDS = ("edit", "create", "move", "delete", "made")

LIST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "how many, newest first (default 20)"}},
}
LIST_DESCRIPTION = (
    "List the changes this connection made to the notes where it edits, newest first: each one's change_id, "
    "when, what it did and where, and whether it was undone. Every change's own answer names its change_id too; "
    "use this when you no longer have it.")
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
    create_page's ``made``, a note tool's its action's kind."""
    return "made" if tool == "create_page" else action.get("kind") or ""


def record(ws: str, token_id: str, agent: dict, tool: str, action: dict) -> str:
    """Journal one change the connection ``token_id`` made, with the stamp
    it made it under, when its action carries a revert record: its
    change_id, else "" (nothing to take back by)."""
    revert, kind = action.get("revert"), revert_kind(tool, action)
    if not isinstance(revert, dict) or kind not in KINDS or not action.get("block_id"):
        return ""
    change_id = secrets.token_urlsafe(9)
    now = datetime.now(timezone.utc)
    with connect_pages_db(ws) as conn:
        conn.execute("INSERT INTO agent_changes (id, token_id, agent, at, tool, kind, block_id, page_id, summary, revert) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (change_id, token_id, json.dumps(agent or {}), format_stamp(now), tool, kind, action["block_id"],
                      action.get("page_id") or "", action.get("summary") or tool, json.dumps(revert)))
        conn.execute("DELETE FROM agent_changes WHERE at < ?", (format_stamp(now - timedelta(days=KEEP_DAYS)),))
        conn.execute("DELETE FROM agent_changes WHERE token_id = ? AND id NOT IN (SELECT id FROM agent_changes "
                     "WHERE token_id = ? ORDER BY at DESC LIMIT ?)", (token_id, token_id, KEEP_PER_CONNECTION))
        conn.commit()
    return change_id


_COLUMNS = "c.id, c.token_id, c.at, c.tool, c.kind, c.block_id, c.page_id, c.summary, c.revert, c.undone_at, p.content"


def _change(row) -> dict:
    return {"id": row[0], "token_id": row[1], "at": row[2], "tool": row[3], "kind": row[4], "block_id": row[5],
            "page_id": row[6], "summary": row[7], "revert": json.loads(row[8]), "undone": bool(row[9]),
            "page_title": row[10] or ""}


def listed(ws: str, token_id: str, limit: int = 20) -> list[dict]:
    """The connection's changes in ``ws``, newest first, with their page's
    title (empty when the page is gone)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"SELECT {_COLUMNS} FROM agent_changes c LEFT JOIN unified_blocks p ON p.id = c.page_id "
                            "WHERE c.token_id = ? ORDER BY c.at DESC LIMIT ?", (token_id, limit)).fetchall()
    return [_change(r) for r in rows]


def find(ws: str, change_id: str) -> dict | None:
    with connect_pages_db(ws) as conn:
        row = conn.execute(f"SELECT {_COLUMNS} FROM agent_changes c LEFT JOIN unified_blocks p ON p.id = c.page_id "
                           "WHERE c.id = ?", (change_id,)).fetchone()
    return _change(row) if row else None


def undo(ws: str, change: dict, *, actor: str, force: bool = False) -> dict:
    """Take ``change`` back (one ``find`` answered), written as ``actor``:
    ``ai_revert.revert_change``'s answer, and the change marked undone.
    Raises ``ai_revert.RevertError`` when it cannot (409 with what forcing
    would do, 404 when what it changed is gone)."""
    result = ai_revert.revert_change(ws, change["kind"], change["block_id"], change["revert"], force=force,
                                     actor=actor)
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE agent_changes SET undone_at = ? WHERE id = ? AND undone_at = ''", (page_now(), change["id"]))
        conn.commit()
    return result


def when(change: dict) -> str:
    """When a change was made, as its lines say it: ``2026-11-20 10:02 UTC``."""
    return change["at"][:16].replace("T", " ") + " UTC"

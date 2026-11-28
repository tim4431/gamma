"""The changes agents make to a workspace's notes (docs/dev/ai.md
"Reviewing an agent's changes"): Gamma AI's in the chat and an MCP
connection's alike, in the workspace's ``agent_changes`` table
(db.PAGES_SCHEMA). SDK-free, like ``mcp_export``.

Every changing tool's action carries a revert record, the note as it was
before the call and as the call left it (docs/dev/ai_tools.md "Reverting
a note change"). ``ai_tools.registry.run_agent_tool`` journals each one
whose kind ``ai_revert`` takes back (``KINDS``) under an id its action
names (``change_id``), with the state it starts in (``change_state``).
That one row is what every surface reads: the reply's rows in the chat,
the page that draws a suggestion, the connection's Changes in Settings and
MCP's own ``list_changes`` and ``undo_change``.

The account's review mode (``REVIEW_PREF_KEY``, read by ``reviewing``)
decides that state. In Editing, the default, a change is made once the
person allowed it as their permissions say (a card, a client's question,
Allow) and is kept as it is written. In Reviewing, a change Gamma draws
where it lands (``ai_tools.registry.TRACKED_TOOLS``) is made without
asking and is a suggestion (``open``) until the person accepts it
(``kept``) or rejects it (``undone``); every other change is kept, as in
Editing. A suggestion stays one after the account goes back to Editing.
A redo of an undone change keeps it. Undoing is ``ai_revert``'s revert, a
merge into the notes as they are now, which stops (``filled``,
``changed``, ``moved``; forceable) when someone's work since would be
lost. Each write tells the account's open tabs (``{t: "ai_changes"}``) so
every page that draws a suggestion draws it as it is now. Of the decided
changes, the newest KEEP_PER_CONNECTION per account and connection are
kept, none older than KEEP_DAYS; a suggestion stays until it is decided.
"""

import json
import secrets
from datetime import datetime, timedelta, timezone

from . import ai_revert, collab
from .db import connect_pages_db, format_stamp, get_pref, page_now

KEEP_DAYS = 30
KEEP_PER_CONNECTION = 500
# The revert kinds a note or page tool's record is taken back by
# (ai_revert._PLANS; frontend chat/aiRevert.js ``revertKind``).
KINDS = ("edit", "create", "move", "delete", "made")
OPEN, KEPT, UNDONE = "open", "kept", "undone"
# What the model reads after a change made as a suggestion (Reviewing), in
# the chat and over MCP alike (ai_tools/registry.py run_agent_tool).
SUGGESTED = ("It is made, as a suggestion: the user reviews AI changes in Gamma and accepts or rejects it "
             "there. Do not make it again.")
# What the model reads after such a change the journal could not keep: it
# stands as written, with nothing in Gamma to accept or reject.
UNTRACKED = "It is made, but could not be kept as a suggestion: it stands as written. Tell the user."
# The most ids one read of their states or one Accept all names.
MAX_IDS = 500
# The account's review mode (db.USER_PREF_KEYS): true for Reviewing; false
# or unset for Editing. The web app reads and writes it through /api/prefs.
REVIEW_PREF_KEY = "ai-review"

LIST_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 100,
                             "description": "how many, newest first (default 20)"}},
}
LIST_DESCRIPTION = (
    "List the changes this connection made to the notes where it edits, newest first: each one's change_id, "
    "when, what it did and where, and whether it stands, was undone, or is a suggestion the user has yet to "
    "accept or reject in Gamma. Every change's own answer names its change_id too; use this when you no longer "
    "have it.")
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


def reviewing(account: str) -> bool:
    """Whether ``account`` (users.id, as the journal's ``account``) reviews
    its agents' changes: its ``REVIEW_PREF_KEY`` pref is true. An account
    that never chose is Editing, and so is no account (a share link's
    visitor)."""
    return bool(account) and get_pref(account, REVIEW_PREF_KEY)[0] is True


def revert_kind(tool: str, action: dict) -> str:
    """The revert kind a tool's action is taken back by: a page made by
    create_page's ``made``, a highlight by a new block's, a note tool's its
    action's kind."""
    return "made" if tool == "create_page" else "create" if tool == "highlight" else action.get("kind") or ""


def nudge(account: str, ws: str, page_id: str) -> None:
    """Tell the account's open tabs that a change of this page moved on:
    content-free, a tab reads the page's suggestions again over HTTP."""
    collab.publish_account(account, {"t": "ai_changes", "ws": ws, "page_id": page_id})


# A change still waiting for the person: neither kept nor undone.
_UNDECIDED = "kept_at = '' AND undone_at = ''"


def record(ws: str, *, account: str, source: str, token_id: str, agent: dict, tool: str, action: dict,
           kept: bool) -> str:
    """Journal one change ``account``'s agent made (``source`` ``chat`` or
    ``mcp``; ``token_id`` the connection, '' for a person's chat), with the
    stamp it made it under, when its action carries a revert record: its
    change_id, else "" (nothing to take back by). ``kept``: the change is
    kept as it is written; else it is a suggestion that waits for the
    person. A change made for no account (a share link's visitor) is not
    journaled: nobody could decide on it."""
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
        conn.execute(f"DELETE FROM agent_changes WHERE at < ? AND NOT ({_UNDECIDED})",
                     (format_stamp(now - timedelta(days=KEEP_DAYS)),))
        conn.execute(f"DELETE FROM agent_changes WHERE account = ? AND token_id = ? AND NOT ({_UNDECIDED}) AND id "
                     "NOT IN (SELECT id FROM agent_changes WHERE account = ? AND token_id = ? ORDER BY at DESC "
                     "LIMIT ?)", (account, token_id, account, token_id, KEEP_PER_CONNECTION))
        conn.commit()
    nudge(account, ws, page_id)
    return change_id


_COLUMNS = ("c.id, c.token_id, c.at, c.tool, c.kind, c.block_id, c.page_id, c.summary, c.revert, c.undone_at, "
            "p.content, c.account, c.source, c.kept_at, c.agent")


def _state(undone_at: str, kept_at: str) -> str:
    return UNDONE if undone_at else KEPT if kept_at else OPEN


def _change(row) -> dict:
    state = _state(row[9], row[13])
    return {"id": row[0], "token_id": row[1], "at": row[2], "tool": row[3], "kind": row[4], "block_id": row[5],
            "page_id": row[6], "summary": row[7], "revert": json.loads(row[8]), "undone": state == UNDONE,
            "state": state, "page_title": row[10] or "", "account": row[11], "source": row[12],
            "agent": json.loads(row[14] or "{}")}


def _select(where: str) -> str:
    return (f"SELECT {_COLUMNS} FROM agent_changes c LEFT JOIN unified_blocks p ON p.id = c.page_id "
            f"WHERE {where}")


def listed(ws: str, token_id: str, limit: int) -> list[dict]:
    """The connection's changes in ``ws``, newest first, with their page's
    title (empty when the page is gone)."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(_select("c.token_id = ? ORDER BY c.at DESC LIMIT ?"), (token_id, limit)).fetchall()
    return [_change(r) for r in rows]


def suggested_on(ws: str, account: str, page_id: str) -> list[dict]:
    """The suggestions made for ``account`` on the page (its changes that
    are neither kept nor undone), oldest first: what the page draws."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute(_select("c.account = ? AND c.page_id = ? AND c.kept_at = '' AND c.undone_at = '' "
                                    "ORDER BY c.at"), (account, page_id)).fetchall()
    return [_change(r) for r in rows]


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
    return {r[0]: _state(r[1], r[2]) for r in rows}


def find(ws: str, change_id: str) -> dict | None:
    with connect_pages_db(ws) as conn:
        row = conn.execute(_select("c.id = ?"), (change_id,)).fetchone()
    return _change(row) if row else None


def keep(ws: str, account: str, ids) -> list[str]:
    """Accept ``account``'s suggestions among ``ids``, and tell the tabs of
    each page they are on: the ids kept now. One undone or kept already is
    not among them."""
    wanted = _wanted(ids)
    if not account or not wanted:
        return []
    with connect_pages_db(ws) as conn:
        rows = conn.execute(f"SELECT id, page_id FROM agent_changes WHERE account = ? AND {_UNDECIDED} AND id IN "
                            f"({', '.join('?' * len(wanted))})", (account, *wanted)).fetchall()
        if rows:
            conn.execute(f"UPDATE agent_changes SET kept_at = ? WHERE id IN ({', '.join('?' * len(rows))})",
                         (page_now(), *[r[0] for r in rows]))
            conn.commit()
    for page_id in {r[1] for r in rows}:
        nudge(account, ws, page_id)
    return [r[0] for r in rows]


def undo(ws: str, change: dict, *, actor: str, force: bool = False, redo: bool = False) -> dict:
    """Take ``change`` back (one ``find`` answered), or with ``redo`` put an
    undone one back, written as ``actor``: ``ai_revert.revert_change``'s
    answer, and the change marked undone, or kept. Raises
    ``ai_revert.RevertError`` when it cannot (409 with what forcing would
    do, 404 when what it changed is gone)."""
    result = ai_revert.revert_change(ws, change["kind"], change["block_id"], change["revert"], force=force,
                                     redo=redo, actor=actor)
    with connect_pages_db(ws) as conn:
        if redo:
            conn.execute("UPDATE agent_changes SET undone_at = '', kept_at = ? WHERE id = ?",
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
    while it is undone, wherever they undid it (its row, the page, the
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

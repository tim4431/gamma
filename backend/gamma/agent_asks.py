"""The changes an assistant over MCP is asking the person about, drawn in
Gamma where they would land while the question waits (docs/dev/mcp.md
"Asking first"). The question goes to the assistant's client
(elicitation) and, through this registry, to the page in Gamma: the note
or the PDF page draws the change dashed with the card's answers, as a chat
card's is drawn (frontend chat/noteReview.js). The first answer, in either
place, decides; the other is withdrawn. Only the person answers in Gamma,
from a signed-in session: the connection's own token reads no ask and
answers none (routers/agent_changes.py's GET /ai/changes, routers/ai.py's
approvals route).

Each ask is a waiting approval of ``ai_permissions`` (``open_approval``,
answered by ``POST /api/ai/approvals/{id}`` like a chat card), with what
the page needs to draw it: the account, the workspace, the tool, its
permission, the planned change (``ai_tools.approval_preview``) and who
asks. It lives in this process's memory while the question waits: a
restart drops it, and the call it belonged to with it. Each change of the
list tells the account's open tabs to read their page's changes and asks
again (``agent_changes.nudge``).
"""

import threading

from . import agent_changes, ai_permissions
from .ai_tools.registry import tool_permission

_asks: dict[str, dict] = {}  # approval id -> {user, ws, page_id, tool, perm, preview, agent}
_lock = threading.Lock()


def ask(user: str, ws: str, preview: dict, *, tool: str, agent: str) -> str | None:
    """Ask the account ``user`` in Gamma about the change ``preview`` plans,
    drawn where the change is drawn: the page it lands on
    (``preview["page_id"]``), or for a move to another page the page the
    note is on now (``src_page_id``). The id of the approval it waits on
    (``ai_permissions.wait_for``), or None with nothing opened: a change no
    page draws (a page made), or an account with too many approvals
    waiting. The client then asks alone."""
    page_id = str(preview.get("src_page_id") or preview.get("page_id") or "")
    approval_id = ai_permissions.open_approval(user) if page_id else None
    if not approval_id:
        return None
    with _lock:
        _asks[approval_id] = {"user": user, "ws": ws, "page_id": page_id, "tool": tool,
                              "perm": tool_permission(tool), "preview": preview, "agent": agent}
    agent_changes.nudge(user, ws, page_id)
    return approval_id


def settle(approval_id: str) -> None:
    """The question was answered, withdrawn or timed out: no longer drawn,
    and its approval gone, so a late answer finds nothing to decide."""
    with _lock:
        drawn = _asks.pop(approval_id, None)
    ai_permissions.wait_for(approval_id, timeout=0)
    if drawn:
        agent_changes.nudge(drawn["user"], drawn["ws"], drawn["page_id"])


def holds(approval_id: str) -> bool:
    """Whether ``approval_id`` is a question of an assistant over MCP that
    waits here, which only the person answers in Gamma."""
    with _lock:
        return approval_id in _asks


def on_page(user: str, ws: str, page_id: str) -> list[dict]:
    """The questions waiting for ``user`` that are drawn on the page, as the
    page draws them: ``[{id, tool, perm, preview, agent}]``."""
    with _lock:
        return [{"id": approval_id, **{k: waiting[k] for k in ("tool", "perm", "preview", "agent")}}
                for approval_id, waiting in _asks.items()
                if waiting["user"] == user and waiting["ws"] == ws and waiting["page_id"] == page_id]

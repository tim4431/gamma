"""The changes an assistant over MCP is asking the person about, drawn in
Gamma where they would land while the question waits (docs/dev/mcp.md
"Asking first"). The question goes to the assistant's client
(elicitation) and, through this registry, to the page in Gamma: the note
or the PDF page draws the change dashed with the card's answers, as a chat
card's is drawn (frontend chat/noteReview.js). The first answer, in either
place, decides; the other is withdrawn. Only the person answers in Gamma,
from a signed-in session: the connection's own token reads no ask and
answers none (routers/ai.py).

Each entry is a waiting approval of ``ai_permissions`` (``open_approval``,
answered by ``POST /api/ai/approvals/{id}`` like a chat card), with what
the page needs to draw it: the account, the workspace, the tool, its
permission, the planned change (``ai_tools.approval_preview``) and who
asks. It lives in this process's memory while the question waits: a
restart drops it, and the call it belonged to with it. Each change of the
list tells the account's open tabs to read their page's changes and asks
again (``agent_changes.nudge``).
"""

import threading

from . import agent_changes

_asks: dict[str, dict] = {}  # approval id -> {user, ws, page_id, tool, perm, preview, agent}
_lock = threading.Lock()


def show(approval_id: str, *, user: str, ws: str, tool: str, perm: str, preview: dict, agent: str) -> None:
    """Draw the question ``approval_id`` waits on where its change is drawn,
    for the account ``user``: the page the change lands on
    (``preview["page_id"]``), or for a move to another page the page the
    note is on now (``src_page_id``), where the move is drawn. A preview
    without a page draws nowhere."""
    preview = preview or {}
    page_id = str(preview.get("src_page_id") or preview.get("page_id") or "")
    if not approval_id or not page_id:
        return
    with _lock:
        _asks[approval_id] = {"user": user, "ws": ws, "page_id": page_id, "tool": tool, "perm": perm,
                              "preview": preview, "agent": agent}
    agent_changes.nudge(user, ws, page_id)


def settle(approval_id: str) -> None:
    """The question was answered, withdrawn or timed out: no longer drawn."""
    with _lock:
        ask = _asks.pop(approval_id, None)
    if ask:
        agent_changes.nudge(ask["user"], ask["ws"], ask["page_id"])


def holds(approval_id: str) -> bool:
    """Whether ``approval_id`` is a question of an assistant over MCP that
    waits here, which only the person answers in Gamma."""
    with _lock:
        return approval_id in _asks


def on_page(user: str, ws: str, page_id: str) -> list[dict]:
    """The questions waiting for ``user`` that are drawn on the page, as the
    page draws them: ``[{id, tool, perm, preview, agent}]``."""
    with _lock:
        return [{"id": approval_id, **{k: ask[k] for k in ("tool", "perm", "preview", "agent")}}
                for approval_id, ask in _asks.items()
                if ask["user"] == user and ask["ws"] == ws and ask["page_id"] == page_id]

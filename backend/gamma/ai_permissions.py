"""What the chat's tools may do without asking, and the approvals the chat
waits for when a tool needs the user's answer first.

Every tool belongs to one permission (the ``perm`` of its entry in
``ai_tools.TOOLS``), and a chat request carries a state per permission
(``AIChatRequest.permissions``, Settings → AI → Chat → Tools):

- ``"allow"``: the tool runs whenever the model calls it.
- ``"ask"``: the tool is offered, but each call waits for the user's decision
  on an approval card in the chat (:func:`open_approval` / :func:`wait_for`
  here; ``POST /api/ai/approvals/{id}`` delivers the answer).
- ``"off"``: the tool is not offered, and a call is refused.

A permission the request leaves out takes its default: reading is allowed,
changing asks. ``true`` / ``false``, the values an older tab sends, mean
allow / off.

This is the user's guard over their own chat, not access control. What a
request may change at all is decided before any of this: by the workspace
role (``auth.can_write``) and by the tool's scope.

A waiting approval lives in memory for as long as its chat stream waits,
bound to the account that opened it. A decision, the timeout, or the stream
closing ends it; a restart forgets it, and the call is then not made.
"""

import secrets
import threading
import time

STATES = ("allow", "ask", "off")
# What the user may answer on an approval card: run this call; run it and
# stop asking for this permission in this conversation; run it and stop
# asking in Settings too (the client stores that); do not run it.
DECISIONS = ("once", "chat", "always", "deny")
ALLOWING = frozenset({"once", "chat", "always"})
APPROVAL_TIMEOUT = 600.0   # seconds a card waits before its call counts as declined
MAX_PENDING_PER_USER = 20  # approvals waiting at once per account (one per chat stream)
MAX_NOTE = 2000            # chars of what the user says to do instead, with a decline
_POLL = 0.5                # seconds between checks of the stream while a card waits

_pending: dict[str, dict] = {}   # approval id -> {"user", "decided" (Event), "decision", "note"}
_lock = threading.Lock()


def permission_state(perms, key: str, changes: bool) -> str:
    """The state of permission ``key`` in a request's map ``perms``: its value
    when it is a state (or a pre-Ask boolean), else the default, which is
    ask for a permission whose tools change something (``changes``) and
    allow for a reading one."""
    value = perms.get(key) if isinstance(perms, dict) else None
    if value is True:
        return "allow"
    if value is False:
        return "off"
    if value in STATES:
        return value
    return "ask" if changes else "allow"


def open_approval(user: str) -> str | None:
    """A new waiting approval of ``user``'s: its id, or None when the account
    already has too many waiting (the call then counts as not answered)."""
    with _lock:
        if sum(1 for a in _pending.values() if a["user"] == user) >= MAX_PENDING_PER_USER:
            return None
        approval_id = secrets.token_urlsafe(18)
        _pending[approval_id] = {"user": user, "decided": threading.Event(), "decision": None, "note": ""}
        return approval_id


def answer(approval_id: str, user: str, decision: str, note: str = "") -> bool:
    """Record ``user``'s decision on their waiting approval, and with a
    decline what they want done instead (``note``, kept for "deny" only).
    False when none of theirs waits under that id: answered already, timed
    out, its stream closed, or never theirs."""
    if decision not in DECISIONS:
        return False
    with _lock:
        approval = _pending.get(approval_id)
        if not approval or approval["user"] != user or approval["decision"]:
            return False
        approval["decision"] = decision
        approval["note"] = str(note or "").strip()[:MAX_NOTE] if decision == "deny" else ""
        approval["decided"].set()
        return True


def wait_for(approval_id: str, stopped: threading.Event | None = None,
             timeout: float | None = None) -> tuple[str, str]:
    """Block until the approval is answered. Returns ``(decision, note)``:
    the decision and, with a decline, what the user wants done instead;
    ``"stopped"`` once ``stopped`` is set (the chat's stream closed: nothing
    may run for it any more, even a call allowed a moment earlier), or
    ``"expired"`` when ``timeout`` (default :data:`APPROVAL_TIMEOUT`) passes
    unanswered. The approval is gone afterwards, so a late answer finds
    nothing to decide."""
    with _lock:
        approval = _pending.get(approval_id)
    if approval is None:
        return "expired", ""
    deadline = time.monotonic() + (APPROVAL_TIMEOUT if timeout is None else timeout)
    while not approval["decided"].is_set():
        left = deadline - time.monotonic()
        if left <= 0 or (stopped is not None and stopped.is_set()):
            break
        approval["decided"].wait(min(left, _POLL))
    with _lock:
        _pending.pop(approval_id, None)
        decision, note = approval["decision"], approval["note"]
    if stopped is not None and stopped.is_set():
        return "stopped", ""
    return (decision, note) if decision else ("expired", "")

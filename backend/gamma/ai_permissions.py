"""AI tool policy and single-use approvals, scoped to one live chat request.

The browser supplies preferences; only a decision on a server-created request
can release an ``ask`` call. Approvals never replace workspace or tool scope
checks. Pending requests live only while their stream is alive.
"""

import json
import secrets
import threading
import time
from dataclasses import dataclass, field

from fastapi import HTTPException


DEFAULT_PERMISSIONS = {
    "list": "allow", "read": "allow", "block_read": "allow", "view": "allow",
    "search": "allow", "web_search": "allow", "web_read": "allow",
    "rename": "ask", "move": "ask", "block_edit": "ask", "publisher_cookies": "ask",
}
APPROVAL_TIMEOUT = 300.0
MAX_PENDING_PER_USER = 20
DECISIONS = ("allow_once", "allow_always", "deny")


def permission_state(permissions, permission: str) -> str:
    """Read legacy booleans without widening denials; malformed values deny."""
    if permission not in DEFAULT_PERMISSIONS:
        return "deny"
    if not isinstance(permissions, dict) or permission not in permissions:
        return DEFAULT_PERMISSIONS.get(permission, "deny")
    value = permissions[permission]
    if value is True:
        return "allow"
    if value is False:
        return "deny"
    return value if isinstance(value, str) and value in ("allow", "ask", "deny") else "deny"


def _arguments(args: dict) -> str:
    return json.dumps(args, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


@dataclass
class Approval:
    id: str
    run: "AccessRun"
    tool: str
    permission: str
    arguments: str
    summary: str
    target: dict
    expires_at: float
    ready: threading.Event = field(default_factory=threading.Event)
    decision: str | None = None

    def event(self) -> dict:
        # Never share the mutable argument object with the caller or the UI.
        return {"id": self.id, "tool": self.tool, "permission": self.permission,
                "args": json.loads(self.arguments), "summary": self.summary,
                "target": dict(self.target), "expires_at": self.expires_at}


_lock = threading.RLock()
_pending: dict[str, Approval] = {}


class AccessRun:
    """Trusted per-request policy plus grants bound to exact tool arguments."""

    def __init__(self, user: str, workspace: str, permissions: dict):
        self.user, self.workspace = user, workspace
        self.permissions = {key: permission_state(permissions, key) for key in DEFAULT_PERMISSIONS}
        self.cancelled = threading.Event()
        self._always: set[str] = set()
        self._once: set[tuple[str, str, str]] = set()

    def state(self, permission: str) -> str:
        with _lock:
            state = permission_state(self.permissions, permission)
            return "allow" if state == "ask" and permission in self._always else state

    def request(self, tool: str, args: dict, permission: str, metadata: dict) -> Approval:
        with _lock:
            if self.cancelled.is_set():
                raise RuntimeError("chat stopped")
            if self.state(permission) != "ask":
                raise RuntimeError("this tool does not need approval")
            if sum(p.run.user == self.user for p in _pending.values()) >= MAX_PENDING_PER_USER:
                raise RuntimeError("too many permission requests; finish another chat first")
            approval = Approval(secrets.token_urlsafe(24), self, tool, permission, _arguments(args),
                                metadata["summary"], dict(metadata.get("target") or {}),
                                time.time() + APPROVAL_TIMEOUT)
            _pending[approval.id] = approval
            return approval

    def wait(self, approval: Approval) -> str:
        with _lock:
            if approval.run is not self or _pending.get(approval.id) is not approval:
                return "deny"
        try:
            approval.ready.wait(max(0, approval.expires_at - time.time()))
            with _lock:
                decision = approval.decision or "expired"
                if self.cancelled.is_set():
                    decision = "deny"
                if decision == "allow_always":
                    self._always.add(approval.permission)
                elif decision == "allow_once":
                    self._once.add((approval.tool, approval.arguments, approval.permission))
                return decision
        finally:
            with _lock:
                _pending.pop(approval.id, None)

    def authorize(self, workspace: str, user: str, tool: str, args: dict, permission: str) -> bool:
        """Consume a one-call grant only at the actual dispatch boundary."""
        with _lock:
            if self.cancelled.is_set() or (workspace, user) != (self.workspace, self.user):
                return False
            state = self.state(permission)
            if state != "ask":
                return state == "allow"
            key = (tool, _arguments(args), permission)
            if key not in self._once:
                return False
            self._once.remove(key)
            return True

    def close(self):
        """Wake every pending wait before a disconnected stream can do work."""
        with _lock:
            self.cancelled.set()
            self._once.clear()
            self._always.clear()
            for ident, approval in list(_pending.items()):
                if approval.run is self:
                    approval.decision = "deny"
                    approval.ready.set()
                    del _pending[ident]


def resolve(ident: str, user: str, workspace: str, decision: str, *, can_write: bool) -> dict:
    """Owner-only, atomic resolution. A repeated or expired id is unusable."""
    with _lock:
        approval = _pending.get(ident)
        if approval is None or (approval.run.user, approval.run.workspace) != (user, workspace):
            raise HTTPException(404, "Permission request not found or no longer active")
        if approval.decision is not None or approval.run.cancelled.is_set():
            raise HTTPException(409, "Permission request already resolved")
        if time.time() >= approval.expires_at:
            approval.decision = "expired"
            approval.ready.set()
            raise HTTPException(409, "Permission request expired")
        if decision not in DECISIONS:
            raise HTTPException(422, "Invalid permission decision")
        if not can_write and approval.permission in ("rename", "move", "block_edit"):
            raise HTTPException(403, "You can only view this workspace")
        approval.decision = decision
        approval.ready.set()
        return {"permission": approval.permission, "decision": decision}

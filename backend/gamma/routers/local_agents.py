"""The agents on this computer (gamma/local_agents/, docs/dev/local_agents.md
"The routes"): which of Codex CLI and Claude Code the desktop app's own
server finds here and whether each is ready, for the sync panel's agent row.
Only a server that runs agents answers (``local_agents.enabled``); any other
answers 404. A signed-in account's session only: an integration token and
the guest are refused, as on the folder-links routes."""

import threading
import time

from fastapi import APIRouter, HTTPException, Request

from .. import local_agents
from ..auth import require_personal_user_id

router = APIRouter(prefix="/api/local-agents", tags=["local-agents"])

FRESH_EVERY_S = 10   # a re-probe asked for runs at most this often on this server; else the kept answers

_fresh_lock = threading.Lock()
_fresh_at = 0.0       # when the last re-probe asked for began (monotonic)


@router.get("")
def list_agents(request: Request, fresh: int = 0):
    """``{agents: [{name, label, found, version, ready, problem, at}]}``,
    Codex then Claude Code, as last asked (``catalog.cached``: nothing runs,
    and an answer missing or stale is asked in the background, ``found``
    and ``ready`` null meanwhile). ``fresh=1`` asks each agent anew
    (``--version`` and its status command), at most once every
    ``FRESH_EVERY_S`` on this server; sooner, the kept answers."""
    global _fresh_at
    from ..local_agents import catalog

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    require_personal_user_id(request, "Sign in with an account to see the agents on this computer.")
    if fresh:
        with _fresh_lock:
            now = time.monotonic()
            due = now - _fresh_at >= FRESH_EVERY_S or not _fresh_at
            if due:
                _fresh_at = now
        if due:
            return {"agents": catalog.probes(fresh=True)}
    return {"agents": [catalog.cached(name) for name in catalog.AGENTS]}


@router.get("/waiting")
def waiting_sends(request: Request, link: str):
    """``{waiting: n}``: how many Sends of the vault ``link``'s work folder
    an agent turned on now would take (``runner.waiting``, the rule it
    takes them by), for the question the sync panel asks first. The vault's
    maker only; 404 for no such vault or one of another account."""
    from .. import folder_links
    from ..local_agents import runner

    if not local_agents.enabled():
        raise HTTPException(status_code=404, detail="Agents run on the desktop app's own server.")
    user_id = require_personal_user_id(request, "Sign in with an account to see the agents on this computer.")
    found = folder_links.get_link(link)
    if found is None or found["created_by"] != user_id or not folder_links.work_folder_link(found):
        raise HTTPException(status_code=404, detail="no such work folder on this computer")
    return {"waiting": len(runner.waiting(found["workspace_id"], found))}

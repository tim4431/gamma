"""What this computer keeps of its agents, in vaults.db (``db.VAULTS_SCHEMA``,
never migrated): the agent each work folder's vault answers its Sends with
(``agent_folders``, one row a link; none for no agent) and one row a run
(``agent_runs``): which request it took, the agent's session, its state
(``running`` until it ends ``done``, ``failed`` or ``stopped``), the last
progress it showed, the source files it changed, whether the build changed,
its steps and its process.

A run's ``error`` is this computer's record of a failure, the agent's whole
error and its stderr's tail: it may name the user's paths and is never shown
to the workspace's members (their request carries a sentence of the
runner's). A removed vault forgets its agent (``vault.store.forget_link``);
``forget_links`` drops the rows of links that are gone some other way.

Every function opens its own short connection and commits before it returns,
as gamma/vault/store.py does.
"""

import json

from ..blocks_store import new_block_id
from ..db import connect_vaults_db, page_now

MAX_STEPS = 200        # steps a run keeps
MAX_CHANGES = 200      # source files a run keeps as changed
RUN_STATES = ("running", "done", "failed", "stopped")

_RUN_COLUMNS = ("id", "ws", "link_id", "request_id", "agent", "session", "state", "started_at", "ended_at", "detail",
                "error", "changes", "built", "steps", "pid")
_RUN_SELECT = f"SELECT {', '.join(_RUN_COLUMNS)} FROM agent_runs"
_UPDATABLE = ("session", "pid")


def _run(values) -> dict:
    run = dict(zip(_RUN_COLUMNS, values))
    for key in ("changes", "steps"):
        try:
            run[key] = json.loads(run[key] or "[]")
        except ValueError:
            run[key] = []
    run["built"] = bool(run["built"])
    return run


# --- the folders' agents ---------------------------------------------------------------

def agent_of(link_id: str) -> dict | None:
    """``{agent, set_by, set_at}`` of the vault ``link_id``, None for no
    agent."""
    return agents_of([link_id]).get(link_id)


def agents_of(link_ids) -> dict[str, dict]:
    """``{link id: {agent, set_by, set_at}}`` of those of ``link_ids`` that
    have an agent, in one query (a listing's)."""
    ids = sorted(set(link_ids))
    if not ids:
        return {}
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT link_id, agent, set_by, set_at FROM agent_folders "
                            "WHERE link_id IN (SELECT value FROM json_each(?))", (json.dumps(ids),)).fetchall()
    return {link_id: {"agent": agent, "set_by": set_by, "set_at": set_at} for link_id, agent, set_by, set_at in rows}


def set_agent(link_id: str, agent: str, *, by: str) -> None:
    """The vault ``link_id`` answers its Sends with ``agent`` from now on,
    turned on by the account ``by``; ``""`` turns it off (the row goes).
    The same agent again keeps when it was first turned on."""
    with connect_vaults_db() as conn:
        if not agent:
            conn.execute("DELETE FROM agent_folders WHERE link_id = ?", (link_id,))
            return
        conn.execute("INSERT INTO agent_folders (link_id, agent, set_by, set_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(link_id) DO UPDATE SET agent = excluded.agent, set_by = excluded.set_by, "
                     "set_at = excluded.set_at WHERE agent_folders.agent != excluded.agent",
                     (link_id, agent, by or "", page_now()))


def forget_links(live_ids: set[str]) -> int:
    """The agents of the links not in ``live_ids`` forgotten (a vault gone
    while this server was not looking) → how many."""
    with connect_vaults_db() as conn:
        return conn.execute("DELETE FROM agent_folders WHERE link_id NOT IN (SELECT value FROM json_each(?))",
                            (json.dumps(sorted(live_ids)),)).rowcount


# --- the runs ------------------------------------------------------------------------------

def start_run(*, ws: str, link_id: str, request_id: str, agent: str) -> str:
    """A run of ``agent`` on the request ``request_id`` begins → its id."""
    run_id = new_block_id()
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_runs (id, ws, link_id, request_id, agent, state, started_at) "
                     "VALUES (?, ?, ?, ?, ?, 'running', ?)", (run_id, ws, link_id, request_id, agent, page_now()))
    return run_id


def update_run(run_id: str, **fields) -> None:
    """What a run learns while it works: its ``session``, its ``pid``.
    ValueError for another field."""
    keys = list(fields)
    if not keys:
        return
    if not set(keys) <= set(_UPDATABLE):
        raise ValueError(f"no such fields: {sorted(set(keys) - set(_UPDATABLE))}")
    with connect_vaults_db() as conn:
        conn.execute(f"UPDATE agent_runs SET {', '.join(f'{k} = ?' for k in keys)} WHERE id = ?",
                     (*(fields[k] for k in keys), run_id))


def end_run(run_id: str, *, state: str, detail: str = "", error: str = "", session: str = "", changes=(),
            built: bool = False, steps=(), pid: int = 0) -> None:
    """The run ends in ``state`` (done, failed, stopped): what it showed
    last, this computer's record of why it failed, the source files it
    changed (``MAX_CHANGES``), whether the build changed and its steps
    (``MAX_STEPS``). A ``session`` or ``pid`` left empty keeps the one it
    had. A run that ended already stays as it ended."""
    if state not in RUN_STATES[1:]:
        raise ValueError(f"a run ends {', '.join(RUN_STATES[1:])}")
    with connect_vaults_db() as conn:
        conn.execute("UPDATE agent_runs SET state = ?, ended_at = ?, detail = ?, error = ?, "
                     "session = CASE WHEN ? != '' THEN ? ELSE session END, changes = ?, built = ?, steps = ?, "
                     "pid = CASE WHEN ? != 0 THEN ? ELSE pid END WHERE id = ? AND ended_at IS NULL",
                     (state, page_now(), detail or "", error or "", session or "", session or "",
                      json.dumps(list(changes)[:MAX_CHANGES]), int(bool(built)), json.dumps(list(steps)[:MAX_STEPS]),
                      int(pid or 0), int(pid or 0), run_id))


def get_run(run_id: str) -> dict | None:
    with connect_vaults_db() as conn:
        row = conn.execute(f"{_RUN_SELECT} WHERE id = ?", (run_id,)).fetchone()
    return _run(row) if row else None


def open_runs() -> list[dict]:
    """The runs that have not ended, oldest first: at startup, those a
    server that stopped left behind."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_RUN_SELECT} WHERE ended_at IS NULL ORDER BY started_at").fetchall()
    return [_run(row) for row in rows]


def run_of(ws: str, request_id: str) -> dict | None:
    """The newest run of the request, None for none."""
    with connect_vaults_db() as conn:
        row = conn.execute(f"{_RUN_SELECT} WHERE ws = ? AND request_id = ? ORDER BY started_at DESC, rowid DESC "
                           "LIMIT 1", (ws, request_id)).fetchone()
    return _run(row) if row else None

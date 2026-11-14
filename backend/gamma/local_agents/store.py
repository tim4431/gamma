"""What this computer keeps of its agents, in vaults.db (``db.VAULTS_SCHEMA``,
never migrated): the agent each work folder's vault answers its Sends with
(``agent_folders``, one row a link; none for no agent), one row a run
(``agent_runs``): which request it took, the agent's session, its state
(``running`` until it ends ``done``, ``failed`` or ``stopped``), the last
progress it showed, the source files it changed, whether the build changed,
its steps and its process; and one row a thread an agent here answered last
(``agent_threads``): the agent, its session and the directory it ran in,
when the run read the thread, the reply it wrote and the request, so that a
follow-up in the thread resumes that session. For the chat's computer side
(gamma/local_agents/chats.py): one row a conversation (``agent_conversations``),
the session its agent ran in last and the directory, what its next turn
resumes, and one row a turn (``agent_turns``), its state and process (a
turn of a page of another server, claimed there, keyed by its vault in
place of the workspace: ``claimed_turn``). What
a vault's agent may run (``agent_policies``, one row a link; none for
``never``): ``ask``, its chat's turns may ask the person; and the commands
a person allowed in a conversation (``agent_grants``), each exactly as it
was asked, with the directory it runs in and the network host it
reaches. Both are consent given for one directory and one agent: they go
when the agent is turned off or changed (``set_agent``), the vault relinked
(``forget_threads``) or removed, and a grant with its conversation.

A run's or a turn's ``error`` is this computer's record of a failure, the
agent's whole error and its stderr's tail: it may name the user's paths and
is never shown to the workspace's members (their request carries a sentence
of the runner's) nor written in the conversation. A removed vault forgets
its agent, its threads and its conversations' sessions and turns
(``vault.store.forget_link``), a relinked one its threads and sessions
(``forget_threads``); ``forget_links`` drops the rows of links that are gone
some other way.

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
    The same agent again keeps when it was first turned on; turned off or
    changed, what it may run and the grants of its chats go."""
    with connect_vaults_db() as conn:
        had = conn.execute("SELECT agent FROM agent_folders WHERE link_id = ?", (link_id,)).fetchone()
        if not had or had[0] != agent:
            _forget_consent(conn, link_id)
        if not agent:
            conn.execute("DELETE FROM agent_folders WHERE link_id = ?", (link_id,))
            return
        conn.execute("INSERT INTO agent_folders (link_id, agent, set_by, set_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(link_id) DO UPDATE SET agent = excluded.agent, set_by = excluded.set_by, "
                     "set_at = excluded.set_at WHERE agent_folders.agent != excluded.agent",
                     (link_id, agent, by or "", page_now()))


def _forget_consent(conn, link_id: str) -> None:
    """What the vault's agent may run and the commands allowed in its
    chats, forgotten. Inside the caller's connection."""
    conn.execute("DELETE FROM agent_policies WHERE link_id = ?", (link_id,))
    conn.execute("DELETE FROM agent_grants WHERE link_id = ?", (link_id,))


def forget_links(live_ids: set[str]) -> int:
    """The agents of the links not in ``live_ids`` forgotten, and their
    threads, conversations, policies, grants and ended turns (a vault gone
    while this server was not looking) → how many agents."""
    live = json.dumps(sorted(live_ids))
    with connect_vaults_db() as conn:
        for table in ("agent_threads", "agent_conversations", "agent_policies", "agent_grants"):
            conn.execute(f"DELETE FROM {table} WHERE link_id NOT IN (SELECT value FROM json_each(?))", (live,))
        conn.execute("DELETE FROM agent_turns WHERE ended_at IS NOT NULL "
                     "AND link_id NOT IN (SELECT value FROM json_each(?))", (live,))
        return conn.execute("DELETE FROM agent_folders WHERE link_id NOT IN (SELECT value FROM json_each(?))",
                            (live,)).rowcount


# --- the threads answered ---------------------------------------------------------------

_THREAD_COLUMNS = ("thread_id", "agent", "session", "cwd", "seen_at", "read_ids", "comment_id", "request_id", "at")


def answered_thread(link_id: str, thread_id: str, *, agent: str, session: str, cwd: str, seen_at: str,
                    read: list[str], comment_id: str, request_id: str) -> None:
    """The agent ``agent`` of the vault ``link_id`` answered the thread with
    the reply ``comment_id``, in the session ``session`` run in ``cwd``, on
    the request ``request_id``, having read the thread's comments ``read``
    at ``seen_at``: the thread's row, in place of the one it had."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_threads (link_id, thread_id, agent, session, cwd, seen_at, read_ids, "
                     "comment_id, request_id, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
                     "ON CONFLICT(link_id, thread_id) DO UPDATE SET agent = excluded.agent, "
                     "session = excluded.session, cwd = excluded.cwd, seen_at = excluded.seen_at, "
                     "read_ids = excluded.read_ids, comment_id = excluded.comment_id, "
                     "request_id = excluded.request_id, at = excluded.at",
                     (link_id, thread_id, agent, session or "", cwd or "", seen_at or "", json.dumps(list(read or [])),
                      comment_id or "", request_id or "", page_now()))


def threads_of(link_id: str, thread_ids) -> dict[str, dict]:
    """``{thread id: {thread_id, agent, session, cwd, seen_at, read (the
    ids of the comments the run read), comment_id, request_id, at}}`` of
    those of ``thread_ids`` an agent of the vault ``link_id`` answered, in
    one query."""
    ids = sorted(set(thread_ids))
    if not ids:
        return {}
    with connect_vaults_db() as conn:
        rows = conn.execute(f"SELECT {', '.join(_THREAD_COLUMNS)} FROM agent_threads WHERE link_id = ? "
                            "AND thread_id IN (SELECT value FROM json_each(?))", (link_id, json.dumps(ids))).fetchall()
    out = {}
    for row in rows:
        found = dict(zip(_THREAD_COLUMNS, row))
        found["read"] = _ids(found.pop("read_ids"))
        out[row[0]] = found
    return out


def _ids(raw: str) -> list[str]:
    """A stored JSON list of ids; [] for anything else."""
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def forget_threads(link_id: str) -> None:
    """The threads the vault's agent answered forgotten, the sessions of
    its conversations, what it may run and their grants: its directory
    moved (a session, and consent, belong to the directory)."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_threads WHERE link_id = ?", (link_id,))
        conn.execute("DELETE FROM agent_conversations WHERE link_id = ?", (link_id,))
        _forget_consent(conn, link_id)


# --- what an agent may run ------------------------------------------------------------------

RUNS = ("never", "ask")   # never: as it always did; ask: its chat's turns may ask the person


def runs_of(link_id: str) -> str:
    """What the vault's agent may run: ``ask`` or ``never``."""
    return policies_of([link_id]).get(link_id, "never")


def policies_of(link_ids) -> dict[str, str]:
    """``{link id: runs}`` of ``link_ids``, ``never`` for those with no row,
    in one query (a listing's)."""
    ids = sorted(set(link_ids))
    if not ids:
        return {}
    with connect_vaults_db() as conn:
        rows = dict(conn.execute("SELECT link_id, runs FROM agent_policies WHERE link_id IN "
                                 "(SELECT value FROM json_each(?))", (json.dumps(ids),)).fetchall())
    return {link_id: rows.get(link_id) if rows.get(link_id) in RUNS else "never" for link_id in ids}


def set_runs(link_id: str, runs: str, *, by: str) -> None:
    """What the vault's agent may run, set by the account ``by``: ``never``
    takes the row away, and the commands allowed in its chats with it.
    ValueError for another value."""
    if runs not in RUNS:
        raise ValueError(f"an agent may run {' or '.join(RUNS)}")
    with connect_vaults_db() as conn:
        if runs == "never":
            _forget_consent(conn, link_id)
            return
        conn.execute("INSERT INTO agent_policies (link_id, runs, set_by, set_at) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT(link_id) DO UPDATE SET runs = excluded.runs, set_by = excluded.set_by, "
                     "set_at = excluded.set_at WHERE agent_policies.runs != excluded.runs",
                     (link_id, runs, by or "", page_now()))


def grant(conversation_id: str, command: str, *, link_id: str, cwd: str = ".", host: str = "") -> None:
    """The person allowed ``command`` in the conversation (Allow in this
    chat), run in ``cwd`` (as the runner keys it: ``.`` the folder) and
    reaching the network ``host`` ("" for none): its next ask, exactly this
    command there and to that host, is answered without asking."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_grants (conversation_id, command, cwd, host, link_id, at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, command, cwd, host) DO NOTHING",
                     (conversation_id, command.strip(), cwd or ".", (host or "").strip(), link_id, page_now()))


def granted(conversation_id: str, command: str, *, link_id: str, cwd: str = ".", host: str = "") -> bool:
    """Whether the person allowed exactly ``command`` (whitespace around it
    aside) in ``cwd`` to the network ``host`` in the conversation, in the
    vault ``link_id``. Never a prefix, never another directory or host."""
    with connect_vaults_db() as conn:
        return conn.execute("SELECT 1 FROM agent_grants WHERE conversation_id = ? AND command = ? AND cwd = ? "
                            "AND host = ? AND link_id = ?",
                            (conversation_id, command.strip(), cwd or ".", (host or "").strip(),
                             link_id)).fetchone() is not None


def grants_of(conversation_id: str, *, link_id: str) -> list[dict]:
    """What the person allowed in the conversation, in the vault
    ``link_id``: ``[{command, cwd, host}]``, oldest first (the composer's
    Allowed in this chat chip)."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT command, cwd, host FROM agent_grants WHERE conversation_id = ? AND link_id = ? "
                            "ORDER BY at, command", (conversation_id, link_id)).fetchall()
    return [{"command": r[0], "cwd": r[1], "host": r[2]} for r in rows]


def forget_grants(conversation_id: str) -> None:
    """The conversation's grants taken back: each command asks again."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_grants WHERE conversation_id = ?", (conversation_id,))


# --- the runs ------------------------------------------------------------------------------

def start_run(*, ws: str, link_id: str, request_id: str, agent: str, run_id: str = "") -> str:
    """A run of ``agent`` on the request ``request_id`` begins → its id
    (``run_id``, one the run minted before it claimed the request of
    another server, else a new one)."""
    run_id = run_id or new_block_id()
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


# --- the chat's conversations and turns ------------------------------------------------------

TURN_STATES = ("queued", "running", "done", "failed", "stopped")
_TURN_COLUMNS = ("id", "ws", "link_id", "conversation_id", "page_id", "agent", "state", "started_at", "ended_at",
                 "error", "pid")
_TURN_SELECT = f"SELECT {', '.join(_TURN_COLUMNS)} FROM agent_turns"


def conversation_of(conversation_id: str) -> dict | None:
    """``{conversation_id, link_id, agent, session, cwd, at}`` of the
    conversation's last session, None for none."""
    with connect_vaults_db() as conn:
        row = conn.execute("SELECT conversation_id, link_id, agent, session, cwd, at FROM agent_conversations "
                           "WHERE conversation_id = ?", (conversation_id,)).fetchone()
    return dict(zip(("conversation_id", "link_id", "agent", "session", "cwd", "at"), row)) if row else None


def ran_conversation(conversation_id: str, *, link_id: str, agent: str, session: str, cwd: str) -> None:
    """The conversation's agent ran in ``session`` in ``cwd``: its row, in
    place of the one it had."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_conversations (conversation_id, link_id, agent, session, cwd, at) "
                     "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id) DO UPDATE SET link_id = excluded.link_id, "
                     "agent = excluded.agent, session = excluded.session, cwd = excluded.cwd, at = excluded.at",
                     (conversation_id, link_id, agent, session or "", cwd or "", page_now()))


def forget_conversation(conversation_id: str) -> None:
    """A deleted conversation's session and grants forgotten."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_conversations WHERE conversation_id = ?", (conversation_id,))
        conn.execute("DELETE FROM agent_grants WHERE conversation_id = ?", (conversation_id,))


def add_turn(turn_id: str, *, ws: str, link_id: str, conversation_id: str, page_id: str, agent: str) -> None:
    """A turn of the conversation waits for the agent (``queued``)."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_turns (id, ws, link_id, conversation_id, page_id, agent, state, started_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?)",
                     (turn_id, ws, link_id, conversation_id, page_id, agent, page_now()))


def claimed_turn(turn_id: str, *, link_id: str, conversation_id: str, page_id: str, agent: str) -> None:
    """A turn of a page of another server that this computer claimed there
    for the vault ``link_id`` (gamma/local_agents/home.py ``HttpChatHome``),
    its row keyed by the vault in place of the workspace (as the runner
    keys it), queued again when it had one: a claim taken once more after
    its first was settled."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_turns (id, ws, link_id, conversation_id, page_id, agent, state, started_at) "
                     "VALUES (?, ?, ?, ?, ?, ?, 'queued', ?) ON CONFLICT(id) DO UPDATE SET ws = excluded.ws, "
                     "link_id = excluded.link_id, conversation_id = excluded.conversation_id, "
                     "page_id = excluded.page_id, agent = excluded.agent, state = 'queued', "
                     "started_at = excluded.started_at, ended_at = NULL, error = '', pid = NULL",
                     (turn_id, link_id, link_id, conversation_id, page_id, agent, page_now()))


def turn_running(turn_id: str, pid: int = 0) -> None:
    """The turn runs (its process ``pid`` once known)."""
    with connect_vaults_db() as conn:
        conn.execute("UPDATE agent_turns SET state = 'running', pid = CASE WHEN ? != 0 THEN ? ELSE pid END "
                     "WHERE id = ? AND ended_at IS NULL", (int(pid or 0), int(pid or 0), turn_id))


def end_turn(turn_id: str, *, state: str, error: str = "") -> None:
    """The turn ends in ``state`` (done, failed, stopped), ``error`` this
    computer's record of why. A turn that ended already stays as it
    ended."""
    if state not in TURN_STATES[2:]:
        raise ValueError(f"a turn ends {', '.join(TURN_STATES[2:])}")
    with connect_vaults_db() as conn:
        conn.execute("UPDATE agent_turns SET state = ?, ended_at = ?, error = ? WHERE id = ? AND ended_at IS NULL",
                     (state, page_now(), error or "", turn_id))


def get_turn(turn_id: str) -> dict | None:
    """The turn of a page of this server, None for none: a turn of another
    server's page (``claimed_turn``'s row) is that server's to answer for,
    this computer's record alone (``open_turns``)."""
    with connect_vaults_db() as conn:
        row = conn.execute(f"{_TURN_SELECT} WHERE id = ? AND ws != link_id", (turn_id,)).fetchone()
    return dict(zip(_TURN_COLUMNS, row)) if row else None


def open_turns() -> list[dict]:
    """The turns that have not ended, oldest first: at startup, those a
    server that stopped left behind."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_TURN_SELECT} WHERE ended_at IS NULL ORDER BY started_at").fetchall()
    return [dict(zip(_TURN_COLUMNS, row)) for row in rows]

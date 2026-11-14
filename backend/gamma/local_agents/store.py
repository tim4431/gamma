"""What this computer keeps of its agents, in vaults.db (``db.VAULTS_SCHEMA``,
never migrated): its agents (``computer_agents``, one row an agent: whether
it is on, what it may run, ``never`` or ``ask``, and the account that turned
it on, which owns it: its turns, mentions and Sends use it, nobody else's),
the directories it offers them beyond its vaults (``agent_dirs``), one row a
run (``agent_runs``): which request it took, the agent's session, its state
(``running`` until it ends ``done``, ``failed`` or ``stopped``), the last
progress it showed, the source files it changed, whether the build changed,
its steps and its process; and one row a thread an agent here answered last
(``agent_threads``): the agent, its session and the directory it ran in,
when the run read the thread, the reply it wrote and the request, so that a
follow-up in the thread resumes that session. For the chat's computer side
(gamma/local_agents/chats.py): one row a conversation (``agent_conversations``),
the session its agent ran in last, the directory and the vault it is of
("" for none), what its next turn resumes, and one row a turn
(``agent_turns``, its ``page_id`` the conversation's bucket), its state and
process (a turn of a page of another server, claimed there, keyed by its
vault in place of the workspace: ``claimed_turn``). The commands a person allowed
in a conversation (``agent_dir_grants``), each exactly as it was asked,
keyed by the directory the turn works in, with the folder below it the
command runs in and the network host it reaches: consent given for one
directory and one agent, gone when the agent is turned off or set back to
never (``set_agent``, ``set_runs``) and with its conversation. The people
an agent's owner lets use it (``agent_people``: a co-author of a workspace
of this server or of another one this computer is shown on), gone when the
agent is turned off, so an agent turned on again by another account serves
none of them.

Before agents were the computer's, each vault had its own (``agent_folders``)
and what it could run (``agent_policies``), and a chat's grants were keyed
by the vault (``agent_grants``): ``carry_over`` reads the first two once,
and nothing reads any of the three since.

A run's or a turn's ``error`` is this computer's record of a failure, the
agent's whole error and its stderr's tail: it may name the user's paths and
is never shown to the workspace's members (their request carries a sentence
of the runner's) nor written in the conversation. A removed vault forgets
its threads and its conversations' sessions and turns
(``vault.store.forget_link``), a relinked one its threads and sessions
(``forget_threads``); ``forget_links`` drops the rows of links that are gone
some other way.

Every function opens its own short connection and commits before it returns,
as gamma/vault/store.py does.
"""

import json
from pathlib import Path

from ..blocks_store import new_block_id
from ..db import connect_vaults_db, page_now
from .catalog import AGENTS

MAX_STEPS = 200        # steps a run keeps
MAX_CHANGES = 200      # source files a run keeps as changed
MAX_DIRS = 50          # directories this computer offers its agents
MAX_DIR_NAME = 80      # characters of a directory's name
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


# --- the computer's agents ------------------------------------------------------------------

# never: as it always did; ask: a chat turn of a work folder of this server may ask the person in the chat, and
# one of another server's may ask at this computer, in the desktop app's window (runner.ask_refusal)
RUNS = ("never", "ask")
_AGENT_COLUMNS = ("agent", "enabled", "runs", "set_by", "set_at")


def carry_over() -> None:
    """At start, once: an agent that was on for any vault (``agent_folders``)
    is on for the computer, owned by the account that turned it on there
    first, and may ask where any vault of it let it ask on this computer
    too (``agent_policies`` ``computer``: the consent that covered both a
    chat here and another server's turns, which ``ask`` now is; a vault's
    ``ask``, its own chat's alone, carries ``never``); nothing once the
    computer has a row of any agent. Nothing reads the old tables after."""
    with connect_vaults_db() as conn:
        if conn.execute("SELECT 1 FROM computer_agents").fetchone():
            return
        rows = conn.execute("SELECT f.agent, f.set_by, f.set_at, EXISTS (SELECT 1 FROM agent_policies p JOIN "
                            "agent_folders g ON g.link_id = p.link_id WHERE g.agent = f.agent AND p.runs = "
                            "'computer') FROM agent_folders f ORDER BY f.set_at, f.rowid").fetchall()
        for agent, set_by, set_at, asks in rows:
            if agent in AGENTS:
                conn.execute("INSERT INTO computer_agents (agent, enabled, runs, set_by, set_at) VALUES (?, 1, ?, ?, ?) "
                             "ON CONFLICT(agent) DO NOTHING", (agent, "ask" if asks else "never", set_by, set_at))


def agents() -> dict[str, dict]:
    """``{agent: {agent, enabled, runs, set_by, set_at}}`` of every agent
    this computer has a row of, in the catalog's order; an agent turned off
    keeps its row, ``enabled`` false and ``runs`` ``never``."""
    with connect_vaults_db() as conn:
        rows = {r[0]: dict(zip(_AGENT_COLUMNS, r)) for r in conn.execute(
            f"SELECT {', '.join(_AGENT_COLUMNS)} FROM computer_agents")}
    out = {}
    for name in AGENTS:
        if name in rows:
            row = rows[name]
            out[name] = {**row, "enabled": bool(row["enabled"]),
                         "runs": row["runs"] if row["enabled"] and row["runs"] in RUNS else "never"}
    return out


def enabled_agents() -> dict[str, dict]:
    """``agents()`` of those that are on."""
    return {name: row for name, row in agents().items() if row["enabled"]}


def agent_for(user_id: str) -> dict | None:
    """The account's first agent that is on, in the catalog's order (Codex,
    then Claude Code): the one its chat turns and Sends use, and the one its
    mention names; None for none, and for no account."""
    return next((row for row in enabled_agents().values() if user_id and row["set_by"] == user_id), None)


def owns(user_id: str, agent: str) -> bool:
    """Whether ``agent`` is on and the account ``user_id`` turned it on."""
    row = enabled_agents().get(agent)
    return bool(user_id) and row is not None and row["set_by"] == user_id


def runs_of(agent: str) -> str:
    """What ``agent`` may run: ``ask`` or ``never`` (``never`` while it is
    off)."""
    return (agents().get(agent) or {}).get("runs", "never")


def set_agent(agent: str, enabled: bool, *, by: str, force: bool = False) -> bool:
    """``agent`` turned on by the account ``by``, which owns it from then on,
    or off by its owner → whether that changed it: an agent on already
    keeps its owner and when it was turned on, and one another account
    owns is turned off here only with ``force`` (the desktop shell's word).
    Turned off, it may run ``never`` again, and the grants of its chats and
    the people its owner named go. ValueError for an agent Gamma does not
    know."""
    if agent not in AGENTS:
        raise ValueError(f"Gamma knows no agent named {agent}.")
    with connect_vaults_db() as conn:
        if enabled:
            return conn.execute("INSERT INTO computer_agents (agent, enabled, runs, set_by, set_at) "
                                "VALUES (?, 1, 'never', ?, ?) ON CONFLICT(agent) DO UPDATE SET enabled = 1, "
                                "runs = 'never', set_by = excluded.set_by, set_at = excluded.set_at "
                                "WHERE NOT computer_agents.enabled", (agent, by or "", page_now())).rowcount > 0
        owned, args = ("", (agent,)) if force else (" AND set_by = ?", (agent, by or ""))
        changed = conn.execute("UPDATE computer_agents SET enabled = 0, runs = 'never' WHERE agent = ? AND enabled"
                               + owned, args).rowcount > 0
        if changed:
            conn.execute("DELETE FROM agent_dir_grants WHERE agent = ?", (agent,))
            conn.execute("DELETE FROM agent_people WHERE agent = ?", (agent,))
        return changed


def release_account(user_id: str) -> list[str]:
    """The agents the account ``user_id`` owns turned off, as when it is
    deleted → the agents that changed; the grants of their chats and the
    people named for them go."""
    with connect_vaults_db() as conn:
        gone = [row[0] for row in conn.execute("SELECT agent FROM computer_agents WHERE set_by = ? AND enabled",
                                               (user_id,))]
        conn.execute("UPDATE computer_agents SET enabled = 0, runs = 'never' WHERE set_by = ? AND enabled",
                     (user_id,))
        conn.executemany("DELETE FROM agent_dir_grants WHERE agent = ?", [(agent,) for agent in gone])
        conn.executemany("DELETE FROM agent_people WHERE agent = ?", [(agent,) for agent in gone])
    return gone


def set_runs(agent: str, runs: str, *, by: str) -> bool:
    """What ``agent`` may run, set by its owner ``by`` → whether that
    changed it (an agent off, or another account's, is left as it is);
    ``never`` takes the grants of its chats away. ValueError for another
    value."""
    if runs not in RUNS:
        raise ValueError(f"an agent may run {' or '.join(RUNS)}")
    with connect_vaults_db() as conn:
        changed = conn.execute("UPDATE computer_agents SET runs = ? WHERE agent = ? AND enabled AND set_by = ? "
                               "AND runs != ?", (runs, agent, by or "", runs)).rowcount > 0
        if runs == "never":
            conn.execute("DELETE FROM agent_dir_grants WHERE agent = ?", (agent,))
        return changed


# --- the directories offered --------------------------------------------------------------------

def dirs() -> list[dict]:
    """The directories this computer offers its agents beyond its vaults:
    ``[{id, path, name, added_at}]``, in the order they were last given
    (``set_dirs``)."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT id, path, name, added_at FROM agent_dirs ORDER BY rowid").fetchall()
    return [dict(zip(("id", "path", "name", "added_at"), r)) for r in rows]


def set_dirs(entries: list[dict]) -> list[dict]:
    """The directories offered replaced by ``entries`` (``[{id?, path,
    name}]``, each path a directory as ``folder_links.clean_path`` gives
    it): an entry whose ``id`` is offered keeps it and when it was added,
    any other is new; an offered one left out goes → ``dirs()``. A name
    left empty is the directory's own. ValueError for more than
    ``MAX_DIRS``, or a path or an id given twice."""
    if len(entries) > MAX_DIRS:
        raise ValueError(f"At most {MAX_DIRS} directories are offered.")
    paths = [entry["path"] for entry in entries]
    ids = [entry.get("id") for entry in entries if entry.get("id")]
    if len(set(paths)) != len(paths) or len(set(ids)) != len(ids):
        raise ValueError("A directory is offered once.")
    now = page_now()
    with connect_vaults_db() as conn:
        kept = dict(conn.execute("SELECT id, added_at FROM agent_dirs").fetchall())
        conn.execute("DELETE FROM agent_dirs")
        for entry in entries:
            dir_id = entry.get("id") if entry.get("id") in kept else new_block_id()
            name = (str(entry.get("name") or "").strip() or Path(entry["path"]).name)[:MAX_DIR_NAME]
            conn.execute("INSERT INTO agent_dirs (id, path, name, added_at) VALUES (?, ?, ?, ?)",
                         (dir_id, entry["path"], name, kept.get(dir_id, now)))
    return dirs()


# --- the people an agent's owner lets use it -----------------------------------------------------

MAX_PEOPLE = 50        # people one agent serves beside its owner
_PEOPLE_COLUMNS = ("agent", "server", "workspace_id", "account", "username", "at")


def people(agent: str = "") -> list[dict]:
    """The people the owners let use their agents (decision 13 of
    docs/research/agents-as-participants-plan.md), or ``agent``'s:
    ``[{agent, server, workspace_id, account, username, at}]``, ``server``
    "" for this server and else the address of the owner's channel there
    (``computer_links.remote_url``), ``account`` the person's account
    there, ``username`` how Settings names them; in the order they were
    named."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"SELECT {', '.join(_PEOPLE_COLUMNS)} FROM agent_people WHERE ? = '' OR agent = ? "
                            "ORDER BY at, rowid", (agent, agent)).fetchall()
    return [dict(zip(_PEOPLE_COLUMNS, r)) for r in rows]


def named(agent: str, server: str, ws: str, account: str) -> bool:
    """Whether the owner of ``agent`` lets the account ``account`` of the
    workspace ``ws`` on ``server`` ("" this one) use it."""
    if not account:
        return False
    with connect_vaults_db() as conn:
        return conn.execute("SELECT 1 FROM agent_people WHERE server = ? AND workspace_id = ? AND account = ? "
                            "AND agent = ?", (server, ws, account, agent)).fetchone() is not None


def name_person(agent: str, server: str, ws: str, account: str, username: str) -> bool:
    """The account ``account`` of the workspace ``ws`` on ``server`` may
    use ``agent`` from now on → whether that is new. ValueError past
    ``MAX_PEOPLE``."""
    with connect_vaults_db() as conn:
        if conn.execute("SELECT 1 FROM agent_people WHERE server = ? AND workspace_id = ? AND account = ? "
                        "AND agent = ?", (server, ws, account, agent)).fetchone():
            conn.execute("UPDATE agent_people SET username = ? WHERE server = ? AND workspace_id = ? AND account = ? "
                         "AND agent = ?", (username, server, ws, account, agent))
            return False
        if conn.execute("SELECT COUNT(*) FROM agent_people WHERE agent = ?", (agent,)).fetchone()[0] >= MAX_PEOPLE:
            raise ValueError(f"At most {MAX_PEOPLE} people may use one agent.")
        conn.execute("INSERT INTO agent_people (server, workspace_id, account, agent, username, at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (server, ws, account, agent, username, page_now()))
    return True


def unname_person(agent: str, server: str, ws: str, account: str) -> bool:
    """The account may no longer use ``agent`` → whether it could."""
    with connect_vaults_db() as conn:
        return conn.execute("DELETE FROM agent_people WHERE server = ? AND workspace_id = ? AND account = ? "
                            "AND agent = ?", (server, ws, account, agent)).rowcount > 0


def _lends(agent: str, row: dict, ws: str, account: str) -> bool:
    """Whether the owner of ``agent`` (its ``enabled_agents()`` row) lets
    the account ``account`` use it in the workspace ``ws`` of this server,
    while the owner still edits ``ws``."""
    from .. import workspaces

    return named(agent, "", ws, account) and workspaces.at_least(workspaces.role_of(ws, row["set_by"]), "editor")


def lent_to(ws: str, account: str) -> dict[str, dict]:
    """``enabled_agents()`` of those another account owns and lets the
    account ``account`` of the workspace ``ws`` of this server use
    (``_lends``)."""
    return {name: row for name, row in enabled_agents().items()
            if row["set_by"] != account and _lends(name, row, ws, account)}


def usable(user_id: str, ws: str, agent: str) -> dict | None:
    """The ``enabled_agents()`` row of ``agent`` while the account owns it,
    or its owner lets the account use it in ``ws`` (``lent_to``); None
    otherwise."""
    row = enabled_agents().get(agent)
    if row is None or not user_id:
        return None
    return row if row["set_by"] == user_id or _lends(agent, row, ws, user_id) else None


def forget_links(live_ids: set[str]) -> None:
    """The threads, conversations (their grants with them) and ended turns
    of the links not in ``live_ids`` forgotten (a vault gone while this
    server was not looking); a conversation's and a turn's of no vault
    (``link_id`` "") stay."""
    live = json.dumps(sorted(live_ids))
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_dir_grants WHERE conversation_id IN (SELECT conversation_id "
                     "FROM agent_conversations WHERE link_id != '' "
                     "AND link_id NOT IN (SELECT value FROM json_each(?)))", (live,))
        conn.execute("DELETE FROM agent_threads WHERE link_id NOT IN (SELECT value FROM json_each(?))", (live,))
        conn.execute("DELETE FROM agent_conversations WHERE link_id != '' "
                     "AND link_id NOT IN (SELECT value FROM json_each(?))", (live,))
        conn.execute("DELETE FROM agent_turns WHERE ended_at IS NOT NULL AND link_id != '' "
                     "AND link_id NOT IN (SELECT value FROM json_each(?))", (live,))


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
    """The threads the vault's agent answered forgotten, and the sessions
    of its conversations: its directory moved (a session belongs to the
    directory; a grant is keyed by it, so none holds in the new one)."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_threads WHERE link_id = ?", (link_id,))
        conn.execute("DELETE FROM agent_conversations WHERE link_id = ?", (link_id,))


# --- what the person allowed --------------------------------------------------------------------

def grant(conversation_id: str, command: str, *, agent: str, dir: str, cwd: str = ".", host: str = "") -> None:
    """The person allowed ``command`` in the conversation (Allow in this
    chat) of ``agent``, whose turns work in the directory ``dir``, run in
    ``cwd`` below it (as the runner keys it: ``.`` the directory) and
    reaching the network ``host`` ("" for none): its next ask, exactly this
    command there and to that host, is answered without asking."""
    with connect_vaults_db() as conn:
        conn.execute("INSERT INTO agent_dir_grants (conversation_id, dir, command, cwd, host, agent, at) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(conversation_id, dir, command, cwd, host) DO NOTHING",
                     (conversation_id, dir, command.strip(), cwd or ".", (host or "").strip(), agent, page_now()))


def granted(conversation_id: str, command: str, *, dir: str, cwd: str = ".", host: str = "") -> bool:
    """Whether the person allowed exactly ``command`` (whitespace around it
    aside) in ``cwd`` to the network ``host`` in the conversation, whose
    turns work in ``dir``. Never a prefix, never another directory or
    host."""
    with connect_vaults_db() as conn:
        row = conn.execute("SELECT 1 FROM agent_dir_grants WHERE conversation_id = ? AND dir = ? AND command = ? "
                           "AND cwd = ? AND host = ?",
                           (conversation_id, dir, command.strip(), cwd or ".", (host or "").strip())).fetchone()
    return row is not None


def grants_of(conversation_id: str, *, dir: str) -> list[dict]:
    """What the person allowed in the conversation, whose turns work in
    ``dir``: ``[{command, cwd, host}]``, oldest first (the composer's
    Allowed in this chat chip)."""
    with connect_vaults_db() as conn:
        rows = conn.execute("SELECT command, cwd, host FROM agent_dir_grants WHERE conversation_id = ? AND dir = ? "
                            "ORDER BY at, command", (conversation_id, dir)).fetchall()
    return [{"command": r[0], "cwd": r[1], "host": r[2]} for r in rows]


def forget_grants(conversation_id: str) -> None:
    """The conversation's grants taken back: each command asks again."""
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM agent_dir_grants WHERE conversation_id = ?", (conversation_id,))


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
    conversation's last session (``link_id`` its vault's, "" for a
    directory of no vault), None for none."""
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
        conn.execute("DELETE FROM agent_dir_grants WHERE conversation_id = ?", (conversation_id,))


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


def claimed_of(turn_id: str) -> dict | None:
    """The turn of another server's page this computer claimed
    (``claimed_turn``'s row), None for none."""
    with connect_vaults_db() as conn:
        row = conn.execute(f"{_TURN_SELECT} WHERE id = ? AND ws = link_id", (turn_id,)).fetchone()
    return dict(zip(_TURN_COLUMNS, row)) if row else None


def open_turns() -> list[dict]:
    """The turns that have not ended, oldest first: at startup, those a
    server that stopped left behind."""
    with connect_vaults_db() as conn:
        rows = conn.execute(f"{_TURN_SELECT} WHERE ended_at IS NULL ORDER BY started_at").fetchall()
    return [dict(zip(_TURN_COLUMNS, row)) for row in rows]

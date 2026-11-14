"""Send: a person's request that an agent act on a work folder's feedback
(docs/dev/feedback.md "Requests and their states"), in the workspace's
pages.db (``db.FEEDBACK_SCHEMA``).

A request names its work folder, the scope the person sent (a file, or a
folder: W or a directory below it), the open threads it covers and a line
of instruction. Its ``rev`` is the listing's cursor: the next number of the
workspace at the insert and at every change of state, under the write lock,
so ``listing(after=…)`` shows a new request and a state change alike. Its
state goes ``sent`` → ``running`` → ``done`` or ``failed``, set by an agent
(an integration token of the feedback or write scope) or, to withdraw it,
by its requester (``TRANSITIONS``); an agent on this computer takes a
sent one with ``claim`` (docs/dev/local_agents.md), and the run of a
computer that keeps the work folder with an agent there with
``claim_run``, keyed by its run, which alone then writes its state
(``run_state``; gamma/routers/computers.py). A thread it names is answered once a
token or another account replies to it or resolves it after the request
(``answered``, a comment listener): it shows as sent until then
(``comments._sent``). After every write the pages of its threads hear
``{t: "comments"}`` (open tabs refetch the threads and their Sent chips),
and the files of its work folder are due (gamma/feedback/files.py), its
request file written at once where this computer holds the vault.

Nothing here travels: a mirror, a publication, a share export, a backup
merge and a Gamma export leave requests out; a restore in place copies them
with the file. ``sweep`` deletes the requests done or failed
``KEEP_DAYS`` ago, and the rows of threads that are gone.
"""

import json
from datetime import datetime, timedelta, timezone

from .. import collab, config
from ..blocks_store import new_block_id, valid_block_id, write_lock
from ..db import account_names, connect_pages_db, connect_users_db, format_stamp, page_now, parse_stamp, workspace_ids
from ..logbuf import log
from ..work_folders.marks import Tree
from . import entries
from .entries import FeedbackError, Scope

REQUEST_FORMAT = "gamma-feedback-request-1"
STATES = ("sent", "running", "done", "failed")
WRITERS = ("agent", "requester")
MAX_NOTE = 2000          # characters of a request's line of instruction
MAX_DETAIL = 500         # characters of a state's detail (a failure's reason, a line of progress)
MAX_THREADS = entries.MAX_ENTRIES
MAX_LIMIT = 100          # requests one listing gives at most
KEEP_DAYS = 90           # a request done or failed is deleted this long after (``sweep``, on the comments' timer)
WITHDRAWN = "withdrawn"  # the detail of a request its requester withdrew

# (from, to) -> who may make that change; anything else is refused. An
# agent's same state and detail again changes nothing, and so does a
# repeated withdraw (set_state); a requester's other repeats are refused
# like any change of theirs but the withdraw.
TRANSITIONS = {("sent", "running"): {"agent"}, ("sent", "done"): {"agent"}, ("sent", "failed"): {"agent", "requester"},
               ("running", "running"): {"agent"}, ("running", "done"): {"agent"},
               ("running", "failed"): {"agent", "requester"},
               ("failed", "running"): {"agent"}, ("failed", "done"): {"agent"}}

# What follows every committed write of a request (``_changed``): ``fn(ws,
# request_id)``, each called in turn, an error logged and never raised
# (gamma/local_agents/runner.py appends its own: it claims the Sends an
# agent on this computer answers, and stops a run its request withdrew;
# ``_wake_computers``, on every server, wakes the computer that keeps a
# work folder of this server with an agent).
change_listeners: list = []

# How a Send was made (``feedback_requests.via``): a person in Gamma, an
# integration token; "" for one made before step 58 or by a caller that
# does not say (a test, a backup's copy).
SESSION, TOKEN = "session", "token"
VIA = ("", SESSION, TOKEN)

_COLUMNS ="id, rev, work_folder, scope, scope_kind, by, by_name, at, note, state, state_at, state_by, detail"


# --- reading ----------------------------------------------------------------------

def _records(conn, rows) -> list[dict]:
    """``_COLUMNS`` rows as request records, their threads and names read
    once for all of them."""
    if not rows:
        return []
    threads: dict = {}
    for request_id, thread_id, answered_at in conn.execute(
            "SELECT request_id, thread_id, answered_at FROM feedback_request_threads "
            "WHERE request_id IN (SELECT value FROM json_each(?)) ORDER BY request_id, thread_id",
            (json.dumps([r[0] for r in rows]),)):
        threads.setdefault(request_id, []).append({"id": thread_id, "answered": answered_at is not None})
    with connect_users_db() as users:
        names = account_names(users, {r[5] for r in rows})
    tree = Tree.read(conn)
    out = []
    for (request_id, rev, w, scope_id, scope_kind, by, by_name, at, note, state, state_at, state_by,
         detail) in rows:
        try:
            scope = entries.resolve_scope(conn, scope_id, tree)
            scope_out = {"kind": scope.kind, "id": scope.id, "name": scope.name}
        except FeedbackError:  # the file or the directory went since
            scope_out = {"kind": "file" if scope_kind == "file" else "work_folder" if scope_id == w else "folder",
                         "id": scope_id, "name": ""}
        out.append({"id": request_id, "rev": rev, "work_folder": {"id": w, "name": tree.names.get(w, "")},
                    "scope": scope_out, "by": names.get(by) or by_name, "at": at, "note": note, "state": state,
                    "state_at": state_at, "state_by": state_by, "detail": detail,
                    "threads": threads.get(request_id, [])})
    return out


def get(conn, request_id: str) -> dict | None:
    """The request ``{id, rev, work_folder: {id, name}, scope: {kind, id,
    name}, by, at, note, state, state_at, state_by, detail, threads: [{id,
    answered}]}`` (``by`` its requester's username), None for none."""
    row = conn.execute(f"SELECT {_COLUMNS} FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
    return _records(conn, [row])[0] if row else None


def requester(conn, request_id: str) -> str | None:
    """The account id of the request's requester, None for no request."""
    row = conn.execute("SELECT by FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
    return row[0] if row else None


def listing(conn, *, after: int = 0, scope: str = "", state: str = "", limit: int = 50) -> dict:
    """``{requests, cursor}``: the requests written since the cursor
    ``after`` (made or changed: ``rev > after``), oldest first, at most
    ``limit`` (``MAX_LIMIT``); ``scope`` a work folder's or a file's id
    keeps its requests (by ``work_folder`` or ``scope``), ``state`` one
    state's. ``cursor`` is the last ``rev`` given, else ``after``."""
    if state and state not in STATES:
        raise FeedbackError(400, f"state is one of {', '.join(STATES)}")
    where, args = ["rev > ?"], [max(0, after)]
    if scope:
        where.append("(work_folder = ? OR scope = ?)")
        args += [scope, scope]
    if state:
        where.append("state = ?")
        args.append(state)
    rows = conn.execute(f"SELECT {_COLUMNS} FROM feedback_requests WHERE {' AND '.join(where)} ORDER BY rev LIMIT ?",
                        (*args, max(1, min(limit, MAX_LIMIT)))).fetchall()
    return {"requests": _records(conn, rows), "cursor": rows[-1][1] if rows else max(0, after)}


def entries_of(ws, conn, record: dict, *, paths: dict | None = None, link_base: str = "",
               regions: dict | None = None, places: bool = False) -> list[dict]:
    """The entries of the request's threads as they are now, in any state
    (``entries.entries``; a thread gone since is left out): what its file
    holds. ``places``: each placed on a PDF page says where
    (``entries.entries``)."""
    try:
        scope = entries.resolve_scope(conn, record["work_folder"]["id"])
    except FeedbackError:
        return []
    return entries.entries(ws, conn, scope, state="all", paths=paths, link_base=link_base,
                           threads=[t["id"] for t in record["threads"]], regions=regions, places=places)


def document_of(ws, conn, request_id: str, *, paths: dict | None = None, regions: dict | None = None) -> dict | None:
    """The request's file (``.gamma/feedback/requests/<id>.json``, written
    by gamma/feedback/files.py): ``{format, id, rev, workspace, work_folder,
    scope, by, at, note, state, state_at, state_by, detail, answer,
    entries}``, ``answer`` naming this request's state route. Its pictures
    are named as in ``open.json`` (``img/<key>.jpg``); the writer puts them
    where the file is. None for no request."""
    record = get(conn, request_id)
    if record is None:
        return None
    return {"format": REQUEST_FORMAT, "id": record["id"], "rev": record["rev"], "workspace": ws,
            **{k: record[k] for k in ("work_folder", "scope", "by", "at", "note", "state", "state_at", "state_by",
                                      "detail")},
            "answer": {**entries.ANSWER, "state": f"POST /api/feedback/requests/{request_id}/state {{state, detail}}"},
            "entries": entries_of(ws, conn, record, paths=paths, regions=regions)}


# --- writing -----------------------------------------------------------------------

def _next_rev(conn) -> int:
    return conn.execute("SELECT COALESCE(MAX(rev), 0) + 1 FROM feedback_requests").fetchone()[0]


def create(ws, conn, scope: Scope, *, by: str, by_name: str, note: str = "", threads=None,
           request_id: str = "", via: str = "") -> dict:
    """A Send of the scope's open threads (``threads``: those of them, each
    an open thread in the scope, at most ``MAX_THREADS``; omitted, every
    one) with the line of instruction ``note``, by the account ``by``
    (``by_name`` its username) → ``{request, created}``. ``request_id``:
    the client's id (a block id's shape), minted when absent; a request of
    that id by the same account is answered as stored, ``created`` false.
    ``via`` is how it was made (``VIA``: a person's session or an
    integration token), stored for the agent on this computer, which takes
    a session's alone. Commits, then tells the threads' pages and the work
    folder's files. FeedbackError 400 for nothing to send, a thread that
    is no open one here, too many or an id of the wrong shape; 413 for a
    longer note; 409 for another account's id."""
    if via not in VIA:
        raise ValueError(f"via is one of {', '.join(VIA)}")
    note = note if isinstance(note, str) else ""
    if len(note) > MAX_NOTE:
        raise FeedbackError(413, f"an instruction holds at most {MAX_NOTE} characters")
    if request_id and not valid_block_id(request_id):
        raise FeedbackError(400, f"invalid request id: {request_id!r}")
    write_lock(conn)
    try:
        if request_id:
            row = conn.execute("SELECT by FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
            if row:
                if row[0] != by:
                    raise FeedbackError(409, "that request id is taken")
                conn.rollback()
                return {"request": get(conn, request_id), "created": False}
        open_here = entries.thread_ids(conn, scope)
        if threads is None:
            chosen = open_here
        else:
            chosen = list(dict.fromkeys(threads))
            if len(chosen) > MAX_THREADS:
                raise FeedbackError(400, f"a Send names at most {MAX_THREADS} comments")
            known = set(open_here)
            if any(t not in known for t in chosen):
                raise FeedbackError(400, "Only the open comments here can be sent.")
        if not chosen:
            raise FeedbackError(400, "Nothing to send: no open comments here.")
        request_id = request_id or new_block_id()
        now = page_now()
        conn.execute(f"INSERT INTO feedback_requests ({_COLUMNS}, via) "
                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?, '', '', ?)",
                     (request_id, _next_rev(conn), scope.work_folder, scope.id,
                      "file" if scope.kind == "file" else "folder", by, by_name or "", now, note, now, via))
        conn.executemany("INSERT INTO feedback_request_threads (request_id, thread_id) VALUES (?, ?)",
                         [(request_id, t) for t in chosen])
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _changed(ws, conn, request_id)
    return {"request": get(conn, request_id), "created": True}


def claim(ws, conn, request_id: str, *, by_name: str, detail: str) -> dict | None:
    """Take a sent request for an agent on this computer
    (gamma/local_agents/runner.py): ``running`` with ``detail``, set by
    ``by_name``, in one step under the write lock, so of two takers (or a
    taker and a withdraw) one wins → the request, or None when it is no
    longer sent (taken, withdrawn, answered, gone: nothing written). Commits,
    then tells the threads' pages and the work folder's files."""
    detail = (detail or "")[:MAX_DETAIL]
    write_lock(conn)
    try:
        row = conn.execute("SELECT state FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
        if not row or row[0] != "sent":
            conn.rollback()
            return None
        conn.execute("UPDATE feedback_requests SET state = 'running', state_at = ?, state_by = ?, detail = ?, rev = ? "
                     "WHERE id = ?", (page_now(), by_name or "", detail, _next_rev(conn), request_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _changed(ws, conn, request_id)
    return get(conn, request_id)


def set_state(ws, conn, request_id: str, state: str, *, detail: str = "", by_name: str, writer: str,
              only_from: tuple[str, ...] = ()) -> dict:
    """Set the request's state as ``writer`` (``agent``: an integration
    token of the feedback or write scope; ``requester``: its requester's
    session, whose only change is a withdraw, ``failed`` with the detail
    ``withdrawn``) → the request. ``by_name`` is who it says set it (the
    token's display name, or the requester's username). An agent's same
    state with the same detail changes nothing, nor does a repeated
    withdraw; a requester repeating any other state is refused as any
    change of theirs but the withdraw is. ``only_from``: the states the
    writer expects the request in (the local runner's writes pass
    ``("running",)``, so a request withdrawn meanwhile is not overwritten).
    A write takes the next ``rev``. Commits, then tells the threads' pages
    and the work folder's files. FeedbackError 404 for no request, 400 for
    another state, 413 for a longer detail, 409 for a stored state outside
    ``only_from`` or a change the writer may not make (``TRANSITIONS``).
    An agent taking a request that is not running (``running`` from
    ``sent`` or ``failed``) ends a computer's run of it still open
    (``STOPPED``), in the same write: that run is refused from then on."""
    if writer not in WRITERS:
        raise ValueError(f"writer is one of {', '.join(WRITERS)}")
    if state not in STATES:
        raise FeedbackError(400, f"state is one of {', '.join(STATES)}")
    detail = WITHDRAWN if writer == "requester" and state == "failed" else (detail or "")
    if len(detail) > MAX_DETAIL:
        raise FeedbackError(413, f"a detail holds at most {MAX_DETAIL} characters")
    write_lock(conn)
    try:
        row = conn.execute("SELECT state, detail FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
        if not row:
            raise FeedbackError(404, "no such request")
        if only_from and row[0] not in only_from:
            raise FeedbackError(409, f"The request is {row[0]} now.")
        if (row[0], row[1]) == (state, detail) and (writer == "agent" or state == "failed"):
            conn.rollback()
            return get(conn, request_id)
        if writer not in TRANSITIONS.get((row[0], state), ()):
            raise FeedbackError(409, f"A {row[0]} request cannot become {state}.")
        conn.execute("UPDATE feedback_requests SET state = ?, state_at = ?, state_by = ?, detail = ?, rev = ? "
                     "WHERE id = ?", (state, page_now(), by_name or "", detail, _next_rev(conn), request_id))
        if state == "running" and row[0] != "running":   # taken again: a computer's earlier run holds it no more
            conn.execute("UPDATE feedback_request_runs SET ended = ? WHERE request_id = ? AND ended = ''",
                         (STOPPED, request_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _changed(ws, conn, request_id)
    return get(conn, request_id)


def _changed(ws: str, conn, request_id: str) -> None:
    """After a request's write: the pages of its threads refetch them, its
    work folder's files are due, its request file written now where this
    computer keeps the vault, and the ``change_listeners`` hear it. Never
    raises."""
    try:
        pages = [r[0] for r in conn.execute(
            "SELECT DISTINCT b.page_id FROM feedback_request_threads t JOIN comments c ON c.id = t.thread_id "
            "JOIN unified_blocks b ON b.id = c.block_id WHERE t.request_id = ?", (request_id,))]
        w = conn.execute("SELECT work_folder FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()[0]
    except Exception as e:  # noqa: BLE001 — the request is stored; what follows it never fails it
        log.warning(f"[feedback] after request {request_id} in workspace {ws}: {e}")
        _tell(ws, request_id)
        return
    for page_id in pages:
        collab.publish(ws, page_id, {"t": "comments", "block_id": "", "thread_id": ""})
    _files_due(ws, w, request_id)
    _tell(ws, request_id)


def _tell(ws: str, request_id: str) -> None:
    """Each of the ``change_listeners`` in turn, an error logged and never
    raised."""
    for listener in list(change_listeners):
        try:
            listener(ws, request_id)
        except Exception as e:  # noqa: BLE001 — a listener never fails the write it hears of
            log.warning(f"[feedback] request listener {getattr(listener, '__name__', listener)} on "
                        f"{request_id} in workspace {ws}: {e}")


def _wake_computers(ws: str, request_id: str) -> None:
    """A Send made or withdrawn in a work folder a computer keeps with an
    agent wakes that computer's held poll (gamma/computers/registry.py
    ``heard``)."""
    from ..computers import registry  # local: the registry reaches the workspaces, which reach the comments

    registry.heard(ws, request_id)


change_listeners.append(_wake_computers)


def _files_due(ws: str, w: str, request_id: str = "") -> None:
    """The work folder ``w``'s feedback files are due
    (gamma/feedback/files.py), and with ``request_id`` that request's file
    is written now when a vault of this computer keeps ``w`` and may write
    (not paused, not choosing its files). Never raises."""
    if not config.folder_links_enabled():
        return
    try:
        from . import files  # local: the writer reaches the vaults

        files.mark_folder(ws, w)
        link = _local_link(ws, w) if request_id else None
        if link is not None:
            files.write_request(link, request_id)
    except Exception as e:  # noqa: BLE001 — the next pass writes what this one could not
        log.warning(f"[feedback] the files of work folder {w} in workspace {ws}: {e}")


def _local_link(ws: str, w: str) -> dict | None:
    """The vault of this computer that is the work folder ``w`` of ``ws``,
    when it may write its directory now; None otherwise."""
    from .. import folder_links  # local: folder_links imports ops, which imports comments

    for link in folder_links.all_links():
        if (link["workspace_id"] == ws and link["folder_id"] == w and folder_links.work_folder_link(link)
                and not link["status"].get(folder_links.PAUSED) and not folder_links.choosing(link)):
            return link
    return None


def answered(ws: str, event: dict) -> None:
    """The comment listener's part (gamma/feedback/listeners.py): a reply
    or a resolve answers the thread in every request that waits on it and
    was made before it, unless it is its requester's own (a session's: a
    token's write answers whoever it belongs to). An event that says when
    its writer read the thread (``seen``: an agent on this computer's run,
    gamma/feedback/agent.py) answers only the requests made by then: one
    made while the run worked is still the next run's. A reopen answers
    nothing."""
    if event.get("kind") not in ("reply", "resolve") or not event.get("thread_id"):
        return
    with connect_pages_db(ws) as conn:
        now = page_now()
        seen = event.get("seen") or ""
        rows = conn.execute(
            "SELECT t.request_id, r.by, r.at, r.work_folder FROM feedback_request_threads t "
            "JOIN feedback_requests r ON r.id = t.request_id WHERE t.thread_id = ? AND t.answered_at IS NULL",
            (event["thread_id"],)).fetchall()
        hit = [(request_id, w) for request_id, by, at, w in rows
               if (at <= seen if seen else at < now) and (by != event.get("actor") or event.get("agent"))]
        if not hit:
            return
        write_lock(conn)
        try:
            conn.execute("UPDATE feedback_request_threads SET answered_at = ? WHERE thread_id = ? "
                         "AND answered_at IS NULL AND request_id IN (SELECT value FROM json_each(?))",
                         (now, event["thread_id"], json.dumps([request_id for request_id, _ in hit])))
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    for w in sorted({w for _, w in hit}):
        _files_due(ws, w)


# --- a computer's runs ---------------------------------------------------------------------
# A work folder of this server kept on a computer with an agent: the
# computer's runs take its maker's Sends over its channel
# (gamma/routers/computers.py), and this server writes what they answer.
# Each run is keyed by the id the computer minted for it
# (``feedback_request_runs``): its claim and its writes are told apart from
# another taker's, and a retry of either is answered as it was.

STOPPED = "stopped"      # a run's ``ended`` once it was withdrawn or refused
MAX_WAITING = 50         # Sends one listing gives a computer at most


def waiting_for(conn, by: str, folders) -> list[dict]:
    """The Sends of the account ``by`` made in Gamma (``via`` session) in
    the work folders ``folders`` that wait for an agent, oldest first, at
    most ``MAX_WAITING``: ``[{id, folder_id, page_id?, at}]``, ``page_id``
    for a file's Send."""
    rows = conn.execute("SELECT id, work_folder, scope, scope_kind, at FROM feedback_requests WHERE state = 'sent' "
                        "AND via = ? AND by = ? AND work_folder IN (SELECT value FROM json_each(?)) ORDER BY rev "
                        "LIMIT ?", (SESSION, by, json.dumps(sorted(folders)), MAX_WAITING)).fetchall()
    return [{"id": request_id, "folder_id": w, **({"page_id": scope} if kind == "file" else {}), "at": at}
            for request_id, w, scope, kind, at in rows]


def run_of(conn, request_id: str) -> dict | None:
    """The computer's run that holds the request or held it: ``{holder,
    run, agent, seen_at, read, ended}``, None for none."""
    row = conn.execute("SELECT holder, run, agent, seen_at, read, ended FROM feedback_request_runs "
                       "WHERE request_id = ?", (request_id,)).fetchone()
    if not row:
        return None
    try:
        read = json.loads(row[4] or "{}")
    except ValueError:
        read = {}
    return {"holder": row[0], "run": row[1], "agent": row[2], "seen_at": row[3],
            "read": read if isinstance(read, dict) else {}, "ended": row[5]}


def claim_run(ws, conn, request_id: str, *, holder: str, run: str, agent: str, by_name: str, detail: str,
              seen: str, read: dict) -> dict:
    """Take a sent request for the run ``run`` of the computer ``holder``:
    ``running`` with ``detail``, set by ``by_name``, and the run recorded
    with when it read the entries (``seen``, this server's clock) and the
    comments it read (``read``, ids by thread), in one step under the write
    lock → the request. The same run taking it again (a retry) is answered
    as holding it, its ``seen`` and ``read`` those of this read. Commits,
    then tells the threads' pages. FeedbackError 404 for no request, 409
    when another run holds it or it is no longer sent."""
    detail = (detail or "")[:MAX_DETAIL]
    write_lock(conn)
    try:
        row = conn.execute("SELECT state FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
        if not row:
            raise FeedbackError(404, "no such request")
        held = conn.execute("SELECT holder, run FROM feedback_request_runs WHERE request_id = ?",
                            (request_id,)).fetchone()
        if held and tuple(held) == (holder, run) and row[0] == "running":
            conn.execute("UPDATE feedback_request_runs SET seen_at = ?, read = ? WHERE request_id = ?",
                         (seen, json.dumps(read), request_id))
            conn.commit()
            return get(conn, request_id)
        if row[0] != "sent":
            raise FeedbackError(409, "Another run holds this request." if held and row[0] == "running"
                                else f"The request is {row[0]} now.")
        conn.execute("UPDATE feedback_requests SET state = 'running', state_at = ?, state_by = ?, detail = ?, rev = ? "
                     "WHERE id = ?", (page_now(), by_name or "", detail, _next_rev(conn), request_id))
        conn.execute("INSERT OR REPLACE INTO feedback_request_runs (request_id, holder, run, agent, seen_at, read, "
                     "ended) VALUES (?, ?, ?, ?, ?, ?, '')", (request_id, holder, run, agent, seen, json.dumps(read)))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _changed(ws, conn, request_id)
    return get(conn, request_id)


def run_state(ws, conn, request_id: str, *, holder: str, run: str, state: str, detail: str = "",
              by_name: str) -> dict:
    """What the run ``run`` of the computer ``holder`` writes of the
    request it holds: its progress (``running`` with ``detail``) or its end
    (``done``, ``failed``), only while it is running → the request. The
    same detail again changes nothing, and the end the run wrote, written
    again (a retry), is answered as stored. Commits, then tells the
    threads' pages. FeedbackError 400 for another state, 404 when no run of
    ``holder`` holds or held it, 409 when another run holds it or it is no
    longer running (withdrawn: the run is ``STOPPED`` from then on)."""
    if state not in ("running", "done", "failed"):
        raise FeedbackError(400, "state is running, done or failed")
    detail = (detail or "")[:MAX_DETAIL]
    write_lock(conn)
    try:
        row = conn.execute("SELECT state, detail FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
        held = conn.execute("SELECT holder, run, ended FROM feedback_request_runs WHERE request_id = ?",
                            (request_id,)).fetchone()
        if not row or not held or held[0] != holder:
            raise FeedbackError(404, "no such request")
        if held[1] != run:
            raise FeedbackError(409, "Another run holds this request.")
        if held[2] and held[2] == state == row[0]:
            conn.rollback()
            return get(conn, request_id)
        if row[0] != "running" or held[2]:
            conn.execute("UPDATE feedback_request_runs SET ended = ? WHERE request_id = ? AND ended = ''",
                         (STOPPED, request_id))
            conn.commit()
            raise FeedbackError(409, f"The request is {row[0]} now.")
        if (row[0], row[1]) == (state, detail):
            conn.rollback()
            return get(conn, request_id)
        conn.execute("UPDATE feedback_requests SET state = ?, state_at = ?, state_by = ?, detail = ?, rev = ? "
                     "WHERE id = ?", (state, page_now(), by_name or "", detail, _next_rev(conn), request_id))
        if state != "running":
            conn.execute("UPDATE feedback_request_runs SET ended = ? WHERE request_id = ?", (state, request_id))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    _changed(ws, conn, request_id)
    return get(conn, request_id)


def withdrawn_runs(conn, holder: str) -> list[dict]:
    """The runs of the computer ``holder`` still at work on a request that
    is no longer running (withdrawn, failed or gone meanwhile): ``[{id,
    run}]``, each ``STOPPED`` from then on, so it is told once (a run that
    missed it is refused at its next write)."""
    rows = conn.execute("SELECT r.request_id, r.run FROM feedback_request_runs r LEFT JOIN feedback_requests q "
                        "ON q.id = r.request_id WHERE r.holder = ? AND r.ended = '' "
                        "AND (q.state IS NULL OR q.state != 'running') ORDER BY r.request_id", (holder,)).fetchall()
    if not rows:
        return []
    write_lock(conn)
    try:
        conn.execute("UPDATE feedback_request_runs SET ended = ? WHERE ended = '' "
                     "AND request_id IN (SELECT value FROM json_each(?))", (STOPPED, json.dumps([r[0] for r in rows])))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    return [{"id": request_id, "run": run} for request_id, run in rows]


def fail_runs(ws, conn, holder: str, *, words) -> list[str]:
    """The computer ``holder`` went (its token revoked, replaced or
    expired: gamma/computers/registry.py ``forget``), so none of its runs
    can write again: each request one of them still holds ``running``
    fails, ``words(agent)`` giving ``(by_name, detail)``, and every run of
    it still open is ``STOPPED``, in one write → the ids failed. Commits,
    then tells each one's pages and files."""
    write_lock(conn)
    try:
        rows = conn.execute("SELECT r.request_id, r.agent, q.state FROM feedback_request_runs r "
                            "LEFT JOIN feedback_requests q ON q.id = r.request_id WHERE r.holder = ? AND r.ended = '' "
                            "ORDER BY r.request_id", (holder,)).fetchall()
        if not rows:
            conn.rollback()
            return []
        failed, now = [], page_now()
        for request_id, agent, state in rows:
            if state == "running":
                by_name, detail = words(agent)
                conn.execute("UPDATE feedback_requests SET state = 'failed', state_at = ?, state_by = ?, detail = ?, "
                             "rev = ? WHERE id = ?", (now, by_name, detail[:MAX_DETAIL], _next_rev(conn), request_id))
                failed.append(request_id)
        conn.execute("UPDATE feedback_request_runs SET ended = ? WHERE holder = ? AND ended = ''", (STOPPED, holder))
        conn.commit()
    except BaseException:
        conn.rollback()
        raise
    for request_id in failed:
        _changed(ws, conn, request_id)
    return failed


def held_by(conn, holder: str, thread_id: str) -> bool:
    """Whether a run of the computer ``holder`` works on a request that
    names the thread ``thread_id``."""
    return bool(conn.execute("SELECT 1 FROM feedback_request_runs r JOIN feedback_request_threads t "
                             "ON t.request_id = r.request_id WHERE r.holder = ? AND r.ended = '' AND t.thread_id = ?",
                             (holder, thread_id)).fetchone())


def last_speakers(conn, thread_ids) -> list[dict]:
    """Who spoke last in each of the threads ``thread_ids`` (their live
    comments): ``[{id, by_agent, at, comment_id, people: [{id, at}]}]``,
    ``by_agent`` whether a token or an agent wrote the last comment, ``at``
    when it was last written, ``people`` the comments a person's session
    wrote (their ids and when each was last written): what a run's resume
    rule reads (gamma/local_agents/runner.py ``_spoken_after``)."""
    out: dict = {}
    for thread_id, comment_id, agent_id, updated in conn.execute(
            "SELECT thread_id, id, agent_id, updated_at FROM comments WHERE thread_id IN (SELECT value FROM json_each(?)) "
            "AND deleted_at IS NULL AND orphaned_at IS NULL ORDER BY created_at, id",
            (json.dumps(sorted(set(thread_ids))),)):
        said = out.setdefault(thread_id, {"id": thread_id, "people": []})
        said.update(by_agent=bool(agent_id), at=updated, comment_id=comment_id)
        if not agent_id:
            said["people"].append({"id": comment_id, "at": updated})
    return [out[t] for t in sorted(out)]


def spoken_after(conn, thread_id: str, seen: str, read) -> bool:
    """Whether a person (a session: no token, no agent) has a live comment
    in the thread that a run which read it at ``seen`` (the ids ``read``)
    did not read, or edited one after it read: words it never saw. By the
    ids, not the times, since a comment is stamped before its commit."""
    known = set(read or ())
    return any(comment_id not in known or updated > seen for comment_id, updated in conn.execute(
        "SELECT id, updated_at FROM comments WHERE thread_id = ? AND agent_id = '' AND deleted_at IS NULL "
        "AND orphaned_at IS NULL", (thread_id,)))


# --- the sweep --------------------------------------------------------------------------

# What the last sweep learned per workspace: ``(the files' stamp, when its
# next request expires)``, so a workspace whose files have not changed since
# is left unopened until then (as comments.sweep does).
_due: dict[str, tuple[tuple, datetime | None]] = {}


def sweep(*, now: datetime | None = None) -> dict[str, dict]:
    """In every workspace, delete the requests done or failed more than
    ``KEEP_DAYS`` ago and the rows of threads that no longer exist:
    ``{workspace: {requests, threads}}`` for the ones that lost some. A
    workspace that cannot be read is skipped; one whose pages.db has not
    changed since the last sweep is skipped until the expiry it found."""
    from ..trash import files_stamp  # local: trash imports ops, which imports comments

    now = now or datetime.now(timezone.utc)
    out = {}
    for ws in workspace_ids():
        stamp = files_stamp(ws)
        if stamp is None:
            _due.pop(ws, None)
            continue
        known = _due.get(ws)
        if known and known[0] == stamp and (known[1] is None or now < known[1]):
            continue
        try:
            gone, next_expiry = _sweep(ws, now)
            _due[ws] = (files_stamp(ws) or stamp, next_expiry)
        except Exception as e:  # noqa: BLE001 — one workspace never stops the sweep
            _due.pop(ws, None)
            log.warning(f"[feedback] sweep of workspace {ws}: {e}")
            continue
        if any(gone.values()):
            out[ws] = gone
            log.info(f"[feedback] deleted {gone['requests']} request(s) and {gone['threads']} thread row(s) "
                     f"of workspace {ws}")
    return out


def _sweep(ws: str, now: datetime) -> tuple[dict, datetime | None]:
    """One workspace's sweep: the old requests, the rows of threads gone
    and the computers' runs of requests gone."""
    cutoff = format_stamp(now - timedelta(days=KEEP_DAYS))
    with connect_pages_db(ws) as conn:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'feedback_requests'").fetchone():
            return {"requests": 0, "threads": 0}, None
        write_lock(conn)
        try:
            old = [r[0] for r in conn.execute(
                "SELECT id FROM feedback_requests WHERE state IN ('done', 'failed') AND state_at <= ?", (cutoff,))]
            if old:
                conn.execute("DELETE FROM feedback_requests WHERE id IN (SELECT value FROM json_each(?))",
                             (json.dumps(old),))
            threads = conn.execute(
                "DELETE FROM feedback_request_threads WHERE request_id NOT IN (SELECT id FROM feedback_requests) "
                "OR NOT EXISTS (SELECT 1 FROM comments c WHERE c.id = feedback_request_threads.thread_id)").rowcount
            conn.execute("DELETE FROM feedback_request_runs WHERE request_id NOT IN (SELECT id FROM feedback_requests)")
            oldest = conn.execute("SELECT MIN(state_at) FROM feedback_requests "
                                  "WHERE state IN ('done', 'failed')").fetchone()[0]
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
    at = parse_stamp(oldest) if oldest else None
    return {"requests": len(old), "threads": threads}, (at + timedelta(days=KEEP_DAYS) if at else None)

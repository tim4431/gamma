"""The Background tasks API over gamma/jobs.py (docs/dev/tasks.md): list,
read, stop, remove and download jobs.

A job is the business of the account that started it; a workspace's own
jobs (the search indexer) are seen by that workspace's members while they
work in it, and stopped or removed by its editors and owners. Jobs are
started by the endpoints of the work itself, ``POST /api/jobs/<kind>`` in
the router that owns it (export.py, imports.py, metadata.py, ws_backups.py,
admin.py).
The listing also shows the account's scheduled backup tasks while one runs
(gamma/backup_schedule.py, which keeps its own state): read-only rows of
kind ``scheduled-backup``; and the account's turns of the chat's computer
side while an agent on this computer has them queued or running, or they
wait for or run on another computer of the account's (step 4d): read-only
rows of kind ``computer-turn``; and the Sends an agent on this computer
answers in a work folder of the account's: read-only rows of kind
``feedback-run``.

A share view's exports are the share's (``auth.share_job_owner``): a
request carrying the ``?share=`` that started one reads, stops, removes and
downloads it by its id, for any visitor the share admits; the share view
lists no jobs.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import backup_schedule, fetch_handoff, jobs, workspaces
from ..auth import require_user_id, require_ws, share_job_owner, ws_role
from ..logbuf import log
from ..storage import attachment_disposition

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


def _context(request: Request) -> tuple[str, str, str | None]:
    """``(account, workspace, role)``: the request's workspace and the
    account's role in it, or ``("…", "", None)`` when the account is not a
    member of the workspace the request names."""
    user_id = require_user_id(request)
    try:
        ws = require_ws(request)
    except HTTPException:
        return user_id, "", None
    return user_id, ws, ws_role(request)


def _owner(request: Request) -> str:
    """Whose jobs the request reaches: the share's for a ``?share=``
    request (``auth.share_job_owner``), else the session account's."""
    return share_job_owner(request) or require_user_id(request)


def _job_for(request: Request, job_id: str, *, write: bool = False) -> dict:
    """The job with its result, if the account may see it (``write``: stop
    or remove it); 404 otherwise, as for a job that does not exist. A
    viewer asking to stop or remove a workspace's job gets 403. Through a
    share, only the share's own jobs."""
    owner = _owner(request)
    job = jobs.get(job_id, full=True)
    if job is not None:
        if job["owner"] == owner:
            return job
        if job["owner"] == jobs.WORKSPACE and job["workspace"] and not request.query_params.get("share"):
            role = workspaces.role_of(job["workspace"], owner)
            if write and role == "viewer":
                raise HTTPException(status_code=403, detail="you can only view this workspace")
            if role:
                return job
    raise HTTPException(status_code=404, detail="no such task")


def _running_backup_tasks(user_id: str) -> list[dict]:
    """The account's scheduled backup tasks that are queued or running now,
    in the jobs' shape. Finished runs stay in Settings → Backups (and a
    failed one raises the backup-failed notice)."""
    try:
        tasks = backup_schedule.list_tasks(user_id)
    except Exception:  # noqa: BLE001 — a damaged task file never breaks the tray; Settings → Backups shows it
        log.exception("[jobs] could not read the backup tasks")
        return []
    rows = []
    for task in tasks:
        if task.get("state") not in ("queued", "running"):
            continue
        rows.append({
            "id": f"backup-task-{task['id']}", "kind": "scheduled-backup", "owner": user_id, "workspace": "",
            "title": task["name"], "params": {"task_id": task["id"], "name": task["name"]},
            "state": task["state"], "progress": {}, "error": "", "created_at": task.get("last_run") or "",
            "started_at": task.get("last_run") or "", "finished_at": "", "artifact": None,
            "downloaded": False, "stoppable": False, "stopping": False, "readonly": True,
        })
    return rows


def _waiting_papers(user_id: str) -> list[dict]:
    """The account's blocked paper fetches still waiting for a PDF from
    their browser (gamma/fetch_handoff.py), in the jobs' shape: read-only
    rows, so a card whose reply has scrolled away is still somewhere to
    find. A click opens the publisher's page; the chat's card settles it."""
    return [{
        "id": f"paper-{req['id']}", "kind": "paper-handoff", "owner": user_id, "workspace": "",
        "title": req["host"] or req["source"],
        "params": {"request": req["id"], "host": req["host"], "wall": req["wall"],
                   "source": req["source"]},
        # Whose turn it is, in the phase words the tray already speaks.
        "state": "running", "progress": {"phase": "connector" if req["watched"] else "browser"},
        "error": "", "created_at": req["created_at"],
        "started_at": req["created_at"], "finished_at": "", "artifact": None,
        "downloaded": False, "stoppable": False, "stopping": False, "readonly": True,
    } for req in fetch_handoff.waiting(user_id)]


def _computer_turns(user_id: str) -> list[dict]:
    """The account's turns of the chat's computer side that this server's
    agents have queued or running (gamma/local_agents/runner.py
    ``chat_turns``), in the jobs' shape: read-only rows titled ``<agent> ·
    <page title>``, ``progress.phase`` ``asking`` while an ask waits for
    the person. A click opens the page's chat on that side, and Stop is the
    turn's own (``POST /api/local-agents/chat/turns/{turn}/stop``), never
    ``cancel``."""
    from ..db import connect_pages_db
    from ..local_agents import enabled, runner

    if not enabled():
        return []
    try:
        turns = runner.chat_turns(user_id)
    except Exception:  # noqa: BLE001 — the tray never breaks over the agents
        log.exception("[jobs] could not read the chat turns")
        return []
    rows = []
    for turn in reversed(turns):  # newest first, as the jobs
        try:
            with connect_pages_db(turn["ws"]) as conn:
                found = conn.execute("SELECT content FROM unified_blocks WHERE id = ?", (turn["page_id"],)).fetchone()
        except Exception:  # noqa: BLE001 — a workspace that cannot be read names no page
            found = None
        page_title = found[0] if found else ""
        rows.append(_turn_row(user_id, turn, page_title, asking=turn["asking"]))
    return rows


def _turn_row(user_id: str, turn: dict, page_title: str, *, asking: bool = False) -> dict:
    from ..local_agents import catalog

    label = catalog.label(turn["agent"])
    return {
        "id": f"computer-{turn['turn_id']}", "kind": "computer-turn", "owner": user_id, "workspace": turn["ws"],
        "title": f"{label} · {page_title or 'Untitled'}",
        "params": {"page": turn["page_id"], "turn": turn["turn_id"], "agent": turn["agent"], "label": label,
                   "title": page_title, **({"computer": turn["computer"]} if turn.get("computer") else {})},
        "state": turn["state"], "progress": {"phase": "asking"} if asking else {}, "error": "",
        "created_at": turn["queued_at"], "started_at": turn["started_at"], "finished_at": "", "artifact": None,
        "downloaded": False, "stoppable": not turn["stopping"], "stopping": turn["stopping"], "readonly": True,
    }


def _feedback_runs(user_id: str) -> list[dict]:
    """The Sends an agent on this computer answers now in a work folder the
    account made (runner ``send_runs``), in the jobs' shape: read-only rows
    titled ``<agent> · <file or folder>``, ``progress.item`` the agent's
    own words (the request's detail). A click opens the Feedback dialog on
    the Send's file or folder; Stop is its requester's withdraw, theirs
    alone (``POST /api/feedback/requests/{id}/state``)."""
    from ..db import connect_pages_db
    from ..feedback import requests
    from ..folder_links import get_link
    from ..local_agents import catalog, enabled, runner, store

    if not enabled():
        return []
    rows = []
    try:
        for held in runner.send_runs():
            link = get_link(held["link_id"])
            if not link or link["created_by"] != user_id:
                continue
            with connect_pages_db(held["ws"]) as conn:
                record = requests.get(conn, held["request_id"])
                mine = requests.requester(conn, held["request_id"]) == user_id
            if not record or record["state"] != "running":
                continue
            run = store.run_of(held["ws"], held["request_id"])
            label = catalog.label(run["agent"]) if run else record["state_by"]
            scope = record["scope"]
            rows.append({
                "id": f"feedback-{record['id']}", "kind": "feedback-run", "owner": user_id, "workspace": held["ws"],
                "title": f"{label} · {scope['name'] or 'Untitled'}",
                "params": {"request": record["id"], "label": label, "name": scope["name"],
                           "scope": {"kind": "file" if scope["kind"] == "file" else "folder", "id": scope["id"]},
                           "comments": len(record["threads"])},
                "state": "running", "progress": {"item": record["detail"]} if record["detail"] else {}, "error": "",
                "created_at": record["at"], "started_at": (run or {}).get("started_at") or record["state_at"],
                "finished_at": "", "artifact": None, "downloaded": False,
                "stoppable": mine and not held["stopping"], "stopping": held["stopping"], "readonly": True,
            })
    except Exception:  # noqa: BLE001 — the tray never breaks over the agents
        log.exception("[jobs] could not read the Sends the agents answer")
        return []
    return rows


def _far_turns(user_id: str, ws: str) -> list[dict]:
    """The account's turns of the chat's computer side queued or running on
    another computer (step 4d, ``chats.remote_turns``: kept in the
    workspaces its computers keep a work folder of with an agent, and the
    one the request works in), as ``_computer_turns`` gives them, newest
    first; Stop the same route."""
    from ..computers import registry
    from ..local_agents import chats

    rows = []
    try:
        for each in sorted({*registry.workspaces_of(user_id), *([ws] if ws else [])}):
            rows += [_turn_row(user_id, turn, turn["title"]) for turn in chats.remote_turns(each, user_id)]
    except Exception:  # noqa: BLE001 — the tray never breaks over the agents
        log.exception("[jobs] could not read the chat turns run on other computers")
        return []
    return sorted(rows, key=lambda row: row["created_at"], reverse=True)


@router.get("")
def list_jobs(request: Request):
    """``{jobs: [...]}``: the account's jobs (newest first, without their
    results), the running scheduled backups, the paper fetches waiting for
    their browser, the chat turns this computer's agents have queued or
    running and those queued or running on another computer, and the Sends
    this computer's agents answer in the account's work folders, plus the
    own jobs of the workspace the request works in."""
    user_id, ws, _ = _context(request)
    return {"jobs": _running_backup_tasks(user_id) + _waiting_papers(user_id) + _computer_turns(user_id)
            + _far_turns(user_id, ws) + _feedback_runs(user_id) + jobs.for_account(user_id, ws)}


@router.post("/clear")
def clear_jobs(request: Request):
    """Remove every finished job the account may remove: its own, and the
    workspace's own jobs when it may change the workspace."""
    user_id, ws, role = _context(request)
    return {"removed": jobs.clear(user_id, ws if role and role != "viewer" else "")}


@router.get("/{job_id}")
def read_job(job_id: str, request: Request):
    return _job_for(request, job_id)


@router.post("/{job_id}/cancel")
def cancel_job(job_id: str, request: Request):
    """Stop the job: at once while it waits, at its next step while it runs
    (409 once it can no longer stop — a restore swapping the databases)."""
    _job_for(request, job_id, write=True)
    return jobs.cancel(job_id)


@router.delete("/{job_id}")
def dismiss_job(job_id: str, request: Request):
    """Remove a finished job and its file (409 while it runs)."""
    _job_for(request, job_id, write=True)
    jobs.dismiss(job_id)
    return {"ok": True}


@router.get("/{job_id}/download")
def download_job(job_id: str, request: Request):
    """The file a finished job produced (the account's own jobs only, or
    the share's through its ``?share=``). A plain link works: the browser's
    download manager shows its progress."""
    job = _job_for(request, job_id)
    found = jobs.artifact(job_id) if job["owner"] == _owner(request) else None
    if found is None:
        raise HTTPException(status_code=404, detail="this task has no file to download (it expired or was removed)")
    path, name, media_type = found
    jobs.mark_downloaded(job_id)
    return FileResponse(path, media_type=media_type, headers={"Content-Disposition": attachment_disposition(name)})

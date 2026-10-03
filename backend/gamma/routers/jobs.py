"""The Background tasks API over gamma/jobs.py (docs/dev/tasks.md): list,
read, stop, remove and download jobs.

A job is the business of the account that started it; a workspace's own
jobs (the search indexer) are seen by that workspace's members while they
work in it, and stopped or removed by its editors and owners. Jobs are
started by the endpoints of the work itself, ``POST /api/jobs/<kind>`` in
the router that owns it (export.py, imports.py, ws_backups.py, admin.py).
The listing also shows the account's scheduled backup tasks while one runs
(gamma/backup_schedule.py, which keeps its own state): read-only rows of
kind ``scheduled-backup``.
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

from .. import backup_schedule, fetch_handoff, jobs, workspaces
from ..auth import require_user_id, require_ws, ws_role
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


def _job_for(request: Request, job_id: str, *, write: bool = False) -> dict:
    """The job with its result, if the account may see it (``write``: stop
    or remove it); 404 otherwise, as for a job that does not exist. A
    viewer asking to stop or remove a workspace's job gets 403."""
    user_id = require_user_id(request)
    job = jobs.get(job_id, full=True)
    if job is not None:
        if job["owner"] == user_id:
            return job
        if job["owner"] == jobs.WORKSPACE and job["workspace"]:
            role = workspaces.role_of(job["workspace"], user_id)
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


@router.get("")
def list_jobs(request: Request):
    """``{jobs: [...]}``: the account's jobs (newest first, without their
    results), the running scheduled backups and the paper fetches waiting
    for their browser, plus the own jobs of the workspace the request works
    in."""
    user_id, ws, _ = _context(request)
    return {"jobs": _running_backup_tasks(user_id) + _waiting_papers(user_id) + jobs.for_account(user_id, ws)}


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
    """The file a finished job produced (the account's own jobs only). A
    plain link works: the browser's download manager shows its progress."""
    job = _job_for(request, job_id)
    found = jobs.artifact(job_id) if job["owner"] == require_user_id(request) else None
    if found is None:
        raise HTTPException(status_code=404, detail="this task has no file to download (it expired or was removed)")
    path, name, media_type = found
    jobs.mark_downloaded(job_id)
    return FileResponse(path, media_type=media_type, headers={"Content-Disposition": attachment_disposition(name)})

"""Workspace backups over HTTP; the zip itself and the stored snapshots are
gamma/ws_backup.py.

- ``transfers`` (``/api``): a workspace as a download (``/export``; every
  personal workspace of the account at once, ``/export-all``) and a zip
  restored or merged into one (``/import-data``). These answer when the work
  is done, for scripts. The web app starts the same work as background
  jobs (gamma/jobs.py, docs/dev/tasks.md): ``POST /api/jobs/workspace-export``,
  ``/jobs/restore`` (an uploaded zip), ``/jobs/restore-snapshot`` and
  ``/jobs/snapshot``.
- ``router`` (``/api/workspaces/{ws}/backups``): the snapshots Settings →
  Backups manages — take one, list, download, restore in place, delete.

Who may do what: any member (or admin) exports, lists and downloads; an
owner takes, restores and deletes snapshots, and a merge needs an editor.
A guest's workspace keeps no snapshots and takes no restore: it goes away
with the guest (docs/dev/guests.md).
"""

import os
import shutil
import tempfile
import zipfile
from pathlib import Path

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from .. import jobs, workspaces, ws_backup
from ..auth import require_user_id, requested_ws
from ..db import account_id, connect_users_db, page_now, ws_dir
from ..storage import display_filename
from .workspaces import _member

router = APIRouter(prefix="/api/workspaces/{ws}/backups", tags=["backups"])
transfers = APIRouter(prefix="/api", tags=["backups"])

MODES = ("replace", "merge")


def _not_guest(ws: str) -> None:
    if workspaces.is_guest_workspace(ws):
        raise HTTPException(status_code=403, detail="a guest workspace keeps no backups")


def _named(ws: str, name: str) -> dict:
    b = ws_backup.info(ws, name)
    if not b:
        raise HTTPException(status_code=404, detail="no such backup")
    return b


def _mode(mode: str) -> str:
    if mode not in MODES:
        raise HTTPException(status_code=400, detail="mode must be 'replace' or 'merge'")
    return mode


def _target_ws(request: Request, ws: str | None, user: str | None, needed: str) -> str:
    """The workspace an export or restore applies to. ``ws`` names one (else
    the request's usual workspace); ``user`` — a username, admins only —
    means that account's personal workspace (the Settings → Users rows).
    The caller must hold ``needed`` (viewer / editor / owner) in it; server
    admins pass every check, because a backup is how they rescue an
    account."""
    me = require_user_id(request)
    if user and user != request.state.user:
        if not request.state.is_admin:
            raise HTTPException(status_code=403, detail="admin privilege required")
        with connect_users_db() as conn:
            target = workspaces.default_workspace(account_id(conn, user))
        if not target:
            raise HTTPException(status_code=404, detail="no such user")
        return target
    target = ws or requested_ws(request) or request.state.default_ws
    if not workspaces.get(target):
        raise HTTPException(status_code=404, detail="workspace not found")
    if request.state.is_admin:
        return target
    role = workspaces.role_of(target, me)
    if not role:
        raise HTTPException(status_code=404, detail="workspace not found")
    if not workspaces.at_least(role, needed):
        raise HTTPException(status_code=403, detail=f"only a workspace {needed} can do that")
    return target


def _restorable(request: Request, ws: str | None, user: str | None, mode: str) -> str:
    """The workspace a zip may be restored (owners) or merged (editors) into."""
    target = _target_ws(request, ws, user, "owner" if _mode(mode) == "replace" else "editor")
    if workspaces.is_guest_workspace(target):
        raise HTTPException(status_code=403, detail="a guest workspace cannot import backups")
    return target


def _name_of(ws: str) -> str:
    return (workspaces.get(ws) or {}).get("name", "") or ws


def _slug(ws: str) -> str:
    return "".join(c if c.isalnum() else "-" for c in _name_of(ws)).strip("-")[:40] or ws


def _export_name(ws: str, uploads: bool) -> str:
    return f"gamma-export{'' if uploads else '-db'}-{_slug(ws)}-{page_now()[:10]}.zip"


def _export_all_name(username: str, uploads: bool) -> str:
    return f"gamma-export-all{'' if uploads else '-db'}-{username}-{page_now()[:10]}.zip"


def _write_all(targets: list[str], dest: Path, uploads: bool, by: str, progress=jobs.no_progress) -> None:
    """Every workspace of ``targets`` as its own export zip inside one zip
    (``<name>-<id>.zip``; each restores on its own through /import-data).
    ``progress``: a job's report, per workspace."""
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_STORED) as bundle:  # the inner zips are compressed
        for n, target in enumerate(targets):
            progress(done=n, total=len(targets), unit="workspaces", item=_name_of(target))
            inner = Path(f"{dest}.{target}.zip")
            try:
                ws_backup.write_zip(target, inner, uploads=uploads, by=by, progress=lambda **_: progress())
                bundle.write(inner, f"{_slug(target)}-{target}.zip")
            finally:
                inner.unlink(missing_ok=True)
    progress(done=len(targets), total=len(targets), unit="workspaces", item="")


def _temp_zip() -> Path:
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    return Path(tmp.name)


def _zip_download(path: Path, name: str) -> FileResponse:
    return FileResponse(str(path), media_type="application/zip", filename=name,
                        background=BackgroundTask(os.unlink, str(path)))


# --- the work in the request (scripts) ----------------------------------------------

# Sync endpoint on purpose: zipping a large library runs in the threadpool.
@transfers.get("/export")
def export_data(request: Request, uploads: int = 1, ws: str | None = None, user: str | None = None):
    """Full backup of a workspace as a zip (gamma/ws_backup.py): consistent
    SQLite snapshots plus every uploaded file; `uploads=0` skips the files
    for a small database-only backup. Restoring = /api/import-data into any
    workspace. Defaults to the request's workspace; any member may export
    it, and admins any workspace (?ws=) or account (?user=)."""
    target = _target_ws(request, ws, user, "viewer")
    if not ws_dir(target).exists():
        raise HTTPException(status_code=404, detail="no data for this workspace yet")
    tmp = _temp_zip()
    try:
        ws_backup.write_zip(target, tmp, uploads=bool(uploads), by=request.state.user_id)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _zip_download(tmp, _export_name(target, bool(uploads)))


# Sync on purpose, like /export.
@transfers.get("/export-all")
def export_all(request: Request, uploads: int = 1):
    """Every personal workspace of the session account in one zip — one
    /api/export zip per workspace inside (``<name>-<id>.zip``), each of
    which restores on its own through /api/import-data."""
    me = require_user_id(request)
    if request.state.is_guest:
        raise HTTPException(status_code=403, detail="a guest account has nothing to export as a whole")
    tmp = _temp_zip()
    try:
        _write_all(workspaces.personal_workspaces(me), tmp, bool(uploads), me)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise
    return _zip_download(tmp, _export_all_name(request.state.user, bool(uploads)))


# Sync on purpose: unzip + sqlite restore runs in the threadpool.
@transfers.post("/import-data")
def import_data(request: Request, file: UploadFile = File(...), mode: str = "replace",
                ws: str | None = None, user: str | None = None):
    """Restore an /api/export zip into a workspace (the request's, ``?ws=``,
    or — admins only — the personal workspace of ``?user=``): mode=replace
    (default, owners) swaps the databases, mode=merge (editors) adds what is
    missing — gamma/ws_backup.restore_zip. Nothing can be imported into a
    guest's workspace: it goes away with the guest (docs/dev/guests.md)."""
    target = _restorable(request, ws, user, mode)
    with tempfile.TemporaryDirectory(prefix="gamma-import-") as td:
        zpath = Path(td) / "backup.zip"
        with open(zpath, "wb") as out:
            shutil.copyfileobj(file.file, out)
        try:
            return {"ok": True, **ws_backup.restore_zip(target, zpath, mode, by=request.state.user_id)}
        except ws_backup.BackupError as e:
            raise HTTPException(status_code=400, detail=str(e))


# --- the same work as background jobs (the web app) -------------------------------

class WorkspaceExportJob(BaseModel):
    ws: str = ""        # the request's workspace when empty
    user: str = ""      # admins: that account's personal workspace
    all: bool = False   # every personal workspace of the account instead
    uploads: bool = True


@transfers.post("/jobs/workspace-export")
def start_workspace_export(payload: WorkspaceExportJob, request: Request):
    """``/export`` or ``/export-all`` as a job whose file is the zip."""
    me = require_user_id(request)
    uploads = payload.uploads
    if payload.all:
        if request.state.is_guest:
            raise HTTPException(status_code=403, detail="a guest account has nothing to export as a whole")
        targets, username = workspaces.personal_workspaces(me), request.state.user

        def run_all(job):
            _write_all(targets, job.artifact_path, uploads, me, job.progress)
            job.set_artifact(_export_all_name(username, uploads), "application/zip")
            return {"workspaces": len(targets)}

        return jobs.start("workspace-export", owner=me, run=run_all, artifact=True,
                          title="Export of every personal workspace",
                          params={"all": True, "uploads": uploads, "count": len(targets)})
    target = _target_ws(request, payload.ws or None, payload.user or None, "viewer")
    if not ws_dir(target).exists():
        raise HTTPException(status_code=404, detail="no data for this workspace yet")

    def run(job):
        manifest = ws_backup.write_zip(target, job.artifact_path, uploads=uploads, by=me, progress=job.progress)
        job.set_artifact(_export_name(target, uploads), "application/zip")
        return {"upload_files": manifest["upload_files"], "missing_uploads": len(manifest["missing_uploads"])}

    return jobs.start("workspace-export", owner=me, ws=target, run=run, artifact=True,
                      title=f"Export of {_name_of(target)}",
                      params={"ws": target, "name": _name_of(target), "uploads": uploads, "user": payload.user})


class SnapshotJob(BaseModel):
    workspaces: list[str]
    uploads: bool = True
    label: str = "manual"


@transfers.post("/jobs/snapshot")
def start_snapshot(payload: SnapshotJob, request: Request):
    """Take a snapshot of each workspace named (their owner): one job, one
    workspace after the other. A workspace whose snapshot fails (a full
    store) is listed in the result's ``failed``; the job fails only when
    every one did."""
    me = require_user_id(request)
    targets = list(dict.fromkeys(ws for ws in payload.workspaces if ws))
    if not targets:
        raise HTTPException(status_code=400, detail="name the workspaces to back up")
    for ws in targets:
        _member(request, ws, "owner")
        _not_guest(ws)
    label = payload.label.strip() or "manual"
    if not ws_backup.LABEL_RE.match(label):
        raise HTTPException(status_code=400, detail="label must be 1-40 chars of letters, digits, _ . -")
    names = {ws: _name_of(ws) for ws in targets}
    single = len(targets) == 1

    def run(job):
        made, failed = [], []
        for n, ws in enumerate(targets):
            if not single:
                job.progress(done=n, total=len(targets), unit="workspaces", item=names[ws])
            try:
                snapshot = ws_backup.create(ws, label=label, uploads=payload.uploads, by=me,
                                            progress=job.progress if single else (lambda **_: job.progress()))
                made.append({**snapshot, "workspace": ws})
            except ws_backup.BackupError as e:
                failed.append({"workspace": ws, "name": names[ws], "error": str(e)})
        if not single:
            job.progress(done=len(targets), total=len(targets), unit="workspaces", item="")
        if failed and not made:
            raise ws_backup.BackupError(failed[0]["error"] if single else
                                        "; ".join(f"{f['name']}: {f['error']}" for f in failed))
        return {"snapshots": made, "failed": failed}

    return jobs.start("snapshot", owner=me, ws=targets[0] if single else "", run=run,
                      title=f"Snapshot of {names[targets[0]]}" if single else f"Snapshots of {len(targets)} workspaces",
                      params={"workspaces": targets, "names": [names[ws] for ws in targets],
                              "uploads": payload.uploads, "label": label})


# Sync def: the upload is spooled to disk in the threadpool before the job starts.
@transfers.post("/jobs/restore")
def start_restore(request: Request, file: UploadFile = File(...), mode: str = Form("replace"),
                  ws: str = Form(""), user: str = Form("")):
    """``/import-data`` as a job: the upload is kept until the job has read
    it; the result is the restore's report."""
    me = require_user_id(request)
    target = _restorable(request, ws or None, user or None, mode)
    filename = display_filename(file.filename, "backup.zip")
    zpath = jobs.incoming_path()
    try:
        with zpath.open("wb") as out:
            shutil.copyfileobj(file.file, out)

        def run(job):
            try:
                return ws_backup.restore_zip(target, zpath, mode, by=me, progress=job.progress)
            finally:
                zpath.unlink(missing_ok=True)

        return jobs.start("restore", owner=me, ws=target, run=run, title=f"Restore {filename}",
                          params={"ws": target, "name": _name_of(target), "mode": mode, "source": "upload",
                                  "filename": filename, "size": zpath.stat().st_size})
    except BaseException:
        zpath.unlink(missing_ok=True)
        raise


class SnapshotRestoreJob(BaseModel):
    ws: str
    name: str
    mode: str = "replace"


@transfers.post("/jobs/restore-snapshot")
def start_snapshot_restore(payload: SnapshotRestoreJob, request: Request):
    """Restore (owner) or merge (editor) a stored snapshot into its
    workspace as a job, like ``/backups/{name}/restore``."""
    ws, mode = payload.ws, _mode(payload.mode)
    me = _member(request, ws, "owner" if mode == "replace" else "editor")
    _not_guest(ws)
    snapshot = _named(ws, payload.name)
    path = ws_backup.backup_path(ws, payload.name)

    def run(job):
        return ws_backup.restore_zip(ws, path, mode, by=me, progress=job.progress)

    return jobs.start("restore", owner=me, ws=ws, run=run, title=f"Restore of {_name_of(ws)}",
                      params={"ws": ws, "name": _name_of(ws), "mode": mode, "source": "snapshot",
                              "snapshot": payload.name, "snapshot_at": snapshot.get("created_at", "")})


# --- stored snapshots ---------------------------------------------------------------

class BackupCreate(BaseModel):
    label: str = "manual"
    uploads: bool = True


@router.get("")
def list_backups(ws: str, request: Request):
    """``{backups: [{name, size_bytes, created_at, label, uploads,
    upload_files, by, scheduled, task_id, auto, missing_uploads,
    damaged}]}`` (``ws_backup.info``), newest first, plus the per-workspace
    cap."""
    _member(request, ws, "viewer")
    return {"backups": ws_backup.list_backups(ws), "max": ws_backup.MAX_PER_WORKSPACE}


# Sync def: zipping a library runs in the threadpool.
@router.post("")
def create_backup(ws: str, payload: BackupCreate, request: Request):
    """Take a snapshot now (owner): the databases, plus every upload unless
    ``uploads`` is false. (The web app takes one as a job: /jobs/snapshot.)"""
    user_id = _member(request, ws, "owner")
    _not_guest(ws)
    try:
        return ws_backup.create(ws, label=payload.label.strip() or "manual", uploads=payload.uploads, by=user_id)
    except ws_backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{name}/download")
def download_backup(ws: str, name: str, request: Request):
    _member(request, ws, "viewer")
    _named(ws, name)
    return FileResponse(str(ws_backup.backup_path(ws, name)), media_type="application/zip",
                        filename=f"gamma-backup-{_slug(ws)}-{name}.zip")


# Sync def: unzip + sqlite restore runs in the threadpool.
@router.post("/{name}/restore")
def restore_backup(ws: str, name: str, request: Request, mode: str = "replace"):
    """Restore the snapshot into its workspace: ``replace`` (owner) keeps the
    current state as a ``pre-restore`` snapshot (its name in
    ``pre_restore``) and swaps the databases, ``merge`` (editor) adds what
    is missing — the same rules as /api/import-data. (The web app restores
    as a job: /jobs/restore-snapshot.)"""
    user_id = _member(request, ws, "owner" if _mode(mode) == "replace" else "editor")
    _not_guest(ws)
    _named(ws, name)
    try:
        return {"ok": True, **ws_backup.restore_zip(ws, ws_backup.backup_path(ws, name), mode, by=user_id)}
    except ws_backup.BackupError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{name}")
def delete_backup(ws: str, name: str, request: Request):
    _member(request, ws, "owner")
    if not ws_backup.delete(ws, name):
        raise HTTPException(status_code=404, detail="no such backup")
    return {"ok": True}

"""The backup tasks' snapshots in the bucket (docs/dev/debugging.md
"Snapshots and copies in a bucket").

A backup task with ``bucket`` on (gamma/backup_schedule.py) uploads each
snapshot it takes, whole, to ``<prefix>snapshots/<ws>/<name>.zip``: the
snapshot's own name, so a copy and the snapshot it came from match. Beside
the copies, ``snapshots/<ws>/workspace.json`` (``{id, name, kind}``) names
the workspace, so a server that does not have it (a new one after a lost
disk) can still say whose copies they are. The task's retention then prunes
its copies in the bucket as it prunes its snapshots on the server; the
copies of other tasks are never touched. The bucket has no schedule of its
own: it holds what the tasks send it.

The bucket is the one Settings → Backups sets up (gamma/offsite.py
``settings``: saved by an admin, or from the environment). Admins read it
from the same pane: ``listing`` (one listing of ``snapshots/``), ``fetch``
(one copy down into a local file, for a download or a restore) and
``delete``.
"""

import json
import re
import time
from datetime import timedelta
from pathlib import Path

from . import offsite, s3, workspaces, ws_backup
from .db import safe_ws_id

SPACE = "snapshots"
MARKER = "workspace.json"
_TASK_RE = re.compile(r"^\d{8}-\d{6}-task-([0-9a-f]{12})(?:\.\d+)?$")  # backup_schedule's label, task-<id[:12]>


def ready() -> bool:
    """Whether a bucket is set up."""
    return bool(offsite.settings()["bucket"])


def client(**kwargs) -> s3.Client:
    """The bucket set up in Settings → Backups. S3ConfigError without one."""
    conf = offsite.settings()
    if not conf["bucket"]:
        raise s3.S3ConfigError("no bucket is set up: an admin sets one up in Settings → Backups")
    return s3.Client.from_settings(conf, **kwargs)


def key(ws: str, name: str) -> str:
    """The object of one snapshot's copy. ValueError for a name no snapshot
    has."""
    if not ws_backup.NAME_RE.match(name or ""):
        raise ValueError(f"no snapshot is named {name!r}")
    return f"{SPACE}/{safe_ws_id(ws)}/{name}.zip"


def upload(bucket: s3.Client, ws: str, name: str) -> None:
    """Copy the stored snapshot ``name`` up, and the workspace's
    ``workspace.json`` beside it."""
    bucket.put_object(key(ws, name), ws_backup.backup_path(ws, name))
    info = workspaces.get(ws) or {}
    about = {"id": ws, "name": info.get("name", ""), "kind": info.get("kind", "")}
    bucket.put_bytes(f"{SPACE}/{ws}/{MARKER}", json.dumps(about).encode("utf-8"))


def _copy(file: str, size: int, mtime: float) -> dict | None:
    """One listed object as a copy, None for anything else (the marker)."""
    name = file[:-4] if file.endswith(".zip") else ""
    if "/" in file or not ws_backup.NAME_RE.match(name):
        return None
    task = _TASK_RE.match(name)
    return {"name": name, "size_bytes": size, "mtime": mtime, "task": task.group(1) if task else "",
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(mtime))}


def _newest_first(copies: list[dict]) -> list[dict]:
    return sorted(copies, key=lambda c: (c["mtime"], c["name"]), reverse=True)


def prune(bucket: s3.Client, ws: str, task: dict, at) -> int:
    """Remove the task's copies of the workspace past its retention, as
    backup_schedule._prune removes its snapshots: all but the newest
    ``retention_value``, or those older than that many days but the
    newest. How many went."""
    prefix = f"{SPACE}/{safe_ws_id(ws)}/"
    mine = _newest_first([c for k, size, mtime in bucket.list_objects(prefix)
                          if (c := _copy(k[len(prefix):], size, mtime)) and c["task"] == task["id"][:12]])
    if task["retention_mode"] == "count":
        expired = mine[task["retention_value"]:]
    else:
        cutoff = (at - timedelta(days=task["retention_value"])).timestamp()
        expired = [c for c in mine[1:] if c["mtime"] < cutoff]
    for copy in expired:
        bucket.delete_object(key(ws, copy["name"]))
    return len(expired)


def _recorded(bucket: s3.Client, ws: str) -> dict:
    """What ``workspace.json`` says of a workspace this server lacks, {}
    when it says nothing readable."""
    try:
        about = json.loads(bucket.get_bytes(f"{SPACE}/{ws}/{MARKER}") or b"{}")
    except (OSError, ValueError):
        return {}
    return about if isinstance(about, dict) else {}


def listing() -> list[dict]:
    """Every workspace the bucket holds copies of: ``[{workspace, name,
    here, snapshots: [{name, size_bytes, created_at, task}]}]``, the copies
    newest first (``created_at`` is when the copy was sent), the workspaces
    this server has first. ``name`` is this server's, else the one
    ``workspace.json`` recorded ("" when there is none). S3Error or
    S3ConfigError when the bucket cannot be read."""
    bucket = client()
    groups: dict[str, list] = {}
    for object_key, size, mtime in bucket.list_objects(f"{SPACE}/"):
        parts = object_key.split("/")
        if len(parts) != 3:
            continue
        try:
            safe_ws_id(parts[1])
        except ValueError:
            continue
        if copy := _copy(parts[2], size, mtime):
            groups.setdefault(parts[1], []).append(copy)
    out = []
    for ws, copies in groups.items():
        here = workspaces.get(ws)
        name = here["name"] if here else str(_recorded(bucket, ws).get("name") or "")
        out.append({"workspace": ws, "name": name, "here": bool(here),
                    "snapshots": [{k: v for k, v in c.items() if k != "mtime"} for c in _newest_first(copies)]})
    return sorted(out, key=lambda g: (not g["here"], g["name"].lower(), g["workspace"]))


def fetch(ws: str, name: str, dest: Path) -> None:
    """Download the copy into ``dest``. FileNotFoundError when the bucket
    has no such copy."""
    object_key = key(ws, name)
    if not client().get_object(object_key, dest):
        raise FileNotFoundError(f"the bucket holds no copy {name} of this workspace")


def delete(ws: str, name: str) -> None:
    client().delete_object(key(ws, name))

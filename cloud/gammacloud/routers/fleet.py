"""What a host's fleet agent calls with its host token (``Authorization:
Bearer``): the next job (long-polled), a job's result, the heartbeat.
docs/dev/hosted.md."""

import time
from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import db, fleet

router = APIRouter(prefix="/api/fleet")


def _host(conn, request: Request):
    auth = request.headers.get("authorization", "")
    host = fleet.host_by_token(conn, auth[7:].strip()) if auth.lower().startswith("bearer ") else None
    if host is None:
        raise HTTPException(401, "unknown host token", headers={"WWW-Authenticate": "Bearer"})
    return host


@router.get("/jobs")
def next_job(request: Request, wait: float = 0):
    """The host's next queued job, marked running, or ``{"job": null}``
    after ``wait`` seconds (at most ``fleet.MAX_WAIT``). A plain ``def``:
    it waits on a worker thread, never on the event loop."""
    deadline = time.monotonic() + max(0.0, min(wait, fleet.MAX_WAIT))
    with closing(db.connect()) as conn:
        host = _host(conn, request)
        while True:
            job = fleet.claim(conn, host["id"])
            left = deadline - time.monotonic()
            if job is not None or left <= 0:
                return {"job": job}
            fleet.wait_for_work(min(1.0, left))


class JobResult(BaseModel):
    state: str
    result: dict | str | None = None


@router.post("/jobs/{job_id}")
def finish_job(job_id: str, body: JobResult, request: Request):
    with closing(db.connect()) as conn:
        host = _host(conn, request)
        db.begin_write(conn)
        job = fleet.complete(conn, host, job_id, body.state, body.result)
        conn.commit()
    return {"job": job}


@router.post("/heartbeat")
def heartbeat(body: dict, request: Request):
    with closing(db.connect()) as conn:
        host = _host(conn, request)
        db.begin_write(conn)
        fleet.heartbeat(conn, host, body)
        conn.commit()
    return {"ok": True}

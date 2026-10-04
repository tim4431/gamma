"""The fleet: the hosts hosted servers run on, and the job queue their
agents work through (docs/dev/hosted.md). Every host runs one agent
(``cloud/fleet/``) with the Docker socket; it makes outbound calls only,
authenticates with its host token, long-polls for its next job, reports the
result and posts a heartbeat every five minutes. The account server never
touches Docker.

A job is ``queued`` (the agent may take it), ``held`` (a later wave of an
upgrade run), ``running`` (handed to the agent), ``done``, ``failed`` or
``canceled``. ``hosted.py`` decides which jobs to enqueue; this module
stores, hands out and completes them, places new servers on a host and
releases upgrade waves.
"""

import json
import re
import threading

from . import config, db
from .accounts import Problem
from .log import log

KINDS = ("create", "start", "stop", "restart", "delete", "upgrade")
STALE_AFTER = 15 * 60          # a host silent this long takes no new placements
JOB_TIMEOUT = 3600             # a running job with no result after this is failed
MAX_WAIT = 30                  # the longest a job poll is held open
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
HOST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")

# Wakes a held job poll when a job is enqueued. The poll re-reads the
# database at least every second anyway, since the enqueue is only visible
# once its transaction commits.
_wake = threading.Condition()


def wake() -> None:
    with _wake:
        _wake.notify_all()


def wait_for_work(seconds: float) -> None:
    with _wake:
        _wake.wait(seconds)


# --- hosts --------------------------------------------------------------------

def host_by_token(conn, token: str):
    if not token:
        return None
    return conn.execute("SELECT * FROM hosts WHERE token_hash = ?", (db.token_hash(token),)).fetchone()


def add_host(conn, name: str, address: str = "", actor: str = "") -> tuple[dict, str]:
    """A new host and its agent token (shown once; stored as its hash)."""
    name = (name or "").strip()
    if not HOST_NAME_RE.match(name):
        raise Problem(400, "A host name is 1 to 63 letters, digits, dots, hyphens and underscores.")
    if conn.execute("SELECT 1 FROM hosts WHERE name = ?", (name,)).fetchone():
        raise Problem(409, "A host with that name exists.")
    host_id, token = "h_" + db.new_token(9), "gf_" + db.new_token(32)
    conn.execute("INSERT INTO hosts (id, name, address, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                 (host_id, name, (address or "").strip()[:200], db.token_hash(token), db.now()))
    db.audit(conn, "fleet.host_add", actor=actor, detail=f"{host_id} {name}")
    return public_host(conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()), token


def update_host(conn, host_id: str, *, name: str | None = None, accepting: bool | None = None,
                actor: str = "") -> dict:
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    if name is not None and name.strip() != row["name"]:
        name = name.strip()
        if not HOST_NAME_RE.match(name):
            raise Problem(400, "A host name is 1 to 63 letters, digits, dots, hyphens and underscores.")
        if conn.execute("SELECT 1 FROM hosts WHERE name = ? AND id != ?", (name, host_id)).fetchone():
            raise Problem(409, "A host with that name exists.")
        conn.execute("UPDATE hosts SET name = ? WHERE id = ?", (name, host_id))
        db.audit(conn, "fleet.host_rename", actor=actor, detail=f"{host_id} {name}")
    if accepting is not None:
        conn.execute("UPDATE hosts SET accepting = ? WHERE id = ?", (1 if accepting else 0, host_id))
        db.audit(conn, "fleet.host_accepting", actor=actor, detail=f"{host_id} {'on' if accepting else 'off'}")
    return public_host(conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone())


def is_stale(host) -> bool:
    return not host["last_seen_at"] or host["last_seen_at"] < db.after(-STALE_AFTER)


def public_host(row) -> dict:
    out = {k: row[k] for k in row.keys() if k != "token_hash"}
    out["accepting"] = bool(row["accepting"])
    out["stale"] = is_stale(row)
    return out


def hosts(conn) -> list[dict]:
    counts = dict(conn.execute("SELECT host_id, COUNT(*) FROM hosted_servers WHERE state != 'deleted' "
                               "GROUP BY host_id").fetchall())
    return [{**public_host(r), "servers": counts.get(r["id"], 0)}
            for r in conn.execute("SELECT * FROM hosts ORDER BY created_at").fetchall()]


def place(conn, plan: str):
    """The host a new server of ``plan`` goes on: an accepting, fresh host
    with more free disk than the plan's quota, the most free memory first.
    None when no host has room."""
    quota = config.PLAN_LIMITS[plan]["quota_mb"]
    return conn.execute(
        "SELECT * FROM hosts WHERE accepting = 1 AND last_seen_at >= ? AND disk_mb - disk_used_mb > ? "
        "ORDER BY memory_mb - memory_used_mb DESC, created_at LIMIT 1",
        (db.after(-STALE_AFTER), quota)).fetchone()


def heartbeat(conn, host, body: dict) -> None:
    """What an agent reports every five minutes: the host's capacity and
    use, and each container it runs (merged into that server's ``report``
    under ``agent``)."""
    ts = db.now()
    conn.execute("UPDATE hosts SET agent_version = ?, memory_mb = ?, disk_mb = ?, memory_used_mb = ?, "
                 "disk_used_mb = ?, last_seen_at = ? WHERE id = ?",
                 (str(body.get("agent_version") or "")[:40], nonneg(body.get("memory_mb")), nonneg(body.get("disk_mb")),
                  nonneg(body.get("memory_used_mb")), nonneg(body.get("disk_used_mb")), ts, host["id"]))
    for c in body.get("containers") or []:
        if not isinstance(c, dict):
            continue
        row = conn.execute("SELECT id, report FROM hosted_servers WHERE label = ? AND host_id = ?",
                           (str(c.get("label") or ""), host["id"])).fetchone()
        if not row:
            continue
        report = json_dict(row["report"])
        report["agent"] = {"running": bool(c.get("running")), "health": str(c.get("health") or "")[:40],
                           "memory_mb": nonneg(c.get("memory_mb")), "data_mb": nonneg(c.get("data_mb")),
                           "image": str(c.get("image") or "")[:200], "last_seen_at": ts}
        conn.execute("UPDATE hosted_servers SET report = ?, reported_at = ? WHERE id = ?",
                     (json.dumps(report), ts, row["id"]))


def stale_hosts(conn) -> list[str]:
    """The alarm for hosts silent past STALE_AFTER: a log warning on every
    tick and one audit row per silence. Nothing is changed: ``place``
    already skips a stale host, and its first heartbeat back makes it a
    candidate again; ``accepting`` stays the admin's choice."""
    rows = conn.execute("SELECT * FROM hosts WHERE accepting = 1 AND last_seen_at IS NOT NULL AND last_seen_at < ?",
                        (db.after(-STALE_AFTER),)).fetchall()
    for r in rows:
        log.warning("fleet host %s silent since %s: no new placements", r["name"], r["last_seen_at"])
        detail = f"{r['id']} {r['name']} last seen {r['last_seen_at']}"
        if not conn.execute("SELECT 1 FROM audit WHERE event = 'fleet.host_stale' AND detail = ?", (detail,)).fetchone():
            db.audit(conn, "fleet.host_stale", actor="system", detail=detail)
    return [r["name"] for r in rows]


# --- jobs ---------------------------------------------------------------------

def json_dict(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def nonneg(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def enqueue(conn, host_id: str, server_id: str, kind: str, payload: dict, *, wave: str = "",
            state: str = "queued") -> str | None:
    """A job for a host's agent; its id, or None when the server has no
    host yet (nothing to do there)."""
    assert kind in KINDS, kind
    if not host_id:
        return None
    job_id = "j_" + db.new_token(9)
    conn.execute("INSERT INTO fleet_jobs (id, host_id, server_id, kind, payload, state, wave, created_at) "
                 "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                 (job_id, host_id, server_id, kind, json.dumps(payload), state, wave, db.now()))
    wake()
    return job_id


def claim(conn, host_id: str) -> dict | None:
    """The host's oldest queued job, marked running; None when there is none.
    The write lock is taken only once a plain read found a job, so an idle
    long poll never holds it."""
    sql = "SELECT * FROM fleet_jobs WHERE host_id = ? AND state = 'queued' ORDER BY created_at, id LIMIT 1"
    if conn.execute(sql, (host_id,)).fetchone() is None:
        conn.commit()                                # end the read, so the next poll sees new commits
        return None
    db.begin_write(conn)
    row = conn.execute(sql, (host_id,)).fetchone()   # again under the lock: another poll may have taken it
    if not row:
        conn.commit()
        return None
    conn.execute("UPDATE fleet_jobs SET state = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                 (db.now(), row["id"]))
    conn.commit()
    return {"id": row["id"], "kind": row["kind"], "server_id": row["server_id"], "payload": json_dict(row["payload"])}


def complete(conn, host, job_id: str, state: str, result) -> dict:
    """An agent's result for a job it was handed. A create job's payload
    (it holds the container's client secret) is blanked here."""
    if state not in ("done", "failed"):
        raise Problem(400, "state must be done or failed")
    job = conn.execute("SELECT * FROM fleet_jobs WHERE id = ? AND host_id = ?", (job_id, host["id"])).fetchone()
    if not job:
        raise Problem(404, "no such job")
    if job["state"] != "running":
        raise Problem(409, f"the job is {job['state']}, not running")
    text = result if isinstance(result, str) else json.dumps(result if result is not None else {})
    payload = json_dict(job["payload"])
    conn.execute("UPDATE fleet_jobs SET state = ?, result = ?, finished_at = ?, payload = ? WHERE id = ?",
                 (state, text[:4000], db.now(), "{}" if job["kind"] == "create" else job["payload"], job_id))
    from . import hosted  # hosted imports this module
    hosted.job_finished(conn, dict(job), payload, state == "done", result if isinstance(result, dict) else {})
    release_waves(conn)
    return public_job(conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone())


def fail_stuck(conn) -> int:
    """Running jobs with no result after JOB_TIMEOUT (an agent that died
    mid-job) are failed, which also pauses their upgrade run."""
    rows = conn.execute("SELECT * FROM fleet_jobs WHERE state = 'running' AND started_at < ?",
                        (db.after(-JOB_TIMEOUT),)).fetchall()
    from . import hosted
    for job in rows:
        conn.execute("UPDATE fleet_jobs SET state = 'failed', result = ?, finished_at = ? WHERE id = ?",
                     (json.dumps({"error": "no result from the agent within an hour"}), db.now(), job["id"]))
        hosted.job_finished(conn, dict(job), json_dict(job["payload"]), False, {})
    return len(rows)


def retry(conn, job_id: str, actor: str = "") -> dict:
    """Queue a failed or canceled job again under the same id (so its wave
    can go on). A create job's payload was blanked, so it is rebuilt with a
    new client secret."""
    job = conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise Problem(404, "no such job")
    if job["state"] not in ("failed", "canceled"):
        raise Problem(409, f"only a failed or canceled job is retried; this one is {job['state']}")
    payload = job["payload"]
    if job["kind"] == "create":
        from . import hosted
        payload = json.dumps(hosted.create_payload(conn, job["server_id"]))
    conn.execute("UPDATE fleet_jobs SET state = 'queued', result = '', started_at = NULL, finished_at = NULL, "
                 "payload = ? WHERE id = ?", (payload, job_id))
    db.audit(conn, "fleet.job_retry", actor=actor, detail=f"{job_id} {job['kind']}")
    wake()
    return public_job(conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone())


def cancel(conn, job_id: str, actor: str = "") -> dict:
    job = conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone()
    if not job:
        raise Problem(404, "no such job")
    if job["state"] not in ("queued", "held", "failed"):
        raise Problem(409, f"a {job['state']} job cannot be canceled")
    conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, payload = ? WHERE id = ?",
                 (db.now(), "{}" if job["kind"] == "create" else job["payload"], job_id))
    db.audit(conn, "fleet.job_cancel", actor=actor, detail=f"{job_id} {job['kind']}")
    release_waves(conn)
    return public_job(conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone())


def public_job(row) -> dict:
    out = dict(row)
    payload = json_dict(row["payload"])
    payload.pop("env", None)                     # a create job's secrets never leave the queue
    out["payload"] = payload
    return out


def jobs(conn, state: str = "", limit: int = 100) -> list[dict]:
    limit = max(1, min(limit, 500))
    sql = ("SELECT j.*, s.label AS label, h.name AS host FROM fleet_jobs j LEFT JOIN hosted_servers s "
           "ON s.id = j.server_id LEFT JOIN hosts h ON h.id = j.host_id")
    args: tuple = ()
    if state:
        sql += " WHERE j.state = ?"
        args = (state,)
    rows = conn.execute(sql + " ORDER BY j.created_at DESC, j.id DESC LIMIT ?", (*args, limit)).fetchall()
    return [public_job(r) for r in rows]


# --- upgrade waves ------------------------------------------------------------

def upgrade(conn, tag: str, wave_size: int, server_ids: list[str] | None = None, actor: str = "") -> dict:
    """Upgrade servers to ``FLEET_IMAGE:tag`` in waves of ``wave_size``: the
    first wave is queued, the rest held; each wave is released only when
    every job before it is done (or canceled), so one failure pauses the
    run until an admin retries or cancels the failed job."""
    if not TAG_RE.match(tag or ""):
        raise Problem(400, "bad image tag")
    wave_size = max(1, min(int(wave_size or 1), 100))
    sql = ("SELECT * FROM hosted_servers WHERE host_id != '' AND state IN ('running', 'grace', 'read_only', "
           "'suspended')")
    rows = conn.execute(sql + " ORDER BY created_at").fetchall()
    if server_ids is not None:
        wanted = set(server_ids)
        rows = [r for r in rows if r["id"] in wanted]
    if not rows:
        raise Problem(400, "no running server to upgrade")
    busy = {r[0] for r in conn.execute("SELECT server_id FROM fleet_jobs WHERE kind = 'upgrade' "
                                       "AND state IN ('queued', 'held', 'running')").fetchall()}
    rows = [r for r in rows if r["id"] not in busy]
    if not rows:
        raise Problem(409, "every one of those servers already has an upgrade pending")
    run = "u" + db.new_token(6)
    image = f"{config.FLEET_IMAGE}:{tag}"
    for i, row in enumerate(rows):
        n = i // wave_size + 1
        enqueue(conn, row["host_id"], row["id"], "upgrade", {"label": row["label"], "image": image, "tag": tag},
                wave=f"{run}/{n:03d}", state="queued" if n == 1 else "held")
    waves = (len(rows) - 1) // wave_size + 1
    db.audit(conn, "fleet.upgrade", actor=actor, detail=f"{run} {image} servers={len(rows)} waves={waves}")
    return {"run": run, "image": image, "jobs": len(rows), "waves": waves}


def release_waves(conn) -> list[str]:
    """For every run with held jobs: queue its next wave once every job of
    the earlier waves is done or canceled. The waves released."""
    released = []
    runs = [r[0] for r in conn.execute("SELECT DISTINCT substr(wave, 1, instr(wave, '/') - 1) FROM fleet_jobs "
                                       "WHERE state = 'held' AND wave != ''").fetchall()]
    for run in runs:
        nxt = conn.execute("SELECT MIN(wave) FROM fleet_jobs WHERE state = 'held' AND wave LIKE ?",
                           (run + "/%",)).fetchone()[0]
        pending = conn.execute("SELECT COUNT(*) FROM fleet_jobs WHERE wave LIKE ? AND wave < ? "
                               "AND state NOT IN ('done', 'canceled')", (run + "/%", nxt)).fetchone()[0]
        if pending:
            continue
        conn.execute("UPDATE fleet_jobs SET state = 'queued' WHERE wave = ? AND state = 'held'", (nxt,))
        released.append(nxt)
        wake()
    return released

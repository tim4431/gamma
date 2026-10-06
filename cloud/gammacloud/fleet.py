"""The fleet: the hosts hosted servers run on, and the job queue their
agents work through (docs/dev/hosted.md). Every host runs one agent
(``cloud/fleet/``) with the Docker socket; it makes outbound calls only,
authenticates with its host token, long-polls for its next job, reports the
result and posts a heartbeat every five minutes. The account server never
touches Docker.

A job is ``queued`` (the agent may take it), ``held`` (a later wave of an
upgrade or update run), ``running`` (handed to the agent), ``done``,
``failed`` or ``canceled``. ``hosted.py`` decides which jobs a server's
lifecycle needs; this module stores, hands out and completes them, places
new servers on a host by the memory already committed there, keeps each
host's orphan containers, and runs upgrades and environment updates (one
server, or many in waves), by hand or, for outdated servers, by itself.
"""

import json
import math
import re
import threading

from . import config, db, settings
from .accounts import Problem
from .log import log

KINDS = ("create", "start", "stop", "restart", "delete", "upgrade", "rollback", "logs", "update")
SECRET_KINDS = ("create", "update")   # their payload holds secrets while it runs: blanked when the job ends
STALE_AFTER = 15 * 60          # a host silent this long takes no new placements
JOB_TIMEOUT = 3600             # a running job with no result after this is failed
MAX_WAIT = 30                  # the longest a job poll is held open
HOST_RESERVE_MB = 1024         # memory a host keeps for itself: never placed
RESULT_MAX = 4000              # a job's stored result, in characters
LOGS_RESULT_MAX = 100_000      # a logs job's (its oldest lines go first)
UPGRADABLE = ("running", "grace", "read_only", "suspended")
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


def json_dict(raw) -> dict:
    try:
        value = json.loads(raw or "{}")
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_list(raw) -> list:
    try:
        value = json.loads(raw or "[]")
    except ValueError:
        return []
    return value if isinstance(value, list) else []


def nonneg(value) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError):
        return 0


def _number(value) -> float | None:
    """A non-negative number from a report, to a tenth; None for anything else."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        return None
    return round(float(value), 1)


def default_tag() -> str:
    """The tag a new server runs and what *outdated* compares with: the Admin
    page's setting, else the environment's (``settings.fleet_image_tag``)."""
    return settings.fleet_image_tag()


def default_image() -> str:
    return f"{config.FLEET_IMAGE}:{default_tag()}"


def outdated_why(row) -> str:
    """Why a server is outdated: ``tag`` when it runs another tag than the
    default, ``image`` when its agent reports that the registry's image for
    the tag it runs is not the one it runs (a container started on
    ``latest`` months ago). "" when neither, or the server is deleted."""
    if row["state"] == "deleted":
        return ""
    if (row["image_tag"] or default_tag()) != default_tag():
        return "tag"
    agent = json_dict(row["report"]).get("agent")
    return "image" if isinstance(agent, dict) and agent.get("image_stale") is True else ""


def created(conn, server_id: str) -> bool:
    """Whether the server's container exists: its last create job is done."""
    job = conn.execute("SELECT state FROM fleet_jobs WHERE server_id = ? AND kind = 'create' "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (server_id,)).fetchone()
    return bool(job and job["state"] == "done")


def size_of(limits: dict) -> tuple[int, float]:
    """``(memory_mb, cpus)`` a server is sized to: its stored limits, else
    its plan's (Pro when the plan is unknown or has no container)."""
    plan = config.PLAN_LIMITS.get(limits.get("plan") or "", {})
    if not plan.get("hosted"):
        plan = config.PLAN_LIMITS["pro"]
    return nonneg(limits.get("memory_mb")) or plan["memory_mb"], float(limits.get("cpus") or plan["cpus"])


# --- hosts --------------------------------------------------------------------

def host_by_token(conn, token: str):
    if not token:
        return None
    return conn.execute("SELECT * FROM hosts WHERE token_hash = ?", (db.token_hash(token),)).fetchone()


def _check_name(conn, name: str, host_id: str = "") -> str:
    name = (name or "").strip()
    if not HOST_NAME_RE.match(name):
        raise Problem(400, "A host name is 1 to 63 letters, digits, dots, hyphens and underscores.")
    if conn.execute("SELECT 1 FROM hosts WHERE name = ? AND id != ?", (name, host_id)).fetchone():
        raise Problem(409, "A host with that name exists.")
    return name


def add_host(conn, name: str, address: str = "", actor: str = "") -> tuple[dict, str]:
    """A new host and its agent token (shown once; stored as its hash)."""
    name = _check_name(conn, name)
    host_id, token = "h_" + db.new_token(9), "gf_" + db.new_token(32)
    conn.execute("INSERT INTO hosts (id, name, address, token_hash, created_at) VALUES (?, ?, ?, ?, ?)",
                 (host_id, name, (address or "").strip()[:200], db.token_hash(token), db.now()))
    db.audit(conn, "fleet.host_add", actor=actor, detail=f"{host_id} {name}")
    return host_view(conn, host_id), token


def update_host(conn, host_id: str, *, name: str | None = None, accepting: bool | None = None,
                actor: str = "") -> dict:
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    if name is not None and name.strip() != row["name"]:
        name = _check_name(conn, name, host_id)
        conn.execute("UPDATE hosts SET name = ? WHERE id = ?", (name, host_id))
        db.audit(conn, "fleet.host_rename", actor=actor, detail=f"{host_id} {name}")
    if accepting is not None:
        conn.execute("UPDATE hosts SET accepting = ? WHERE id = ?", (1 if accepting else 0, host_id))
        db.audit(conn, "fleet.host_accepting", actor=actor, detail=f"{host_id} {'on' if accepting else 'off'}")
    return host_view(conn, host_id)


def is_stale(row) -> bool:
    return not row["last_seen_at"] or row["last_seen_at"] < db.after(-STALE_AFTER)


def _placed(conn) -> dict[str, dict]:
    """Per host: the count and the committed memory of its servers that are
    not deleted, and the labels of all its server rows (deleted ones too:
    their containers are not orphans)."""
    out: dict[str, dict] = {}
    for r in conn.execute("SELECT host_id, label, state, limits FROM hosted_servers WHERE host_id != ''").fetchall():
        use = out.setdefault(r["host_id"], {"servers": 0, "committed_mb": 0, "labels": set()})
        use["labels"].add(r["label"])
        if r["state"] != "deleted":
            use["servers"] += 1
            use["committed_mb"] += size_of(json_dict(r["limits"]))[0]
    return out


def _free_mb(row, committed_mb: int) -> int:
    return max(0, row["memory_mb"] - committed_mb - HOST_RESERVE_MB)


def public_host(row, placed: dict | None = None) -> dict:
    """A host as the admin sees it, with what is placed on it (``placed``
    from ``_placed``; none counts as an empty host)."""
    use = (placed or {}).get(row["id"]) or {"servers": 0, "committed_mb": 0, "labels": set()}
    out = {k: row[k] for k in row.keys() if k != "token_hash"}
    out["accepting"] = bool(row["accepting"])
    out["stale"] = is_stale(row)
    out["servers"] = use["servers"]
    out["committed_mb"] = use["committed_mb"]
    out["reserve_mb"] = HOST_RESERVE_MB
    out["free_mb"] = _free_mb(row, use["committed_mb"])
    # a server placed since the heartbeat is no orphan any more
    out["orphans"] = [label for label in _json_list(row["orphans"]) if label not in use["labels"]]
    return out


def host_view(conn, host_id: str) -> dict:
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    return public_host(row, _placed(conn))


def hosts(conn) -> list[dict]:
    placed = _placed(conn)
    return [public_host(r, placed) for r in conn.execute("SELECT * FROM hosts ORDER BY created_at").fetchall()]


def place(conn, plan: str):
    """The host a new server of ``plan`` goes on: an accepting host seen
    within STALE_AFTER, with more free disk than the plan's quota and at
    least the plan's memory free once what is committed to its servers and
    HOST_RESERVE_MB are counted; the most free memory first (the oldest
    host on a tie). None when no host has room."""
    need, quota = config.PLAN_LIMITS[plan]["memory_mb"], config.PLAN_LIMITS[plan]["quota_mb"]
    rows = conn.execute("SELECT * FROM hosts WHERE accepting = 1 AND last_seen_at >= ? AND disk_mb - disk_used_mb > ? "
                        "ORDER BY created_at", (db.after(-STALE_AFTER), quota)).fetchall()
    placed = _placed(conn)
    fits = [(free, r) for r in rows
            if (free := _free_mb(r, (placed.get(r["id"]) or {}).get("committed_mb", 0))) >= need]
    return max(fits, key=lambda f: f[0])[1] if fits else None


def heartbeat(conn, host, body: dict) -> None:
    """What an agent reports every five minutes: the host's capacity and
    use, and each container it runs. A server's container is merged into
    its ``report`` under ``agent``; a container no server row on this host
    names is kept as one of the host's ``orphans`` until it goes or a row
    names it."""
    ts = db.now()
    labels = {r[0] for r in conn.execute("SELECT label FROM hosted_servers WHERE host_id = ?", (host["id"],))}
    orphans = []
    for c in body.get("containers") or []:
        if not isinstance(c, dict) or not c.get("label"):
            continue
        label = str(c["label"])[:63]
        if label not in labels:
            if label not in orphans:
                orphans.append(label)
            continue
        row = conn.execute("SELECT id, report FROM hosted_servers WHERE label = ? AND host_id = ?",
                           (label, host["id"])).fetchone()
        report = json_dict(row["report"])
        report["agent"] = {"running": bool(c.get("running")), "health": str(c.get("health") or "")[:40],
                           "memory_mb": nonneg(c.get("memory_mb")), "memory_limit_mb": nonneg(c.get("memory_limit_mb")),
                           "data_mb": nonneg(c.get("data_mb")), "image": str(c.get("image") or "")[:200],
                           "last_seen_at": ts}
        conn.execute("UPDATE hosted_servers SET report = ?, reported_at = ? WHERE id = ?",
                     (json.dumps(report), ts, row["id"]))
    conn.execute("UPDATE hosts SET agent_version = ?, memory_mb = ?, disk_mb = ?, memory_used_mb = ?, "
                 "disk_used_mb = ?, orphans = ?, last_seen_at = ? WHERE id = ?",
                 (str(body.get("agent_version") or "")[:40], nonneg(body.get("memory_mb")), nonneg(body.get("disk_mb")),
                  nonneg(body.get("memory_used_mb")), nonneg(body.get("disk_used_mb")),
                  json.dumps(sorted(orphans)[:100]), ts, host["id"]))


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


def remove_orphan(conn, host_id: str, label: str, actor: str = "") -> dict:
    """A ``delete`` job for a container no server row names: its container
    and data directory go. The payload names no account, so the agent
    leaves the bucket alone (the prefix may belong to a live server). A
    removal already pending is returned rather than doubled."""
    found = host_view(conn, host_id)
    if conn.execute("SELECT 1 FROM hosted_servers WHERE host_id = ? AND label = ?", (host_id, label)).fetchone():
        raise Problem(409, f"a server on this host is labelled {label}")
    if label not in found["orphans"]:
        raise Problem(404, f"no orphan container {label} on this host")
    for r in conn.execute("SELECT id, payload FROM fleet_jobs WHERE host_id = ? AND server_id = '' AND kind = 'delete' "
                          "AND state IN ('queued', 'running')", (host_id,)).fetchall():
        if json_dict(r["payload"]).get("label") == label:
            return job(conn, r["id"])
    job_id = enqueue(conn, host_id, "", "delete", {"label": label, "account_id": ""})
    db.audit(conn, "fleet.orphan_remove", actor=actor, detail=f"{host_id} {label}")
    return job(conn, job_id)


def _drop_orphan(conn, host_id: str, label: str) -> None:
    row = conn.execute("SELECT orphans FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if row:
        conn.execute("UPDATE hosts SET orphans = ? WHERE id = ?",
                     (json.dumps([x for x in _json_list(row["orphans"]) if x != label]), host_id))


# --- jobs ---------------------------------------------------------------------

def enqueue(conn, host_id: str, server_id: str, kind: str, payload: dict, *, wave: str = "",
            state: str = "queued") -> str | None:
    """A job for a host's agent; its id, or None when there is no host (a
    server not placed yet: nothing to do there). ``server_id`` is empty for
    a job on no server's behalf (an orphan's removal)."""
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
    while True:
        row = conn.execute(sql, (host_id,)).fetchone()   # again under the lock: another poll may have taken it
        if not row:
            conn.commit()
            return None
        taken = _orphan_label_taken(conn, row)
        if not taken:
            break
        # an orphan's removal whose label a server took since it was queued
        # (or retried): handing it out would delete that server
        conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, result = ? WHERE id = ?",
                     (db.now(), json.dumps({"error": taken}), row["id"]))
    conn.execute("UPDATE fleet_jobs SET state = 'running', started_at = ?, attempts = attempts + 1 WHERE id = ?",
                 (db.now(), row["id"]))
    conn.commit()
    return {"id": row["id"], "kind": row["kind"], "server_id": row["server_id"], "payload": json_dict(row["payload"])}


def _orphan_label_taken(conn, job) -> str:
    """For an orphan's removal (a delete job with no server), why it must
    not run: a server on that host holds its label now. "" when it may."""
    if job["kind"] != "delete" or job["server_id"]:
        return ""
    label = json_dict(job["payload"]).get("label", "")
    held = conn.execute("SELECT 1 FROM hosted_servers WHERE host_id = ? AND label = ? AND state != 'deleted'",
                        (job["host_id"], label)).fetchone()
    return f"{label} belongs to a hosted server now; its removal as an orphan is canceled" if held else ""


def _result_text(kind: str, result) -> str:
    """A result as stored: JSON text, RESULT_MAX characters at most (a
    logs job's LOGS_RESULT_MAX, by dropping its oldest lines)."""
    text = result if isinstance(result, str) else json.dumps(result if result is not None else {})
    limit = LOGS_RESULT_MAX if kind == "logs" else RESULT_MAX
    if len(text) > limit and isinstance(result, dict) and isinstance(result.get("lines"), list):
        lines = list(result["lines"])
        # each line's encoded size once (with its ", "), then drop from the front
        sizes = [len(json.dumps(line)) + 2 for line in lines]
        total = len(json.dumps({**result, "lines": [], "truncated": True})) + sum(sizes)
        start = 0
        while start < len(lines) and total > limit:
            total -= sizes[start]
            start += 1
        text = json.dumps({**result, "lines": lines[start:], "truncated": True})
    return text[:limit]


def complete(conn, host, job_id: str, state: str, result) -> dict:
    """An agent's result for a job it was handed. A create job's payload
    (it holds the container's client secret) is blanked here. A result
    that arrives after ``fail_stuck`` gave up on the job still counts, as
    long as no admin retried or canceled it since."""
    if state not in ("done", "failed"):
        raise Problem(400, "state must be done or failed")
    job_row = conn.execute("SELECT * FROM fleet_jobs WHERE id = ? AND host_id = ?", (job_id, host["id"])).fetchone()
    if not job_row:
        raise Problem(404, "no such job")
    late = job_row["state"] == "failed" and json_dict(job_row["result"]).get("timed_out")
    if job_row["state"] != "running" and not late:
        raise Problem(409, f"the job is {job_row['state']}, not running")
    payload = json_dict(job_row["payload"])
    conn.execute("UPDATE fleet_jobs SET state = ?, result = ?, finished_at = ?, payload = ? WHERE id = ?",
                 (state, _result_text(job_row["kind"], result), db.now(),
                  "{}" if job_row["kind"] == "create" else job_row["payload"], job_id))
    if job_row["kind"] == "delete" and not job_row["server_id"] and state == "done":
        _drop_orphan(conn, host["id"], payload.get("label", ""))
    from . import hosted  # hosted imports this module
    hosted.job_finished(conn, dict(job_row), payload, state == "done", result if isinstance(result, dict) else {})
    release_waves(conn)
    return job(conn, job_id)


def fail_stuck(conn) -> int:
    """Running jobs with no result after JOB_TIMEOUT (an agent that died
    mid-job) are failed, which also pauses their upgrade run. A create
    job's payload is blanked as on any finish; a result that comes late is
    still taken (``complete``)."""
    rows = conn.execute("SELECT * FROM fleet_jobs WHERE state = 'running' AND started_at < ?",
                        (db.after(-JOB_TIMEOUT),)).fetchall()
    from . import hosted
    result = {"error": "no result from the agent within an hour", "timed_out": True}
    for row in rows:
        conn.execute("UPDATE fleet_jobs SET state = 'failed', result = ?, finished_at = ?, payload = ? WHERE id = ?",
                     (json.dumps(result), db.now(), "{}" if row["kind"] == "create" else row["payload"], row["id"]))
        hosted.job_finished(conn, dict(row), json_dict(row["payload"]), False, result)
    return len(rows)


def retry(conn, job_id: str, actor: str = "") -> dict:
    """Queue a failed or canceled job again under the same id (so its wave
    can go on). A create job's payload was blanked, so it is rebuilt with a
    new client secret. Refused for a deleted server (but its delete job)
    and for a create job a later one replaced."""
    row = conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise Problem(404, "no such job")
    if row["state"] not in ("failed", "canceled"):
        raise Problem(409, f"only a failed or canceled job is retried; this one is {row['state']}")
    if row["server_id"] and row["kind"] != "delete":
        server = conn.execute("SELECT state FROM hosted_servers WHERE id = ?", (row["server_id"],)).fetchone()
        if server is None or server["state"] == "deleted":
            raise Problem(409, "the server is deleted")
    taken = _orphan_label_taken(conn, row)
    if taken:
        raise Problem(409, taken)
    payload = row["payload"]
    if row["kind"] == "create":
        latest = conn.execute("SELECT id FROM fleet_jobs WHERE server_id = ? AND kind = 'create' "
                              "ORDER BY created_at DESC, rowid DESC LIMIT 1", (row["server_id"],)).fetchone()
        if latest["id"] != job_id:
            raise Problem(409, "a later create job replaced this one")
        from . import hosted
        payload = json.dumps(hosted.create_payload(conn, row["server_id"]))
    conn.execute("UPDATE fleet_jobs SET state = 'queued', result = '', started_at = NULL, finished_at = NULL, "
                 "payload = ? WHERE id = ?", (payload, job_id))
    db.audit(conn, "fleet.job_retry", actor=actor, detail=f"{job_id} {row['kind']}")
    wake()
    return job(conn, job_id)


def cancel(conn, job_id: str, actor: str = "") -> dict:
    row = conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        raise Problem(404, "no such job")
    if row["state"] not in ("queued", "held", "failed"):
        raise Problem(409, f"a {row['state']} job cannot be canceled")
    conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, payload = ? WHERE id = ?",
                 (db.now(), "{}" if row["kind"] == "create" else row["payload"], job_id))
    db.audit(conn, "fleet.job_cancel", actor=actor, detail=f"{job_id} {row['kind']}")
    release_waves(conn)
    return job(conn, job_id)


def _duration(row) -> float | None:
    """Seconds from start to finish, or to now while running."""
    if not row["started_at"]:
        return None
    end = row["finished_at"] or (db.now() if row["state"] == "running" else None)
    return round((db.parse(end) - db.parse(row["started_at"])).total_seconds(), 1) if end else None


def _waves(conn) -> dict[str, tuple[int, int]]:
    """Per upgrade run: its jobs, and how many of them are done."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT substr(wave, 1, instr(wave, '/') - 1), COUNT(*), SUM(state = 'done') FROM fleet_jobs "
        "WHERE wave != '' GROUP BY 1").fetchall()}


def public_job(row, waves: dict | None = None, full: bool = True) -> dict:
    """A job as the admin sees it: never a create payload's ``env`` (its
    secrets never leave the queue); in a list (``full`` False) a logs
    job's lines are only counted."""
    out = dict(row)
    payload = json_dict(row["payload"])
    payload.pop("env", None)
    out["payload"] = payload
    out["label"] = out.get("label") or str(payload.get("label") or "")   # an orphan's removal has no server
    out["duration_s"] = _duration(row)
    total, done = (waves or {}).get(row["wave"].partition("/")[0], (None, None)) if row["wave"] else (None, None)
    out["wave_total"], out["wave_done"] = total, done
    if not full and row["kind"] == "logs":
        result = json_dict(row["result"])
        if isinstance(result.get("lines"), list):
            out["result"] = json.dumps({**{k: v for k, v in result.items() if k != "lines"},
                                        "line_count": len(result["lines"])})
    return out


_JOBS_SQL = ("SELECT j.*, s.label AS label, h.name AS host FROM fleet_jobs j LEFT JOIN hosted_servers s "
             "ON s.id = j.server_id LEFT JOIN hosts h ON h.id = j.host_id")


def job(conn, job_id: str) -> dict:
    """One job, with its whole result."""
    row = conn.execute(_JOBS_SQL + " WHERE j.id = ?", (job_id,)).fetchone()
    if not row:
        raise Problem(404, "no such job")
    return public_job(row, _waves(conn))


def jobs(conn, state: str = "", limit: int = 100) -> list[dict]:
    limit = max(1, min(limit, 500))
    sql, args = _JOBS_SQL, ()
    if state:
        sql += " WHERE j.state = ?"
        args = (state,)
    rows = conn.execute(sql + " ORDER BY j.created_at DESC, j.id DESC LIMIT ?", (*args, limit)).fetchall()
    waves = _waves(conn)
    return [public_job(r, waves, full=False) for r in rows]


# --- upgrades -----------------------------------------------------------------

def _upgrade_pending(conn) -> set[str]:
    """Servers with an image upgrade queued, held or running. A resize (an
    upgrade job naming no image) does not count: it keeps whatever image
    the container runs, so either order ends the same."""
    rows = conn.execute("SELECT server_id, payload FROM fleet_jobs WHERE kind = 'upgrade' "
                        "AND state IN ('queued', 'held', 'running')").fetchall()
    return {r["server_id"] for r in rows if json_dict(r["payload"]).get("image")}


def _upgrade_payload(row, tag: str) -> dict:
    return {"label": row["label"], "image": f"{config.FLEET_IMAGE}:{tag}", "tag": tag}


def upgrade(conn, tag: str, wave_size: int, server_ids: list[str] | None = None, actor: str = "") -> dict:
    """Upgrade servers to ``FLEET_IMAGE:tag`` in waves of ``wave_size``: the
    first wave is queued, the rest held; each wave is released only when
    every job before it is done (or canceled), so one failure pauses the
    run until an admin retries or cancels the failed job."""
    if not TAG_RE.match(tag or ""):
        raise Problem(400, "bad image tag")
    wave_size = max(1, min(int(wave_size or 1), 100))
    rows = conn.execute("SELECT * FROM hosted_servers WHERE host_id != '' AND state IN (%s) ORDER BY created_at"
                        % ",".join("?" * len(UPGRADABLE)), UPGRADABLE).fetchall()
    if server_ids is not None:
        wanted = set(server_ids)
        rows = [r for r in rows if r["id"] in wanted]
    if not rows:
        raise Problem(400, "no running server to upgrade")
    busy = _upgrade_pending(conn)
    rows = [r for r in rows if r["id"] not in busy]
    if not rows:
        raise Problem(409, "every one of those servers already has an upgrade pending")
    run = "u" + db.new_token(6)
    image = f"{config.FLEET_IMAGE}:{tag}"
    for i, row in enumerate(rows):
        n = i // wave_size + 1
        enqueue(conn, row["host_id"], row["id"], "upgrade", _upgrade_payload(row, tag),
                wave=f"{run}/{n:03d}", state="queued" if n == 1 else "held")
    waves = (len(rows) - 1) // wave_size + 1
    db.audit(conn, "fleet.upgrade", actor=actor, detail=f"{run} {image} servers={len(rows)} waves={waves}")
    return {"run": run, "image": image, "jobs": len(rows), "waves": waves}


def upgrade_one(conn, server_id: str, tag: str, actor: str = "") -> dict:
    """One server to ``FLEET_IMAGE:tag``, outside any wave. Its
    ``image_tag`` moves when the job is done."""
    if not TAG_RE.match(tag or ""):
        raise Problem(400, "bad image tag")
    row = conn.execute("SELECT * FROM hosted_servers WHERE id = ?", (server_id,)).fetchone()
    if row is None:
        raise Problem(404, "no such server")
    if not row["host_id"] or row["state"] not in UPGRADABLE:
        raise Problem(409, f"only a running server is upgraded; this one is {row['state']}")
    if server_id in _upgrade_pending(conn):
        raise Problem(409, "this server already has an upgrade pending")
    payload = _upgrade_payload(row, tag)
    job_id = enqueue(conn, row["host_id"], row["id"], "upgrade", payload)
    db.audit(conn, "fleet.upgrade", row["account_id"], actor, f"{row['label']} {payload['image']}")
    return job(conn, job_id)


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

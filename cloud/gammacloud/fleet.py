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

It also keeps every container each agent reports, the host's own services
(the account server, the share host, Caddy, the demo, the agent itself)
as well as the hosted servers, tells Gamma's from whatever else the
machine runs (``is_gamma``), and queues the jobs the Machines tab sends
one of them by its name (``container_job``).
"""

import ipaddress
import json
import math
import re
import shlex
import threading

from . import config, db, dns, metrics, settings
from .accounts import Problem
from .log import log

# a job for any container of a host, by its name (``container_job``); the server's own kinds come first
CONTAINER_KINDS = ("container_restart", "container_start", "container_stop", "container_logs", "container_update",
                   "container_rollback")
KINDS = ("create", "start", "stop", "restart", "delete", "upgrade", "rollback", "logs", "update") + CONTAINER_KINDS
SECRET_KINDS = ("create", "update")   # their payload holds secrets while it runs: blanked when the job ends
LOG_KINDS = ("logs", "container_logs")   # their result holds log lines: LOGS_RESULT_MAX
STALE_AFTER = 15 * 60          # a host silent this long takes no new placements
JOB_TIMEOUT = 3600             # a running job with no result after this is failed
MAX_WAIT = 30                  # the longest a job poll is held open
HOST_RESERVE_MB = 1024         # memory a host keeps for itself: never placed
RESULT_MAX = 4000              # a job's stored result, in characters
LOGS_RESULT_MAX = 100_000      # a logs job's (its oldest lines go first)
LOG_LINES_MAX = 5000           # the most lines a container_logs job asks for
CONTAINERS_MAX = 200           # the containers of a host kept from one heartbeat
GAMMA_REPO = "ghcr.io/tim4431/gamma"   # Gamma's images are under it: the server's, gamma-cloud and gamma-fleet
UPGRADABLE = ("running", "grace", "read_only", "suspended")
TAG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
HOST_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,62}$")
CONTAINER_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")   # Docker's own rule for a name
STATUSES = ("running", "restarting", "paused", "created", "exited", "dead", "removing")
HEALTHS = ("healthy", "unhealthy", "starting")
# The agent's installer (cloud/fleet/deploy/bootstrap.sh) and what it assumes when not told otherwise.
BOOTSTRAP_URL = "https://raw.githubusercontent.com/tim4431/Gamma/main/cloud/fleet/deploy/bootstrap.sh"
BOOTSTRAP_ACCOUNT_URL = "https://account.gammapdf.com"
BOOTSTRAP_DOMAIN = "gammapdf.com"

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


def _text(value, n: int) -> str:
    """Text from a report, cut to ``n`` characters; "" for anything else."""
    return value[:n] if isinstance(value, str) else ""


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


def _check_ip(value: str) -> str:
    """A host's public address as stored: "" for none, else an IPv4 or IPv6
    address in its usual form."""
    value = (value or "").strip()
    if not value:
        return ""
    try:
        return str(ipaddress.ip_address(value))
    except ValueError:
        raise Problem(400, "A public IP is an IPv4 or IPv6 address, or empty.") from None


def add_host(conn, name: str, address: str = "", actor: str = "", public_ip: str = "") -> tuple[dict, str]:
    """A new host and its agent token (shown once; stored as its hash).
    ``public_ip`` makes it a routed host (``dns.py``): it runs a Caddy of
    its own, and each server on it gets a DNS record."""
    name, public_ip = _check_name(conn, name), _check_ip(public_ip)
    host_id, token = "h_" + db.new_token(9), "gf_" + db.new_token(32)
    conn.execute("INSERT INTO hosts (id, name, address, public_ip, token_hash, created_at) VALUES (?, ?, ?, ?, ?, ?)",
                 (host_id, name, (address or "").strip()[:200], public_ip, db.token_hash(token), db.now()))
    db.audit(conn, "fleet.host_add", actor=actor, detail=f"{host_id} {name}" + (f" {public_ip}" if public_ip else ""))
    return host_view(conn, host_id), token


def bootstrap_command(token: str, edge: bool) -> str:
    """The line that installs the agent on a fresh host with ``token``
    (``cloud/fleet/deploy/bootstrap.sh``); ``edge`` adds the host's own
    Caddy. The account server's address and the domain are named only when
    they are not the script's defaults."""
    args = ["--token", token]
    if config.PUBLIC_URL != BOOTSTRAP_ACCOUNT_URL:
        args += ["--account-url", config.PUBLIC_URL]
    if edge:
        args.append("--edge")
        if config.HOSTED_DOMAIN and config.HOSTED_DOMAIN != BOOTSTRAP_DOMAIN:
            args += ["--domain", config.HOSTED_DOMAIN]
    return f"curl -fsSL {BOOTSTRAP_URL} | bash -s -- " + " ".join(shlex.quote(a) for a in args)


def update_host(conn, host_id: str, *, name: str | None = None, accepting: bool | None = None,
                public_ip: str | None = None, actor: str = "") -> dict:
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
    if public_ip is not None and _check_ip(public_ip) != row["public_ip"]:
        public_ip = _check_ip(public_ip)
        conn.execute("UPDATE hosts SET public_ip = ? WHERE id = ?", (public_ip, host_id))
        db.audit(conn, "fleet.host_ip", actor=actor, detail=f"{host_id} {public_ip or 'none'}")
        dns.kick()          # its servers' records are made, moved or removed once this commits
    return host_view(conn, host_id)


def rotate_token(conn, host_id: str, actor: str = "") -> tuple[dict, str]:
    """A new agent token for the host (shown once; its hash replaces the
    old one's). The old token stops working at once: the agent is refused
    until its ``.env`` holds the new one."""
    row = conn.execute("SELECT id, name FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    token = "gf_" + db.new_token(32)
    conn.execute("UPDATE hosts SET token_hash = ? WHERE id = ?", (db.token_hash(token), host_id))
    db.audit(conn, "fleet.host_token", actor=actor, detail=f"{host_id} {row['name']}")
    return host_view(conn, host_id), token


def remove_host(conn, host_id: str, actor: str = "") -> None:
    """Forget a host no server is on (a deleted server's row may still name
    it); 409 while one is. Its jobs that have not finished are canceled, and
    its failed ones too: no agent reports on them any more, and their alerts
    would stay open. Nothing on the machine is touched; its token stops
    working with the row."""
    row = conn.execute("SELECT id, name FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    n = conn.execute("SELECT COUNT(*) FROM hosted_servers WHERE host_id = ? AND state != 'deleted'",
                     (host_id,)).fetchone()[0]
    if n:
        raise Problem(409, f"{row['name']} has {n} server{'s' if n != 1 else ''} on it: delete or move them first")
    conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, payload = CASE WHEN kind IN (%s) "
                 "THEN '{}' ELSE payload END WHERE host_id = ? AND state IN ('queued', 'held', 'running', 'failed')"
                 % ",".join("?" * len(SECRET_KINDS)), (db.now(), *SECRET_KINDS, host_id))
    conn.execute("DELETE FROM hosts WHERE id = ?", (host_id,))
    db.audit(conn, "fleet.host_remove", actor=actor, detail=f"{host_id} {row['name']}")
    release_waves(conn)


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
    out["containers"] = _host_containers(row["containers"])
    # a routed host: "on", or "off" while DNS is, which keeps it out of placement
    out["dns"] = ("on" if dns.enabled() else "off") if row["public_ip"] else ""
    return out


def host_view(conn, host_id: str) -> dict:
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    return public_host(row, _placed(conn))


def hosts(conn) -> list[dict]:
    placed = _placed(conn)
    return [public_host(r, placed) for r in conn.execute("SELECT * FROM hosts ORDER BY created_at").fetchall()]


def place(conn, need: int, quota: int):
    """The host a new server goes on: an accepting host seen within
    STALE_AFTER, with more free disk than its ``quota`` (MB) and at least
    ``need`` MB of memory free once what is committed to its servers and
    HOST_RESERVE_MB are counted; the most free memory first (the oldest
    host on a tie). None when no host has room. ``hosted`` passes the
    server's numbers: its plan's, or its own overrides. A routed host (one
    with a ``public_ip``) qualifies only while DNS is on: a server there is
    reached through a record of its own (``dns.py``)."""
    rows = conn.execute("SELECT * FROM hosts WHERE accepting = 1 AND last_seen_at >= ? AND disk_mb - disk_used_mb > ? "
                        "AND (public_ip = '' OR ?) ORDER BY created_at",
                        (db.after(-STALE_AFTER), quota, 1 if dns.enabled() else 0)).fetchall()
    placed = _placed(conn)
    fits = [(free, r) for r in rows
            if (free := _free_mb(r, (placed.get(r["id"]) or {}).get("committed_mb", 0))) >= need]
    return max(fits, key=lambda f: f[0])[1] if fits else None


def _image_repo(image: str) -> str:
    """An image reference's repository: less its digest, and less its tag
    (a ``:`` after the last ``/``; one before it is a registry's port)."""
    repo = image.partition("@")[0]
    head, colon, tail = repo.rpartition(":")
    return head if colon and "/" not in tail else repo


def is_gamma(c: dict) -> bool:
    """Whether a container of a host's list is Gamma's, rather than
    something else the machine runs: its name starts with ``gamma-``, its
    Compose project with ``gamma``, its image's repository is FLEET_IMAGE
    or under GAMMA_REPO, or it is a hosted server's (``managed``) or the
    agent's own (``self``). The Machines tab shows the others only when
    asked; *Update all* and the ``container_down`` alert leave them out."""
    compose = c["compose"] if isinstance(c.get("compose"), dict) else {}
    repo = _image_repo(str(c.get("image") or ""))
    return (str(c.get("name") or "").startswith("gamma-") or str(compose.get("project") or "").startswith("gamma")
            or repo == config.FLEET_IMAGE or repo.startswith(GAMMA_REPO)
            or c.get("managed") is True or c.get("self") is True)


def _host_containers(raw) -> list[dict]:
    """A host's stored ``containers``, each with ``gamma``: an entry kept
    before the heartbeat stored it is classified as it is read."""
    out = [c for c in _json_list(raw) if isinstance(c, dict)]
    for c in out:
        if not isinstance(c.get("gamma"), bool):
            c["gamma"] = is_gamma(c)
    return out


def _docker_entry(c) -> dict | None:
    """One container of a heartbeat's ``docker`` list as it is kept: every
    field checked, text cut to length, numbers from 0, a flag true only
    when it is ``true``, and what an agent does not send (or sends as
    nonsense) unknown; then whether it is Gamma's (``gamma``, ``is_gamma``).
    None for an entry with no usable name."""
    if not isinstance(c, dict):
        return None
    name = _text(c.get("name"), 130).lstrip("/")
    if not CONTAINER_NAME_RE.match(name):
        return None
    compose, ports = c.get("compose"), c.get("ports")
    compose = ({"project": _text(compose.get("project"), 64), "service": _text(compose.get("service"), 64)}
               if isinstance(compose, dict) else None)
    entry = {"id": _text(c.get("id"), 64), "name": name, "image": _text(c.get("image"), 200),
             "image_id": _text(c.get("image_id"), 80),
             "status": c["status"] if c.get("status") in STATUSES else "",
             "health": c["health"] if c.get("health") in HEALTHS else "",
             "created_at": _text(c.get("created_at"), 40), "started_at": _text(c.get("started_at"), 40),
             "restarts": nonneg(c.get("restarts")), "restart_policy": _text(c.get("restart_policy"), 20),
             "memory_mb": nonneg(c.get("memory_mb")), "memory_limit_mb": nonneg(c.get("memory_limit_mb")),
             "cpu_pct": _number(c.get("cpu_pct")),
             "image_stale": c["image_stale"] if isinstance(c.get("image_stale"), bool) else None,
             "managed": c.get("managed") is True, "self": c.get("self") is True,
             "compose": compose if compose and (compose["project"] or compose["service"]) else None,
             "ports": [p[:80] for p in ports[:20] if isinstance(p, str)] if isinstance(ports, list) else []}
    entry["gamma"] = is_gamma(entry)
    return entry


def heartbeat(conn, host, body: dict) -> None:
    """What an agent reports every five minutes: the host's capacity and
    use, and each container it runs. A server's container is merged into
    its ``report`` under ``agent`` (what Docker says of it, and whether the
    registry has another image for its tag: ``image_stale``, None when the
    agent could not tell); a container no server row on this host names is
    kept as one of the host's ``orphans`` until it goes or a row names it.
    The ``docker`` list, every container on the host whatever runs it,
    replaces the host's ``containers`` (``_docker_entry``; CONTAINERS_MAX at
    most, an older agent's none). The host's use (its ``cpu_pct`` too, the
    host's CPU since the agent's last heartbeat), each server's container
    and each container are also kept as the hour's sample
    (``metrics.record``)."""
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
                           "cpu_pct": _number(c.get("cpu_pct")), "restarts": nonneg(c.get("restarts")),
                           "started_at": str(c.get("started_at") or "")[:40], "oom_killed": c.get("oom_killed") is True,
                           "image_stale": c["image_stale"] if isinstance(c.get("image_stale"), bool) else None,
                           "last_seen_at": ts}
        conn.execute("UPDATE hosted_servers SET report = ?, reported_at = ? WHERE id = ?",
                     (json.dumps(report), ts, row["id"]))
        metrics.record(conn, "server", row["id"], {k: report["agent"][k] for k in ("memory_mb", "cpu_pct", "data_mb",
                                                                                   "restarts")})
    docker, names = [], set()
    for c in body.get("docker") if isinstance(body.get("docker"), list) else []:
        entry = _docker_entry(c)
        if entry is None or entry["name"] in names:
            continue
        names.add(entry["name"])
        docker.append(entry)
        metrics.record(conn, "container", f"{host['id']}:{entry['name']}",
                       {"memory_mb": entry["memory_mb"], "cpu_pct": entry["cpu_pct"]})
        if len(docker) == CONTAINERS_MAX:
            break
    conn.execute("UPDATE hosts SET agent_version = ?, memory_mb = ?, disk_mb = ?, memory_used_mb = ?, "
                 "disk_used_mb = ?, orphans = ?, containers = ?, last_seen_at = ? WHERE id = ?",
                 (str(body.get("agent_version") or "")[:40], nonneg(body.get("memory_mb")), nonneg(body.get("disk_mb")),
                  nonneg(body.get("memory_used_mb")), nonneg(body.get("disk_used_mb")),
                  json.dumps(sorted(orphans)[:100]), json.dumps(docker), ts, host["id"]))
    use = _placed(conn).get(host["id"]) or {"servers": 0, "committed_mb": 0}
    metrics.record(conn, "host", host["id"], {"memory_used_mb": nonneg(body.get("memory_used_mb")),
                                              "disk_used_mb": nonneg(body.get("disk_used_mb")),
                                              "cpu_pct": _number(body.get("cpu_pct")),
                                              "committed_mb": use["committed_mb"], "servers": use["servers"]})


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


# --- every container of a host -------------------------------------------------
# The host's last ``docker`` list (``hosts.containers``): the hosted servers'
# containers and the host's own services. A job for one of them names it by
# its name and has no server; the agent answers it like a server's job.

def is_kept(name: str, names) -> bool:
    """Whether ``name`` is the ``<name>-prev`` a failed update or upgrade kept
    stopped beside the container it was replacing."""
    return name.endswith("-prev") and name[:-5] in names


def updatable(containers: list[dict]) -> list[dict]:
    """The containers *Update all* takes, of a list ``_host_containers``
    read: Gamma's (``gamma``), not a hosted server's (an upgrade run moves
    those), not one a failed update kept, and with a newer image of their
    tag in the registry. The agent's own comes last: its helper replaces
    the agent that would run the rest."""
    names = {c.get("name") for c in containers}
    todo = [c for c in containers if c.get("gamma") and not c.get("managed") and c.get("image_stale") is True
            and not is_kept(c.get("name", ""), names)]
    return sorted(todo, key=lambda c: bool(c.get("self")))


def _host_container(conn, host_id: str, name: str):
    """The host's row and its container ``name`` in the last report; 404
    for either missing."""
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    entry = next((c for c in _json_list(row["containers"]) if isinstance(c, dict) and c.get("name") == name), None)
    if entry is None:
        raise Problem(404, f"no container {name} on {row['name']}")
    return row, entry


def container_job(conn, host_id: str, name: str, kind: str, actor: str = "", lines=None, *, wave: str = "",
                  state: str = "queued") -> dict:
    """A job for one container of the host, by its name in the agent's last
    report: ``container_restart``, ``_start``, ``_stop``, ``_logs`` (its last
    ``lines``, at most LOG_LINES_MAX), ``_update`` (the newest image of its
    tag, the same configuration) or ``_rollback`` (back to the ``-prev`` a
    failed update kept). A hosted server's container is only read here: the
    server's own actions change it and keep its row in step. The agent never
    stops itself, since it would take no more jobs, and the other half of
    its own ``-prev`` pair is not started while it runs: that would be a
    second agent. The same job already waiting or running for the container
    is answered rather than doubled; a logs job, which may ask for another
    number of lines, is always new."""
    if kind not in CONTAINER_KINDS:
        raise Problem(400, "kind must be " + ", ".join(CONTAINER_KINDS))
    host, entry = _host_container(conn, host_id, name)
    if entry.get("managed") and kind != "container_logs":
        raise Problem(409, f"{name} is a hosted server's container: use that server's own actions on the Servers "
                           "tab (an orphan's is Remove)")
    if entry.get("self") and kind == "container_stop":
        raise Problem(409, f"{name} is the agent itself: stopped, it would take no more jobs")
    own = next((c for c in _json_list(host["containers"]) if isinstance(c, dict) and c.get("self")), None)
    mine = own.get("name", "") if own else ""
    if kind == "container_start" and own and own.get("status") == "running" and name in (
            {mine + "-prev", mine.removesuffix("-prev")} - {mine}):
        raise Problem(409, f"{name} is the agent's other container: started beside {mine}, which runs, it would be "
                           "a second agent")
    payload = {"container": name}
    if kind == "container_logs":
        if lines is not None:
            if isinstance(lines, bool) or not isinstance(lines, int) or not 1 <= lines <= LOG_LINES_MAX:
                raise Problem(400, f"lines is a whole number from 1 to {LOG_LINES_MAX}")
            payload["lines"] = lines
    else:
        for r in conn.execute("SELECT id, payload FROM fleet_jobs WHERE host_id = ? AND server_id = '' AND kind = ? "
                              "AND state IN ('queued', 'held', 'running')", (host_id, kind)).fetchall():
            if json_dict(r["payload"]).get("container") == name:
                return job(conn, r["id"])
    job_id = enqueue(conn, host_id, "", kind, payload, wave=wave, state=state)
    db.audit(conn, "fleet.container", actor=actor, detail=f"{host_id} {name} {kind}")
    return job(conn, job_id)


def update_all(conn, host_id: str, actor: str = "") -> list[dict]:
    """A ``container_update`` for each container of the host ``updatable``
    takes (Gamma's only), as one run: the agent's own in a second wave,
    held until every job of the first is done (or canceled), since its
    helper replaces the agent that runs them. A failure in the first wave
    so holds the agent's update until the job is retried or canceled, as in
    any run."""
    row = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    if not row:
        raise Problem(404, "no such host")
    todo = updatable(_host_containers(row["containers"]))
    if not todo:
        raise Problem(409, f"none of Gamma's containers on {row['name']} has a newer image")
    run, others = "c" + db.new_token(6), any(not c.get("self") for c in todo)
    out = []
    for c in todo:
        later = bool(c.get("self")) and others
        out.append(container_job(conn, host_id, c["name"], "container_update", actor,
                                 wave=f"{run}/{2 if later else 1:03d}", state="held" if later else "queued"))
    return out


def _container_image_known(conn, host_id: str, name: str, stale) -> None:
    """Set what the host's last report says of a container's image
    (``image_stale``) until the agent's next heartbeat says it again."""
    row = conn.execute("SELECT containers FROM hosts WHERE id = ?", (host_id,)).fetchone()
    entries = _json_list(row["containers"]) if row else []
    for c in entries:
        if isinstance(c, dict) and c.get("name") == name:
            c["image_stale"] = stale
            conn.execute("UPDATE hosts SET containers = ? WHERE id = ?", (json.dumps(entries), host_id))
            return


def machines(conn) -> list[dict]:
    """Every host as the Machines tab shows it: ``public_host``, whose
    containers each say whether they are Gamma's (``gamma``) and a
    ``-prev`` a failed update kept (``kept``) and, for a hosted server's
    (``managed``), its ``label``, the ``server`` it runs (``{id, label,
    state, username}``, or None) and whether it is an ``orphan``; then
    ``others`` (how many are not Gamma's), ``cpu_pct`` (the host's CPU use
    in its latest sample, ``metrics.latest``), ``agent_stale`` (the agent's
    own container's ``image_stale``), ``updates`` (how many *Update all*
    takes) and the host's last 20 jobs. The agent's own container has
    ``helper``: the kind of its update, restart or rollback that is done
    but not yet followed by a heartbeat ("" when none). Such a job only
    starts a helper container, which replaces the agent; the new one says
    it is there with its first heartbeat."""
    servers = {(r["host_id"], r["label"]): {"id": r["id"], "label": r["label"], "state": r["state"],
                                            "username": r["username"] or ""}
               for r in conn.execute("SELECT s.id, s.host_id, s.label, s.state, a.username FROM hosted_servers s "
                                     "LEFT JOIN accounts a ON a.id = s.account_id WHERE s.host_id != ''").fetchall()}
    waves, latest, out = _waves(conn), metrics.latest(conn, "host"), []
    for h in hosts(conn):
        h["others"] = sum(1 for c in h["containers"] if not c["gamma"])
        h["cpu_pct"] = (latest.get(h["id"]) or {}).get("cpu_pct")
        names = {c.get("name") for c in h["containers"]}
        for c in h["containers"]:
            name = c.get("name", "")
            c["kept"] = is_kept(name, names)
            if c.get("managed"):
                label = name[len("gamma-"):] if name.startswith("gamma-") else name
                c["label"] = label[:-len("-prev")] if c["kept"] else label
                c["server"] = servers.get((h["id"], c["label"]))
                c["orphan"] = not c["kept"] and c["label"] in h["orphans"]
        own = next((c for c in h["containers"] if c.get("self")), None)
        h["agent_stale"] = own.get("image_stale") if own else None
        if own:
            done = conn.execute(
                "SELECT kind, payload FROM fleet_jobs WHERE host_id = ? AND server_id = '' AND state = 'done' "
                "AND kind IN ('container_update', 'container_restart', 'container_rollback') AND finished_at > ? "
                "ORDER BY finished_at DESC", (h["id"], h["last_seen_at"] or "")).fetchall()
            own["helper"] = next((r["kind"] for r in done
                                  if json_dict(r["payload"]).get("container") == own.get("name")), "")
        h["updates"] = len(updatable(h["containers"]))
        h["jobs"] = [public_job(r, waves, full=False) for r in conn.execute(
            _JOBS_SQL + " WHERE j.host_id = ? ORDER BY j.created_at DESC, j.id DESC LIMIT 20", (h["id"],)).fetchall()]
        out.append(h)
    return out


# --- jobs ---------------------------------------------------------------------

def enqueue(conn, host_id: str, server_id: str, kind: str, payload: dict, *, wave: str = "",
            state: str = "queued") -> str | None:
    """A job for a host's agent; its id, or None when there is no host (a
    server not placed yet: nothing to do there). ``server_id`` is empty for
    a job on no server's behalf (an orphan's removal, a container's job)."""
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
    """The host's oldest queued job, marked running, with the payload the
    agent gets (``_handed``); None when there is none. The write lock is
    taken only once a plain read found a job, so an idle long poll never
    holds it."""
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
        payload, refused = _handed(conn, row)
        if not refused:
            break
        conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, result = ?, payload = ? WHERE id = ?",
                     (db.now(), json.dumps({"error": refused}),
                      "{}" if row["kind"] in SECRET_KINDS else row["payload"], row["id"]))
    conn.execute("UPDATE fleet_jobs SET state = 'running', started_at = ?, attempts = attempts + 1, payload = ? "
                 "WHERE id = ?", (db.now(), json.dumps(payload), row["id"]))
    conn.commit()
    return {"id": row["id"], "kind": row["kind"], "server_id": row["server_id"], "payload": payload}


def _handed(conn, job) -> tuple[dict, str]:
    """The payload the agent gets for a job, and why the job must not run
    ("" when it may). A create or update job's ``extra_env`` (the fleet's
    variables with the server's own over them) is read now rather than when
    it was queued, so a job that waited, in a later wave say, or was
    retried after its payload was blanked, applies the newest; it stays in
    the payload only while the job runs. An orphan's removal whose label a
    server took since it was queued (or retried) must not run: it would
    delete that server."""
    payload = json_dict(job["payload"])
    if job["kind"] in SECRET_KINDS and job["server_id"]:
        server = conn.execute("SELECT * FROM hosted_servers WHERE id = ?", (job["server_id"],)).fetchone()
        if server is None or server["state"] == "deleted":
            return payload, "the server is deleted"
        from . import hosted  # hosted imports this module
        payload = {**payload, "label": server["label"], "extra_env": hosted.extra_env(conn, server)}
    return payload, _orphan_label_taken(conn, job)


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
    limit = LOGS_RESULT_MAX if kind in LOG_KINDS else RESULT_MAX
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
    """An agent's result for a job it was handed. A create or update job's
    payload (it holds the container's client secret, or its environment)
    is blanked here. A result that arrives after ``fail_stuck`` gave up on
    the job still counts, as long as no admin retried or canceled it
    since. A done ``container_update`` pulled the newest image of its tag,
    so the container is no longer stale by the last report; after a
    ``container_rollback`` that is unknown, until the next heartbeat."""
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
                  "{}" if job_row["kind"] in SECRET_KINDS else job_row["payload"], job_id))
    if job_row["kind"] == "delete" and not job_row["server_id"] and state == "done":
        _drop_orphan(conn, host["id"], payload.get("label", ""))
    if job_row["kind"] in ("container_update", "container_rollback") and state == "done":
        _container_image_known(conn, host["id"], payload.get("container", ""),
                               False if job_row["kind"] == "container_update" else None)
    from . import hosted  # hosted imports this module
    hosted.job_finished(conn, dict(job_row), payload, state == "done", result if isinstance(result, dict) else {})
    release_waves(conn)
    return job(conn, job_id)


def fail_stuck(conn) -> int:
    """Running jobs with no result after JOB_TIMEOUT (an agent that died
    mid-job) are failed, which also pauses their run. A create or update
    job's payload is blanked as on any finish; a result that comes late is
    still taken (``complete``)."""
    rows = conn.execute("SELECT * FROM fleet_jobs WHERE state = 'running' AND started_at < ?",
                        (db.after(-JOB_TIMEOUT),)).fetchall()
    from . import hosted
    result = {"error": "no result from the agent within an hour", "timed_out": True}
    for row in rows:
        conn.execute("UPDATE fleet_jobs SET state = 'failed', result = ?, finished_at = ?, payload = ? WHERE id = ?",
                     (json.dumps(result), db.now(), "{}" if row["kind"] in SECRET_KINDS else row["payload"], row["id"]))
        hosted.job_finished(conn, dict(row), json_dict(row["payload"]), False, result)
    return len(rows)


def retry(conn, job_id: str, actor: str = "") -> dict:
    """Queue a failed or canceled job again under the same id (so its wave
    can go on). A create job's payload was blanked, so it is rebuilt with a
    new client secret; an update job's is built again when the agent takes
    it (``_handed``). Refused for a deleted server (but its delete job) and
    for a create job a later one replaced."""
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
                 (db.now(), "{}" if row["kind"] in SECRET_KINDS else row["payload"], job_id))
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
    """Per upgrade or update run: its jobs, and how many of them are done."""
    return {r[0]: (r[1], r[2]) for r in conn.execute(
        "SELECT substr(wave, 1, instr(wave, '/') - 1), COUNT(*), SUM(state = 'done') FROM fleet_jobs "
        "WHERE wave != '' GROUP BY 1").fetchall()}


def public_job(row, waves: dict | None = None, full: bool = True) -> dict:
    """A job as the admin sees it: never a create payload's ``env`` and of
    an ``extra_env`` only the names (their secrets never leave the queue);
    in a list (``full`` False) a logs job's lines are only counted."""
    out = dict(row)
    payload = json_dict(row["payload"])
    payload.pop("env", None)
    if isinstance(payload.get("extra_env"), dict):
        payload["extra_env"] = sorted(payload["extra_env"])
    out["payload"] = payload
    # an orphan's removal and a container's job have no server: the label or the container they name
    out["label"] = out.get("label") or str(payload.get("label") or payload.get("container") or "")
    out["duration_s"] = _duration(row)
    total, done = (waves or {}).get(row["wave"].partition("/")[0], (None, None)) if row["wave"] else (None, None)
    out["wave_total"], out["wave_done"] = total, done
    if not full and row["kind"] in LOG_KINDS:
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


# --- upgrades and environment updates -------------------------------------------
# Both recreate containers, one wave of servers at a time: an ``upgrade``
# run moves them to an image tag, an ``update`` run rebuilds them on their
# own image with the extra environment as it is now (hosted.extra_env).

def _upgrade_pending(conn) -> set[str]:
    """Servers with an image upgrade queued, held or running. A resize (an
    upgrade job naming no image) does not count: it keeps whatever image
    the container runs, so either order ends the same."""
    rows = conn.execute("SELECT server_id, payload FROM fleet_jobs WHERE kind = 'upgrade' "
                        "AND state IN ('queued', 'held', 'running')").fetchall()
    return {r["server_id"] for r in rows if json_dict(r["payload"]).get("image")}


def update_pending(conn) -> set[str]:
    """Servers with an update queued or held. It reads the environment when
    the agent takes it, so it applies the newest anyway; one already
    running may not, so it does not count."""
    rows = conn.execute("SELECT server_id FROM fleet_jobs WHERE kind = 'update' AND state IN ('queued', 'held')")
    return {r[0] for r in rows.fetchall()}


def _upgrade_payload(row, tag: str) -> dict:
    return {"label": row["label"], "image": f"{config.FLEET_IMAGE}:{tag}", "tag": tag}


def _run_rows(conn, server_ids: list[str] | None, busy: set[str], kind: str) -> list:
    """The servers a run of ``kind`` takes: those with a host in a state an
    upgrade takes (only ``server_ids`` when given; for an update, only
    those whose container exists), less the ``busy`` ones."""
    rows = conn.execute("SELECT * FROM hosted_servers WHERE host_id != '' AND state IN (%s) ORDER BY created_at"
                        % ",".join("?" * len(UPGRADABLE)), UPGRADABLE).fetchall()
    if server_ids is not None:
        wanted = set(server_ids)
        rows = [r for r in rows if r["id"] in wanted]
    if kind == "update":
        rows = [r for r in rows if created(conn, r["id"])]
    if not rows:
        raise Problem(400, f"no running server to {kind}")
    rows = [r for r in rows if r["id"] not in busy]
    if not rows:
        raise Problem(409, f"every one of those servers already has an {kind} pending")
    return rows


def _start_run(conn, kind: str, rows: list, wave_size: int, payload, what: str, actor: str) -> dict:
    """One ``kind`` job per server row (``payload(row)``) in waves of
    ``wave_size`` under a new run id: the first wave is queued, the rest
    held; each wave is released only when every job before it is done (or
    canceled), so one failure pauses the run until an admin retries or
    cancels the failed job (``release_waves``)."""
    wave_size = max(1, min(int(wave_size or 1), 100))
    run = ("u" if kind == "upgrade" else "e") + db.new_token(6)
    for i, row in enumerate(rows):
        n = i // wave_size + 1
        enqueue(conn, row["host_id"], row["id"], kind, payload(row), wave=f"{run}/{n:03d}",
                state="queued" if n == 1 else "held")
    waves = (len(rows) - 1) // wave_size + 1
    db.audit(conn, f"fleet.{kind}", actor=actor, detail=f"{run} {what} servers={len(rows)} waves={waves}")
    return {"run": run, "jobs": len(rows), "waves": waves}


def upgrade(conn, tag: str, wave_size: int, server_ids: list[str] | None = None, actor: str = "") -> dict:
    """Upgrade servers to ``FLEET_IMAGE:tag`` in waves of ``wave_size``. The
    tag may be the one a server runs already: the agent pulls it, so the
    server gets the registry's newest image for it."""
    if not TAG_RE.match(tag or ""):
        raise Problem(400, "bad image tag")
    rows = _run_rows(conn, server_ids, _upgrade_pending(conn), "upgrade")
    image = f"{config.FLEET_IMAGE}:{tag}"
    return {**_start_run(conn, "upgrade", rows, wave_size, lambda r: _upgrade_payload(r, tag), image, actor),
            "image": image}


def upgrade_outdated(conn, wave_size: int, actor: str = "") -> dict:
    """An upgrade run to the default tag for exactly the outdated servers
    (``outdated_why``)."""
    rows = conn.execute("SELECT * FROM hosted_servers WHERE state != 'deleted'").fetchall()
    ids = [r["id"] for r in rows if outdated_why(r)]
    if not ids:
        raise Problem(409, "no server is outdated")
    return upgrade(conn, default_tag(), wave_size, ids, actor)


def upgrade_in_flight(conn) -> bool:
    """Whether an upgrade run has a job queued, held, running or failed: a
    failed one pauses its run until an admin retries or cancels it."""
    return conn.execute("SELECT 1 FROM fleet_jobs WHERE kind = 'upgrade' AND wave != '' "
                        "AND state IN ('queued', 'held', 'running', 'failed') LIMIT 1").fetchone() is not None


AUTO_UPGRADE_REST = 86400      # an automatic upgrade for a moved image waits this long after the last one


def _upgraded_within(conn, server_id: str, seconds: int) -> bool:
    """Whether an image upgrade of the server (not a resize) finished within
    ``seconds``."""
    return conn.execute("SELECT 1 FROM fleet_jobs WHERE server_id = ? AND kind = 'upgrade' AND state = 'done' "
                        "AND payload LIKE '%\"image\"%' AND finished_at >= ? LIMIT 1",
                        (server_id, db.after(-seconds))).fetchone() is not None


def auto_upgrade(conn) -> dict | None:
    """The hourly pass's upgrade, while ``settings.fleet_auto_upgrade`` is on:
    the outdated servers one per wave, unless an upgrade run is in flight. A
    failed upgrade so stops the automatic ones too until an admin retries
    or cancels it. A server outdated only because its tag moved in the
    registry is taken once a day at most (``AUTO_UPGRADE_REST``): were the
    agent's staleness report ever wrong after a pull, the hourly pass would
    otherwise rebuild that server every hour. The run, or None when none
    was started."""
    if not settings.fleet_auto_upgrade() or upgrade_in_flight(conn):
        return None
    rows = conn.execute("SELECT * FROM hosted_servers WHERE state != 'deleted'").fetchall()
    ids = [r["id"] for r in rows
           if outdated_why(r) == "tag"
           or (outdated_why(r) == "image" and not _upgraded_within(conn, r["id"], AUTO_UPGRADE_REST))]
    if not ids:
        return None
    try:
        run = upgrade(conn, default_tag(), 1, ids, actor="system")
    except Problem:
        return None                # nothing of it that an upgrade takes now
    db.audit(conn, "fleet.auto_upgrade", actor="system", detail=f"{run['run']} {run['image']} servers={run['jobs']}")
    return run


def update_env(conn, wave_size: int, server_ids: list[str] | None = None, actor: str = "") -> dict:
    """Apply the extra environment to running servers in waves: an
    ``update`` job each, which rebuilds the container on its own image.
    Servers with an update waiting already are skipped."""
    rows = _run_rows(conn, server_ids, update_pending(conn), "update")
    return _start_run(conn, "update", rows, wave_size, lambda r: {"label": r["label"]}, "environment", actor)


def upgrade_one(conn, server_id: str, tag: str, actor: str = "") -> dict:
    """One server to ``FLEET_IMAGE:tag``, outside any wave (its own tag
    too). Its ``image_tag`` moves when the job is done."""
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

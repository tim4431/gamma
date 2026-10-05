"""Hosted servers: one paid Gamma container per account at
``<label>.<HOSTED_DOMAIN>`` (docs/dev/hosted.md).

The row in ``hosted_servers`` follows the account's effective plan and its
subscription (``plan_changed``, called by billing and the admin inside
their transaction) and the clock (``tick``, hourly). ``state``:

- ``provisioning``: the row exists, the container does not yet (no host had
  room, or the ``create`` job has not finished);
- ``running``: normal;
- ``grace``: payment failed, still writable until ``grace_until``;
- ``read_only``: the subscription lapsed or the plan stopped being hosted;
  writes are refused, reads and exports work;
- ``stopped``: READ_ONLY_DAYS after read-only, the container is stopped;
- ``deleted``: DELETE_DAYS after read-only, container, data and bucket
  prefix are removed; the row stays with ``deleted_at``;
- ``suspended``: an admin's hold (read-only, the lifecycle leaves it alone
  until an admin resumes it).

``limits`` is the answer the container gets from ``POST /api/hosted/sync``
(``limits_for``), recomputed on every pass so its dates and message are
current. Its ``memory_mb`` and ``cpus`` are the container's size: a pass
that changes them recreates the container with the new size (``_resize``).
"""

import hashlib
import json
import threading
from datetime import timedelta
from urllib.parse import urlsplit

from . import config, db, fleet, mail, oidc
from .accounts import Problem
from .log import log

READ_ONLY_STATES = ("read_only", "stopped", "suspended")
LAPSED = ("canceled", "unpaid", "incomplete_expired")
STATUS = {"provisioning": "active", "running": "active", "grace": "grace", "read_only": "read_only",
          "suspended": "read_only", "stopped": "stopped", "deleted": "stopped"}
DELETE_WARNING_DAYS = 7
WAITING = "waiting for a host with room"   # the report note of a server no host has room for


def _hosted(plan) -> bool:
    return bool(config.PLAN_LIMITS.get(plan or "", {}).get("hosted"))


def url_of(label: str) -> str:
    return f"https://{label}.{config.HOSTED_DOMAIN}" if config.HOSTED_DOMAIN else ""


def _day(ts: str) -> str:
    d = db.parse(ts)
    return f"{d.day} {d.strftime('%b')}"


def _plus_days(ts: str, days: float) -> str:
    t = db.parse(ts) + timedelta(days=days)
    return t.strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _portal() -> str:
    return urlsplit(config.PUBLIC_URL).netloc + "/plan"


def _row(conn, server_id: str):
    return conn.execute("SELECT * FROM hosted_servers WHERE id = ?", (server_id,)).fetchone()


def _of_account(conn, account_id: str):
    return conn.execute("SELECT * FROM hosted_servers WHERE account_id = ?", (account_id,)).fetchone()


def _account(conn, account_id: str):
    """The account, deleted or not (a deleted account's server lapses)."""
    return conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()


def _subscription(conn, account_id: str):
    return conn.execute("SELECT * FROM subscriptions WHERE account_id = ?", (account_id,)).fetchone()


def _host_name(conn, host_id: str) -> str:
    host = conn.execute("SELECT name FROM hosts WHERE id = ?", (host_id,)).fetchone()
    return host["name"] if host else ""


# --- what the account and its subscription say --------------------------------

def target(account, sub, previous_plan: str = "") -> tuple[str, str, str | None]:
    """``(plan, status, grace_until)`` the server should have: status
    ``active``, ``grace`` or ``read_only``. A courtesy grant (a hosted
    ``granted_plan``) and an effective hosted plan with no subscription are
    active and never lapse; otherwise the subscription decides. ``plan`` is
    the effective plan while it is hosted, else the last hosted plan the
    server ran (its limits while read-only)."""
    effective = account["plan"] if account is not None and not account["deleted_at"] else "free"
    if not _hosted(effective):
        fallback = next((p for p in (previous_plan, sub["plan"] if sub else "") if _hosted(p)), "plus")
        return fallback, "read_only", None
    if account["granted_plan"] and _hosted(account["granted_plan"]):
        return effective, "active", None
    status = sub["status"] if sub else "none"
    if status == "past_due":
        until = _plus_days(sub["past_due_since"] or db.now(), config.GRACE_DAYS)
        return effective, ("grace" if db.now() < until else "read_only"), until
    if status in LAPSED or (sub is not None and sub["ended_at"]):
        return effective, "read_only", None
    return effective, "active", None


def _message(state: str, row, grace_until: str | None) -> str:
    if state == "grace" and grace_until:
        return (f"Payment failed; this server becomes read-only on {_day(grace_until)} unless the card is fixed "
                f"at {_portal()}.")
    if state == "read_only":
        stops = _plus_days(row["state_changed_at"], config.READ_ONLY_DAYS)
        return (f"This server is read-only because its plan ended. Reads and exports still work; it stops on "
                f"{_day(stops)}. Resume the plan at {_portal()}.")
    if state == "stopped":
        gone = _plus_days(row["state_changed_at"], config.DELETE_DAYS - config.READ_ONLY_DAYS)
        return f"This server is stopped and is deleted on {_day(gone)}. Resume the plan at {_portal()}."
    if state == "suspended":
        return "This server is suspended by Gamma Cloud. Reads and exports still work; writes are refused."
    return ""


def limits_for(row, plan: str, grace_until: str | None) -> dict:
    """The sync answer for a server in its current state. A grace period
    (``grace_until`` set) shows on a server still being provisioned too."""
    p = config.PLAN_LIMITS[plan]
    status = STATUS[row["state"]]
    if grace_until and status == "active":
        status = "grace"
    return {"plan": plan, "status": status, "read_only": row["state"] in READ_ONLY_STATES,
            "policy": p["policy"], "max_accounts": p["max_accounts"], "quota_mb": p["quota_mb"],
            "max_upload_mb": p["max_upload_mb"], "memory_mb": p["memory_mb"], "cpus": p["cpus"],
            "offsite": {"interval_s": p["offsite_interval_s"], "keep": p["offsite_keep"]},
            "grace_until": grace_until if status == "grace" else None,
            "message": _message("grace" if status == "grace" else row["state"], row, grace_until)}


# --- mail ---------------------------------------------------------------------

def _send(to: str, subject: str, text: str, html: str) -> None:
    try:
        mail.send(to, subject, text, html)
    except mail.MailError as e:
        log.warning("hosted-server mail to %s failed: %s", to, e)


def _mail(conn, account_id: str, subject: str, paragraphs: list[str], button: tuple[str, str] | None = None) -> None:
    """Lifecycle mail to the account's address. It may be called inside a
    caller's write transaction, so SMTP goes out on a thread rather than
    holding the lock."""
    account = _account(conn, account_id)
    if account is None or not account["email"]:
        return
    text, html = mail.compose(f"Hi {account['display_name'] or account['username']},", paragraphs, button)
    if config.MAIL_BACKEND == "smtp":
        threading.Thread(target=_send, args=(account["email"], subject, text, html), daemon=True).start()
    else:
        _send(account["email"], subject, text, html)


def _plan_button() -> tuple[str, str]:
    return ("Open your plan", config.PUBLIC_URL + "/plan")


# --- state changes ------------------------------------------------------------

def _set_state(conn, row, state: str, actor: str, why: str = "") -> None:
    if state == row["state"]:
        return
    conn.execute("UPDATE hosted_servers SET state = ?, read_only = ?, state_changed_at = ? WHERE id = ?",
                 (state, 1 if state in READ_ONLY_STATES else 0, db.now(), row["id"]))
    db.audit(conn, "hosted.state", row["account_id"], actor,
             f"{row['label']} {row['state']} -> {state}" + (f" ({why})" if why else ""))


def _store_limits(conn, server_id: str, plan: str, grace_until: str | None) -> dict:
    row = _row(conn, server_id)
    limits = limits_for(row, plan, grace_until)
    conn.execute("UPDATE hosted_servers SET limits = ? WHERE id = ?", (json.dumps(limits), server_id))
    return limits


def _created(conn, row) -> bool:
    """Whether the server's container exists: its last create job is done."""
    job = conn.execute("SELECT state FROM fleet_jobs WHERE server_id = ? AND kind = 'create' "
                       "ORDER BY created_at DESC, rowid DESC LIMIT 1", (row["id"],)).fetchone()
    return bool(job and job["state"] == "done")


def _apply(conn, row, actor: str) -> dict:
    """Move a live server to what its account says now and store its
    limits; returns them. A plan with another size resizes the container."""
    before = fleet.json_dict(row["limits"])
    previous = before.get("plan", "")
    plan, status, grace_until = target(_account(conn, row["account_id"]), _subscription(conn, row["account_id"]),
                                       previous)
    state = row["state"]
    if state not in ("deleted", "suspended"):
        # a server whose container was never made goes back to provisioning
        live = "running" if _created(conn, row) else "provisioning"
        new = state
        if status == "active" and state in ("grace", "read_only", "stopped"):
            new = live
        elif status == "grace" and state in ("running", "read_only", "stopped"):
            new = "grace" if live == "running" else live
        elif status == "read_only" and state in ("provisioning", "running", "grace"):
            new = "read_only"
        if new != state:
            if state == "stopped" and live == "running":
                fleet.enqueue(conn, row["host_id"], row["id"], "start", {"label": row["label"]})
            _set_state(conn, row, new, actor, status)
            if new == "read_only":
                stops = _plus_days(db.now(), config.READ_ONLY_DAYS)
                _mail(conn, row["account_id"], "Your Gamma server is read-only", [
                    f"Your plan ended, so {url_of(row['label']) or row['label']} is now read-only: you can still "
                    "open everything and export it from Settings → Backups, but nothing new can be written.",
                    f"It stops on {_day(stops)} and is deleted {config.DELETE_DAYS} days after today. Resuming "
                    "the plan before then brings it back as it was."], _plan_button())
    if previous and previous != plan and row["state"] != "deleted":
        db.audit(conn, "hosted.limits", row["account_id"], actor, f"{row['label']} {previous} -> {plan}")
    limits = _store_limits(conn, row["id"], plan, grace_until)
    if before.get("memory_mb"):                   # a first store has nothing to move from: create sizes it
        _resize_if_moved(conn, row["id"], fleet.size_of(before), actor)
    return limits


def _resize_if_moved(conn, server_id: str, was: tuple[int, float] | None, actor: str) -> None:
    """Resize the server's container when its plan's size is not ``was``
    (the size it was made or last sized with). ``None`` means unknown, a
    create job whose payload was blanked: resize to be sure, which costs a
    no-op update at most."""
    row = _row(conn, server_id)
    if row is None or row["state"] == "deleted":
        return
    size = fleet.size_of(fleet.json_dict(row["limits"]))
    if was != size:
        _resize(conn, row, size, actor)


def _resize(conn, row, size: tuple[int, float], actor: str) -> None:
    """Size the server's container to ``(memory_mb, cpus)``: an ``upgrade``
    job that names no image, which the agent applies in place (Docker's
    update: the same container and image, no restart, a stopped one stays
    stopped); a queued one takes the new size rather than a second job. Before
    the container exists, a queued create job takes it instead, and one
    already running is resized when it is done (``job_finished``). A
    server with no host is sized at placement."""
    if not row["host_id"]:
        return
    fields = {"memory_mb": size[0], "cpus": size[1]}
    kind = "upgrade" if _created(conn, row) else "create"
    for job in conn.execute("SELECT id, payload FROM fleet_jobs WHERE server_id = ? AND kind = ? AND state = 'queued'",
                            (row["id"], kind)).fetchall():
        payload = fleet.json_dict(job["payload"])
        if kind == "create" or not payload.get("image"):
            conn.execute("UPDATE fleet_jobs SET payload = ? WHERE id = ?", (json.dumps({**payload, **fields}), job["id"]))
            return
    if kind == "upgrade":
        fleet.enqueue(conn, row["host_id"], row["id"], "upgrade", {"label": row["label"], **fields})
        db.audit(conn, "hosted.resize", row["account_id"], actor, f"{row['label']} {size[0]} MB, {size[1]:g} CPU")


def plan_changed(conn, account_id: str, actor: str = "system") -> None:
    """Recompute the account's hosted server from its effective plan and
    subscription, inside the caller's transaction: create one when the plan
    became hosted (hosting must be on, ``HOSTED_DOMAIN``), resume, start
    the grace period, turn it read-only, or relimit it."""
    row = _of_account(conn, account_id)
    if row is not None and row["state"] != "deleted":
        _apply(conn, row, actor)
        return
    account = _account(conn, account_id)
    _, status, _ = target(account, _subscription(conn, account_id))
    if status == "read_only":                        # so also any plan that is not hosted
        return
    if not config.HOSTED_DOMAIN:
        log.info("account %s is on a hosted plan but hosting is off (GAMMA_CLOUD_HOSTED_DOMAIN)", account_id)
        return
    if not account["email_verified_at"]:
        # no server for an unconfirmed address; the verify path calls this again
        log.info("account %s is on a hosted plan but its e-mail is not confirmed yet", account_id)
        return
    create(conn, account_id, actor)


def _free_label(conn, account) -> str:
    """The new server's label: the username, unless another account's
    server row (in any state: a deleted one may still have its delete job
    pending) holds it, then the username plus a few characters derived from
    the account id. Another row is never relabelled: its container and data
    directory are named after its own label."""
    def taken(label):
        return conn.execute("SELECT 1 FROM hosted_servers WHERE label = ? AND account_id != ?",
                            (label, account["id"])).fetchone() is not None
    label = account["username"]
    digest = hashlib.sha256(account["id"].encode()).hexdigest()
    n = 4
    while taken(label):
        label = f"{account['username'][:62 - n]}-{digest[:n]}"
        n += 1
    return label


def create(conn, account_id: str, actor: str = "system") -> None:
    """A new server row for the account (or a deleted one brought back) and
    its placement."""
    account = _account(conn, account_id)
    if account is None or account["deleted_at"]:
        raise Problem(404, "no such account")
    if not config.HOSTED_DOMAIN:
        raise Problem(400, "Hosting is off: set GAMMA_CLOUD_HOSTED_DOMAIN.")
    row = _of_account(conn, account_id)
    if row is not None and row["state"] != "deleted":
        raise Problem(409, "This account already has a hosted server.")
    if not account["email_verified_at"]:
        raise Problem(409, "The account's e-mail address is not confirmed yet.")
    label, ts = _free_label(conn, account), db.now()
    if row is None:
        server_id = "s_" + db.new_token(9)
        conn.execute("INSERT INTO hosted_servers (id, account_id, label, image_tag, state, state_changed_at, "
                     "created_at) VALUES (?, ?, ?, ?, 'provisioning', ?, ?)",
                     (server_id, account_id, label, config.FLEET_IMAGE_TAG, ts, ts))
    else:
        server_id = row["id"]
        conn.execute("UPDATE hosted_servers SET label = ?, host_id = '', client_id = '', image_tag = ?, "
                     "state = 'provisioning', read_only = 0, report = '{}', reported_at = NULL, synced_at = NULL, "
                     "state_changed_at = ?, deleted_at = NULL WHERE id = ?",
                     (label, config.FLEET_IMAGE_TAG, ts, server_id))
    db.audit(conn, "hosted.create", account_id, actor, f"{server_id} {label}")
    _provision(conn, _row(conn, server_id))
    _apply(conn, _row(conn, server_id), actor)


def _plan_of(conn, row) -> str:
    """The plan a server is placed and created for: the account's effective
    plan while it is hosted, else the last one the server ran, else Plus."""
    account = _account(conn, row["account_id"])
    for plan in (account["plan"] if account else "", fleet.json_dict(row["limits"]).get("plan")):
        if _hosted(plan):
            return plan
    return "plus"


def create_payload(conn, server_id: str) -> dict:
    """The ``create`` job's payload, with a new client secret: the
    container's OIDC client is made on first use and its secret rotated on
    every later build (the payload is the only place the secret exists).
    The container is sized for its plan."""
    row = _row(conn, server_id)
    plan = _plan_of(conn, row)
    p = config.PLAN_LIMITS[plan]
    url = url_of(row["label"])
    if row["client_id"] and oidc.get_client(conn, row["client_id"]):
        client_id, secret = row["client_id"], oidc.rotate_secret(conn, row["client_id"], actor="system")
    else:
        client_id, secret = oidc.create_client(conn, name=f"Hosted: {row['label']}", kind="container",
                                               redirect_uris=[url + "/api/auth/cloud/callback"],
                                               server_id=row["id"], actor="system", owner=row["account_id"])
        conn.execute("UPDATE hosted_servers SET client_id = ? WHERE id = ?", (client_id, row["id"]))
    # GAMMA_GUEST_MAX=0: no guest logins on a customer's server. A guest
    # account would not count against the plan's accounts, and would spend
    # the customer's memory and disk; the public demo is where guests live.
    env = {"GAMMA_HOSTED": "1", "GAMMA_CLOUD_ISSUER": config.PUBLIC_URL, "GAMMA_CLOUD_CLIENT_ID": client_id,
           "GAMMA_CLOUD_CLIENT_SECRET": secret, "GAMMA_CLOUD_POLICY": p["policy"],
           "GAMMA_CLOUD_ADMIN_SUBJECT": row["account_id"], "GAMMA_PUBLIC_URL": url, "GAMMA_GUEST_MAX": "0"}
    return {"label": row["label"], "account_id": row["account_id"], "plan": plan,
            "image": f"{config.FLEET_IMAGE}:{row['image_tag'] or config.FLEET_IMAGE_TAG}", "env": env,
            "data_dir": row["label"], "memory_mb": p["memory_mb"], "cpus": p["cpus"], "network": None,
            "public_url": url}


def _provision(conn, row, quiet: bool = False) -> bool:
    """Place a ``provisioning`` server with no host and enqueue its create
    job; whether it was placed. No host with room: the row waits (a note
    in its report), and the next heartbeat or the hourly tick tries again.
    ``quiet`` (a heartbeat's retry) logs nothing for a server still
    waiting, since that repeats every five minutes per host."""
    plan = _plan_of(conn, row)
    host = fleet.place(conn, plan)
    if host is None:
        report = fleet.json_dict(row["report"])
        if report.get("note") != WAITING:
            conn.execute("UPDATE hosted_servers SET report = ? WHERE id = ?",
                         (json.dumps({**report, "note": WAITING}), row["id"]))
        if not quiet:
            log.warning("no fleet host has room for %s (%s)", row["label"], plan)
        return False
    conn.execute("UPDATE hosted_servers SET host_id = ?, report = '{}' WHERE id = ?", (host["id"], row["id"]))
    fleet.enqueue(conn, host["id"], row["id"], "create", create_payload(conn, row["id"]))
    db.audit(conn, "hosted.place", row["account_id"], "system", f"{row['label']} on {host['name']}")
    return True


def place_waiting(conn) -> int:
    """Place every server still waiting for a host. A heartbeat calls it, so
    a new host's first report, or room freed on one, takes the waiting
    servers at once instead of at the next hourly tick. The caller commits."""
    if not config.HOSTED_DOMAIN:
        return 0
    rows = conn.execute("SELECT * FROM hosted_servers WHERE state = 'provisioning' AND host_id = '' "
                        "ORDER BY created_at").fetchall()
    placed = 0
    for row in rows:
        # one server's failure must not fail the heartbeat that carries it
        conn.execute("SAVEPOINT place_waiting")
        try:
            placed += _provision(conn, row, quiet=True)
        except Exception as e:  # noqa: BLE001
            conn.execute("ROLLBACK TO place_waiting")
            log.warning("placing %s failed: %s", row["label"], e)
        conn.execute("RELEASE place_waiting")
    return placed


def _delete(conn, row, actor: str, why: str = "") -> None:
    """The end: the container, its data and its bucket prefix go (a delete
    job), the client is removed, the row stays as ``deleted``."""
    fleet.enqueue(conn, row["host_id"], row["id"], "delete", {"label": row["label"], "account_id": row["account_id"]})
    conn.execute("UPDATE fleet_jobs SET state = 'canceled', finished_at = ?, payload = "
                 "CASE kind WHEN 'create' THEN '{}' ELSE payload END "
                 "WHERE server_id = ? AND state IN ('queued', 'held') AND kind != 'delete'", (db.now(), row["id"]))
    if row["client_id"]:
        oidc.delete_client(conn, row["client_id"], actor=actor)
    _set_state(conn, row, "deleted", actor, why)
    conn.execute("UPDATE hosted_servers SET deleted_at = ? WHERE id = ?", (db.now(), row["id"]))


def _note(conn, row, note: str) -> None:
    """Set (or with ``""`` clear) the admin's note in the server's report."""
    report = fleet.json_dict(row["report"])
    if note:
        report["note"] = note
    elif report.pop("note", None) is None:
        return
    conn.execute("UPDATE hosted_servers SET report = ? WHERE id = ?", (json.dumps(report), row["id"]))


def _tag_of(image) -> str:
    """The tag of a ``FLEET_IMAGE:<tag>`` image, else ``""``."""
    prefix = config.FLEET_IMAGE + ":"
    tag = image[len(prefix):] if isinstance(image, str) and image.startswith(prefix) else ""
    return tag if fleet.TAG_RE.match(tag) else ""


def job_finished(conn, job: dict, payload: dict, ok: bool, result: dict) -> None:
    """What a finished job changes on its server (``fleet.complete``,
    ``fleet.fail_stuck``): a done create runs the server (and resizes it
    when its plan's size moved meanwhile), a failed one leaves a note, a
    done upgrade or rollback moves ``image_tag``."""
    row = _row(conn, job["server_id"]) if job["server_id"] else None
    if row is None:
        return
    error = str(result.get("error") or "")[:300] if not ok else ""
    db.audit(conn, f"fleet.job_{'done' if ok else 'failed'}", row["account_id"], "agent",
             f"{job['id']} {job['kind']} {row['label']}" + (f": {error}" if error else ""))
    if job["kind"] == "create" and ok:
        _note(conn, row, "")
        if row["state"] == "provisioning":
            _set_state(conn, row, "running", "agent", "created")
            _apply(conn, _row(conn, row["id"]), "agent")
            url = url_of(row["label"])
            _mail(conn, row["account_id"], "Your Gamma is ready", [
                f"Your Gamma server is up at {url}. Sign in there with this Gamma Cloud account; you are its admin.",
                "The desktop app, the browser extension and your assistants can all connect to that address."],
                ("Open your Gamma", url))
        _resize_if_moved(conn, row["id"], fleet.size_of(payload) if payload.get("memory_mb") else None, "agent")
    elif job["kind"] == "create":
        _note(conn, row, f"create failed: {error}")
    elif job["kind"] == "upgrade" and ok and payload.get("tag"):
        conn.execute("UPDATE hosted_servers SET image_tag = ? WHERE id = ?", (payload["tag"], row["id"]))
    elif job["kind"] == "rollback" and ok and _tag_of(result.get("image")):
        conn.execute("UPDATE hosted_servers SET image_tag = ? WHERE id = ?", (_tag_of(result["image"]), row["id"]))


# --- the hourly pass ----------------------------------------------------------

def tick(conn) -> None:
    """Hosts gone silent, jobs that never finished, placement retries, the
    lifecycle (grace ending, read-only → stopped → deleted, the warning a
    week before), and the next upgrade waves. The caller commits."""
    fleet.stale_hosts(conn)
    fleet.fail_stuck(conn)
    now = db.now()
    for row in conn.execute("SELECT * FROM hosted_servers WHERE state != 'deleted'").fetchall():
        # one server's failure must not stop the others' lifecycle
        conn.execute("SAVEPOINT hosted_tick")
        try:
            _tick_one(conn, row, now)
        except Exception as e:  # noqa: BLE001
            conn.execute("ROLLBACK TO hosted_tick")
            log.warning("hosted tick for %s failed: %s", row["label"], e)
        conn.execute("RELEASE hosted_tick")
    fleet.release_waves(conn)


def _tick_one(conn, row, now: str) -> None:
    if row["state"] == "provisioning" and not row["host_id"] and config.HOSTED_DOMAIN:
        _provision(conn, row)
    _apply(conn, _row(conn, row["id"]), "system")
    row = _row(conn, row["id"])
    if row["state"] == "read_only" and now >= _plus_days(row["state_changed_at"], config.READ_ONLY_DAYS):
        fleet.enqueue(conn, row["host_id"], row["id"], "stop", {"label": row["label"]})
        _set_state(conn, row, "stopped", "system", f"read-only for {config.READ_ONLY_DAYS} days")
        row = _row(conn, row["id"])
        gone = _plus_days(row["state_changed_at"], config.DELETE_DAYS - config.READ_ONLY_DAYS)
        _mail(conn, row["account_id"], "Your Gamma server is stopped", [
            f"{url_of(row['label']) or row['label']} has been read-only for {config.READ_ONLY_DAYS} days and "
            f"is now stopped. It is deleted with all its files on {_day(gone)}.",
            "Resuming the plan before then starts it again as it was."], _plan_button())
        _store_limits(conn, row["id"], fleet.json_dict(row["limits"]).get("plan") or "plus", None)
    elif row["state"] == "stopped":
        gone = _plus_days(row["state_changed_at"], config.DELETE_DAYS - config.READ_ONLY_DAYS)
        if now >= gone:
            _delete(conn, row, "system", f"{config.DELETE_DAYS} days after the plan ended")
        elif now >= _plus_days(gone, -DELETE_WARNING_DAYS) and not conn.execute(
                "SELECT 1 FROM audit WHERE event = 'hosted.delete_warning' AND account_id = ? AND at >= ?",
                (row["account_id"], row["state_changed_at"])).fetchone():
            db.audit(conn, "hosted.delete_warning", row["account_id"], "system", row["label"])
            _mail(conn, row["account_id"], "Your Gamma server will be deleted in a week", [
                f"{url_of(row['label']) or row['label']} is deleted with all its files on {_day(gone)}.",
                "To keep it, resume the plan before then; to keep only the files, resume it for a moment "
                "and download a backup from Settings → Backups."], _plan_button())


# --- admin actions ------------------------------------------------------------

ACTIONS = ("restart", "stop", "start", "suspend", "resume", "delete")
JOB_ACTIONS = ("logs", "rollback")             # answered with the job, not the server


def _live(conn, server_id: str, need_host: bool = False):
    row = _row(conn, server_id)
    if row is None:
        raise Problem(404, "no such server")
    if row["state"] == "deleted":
        raise Problem(409, "the server is deleted")
    if need_host and not row["host_id"]:
        raise Problem(409, "the server has no host yet")
    return row


def _container_job(conn, row, kind: str, actor: str) -> str:
    job_id = fleet.enqueue(conn, row["host_id"], row["id"], kind, {"label": row["label"]})
    db.audit(conn, f"hosted.{kind}", row["account_id"], actor, row["label"])
    return job_id


def admin_action(conn, server_id: str, action: str, actor: str) -> dict:
    """``restart``/``stop``/``start`` act on the container only (the
    lifecycle state stays); ``suspend`` holds the server read-only until
    ``resume``; ``delete`` ends it now (a paying account gets a new one at
    its next plan change)."""
    if action not in ACTIONS:
        raise Problem(400, "action must be " + ", ".join(ACTIONS))
    row = _live(conn, server_id, need_host=action in ("restart", "stop", "start"))
    if action in ("restart", "stop", "start"):
        _container_job(conn, row, action, actor)
    elif action == "suspend":
        _set_state(conn, row, "suspended", actor, "admin")
        _apply(conn, _row(conn, server_id), actor)
    elif action == "resume":
        if row["state"] != "suspended":
            raise Problem(409, "only a suspended server is resumed")
        _set_state(conn, row, "running" if _created(conn, row) else "provisioning", actor, "admin")
        _apply(conn, _row(conn, server_id), actor)
    else:
        _delete(conn, row, actor, "admin")
    return admin_view(conn, _row(conn, server_id))


def admin_job(conn, server_id: str, kind: str, actor: str) -> dict:
    """``logs`` (the container's last lines, in the job's result) or
    ``rollback`` (back to the container a failed upgrade kept): the job."""
    if kind not in JOB_ACTIONS:
        raise Problem(400, "action must be " + ", ".join(JOB_ACTIONS))
    return fleet.job(conn, _container_job(conn, _live(conn, server_id, need_host=True), kind, actor))


def admin_view(conn, row) -> dict:
    """A server as the admin sees it: the row, its size and image (and
    whether that is the fleet's default), its account and host."""
    out = {k: row[k] for k in row.keys()}
    out["read_only"] = bool(row["read_only"])
    out["limits"], out["report"] = fleet.json_dict(row["limits"]), fleet.json_dict(row["report"])
    out["url"] = url_of(row["label"])
    account = _account(conn, row["account_id"])
    out["username"] = account["username"] if account else ""
    out["plan"] = account["plan"] if account else ""
    out["memory_mb"], out["cpus"] = fleet.size_of(out["limits"])
    out["quota_mb"] = out["limits"].get("quota_mb") or config.PLAN_LIMITS[_plan_of(conn, row)]["quota_mb"]
    out["image"] = f"{config.FLEET_IMAGE}:{row['image_tag'] or config.FLEET_IMAGE_TAG}"
    out["outdated"] = row["state"] != "deleted" and out["image"] != fleet.default_image()
    out["host"] = _host_name(conn, row["host_id"])
    out["jobs"] = dict(conn.execute("SELECT state, COUNT(*) FROM fleet_jobs WHERE server_id = ? GROUP BY state",
                                    (row["id"],)).fetchall())
    return out


def servers(conn) -> list[dict]:
    return [admin_view(conn, r) for r in conn.execute("SELECT * FROM hosted_servers ORDER BY created_at DESC").fetchall()]


def provision(conn, account_id: str, actor: str) -> dict:
    """Host an account by hand (an invited customer before billing): its
    effective plan must be hosted."""
    account = _account(conn, account_id)
    if account is None or account["deleted_at"]:
        raise Problem(404, "no such account")
    if not _hosted(account["plan"]):
        raise Problem(400, f"The account's plan ({account['plan']}) has no hosted server.")
    row = _of_account(conn, account_id)
    if row is not None and row["state"] == "provisioning" and not row["host_id"]:
        _provision(conn, row)                     # a server still waiting for a host: try placing it now
    else:
        create(conn, account_id, actor)
    return admin_view(conn, _of_account(conn, account_id))


def purge_account(conn, account_id: str, actor: str = "system") -> None:
    """Before an account row is purged: a live server is deleted now (its
    delete job still runs) and the row goes, since it references the
    account. A soft-deleted account needs nothing: ``plan_changed`` reads a
    deleted account as a lapse, so its server turns read-only and follows
    the lifecycle, and a restore within the grace period resumes it."""
    row = _of_account(conn, account_id)
    if row is None:
        return
    if row["state"] != "deleted":
        _delete(conn, row, actor, "account purged")
    conn.execute("DELETE FROM hosted_servers WHERE account_id = ?", (account_id,))


# --- the container's sync and the plan page -------------------------------------

def _text(value, n: int) -> str:
    return str(value or "")[:n]


def sync(conn, server_id: str, body: dict) -> dict:
    """A container's report in, its limits out."""
    row = _row(conn, server_id)
    schema = body.get("schema")
    report = {"version": _text(body.get("version"), 80),
              "schema": schema if isinstance(schema, int) and not isinstance(schema, bool) else None,
              "accounts": fleet.nonneg(body.get("accounts")), "uploads_bytes": fleet.nonneg(body.get("uploads_bytes")),
              "data_bytes": fleet.nonneg(body.get("data_bytes")), "public_url": _text(body.get("public_url"), 300)}
    agent = fleet.json_dict(row["report"]).get("agent")
    if agent:
        report["agent"] = agent
    ts = db.now()
    conn.execute("UPDATE hosted_servers SET report = ?, reported_at = ?, synced_at = ? WHERE id = ?",
                 (json.dumps(report), ts, ts, server_id))
    if report["public_url"] and report["public_url"].rstrip("/") != url_of(row["label"]):
        log.info("hosted server %s reports public URL %s", row["label"], report["public_url"])
    return _apply(conn, _row(conn, server_id), "container")


def status_for(conn, account_id: str) -> dict | None:
    """The account's hosted server for the plan page, or None."""
    row = _of_account(conn, account_id)
    if row is None:
        return None
    return {"id": row["id"], "label": row["label"], "url": url_of(row["label"]), "state": row["state"],
            "read_only": bool(row["read_only"]), "limits": fleet.json_dict(row["limits"]),
            "report": fleet.json_dict(row["report"]), "reported_at": row["reported_at"], "synced_at": row["synced_at"],
            "host": _host_name(conn, row["host_id"])}

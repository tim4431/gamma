"""The admin API under ``/api/admin``: accounts, invites, OIDC clients,
the server settings with the plans on sale, the environment's configuration
(read-only) and a test mail, the audit log, the fleet's machines and every
container on them, hosted servers and jobs, the Overview with the operator's
alerts, and the fleet's history. Only an
account with ``is_admin`` (set
with ``manage.py set-admin``) and only through a portal session — never a
bearer token from a Gamma server."""

from contextlib import closing

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from .. import accounts, alerts, billing, config, db, fleet, hosted, mail, metrics, oidc, ratelimit, settings
from ..accounts import Problem
from .accounts import portal_account, send_mail, verify_message

router = APIRouter(prefix="/api/admin")


def require_admin(conn, request: Request):
    account = portal_account(conn, request)
    if not account["is_admin"]:
        raise HTTPException(403, "admin only")
    return account


def _row(account) -> dict:
    return {**accounts.public(account), "deleted_at": account["deleted_at"]}


# --- accounts -----------------------------------------------------------------

@router.get("/accounts")
def list_accounts(request: Request, q: str = "", limit: int = 50, offset: int = 0):
    limit = max(1, min(limit, 200))
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        like = f"%{q.strip().lower()}%"
        rows = conn.execute("SELECT * FROM accounts WHERE username LIKE ? OR email LIKE ? OR id = ? "
                            "ORDER BY created_at DESC LIMIT ? OFFSET ?", (like, like, q.strip(), limit, offset)).fetchall()
        total = conn.execute("SELECT COUNT(*) FROM accounts WHERE username LIKE ? OR email LIKE ? OR id = ?",
                             (like, like, q.strip())).fetchone()[0]
        return {"accounts": [_row(r) for r in rows], "total": total}


@router.get("/accounts/{account_id}")
def get_account(account_id: str, request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        row = conn.execute("SELECT * FROM accounts WHERE id = ?", (account_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such account")
        audit = conn.execute("SELECT at, actor, event, detail FROM audit WHERE account_id = ? ORDER BY id DESC LIMIT 100",
                             (account_id,)).fetchall()
        return {"account": _row(row), "devices": oidc.devices(conn, account_id),
                "audit": [dict(a) for a in audit]}


class AccountPatch(BaseModel):
    plan: str | None = None
    granted_until: str | None = None
    is_admin: bool | None = None
    verified: bool | None = None
    username: str | None = None


@router.patch("/accounts/{account_id}")
def patch_account(account_id: str, body: AccountPatch, request: Request):
    """``plan`` replaces the grant, with no end unless ``granted_until``
    comes with it; ``granted_until`` alone dates the grant the account has
    (an ISO date or date and time; null clears it)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        account = accounts.by_id(conn, account_id)
        if not account:
            raise HTTPException(404, "no such account")
        if body.plan is not None or "granted_until" in body.model_fields_set:
            accounts.set_plan(conn, account_id, account["granted_plan"] if body.plan is None else body.plan,
                              admin["id"], until=accounts.until_from(body.granted_until))
        if body.is_admin is not None:
            if account_id == admin["id"] and not body.is_admin:
                raise Problem(400, "you cannot demote yourself")
            accounts.set_admin(conn, account_id, body.is_admin, admin["id"])
        if body.verified:
            accounts.mark_verified(conn, account_id)
        if body.username is not None:
            accounts.set_username(conn, account_id, body.username, admin["id"])
        conn.commit()
        return {"account": _row(accounts.by_id(conn, account_id))}


@router.post("/accounts/{account_id}/resend-verify")
def admin_resend_verify(account_id: str, request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        account = accounts.by_id(conn, account_id)
        if not account:
            raise HTTPException(404, "no such account")
        if account["email_verified_at"]:
            return {"ok": True, "already": True}
        message = verify_message(conn, account)
        conn.commit()
    send_mail(*message)
    return {"ok": True}


@router.post("/accounts/{account_id}/delete")
def admin_delete(account_id: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        if account_id == admin["id"]:
            raise HTTPException(400, "delete your own account from the account page")
        if not accounts.by_id(conn, account_id):
            raise HTTPException(404, "no such account")
        conn.commit()
    # a live subscription is cancelled at Stripe first; 502 (nothing deleted) if Stripe cannot be reached
    billing.cancel_for_deletion(account_id, admin["id"])
    with closing(db.connect()) as conn:
        accounts.delete(conn, account_id, actor=admin["id"])
        conn.commit()
    return {"ok": True}


def _deleted(conn, account_id: str):
    if accounts.by_id_deleted(conn, account_id):
        return
    if accounts.by_id(conn, account_id):
        raise Problem(409, "The account is not deleted.")
    raise HTTPException(404, "no such account")


@router.post("/accounts/{account_id}/restore")
def admin_restore(account_id: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        _deleted(conn, account_id)
        accounts.restore(conn, account_id, admin["id"])
        conn.commit()
        return {"account": _row(accounts.by_id(conn, account_id))}


@router.post("/accounts/{account_id}/purge")
def admin_purge(account_id: str, request: Request):
    """Only a deleted account: a live one is deleted first, so the purge
    never skips the delete's sign-out."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        _deleted(conn, account_id)
        accounts.purge(conn, account_id, admin["id"])
        conn.commit()
    return {"ok": True}


# --- invites ------------------------------------------------------------------

class InviteBody(BaseModel):
    uses: int = 1
    plan: str = "free"
    note: str = ""
    expires_days: int | None = None   # the code stops working that many days from now
    grant_days: int | None = None     # the plan it grants ends that many days after each registration


class InvitePatch(BaseModel):
    disabled: bool


@router.get("/invites")
def list_invites(request: Request):
    """Each row with ``used`` and ``state`` (``accounts.invite_view``)."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        rows = conn.execute("SELECT * FROM invites ORDER BY created_at DESC LIMIT 500").fetchall()
        return {"invites": [accounts.invite_view(r) for r in rows]}


@router.post("/invites")
def create_invite(body: InviteBody, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        row = accounts.make_invite(conn, uses=body.uses, plan=body.plan, note=body.note, created_by=admin["id"],
                                   expires_days=body.expires_days, grant_days=body.grant_days)
        conn.commit()
        return {"invite": row}


@router.get("/invites/{code}")
def get_invite(code: str, request: Request):
    """The invite and the accounts that registered with it, newest first."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        row = conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()
        if not row:
            raise HTTPException(404, "no such invite")
        used = conn.execute("SELECT id, username, plan, created_at, deleted_at FROM accounts WHERE invite_code = ? "
                            "ORDER BY created_at DESC", (code,)).fetchall()
        return {"invite": accounts.invite_view(row), "accounts": [dict(a) for a in used]}


@router.patch("/invites/{code}")
def patch_invite(code: str, body: InvitePatch, request: Request):
    """Turn a code off, or on again."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        row = accounts.set_invite_disabled(conn, code, body.disabled, admin["id"])
        conn.commit()
        return {"invite": row}


@router.delete("/invites/{code}")
def delete_invite(code: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        cur = conn.execute("DELETE FROM invites WHERE code = ?", (code,))
        if not cur.rowcount:
            raise HTTPException(404, "no such invite")
        db.audit(conn, "invite.delete", actor=admin["id"], detail=code)
        conn.commit()
    return {"ok": True}


# --- server settings ----------------------------------------------------------

def _settings_view() -> dict:
    """``settings.admin_view`` and the Plans section's rows: each paid plan,
    whether the operator offers it (``on_sale``), whether it can be bought
    now (``sellable``) and why not (``billing.why_not``)."""
    plans = [{"plan": p, "on_sale": p in settings.plans_on_sale(), "sellable": billing.can_sell(p),
              "reason": billing.why_not(p)} for p in settings.PAID_PLANS]
    return {**settings.admin_view(), "plans": plans}


@router.get("/settings")
def get_settings(request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
    return _settings_view()


@router.patch("/settings")
def patch_settings(body: dict, request: Request):
    """Write the keys given and leave the rest (``settings.update``)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        try:
            settings.update(conn, body, actor=admin["id"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        conn.commit()
    settings.invalidate()
    return _settings_view()


@router.get("/config")
def get_config(request: Request):
    """What the environment fixes, read-only (``config.admin_view``): never
    a secret's value."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
    return {"groups": config.admin_view(db.SCHEMA_VERSION)}


@router.post("/test-mail")
def test_mail(request: Request):
    """A short message to the calling admin's own address; answers what
    became of it, or 502 with the mail server's error."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        conn.commit()
    ratelimit.check(f"test-mail:{admin['id']}", 5, 600)
    try:
        detail = mail.send_test(admin["email"])
    except mail.MailError as e:
        raise Problem(502, f"The mail could not be sent: {e}") from e
    return {"ok": True, "to": admin["email"], "backend": config.MAIL_BACKEND, "detail": detail}


# --- OIDC clients -------------------------------------------------------------

class ClientBody(BaseModel):
    name: str
    kind: str
    redirect_uris: list[str]
    server_id: str = ""


@router.get("/clients")
def list_clients(request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        rows = conn.execute("SELECT client_id, kind, name, redirect_uris, server_id, created_at, owner_account_id "
                            "FROM oauth_clients "
                            "ORDER BY created_at DESC").fetchall()
        return {"clients": [dict(r) for r in rows]}


@router.post("/clients")
def create_client(body: ClientBody, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        client_id, secret = oidc.create_client(conn, name=body.name, kind=body.kind, redirect_uris=body.redirect_uris,
                                               server_id=body.server_id, actor=admin["id"])
        conn.commit()
    return {"client_id": client_id, "client_secret": secret}


@router.delete("/clients/{client_id}")
def delete_client(client_id: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        if not oidc.delete_client(conn, client_id, actor=admin["id"]):
            raise HTTPException(404, "no such client")
        conn.commit()
    return {"ok": True}


# --- audit --------------------------------------------------------------------

@router.get("/audit")
def audit_log(request: Request, limit: int = 200):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        rows = conn.execute("SELECT * FROM audit ORDER BY id DESC LIMIT ?", (max(1, min(limit, 1000)),)).fetchall()
        return {"audit": [dict(r) for r in rows]}


# --- billing (docs/dev/billing.md) ----------------------------------------------
# The Billing tab: the subscription copies, and a re-read from Stripe. The
# plan select above stays the courtesy grant (accounts.set_plan).

@router.get("/subscriptions")
def list_subscriptions(request: Request, status: str = "", limit: int = 200):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        rows = conn.execute(
            "SELECT s.*, a.username, a.email, a.plan AS effective_plan FROM subscriptions s "
            "LEFT JOIN accounts a ON a.id = s.account_id WHERE (? = '' OR s.status = ?) "
            "ORDER BY s.updated_at DESC LIMIT ?", (status, status, max(1, min(limit, 1000)))).fetchall()
        # ``test_mode``: a test key's customers live under /test/ in Stripe's dashboard.
        return {"enabled": billing.enabled(), "test_mode": "_test_" in config.STRIPE_SECRET,
                "summary": billing.admin_summary(conn), "subscriptions": [dict(r) for r in rows]}


@router.get("/billing-events")
def list_billing_events(request: Request, limit: int = 50):
    """The newest webhook deliveries with what each did (``billing_events``)."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        rows = conn.execute(
            "SELECT e.id, e.type, e.account_id, e.outcome, e.received_at, a.username FROM billing_events e "
            "LEFT JOIN accounts a ON a.id = e.account_id ORDER BY e.received_at DESC, e.rowid DESC LIMIT ?",
            (max(1, min(limit, 500)),)).fetchall()
        return {"events": [dict(r) for r in rows]}


@router.post("/subscriptions/{account_id}/refresh")
def refresh_subscription(account_id: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        conn.commit()
        if not accounts.subscription(conn, account_id):
            raise HTTPException(404, "no subscription for that account")
    if not billing.enabled():
        raise Problem(503, "Billing is off.")
    try:
        billing.refresh(account_id)
    except billing.BillingError as e:
        raise Problem(502, f"Stripe did not answer: {e}") from e
    with closing(db.connect()) as conn:
        db.audit(conn, "billing.refresh", account_id, admin["id"])
        conn.commit()
        return {"subscription": dict(accounts.subscription(conn, account_id))}


# --- hosted servers and the fleet (docs/dev/hosted.md) ------------------------
# The machines and every container on them: the Machines tab. Hosted
# servers, their limits, environment, jobs and upgrades: the Servers tab.
# The actions that enqueue a job for a container (a container's own, logs,
# rollback, one server's upgrade, an orphan's removal) answer with that
# job, which the page polls at GET /jobs/{id}. An environment variable's
# value goes in and never comes back out: only names are answered.

class HostBody(BaseModel):
    name: str
    address: str = ""
    public_ip: str = ""


class HostPatch(BaseModel):
    name: str | None = None
    accepting: bool | None = None
    public_ip: str | None = None


class ContainerBody(BaseModel):
    lines: int | None = None


class ProvisionBody(BaseModel):
    account_id: str


class UpgradeBody(BaseModel):
    tag: str = ""
    wave_size: int = 1
    server_ids: list[str] | None = None
    outdated: bool = False


class ServerUpgradeBody(BaseModel):
    tag: str


class ApplyEnvBody(BaseModel):
    wave_size: int = 1
    server_ids: list[str] | None = None


@router.get("/hosts")
def list_hosts(request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"hosts": _trended(conn, "host", fleet.hosts(conn))}


@router.post("/hosts")
def add_host(body: HostBody, request: Request):
    """A new host; its agent token is in this answer only, with the line
    that installs the agent with it (and the host's own Caddy when it has a
    public IP)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        host, token = fleet.add_host(conn, body.name, body.address, actor=admin["id"], public_ip=body.public_ip)
        conn.commit()
    return {"host": host, "token": token, "bootstrap": fleet.bootstrap_command(token, bool(host["public_ip"]))}


@router.patch("/hosts/{host_id}")
def patch_host(host_id: str, body: HostPatch, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        host = fleet.update_host(conn, host_id, name=body.name, accepting=body.accepting, public_ip=body.public_ip,
                                 actor=admin["id"])
        conn.commit()
    return {"host": host}


@router.delete("/hosts/{host_id}")
def remove_host(host_id: str, request: Request):
    """Forget a host no server is on (409 otherwise); its unfinished and
    failed jobs are canceled. Nothing on the machine is touched."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        fleet.remove_host(conn, host_id, actor=admin["id"])
        conn.commit()
    return {"ok": True}


@router.post("/hosts/{host_id}/token")
def rotate_host_token(host_id: str, request: Request):
    """A new agent token for the host, in this answer only, with the line
    that installs the agent with it; the old one stops working."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        host, token = fleet.rotate_token(conn, host_id, actor=admin["id"])
        conn.commit()
    return {"host": host, "token": token, "bootstrap": fleet.bootstrap_command(token, bool(host["public_ip"]))}


@router.post("/hosts/{host_id}/orphans/{label}/remove")
def remove_orphan(host_id: str, label: str, request: Request):
    """A container on the host that no server row names: a delete job for
    it (container and data directory; the bucket is left alone)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        job = fleet.remove_orphan(conn, host_id, label, actor=admin["id"])
        conn.commit()
    return {"job": job}


@router.get("/machines")
def list_machines(request: Request):
    """The Machines tab: every host with its containers (``fleet.machines``),
    the host's and each container's ``trend``, and the default image."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        machines = _trended(conn, "host", fleet.machines(conn))
        trends = metrics.trends(conn, "container", TRENDS["container"], TREND_HOURS)
        for h in machines:
            for c in h["containers"]:
                c["trend"] = trends.get(f"{h['id']}:{c.get('name')}", [])
        return {"machines": machines, "default_image": fleet.default_image()}


@router.post("/hosts/{host_id}/update-all")
def update_all(host_id: str, request: Request):
    """A ``container_update`` for every container of the host with a newer
    image that is not a hosted server's, the agent's own last."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        jobs = fleet.update_all(conn, host_id, actor=admin["id"])
        conn.commit()
    return {"jobs": jobs}


CONTAINER_ACTIONS = ("restart", "start", "stop", "logs", "update", "rollback")


@router.post("/hosts/{host_id}/containers/{name}/{action}")
def container_action(host_id: str, name: str, action: str, request: Request, body: ContainerBody | None = None):
    """A job for one container of the host, by its name: ``restart``,
    ``start``, ``stop``, ``logs`` (``{lines}``, at most 5000), ``update`` or
    ``rollback`` (``fleet.container_job``)."""
    if action not in CONTAINER_ACTIONS:
        raise HTTPException(404, "no such action")
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        job = fleet.container_job(conn, host_id, name, f"container_{action}", admin["id"],
                                  lines=body.lines if body else None)
        conn.commit()
    return {"job": job}


@router.get("/servers")
def list_servers(request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"servers": _trended(conn, "server", hosted.servers(conn)), "default_image": fleet.default_image(),
                "auto_upgrade": settings.fleet_auto_upgrade()}


@router.post("/servers/provision")
def provision_server(body: ProvisionBody, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        server = hosted.provision(conn, body.account_id, admin["id"])
        conn.commit()
    return {"server": server}


@router.post("/servers/upgrade")
def upgrade_servers(body: UpgradeBody, request: Request):
    """Servers to ``tag`` in waves; with ``outdated``, exactly the outdated
    servers to the default tag (``tag`` and ``server_ids`` are not read)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        if body.outdated:
            run = fleet.upgrade_outdated(conn, body.wave_size, actor=admin["id"])
        else:
            run = fleet.upgrade(conn, body.tag, body.wave_size, body.server_ids, actor=admin["id"])
        conn.commit()
    return run


@router.post("/servers/apply-env")
def apply_env(body: ApplyEnvBody, request: Request):
    """An update run: running servers rebuilt with the extra environment as
    it is now, in waves."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        run = fleet.update_env(conn, body.wave_size, body.server_ids, actor=admin["id"])
        conn.commit()
    return run


@router.patch("/servers/{server_id}")
def patch_server(server_id: str, body: dict, request: Request):
    """``overrides``: the server's own limits over its plan's
    (``hosted.set_overrides``); ``env``: ``{set: {NAME: value}, unset:
    [NAME]}``, its own variables, applied now (``hosted.set_env``)."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        if not body or set(body) - {"overrides", "env"}:
            raise HTTPException(400, "send overrides, env or both")
        env = body.get("env", {})
        if not isinstance(env, dict):
            raise HTTPException(400, "env is {set, unset}")
        db.begin_write(conn)
        if "overrides" in body:
            server = hosted.set_overrides(conn, server_id, body["overrides"], admin["id"])
        if "env" in body:
            server = hosted.set_env(conn, server_id, env.get("set"), env.get("unset"), admin["id"])
        conn.commit()
    return {"server": server}


@router.get("/fleet-env")
def get_fleet_env(request: Request):
    """The names of the fleet's extra variables; their values stay here."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"names": sorted(settings.fleet_env(conn))}


@router.patch("/fleet-env")
def patch_fleet_env(body: dict, request: Request):
    """``{set: {NAME: value}, unset: [NAME]}``. Nothing that runs changes:
    a new server gets them, running ones with POST /servers/apply-env."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        try:
            names = settings.set_fleet_env(conn, body.get("set"), body.get("unset"), admin["id"])
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        conn.commit()
    return {"names": names}


# Before /servers/{server_id}/{action}, which would take these paths too.
@router.post("/servers/{server_id}/upgrade")
def upgrade_server(server_id: str, body: ServerUpgradeBody, request: Request):
    """This server alone to an image tag, outside any wave."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        job = fleet.upgrade_one(conn, server_id, body.tag, actor=admin["id"])
        conn.commit()
    return {"job": job}


def _server_job(server_id: str, kind: str, request: Request) -> dict:
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        job = hosted.admin_job(conn, server_id, kind, admin["id"])
        conn.commit()
    return {"job": job}


@router.post("/servers/{server_id}/logs")
def server_logs(server_id: str, request: Request):
    """A logs job: its result holds the container's last 200 lines."""
    return _server_job(server_id, "logs", request)


@router.post("/servers/{server_id}/rollback")
def server_rollback(server_id: str, request: Request):
    """A rollback job: back to the container a failed upgrade kept."""
    return _server_job(server_id, "rollback", request)


@router.post("/servers/{server_id}/{action}")
def server_action(server_id: str, action: str, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        server = hosted.admin_action(conn, server_id, action, admin["id"])
        conn.commit()
    return {"server": server}


@router.get("/jobs")
def list_jobs(request: Request, state: str = "", limit: int = 100):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"jobs": fleet.jobs(conn, state, limit)}


@router.get("/jobs/{job_id}")
def get_job(job_id: str, request: Request):
    """One job with its whole result (a logs job's lines)."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"job": fleet.job(conn, job_id)}


@router.post("/jobs/{job_id}/{action}")
def job_action(job_id: str, action: str, request: Request):
    """``retry`` a failed or canceled job, or ``cancel`` a queued, held or
    failed one (either lets a paused upgrade or update run go on, and lets
    automatic upgrades start again)."""
    if action not in ("retry", "cancel"):
        raise HTTPException(404, "no such action")
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        job = (fleet.retry if action == "retry" else fleet.cancel)(conn, job_id, admin["id"])
        conn.commit()
    return {"job": job}


# --- the Overview, the alerts and the history (docs/dev/hosted.md) -------------
# The Overview tab and the alert list bring the alerts up to date before they
# read them, so a job retried a moment ago is no longer listed; any that fall
# due then are mailed as the next heartbeat would have mailed them.

TRENDS = {"host": ("memory_used_mb", "disk_used_mb"), "server": ("memory_mb", "cpu_pct", "data_mb"),
          "container": ("memory_mb", "cpu_pct")}
TREND_HOURS = 48


def _trended(conn, kind: str, rows: list[dict]) -> list[dict]:
    """``rows`` (hosts or servers), each with ``trend``: its samples of the
    last TREND_HOURS hours in the few series the Servers and Machines tabs
    draw inline."""
    trends = metrics.trends(conn, kind, TRENDS[kind], TREND_HOURS)
    for row in rows:
        row["trend"] = trends.get(row["id"], [])
    return rows


def _sync_alerts(conn) -> None:
    db.begin_write(conn)
    due = alerts.sync(conn)
    conn.commit()
    alerts.notify(due)


def _counts(conn) -> dict:
    """The Overview's tiles: accounts, billing (``billing.admin_summary``),
    the fleet, and the settings that decide who gets in and what is sold."""
    row = conn.execute("SELECT SUM(deleted_at IS NULL), SUM(deleted_at IS NULL AND created_at >= ?), "
                       "SUM(deleted_at IS NULL AND email_verified_at IS NULL), SUM(deleted_at IS NOT NULL) "
                       "FROM accounts", (db.after(-7 * 86400),)).fetchone()
    hosts = fleet.hosts(conn)
    servers = conn.execute("SELECT * FROM hosted_servers WHERE state != 'deleted'").fetchall()
    by_state: dict[str, int] = {}
    for s in servers:
        by_state[s["state"]] = by_state.get(s["state"], 0) + 1
    jobs = dict(conn.execute("SELECT state, COUNT(*) FROM fleet_jobs GROUP BY state").fetchall())
    view = settings.admin_view()
    return {
        "accounts": dict(zip(("total", "new_7d", "unverified", "deleted"), (n or 0 for n in row))),
        "billing": {**billing.admin_summary(conn), "enabled": billing.enabled()},
        "fleet": {"hosts": {"fresh": sum(not h["stale"] for h in hosts), "stale": sum(h["stale"] for h in hosts)},
                  "servers": {"by_state": by_state, "outdated": sum(bool(fleet.outdated_why(s)) for s in servers)},
                  "jobs": {k: jobs.get(k, 0) for k in ("queued", "running", "failed")}},
        "settings": {**{k: view[k] for k in ("registration", "turnstile_on", "plans_on_sale", "alerts", "alert_email",
                                             "fleet_auto_upgrade")}, "fleet_image_tag": fleet.default_tag()},
    }


@router.get("/overview")
def overview(request: Request):
    """The Overview tab: the open alerts an admin has not dismissed, and
    the counts (``_counts``)."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        _sync_alerts(conn)
        return {"alerts": [a for a in alerts.listing(conn) if not a["dismissed_at"]], **_counts(conn)}


@router.get("/alerts")
def list_alerts(request: Request, resolved: bool = Query(False, alias="all")):
    """The open alerts, dismissed ones too; with ``?all=1``, also those
    resolved in the last 7 days."""
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        _sync_alerts(conn)
        return {"alerts": alerts.listing(conn, 7 if resolved else 0)}


@router.post("/alerts/test")
def test_alert(request: Request):
    """*Send test alert*: an alert mail to where alerts go, sent now."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        conn.commit()
    ratelimit.check(f"test-alert:{admin['id']}", 5, 600)
    try:
        to = alerts.send_test()
    except LookupError:
        raise Problem(409, "There is no address to send it to: set one, or confirm an admin's address.") from None
    except mail.MailError as e:
        raise Problem(502, f"The mail could not be sent: {e}") from e
    return {"ok": True, "to": to, "detail": f"Sent to {', '.join(to)} through the {config.MAIL_BACKEND} backend."}


@router.post("/alerts/{key}/dismiss")
def dismiss_alert(key: str, request: Request):
    """Hide an open alert until it resolves and comes back."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        db.begin_write(conn)
        try:
            alert = alerts.dismiss(conn, key, admin["id"])
        except LookupError:
            raise HTTPException(404, "no such open alert") from None
        conn.commit()
    return {"alert": alert}


@router.get("/metrics")
def get_metrics(request: Request, kind: str, ref: str, hours: int = 168):
    """A host's, server's or container's (``<host id>:<name>``) hourly
    samples (``metrics.series``) of the last ``hours``, at most
    ``metrics.MAX_HOURS``."""
    if kind not in metrics.KINDS:
        raise HTTPException(400, "kind is host, server or container")
    hours = max(1, min(hours, metrics.MAX_HOURS))
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"kind": kind, "ref": ref, "hours": hours, "points": metrics.series(conn, kind, ref, hours)}

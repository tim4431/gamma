"""The admin API under ``/api/admin``: accounts, invites, OIDC clients,
the server settings with the plans on sale, the environment's configuration
(read-only) and a test mail, the audit log, the fleet's hosts, hosted servers and
jobs. Only an account with ``is_admin`` (set
with ``manage.py set-admin``) and only through a portal session — never a
bearer token from a Gamma server."""

from contextlib import closing

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel

from .. import accounts, billing, config, db, fleet, hosted, mail, oidc, ratelimit, settings
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
# Hosts, hosted servers, their limits, environment, jobs and upgrades: the
# Servers tab. The actions that enqueue a job for the container (logs,
# rollback, one server's upgrade, an orphan's removal) answer with that
# job, which the page polls at GET /jobs/{id}. An environment variable's
# value goes in and never comes back out: only names are answered.

class HostBody(BaseModel):
    name: str
    address: str = ""


class HostPatch(BaseModel):
    name: str | None = None
    accepting: bool | None = None


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
        return {"hosts": fleet.hosts(conn)}


@router.post("/hosts")
def add_host(body: HostBody, request: Request):
    """A new host; its agent token is in this answer only."""
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        host, token = fleet.add_host(conn, body.name, body.address, actor=admin["id"])
        conn.commit()
    return {"host": host, "token": token}


@router.patch("/hosts/{host_id}")
def patch_host(host_id: str, body: HostPatch, request: Request):
    with closing(db.connect()) as conn:
        admin = require_admin(conn, request)
        host = fleet.update_host(conn, host_id, name=body.name, accepting=body.accepting, actor=admin["id"])
        conn.commit()
    return {"host": host}


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


@router.get("/servers")
def list_servers(request: Request):
    with closing(db.connect()) as conn:
        require_admin(conn, request)
        return {"servers": hosted.servers(conn), "default_image": fleet.default_image(),
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

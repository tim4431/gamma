"""Hosted servers: the container's sync, plan_changed, the hourly lifecycle
and the plan page's status (docs/dev/hosted.md)."""

import base64
import json
from contextlib import closing

import pytest
from conftest import register, verify

from gammacloud import accounts, config, db, fleet, hosted, mail, oidc

DOMAIN = "gammapdf.test"


# The container machinery (placement, limits, resizes, the lifecycle) is
# tested against three sizes of container plan, as the plans were before Lite
# and Plus moved to the shared server: a change between container plans
# relimits and resizes the same way whatever the table says. What the real
# table does is at the end of this file.
CONTAINER_PLANS = {
    "free": {},
    "lite": {"hosted": True, "quota_mb": 1024, "max_upload_mb": 50, "max_accounts": 1,
             "policy": "refuse", "offsite_interval_s": 86400, "offsite_keep": 3, "memory_mb": 512, "cpus": 1.0},
    "plus": {"hosted": True, "quota_mb": 6 * 1024, "max_upload_mb": 100, "max_accounts": 1,
             "policy": "refuse", "offsite_interval_s": 86400, "offsite_keep": 7, "memory_mb": 768, "cpus": 1.0},
    "pro": config.PLAN_LIMITS["pro"],
}


@pytest.fixture
def hosting(monkeypatch):
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)
    monkeypatch.setattr(config, "PLAN_LIMITS", CONTAINER_PLANS)


# --- helpers (test_fleet.py uses them too) --------------------------------------

def make_account(username="alice", plan="plus", granted=None):
    """An account on ``plan`` (granted unless ``granted`` says otherwise),
    told to the hosted side the way billing does."""
    with closing(db.connect()) as conn:
        a = accounts.create(conn, email=f"{username}@example.org", username=username, password=None, verified=True)
        conn.execute("UPDATE accounts SET plan = ?, granted_plan = ? WHERE id = ?",
                     (plan, plan if granted is None else granted, a["id"]))
        hosted.plan_changed(conn, a["id"])
        conn.commit()
    return a["id"]


def set_plan(account_id, plan, granted=None, status=None, past_due_since=None, ended_at=None):
    """Change the account's effective plan and/or subscription row, then
    ``plan_changed``."""
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET plan = ? WHERE id = ?", (plan, account_id))
        if granted is not None:
            conn.execute("UPDATE accounts SET granted_plan = ? WHERE id = ?", (granted, account_id))
        if status is not None:
            conn.execute("INSERT INTO subscriptions (account_id, plan, status, past_due_since, ended_at, updated_at) "
                         "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT (account_id) DO UPDATE SET plan = excluded.plan, "
                         "status = excluded.status, past_due_since = excluded.past_due_since, "
                         "ended_at = excluded.ended_at", (account_id, plan if plan != "free" else "plus", status,
                                                          past_due_since, ended_at, db.now()))
        hosted.plan_changed(conn, account_id)
        conn.commit()


def make_host(name="vps-1", memory_mb=8192, disk_mb=500_000, memory_used_mb=1024, disk_used_mb=10_000):
    with closing(db.connect()) as conn:
        host, token = fleet.add_host(conn, name, actor="test")
        fleet.heartbeat(conn, dict(host), {"agent_version": "1", "memory_mb": memory_mb, "disk_mb": disk_mb,
                                           "memory_used_mb": memory_used_mb, "disk_used_mb": disk_used_mb})
        conn.commit()
    return host["id"], token


def server(account_id):
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT * FROM hosted_servers WHERE account_id = ?", (account_id,)).fetchone()
        return dict(row) if row else None


def jobs_of(account_id):
    with closing(db.connect()) as conn:
        return [dict(r) for r in conn.execute(
            "SELECT j.* FROM fleet_jobs j JOIN hosted_servers s ON s.id = j.server_id WHERE s.account_id = ? "
            "ORDER BY j.created_at, j.rowid", (account_id,)).fetchall()]


def tick():
    with closing(db.connect()) as conn:
        hosted.tick(conn)
        conn.commit()


def backdate(account_id, column, days):
    with closing(db.connect()) as conn:
        if column == "past_due_since":
            conn.execute("UPDATE subscriptions SET past_due_since = ? WHERE account_id = ?",
                         (db.after(-days * 86400), account_id))
        else:
            conn.execute(f"UPDATE hosted_servers SET {column} = ? WHERE account_id = ?",
                         (db.after(-days * 86400), account_id))
        conn.commit()


def credentials(account_id):
    """The container's client id and secret, from its create job's payload."""
    job = next(j for j in jobs_of(account_id) if j["kind"] == "create")
    env = json.loads(job["payload"])["env"]
    return env["GAMMA_CLOUD_CLIENT_ID"], env["GAMMA_CLOUD_CLIENT_SECRET"]


def basic(client_id, secret):
    return {"Authorization": "Basic " + base64.b64encode(f"{client_id}:{secret}".encode()).decode()}


REPORT = {"version": "1.2.3", "schema": 41, "accounts": 1, "uploads_bytes": 5 << 20, "data_bytes": 9 << 20,
          "public_url": f"https://alice-user.{DOMAIN}"}


def subjects(subject):
    return [m for m in mail.outbox if m["subject"] == subject]


# --- sync ---------------------------------------------------------------------

def test_sync_needs_the_containers_own_client(client, hosting):
    make_host()
    alice = make_account("alice", "plus")
    client_id, secret = credentials(alice)
    assert client.post("/api/hosted/sync", json=REPORT).status_code == 401
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic("gc_nobody", "x")).status_code == 401
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, "wrong")).status_code == 401
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic(config.DESKTOP_CLIENT_ID, "")).status_code == 401
    # a confidential client that is not a hosted server's
    with closing(db.connect()) as conn:
        other_id, other_secret = oidc.create_client(
            conn, name="share", kind="share-host", redirect_uris=["https://share.example/api/auth/cloud/callback"])
        conn.commit()
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic(other_id, other_secret)).status_code == 404

    r = client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, secret))
    assert r.status_code == 200, r.text
    assert r.json() == {"plan": "plus", "status": "active", "read_only": False, "policy": "refuse",
                        "max_accounts": 1, "quota_mb": 6144, "max_upload_mb": 100, "memory_mb": 768, "cpus": 1.0,
                        "offsite": {"interval_s": 86400, "keep": 7}, "grace_until": None, "message": ""}
    row = server(alice)
    assert json.loads(row["limits"]) == r.json()          # what is stored is exactly the answer
    report = json.loads(row["report"])
    assert report["version"] == "1.2.3" and report["schema"] == 41 and report["data_bytes"] == 9 << 20
    assert row["synced_at"] and row["reported_at"]


def test_sync_answers_pro_limits_and_is_rate_limited(client, hosting, monkeypatch):
    make_host()
    alice = make_account("alice", "plus")
    client_id, secret = credentials(alice)
    set_plan(alice, "pro", granted="pro")
    r = client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, secret))
    assert r.json()["plan"] == "pro" and r.json()["policy"] == "invited" and r.json()["max_accounts"] == 10
    assert r.json()["quota_mb"] == 102400 and r.json()["offsite"] == {"interval_s": 3600, "keep": 30}
    from gammacloud.routers import hosted as hosted_router
    monkeypatch.setattr(hosted_router, "SYNCS_PER_HOUR", 2)
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, secret)).status_code == 200
    assert client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, secret)).status_code == 429


def test_grace_answer_and_message(client, hosting):
    make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    assert server(alice)["state"] == "provisioning"
    client_id, secret = credentials(alice)
    set_plan(alice, "plus", status="past_due", past_due_since=db.now())
    row = server(alice)
    assert row["state"] == "provisioning" and json.loads(row["limits"])["status"] == "grace"
    _running(alice)
    set_plan(alice, "plus", status="past_due", past_due_since=db.now())
    row = server(alice)
    assert row["state"] == "grace" and not row["read_only"]
    answer = client.post("/api/hosted/sync", json=REPORT, headers=basic(client_id, secret)).json()
    assert answer["status"] == "grace" and answer["read_only"] is False
    assert answer["grace_until"] > db.after(6 * 86400) and answer["grace_until"] < db.after(8 * 86400)
    assert answer["message"].startswith("Payment failed; this server becomes read-only on ")
    assert "/plan" in answer["message"]


def test_sync_keeps_the_usage_counts_and_nothing_else(client, hosting):
    make_host()
    alice = make_account("alice", "plus")
    auth = basic(*credentials(alice))
    body = {**REPORT, "active_accounts": 2, "last_write_at": "2026-10-05T08:00:00.000Z", "errors": 3, "uptime_s": 7200,
            "titles": ["a page of the library"]}
    assert client.post("/api/hosted/sync", json=body, headers=auth).status_code == 200
    report = json.loads(server(alice)["report"])
    assert (report["active_accounts"], report["last_write_at"], report["errors"], report["uptime_s"]) == (
        2, "2026-10-05T08:00:00.000Z", 3, 7200)
    assert "titles" not in report
    junk = {**REPORT, "active_accounts": -4, "last_write_at": "", "errors": "many", "uptime_s": None}
    client.post("/api/hosted/sync", json=junk, headers=auth)
    report = json.loads(server(alice)["report"])
    assert (report["active_accounts"], report["last_write_at"], report["errors"], report["uptime_s"]) == (0, None, 0, 0)


# --- plan_changed -------------------------------------------------------------

def test_nothing_is_created_while_hosting_is_off(client):
    alice = make_account("alice", "plus")
    assert server(alice) is None


def test_a_plus_grant_creates_a_server_and_its_create_job(client, hosting):
    alice = make_account("alice", "free")
    assert server(alice) is None
    set_plan(alice, "plus", granted="plus")
    row = server(alice)
    assert row["label"] == "alice" and row["state"] == "provisioning" and not row["host_id"]
    assert "waiting for a host" in json.loads(row["report"])["note"]
    assert jobs_of(alice) == []
    host_id, _ = make_host()
    tick()                                                 # placement is retried hourly
    row = server(alice)
    assert row["host_id"] == host_id and row["client_id"].startswith("gc_")
    [job] = jobs_of(alice)
    payload = json.loads(job["payload"])
    assert job["kind"] == "create" and job["state"] == "queued"
    assert payload["label"] == "alice" and payload["image"] == f"{config.FLEET_IMAGE}:{config.FLEET_IMAGE_TAG}"
    assert (payload["memory_mb"], payload["cpus"]) == (768, 1.0)          # sized for the plan
    env = payload["env"]
    assert env["GAMMA_HOSTED"] == "1" and env["GAMMA_CLOUD_ISSUER"] == config.PUBLIC_URL
    assert env["GAMMA_CLOUD_ADMIN_SUBJECT"] == alice and env["GAMMA_CLOUD_POLICY"] == "refuse"
    assert env["GAMMA_GUEST_MAX"] == "0"                                    # no guests on a paid server
    assert env["GAMMA_PUBLIC_URL"] == f"https://alice-user.{DOMAIN}"
    with closing(db.connect()) as conn:
        c = conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (row["client_id"],)).fetchone()
    assert c["kind"] == "container" and json.loads(c["redirect_uris"]) == [f"https://alice-user.{DOMAIN}/api/auth/cloud/callback"]
    assert c["secret_hash"] == db.token_hash(env["GAMMA_CLOUD_CLIENT_SECRET"])
    set_plan(alice, "plus")                                # again: nothing new
    assert len(jobs_of(alice)) == 1


def test_pro_relimits_and_cancel_turns_read_only(client, hosting):
    make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    set_plan(alice, "pro", status="active")
    limits = json.loads(server(alice)["limits"])
    assert limits["plan"] == "pro" and limits["policy"] == "invited" and limits["max_upload_mb"] == 250
    n = len(mail.outbox)
    set_plan(alice, "free", status="canceled", ended_at=db.now())
    row = server(alice)
    assert row["state"] == "read_only" and row["read_only"] == 1
    limits = json.loads(row["limits"])
    assert limits["status"] == "read_only" and limits["read_only"] is True and limits["plan"] == "pro"
    assert "read-only" in limits["message"]
    assert [m["subject"] for m in mail.outbox[n:]] == ["Your Gamma server is read-only"]
    with closing(db.connect()) as conn:
        events = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'hosted.state'").fetchall()]
    assert any("-> read_only" in e for e in events)
    set_plan(alice, "plus", status="active")               # resubscribed before its container was made
    assert server(alice)["state"] == "provisioning"
    _running(alice)
    set_plan(alice, "free", status="canceled", ended_at=db.now())
    set_plan(alice, "plus", status="active")
    row = server(alice)
    assert row["state"] == "running" and row["read_only"] == 0


def test_a_courtesy_grant_never_lapses(client, hosting):
    make_host()
    alice = make_account("alice", "plus")
    set_plan(alice, "plus", status="canceled", ended_at=db.now())
    assert server(alice)["state"] == "provisioning"
    set_plan(alice, "plus", status="past_due", past_due_since=db.after(-30 * 86400))
    tick()
    assert server(alice)["state"] == "provisioning" and json.loads(server(alice)["limits"])["status"] == "active"


# --- the lifecycle ------------------------------------------------------------

def _running(alice):
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosted_servers SET state = 'running' WHERE account_id = ?", (alice,))
        conn.execute("UPDATE fleet_jobs SET state = 'done'")
        conn.commit()


def test_tick_grace_read_only_stopped_deleted(client, hosting):
    make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    _running(alice)
    client_id, _ = credentials(alice)
    set_plan(alice, "plus", status="past_due", past_due_since=db.now())
    assert server(alice)["state"] == "grace"
    tick()
    assert server(alice)["state"] == "grace"
    backdate(alice, "past_due_since", config.GRACE_DAYS + 0.1)
    tick()
    row = server(alice)
    assert row["state"] == "read_only" and row["read_only"] == 1
    assert len(subjects("Your Gamma server is read-only")) == 1
    assert "Your plan ended" in mail.outbox[-1]["body"]

    tick()                                                 # nothing more yet
    assert server(alice)["state"] == "read_only"

    def retires():
        with closing(db.connect()) as conn:
            st = hosted.status_for(conn, alice)
        return st["stops_at"], st["deletes_at"]
    stops, gone = retires()                                # the Plan page's dates are the lifecycle's own
    assert stops[:10] == db.after(config.READ_ONLY_DAYS * 86400)[:10]
    assert gone[:10] == db.after(config.DELETE_DAYS * 86400)[:10]
    backdate(alice, "state_changed_at", config.READ_ONLY_DAYS + 0.1)
    tick()
    row = server(alice)
    assert row["state"] == "stopped" and json.loads(row["limits"])["status"] == "stopped"
    stops, gone = retires()
    assert stops is None and gone[:10] == db.after((config.DELETE_DAYS - config.READ_ONLY_DAYS) * 86400)[:10]
    assert [j["kind"] for j in jobs_of(alice) if j["state"] == "queued"] == ["stop"]
    assert len(subjects("Your Gamma server is stopped")) == 1

    backdate(alice, "state_changed_at", config.DELETE_DAYS - config.READ_ONLY_DAYS - 3)
    tick()
    tick()
    assert len(subjects("Your Gamma server will be deleted in a week")) == 1
    assert server(alice)["state"] == "stopped"

    backdate(alice, "state_changed_at", config.DELETE_DAYS - config.READ_ONLY_DAYS + 0.1)
    tick()
    row = server(alice)
    assert row["state"] == "deleted" and row["deleted_at"]
    delete = [j for j in jobs_of(alice) if j["kind"] == "delete"]
    assert len(delete) == 1 and json.loads(delete[0]["payload"]) == {"label": "alice", "account_id": alice}
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT 1 FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone() is None
    tick()
    assert len([j for j in jobs_of(alice) if j["kind"] == "delete"]) == 1


def test_resuming_a_stopped_server_starts_it(client, hosting):
    make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    _running(alice)
    set_plan(alice, "free", status="canceled", ended_at=db.now())
    backdate(alice, "state_changed_at", config.READ_ONLY_DAYS + 0.1)
    tick()
    assert server(alice)["state"] == "stopped"
    set_plan(alice, "plus", status="active")
    row = server(alice)
    assert row["state"] == "running" and row["read_only"] == 0
    assert jobs_of(alice)[-1]["kind"] == "start"


def test_me_lists_the_hosted_server_until_it_is_deleted(client, hosting):
    from gammacloud import servers
    make_host()
    account = register(client, "alice")
    verify(client)
    assert client.get("/api/me").json()["servers"] == []
    set_plan(account["id"], "plus", granted="plus")
    url = f"https://alice-user.{DOMAIN}"
    [s] = client.get("/api/me").json()["servers"]
    assert s["url"] == url and s["kind"] == "hosted" and s["hosted"] is True and s["name"] == servers.HOSTED_NAME
    assert s["local"] is False and s["version"] == "" and s["schema"] is None
    # the container's first sync fills in its build; its own registration is the same entry
    client_id, secret = credentials(account["id"])
    client.post("/api/hosted/sync", json={**REPORT, "public_url": url}, headers=basic(client_id, secret))
    with closing(db.connect()) as conn:
        servers.link(conn, account["id"], url, "alice's Gamma", grant_id="g1")
        conn.commit()
        [entry] = servers.of_account(conn, account["id"], grants=True)
        assert entry["kind"] == "hosted" and entry["grant_id"] == "g1"
        [merged] = servers.merge([], servers.of_account(conn, account["id"], grants=True))
        assert merged["server"]["hosted"] and merged["grant"] is None          # kept with no live grant
    [s] = client.get("/api/me").json()["servers"]
    assert s["version"] == "1.2.3" and s["schema"] == 41 and s["last_seen_at"] >= server(account["id"])["synced_at"]
    # unlinking (the container's DELETE /api/me/servers) or Remove on the portal leaves it listed
    assert client.post("/api/servers/remove", json={"url": url}).status_code == 200      # the linked twin
    assert client.post("/api/servers/remove", json={"url": url}).status_code == 404
    assert [s["kind"] for s in client.get("/api/me").json()["servers"]] == ["hosted"]
    assert "Your hosted Gamma" in client.get("/devices").text
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, server(account["id"])["id"], "delete", "test")
        conn.commit()
    assert client.get("/api/me").json()["servers"] == []


def test_a_deleted_account_lapses_and_a_purge_tears_down(client, hosting):
    make_host()
    alice = make_account("alice", "plus")
    _running(alice)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET deleted_at = ? WHERE id = ?", (db.now(), alice))
        hosted.plan_changed(conn, alice)
        conn.commit()
    assert server(alice)["state"] == "read_only"
    with closing(db.connect()) as conn:
        hosted.purge_account(conn, alice, "test")
        conn.execute("DELETE FROM accounts WHERE id = ?", (alice,))   # the foreign key no longer holds it
        conn.commit()
        [job] = conn.execute("SELECT * FROM fleet_jobs WHERE kind = 'delete'").fetchall()
    assert server(alice) is None and json.loads(job["payload"])["label"] == "alice"


def test_a_taken_label_is_never_reused(client, hosting):
    """A renamed owner's live server keeps its label; a newcomer with that
    username gets a label of its own, and its create job names only that."""
    make_host()
    first = make_account("alice", "plus")
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET username = 'alice2', email = 'alice2@example.org', email_canon = 'alice2@example.org' WHERE id = ?", (first,))
        conn.commit()
    newcomer = make_account("alice", "plus")
    old, new = server(first), server(newcomer)
    assert old["label"] == "alice" and new["label"].startswith("alice-") and new["label"] != "alice"
    payload = json.loads(next(j for j in jobs_of(newcomer) if j["kind"] == "create")["payload"])
    assert payload["label"] == payload["data_dir"] == new["label"]
    assert payload["env"]["GAMMA_PUBLIC_URL"] == f"https://{new['label']}-user.{DOMAIN}"
    # a deleted row (its delete job may still be pending) keeps its label too
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, old["id"], "delete", "test")
        third = accounts.create(conn, email="x@example.org", username="bob", password=None, verified=True)
        conn.execute("UPDATE accounts SET username = 'alice3' WHERE id = ?", (newcomer,))
        conn.execute("UPDATE accounts SET username = 'alice', plan = 'plus', granted_plan = 'plus' WHERE id = ?",
                     (third["id"],))
        hosted.plan_changed(conn, third["id"])
        conn.commit()
    assert server(first)["label"] == "alice" and server(newcomer)["label"] == new["label"]
    assert server(third["id"])["label"] not in ("alice", new["label"])


def test_an_unconfirmed_address_gets_no_server(client, hosting):
    make_host()
    with closing(db.connect()) as conn:
        a = accounts.create(conn, email="eve@example.org", username="eve", password=None)
        conn.execute("UPDATE accounts SET plan = 'pro', granted_plan = 'pro' WHERE id = ?", (a["id"],))
        hosted.plan_changed(conn, a["id"])
        with pytest.raises(accounts.Problem):
            hosted.provision(conn, a["id"], "test")
        conn.commit()
    assert server(a["id"]) is None and not subjects("Your Gamma is ready")
    with closing(db.connect()) as conn:                       # what the verify path does
        accounts.mark_verified(conn, a["id"])
        hosted.plan_changed(conn, a["id"])
        conn.commit()
    assert server(a["id"])["state"] == "provisioning"


def test_a_deleted_server_comes_back_fresh(client, hosting):
    make_host()
    alice = make_account("alice", "plus")
    first = server(alice)
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, first["id"], "delete", "test")
        conn.commit()
    assert server(alice)["state"] == "deleted"
    set_plan(alice, "plus")
    row = server(alice)
    assert row["id"] == first["id"] and row["state"] == "provisioning" and row["deleted_at"] is None
    assert row["client_id"] != first["client_id"]


# --- a server's own limits and environment ---------------------------------------

def overrides(server_id, values):
    with closing(db.connect()) as conn:
        view = hosted.set_overrides(conn, server_id, values, "admin")
        conn.commit()
    return view


def test_a_servers_own_limits_go_over_its_plan(client, hosting):
    host_id, _ = make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    _running(alice)
    sid = server(alice)["id"]
    with closing(db.connect()) as conn:
        for bad in ({"disk_mb": 1}, {"quota_mb": 0}, {"quota_mb": 1.5}, {"memory_mb": True}, {"max_accounts": "3"},
                    {"cpus": 0.1}, {"cpus": 65}, {"cpus": float("nan")}, ["quota_mb"]):
            with pytest.raises(accounts.Problem):
                hosted.set_overrides(conn, sid, bad, "admin")
    view = overrides(sid, {"quota_mb": 20480, "max_accounts": 3, "memory_mb": 1024, "cpus": 1.5})
    assert view["overrides"] == {"quota_mb": 20480, "max_accounts": 3, "memory_mb": 1024, "cpus": 1.5}
    assert view["plan_limits"] == {"quota_mb": 6144, "max_upload_mb": 100, "max_accounts": 1, "memory_mb": 768,
                                   "cpus": 1.0}
    assert (view["quota_mb"], view["memory_mb"], view["cpus"]) == (20480, 1024, 1.5)
    limits = json.loads(server(alice)["limits"])
    assert [limits[k] for k in hosted.OVERRIDES] == [20480, 100, 3, 1024, 1.5]
    [resize] = [j for j in jobs_of(alice) if j["kind"] == "upgrade"]          # the new size, in place
    assert json.loads(resize["payload"]) == {"label": "alice", "memory_mb": 1024, "cpus": 1.5}
    with closing(db.connect()) as conn:
        assert {h["id"]: h for h in fleet.hosts(conn)}[host_id]["committed_mb"] == 1024
        [detail] = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'hosted.override'")]
    assert detail == "alice quota_mb=20480, max_accounts=3, memory_mb=1024, cpus=1.5"

    # a plan change keeps them; the plan's other numbers follow it, and the size did not move
    set_plan(alice, "pro", status="active")
    limits = json.loads(server(alice)["limits"])
    assert (limits["plan"], limits["quota_mb"], limits["max_upload_mb"], limits["memory_mb"]) == ("pro", 20480, 250, 1024)
    assert len([j for j in jobs_of(alice) if j["kind"] == "upgrade"]) == 1
    # so does a lapse, and the operator still resizes a lapsed server
    set_plan(alice, "free", status="canceled", ended_at=db.now())
    assert server(alice)["state"] == "read_only" and json.loads(server(alice)["limits"])["memory_mb"] == 1024
    view = overrides(sid, {"memory_mb": 2048, "quota_mb": None})
    assert view["overrides"] == {"max_accounts": 3, "memory_mb": 2048, "cpus": 1.5} and view["quota_mb"] == 102400
    [resize] = [j for j in jobs_of(alice) if j["kind"] == "upgrade"]          # the queued resize takes the new size
    assert json.loads(resize["payload"])["memory_mb"] == 2048
    view = overrides(sid, {k: None for k in hosted.OVERRIDES})
    assert view["overrides"] == {} and (view["memory_mb"], view["cpus"]) == (1536, 2.0)
    assert overrides(sid, {})["overrides"] == {}


def test_a_waiting_server_is_placed_and_made_by_its_own_numbers(client, hosting):
    alice = make_account("alice", "plus")
    sid = server(alice)["id"]
    overrides(sid, {"memory_mb": 4096})
    make_host(memory_mb=4096)                                              # 3072 MB to place: too little
    tick()
    assert server(alice)["host_id"] == ""
    overrides(sid, {"memory_mb": 2048, "cpus": 3})
    tick()
    [job] = jobs_of(alice)
    payload = json.loads(job["payload"])
    assert server(alice)["host_id"] and (payload["memory_mb"], payload["cpus"]) == (2048, 3.0)
    # a deleted server brought back starts fresh
    with closing(db.connect()) as conn:
        hosted.set_env(conn, sid, {"FEATURE": "on"}, None, "admin")
        hosted.admin_action(conn, sid, "delete", "admin")
        conn.commit()
    set_plan(alice, "plus")
    row = server(alice)
    assert row["state"] == "provisioning" and row["overrides"] == "{}" and row["env"] == "{}"


def test_extra_variables_are_checked_and_audited_by_name(client):
    from gammacloud import settings
    with closing(db.connect()) as conn:
        for values, unset in (({"lower": "x"}, None), ({"_A": "x"}, None), ({"A" * 65: "x"}, None),
                              ({"GAMMA_HOSTED": "0"}, None), ({"GAMMA_PUBLIC_URL": "x"}, None),
                              ({"GAMMA_GUEST_MAX": "9"}, None), ({"FORWARDED_ALLOW_IPS": "*"}, None),
                              ({"GAMMA_CLOUD_ISSUER": "x"}, None), ({"GAMMA_S3_BUCKET": "x"}, None),
                              ({"A": "two\nlines"}, None), ({"A": "nul\0"}, None), ({"A": "x" * 4001}, None),
                              ({"A": 1}, None), (["A"], None), (None, "A"), (None, ["lower"])):
            with pytest.raises(ValueError):
                settings.set_fleet_env(conn, values, unset, "admin")
        assert settings.set_fleet_env(conn, {"SMTP_HOST": "mail.example", "B": ""}, None, "admin") == ["B", "SMTP_HOST"]
        assert settings.set_fleet_env(conn, {"C": "x" * 4000}, ["B"], "admin") == ["C", "SMTP_HOST"]
        assert settings.set_fleet_env(conn, None, ["NOT_SET"], "admin") == ["C", "SMTP_HOST"]   # no change, no row
        conn.commit()
        details = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'settings.fleet_env' ORDER BY id")]
    assert details == ["set B,SMTP_HOST", "set C unset B"]
    assert settings.fleet_env() == {"SMTP_HOST": "mail.example", "C": "x" * 4000}
    with pytest.raises(ValueError):                                         # not a key the Settings tab writes
        settings.clean(settings.FLEET_ENV_KEY, "{}")


# --- the plan page ------------------------------------------------------------

def test_status_for_and_the_status_endpoint(client, hosting):
    make_host("vps-1")
    assert client.get("/api/hosted/status").status_code == 401
    account = register(client, "alice")
    verify(client)
    with closing(db.connect()) as conn:
        assert hosted.status_for(conn, account["id"]) is None
    assert client.get("/api/hosted/status").json() == {"server": None}
    set_plan(account["id"], "pro", granted="pro")
    d = client.get("/api/hosted/status").json()["server"]
    assert set(d) == {"id", "label", "url", "state", "read_only", "limits", "report", "reported_at", "synced_at",
                      "stops_at", "deletes_at", "host"}
    assert d["stops_at"] is None and d["deletes_at"] is None        # only a lapsed server has them
    assert d["url"] == f"https://alice-user.{DOMAIN}" and d["state"] == "provisioning" and d["host"] == "vps-1"
    assert d["limits"]["plan"] == "pro" and d["read_only"] is False


# --- the real plans: Lite and Plus live on the shared server ----------------------

def test_lite_and_plus_have_no_container_and_carry_their_storage_as_a_claim(client, monkeypatch):
    from gammacloud import oidc
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)
    make_host()
    lite, plus, pro = make_account("lena", "lite"), make_account("paul", "plus"), make_account("petra", "pro")
    assert server(lite) is None and server(plus) is None
    assert server(pro)["state"] == "provisioning" and json.loads(server(pro)["limits"])["plan"] == "pro"
    # what the shared server reads: the plan's storage, none for a free account, Plus's for Pro
    with closing(db.connect()) as conn:
        claims = {name: oidc.claims_for(accounts.by_id(conn, a), "openid") for name, a in
                  (("lite", lite), ("plus", plus), ("pro", pro))}
        free = accounts.create(conn, email="fred@example.org", username="fred", password=None, verified=True)
        assert "limits" not in oidc.claims_for(accounts.by_id(conn, free["id"]), "openid")
    assert claims["lite"]["limits"] == {"quota_mb": 1024, "max_upload_mb": 50}
    assert claims["plus"]["limits"] == claims["pro"]["limits"] == {"quota_mb": 6144, "max_upload_mb": 100}
    # the admin cannot provision a container for a plan without one
    with closing(db.connect()) as conn:
        with pytest.raises(accounts.Problem):
            hosted.provision(conn, plus, "admin")


def test_a_server_whose_plan_moved_to_the_shared_server_lapses_as_it_is(client, monkeypatch):
    """Down from Pro, or a server made while Lite and Plus had containers:
    it turns read-only under Pro's limits, keeps its size, and no new one is
    made; the lifecycle (or the admin's Delete) retires it."""
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)
    monkeypatch.setattr(config, "PLAN_LIMITS", CONTAINER_PLANS)
    make_host()
    alice = make_account("alice", "lite")
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET state = 'done' WHERE kind = 'create'")
        conn.execute("UPDATE hosted_servers SET state = 'running' WHERE account_id = ?", (alice,))
        conn.commit()
    assert json.loads(server(alice)["limits"])["memory_mb"] == 512
    monkeypatch.undo()                                    # the real table again: Lite has no container
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)
    set_plan(alice, "lite")
    row = server(alice)
    limits = json.loads(row["limits"])
    assert row["state"] == "read_only" and limits["plan"] == "pro" and limits["read_only"] is True
    body = mail.outbox[-1]["body"]                        # the mail says why: the plan is paid, it has no server
    assert "Your Lite plan is active" in body and "Your plan ended" not in body and "import it at" in body
    assert [j["kind"] for j in jobs_of(alice) if j["state"] == "queued"] == []   # not resized
    tick()
    assert server(alice)["state"] == "read_only"
    with closing(db.connect()) as conn:                   # and the admin may delete it at once
        hosted.admin_action(conn, row["id"], "delete", "admin")
        conn.commit()
    assert server(alice)["state"] == "deleted"
    set_plan(alice, "plus")
    assert server(alice)["state"] == "deleted"            # a shared plan brings no server back


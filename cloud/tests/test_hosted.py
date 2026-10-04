"""Hosted servers: the container's sync, plan_changed, the hourly lifecycle
and the plan page's status (docs/dev/hosted.md)."""

import base64
import json
from contextlib import closing

import pytest
from conftest import register, verify

from gammacloud import accounts, config, db, fleet, hosted, mail, oidc

DOMAIN = "gammapdf.test"


@pytest.fixture
def hosting(monkeypatch):
    monkeypatch.setattr(config, "HOSTED_DOMAIN", DOMAIN)


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
          "public_url": f"https://alice.{DOMAIN}"}


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
    assert env["GAMMA_PUBLIC_URL"] == f"https://alice.{DOMAIN}"
    with closing(db.connect()) as conn:
        c = conn.execute("SELECT * FROM oauth_clients WHERE client_id = ?", (row["client_id"],)).fetchone()
    assert c["kind"] == "container" and json.loads(c["redirect_uris"]) == [f"https://alice.{DOMAIN}/api/auth/cloud/callback"]
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

    tick()                                                 # nothing more yet
    assert server(alice)["state"] == "read_only"
    backdate(alice, "state_changed_at", config.READ_ONLY_DAYS + 0.1)
    tick()
    row = server(alice)
    assert row["state"] == "stopped" and json.loads(row["limits"])["status"] == "stopped"
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
    url = f"https://alice.{DOMAIN}"
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
    assert payload["env"]["GAMMA_PUBLIC_URL"] == f"https://{new['label']}.{DOMAIN}"
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
    assert set(d) == {"id", "label", "url", "state", "read_only", "limits", "report", "reported_at", "synced_at", "host"}
    assert d["url"] == f"https://alice.{DOMAIN}" and d["state"] == "provisioning" and d["host"] == "vps-1"
    assert d["limits"]["plan"] == "pro" and d["read_only"] is False

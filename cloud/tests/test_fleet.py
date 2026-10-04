"""The fleet: host tokens, the job queue an agent works through, the
heartbeat, upgrade waves, placement, and the admin's Servers tab API and
CLI (docs/dev/hosted.md)."""

import json
import threading
import time
from contextlib import closing

import pytest
from conftest import make_admin, register
from test_hosted import DOMAIN, hosting, jobs_of, make_account, make_host, server, tick  # noqa: F401

import manage
from gammacloud import config, db, fleet, mail


def bearer(token):
    return {"Authorization": f"Bearer {token}"}


def next_job(client, token, wait=0):
    r = client.get("/api/fleet/jobs", params={"wait": wait}, headers=bearer(token))
    assert r.status_code == 200, r.text
    return r.json()["job"]


def finish(client, token, job_id, state="done", result=None):
    return client.post(f"/api/fleet/jobs/{job_id}", json={"state": state, "result": result or {}},
                       headers=bearer(token))


def test_the_host_token_is_required(client):
    _, token = make_host()
    assert client.get("/api/fleet/jobs").status_code == 401
    assert client.get("/api/fleet/jobs", headers=bearer("gf_wrong")).status_code == 401
    assert client.post("/api/fleet/heartbeat", json={}, headers={"Authorization": f"Basic {token}"}).status_code == 401
    assert client.post("/api/fleet/jobs/j_x", json={"state": "done"}, headers=bearer("nope")).status_code == 401
    assert next_job(client, token) is None


def test_a_create_job_runs_and_its_secret_is_blanked(client, hosting):
    host_id, token = make_host()
    _, other = make_host("vps-2", memory_mb=1024)          # less free memory: not chosen
    alice = make_account("alice", "plus")
    assert server(alice)["host_id"] == host_id
    assert next_job(client, other) is None
    job = next_job(client, token)
    assert job["kind"] == "create" and job["payload"]["env"]["GAMMA_CLOUD_CLIENT_SECRET"]
    assert next_job(client, token) is None                 # it is running now
    assert finish(client, other, job["id"]).status_code == 404
    assert finish(client, token, job["id"], state="maybe").status_code == 400
    n = len(mail.outbox)
    r = finish(client, token, job["id"], result={"container": "gamma-alice", "health": "ok"})
    assert r.status_code == 200 and r.json()["job"]["state"] == "done" and r.json()["job"]["payload"] == {}
    assert finish(client, token, job["id"]).status_code == 409
    [row] = jobs_of(alice)
    assert row["payload"] == "{}" and json.loads(row["result"])["health"] == "ok" and row["attempts"] == 1
    assert server(alice)["state"] == "running"
    assert [m["subject"] for m in mail.outbox[n:]] == ["Your Gamma is ready"]
    assert f"https://alice.{DOMAIN}" in mail.outbox[-1]["body"]


def test_a_failed_create_is_retried_with_a_new_secret(client, hosting):
    _, token = make_host()
    alice = make_account("alice", "plus")
    job = next_job(client, token)
    first_secret = job["payload"]["env"]["GAMMA_CLOUD_CLIENT_SECRET"]
    assert finish(client, token, job["id"], "failed", {"error": "pull failed"}).status_code == 200
    assert server(alice)["state"] == "provisioning"
    assert "pull failed" in json.loads(server(alice)["report"])["note"]
    with closing(db.connect()) as conn:
        fleet.retry(conn, job["id"], "test")
        conn.commit()
    again = next_job(client, token)
    assert again["id"] == job["id"] and again["payload"]["env"]["GAMMA_CLOUD_CLIENT_SECRET"] != first_secret
    assert again["payload"]["env"]["GAMMA_CLOUD_CLIENT_ID"] == job["payload"]["env"]["GAMMA_CLOUD_CLIENT_ID"]


def test_the_poll_waits_and_wakes(client, hosting):
    _, token = make_host()
    t0 = time.monotonic()
    assert next_job(client, token, wait=0.6) is None
    assert time.monotonic() - t0 >= 0.5
    threading.Timer(0.3, lambda: make_account("alice", "plus")).start()
    t0 = time.monotonic()
    job = next_job(client, token, wait=10)
    assert job and job["kind"] == "create" and time.monotonic() - t0 < 5


def test_an_idle_poll_takes_no_write_lock(client, hosting, monkeypatch):
    """With nothing queued the poll only reads, so another writer holding
    the lock does not stall it (it would wait out BUSY_TIMEOUT, then 503)."""
    _, token = make_host()
    monkeypatch.setattr(db, "BUSY_TIMEOUT", 0.3)
    with closing(db.connect()) as lock:
        lock.execute("BEGIN IMMEDIATE")
        lock.execute("UPDATE hosts SET address = 'busy'")
        assert next_job(client, token, wait=1.5) is None
        lock.rollback()
    make_account("alice", "plus")
    assert next_job(client, token)["kind"] == "create"


def test_the_heartbeat_updates_the_host_and_its_servers(client, hosting):
    host_id, token = make_host()
    alice = make_account("alice", "plus")
    body = {"agent_version": "0.1", "memory_mb": 16000, "disk_mb": 900000, "memory_used_mb": 3000,
            "disk_used_mb": 20000, "containers": [
                {"label": "alice", "running": True, "health": "healthy", "memory_mb": 300, "data_mb": 42,
                 "image": "ghcr.io/tim4431/gamma:sha-1"},
                {"label": "stranger", "running": True}, "junk"]}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    with closing(db.connect()) as conn:
        h = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    assert (h["agent_version"], h["memory_mb"], h["memory_used_mb"], h["disk_mb"]) == ("0.1", 16000, 3000, 900000)
    agent = json.loads(server(alice)["report"])["agent"]
    assert agent["running"] is True and agent["data_mb"] == 42 and agent["image"].endswith("sha-1")
    # the container's own sync keeps what the agent reported
    from test_hosted import REPORT, basic, credentials
    client.post("/api/hosted/sync", json=REPORT, headers=basic(*credentials(alice)))
    report = json.loads(server(alice)["report"])
    assert report["version"] == "1.2.3" and report["agent"]["data_mb"] == 42


def test_placement_needs_room_and_a_fresh_host(client, hosting):
    host_id, _ = make_host(disk_mb=8000, disk_used_mb=4000)      # 4 GB free < Plus's 6 GB
    alice = make_account("alice", "plus")
    assert server(alice)["host_id"] == ""
    roomy, _ = make_host("vps-2")
    with closing(db.connect()) as conn:                          # silent past STALE_AFTER
        conn.execute("UPDATE hosts SET last_seen_at = ? WHERE id = ?", (db.after(-fleet.STALE_AFTER - 60), roomy))
        conn.commit()
    tick()
    tick()
    assert server(alice)["host_id"] == ""
    with closing(db.connect()) as conn:
        h = conn.execute("SELECT * FROM hosts WHERE id = ?", (roomy,)).fetchone()
        assert h["accepting"] == 1                                # the admin's flag is left alone
        assert len(conn.execute("SELECT 1 FROM audit WHERE event = 'fleet.host_stale'").fetchall()) == 1
        assert fleet.public_host(h)["stale"] is True
        fleet.heartbeat(conn, dict(h), {"memory_mb": 8000, "disk_mb": 100000})   # back: a candidate again
        conn.commit()
    tick()
    assert server(alice)["host_id"] == roomy


def _three_running(client, token):
    ids = []
    for name in ("alice", "bob", "carol"):
        a = make_account(name, "plus")
        job = next_job(client, token)
        finish(client, token, job["id"])
        ids.append(server(a)["id"])
    return ids


def test_upgrade_waves_release_in_order_and_pause_on_failure(client, hosting):
    _, token = make_host()
    ids = _three_running(client, token)
    with closing(db.connect()) as conn:
        with pytest.raises(Exception):
            fleet.upgrade(conn, "bad tag!", 1)
        run = fleet.upgrade(conn, "sha-new", 1, actor="test")
        conn.commit()
    assert run["jobs"] == 3 and run["waves"] == 3
    with closing(db.connect()) as conn:
        states = [r[0] for r in conn.execute("SELECT state FROM fleet_jobs WHERE kind = 'upgrade' ORDER BY wave")]
        assert states == ["queued", "held", "held"]
        with pytest.raises(Exception):                            # one upgrade per server at a time
            fleet.upgrade(conn, "sha-other", 1)
    first = next_job(client, token)
    assert first["kind"] == "upgrade" and first["payload"]["image"] == f"{config.FLEET_IMAGE}:sha-new"
    assert next_job(client, token) is None                        # the next wave waits
    finish(client, token, first["id"])
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT image_tag FROM hosted_servers WHERE id = ?", (ids[0],)).fetchone()[0] == "sha-new"
    second = next_job(client, token)
    assert second["server_id"] == ids[1]
    finish(client, token, second["id"], "failed", {"error": "health check timed out"})
    tick()
    assert next_job(client, token) is None                        # paused
    with closing(db.connect()) as conn:
        fleet.retry(conn, second["id"], "test")
        conn.commit()
    again = next_job(client, token)
    assert again["id"] == second["id"]
    finish(client, token, again["id"])
    third = next_job(client, token)
    assert third["server_id"] == ids[2]


def test_canceling_the_failed_job_lets_the_run_go_on(client, hosting):
    _, token = make_host()
    ids = _three_running(client, token)
    with closing(db.connect()) as conn:
        fleet.upgrade(conn, "sha-new", 1, server_ids=[ids[0], ids[2]])
        conn.commit()
    job = next_job(client, token)
    assert job["server_id"] == ids[0]
    finish(client, token, job["id"], "failed", {"error": "x"})
    assert next_job(client, token) is None
    with closing(db.connect()) as conn:
        fleet.cancel(conn, job["id"], "test")
        conn.commit()
    assert next_job(client, token)["server_id"] == ids[2]


def test_a_job_with_no_result_fails_after_an_hour(client, hosting):
    _, token = make_host()
    alice = make_account("alice", "plus")
    job = next_job(client, token)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET started_at = ? WHERE id = ?", (db.after(-fleet.JOB_TIMEOUT - 60), job["id"]))
        conn.commit()
    tick()
    [row] = jobs_of(alice)
    assert row["state"] == "failed" and "within an hour" in row["result"]


# --- admin --------------------------------------------------------------------

def test_admin_endpoints(client, hosting):
    me = register(client, "operator")
    assert client.get("/api/admin/hosts").status_code == 403
    make_admin("operator")
    r = client.post("/api/admin/hosts", json={"name": "vps-1", "address": "203.0.113.5"})
    assert r.status_code == 200 and r.json()["token"].startswith("gf_")
    host, token = r.json()["host"], r.json()["token"]
    assert "token_hash" not in host and host["stale"] is True
    assert client.post("/api/admin/hosts", json={"name": "vps-1"}).status_code == 409
    assert client.post("/api/admin/hosts", json={"name": "bad name"}).status_code == 400
    client.post("/api/fleet/heartbeat", json={"memory_mb": 8000, "disk_mb": 200000}, headers=bearer(token))
    [listed] = client.get("/api/admin/hosts").json()["hosts"]
    assert listed["memory_mb"] == 8000 and listed["stale"] is False and listed["servers"] == 0

    assert client.post("/api/admin/servers/provision", json={"account_id": me["id"]}).status_code == 400  # free
    assert client.post("/api/admin/servers/provision", json={"account_id": "nobody"}).status_code == 404
    bob = make_account("bob", "free")
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET plan = 'plus', granted_plan = 'plus' WHERE id = ?", (bob,))
        conn.commit()
    r = client.post("/api/admin/servers/provision", json={"account_id": bob})
    assert r.status_code == 200 and r.json()["server"]["label"] == "bob" and r.json()["server"]["host"] == "vps-1"
    assert client.post("/api/admin/servers/provision", json={"account_id": bob}).status_code == 409
    sid = r.json()["server"]["id"]
    [s] = client.get("/api/admin/servers").json()["servers"]
    assert s["username"] == "bob" and s["url"] == f"https://bob.{DOMAIN}" and s["jobs"] == {"queued": 1}

    jobs = client.get("/api/admin/jobs").json()["jobs"]
    assert jobs[0]["kind"] == "create" and "env" not in jobs[0]["payload"] and jobs[0]["label"] == "bob"
    assert client.get("/api/admin/jobs", params={"state": "done"}).json()["jobs"] == []

    assert client.post(f"/api/admin/servers/{sid}/explode").status_code == 400
    assert client.post("/api/admin/servers/s_nope/restart").status_code == 404
    assert client.post(f"/api/admin/servers/{sid}/restart").status_code == 200
    r = client.post(f"/api/admin/servers/{sid}/suspend")
    assert r.json()["server"]["state"] == "suspended" and r.json()["server"]["read_only"] is True
    assert r.json()["server"]["limits"]["status"] == "read_only"
    tick()
    assert server(bob)["state"] == "suspended"                   # the lifecycle leaves a hold alone
    assert client.post(f"/api/admin/servers/{sid}/resume").json()["server"]["state"] == "provisioning"
    assert client.post(f"/api/admin/servers/{sid}/resume").status_code == 409

    assert client.post("/api/admin/servers/upgrade", json={"tag": "x y"}).status_code == 400
    assert client.post("/api/admin/servers/upgrade", json={"tag": "sha-1"}).status_code == 400   # none running
    job_id = jobs[0]["id"]
    assert client.post(f"/api/admin/jobs/{job_id}/retry").status_code == 409                     # queued
    assert client.post(f"/api/admin/jobs/{job_id}/cancel").json()["job"]["state"] == "canceled"
    assert client.post(f"/api/admin/jobs/{job_id}/nope").status_code == 404

    r = client.patch(f"/api/admin/hosts/{host['id']}", json={"accepting": False, "name": "vps-a"})
    assert r.json()["host"]["accepting"] is False and r.json()["host"]["name"] == "vps-a"
    r = client.post(f"/api/admin/servers/{sid}/delete")
    assert r.json()["server"]["state"] == "deleted"
    assert client.post(f"/api/admin/servers/{sid}/restart").status_code == 409
    assert "tab-servers" in client.get("/admin").text and "loadServers" in client.get("/admin").text


def test_cli(capsys, client, hosting):
    make_host("vps-2")
    manage.main(["add-host", "vps-1"])
    out = capsys.readouterr().out
    assert "GAMMA_FLEET_HOST_TOKEN=gf_" in out
    manage.main(["hosts"])
    assert "vps-1" in capsys.readouterr().out
    make_account("dora", "free")
    with pytest.raises(SystemExit):
        manage.main(["provision", "dora"])                      # free plan
    with pytest.raises(SystemExit):
        manage.main(["provision", "nobody"])
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET plan = 'pro', granted_plan = 'pro' WHERE username = 'dora'")
        conn.commit()
    manage.main(["provision", "dora"])
    assert "dora" in capsys.readouterr().out
    manage.main(["servers"])
    out = capsys.readouterr().out
    assert "dora" in out and "provisioning" in out
    manage.main(["jobs"])
    assert "create" in capsys.readouterr().out

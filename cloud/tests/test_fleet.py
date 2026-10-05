"""The fleet: host tokens, the job queue an agent works through, the
heartbeat and orphans, placement by committed memory, resizes, upgrades
(waves and one server), stuck jobs and retries, and the admin's Servers tab
API and CLI (docs/dev/hosted.md)."""

import json
import threading
import time
from contextlib import closing

import pytest
from conftest import make_admin, register, steps_after
from test_hosted import DOMAIN, hosting, jobs_of, make_account, make_host, server, set_plan, tick  # noqa: F401

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
                {"label": "alice", "running": True, "health": "healthy", "memory_mb": 300, "memory_limit_mb": 768,
                 "data_mb": 42, "image": "ghcr.io/tim4431/gamma:sha-1"},
                {"label": "stranger", "running": True}, "junk"]}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    with closing(db.connect()) as conn:
        h = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    assert (h["agent_version"], h["memory_mb"], h["memory_used_mb"], h["disk_mb"]) == ("0.1", 16000, 3000, 900000)
    assert json.loads(h["orphans"]) == ["stranger"]
    agent = json.loads(server(alice)["report"])["agent"]
    assert agent["running"] is True and agent["data_mb"] == 42 and agent["image"].endswith("sha-1")
    assert agent["memory_limit_mb"] == 768
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


def test_a_waiting_server_is_placed_by_the_first_heartbeat_or_by_provision(client, hosting):
    """A server created while no host had room waits; a host's heartbeat
    places it at once, and Provision on a waiting server retries rather
    than refusing it as one that exists."""
    alice = make_account("alice", "lite")
    assert server(alice)["state"] == "provisioning" and server(alice)["host_id"] == ""
    with closing(db.connect()) as conn:
        host, token = fleet.add_host(conn, "vps-new", actor="test")        # no heartbeat yet: not a candidate
        conn.commit()
    assert server(alice)["host_id"] == ""
    body = {"agent_version": "1", "memory_mb": 4096, "disk_mb": 100_000, "memory_used_mb": 500, "disk_used_mb": 1000}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    assert server(alice)["host_id"] == host["id"]
    assert [j["kind"] for j in jobs_of(alice)] == ["create"]

    with closing(db.connect()) as conn:                                     # closed while bob signs up: he waits
        conn.execute("UPDATE hosts SET accepting = 0")
        conn.commit()
    bob = make_account("bob", "lite")
    assert server(bob)["host_id"] == ""
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosts SET accepting = 1")
        conn.commit()
    register(client, "operator")
    make_admin("operator")
    r = client.post("/api/admin/servers/provision", json={"account_id": bob})
    assert r.status_code == 200, r.text
    assert server(bob)["host_id"] == host["id"]


def test_placement_counts_the_memory_committed_to_servers(client, hosting):
    """Free memory is the host's memory less the plans' memory of the
    servers on it and the host's reserve, whatever it uses right now."""
    big, _ = make_host("vps-big", memory_mb=4096, memory_used_mb=4000)     # 3072 free to place
    small, _ = make_host("vps-small", memory_mb=3072, memory_used_mb=0)    # 2048
    ann, ben, cat, dan = (make_account(name, "pro") for name in ("ann", "ben", "cat", "dan"))
    assert [server(a)["host_id"] for a in (ann, ben, cat, dan)] == [big, small, big, ""]   # 1536 MB each
    with closing(db.connect()) as conn:
        hosts = {h["id"]: h for h in fleet.hosts(conn)}
    assert (hosts[big]["committed_mb"], hosts[big]["free_mb"], hosts[big]["reserve_mb"]) == (3072, 0, 1024)
    assert (hosts[small]["committed_mb"], hosts[small]["free_mb"], hosts[small]["servers"]) == (1536, 512, 1)
    assert server(make_account("eve", "lite"))["host_id"] == small          # 512 MB still fits
    with closing(db.connect()) as conn:                                     # a deleted server frees its memory
        from gammacloud import hosted
        hosted.admin_action(conn, server(ann)["id"], "delete", "test")
        conn.commit()
    tick()
    assert server(dan)["host_id"] == big


def server_by_id(server_id):
    with closing(db.connect()) as conn:
        return dict(conn.execute("SELECT * FROM hosted_servers WHERE id = ?", (server_id,)).fetchone())


def test_orphans_are_kept_until_they_go_and_can_be_removed(client, hosting):
    me = register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    beat = {"memory_mb": 8192, "disk_mb": 500_000, "containers": [{"label": "ghost", "running": True},
                                                                     {"label": "old", "running": False}]}
    client.post("/api/fleet/heartbeat", json=beat, headers=bearer(token))
    [h] = client.get("/api/admin/hosts").json()["hosts"]
    assert h["orphans"] == ["ghost", "old"]
    assert client.post(f"/api/admin/hosts/{host_id}/orphans/nobody/remove").status_code == 404
    assert client.post("/api/admin/hosts/h_nope/orphans/ghost/remove").status_code == 404
    r = client.post(f"/api/admin/hosts/{host_id}/orphans/ghost/remove")
    job = r.json()["job"]
    assert r.status_code == 200 and job["kind"] == "delete" and job["server_id"] == "" and job["label"] == "ghost"
    assert job["payload"] == {"label": "ghost", "account_id": ""} and job["state"] == "queued"
    assert client.post(f"/api/admin/hosts/{host_id}/orphans/ghost/remove").json()["job"]["id"] == job["id"]
    claimed = next_job(client, token)
    assert claimed["id"] == job["id"] and claimed["payload"]["account_id"] == ""
    assert finish(client, token, job["id"], result={"removed": ["gamma-ghost"]}).status_code == 200
    [h] = client.get("/api/admin/hosts").json()["hosts"]
    assert h["orphans"] == ["old"]                                          # gone once its removal is done
    # a server row for the label makes it no orphan; a heartbeat without it clears it too
    alice = make_account("old", "plus")
    assert server(alice)["host_id"] == host_id
    [h] = client.get("/api/admin/hosts").json()["hosts"]
    assert h["orphans"] == []
    assert client.post(f"/api/admin/hosts/{host_id}/orphans/old/remove").status_code == 409
    client.post("/api/fleet/heartbeat", json={**beat, "containers": []}, headers=bearer(token))
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT orphans FROM hosts WHERE id = ?", (host_id,)).fetchone()[0] == "[]"
    assert me


def test_an_orphans_removal_never_reaches_a_server_that_took_its_label(client, hosting):
    """Removing orphan "carol" is queued; before the agent asks, an account
    named carol gets a server on that host. The job is canceled when it
    would be handed out, and retrying it is refused: running it would
    delete carol's live container and data."""
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    beat = {"memory_mb": 8192, "disk_mb": 500_000, "containers": [{"label": "carol", "running": True}]}
    client.post("/api/fleet/heartbeat", json=beat, headers=bearer(token))
    job = client.post(f"/api/admin/hosts/{host_id}/orphans/carol/remove").json()["job"]
    carol = make_account("carol", "plus")
    assert server(carol)["host_id"] == host_id and server(carol)["label"] == "carol"
    handed = next_job(client, token)
    assert handed["kind"] == "create" and handed["server_id"] == server(carol)["id"]    # the orphan's job was skipped
    with closing(db.connect()) as conn:
        row = conn.execute("SELECT state, result FROM fleet_jobs WHERE id = ?", (job["id"],)).fetchone()
    assert row["state"] == "canceled" and "belongs to a hosted server" in row["result"]
    r = client.post(f"/api/admin/jobs/{job['id']}/retry")
    assert r.status_code == 409 and "belongs to a hosted server" in r.json()["detail"]


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


def test_a_stuck_create_is_blanked_and_its_late_result_still_counts(client, hosting):
    _, token = make_host()
    alice = make_account("alice", "plus")
    job = next_job(client, token)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET started_at = ? WHERE id = ?", (db.after(-fleet.JOB_TIMEOUT - 60), job["id"]))
        conn.commit()
    tick()
    [row] = jobs_of(alice)
    assert row["state"] == "failed" and row["payload"] == "{}"               # the secret does not linger
    assert json.loads(server(alice)["report"])["note"] == "create failed: no result from the agent within an hour"
    n = len(mail.outbox)
    assert finish(client, token, job["id"], result={"container": "gamma-alice"}).status_code == 200   # late, but done
    assert server(alice)["state"] == "running" and "note" not in json.loads(server(alice)["report"])
    assert [m["subject"] for m in mail.outbox[n:]] == ["Your Gamma is ready"]
    assert finish(client, token, job["id"]).status_code == 409               # only once
    # the blanked payload no longer says what size the container was made with,
    # so the late result sizes it to the plan to be sure (an in-place update)
    resize = [j for j in jobs_of(alice) if j["kind"] == "upgrade"]
    assert len(resize) == 1 and json.loads(resize[0]["payload"]) == {"label": "alice", "memory_mb": 768, "cpus": 1.0}


def test_a_late_result_does_not_undo_an_admins_cancel(client, hosting):
    _, token = make_host()
    ids = _three_running(client, token)
    with closing(db.connect()) as conn:
        fleet.upgrade(conn, "sha-new", 1, server_ids=[ids[0]])
        conn.commit()
    job = next_job(client, token)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET started_at = ? WHERE id = ?", (db.after(-fleet.JOB_TIMEOUT - 60), job["id"]))
        conn.commit()
    tick()
    with closing(db.connect()) as conn:
        fleet.cancel(conn, job["id"], "test")
        conn.commit()
    assert finish(client, token, job["id"]).status_code == 409
    assert server_by_id(ids[0])["image_tag"] == config.FLEET_IMAGE_TAG


def test_retry_is_refused_for_a_deleted_server_and_a_replaced_create(client, hosting):
    from gammacloud import hosted
    _, token = make_host()
    alice = make_account("alice", "plus")
    job = next_job(client, token)
    finish(client, token, job["id"], "failed", {"error": "pull failed"})
    sid = server(alice)["id"]
    with closing(db.connect()) as conn:
        hosted.admin_action(conn, sid, "delete", "test")
        conn.commit()
        with pytest.raises(Exception, match="deleted"):
            fleet.retry(conn, job["id"], "test")
    delete = next_job(client, token)
    finish(client, token, delete["id"], "failed", {"error": "busy"})
    with closing(db.connect()) as conn:
        assert fleet.retry(conn, delete["id"], "test")["state"] == "queued"  # a delete job still is
        conn.commit()
    set_plan(alice, "plus")                                                  # the row comes back with a new create job
    assert server(alice)["state"] == "provisioning"
    with closing(db.connect()) as conn:
        with pytest.raises(Exception, match="later create"):
            fleet.retry(conn, job["id"], "test")


# --- sizes, logs, rollback, one server's upgrade --------------------------------

def _claim(client, token, kind):
    job = next_job(client, token)
    assert job and job["kind"] == kind, job
    return job


def test_a_plan_change_resizes_the_container(client, hosting):
    host_id, token = make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    create = _claim(client, token, "create")
    assert (create["payload"]["memory_mb"], create["payload"]["cpus"]) == (768, 1.0)
    set_plan(alice, "pro", status="active")                                 # while the create runs
    finish(client, token, create["id"])
    resize = _claim(client, token, "upgrade")
    assert resize["payload"] == {"label": "alice", "memory_mb": 1536, "cpus": 2.0}
    finish(client, token, resize["id"], result={"image": f"{config.FLEET_IMAGE}:{config.FLEET_IMAGE_TAG}"})
    assert server(alice)["image_tag"] == config.FLEET_IMAGE_TAG
    limits = json.loads(server(alice)["limits"])
    assert (limits["memory_mb"], limits["cpus"]) == (1536, 2.0)
    with closing(db.connect()) as conn:
        assert {h["id"]: h for h in fleet.hosts(conn)}[host_id]["committed_mb"] == 1536
    # down and up again before the agent got to it: one job, sized for the last plan
    set_plan(alice, "plus", status="active")
    set_plan(alice, "lite", status="active")
    queued = [j for j in jobs_of(alice) if j["state"] == "queued"]
    assert len(queued) == 1 and json.loads(queued[0]["payload"]) == {"label": "alice", "memory_mb": 512, "cpus": 1.0}
    # a resize is no pending upgrade: a wave still takes the server
    with closing(db.connect()) as conn:
        assert fleet.upgrade(conn, "sha-new", 1)["jobs"] == 1
    # the same plan again, or a lapse (the last hosted plan's size stays): nothing new
    set_plan(alice, "lite", status="active")
    set_plan(alice, "free", status="canceled", ended_at=db.now())
    assert len([j for j in jobs_of(alice) if j["state"] == "queued"]) == 1


def test_a_queued_create_takes_the_new_size(client, hosting):
    make_host()
    alice = make_account("alice", "plus", granted="free")
    set_plan(alice, "plus", status="active")
    set_plan(alice, "pro", status="active")
    [job] = jobs_of(alice)
    payload = json.loads(job["payload"])
    assert job["kind"] == "create" and (payload["memory_mb"], payload["cpus"]) == (1536, 2.0)


def test_logs_and_rollback_jobs(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, _, _] = _three_running(client, token)
    assert client.post("/api/admin/servers/s_nope/logs").status_code == 404
    r = client.post(f"/api/admin/servers/{sid}/logs")
    job = r.json()["job"]
    assert r.status_code == 200 and job["kind"] == "logs" and job["state"] == "queued" and job["label"] == "alice"
    assert client.get(f"/api/admin/jobs/{job['id']}").json()["job"]["state"] == "queued"
    assert client.get("/api/admin/jobs/j_nope").status_code == 404
    claimed = _claim(client, token, "logs")
    assert claimed["payload"] == {"label": "alice"}
    lines = [f"2026-10-04T09:00:00.{i:09d}Z " + "x" * 600 for i in range(200)]   # past LOGS_RESULT_MAX
    finish(client, token, job["id"], result={"container": "gamma-alice", "lines": lines, "since": "2026-10-04T09:00:00Z"})
    full = client.get(f"/api/admin/jobs/{job['id']}").json()["job"]
    result = json.loads(full["result"])
    assert full["state"] == "done" and result["truncated"] is True and result["lines"][-1] == lines[-1]
    assert 100 < len(result["lines"]) < 200 and len(full["result"]) <= fleet.LOGS_RESULT_MAX
    listed = next(j for j in client.get("/api/admin/jobs").json()["jobs"] if j["id"] == job["id"])
    assert json.loads(listed["result"]) == {"container": "gamma-alice", "since": "2026-10-04T09:00:00Z",
                                            "truncated": True, "line_count": len(result["lines"])}

    r = client.post(f"/api/admin/servers/{sid}/rollback")
    job = r.json()["job"]
    assert job["kind"] == "rollback"
    _claim(client, token, "rollback")
    finish(client, token, job["id"], result={"container": "gamma-alice", "image": f"{config.FLEET_IMAGE}:sha-old"})
    assert server_by_id(sid)["image_tag"] == "sha-old"
    with closing(db.connect()) as conn:
        assert {"hosted.logs", "hosted.rollback"} <= {r[0] for r in conn.execute("SELECT event FROM audit")}


def test_one_servers_upgrade_and_the_outdated_flag(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, other, _] = _three_running(client, token)
    listed = client.get("/api/admin/servers").json()
    assert listed["default_image"] == f"{config.FLEET_IMAGE}:{config.FLEET_IMAGE_TAG}"
    s = next(x for x in listed["servers"] if x["id"] == sid)
    assert (s["memory_mb"], s["cpus"], s["quota_mb"], s["outdated"]) == (768, 1.0, 6144, False)
    assert s["image"] == listed["default_image"]
    assert client.post(f"/api/admin/servers/{sid}/upgrade", json={"tag": "bad tag"}).status_code == 400
    assert client.post("/api/admin/servers/s_nope/upgrade", json={"tag": "sha-2"}).status_code == 404
    r = client.post(f"/api/admin/servers/{sid}/upgrade", json={"tag": "sha-2"})
    job = r.json()["job"]
    assert r.status_code == 200 and job["kind"] == "upgrade" and job["wave"] == "" and job["server_id"] == sid
    assert job["payload"] == {"label": "alice", "image": f"{config.FLEET_IMAGE}:sha-2", "tag": "sha-2"}
    assert client.post(f"/api/admin/servers/{sid}/upgrade", json={"tag": "sha-3"}).status_code == 409
    assert server_by_id(sid)["image_tag"] == config.FLEET_IMAGE_TAG          # moves when the job is done
    finish(client, token, _claim(client, token, "upgrade")["id"])
    servers = {x["id"]: x for x in client.get("/api/admin/servers").json()["servers"]}
    assert servers[sid]["image_tag"] == "sha-2" and servers[sid]["outdated"] is True
    assert servers[other]["outdated"] is False
    bob = make_account("dora", "plus")                                       # not created yet
    assert client.post(f"/api/admin/servers/{server(bob)['id']}/upgrade", json={"tag": "sha-2"}).status_code == 409


def test_jobs_carry_their_duration_and_their_runs_progress(client, hosting):
    _, token = make_host()
    _three_running(client, token)
    with closing(db.connect()) as conn:
        run = fleet.upgrade(conn, "sha-new", 2, actor="test")
        conn.commit()
    first = next_job(client, token)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET started_at = ? WHERE id = ?", (db.after(-30), first["id"]))
        conn.commit()
        rows = {j["id"]: j for j in fleet.jobs(conn)}
    assert rows[first["id"]]["duration_s"] >= 30 and rows[first["id"]]["state"] == "running"
    wave = [j for j in rows.values() if j["wave"].startswith(run["run"])]
    assert len(wave) == 3 and {(j["wave_total"], j["wave_done"]) for j in wave} == {(3, 0)}
    assert {j["duration_s"] for j in wave if j["state"] in ("queued", "held")} == {None}
    create = next(j for j in rows.values() if j["kind"] == "create")
    assert create["wave_total"] is None and create["duration_s"] is not None
    finish(client, token, first["id"])
    with closing(db.connect()) as conn:
        done = fleet.job(conn, first["id"])
    assert done["wave_done"] == 1 and 30 <= done["duration_s"] < 60


def test_step_10_adds_the_orphans_column():
    with closing(db.connect()) as conn:
        conn.execute("ALTER TABLE hosts DROP COLUMN orphans")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
    assert db.ensure_current() == steps_after(9) == ["fleet_orphans"]
    with closing(db.connect()) as conn:
        host, _ = fleet.add_host(conn, "vps-1")
        assert host["orphans"] == []


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
    out = capsys.readouterr().out
    assert "vps-1" in out and "committed=0MB" in out
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
    assert "dora" in out and "provisioning" in out and "mem=1536MB" in out
    manage.main(["jobs"])
    assert "create" in capsys.readouterr().out

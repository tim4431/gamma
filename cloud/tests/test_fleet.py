"""The fleet: host tokens, the job queue an agent works through, the
heartbeat and orphans, placement by committed memory, resizes, upgrades
(waves and one server), stuck jobs and retries, every container of a host
and its jobs, and the admin's Machines and Servers tab API and CLI
(docs/dev/hosted.md)."""

import json
import threading
import time
from contextlib import closing

import pytest
from conftest import make_admin, register, set_setting, steps_after
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
    assert f"https://alice-user.{DOMAIN}" in mail.outbox[-1]["body"]


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
                 "data_mb": 42, "image": "ghcr.io/tim4431/gamma:sha-1", "cpu_pct": 12.345, "restarts": 2,
                 "started_at": "2026-10-05T07:00:00Z", "oom_killed": True, "image_stale": False},
                {"label": "stranger", "running": True}, "junk"]}
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200
    with closing(db.connect()) as conn:
        h = conn.execute("SELECT * FROM hosts WHERE id = ?", (host_id,)).fetchone()
    assert (h["agent_version"], h["memory_mb"], h["memory_used_mb"], h["disk_mb"]) == ("0.1", 16000, 3000, 900000)
    assert json.loads(h["orphans"]) == ["stranger"]
    agent = json.loads(server(alice)["report"])["agent"]
    assert agent["running"] is True and agent["data_mb"] == 42 and agent["image"].endswith("sha-1")
    assert agent["memory_limit_mb"] == 768
    assert (agent["cpu_pct"], agent["restarts"], agent["started_at"], agent["oom_killed"], agent["image_stale"]) == (
        12.3, 2, "2026-10-05T07:00:00Z", True, False)
    # what an older agent sends, or nonsense: unknown, not wrong
    odd = {"label": "alice", "running": True, "data_mb": 42, "cpu_pct": "high", "restarts": -1, "oom_killed": "yes",
           "image_stale": "no"}
    client.post("/api/fleet/heartbeat", json={**body, "containers": [odd]}, headers=bearer(token))
    agent = json.loads(server(alice)["report"])["agent"]
    assert (agent["cpu_pct"], agent["restarts"], agent["started_at"], agent["oom_killed"], agent["image_stale"]) == (
        None, 0, "", False, None)
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


# --- the default tag, outdated servers, automatic upgrades ----------------------

def kinds(kind, state=None):
    """Every job of ``kind`` (in ``state``) as (server_id, state, wave), oldest first."""
    with closing(db.connect()) as conn:
        return [tuple(r) for r in conn.execute(
            "SELECT server_id, state, wave FROM fleet_jobs WHERE kind = ? AND (? IS NULL OR state = ?) "
            "ORDER BY created_at, rowid", (kind, state, state)).fetchall()]


def job_row(job_id):
    with closing(db.connect()) as conn:
        return dict(conn.execute("SELECT * FROM fleet_jobs WHERE id = ?", (job_id,)).fetchone())


def test_the_default_tag_is_a_setting(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, _, _] = _three_running(client, token)
    set_setting("fleet_image_tag", "sha-new")
    listed = client.get("/api/admin/servers").json()
    assert listed["default_image"] == fleet.default_image() == f"{config.FLEET_IMAGE}:sha-new"
    assert listed["auto_upgrade"] is False
    s = next(x for x in listed["servers"] if x["id"] == sid)
    assert (s["outdated"], s["outdated_why"], s["image_tag"]) == (True, "tag", config.FLEET_IMAGE_TAG)
    dora = make_account("dora", "plus")                                     # a new server runs the new default
    assert server(dora)["image_tag"] == "sha-new"
    assert json.loads(jobs_of(dora)[0]["payload"])["image"] == f"{config.FLEET_IMAGE}:sha-new"
    set_setting("fleet_image_tag", "")                                      # back to the environment's
    assert fleet.default_tag() == config.FLEET_IMAGE_TAG
    s = next(x for x in client.get("/api/admin/servers").json()["servers"] if x["id"] == sid)
    assert s["outdated"] is False and s["outdated_why"] == ""


def test_outdated_by_its_image_and_an_upgrade_to_the_same_tag(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, other, third] = _three_running(client, token)
    tag = config.FLEET_IMAGE_TAG
    assert client.post("/api/admin/servers/upgrade", json={"outdated": True}).status_code == 409   # none is
    beat = {"memory_mb": 8192, "disk_mb": 500_000, "containers": [
        {"label": "alice", "running": True, "image": f"{config.FLEET_IMAGE}:{tag}", "image_stale": True},
        {"label": "bob", "running": True, "image_stale": None}, {"label": "carol", "running": True}]}
    client.post("/api/fleet/heartbeat", json=beat, headers=bearer(token))
    servers = {x["id"]: x for x in client.get("/api/admin/servers").json()["servers"]}
    assert (servers[sid]["outdated"], servers[sid]["outdated_why"]) == (True, "image")
    assert not servers[other]["outdated"] and not servers[third]["outdated"]
    # one server, to the tag it runs already: the agent pulls it again
    r = client.post(f"/api/admin/servers/{sid}/upgrade", json={"tag": tag})
    assert r.status_code == 200 and r.json()["job"]["payload"]["image"] == f"{config.FLEET_IMAGE}:{tag}"
    finish(client, token, _claim(client, token, "upgrade")["id"], result={"image": f"{config.FLEET_IMAGE}:{tag}"})
    s = next(x for x in client.get("/api/admin/servers").json()["servers"] if x["id"] == sid)
    assert s["outdated"] is False and s["report"]["agent"]["image_stale"] is False   # until the next heartbeat
    # every outdated server, and only those, in a run: by its image, or by its tag
    client.post("/api/fleet/heartbeat", json=beat, headers=bearer(token))
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosted_servers SET image_tag = 'sha-old' WHERE id = ?", (other,))
        conn.commit()
    r = client.post("/api/admin/servers/upgrade", json={"outdated": True, "wave_size": 5})
    assert r.status_code == 200 and (r.json()["jobs"], r.json()["image"]) == (2, f"{config.FLEET_IMAGE}:{tag}")
    assert {k[0] for k in kinds("upgrade", "queued")} == {sid, other}
    assert client.post("/api/admin/servers/upgrade", json={"outdated": True}).status_code == 409   # pending now
    assert client.post("/api/admin/servers/upgrade", json={"wave_size": 1}).status_code == 400     # no tag


def test_automatic_upgrades_stop_at_a_failure(client, hosting):
    _, token = make_host()
    [sid, other, third] = _three_running(client, token)
    set_setting("fleet_image_tag", "sha-new")
    with closing(db.connect()) as conn:                                     # two of them run it already
        conn.execute("UPDATE hosted_servers SET image_tag = 'sha-new' WHERE id != ?", (sid,))
        conn.commit()
    tick()
    assert kinds("upgrade") == []                                           # off by default
    set_setting("fleet_auto_upgrade", "on")
    tick()
    [(server_id, state, wave)] = kinds("upgrade")
    assert (server_id, state) == (sid, "queued") and wave.endswith("/001")
    with closing(db.connect()) as conn:
        [audit] = conn.execute("SELECT actor, detail FROM audit WHERE event = 'fleet.auto_upgrade'").fetchall()
    assert audit["actor"] == "system" and "sha-new" in audit["detail"] and "servers=1" in audit["detail"]
    tick()
    assert len(kinds("upgrade")) == 1                                       # a run in flight: no second one
    job = _claim(client, token, "upgrade")
    finish(client, token, job["id"], "failed", {"error": "pull failed"})
    tick()
    assert len(kinds("upgrade")) == 1                                       # the failure holds the automatic pass
    with closing(db.connect()) as conn:
        fleet.cancel(conn, job["id"], "test")
        conn.commit()
    tick()
    assert [k[1] for k in kinds("upgrade")] == ["canceled", "queued"]       # canceled: it tries again
    finish(client, token, _claim(client, token, "upgrade")["id"])
    tick()
    assert len(kinds("upgrade")) == 2 and server_by_id(sid)["image_tag"] == "sha-new"   # nothing outdated


def test_an_automatic_upgrade_for_a_moved_image_waits_a_day(client, hosting):
    """A server outdated only by the agent's staleness report is rebuilt by
    the automatic pass once a day at most, so a report that stayed wrong
    after a pull cannot restart it every hour; the admin's own Upgrade all
    outdated is not held back."""
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, other, third] = _three_running(client, token)
    image = f"{config.FLEET_IMAGE}:{config.FLEET_IMAGE_TAG}"
    stale = {"memory_mb": 8192, "disk_mb": 500_000, "containers": [
        {"label": "alice", "running": True, "image": image, "image_stale": True},
        {"label": "bob", "running": True}, {"label": "carol", "running": True}]}
    client.post("/api/fleet/heartbeat", json=stale, headers=bearer(token))
    set_setting("fleet_auto_upgrade", "on")
    tick()
    assert [k[:2] for k in kinds("upgrade")] == [(sid, "queued")]
    job = _claim(client, token, "upgrade")
    finish(client, token, job["id"], result={"image": image})
    client.post("/api/fleet/heartbeat", json=stale, headers=bearer(token))      # the agent still says stale
    tick()
    assert len(kinds("upgrade")) == 1                                            # not again today
    r = client.post("/api/admin/servers/upgrade", json={"outdated": True})       # the admin may, at once
    assert r.status_code == 200 and r.json()["jobs"] == 1
    with closing(db.connect()) as conn:
        fleet.cancel(conn, next(j["id"] for j in fleet.jobs(conn, "queued", 10)), "test")
        conn.execute("UPDATE fleet_jobs SET finished_at = ? WHERE id = ?", (db.after(-2 * 86400), job["id"]))
        conn.commit()
    tick()                                                                       # a day later: taken again
    assert [k[:2] for k in kinds("upgrade")][-1] == (sid, "queued")


# --- the extra environment and update jobs ---------------------------------------

def test_the_environment_is_read_when_the_agent_takes_the_job(client, hosting):
    from gammacloud import hosted, settings
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    alice = make_account("alice", "plus")
    sid = server(alice)["id"]
    with closing(db.connect()) as conn:
        settings.set_fleet_env(conn, {"SMTP_HOST": "mail.example", "MODE": "fleet"}, None, "admin")
        view = hosted.set_env(conn, sid, {"MODE": "own", "TOKEN": "s3cret"}, None, "admin")
        conn.commit()
    assert view["env_names"] == ["MODE", "TOKEN"] and "env" not in view and "s3cret" not in json.dumps(view)
    assert [j["kind"] for j in jobs_of(alice)] == ["create"]                 # no container yet: the create takes them
    create = next_job(client, token)                                         # queued before they were saved
    assert create["payload"]["extra_env"] == {"SMTP_HOST": "mail.example", "MODE": "own", "TOKEN": "s3cret"}
    assert "TOKEN" not in create["payload"]["env"]
    shown = client.get(f"/api/admin/jobs/{create['id']}").json()["job"]
    assert shown["payload"]["extra_env"] == ["MODE", "SMTP_HOST", "TOKEN"] and "s3cret" not in json.dumps(shown)
    with closing(db.connect()) as conn:                                      # saved while it runs: an update follows
        hosted.set_env(conn, sid, {"TOKEN": "n3w"}, None, "admin")
        conn.commit()
    assert [j["kind"] for j in jobs_of(alice)] == ["create"]
    finish(client, token, create["id"])
    update = _claim(client, token, "update")
    assert update["payload"] == {"label": "alice", "extra_env": {"SMTP_HOST": "mail.example", "MODE": "own", "TOKEN": "n3w"}}
    finish(client, token, update["id"], result={"container": "gamma-alice", "extra_env": ["MODE", "SMTP_HOST", "TOKEN"]})
    assert [json.loads(j["payload"]) for j in jobs_of(alice)] == [{}, {}]   # neither keeps a value
    with closing(db.connect()) as conn:
        audit = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event IN ('hosted.env', 'settings.fleet_env') "
                                            "ORDER BY id")]
    assert audit == ["set MODE,SMTP_HOST", "alice set MODE,TOKEN", "alice set TOKEN"]


def test_an_update_job_applies_a_running_servers_variables(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    [sid, _, _] = _three_running(client, token)
    r = client.patch(f"/api/admin/servers/{sid}", json={"env": {"set": {"TOKEN": "one"}}})
    assert r.status_code == 200 and r.json()["server"]["env_names"] == ["TOKEN"]
    client.patch(f"/api/admin/servers/{sid}", json={"env": {"set": {"TOKEN": "two", "MODE": "x"}}})
    assert [k[1] for k in kinds("update")] == ["queued"]                     # one job: it reads the newest when taken
    job = _claim(client, token, "update")
    assert job["server_id"] == sid and job["payload"] == {"label": "alice", "extra_env": {"TOKEN": "two", "MODE": "x"}}
    shown = client.get(f"/api/admin/jobs/{job['id']}").json()["job"]
    assert shown["payload"]["extra_env"] == ["MODE", "TOKEN"] and "two" not in json.dumps(shown["payload"])
    # it times out: blanked; a retry is built again when the agent takes it
    with closing(db.connect()) as conn:
        conn.execute("UPDATE fleet_jobs SET started_at = ? WHERE id = ?", (db.after(-fleet.JOB_TIMEOUT - 60), job["id"]))
        conn.commit()
    tick()
    assert job_row(job["id"])["state"] == "failed" and job_row(job["id"])["payload"] == "{}"
    assert client.post(f"/api/admin/jobs/{job['id']}/retry").status_code == 200
    again = _claim(client, token, "update")
    assert again["id"] == job["id"] and again["payload"]["extra_env"] == {"TOKEN": "two", "MODE": "x"}
    finish(client, token, again["id"])
    # canceled before it ran: blanked too
    client.patch(f"/api/admin/servers/{sid}", json={"env": {"unset": ["MODE"]}})
    assert [k[1] for k in kinds("update")] == ["done", "queued"]
    [queued] = client.get("/api/admin/jobs", params={"state": "queued"}).json()["jobs"]
    assert client.post(f"/api/admin/jobs/{queued['id']}/cancel").json()["job"]["payload"] == {}
    # what is refused
    for body in ({"env": {"set": {"GAMMA_CLOUD_CLIENT_SECRET": "x"}}}, {"env": {"set": {"A": "x\ny"}}},
                 {"env": "A=1"}, {"color": "red"}, {}, {"overrides": {"memory_mb": -1}}):
        assert client.patch(f"/api/admin/servers/{sid}", json=body).status_code == 400, body
    assert client.patch("/api/admin/servers/s_nope", json={"env": {"set": {"A": "1"}}}).status_code == 404
    r = client.patch(f"/api/admin/servers/{sid}", json={"overrides": {"max_accounts": 5}, "env": {"set": {"B": "2"}}})
    assert r.status_code == 200 and r.json()["server"]["overrides"] == {"max_accounts": 5}
    assert r.json()["server"]["env_names"] == ["B", "TOKEN"]
    # deleting the server cancels the update that waits, blanked like a create
    [waiting] = client.get("/api/admin/jobs", params={"state": "queued"}).json()["jobs"]
    assert waiting["kind"] == "update" and client.post(f"/api/admin/servers/{sid}/delete").status_code == 200
    assert (job_row(waiting["id"])["state"], job_row(waiting["id"])["payload"]) == ("canceled", "{}")


def test_the_fleets_variables_and_an_update_run(client, hosting):
    register(client, "operator")
    make_admin("operator")
    _, token = make_host()
    ids = _three_running(client, token)
    dora = make_account("dora", "plus")                                      # a state a run takes, but no container
    finish(client, token, _claim(client, token, "create")["id"], "failed", {"error": "pull failed"})
    client.post(f"/api/admin/servers/{server(dora)['id']}/suspend")
    assert client.get("/api/admin/fleet-env").json() == {"names": []}
    r = client.patch("/api/admin/fleet-env", json={"set": {"SMTP_HOST": "mail.example", "SMTP_PASSWORD": "pw-1"}})
    assert r.status_code == 200 and r.json() == {"names": ["SMTP_HOST", "SMTP_PASSWORD"]}
    assert client.patch("/api/admin/fleet-env", json={"set": {"GAMMA_PUBLIC_URL": "x"}}).status_code == 400
    assert client.patch("/api/admin/fleet-env", json={"unset": "SMTP_HOST"}).status_code == 400
    listed = client.get("/api/admin/fleet-env")
    assert listed.json() == {"names": ["SMTP_HOST", "SMTP_PASSWORD"]} and "pw-1" not in listed.text
    assert kinds("update") == []                                             # saving enqueues nothing
    client.patch(f"/api/admin/servers/{ids[1]}", json={"env": {"set": {"OWN": "1"}}})   # waits already: skipped
    r = client.post("/api/admin/servers/apply-env", json={"wave_size": 1})
    assert r.status_code == 200 and (r.json()["jobs"], r.json()["waves"]) == (2, 2) and r.json()["run"].startswith("e")
    assert [k[0] for k in kinds("update", "held")] == [ids[2]]
    a, b = next_job(client, token), next_job(client, token)                  # its own update and the first wave
    assert next_job(client, token) is None                                   # the second wave is held
    assert {a["server_id"], b["server_id"]} == {ids[0], ids[1]}
    own = a if a["server_id"] == ids[1] else b
    assert own["payload"]["extra_env"] == {"SMTP_HOST": "mail.example", "SMTP_PASSWORD": "pw-1", "OWN": "1"}
    finish(client, token, own["id"])
    wave1 = b if own is a else a
    # a variable removed while the run goes on is gone from the waves still held
    client.patch("/api/admin/fleet-env", json={"unset": ["SMTP_PASSWORD"]})
    finish(client, token, wave1["id"], "failed", {"error": "health check timed out"})
    tick()
    assert next_job(client, token) is None                                   # paused by the failure
    client.post(f"/api/admin/jobs/{wave1['id']}/cancel")
    wave2 = _claim(client, token, "update")
    assert wave2["server_id"] == ids[2] and wave2["payload"]["extra_env"] == {"SMTP_HOST": "mail.example"}
    shown = next(j for j in client.get("/api/admin/jobs").json()["jobs"] if j["id"] == wave2["id"])
    assert shown["wave_total"] == 2 and shown["wave"].startswith(r.json()["run"])
    # one running may predate a change, so it does not count as waiting; a queued one does
    assert client.post("/api/admin/servers/apply-env", json={"server_ids": [ids[2]]}).json()["jobs"] == 1
    assert client.post("/api/admin/servers/apply-env", json={"server_ids": [ids[2]]}).status_code == 409
    assert client.post("/api/admin/servers/apply-env", json={"server_ids": [server(dora)["id"]]}).status_code == 400
    with closing(db.connect()) as conn:
        runs = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'fleet.update' ORDER BY id")]
    assert runs[0].endswith("environment servers=2 waves=2")


# --- every container of a host: the Machines tab ------------------------------------

def docker(name, **over):
    """One entry of a heartbeat's ``docker`` list as an agent 0.3.0 sends it."""
    return {"id": "c" * 64, "name": name, "image": "caddy:2-alpine", "image_id": "sha256:" + "a" * 64,
            "status": "running", "health": "", "created_at": "2026-10-01T08:00:00Z",
            "started_at": "2026-10-05T08:00:00Z", "restarts": 0, "restart_policy": "unless-stopped",
            "memory_mb": 40, "memory_limit_mb": 0, "cpu_pct": 0.3, "image_stale": False, "managed": False,
            "self": False, "compose": None, "ports": [], **over}


def machine():
    """What runs on the VPS: the account project (the account server, the
    share host, Caddy), the demo, the agent itself, a hosted server and the
    container a failed update of the share host kept."""
    project = lambda service: {"project": "gamma-account", "service": service}  # noqa: E731
    return [docker("gamma-account-account-1", image="ghcr.io/tim4431/gamma-cloud:latest", compose=project("account"),
                   ports=["127.0.0.1:9002->9002/tcp"], image_stale=True),
            docker("gamma-account-share-1", image="ghcr.io/tim4431/gamma:sha-a0d31c6", compose=project("share"),
                   image_stale=True),
            docker("gamma-account-share-1-prev", status="exited", compose=project("share"), image_stale=True),
            docker("gamma-account-caddy-1", compose=project("caddy"), ports=["0.0.0.0:443->443/tcp"]),
            docker("gamma-demo", image="ghcr.io/tim4431/gamma:sha-1"),
            docker("gamma-fleet-fleet-1", image="ghcr.io/tim4431/gamma-fleet:latest", self=True, image_stale=True,
                   compose={"project": "gamma-fleet", "service": "fleet"}),
            docker("gamma-alice", image=f"{config.FLEET_IMAGE}:latest", managed=True, image_stale=True)]


def report(client, token, containers=(), entries=None, **host):
    body = {"agent_version": "0.3.0", "memory_mb": 8192, "disk_mb": 500_000, "memory_used_mb": 2048,
            "disk_used_mb": 20_000, "containers": list(containers), **host}
    if entries is not None:
        body["docker"] = entries
    assert client.post("/api/fleet/heartbeat", json=body, headers=bearer(token)).status_code == 200


def stored(host_id):
    with closing(db.connect()) as conn:
        return json.loads(conn.execute("SELECT containers FROM hosts WHERE id = ?", (host_id,)).fetchone()[0])


def test_the_heartbeat_keeps_every_container_checked(client, hosting):
    host_id, token = make_host()
    report(client, token, entries=[
        docker("gamma-account-account-1", compose={"project": "gamma-account", "service": "account", "x": 1},
               ports=["127.0.0.1:9002->9002/tcp", 7], image_stale=True, status="restarting", health="unhealthy"),
        docker("/gamma-fleet-fleet-1", self=True, cpu_pct="high", restarts=-3, status="sleeping", health="meh",
               managed="yes", ports="80", compose="x", image_stale="no", memory_mb=-5, image=["x"]),
        docker("gamma-alice", managed=True),
        docker("gamma-account-account-1", status="dead"),                   # the same name again: the first counts
        {"name": "bad name!"}, {"name": ""}, {"image": "x"}, "junk", 42, docker("x" * 200)])
    [account, agent, alice] = stored(host_id)
    assert (account["name"], account["compose"], account["ports"]) == (
        "gamma-account-account-1", {"project": "gamma-account", "service": "account"}, ["127.0.0.1:9002->9002/tcp"])
    assert (account["status"], account["health"], account["image_stale"], account["cpu_pct"]) == (
        "restarting", "unhealthy", True, 0.3)
    # what an agent sends as nonsense is unknown, not wrong
    assert agent["name"] == "gamma-fleet-fleet-1" and agent["self"] is True
    assert (agent["cpu_pct"], agent["restarts"], agent["status"], agent["health"], agent["managed"], agent["ports"],
            agent["compose"], agent["image_stale"], agent["memory_mb"], agent["image"]) == (
        None, 0, "", "", False, [], None, None, 0, "")
    assert alice["managed"] is True and set(alice) == set(account)
    report(client, token, entries=[docker(f"c-{i}") for i in range(fleet.CONTAINERS_MAX + 5)])
    assert len(stored(host_id)) == fleet.CONTAINERS_MAX
    report(client, token)                                                   # an older agent lists none
    assert stored(host_id) == []


def test_which_containers_are_gammas(monkeypatch):
    """Gamma's by its name, its Compose project or its image's repository,
    or as a hosted server's or the agent's own; anything else the machine
    runs is not."""
    gamma = lambda **c: fleet.is_gamma({"name": "web-1", "image": "nginx:1.27", "compose": None, **c})  # noqa: E731
    assert not gamma()
    assert gamma(name="gamma-demo") and not gamma(name="gamma") and not gamma(name="my-gamma-1")
    assert gamma(compose={"project": "gamma-account", "service": "share"}) and gamma(compose={"project": "gamma"})
    assert not gamma(compose={"project": "nextcloud", "service": "gamma"}) and not gamma(compose="gamma")
    digest = "@sha256:" + "f" * 64
    for image in ("ghcr.io/tim4431/gamma:sha-1", "ghcr.io/tim4431/gamma", "ghcr.io/tim4431/gamma-cloud:latest",
                  "ghcr.io/tim4431/gamma-fleet" + digest, "ghcr.io/tim4431/gamma-fleet:latest" + digest):
        assert gamma(image=image), image
    for image in ("ghcr.io/someone/gamma:1", "caddy:2-alpine", "sha256:" + "a" * 64, "", "registry.example:5000/gamma"):
        assert not gamma(image=image), image
    monkeypatch.setattr(config, "FLEET_IMAGE", "registry.example:5000/me/gamma")      # a fleet image of one's own
    assert gamma(image="registry.example:5000/me/gamma:sha-2") and gamma(image="registry.example:5000/me/gamma")
    assert not gamma(image="registry.example:5000/me/gamma-other:1")
    assert gamma(managed=True) and gamma(self=True) and not gamma(managed="yes", self=1)


def test_the_verdict_is_kept_with_each_container(client, hosting):
    """Stored with the heartbeat (whatever the agent says of it); a list
    kept before that is classified as it is read, for the view and for
    *Update all*."""
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    report(client, token, entries=[docker("gamma-demo", image_stale=True),
                                   docker("watchtower", image="containrrr/watchtower:latest", image_stale=True,
                                          gamma=True)])
    assert [(c["name"], c["gamma"]) for c in stored(host_id)] == [("gamma-demo", True), ("watchtower", False)]
    older = [{k: v for k, v in c.items() if k != "gamma"} for c in stored(host_id)]
    with closing(db.connect()) as conn:
        conn.execute("UPDATE hosts SET containers = ?", (json.dumps(older),))
        conn.commit()
    [m] = client.get("/api/admin/machines").json()["machines"]
    assert [(c["name"], c["gamma"]) for c in m["containers"]] == [("gamma-demo", True), ("watchtower", False)]
    assert (m["others"], m["updates"]) == (1, 1)
    jobs = client.post(f"/api/admin/hosts/{host_id}/update-all").json()["jobs"]
    assert [j["label"] for j in jobs] == ["gamma-demo"]                     # not the other's newer image


def test_a_containers_jobs_and_what_is_refused(client, hosting):
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    report(client, token, entries=machine())
    base = f"/api/admin/hosts/{host_id}/containers"
    r = client.post(f"{base}/gamma-account-share-1/restart")
    job = r.json()["job"]
    assert r.status_code == 200 and (job["kind"], job["server_id"], job["label"], job["state"]) == (
        "container_restart", "", "gamma-account-share-1", "queued")
    assert job["payload"] == {"container": "gamma-account-share-1"}
    assert client.post(f"{base}/gamma-account-share-1/restart").json()["job"]["id"] == job["id"]   # not doubled
    assert next_job(client, token) == {"id": job["id"], "kind": "container_restart", "server_id": "",
                                       "payload": {"container": "gamma-account-share-1"}}
    assert finish(client, token, job["id"], result={"container": "gamma-account-share-1", "status": "running",
                                                    "health": ""}).status_code == 200
    # a log, with the lines asked for, and kept like a server's log
    r = client.post(f"{base}/gamma-account-caddy-1/logs", json={"lines": 5000})
    assert r.json()["job"]["payload"] == {"container": "gamma-account-caddy-1", "lines": 5000}
    r = client.post(f"{base}/gamma-account-caddy-1/logs")
    assert r.json()["job"]["payload"] == {"container": "gamma-account-caddy-1"}
    for lines in (0, 5001, "many"):
        assert client.post(f"{base}/gamma-account-caddy-1/logs", json={"lines": lines}).status_code in (400, 422)
    logs = _claim(client, token, "container_logs")
    lines = [f"2026-10-06T09:00:00.{i:09d}Z " + "x" * 300 for i in range(1000)]
    finish(client, token, logs["id"], result={"container": "gamma-account-caddy-1", "lines": lines, "since": ""})
    kept = json.loads(client.get(f"/api/admin/jobs/{logs['id']}").json()["job"]["result"])
    assert kept["truncated"] is True and fleet.RESULT_MAX < len(json.dumps(kept)) <= fleet.LOGS_RESULT_MAX
    listed = next(j for j in client.get("/api/admin/jobs").json()["jobs"] if j["id"] == logs["id"])
    assert json.loads(listed["result"])["line_count"] == len(kept["lines"])
    # a hosted server's container is changed only through the server; the agent does not stop itself
    for action in ("restart", "start", "stop", "update", "rollback"):
        r = client.post(f"{base}/gamma-alice/{action}")
        assert r.status_code == 409 and "Servers tab" in r.json()["detail"], action
    assert client.post(f"{base}/gamma-alice/logs").status_code == 200   # reading it is fine
    assert client.post(f"{base}/gamma-fleet-fleet-1/stop").status_code == 409
    assert client.post(f"{base}/gamma-fleet-fleet-1/restart").status_code == 200
    assert client.post(f"{base}/nobody/restart").status_code == 404
    assert client.post("/api/admin/hosts/h_nope/containers/gamma-demo/restart").status_code == 404
    assert client.post(f"{base}/gamma-demo/explode").status_code == 404
    with closing(db.connect()) as conn:
        audit = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'fleet.container' ORDER BY id")]
    assert audit[0] == f"{host_id} gamma-account-share-1 container_restart" and len(audit) == 5   # none refused
    # a failed one is retried like any job, and taken again
    stop = client.post(f"{base}/gamma-demo/stop").json()["job"]
    for j in [next_job(client, token) for _ in range(4)]:
        finish(client, token, j["id"], *(("failed", {"error": "no such container"}) if j["id"] == stop["id"] else ()))
    assert client.post(f"/api/admin/jobs/{stop['id']}/retry").json()["job"]["state"] == "queued"
    assert next_job(client, token)["id"] == stop["id"]


def test_the_agents_own_container(client, hosting):
    """The agent's own update only starts a helper and is done at once, so
    its row says so until the next heartbeat. Its -prev is never started
    beside it, nor it beside its -prev when a failed update left the old
    agent running under that name."""
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    entries = machine() + [docker("gamma-fleet-fleet-1-prev", status="exited", image="ghcr.io/tim4431/gamma-fleet:old")]
    report(client, token, entries=entries)
    base = f"/api/admin/hosts/{host_id}/containers"
    r = client.post(f"{base}/gamma-fleet-fleet-1-prev/start")
    assert r.status_code == 409 and "second agent" in r.json()["detail"]
    own = lambda: next(c for c in client.get("/api/admin/machines").json()["machines"][0]["containers"]  # noqa: E731
                       if c["self"])
    assert own()["helper"] == ""
    with closing(db.connect()) as conn:                                     # the last heartbeat, a minute ago
        conn.execute("UPDATE hosts SET last_seen_at = ?", (db.after(-60),))
        conn.commit()
    client.post(f"{base}/gamma-fleet-fleet-1/update")
    finish(client, token, _claim(client, token, "container_update")["id"],
           result={"container": "gamma-fleet-fleet-1", "image": "ghcr.io/tim4431/gamma-fleet:latest",
                   "note": "a helper replaces the agent"})
    assert own()["helper"] == "container_update"
    report(client, token, entries=entries)                                  # the new agent's first heartbeat
    assert own()["helper"] == ""
    # the helper gave up: the old agent runs again as -prev, the new one is stopped under the name
    flipped = [e for e in machine() if not e["self"]] + [
        docker("gamma-fleet-fleet-1", status="exited"), docker("gamma-fleet-fleet-1-prev", self=True)]
    report(client, token, entries=flipped)
    assert client.post(f"{base}/gamma-fleet-fleet-1/start").status_code == 409
    assert client.post(f"{base}/gamma-fleet-fleet-1/rollback").status_code == 200
    assert client.post(f"{base}/gamma-demo/start").status_code == 200      # any other container starts


def test_a_done_update_or_rollback_says_what_is_known_of_the_image(client, hosting):
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    report(client, token, entries=machine())
    base = f"/api/admin/hosts/{host_id}/containers/gamma-account-share-1"
    stale = lambda: {c["name"]: c for c in stored(host_id)}["gamma-account-share-1"]["image_stale"]  # noqa: E731
    job = client.post(f"{base}/update").json()["job"]
    finish(client, token, _claim(client, token, "container_update")["id"], "failed", {"error": "health check"})
    assert stale() is True                                                    # a failure changes nothing
    client.post(f"/api/admin/jobs/{job['id']}/retry")
    finish(client, token, _claim(client, token, "container_update")["id"],
           result={"container": "gamma-account-share-1", "image": "ghcr.io/tim4431/gamma:sha-a0d31c6"})
    assert stale() is False                                                   # until the next heartbeat
    client.post(f"{base}/rollback")
    finish(client, token, _claim(client, token, "container_rollback")["id"])
    assert stale() is None
    report(client, token, entries=machine())
    assert stale() is True


def test_update_all_takes_the_newer_images_and_the_agents_own_last(client, hosting):
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    report(client, token, entries=machine() + [docker("watchtower", image="containrrr/watchtower", image_stale=True)])
    [m] = client.get("/api/admin/machines").json()["machines"]
    assert (m["updates"], m["agent_stale"]) == (3, True)    # not alice's, the kept share-1-prev's nor the other's
    r = client.post(f"/api/admin/hosts/{host_id}/update-all")
    jobs = r.json()["jobs"]
    assert r.status_code == 200 and [(j["label"], j["state"]) for j in jobs] == [
        ("gamma-account-account-1", "queued"), ("gamma-account-share-1", "queued"), ("gamma-fleet-fleet-1", "held")]
    assert len({j["wave"].partition("/")[0] for j in jobs}) == 1 and jobs[2]["wave"].endswith("/002")
    again = client.post(f"/api/admin/hosts/{host_id}/update-all").json()["jobs"]
    assert [j["id"] for j in again] == [j["id"] for j in jobs]               # a second click doubles nothing
    first = {j["payload"]["container"]: j for j in (next_job(client, token), next_job(client, token))}
    assert set(first) == {"gamma-account-account-1", "gamma-account-share-1"}
    assert next_job(client, token) is None                                  # the agent's waits for the others
    finish(client, token, first["gamma-account-account-1"]["id"])
    finish(client, token, first["gamma-account-share-1"]["id"], "failed", {"error": "pull failed"})
    assert next_job(client, token) is None                                  # a failure holds it too
    client.post(f"/api/admin/jobs/{first['gamma-account-share-1']['id']}/cancel")
    own = next_job(client, token)
    assert own["payload"] == {"container": "gamma-fleet-fleet-1"}
    finish(client, token, own["id"], result={"container": "gamma-fleet-fleet-1", "note": "a helper replaces the agent"})
    [m] = client.get("/api/admin/machines").json()["machines"]
    assert (m["agent_stale"], m["updates"]) == (False, 1)                   # the share host's is still to do
    [share] = client.post(f"/api/admin/hosts/{host_id}/update-all").json()["jobs"]
    assert (share["label"], share["state"]) == ("gamma-account-share-1", "queued")
    finish(client, token, _claim(client, token, "container_update")["id"])
    r = client.post(f"/api/admin/hosts/{host_id}/update-all")
    assert r.status_code == 409 and "newer image" in r.json()["detail"]
    assert client.post("/api/admin/hosts/h_nope/update-all").status_code == 404


def test_a_hosts_token_is_rotated(client, hosting):
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    r = client.post(f"/api/admin/hosts/{host_id}/token")
    new = r.json()["token"]
    assert r.status_code == 200 and new.startswith("gf_") and new != token and f"--token {new}" in r.json()["bootstrap"]
    assert "token_hash" not in r.json()["host"]
    assert client.get("/api/fleet/jobs", headers=bearer(token)).status_code == 401
    assert next_job(client, new) is None
    assert client.post("/api/admin/hosts/h_nope/token").status_code == 404
    with closing(db.connect()) as conn:
        [detail] = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'fleet.host_token'")]
    assert detail == f"{host_id} vps-1" and new not in detail


def test_a_host_is_removed_once_no_server_is_on_it(client, hosting):
    register(client, "operator")
    make_admin("operator")
    host_id, token = make_host()
    alice = make_account("alice", "plus")
    report(client, token, entries=machine())
    failed = client.post(f"/api/admin/hosts/{host_id}/containers/gamma-demo/restart").json()["job"]
    finish(client, token, next_job(client, token)["id"])                    # alice's create
    finish(client, token, _claim(client, token, "container_restart")["id"], "failed", {"error": "x"})
    r = client.delete(f"/api/admin/hosts/{host_id}")
    assert r.status_code == 409 and "1 server" in r.json()["detail"]
    client.post(f"/api/admin/servers/{server(alice)['id']}/delete")        # its delete job waits
    assert client.delete(f"/api/admin/hosts/{host_id}").json() == {"ok": True}
    assert client.get("/api/admin/hosts").json()["hosts"] == []
    assert client.get("/api/fleet/jobs", headers=bearer(token)).status_code == 401
    assert {r["state"] for r in jobs_of(alice) if r["kind"] == "delete"} == {"canceled"}
    assert job_row(failed["id"])["state"] == "canceled"                      # its alert does not linger
    assert client.delete(f"/api/admin/hosts/{host_id}").status_code == 404
    with closing(db.connect()) as conn:
        detail = conn.execute("SELECT detail FROM audit WHERE event = 'fleet.host_remove'").fetchone()[0]
    assert detail == f"{host_id} vps-1"


def test_the_machines_view(client, hosting):
    me = register(client, "operator")
    host_id, token = make_host()
    alice = make_account("alice", "plus")
    finish(client, token, next_job(client, token)["id"])
    entries = machine() + [docker("gamma-alice-prev", managed=True, status="exited"),
                           docker("gamma-ghost", managed=True), docker("watchtower", image_stale=True),
                           docker("nextcloud-app-1", image="nextcloud:29", compose={"project": "nextcloud",
                                                                                    "service": "app"})]
    report(client, token, containers=[{"label": "alice", "running": True}, {"label": "ghost", "running": True}],
           entries=entries)
    assert client.get("/api/admin/machines").status_code == 403
    make_admin("operator")
    d = client.get("/api/admin/machines").json()
    [m] = d["machines"]
    assert d["default_image"] == fleet.default_image() and m["id"] == host_id and "token_hash" not in m
    cs = {c["name"]: c for c in m["containers"]}
    assert len(cs) == len(entries)
    sid = server(alice)["id"]
    assert cs["gamma-alice"]["server"] == {"id": sid, "label": "alice", "state": "running", "username": "alice"}
    assert (cs["gamma-alice"]["orphan"], cs["gamma-alice"]["kept"]) == (False, False)
    assert cs["gamma-alice-prev"]["kept"] is True and cs["gamma-alice-prev"]["server"]["id"] == sid
    ghost = cs["gamma-ghost"]
    assert (ghost["orphan"], ghost["server"], ghost["label"]) == (True, None, "ghost")
    assert cs["gamma-account-share-1-prev"]["kept"] is True and "server" not in cs["gamma-account-share-1"]
    assert {n for n, c in cs.items() if not c["gamma"]} == {"watchtower", "nextcloud-app-1"} and m["others"] == 2
    assert (m["agent_stale"], m["updates"], m["servers"], m["cpu_pct"]) == (True, 3, 1, None)   # it sent no CPU
    assert [j["kind"] for j in m["jobs"]] == ["create"]
    for _ in range(25):
        client.post(f"/api/admin/hosts/{host_id}/containers/gamma-demo/logs")
    [m] = client.get("/api/admin/machines").json()["machines"]
    assert len(m["jobs"]) == 20 and m["jobs"][0]["kind"] == "container_logs"
    # the orphan goes through the orphan endpoint, as before
    r = client.post(f"/api/admin/hosts/{host_id}/orphans/ghost/remove")
    assert r.status_code == 200 and r.json()["job"]["payload"] == {"label": "ghost", "account_id": ""}
    page = client.get("/admin").text
    assert page.index("data-tab=clients") < page.index("data-tab=machines") < page.index("data-tab=servers")
    assert "id=tab-machines" in page and "loadMachines" in page and "fleetUI" in page and "Update all" in page
    assert me


def test_step_12_adds_the_containers_column():
    with closing(db.connect()) as conn:
        conn.execute("ALTER TABLE hosts DROP COLUMN containers")
        conn.execute("PRAGMA user_version = 11")
        conn.commit()
    assert steps_after(11)[0] == "fleet_containers"
    assert db.ensure_current() == steps_after(11)
    with closing(db.connect()) as conn:
        assert "containers" in [r[1] for r in conn.execute("PRAGMA table_info(hosts)")]
        host, _ = fleet.add_host(conn, "vps-1")
        assert host["containers"] == []


def test_step_10_adds_the_orphans_column():
    with closing(db.connect()) as conn:
        conn.execute("ALTER TABLE hosts DROP COLUMN orphans")
        conn.execute("PRAGMA user_version = 9")
        conn.commit()
    assert steps_after(9)[:2] == ["fleet_orphans", "operations"]               # later steps follow
    assert db.ensure_current() == steps_after(9)
    with closing(db.connect()) as conn:
        host, _ = fleet.add_host(conn, "vps-1")
        assert host["orphans"] == []


def test_step_11_adds_the_operations_columns_and_tables():
    """Back to the shape of step 10, with an invite made then: the upgrade
    adds every column and table, and the code's total is what was left."""
    added = {"invites": ("uses_total", "expires_at", "disabled", "grant_days"),
             "accounts": ("invite_code", "granted_until"), "hosts": ("public_ip",),
             "hosted_servers": ("overrides", "env", "dns_record_id", "dns_target")}
    with closing(db.connect()) as conn:
        for table, columns in added.items():
            for column in columns:
                conn.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
        conn.execute("DROP TABLE alerts")
        conn.execute("DROP TABLE metrics")
        conn.execute("INSERT INTO invites (code, uses_left, plan, created_at) VALUES ('old', 3, 'free', ?)", (db.now(),))
        conn.execute("PRAGMA user_version = 10")
        conn.commit()
    assert steps_after(10)[0] == "operations"
    assert db.ensure_current() == steps_after(10)
    with closing(db.connect()) as conn:
        for table, columns in added.items():
            have = [r[1] for r in conn.execute(f"PRAGMA table_info({table})")]
            assert all(column in have for column in columns), table
        invite = conn.execute("SELECT * FROM invites WHERE code = 'old'").fetchone()
        assert invite["uses_total"] == 3 and invite["disabled"] == 0 and invite["expires_at"] is None
        assert conn.execute("SELECT COUNT(*) FROM alerts").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM metrics").fetchone()[0] == 0


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
    assert s["username"] == "bob" and s["url"] == f"https://bob-user.{DOMAIN}" and s["jobs"] == {"queued": 1}

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
    assert "vps-1" in out and "committed=0MB" in out and "containers=0 updates=0" in out
    with closing(db.connect()) as conn:
        host = conn.execute("SELECT * FROM hosts WHERE name = 'vps-2'").fetchone()
        fleet.heartbeat(conn, dict(host), {"memory_mb": 8192, "disk_mb": 500_000, "docker": machine()})
        conn.commit()
    manage.main(["hosts"])
    assert "containers=7 updates=3" in capsys.readouterr().out
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
    assert "outdated" not in out and "own=" not in out
    from gammacloud import hosted
    with closing(db.connect()) as conn:
        hosted.set_overrides(conn, conn.execute("SELECT id FROM hosted_servers").fetchone()[0], {"memory_mb": 2048}, "cli")
        conn.commit()
    set_setting("fleet_image_tag", "sha-new")
    manage.main(["servers"])
    out = capsys.readouterr().out
    assert "mem=2048MB" in out and "(outdated: tag)" in out and "own=memory_mb=2048" in out
    manage.main(["jobs"])
    assert "create" in capsys.readouterr().out

"""Backups as background jobs (routers/ws_backups.py, routers/admin.py):
snapshots of one or several workspaces, a snapshot restored or merged in
place, the whole-server snapshot, what each refuses — and that a restore
can no longer be stopped once it swaps the databases."""

import threading

import pytest

from conftest import login, make_page, make_user, workspace_of


@pytest.fixture(scope="module")
def owner(client):
    make_user("bj_owner", "bj-owner-pw")
    return login("bj_owner", "bj-owner-pw")


@pytest.fixture(scope="module")
def other(client):
    make_user("bj_other", "bj-other-pw")
    return login("bj_other", "bj-other-pw")


@pytest.fixture(scope="module")
def boss(client):
    make_user("bj_boss", "bj-boss-pw", is_admin=1)
    return login("bj_boss", "bj-boss-pw")


def _job(c, route, body):
    from gamma import jobs
    r = c.post(f"/api/jobs/{route}", json=body)
    assert r.status_code == 200, r.text
    return jobs.wait(r.json()["id"])


def test_a_snapshot_job_of_one_workspace(owner):
    from gamma import ws_backup

    ws = workspace_of("bj_owner")
    make_page(owner, "Before the snapshot")
    job = _job(owner, "snapshot", {"workspaces": [ws], "uploads": False, "label": "job-test"})
    assert job["state"] == "done", job["error"]
    assert job["kind"] == "snapshot" and job["workspace"] == ws and job["params"]["names"]
    made = job["result"]["snapshots"]
    assert len(made) == 1 and made[0]["workspace"] == ws and made[0]["label"] == "job-test"
    assert any(b["name"] == made[0]["name"] for b in ws_backup.list_backups(ws))
    assert job["progress"]["unit"] == "bytes" and job["progress"]["done"] == job["progress"]["total"]


def test_a_snapshot_job_of_several_workspaces_goes_on_past_a_full_store(owner, monkeypatch):
    from gamma import ws_backup

    first = workspace_of("bj_owner")
    second = owner.post("/api/workspaces", json={"name": "BJ second"}).json()["id"]
    real = ws_backup.create

    def full_for_first(ws, **kwargs):
        if ws == first:
            raise ws_backup.BackupError("this workspace already has 20 backups — delete one first")
        return real(ws, **kwargs)

    monkeypatch.setattr(ws_backup, "create", full_for_first)
    job = _job(owner, "snapshot", {"workspaces": [first, second], "uploads": False})
    assert job["state"] == "done" and job["workspace"] == ""
    assert [s["workspace"] for s in job["result"]["snapshots"]] == [second]
    assert job["result"]["failed"][0]["workspace"] == first
    assert job["progress"] == {"done": 2, "total": 2, "unit": "workspaces", "item": ""}
    only_first = _job(owner, "snapshot", {"workspaces": [first], "uploads": False})
    assert only_first["state"] == "failed" and "delete one first" in only_first["error"]


def test_restoring_a_snapshot_as_a_job(owner):
    from gamma import ws_backup

    ws = workspace_of("bj_owner")
    snapshot = _job(owner, "snapshot", {"workspaces": [ws], "uploads": True, "label": "restore-me"})
    name = snapshot["result"]["snapshots"][0]["name"]
    make_page(owner, "Made after the snapshot")
    job = _job(owner, "restore-snapshot", {"ws": ws, "name": name, "mode": "replace"})
    assert job["state"] == "done", job["error"]
    assert job["params"]["source"] == "snapshot" and job["params"]["snapshot"] == name
    assert job["result"]["mode"] == "replace" and job["result"]["pre_restore"]
    assert job["progress"]["phase"] == "restoring" and not job["stoppable"]
    titles = [b["content"] for b in owner.get("/api/blocks/root/children").json()["children"]]
    assert "Made after the snapshot" not in titles
    assert any(b["name"] == job["result"]["pre_restore"] for b in ws_backup.list_backups(ws))


def test_a_restore_cannot_be_stopped_once_it_swaps_the_databases(owner, monkeypatch):
    from gamma import jobs, ws_backup

    ws = workspace_of("bj_owner")
    name = _job(owner, "snapshot", {"workspaces": [ws], "uploads": False})["result"]["snapshots"][0]["name"]
    reached, release = threading.Event(), threading.Event()
    real = ws_backup._merge

    def held(*args, **kwargs):
        reached.set()
        release.wait(10)
        return real(*args, **kwargs)

    monkeypatch.setattr(ws_backup, "_merge", held)
    started = owner.post("/api/jobs/restore-snapshot", json={"ws": ws, "name": name, "mode": "merge"}).json()
    assert reached.wait(10)
    assert owner.post(f"/api/jobs/{started['id']}/cancel").status_code == 409
    release.set()
    assert jobs.wait(started["id"])["state"] == "done"


def test_what_the_backup_jobs_refuse(owner, other):
    ws = workspace_of("bj_owner")
    assert other.post("/api/jobs/snapshot", json={"workspaces": [ws]}).status_code == 404
    assert owner.post("/api/jobs/snapshot", json={"workspaces": []}).status_code == 400
    assert owner.post("/api/jobs/snapshot", json={"workspaces": [ws], "label": "no spaces"}).status_code == 400
    assert owner.post("/api/jobs/restore-snapshot", json={"ws": ws, "name": "20990101-000000-x"}).status_code == 404
    assert owner.post("/api/jobs/restore-snapshot", json={"ws": ws, "name": "x", "mode": "swap"}).status_code == 400
    assert other.post("/api/jobs/workspace-export", json={"ws": ws}).status_code == 404
    assert other.post("/api/jobs/restore", data={"mode": "merge", "ws": ws},
                      files={"file": ("x.zip", b"PK", "application/zip")}).status_code == 404


def test_the_server_snapshot_job_is_for_admins(owner, boss):
    from gamma import backups

    assert owner.post("/api/jobs/server-backup", json={"label": "srv"}).status_code == 403
    assert boss.post("/api/jobs/server-backup", json={"label": "bad label"}).status_code == 400
    job = _job(boss, "server-backup", {"label": "srv-job", "uploads": False})
    assert job["state"] == "done", job["error"]
    assert job["result"]["label"] == "srv-job" and backups.info(job["result"]["name"])
    assert job["progress"]["phase"] == "databases"
    backups.delete(job["result"]["name"])

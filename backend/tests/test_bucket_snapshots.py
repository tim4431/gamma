"""The backup tasks' copies in the bucket (gamma/bucket_snapshots.py,
gamma/backup_schedule.py, docs/dev/debugging.md "Snapshots and copies in a
bucket") against moto's in-process bucket (skipped without moto): a task
copying each snapshot it takes and pruning its copies to its retention,
never another task's; who may copy and with what bucket; a copy that fails
leaving the snapshots and the schedule; and the admin's listing, download,
restore (into the workspace, or as a new one after a lost disk) and delete
through the API."""

import json

import pytest

from conftest import S3_TEST_BUCKET as BUCKET, account_of, login, make_page, make_user, workspace_of
from gamma import backup_schedule as tasks, bucket_snapshots, db, offsite, s3, workspaces, ws_backup
from gamma.routers.backup_tasks import TaskInput

PREFIX = "bs"


@pytest.fixture(autouse=True)
def _no_environment(monkeypatch):
    for var in ("GAMMA_S3_BUCKET", "GAMMA_S3_ENDPOINT", "GAMMA_S3_REGION", "GAMMA_S3_ACCESS_KEY",
                "GAMMA_S3_SECRET_KEY", "GAMMA_S3_PREFIX", "GAMMA_OFFSITE"):
        monkeypatch.delenv(var, raising=False)


def _forget_bucket():
    with db.connect_users_db() as conn:
        conn.execute("DELETE FROM settings WHERE key = 'offsite'")
        conn.commit()


@pytest.fixture
def bucket(client, s3_bucket):
    """The bucket saved in Settings → Backups: moto's, under ``bs/``; the
    raw boto3 client. Forgotten after the test."""
    offsite.save({"bucket": BUCKET, "region": "us-east-1", "access_key": "testing", "secret_key": "testing",
                  "prefix": PREFIX})
    yield s3_bucket
    _forget_bucket()


@pytest.fixture
def scheduler(tmp_path, monkeypatch):
    """Tasks and snapshots in the test's own backups/; run_due run by hand."""
    monkeypatch.setattr(tasks.config, "BACKUPS_DIR", tmp_path / "backups")
    monkeypatch.setattr(tasks, "_wake", lambda: None)


def _keys(raw, ws):
    listed = raw.list_objects_v2(Bucket=BUCKET, Prefix=f"{PREFIX}/snapshots/{ws}/")
    return sorted(o["Key"].rsplit("/", 1)[1] for o in listed.get("Contents", ()))


def _run(owner, task):
    tasks.mutate(owner, task["id"], "run")
    tasks.run_due()
    return tasks.read(task["id"])


def _task(owner, ws, **changes):
    data = TaskInput(**{"name": "To the bucket", "workspaces": [ws], "uploads": False, "bucket": True,
                        **changes}).model_dump()
    return tasks.save(owner, data)


def test_a_task_copies_its_snapshots_and_prunes_them_to_its_retention(bucket, scheduler):
    ws = make_user("bs_admin", "bs-admin-pw", is_admin=1)
    owner = account_of("bs_admin")
    task = _task(owner, ws, retention_mode="count", retention_value=2)
    other = _task(owner, ws, retention_mode="count", retention_value=5)
    plain = _task(owner, ws, bucket=False)
    assert _run(owner, other)["state"] == "finished"
    for _ in range(3):
        done = _run(owner, task)
        assert done["state"] == "finished", done["last_error"]
        assert done["last_result"]["bucket"] == 1
    assert "bucket" not in _run(owner, plain)["last_result"]  # a task without it sends nothing

    mine = sorted(b["name"] + ".zip" for b in ws_backup.list_backups(ws) if b["task_id"] == task["id"])
    others = [b["name"] + ".zip" for b in ws_backup.list_backups(ws) if b["task_id"] == other["id"]]
    assert len(mine) == 2 and len(others) == 1
    # the task's two newest, the other task's one (its retention, not this one's), and the name file
    assert _keys(bucket, ws) == sorted(mine + others + ["workspace.json"])
    about = json.loads(bucket.get_object(Bucket=BUCKET, Key=f"{PREFIX}/snapshots/{ws}/workspace.json")["Body"].read())
    assert about == {"id": ws, "name": workspaces.get(ws)["name"], "kind": "personal"}
    copy = bucket.get_object(Bucket=BUCKET, Key=f"{PREFIX}/snapshots/{ws}/{mine[0]}")["Body"].read()
    assert copy == (ws_backup.store_dir(ws) / mine[0]).read_bytes()


def test_who_may_copy_to_the_bucket(client, s3_bucket, scheduler):
    _forget_bucket()
    ws = make_user("bs_admin2", "bs-admin2-pw", is_admin=1)
    with pytest.raises(tasks.TaskError, match="No bucket is set up"):
        _task(account_of("bs_admin2"), ws)
    offsite.save({"bucket": BUCKET, "region": "us-east-1", "access_key": "testing", "secret_key": "testing"})
    try:
        member = make_user("bs_member", "bs-member-pw")
        with pytest.raises(tasks.TaskError, match="Only a server admin"):
            _task(account_of("bs_member"), member)
        assert tasks.save(account_of("bs_member"), TaskInput(name="Here only", workspaces=[member]).model_dump())[
            "bucket"] is False
        # paused, a task that can no longer copy still saves (as with a lost workspace)
        task = _task(account_of("bs_admin2"), ws)
        _forget_bucket()
        assert tasks.save(account_of("bs_admin2"), {**TaskInput(name="Paused", workspaces=[ws], bucket=True,
                                                                 enabled=False).model_dump()}, task["id"])
    finally:
        _forget_bucket()


def test_a_copy_that_fails_leaves_the_snapshots_and_the_schedule(bucket, scheduler, monkeypatch):
    ws = make_user("bs_admin3", "bs-admin3-pw", is_admin=1)
    owner = account_of("bs_admin3")
    task = _task(owner, ws)

    def refused(*_args):
        raise s3.S3Error("PUT snapshots/x in s3://bucket/: AccessDenied")
    monkeypatch.setattr(bucket_snapshots, "upload", refused)
    done = _run(owner, task)
    assert done["state"] == "failed" and done["next_run"] == task["next_run"]  # no retry an hour on
    assert done["last_error"].startswith("The snapshots were taken, but copying them to the bucket failed")
    assert "AccessDenied" in done["last_error"]
    assert [b for b in ws_backup.list_backups(ws) if b["task_id"] == task["id"]]
    assert _keys(bucket, ws) == []


# --- the admin's listing, download, restore and delete -------------------------------

def _job(c, route, body):
    from gamma import jobs
    r = c.post(f"/api/jobs/{route}", json=body)
    assert r.status_code == 200, r.text
    return jobs.wait(r.json()["id"])


def test_the_copies_through_the_api(bucket):
    make_user("bs_boss", "bs-boss-pw", is_admin=1)
    make_user("bs_user", "bs-user-pw")
    boss, user = login("bs_boss", "bs-boss-pw"), login("bs_user", "bs-user-pw")
    ws = workspace_of("bs_boss")
    make_page(boss, "In the copy")
    snap = ws_backup.create(ws, label="task-0123456789ab", uploads=False, scheduled=True,
                            task_id="0123456789ab" + "0" * 20)
    bucket_snapshots.upload(bucket_snapshots.client(), ws, snap["name"])
    # the copy of a workspace this server does not have: a lost disk
    gone = workspaces.new_workspace_id()
    zipped = (ws_backup.store_dir(ws) / f"{snap['name']}.zip").read_bytes()
    bucket.put_object(Bucket=BUCKET, Key=f"{PREFIX}/snapshots/{gone}/{snap['name']}.zip", Body=zipped)
    bucket.put_object(Bucket=BUCKET, Key=f"{PREFIX}/snapshots/{gone}/workspace.json", Body=b'{"name": "Gone"}')

    groups = boss.get("/api/admin/offsite/snapshots").json()["groups"]
    assert [(g["workspace"], g["here"]) for g in groups] == [(ws, True), (gone, False)]
    assert groups[1]["name"] == "Gone"
    assert [(c["name"], c["task"], c["size_bytes"]) for c in groups[0]["snapshots"]] == [
        (snap["name"], "0123456789ab", len(zipped))]
    for route, body in (("bucket-download", {"ws": ws, "name": snap["name"]}),
                        ("bucket-restore", {"ws": ws, "name": snap["name"]})):
        assert user.post(f"/api/jobs/{route}", json=body).status_code == 403
    assert boss.post("/api/jobs/bucket-download", json={"ws": ws, "name": "../x"}).status_code == 404
    r = boss.post("/api/jobs/bucket-restore", json={"ws": gone, "name": snap["name"]})
    assert r.status_code == 404 and "as a new one" in r.json()["detail"]

    job = _job(boss, "bucket-download", {"ws": ws, "name": snap["name"]})
    assert job["state"] == "done", job["error"]
    assert boss.get(f"/api/jobs/{job['id']}/download").content == zipped

    before = {b["name"] for b in ws_backup.list_backups(ws)}
    job = _job(boss, "bucket-restore", {"ws": ws, "name": snap["name"], "mode": "replace"})
    assert job["state"] == "done", job["error"]
    assert job["params"]["source"] == "bucket" and job["workspace"] == ws
    assert any(b["auto"] for b in ws_backup.list_backups(ws) if b["name"] not in before)  # the "Before restore"

    job = _job(boss, "bucket-restore", {"ws": gone, "name": snap["name"], "into": "new"})
    assert job["state"] == "done", job["error"]
    assert job["result"]["workspace"] == gone  # the copy's own id, free here
    made = workspaces.get(gone)
    assert made["name"] == workspaces.get(ws)["name"] and workspaces.role_of(gone, account_of("bs_boss")) == "owner"
    assert [g["here"] for g in boss.get("/api/admin/offsite/snapshots").json()["groups"]] == [True, True]

    assert boss.delete(f"/api/admin/offsite/snapshots/{ws}/{snap['name']}").json() == {"ok": True}
    assert _keys(bucket, ws) == ["workspace.json"]
    assert boss.delete(f"/api/admin/offsite/snapshots/{ws}/not-a-name").status_code == 404
    _forget_bucket()
    assert boss.get("/api/admin/offsite/snapshots").status_code == 400

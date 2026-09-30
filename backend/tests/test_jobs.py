"""Background jobs (gamma/jobs.py, routers/jobs.py): a job's lifecycle,
progress, stop and failure, the running limits, who sees and controls what,
the files jobs write, and the passes that end them."""

import json
import os
import sqlite3
import threading
import time
from contextlib import closing

import pytest
from fastapi import HTTPException

from conftest import login, make_user, workspace_of


@pytest.fixture(scope="module")
def alice(client):
    make_user("jb_alice", "jb-alice-pw")
    return login("jb_alice", "jb-alice-pw")


@pytest.fixture(scope="module")
def bob(client):
    make_user("jb_bob", "jb-bob-pw")
    return login("jb_bob", "jb-bob-pw")


@pytest.fixture(scope="module")
def cara(client):
    make_user("jb_cara", "jb-cara-pw")
    return login("jb_cara", "jb-cara-pw")


@pytest.fixture(scope="module")
def lab(client, alice, bob, cara):
    """A shared workspace: alice owns it, cara edits, bob views."""
    make_user("jb_boss", "jb-boss-pw", is_admin=1)
    boss = login("jb_boss", "jb-boss-pw")
    ws = boss.post("/api/workspaces", json={"name": "Jobs lab", "kind": "shared", "owner": "jb_alice"}).json()["id"]
    assert alice.put(f"/api/workspaces/{ws}/members/jb_cara", json={"role": "editor"}).status_code == 200
    assert alice.put(f"/api/workspaces/{ws}/members/jb_bob", json={"role": "viewer"}).status_code == 200
    return ws


def _in(ws):
    return {"X-Gamma-Workspace": ws}


class Gate:
    """A run that holds where the test says: ``started`` once it runs (after
    reporting ``before``), then waits for ``release()``, reports once more (a
    stop lands there) and returns ``result``."""

    def __init__(self, result=None, before=None, stoppable=True):
        self.started, self.go = threading.Event(), threading.Event()
        self.result, self.before, self.stoppable = result, before or {}, stoppable

    def __call__(self, job):
        job.progress(**self.before, **({} if self.stoppable else {"stoppable": False}))
        self.started.set()
        assert self.go.wait(10), "the test never released the job"
        job.progress(done=2, total=2)
        return self.result

    def release(self):
        self.go.set()


def _start(owner, run, **kwargs):
    from gamma import jobs
    return jobs.start(kwargs.pop("kind", "test"), owner=owner, run=run, **kwargs)


def _finished(job_id):
    from gamma import jobs
    job = jobs.wait(job_id)
    assert job["state"] in ("done", "failed", "cancelled"), job
    return job


def test_a_job_reports_while_it_runs_and_keeps_its_result(alice):
    gate = Gate(result={"made": 3}, before={"done": 1, "total": 2, "unit": "items", "item": "First"})
    job = _start("jb_alice", gate, ws=workspace_of("jb_alice"), title="A test job", params={"x": 1})
    assert job["state"] in ("queued", "running") and job["params"] == {"x": 1} and "result" not in job
    assert gate.started.wait(10)
    listed = next(j for j in alice.get("/api/jobs").json()["jobs"] if j["id"] == job["id"])
    assert listed["state"] == "running" and listed["stoppable"] and listed["started_at"]
    assert listed["progress"] == {"done": 1, "total": 2, "unit": "items", "item": "First"}
    assert "result" not in listed  # a listing stays small
    gate.release()
    done = _finished(job["id"])
    assert done["state"] == "done" and done["result"] == {"made": 3} and done["finished_at"]
    assert done["progress"] == {"done": 2, "total": 2, "unit": "items", "item": "First"}
    assert not done["stoppable"] and done["artifact"] is None
    assert alice.get(f"/api/jobs/{job['id']}").json()["result"] == {"made": 3}


def test_a_new_phase_or_unit_starts_its_own_counts():
    seen = []

    def run(job):
        job.progress(done=3, total=5, unit="files", item="a.pdf")
        seen.append(job._live.progress)
        job.progress(phase="checking")
        seen.append(job._live.progress)
        job.progress(unit="bytes", done=10)
        seen.append(job._live.progress)
        job.progress(item="")
        seen.append(job._live.progress)

    _finished(_start("jb_alice", run)["id"])
    assert seen == [{"done": 3, "total": 5, "unit": "files", "item": "a.pdf"}, {"phase": "checking"},
                    {"phase": "checking", "unit": "bytes", "done": 10},
                    {"phase": "checking", "unit": "bytes", "done": 10, "item": ""}]


@pytest.mark.parametrize("raised, message", [
    (HTTPException(status_code=400, detail="not a zip file"), "not a zip file"),
    (ValueError("the backup is damaged"), "the backup is damaged"),
    (RuntimeError("boom"), "unexpected error (RuntimeError: boom)"),
])
def test_a_failing_run_fails_the_job_with_its_message(raised, message):
    def run(job):
        raise raised

    job = _finished(_start("jb_alice", run)["id"])
    assert job["state"] == "failed" and job["error"] == message and job["result"] is None


def test_a_file_the_job_wrote_is_its_download(alice, bob):
    from gamma import jobs

    def run(job):
        job.artifact_path.write_bytes("résumé\n".encode("utf-8"))
        job.set_artifact("Übersicht résumé.txt", "text/plain")
        return {"ok": True}

    job = _finished(_start("jb_alice", run, ws=workspace_of("jb_alice"), artifact=True)["id"])
    assert job["artifact"] == {"name": "Übersicht résumé.txt", "type": "text/plain", "size": len("résumé\n".encode())}
    assert not job["downloaded"]
    assert bob.get(f"/api/jobs/{job['id']}/download").status_code == 404
    r = alice.get(f"/api/jobs/{job['id']}/download")
    assert r.status_code == 200 and r.content == "résumé\n".encode("utf-8")
    assert "filename*=UTF-8''%C3%9Cbersicht%20r%C3%A9sum%C3%A9.txt" in r.headers["content-disposition"]
    assert jobs.get(job["id"])["downloaded"]

    def forgot(job):
        job.set_artifact("never-written.zip")

    missing = _finished(_start("jb_alice", forgot, artifact=True)["id"])
    assert missing["state"] == "failed" and "without writing its file" in missing["error"]
    assert not (jobs.root() / missing["id"]).exists()


def test_a_job_never_reads_as_ended_before_its_row_says_how(monkeypatch):
    """Between the worker's last write and its leaving the live set, a
    reader sees the job still running — never done without its file."""
    from gamma import jobs

    real, written, go = jobs._update, threading.Event(), threading.Event()

    def slow_final_write(job_id, **fields):
        real(job_id, **fields)
        if "finished_at" in fields:
            written.set()
            go.wait(10)

    monkeypatch.setattr(jobs, "_update", slow_final_write)

    def run(job):
        job.artifact_path.write_bytes(b"file")
        job.set_artifact("file.bin")

    job = _start("jb_alice", run, artifact=True)
    assert written.wait(10)
    seen = jobs.get(job["id"])
    assert seen["state"] == "running" and seen["artifact"] is None
    go.set()
    done = _finished(job["id"])
    assert done["state"] == "done" and done["artifact"]["name"] == "file.bin"


def test_stopping_a_running_job(alice):
    from gamma import jobs

    gate = Gate()
    job = _start("jb_alice", gate)
    assert gate.started.wait(10)
    stopped = alice.post(f"/api/jobs/{job['id']}/cancel").json()
    assert stopped["stopping"] and not stopped["stoppable"] and stopped["state"] == "running"
    gate.release()
    assert _finished(job["id"])["state"] == "cancelled"
    assert not (jobs.root() / job["id"]).exists()


def test_a_job_past_its_point_of_no_return_cannot_be_stopped(alice):
    gate = Gate(stoppable=False, result="kept")
    job = _start("jb_alice", gate)
    assert gate.started.wait(10)
    assert not alice.get(f"/api/jobs/{job['id']}").json()["stoppable"]
    assert alice.post(f"/api/jobs/{job['id']}/cancel").status_code == 409
    gate.release()
    assert _finished(job["id"])["result"] == "kept"


def test_an_account_runs_two_jobs_at_once_the_rest_wait_their_turn(alice):
    from gamma import jobs

    gates = [Gate(result=n) for n in range(4)]
    started = [_start("jb_alice", gate) for gate in gates]
    assert gates[0].started.wait(10) and gates[1].started.wait(10)
    time.sleep(0.1)
    assert [jobs.get(j["id"])["state"] for j in started] == ["running", "running", "queued", "queued"]
    assert alice.post(f"/api/jobs/{started[3]['id']}/cancel").json()["state"] == "cancelled"  # a waiting one ends at once
    gates[0].release()
    assert gates[2].started.wait(10)  # the oldest waiting one takes the free slot
    for gate in gates[1:3]:
        gate.release()
    assert [_finished(j["id"])["state"] for j in started] == ["done", "done", "done", "cancelled"]
    assert not gates[3].started.is_set()


def test_the_workspaces_own_jobs_are_outside_the_account_limits():
    from gamma import jobs

    gates = [Gate() for _ in range(3)]
    started = [_start(jobs.WORKSPACE, gate, ws=f"jb-ws-{n}") for n, gate in enumerate(gates)]
    assert all(gate.started.wait(10) for gate in gates)
    for gate in gates:
        gate.release()
    assert all(_finished(j["id"])["state"] == "done" for j in started)


def test_too_many_jobs_of_one_account_are_refused(monkeypatch):
    from gamma import jobs

    monkeypatch.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 2)
    gates = [Gate(), Gate()]
    started = [_start("jb_alice", gate) for gate in gates]
    with pytest.raises(HTTPException) as refused:
        _start("jb_alice", Gate())
    assert refused.value.status_code == 429
    for gate in gates:
        gate.release()
    for job in started:
        _finished(job["id"])


def test_the_same_work_twice_is_busy():
    from gamma import jobs

    gate = Gate()
    first = _start("jb_alice", gate, key="same")
    with pytest.raises(jobs.Busy) as busy:
        _start("jb_alice", Gate(), key="same")
    assert busy.value.job["id"] == first["id"]
    other = _start("jb_bob", lambda job: None, key="same")  # another account's is other work
    gate.release()
    _finished(first["id"])
    _finished(other["id"])
    assert jobs.latest("jb_alice", "test", "same")["id"] == first["id"]
    assert _start("jb_alice", lambda job: None, key="same")["id"] != first["id"]  # once finished, it may run again


def test_a_job_is_its_owners_business(alice, bob):
    job = _finished(_start("jb_alice", lambda job: "private", ws=workspace_of("jb_alice"))["id"])
    assert all(j["id"] != job["id"] for j in bob.get("/api/jobs").json()["jobs"])
    assert bob.get(f"/api/jobs/{job['id']}").status_code == 404
    assert bob.post(f"/api/jobs/{job['id']}/cancel").status_code == 404
    assert bob.delete(f"/api/jobs/{job['id']}").status_code == 404
    assert bob.get("/api/jobs/not-a-job").status_code == 404
    assert any(j["id"] == job["id"] for j in alice.get("/api/jobs").json()["jobs"])


def test_workspace_jobs_are_seen_by_its_members_where_they_work(lab, alice, bob, cara):
    from gamma import jobs

    gate = Gate()
    job = _start(jobs.WORKSPACE, gate, ws=lab, kind="indexing")
    assert gate.started.wait(10)
    assert any(j["id"] == job["id"] for j in bob.get("/api/jobs", headers=_in(lab)).json()["jobs"])
    assert all(j["id"] != job["id"] for j in bob.get("/api/jobs").json()["jobs"])  # in his own workspace
    assert bob.get(f"/api/jobs/{job['id']}").status_code == 200
    assert bob.post(f"/api/jobs/{job['id']}/cancel").status_code == 403  # a viewer only looks
    assert cara.post(f"/api/jobs/{job['id']}/cancel").json()["stopping"]
    gate.release()
    assert _finished(job["id"])["state"] == "cancelled"
    make_user("jb_stranger", "jb-stranger-pw")
    assert login("jb_stranger", "jb-stranger-pw").get(f"/api/jobs/{job['id']}").status_code == 404
    assert bob.post("/api/jobs/clear", headers=_in(lab)).status_code == 200
    assert jobs.get(job["id"]) is not None  # a viewer's clear leaves the workspace's jobs
    cara.post("/api/jobs/clear", headers=_in(lab))
    assert jobs.get(job["id"]) is None


def test_removing_a_job_and_clearing_the_finished_ones(alice):
    from gamma import jobs

    gate = Gate()
    running = _start("jb_alice", gate)
    assert gate.started.wait(10)
    assert alice.delete(f"/api/jobs/{running['id']}").status_code == 409

    def run(job):
        job.artifact_path.write_bytes(b"x")
        job.set_artifact("x.bin")

    finished = _finished(_start("jb_alice", run, artifact=True)["id"])
    assert (jobs.root() / finished["id"] / "artifact").is_file()
    assert alice.delete(f"/api/jobs/{finished['id']}").json() == {"ok": True}
    assert not (jobs.root() / finished["id"]).exists() and jobs.get(finished["id"]) is None
    other = _finished(_start("jb_alice", lambda job: None)["id"])
    assert alice.post("/api/jobs/clear").json()["removed"] >= 1
    assert jobs.get(other["id"]) is None and jobs.get(running["id"])["state"] == "running"  # a running one stays
    gate.release()
    _finished(running["id"])


def test_a_job_that_writes_files_needs_room(monkeypatch):
    from gamma import jobs

    def run(job):
        job.artifact_path.write_bytes(b"12345")
        job.set_artifact("five.bin")

    _finished(_start("jb_alice", run, artifact=True)["id"])
    monkeypatch.setattr(jobs, "ARTIFACT_CAP", 1)
    with pytest.raises(HTTPException) as full:
        _start("jb_alice", run, artifact=True)
    assert full.value.status_code == 507 and "Background tasks" in full.value.detail
    _finished(_start("jb_alice", lambda job: None)["id"])  # a job without a file still starts


def test_running_scheduled_backups_are_listed_read_only(alice, monkeypatch):
    from gamma import backup_schedule

    tasks = [{"id": "t" * 32, "name": "Nightly", "state": "running", "last_run": "2026-09-29T03:00:00+00:00"},
             {"id": "u" * 32, "name": "Weekly", "state": "finished", "last_run": "2026-09-28T03:00:00+00:00"}]
    monkeypatch.setattr(backup_schedule, "list_tasks", lambda owner: tasks if owner == "jb_alice" else [])
    rows = [j for j in alice.get("/api/jobs").json()["jobs"] if j["kind"] == "scheduled-backup"]
    assert [(r["title"], r["state"], r["readonly"], r["stoppable"]) for r in rows] == [("Nightly", "running", True, False)]


def test_jobs_are_for_signed_in_accounts(anon):
    assert anon.get("/api/jobs").status_code == 401
    assert anon.post("/api/jobs/export", json={"folder": "x"}).status_code == 401


def test_restart_fails_what_the_stopped_process_left(data_dir):
    from gamma import jobs
    from gamma.db import connect_users_db, page_now

    connect_users_db().close()
    with closing(sqlite3.connect(str(data_dir / "users.db"))) as conn:
        for job_id, state, instance in (("a" * 24, "running", "gone"), ("b" * 24, "queued", "gone"),
                                        ("c" * 24, "running", jobs.INSTANCE), ("d" * 24, "done", "gone")):
            conn.execute("INSERT INTO jobs (id, owner, kind, state, instance, created_at) VALUES (?, 'u', 'k', ?, ?, ?)",
                         (job_id, state, instance, page_now()))
        conn.commit()
    (jobs.root() / ("a" * 24)).mkdir(parents=True)
    assert jobs.recover() == 2
    states = {job_id: (jobs.get(job_id)["state"], jobs.get(job_id)["error"]) for job_id in ("a" * 24, "b" * 24, "c" * 24, "d" * 24)}
    assert states == {"a" * 24: ("failed", jobs.INTERRUPTED), "b" * 24: ("failed", jobs.INTERRUPTED),
                      "c" * 24: ("running", ""), "d" * 24: ("done", "")}
    assert not (jobs.root() / ("a" * 24)).exists()


def test_the_sweep_ends_old_jobs_their_files_and_leftovers(data_dir):
    from gamma import jobs
    from gamma.db import connect_users_db

    connect_users_db().close()

    def run(job):
        job.artifact_path.write_bytes(b"old")
        job.set_artifact("old.bin")

    old = _finished(_start("jb_alice", run, artifact=True)["id"])
    orphan = jobs.root() / ("e" * 24)
    orphan.mkdir(parents=True)
    upload = jobs.incoming_path()
    upload.write_bytes(b"never taken")
    fresh_orphan = jobs.root() / ("f" * 24)
    fresh_orphan.mkdir()
    long_ago = time.time() - jobs.RETENTION_S - 60
    for path in (orphan, upload):
        os.utime(path, (long_ago, long_ago))
    assert jobs.sweep() == 0  # nothing finished that long ago yet
    assert (jobs.root() / old["id"]).exists() and not orphan.exists() and not upload.exists()
    assert fresh_orphan.exists()  # too young: it may be a job starting right now
    assert jobs.sweep(now=time.time() + jobs.RETENTION_S + 60) == 1
    assert jobs.get(old["id"]) is None and not (jobs.root() / old["id"]).exists() and not fresh_orphan.exists()


def test_an_account_takes_its_jobs_along(client):
    from gamma import jobs, workspaces
    from gamma.db import connect_users_db
    from gamma.routers.admin import rename_account

    make_user("jb_renamed", "jb-renamed-pw")

    def run(job):
        job.artifact_path.write_bytes(b"mine")
        job.set_artifact("mine.bin")

    job = _finished(_start("jb_renamed", run, artifact=True)["id"])
    with connect_users_db() as conn:
        rename_account(conn, "jb_renamed", "jb_renamed2")
    assert [j["id"] for j in jobs.for_account("jb_renamed2")] == [job["id"]]
    assert jobs.for_account("jb_renamed") == []
    workspaces.delete_account("jb_renamed2")
    assert jobs.get(job["id"]) is None and not (jobs.root() / job["id"]).exists()


def test_a_listing_is_the_newest_jobs_first(alice):
    ids = [_finished(_start("jb_cara", lambda job, n=n: n)["id"])["id"] for n in range(3)]
    from gamma import jobs
    listed = [j["id"] for j in jobs.for_account("jb_cara") if j["id"] in ids]
    assert listed == ids[::-1]
    assert json.loads(json.dumps(jobs.for_account("jb_cara")))  # plain JSON

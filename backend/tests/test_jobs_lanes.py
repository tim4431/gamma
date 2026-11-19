"""A kind with a lane of its own (gamma/jobs.py ``KIND_LIMITS``, the ``ocr``
job): so many of it run at once whoever starts them, the rest wait queued
and stop at once, and none of them takes a place of the server's or an
account's running limits, so an export still starts beside them;
``jobs.live`` finds a kind's job by its key whoever owns it, and a start
``exclusive`` refuses beside it; a report may say more of its phase
(``since``, ``from``)."""

import pytest
from conftest import account_of, login, make_user
from test_jobs import Gate

from gamma import jobs


@pytest.fixture(scope="module")
def owners(client):
    for name in ("jl_alice", "jl_bob"):
        make_user(name, "jl-pass-12345")
    login("jl_alice", "jl-pass-12345")
    return account_of("jl_alice"), account_of("jl_bob")


def _start(owner, run, kind="ocr", **kwargs):
    return jobs.start(kind, owner=owner, run=run, **kwargs)


def _ended(job_id):
    job = jobs.wait(job_id)
    assert job["state"] in ("done", "failed", "cancelled"), job
    return job


def test_an_ocr_lane_runs_one_and_an_export_still_starts(owners, monkeypatch):
    alice, bob = owners
    monkeypatch.setenv("GAMMA_OCR_WORKERS", "1")
    monkeypatch.setattr(jobs, "MAX_RUNNING_PER_ACCOUNT", 1)
    first, second, export = Gate(result={"n": 1}), Gate(result={"n": 2}), Gate(result={"n": 3})
    a = _start(alice, first, key="w:a")
    assert first.started.wait(10)
    b = _start(bob, second, key="w:b")
    assert jobs.get(b["id"])["state"] == "queued", "the lane is full, whoever starts it"
    c = _start(alice, export, kind="export")
    assert export.started.wait(10), "the account's one running place is not the lane's"
    assert jobs.get(c["id"])["state"] == "running"
    stopped = _start(alice, Gate(), key="w:c")
    assert jobs.cancel(stopped["id"])["state"] == "cancelled", "a waiting one stops at once"
    first.release()
    assert _ended(a["id"])["state"] == "done"
    assert second.started.wait(10), "the next one in the lane starts as the first ends"
    second.release()
    export.release()
    assert _ended(b["id"])["result"] == {"n": 2} and _ended(c["id"])["result"] == {"n": 3}


def test_the_lane_takes_as_many_as_it_is_set_to(owners, monkeypatch):
    alice, bob = owners
    monkeypatch.setenv("GAMMA_OCR_WORKERS", "2")
    gates = [Gate(), Gate(), Gate()]
    started = [_start(owner, gate, key=f"w:{n}") for n, (owner, gate) in enumerate(zip((alice, bob, alice), gates))]
    assert gates[0].started.wait(10) and gates[1].started.wait(10)
    assert jobs.get(started[2]["id"])["state"] == "queued"
    for gate in gates:
        gate.release()
    for job in started:
        assert _ended(job["id"])["state"] == "done"


def test_live_finds_a_kinds_job_by_its_key_whoever_owns_it(owners):
    alice, bob = owners
    gate = Gate()
    job = _start(alice, gate, key="w:live")
    assert gate.started.wait(10)
    found = jobs.live("ocr", "w:live")
    assert found["id"] == job["id"] and found["owner"] == alice and found["state"] == "running"
    assert jobs.live("ocr", "w:other") is None and jobs.live("export", "w:live") is None
    gate.release()
    _ended(job["id"])
    assert jobs.live("ocr", "w:live") is None, "an ended one is no longer live"


def test_an_exclusive_start_finds_any_owners_job_of_its_key(owners):
    alice, bob = owners
    gate = Gate()
    job = _start(alice, gate, key="w:only")
    assert gate.started.wait(10)
    try:
        with pytest.raises(jobs.Busy) as busy:
            _start(bob, Gate(), key="w:only", exclusive=True)
        assert busy.value.job["id"] == job["id"] and busy.value.job["owner"] == alice
        other = _start(bob, Gate(result={}), key="w:only")  # without it, the key is the owner's alone
        assert other["id"] != job["id"] and jobs.cancel(other["id"])["state"] == "cancelled"
    finally:
        gate.release()
    _ended(job["id"])


def test_a_report_says_more_of_its_phase(owners):
    alice, _bob = owners

    def run(job):
        job.progress(done=1, total=4, unit="pages", phase="recognizing", since=1700000000000, **{"from": 1})
        job.progress(done=2)
        seen.append(jobs.get(job._live.id)["progress"])
        job.progress(phase="updating", stoppable=False)

    seen = []
    _ended(_start(alice, run, key="w:report")["id"])
    assert seen == [{"phase": "recognizing", "done": 2, "total": 4, "unit": "pages", "since": 1700000000000,
                     "from": 1}]

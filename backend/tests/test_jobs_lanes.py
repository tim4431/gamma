"""A kind with a lane of its own (gamma/jobs.py ``KIND_LIMITS``, the ``ocr``
job): so many of it run at once whoever starts them, the rest wait queued
and stop at once, and none of them takes a place of the server's or an
account's running limits, so an export still starts beside them;
``jobs.live`` finds a kind's job by its key whoever owns it, and a start
``exclusive`` refuses beside it (or beside a job of any kind it names); a
report may say more of its phase (``since``, ``from``). A job that
``yields`` (an automatic recognition) gives way to one asked for, which
its lane starts first, until ``promote``, and goes on when the one that
waited is stopped; once it gave way it is promoted no more and a start of
its work is a job of its own. A job stopped while it waits tells its end
hook. A kind whose work is done elsewhere (``AT_ONCE``, a window's
recognition) starts at once."""

import threading
import time

import pytest
from conftest import account_of, login, make_user
from fastapi import HTTPException
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


def test_the_lane_takes_as_many_as_it_is_set_to(owners, server_option):
    alice, bob = owners
    server_option("ocr_workers", 2)
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


def test_an_exclusive_start_of_several_kinds_finds_any_of_them(owners):
    alice, bob = owners
    gate = Gate()
    drop = _start(alice, gate, kind="ocr-drop", key="w:kinds")
    assert gate.started.wait(10)
    try:
        for kind in ("ocr", "ocr-tab"):
            with pytest.raises(jobs.Busy) as busy:
                _start(bob, Gate(), kind=kind, key="w:kinds", exclusive=("ocr", "ocr-tab", "ocr-drop"))
            assert busy.value.job["id"] == drop["id"] and busy.value.job["kind"] == "ocr-drop", "it says which"
        alone = Gate(result={})
        other = _start(bob, alone, key="w:kinds", exclusive=True)  # of its own kind alone
        assert other["id"] != drop["id"]
        alone.release()
        assert _ended(other["id"])["state"] == "done"
    finally:
        gate.release()
    _ended(drop["id"])


def test_a_kind_whose_work_is_done_elsewhere_starts_at_once(owners, monkeypatch):
    alice, _bob = owners
    monkeypatch.setattr(jobs, "MAX_RUNNING_PER_ACCOUNT", 1)
    export, window, second = Gate(), Gate(), Gate()
    a = _start(alice, export, kind="export")
    assert export.started.wait(10)
    b = _start(alice, window, kind="ocr-tab", key="w:tab")
    assert window.started.wait(10), "the account's one running place does not hold it"
    export.release()
    _ended(a["id"])
    c = _start(alice, second, kind="export")
    assert second.started.wait(10), "nor does it take that place"
    monkeypatch.setattr(jobs, "MAX_ACTIVE_PER_ACCOUNT", 2)
    with pytest.raises(HTTPException) as full:
        _start(alice, Gate(), kind="ocr-tab", key="w:tab2")
    assert full.value.status_code == 429, "it counts toward the account's jobs like any"
    window.release()
    second.release()
    assert _ended(b["id"])["state"] == _ended(c["id"])["state"] == "done"


class _Pages:
    """A lane job of many pages: a report a page, until ``release``d to end."""

    def __init__(self):
        self.started, self.go, self.pages = threading.Event(), threading.Event(), 0

    def __call__(self, job):
        while not self.go.is_set():
            job.progress(done=self.pages, unit="pages", phase="recognizing")
            self.started.set()
            self.pages += 1
            time.sleep(0.01)
        return {"pages": self.pages}


def test_a_job_that_yields_gives_way_to_one_asked_for(owners, monkeypatch):
    alice, _bob = owners
    told, auto = [], _Pages()
    a = _start(jobs.WORKSPACE, auto, key="w:auto", yields=True, ended=told.append)
    assert auto.started.wait(10)
    manual = Gate(result={"manual": True})
    m = _start(alice, manual, key="w:manual")
    assert manual.started.wait(10), "the automatic run gave way at its next page"
    ended = _ended(a["id"])
    assert ended["state"] == "cancelled" and told == ["yielded"], "cancelled, and its hook told it yielded"
    manual.release()
    assert _ended(m["id"])["result"] == {"manual": True}


def test_a_lane_starts_the_jobs_asked_for_first(owners, monkeypatch):
    alice, _bob = owners
    first = Gate()
    holder = _start(alice, first, key="w:first")
    assert first.started.wait(10)
    late, asked = Gate(), Gate()
    queued_auto = _start(jobs.WORKSPACE, late, key="w:late", yields=True)
    queued_manual = _start(alice, asked, key="w:asked")
    first.release()
    assert asked.started.wait(10) and not late.started.is_set(), "the one asked for, though it came later"
    asked.release()
    assert late.started.wait(10)
    late.release()
    for job in (holder, queued_manual, queued_auto):
        assert _ended(job["id"])["state"] == "done"


def test_a_promoted_job_no_longer_gives_way(owners, monkeypatch):
    alice, _bob = owners
    told, auto = [], Gate(result={"auto": True})
    a = _start(jobs.WORKSPACE, auto, key="w:promoted", yields=True, ended=told.append)
    assert auto.started.wait(10)
    manual = Gate()
    m = _start(alice, manual, key="w:waits")
    assert jobs._live[a["id"]].giving_way, "asked to give way at its next report"
    assert jobs.promote(a["id"]) and jobs.promote(a["id"]), "promoted, then again: it goes on for both"
    assert not jobs.promote("0" * 24)
    auto.release()
    assert _ended(a["id"])["result"] == {"auto": True} and told == ["done"], "someone asked for it: it goes on"
    assert manual.started.wait(10)
    manual.release()
    _ended(m["id"])


def test_a_stop_of_the_job_that_waited_withdraws_the_give_way(owners, monkeypatch):
    alice, _bob = owners
    told, auto = [], Gate(result={"auto": True})
    a = _start(jobs.WORKSPACE, auto, key="w:withdrawn", yields=True, ended=told.append)
    assert auto.started.wait(10)
    m = _start(alice, Gate(), key="w:stopped")
    assert jobs._live[a["id"]].giving_way
    assert jobs.cancel(m["id"])["state"] == "cancelled"
    assert not jobs._live[a["id"]].giving_way, "nobody waits for its place any more"
    auto.release()
    assert _ended(a["id"])["result"] == {"auto": True} and told == ["done"], "it went on"


def test_a_job_that_gave_way_is_promoted_no_more_and_a_start_beside_it_is_its_own(owners, monkeypatch):
    alice, _bob = owners
    told, ending, hold = [], threading.Event(), threading.Event()

    def run(job):  # reports until it gives way, then holds while it ends
        try:
            while True:
                job.progress(unit="pages")
                time.sleep(0.01)
        finally:
            ending.set()
            assert hold.wait(10), "the test never let the job end"

    a = _start(jobs.WORKSPACE, run, key="w:gave", yields=True, exclusive=True, ended=told.append)
    waiting, mine = Gate(), Gate(result={})
    w = _start(alice, waiting, key="w:first")
    assert ending.wait(10), "asked to give way, it does at its next report"
    try:
        assert jobs.get(a["id"])["stopping"], "it reads as stopping while it ends"
        assert not jobs.promote(a["id"]), "too late: it gave way"
        m = _start(alice, mine, key="w:gave", exclusive=True)
        assert m["id"] != a["id"] and m["state"] == "queued", "no Busy with it: a job of its own"
    finally:
        hold.set()
    assert _ended(a["id"])["state"] == "cancelled" and told == ["yielded"]
    assert waiting.started.wait(10)
    waiting.release()
    assert mine.started.wait(10)
    mine.release()
    assert _ended(w["id"])["state"] == _ended(m["id"])["state"] == "done"


def test_a_job_stopped_while_it_waits_tells_its_end_hook(owners, monkeypatch):
    alice, _bob = owners
    first = Gate()
    holder = _start(alice, first, key="w:holds")
    assert first.started.wait(10)
    told = []

    def ended(state):  # the job reads as ended by then
        told.append((state, jobs.latest(alice, "ocr", "w:waits")["state"]))

    queued = _start(alice, Gate(), key="w:waits", ended=ended)
    assert jobs.cancel(queued["id"])["state"] == "cancelled"
    assert told == [("cancelled", "cancelled")], "told before the stop returns"
    first.release()
    _ended(holder["id"])


def test_a_job_past_its_last_stop_does_not_give_way(owners, monkeypatch):
    alice, _bob = owners
    auto = Gate(result={"applied": True}, stoppable=False)
    a = _start(jobs.WORKSPACE, auto, key="w:applying", yields=True)
    assert auto.started.wait(10)
    manual = Gate()
    m = _start(alice, manual, key="w:after")
    auto.release()
    assert _ended(a["id"])["result"] == {"applied": True}
    assert manual.started.wait(10)
    manual.release()
    _ended(m["id"])
    assert jobs.live_count("ocr") == 0

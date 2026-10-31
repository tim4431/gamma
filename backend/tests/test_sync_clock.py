"""The shared sync clock (gamma/sync_clock.py). Each engine keeps its own
requests, dirty marks and loop. A write counts for every engine but the one
that made it. A loop passes over a workspace whose round is running and
drops requests nobody will run. An engine with a pool of workers runs that
many rounds at once, one workspace's long round holding up no other's, and
hands a workspace whose round runs no second. The mirror's clock names in
gamma/sync_engine.py stay aliases of its engine's objects.

Most tests drive ``Engine.tick`` with a fake monotonic clock. The others
start real loop threads (in an empty registry, or an engine's own) and
stop them again."""

import threading
import time

import pytest

from conftest import account_of, make_user
from gamma import config, ops, sync_clock, sync_engine
from gamma.db import connect_users_db, page_now


class FakeTime:
    """``sync_clock``'s view of ``time``: a monotonic clock the test moves."""

    def __init__(self, at: float = 1000.0):
        self.at = at

    def monotonic(self) -> float:
        return self.at


@pytest.fixture
def clock(monkeypatch):
    fake = FakeTime()
    monkeypatch.setattr(sync_clock, "time", fake)
    return fake


@pytest.fixture
def registry(monkeypatch):
    """An empty engine registry and no commit listeners but the ones the
    test registers, which go away with it."""
    monkeypatch.setattr(sync_clock, "_engines", [])
    monkeypatch.setattr(ops, "commit_listeners", [])
    return sync_clock._engines


def engine(name="a", *, rows=(), client="a", **kw) -> sync_clock.Engine:
    """An engine over a list of rows the test edits (``e.listed``) that
    records its rounds (``e.ran``)."""
    listed, ran = list(rows), []
    e = sync_clock.Engine(name, rows=lambda: listed, run=ran.append, client=client, **kw)
    e.listed, e.ran = listed, ran
    return e


def hold(lock: threading.Lock) -> threading.Event:
    """Hold ``lock`` in another thread, as a Sync now would, until the
    returned event is set (or 5 s pass, so a clock that waited fails
    instead of hanging)."""
    release, held = threading.Event(), threading.Event()

    def run():
        with lock:
            held.set()
            release.wait(5)

    threading.Thread(target=run, daemon=True).start()
    assert held.wait(5)
    return release


def test_two_engines_keep_their_own_marks(clock):
    a = engine("a", rows=[("w1", "two-way", 30, 1)], client="sync")
    b = engine("b", rows=[("w2", "two-way", 30, 1)], client="zotero")
    a.refresh()
    b.refresh()
    assert a.wants_change == {"w1": True} and b.wants_change == {"w2": True}
    a.request("w1")
    assert "w1" in a.pending and b.pending == {}
    assert a.wake.is_set() and not b.wake.is_set()
    for e in (a, b):
        e.on_commit("w3", "t")
    a.settle("w3", clock.at)
    assert not a.has_local_changes("w3") and b.has_local_changes("w3")
    a.last_run["w1"] = clock.at
    assert b.last_run == {}
    # one engine's refresh leaves the other's wants alone (a shared dict cleared them all)
    b.listed.clear()
    b.refresh()
    assert a.wants_change == {"w1": True} and b.wants_change == {}


def test_a_write_counts_for_every_engine_but_the_one_that_made_it(registry, clock):
    rows = [("w", "two-way", 30, 1)]
    mirror = sync_clock.register("mirror", rows=lambda: rows, run=lambda ws: None, client="sync")
    zotero = sync_clock.register("zotero", rows=lambda: rows, run=lambda ws: None, client="zotero", debounce=3.0)
    for e in registry:
        e.refresh()
    # a page the Zotero link wrote: a local edit for a mirror of the same workspace only
    ops.notify_commit("w", "zotero", "p1")
    assert mirror.has_local_changes("w") and "w" in mirror.pending
    assert not zotero.has_local_changes("w") and "w" not in zotero.pending
    for e in registry:
        e.dirty.clear()
        e.pending.clear()
    # a clone's push into its origin (client "sync"): an edit to send on to Zotero
    ops.notify_commit("w", "sync", "p1")
    assert zotero.has_local_changes("w") and zotero.pending == {"w": clock.at + 3.0}
    assert not mirror.has_local_changes("w") and mirror.pending == {}
    # a person's edit: both
    ops.notify_commit("w", "", "p1")
    assert mirror.has_local_changes("w") and zotero.has_local_changes("w")


def test_a_write_asks_for_a_round_only_where_the_workspace_syncs_on_change(clock):
    e = engine(rows=[("on", "two-way", 30, 1), ("quiet", "two-way", 30, 0), ("off", "off", 30, 1)])
    e.refresh()
    for ws in ("on", "quiet", "off", "unlisted"):
        e.on_commit(ws, "t")
    assert set(e.pending) == {"on"}
    assert set(e.dirty) == {"on", "quiet", "off", "unlisted"}


def test_counts_leaves_out_writes_the_engine_does_not_carry(clock):
    e = engine(rows=[("w", "two-way", 30, 1)], counts=lambda ws, page_id: page_id in ("", "shown"))
    e.refresh()
    e.on_commit("w", "t", "hidden")
    assert not e.has_local_changes("w") and e.pending == {}
    e.on_commit("w", "t", "shown")
    assert e.has_local_changes("w") and "w" in e.pending


def test_debounce_is_per_engine(clock):
    quick = engine("quick", rows=[("w", "two-way", 30, 1)])
    slow = engine("slow", rows=[("w", "two-way", 30, 1)], debounce=3.0)
    for e in (quick, slow):
        e.refresh()
        e.on_commit("w", "t")
    assert quick.pending["w"] == clock.at + sync_clock.DEBOUNCE_S and slow.pending["w"] == clock.at + 3.0
    # a further write within the quiet time moves the round back: a burst is one round
    clock.at += 2
    slow.on_commit("w", "t")
    assert slow.pending["w"] == clock.at + 3.0
    assert not slow.due_now("w", "two-way", 0, clock.at + 2.9) and slow.due_now("w", "two-way", 0, clock.at + 3)
    slow.request("w", 0.5)  # an explicit delay wins over the debounce
    assert slow.pending["w"] == clock.at + 0.5


def test_the_interval_maps_the_stored_cadence(clock):
    live = engine("live", interval=lambda ws, poll_s: 30 if poll_s == 5 else poll_s)  # Zotero's Live: every 30 s
    plain = engine("plain")
    now = clock.at
    for e in (live, plain):
        e.last_run["w"] = now - 10
    assert plain.due_now("w", "two-way", 5, now)
    assert not live.due_now("w", "two-way", 5, now)
    assert not live.due_now("w", "two-way", 5, now + 19.9) and live.due_now("w", "two-way", 5, now + 20)
    assert not live.due_now("w", "two-way", 300, now + 280) and live.due_now("w", "two-way", 300, now + 290)
    assert not live.due_now("w", "two-way", 0, now + 10**6)  # 0: only by hand
    assert not live.due_now("w", "off", 5, now + 10**6)
    assert live.due_now("new", "two-way", 5, now)  # never run since startup: due at once
    # the loop goes by the mapped cadence
    live.listed.append(("w", "two-way", 5, 1))
    live.tick()
    assert live.ran == []
    clock.at += 20
    live.tick()
    assert live.ran == ["w"]


def test_a_tick_runs_what_is_due_with_its_lock_held(clock):
    seen = []
    e = sync_clock.Engine("a", rows=lambda: [("asked", "two-way", 0, 1), ("polled", "two-way", 30, 1),
                                             ("idle", "two-way", 0, 1)],
                          run=lambda ws: seen.append((ws, e.lock(ws).locked())), client="a")
    e.request("asked", 0)
    e.last_run["polled"] = clock.at - 30
    e.last_run["idle"] = clock.at - 10**6
    e.tick()
    assert seen == [("asked", True), ("polled", True)]
    assert e.pending == {} and e.last_run["asked"] == e.last_run["polled"] == clock.at
    assert not e.lock("asked").locked()
    assert e.lock("asked") is e.lock("asked")  # the clock keeps one lock per workspace for an engine without its own


def test_a_round_that_raises_releases_its_lock_and_the_tick_goes_on(clock):
    ran = []

    def run(ws):
        ran.append(ws)
        if ws == "bad":
            raise RuntimeError("boom")

    e = sync_clock.Engine("a", rows=lambda: [("bad", "two-way", 0, 1), ("good", "two-way", 0, 1)], run=run, client="a")
    e.request("bad", 0)
    e.request("good", 0)
    e.tick()
    assert ran == ["bad", "good"] and not e.lock("bad").locked() and e.pending == {}


def test_a_request_made_during_a_round_gets_a_round_after_it(clock):
    ran = []

    def run(ws):
        ran.append(ws)
        if len(ran) == 1:
            e.request(ws, 0)  # a bounded round with work left asks for the next one

    e = sync_clock.Engine("a", rows=lambda: [("w", "two-way", 0, 1)], run=run, client="a")
    e.request("w", 0)
    assert e.tick() == sync_clock.MIN_WAIT_S  # the follow-up is due: look again almost at once
    assert e.tick() == sync_clock.TICK_S
    assert ran == ["w", "w"] and e.pending == {}


def test_a_busy_workspace_is_asked_again_and_the_tick_goes_on(clock):
    locks: dict[str, threading.Lock] = {}
    e = engine(rows=[("busy", "two-way", 0, 1), ("free", "two-way", 0, 1)],
               lock=lambda ws: locks.setdefault(ws, threading.Lock()))
    e.request("busy", 0)
    e.request("free", 0)
    release = hold(e.lock("busy"))
    try:
        started = time.perf_counter()
        wait = e.tick()
        assert time.perf_counter() - started < 1, "the clock waited on a held lock"
        assert e.ran == ["free"]
        assert e.pending == {"busy": clock.at + sync_clock.BUSY_RETRY_S}
        assert e.last_run["busy"] == clock.at
        assert wait == sync_clock.TICK_S
        clock.at += 1
        e.tick()
        assert e.ran == ["free"], "a busy workspace is not tried every tick"
    finally:
        release.set()
    assert e.lock("busy").acquire(timeout=5)  # the other round is over
    e.lock("busy").release()
    clock.at += sync_clock.BUSY_RETRY_S
    e.tick()
    assert e.ran == ["free", "busy"] and e.pending == {}


def test_a_busy_workspace_keeps_a_sooner_request(clock):
    e = engine(rows=[("w", "two-way", 30, 1)])
    e.request("w", 0.5)  # say, the stream's "Zotero changed"
    release = hold(e.lock("w"))  # and a round already running
    try:
        e.tick()  # due by its cadence (never run), busy
        assert e.pending == {"w": clock.at + 0.5}
        e.pending["w"] = clock.at + 60  # a request for much later is brought forward to the retry
        e.last_run.pop("w")
        e.tick()
        assert e.pending == {"w": clock.at + sync_clock.BUSY_RETRY_S}
    finally:
        release.set()


def test_requests_nobody_will_run_are_dropped_once_due(clock):
    e = engine(rows=[("detached", "off", 30, 1), ("live", "two-way", 0, 1)])
    e.request("detached", 0)
    e.request("removed", 0)
    e.request("later", 3)  # not listed yet (a link being made): kept until it is due
    wait = e.tick()
    assert e.ran == [] and e.pending == {"later": clock.at + 3}
    assert wait == sync_clock.TICK_S, "a stale request kept the loop waking every few milliseconds"
    e.listed.append(("later", "two-way", 0, 1))
    clock.at += 3
    e.tick()
    assert e.ran == ["later"] and e.pending == {}
    e.request("live", 0.2)
    assert e.tick() == pytest.approx(0.2)  # the loop sleeps until the next request is due


def test_refresh_updates_wants_in_place(clock):
    e = engine(rows=[("a", "two-way", 30, 1), ("b", "pull", 30, 0), ("c", "off", 30, 1)])
    wants = e.wants_change
    e.refresh()
    assert wants == {"a": True, "b": False, "c": False} and e.wants_change is wants
    e.listed[:] = [("b", "pull", 30, 1)]
    assert e.refresh() == [("b", "pull", 30, 1)]
    assert wants == {"b": True}


def test_a_clean_round_settles_only_the_writes_made_before_it_started(clock):
    e = engine()
    e.on_commit("w", "t")
    e.settle("w", clock.at - 1)  # a write after the round started still waits
    assert e.has_local_changes("w")
    e.settle("w", clock.at)
    assert not e.has_local_changes("w")
    e.settle("w", clock.at)  # nothing to settle


def test_register_adds_the_listener_and_start_runs_resets_then_loops(registry, monkeypatch):
    resets = []

    def broken():
        raise RuntimeError("no table")

    a = sync_clock.register("a", rows=lambda: [], run=lambda ws: None, client="a", reset=lambda: resets.append("a"))
    b = sync_clock.register("b", rows=lambda: [], run=lambda ws: None, client="b", reset=broken)
    c = sync_clock.register("c", rows=lambda: [], run=lambda ws: None, client="c")
    assert ops.commit_listeners == [a.on_commit, b.on_commit, c.on_commit]
    with pytest.raises(ValueError):
        sync_clock.register("a", rows=lambda: [], run=lambda ws: None, client="x")
    assert sync_clock.get("b") is b and sync_clock.get("nope") is None
    sync_clock.start()  # GAMMA_SYNC_INTERVAL=0 (the tests): the resets run, a failing one too, and no loop
    assert resets == ["a"] and all(e._thread is None for e in registry)
    monkeypatch.setattr(config, "sync_interval_s", lambda: 30)
    monkeypatch.setattr(sync_clock, "FIRST_PASS_S", 0)
    try:
        sync_clock.start()
        threads = [e._thread for e in registry]
        assert [t.name for t in threads] == ["a-loop", "b-loop", "c-loop"]
        assert all(t.daemon and t.is_alive() for t in threads)
        assert resets == ["a", "a"]
        sync_clock.start()  # again: the resets again, no second loop
        assert resets == ["a", "a", "a"] and [e._thread for e in registry] == threads
    finally:
        for e in registry:
            e.stop(timeout=5)
    assert not any(t.is_alive() for t in threads)


def test_a_slow_round_of_one_engine_never_holds_up_another(registry, monkeypatch):
    monkeypatch.setattr(config, "sync_interval_s", lambda: 30)
    monkeypatch.setattr(sync_clock, "FIRST_PASS_S", 0)
    inside, release, mirrored = threading.Event(), threading.Event(), threading.Event()

    def slow(ws):
        inside.set()
        release.wait(10)

    zotero = sync_clock.register("zotero", rows=lambda: [("wz", "two-way", 0, 1)], run=slow, client="zotero")
    mirror = sync_clock.register("mirror", rows=lambda: [("wm", "two-way", 0, 1)],
                                 run=lambda ws: mirrored.set(), client="sync")
    try:
        sync_clock.start()
        zotero.request("wz", 0)
        assert inside.wait(5), "a request wakes its loop"
        mirror.request("wm", 0)
        assert mirrored.wait(5), "the mirror's round waited for Zotero's"
    finally:
        release.set()
        for e in registry:
            e.stop(timeout=5)


@pytest.fixture
def pooled(monkeypatch):
    """An engine with a pool of two whose loop never looks by itself (the
    test ticks it), over rows the test lists, its rounds waiting on events."""
    monkeypatch.setattr(sync_clock, "FIRST_PASS_S", 3600)
    gates, ran = {}, []

    def run(ws):
        ran.append(ws)
        gates.setdefault(ws, threading.Event()).wait(5)

    e = sync_clock.Engine("zb-pool", rows=lambda: e.listed, run=run, client="zb", workers=2)
    e.listed, e.gates, e.ran = [], gates, ran
    e.start_loop()
    yield e
    for gate in gates.values():
        gate.set()
    e.stop(timeout=5)


def _until(check, timeout=5.0):
    end = time.monotonic() + timeout
    while not check():
        assert time.monotonic() < end, "timed out"
        time.sleep(0.01)


def test_one_links_long_round_never_holds_up_anothers(pooled):
    pooled.listed[:] = [("A", "two-way", 0, 1), ("B", "two-way", 0, 1)]
    pooled.gates["A"] = threading.Event()
    pooled.request("A", 0)
    pooled.tick()
    _until(lambda: pooled.ran == ["A"])
    pooled.gates["B"] = threading.Event()
    pooled.gates["B"].set()
    pooled.request("B", 0)
    pooled.tick()
    _until(lambda: pooled.ran == ["A", "B"])  # B's round ran while A's is still in its round
    assert pooled.lock("A").locked()
    pooled.gates["A"].set()
    _until(lambda: not pooled.lock("A").locked())


def test_a_link_whose_round_runs_is_handed_no_second_and_its_request_waits_for_after_it(pooled):
    pooled.listed[:] = [("A", "two-way", 0, 1)]
    pooled.gates["A"] = threading.Event()
    pooled.request("A", 0)
    pooled.tick()
    _until(lambda: pooled.ran == ["A"])
    asked = time.monotonic()
    pooled.request("A", 0)  # the round asks for the next at once, before its lock is let go
    pooled.tick()
    time.sleep(0.05)
    assert pooled.ran == ["A"] and pooled.pending["A"] <= asked + 0.01  # no BUSY_RETRY_S, no second round
    assert pooled.queued("A")
    pooled.gates["A"].set()
    _until(lambda: not pooled.lock("A").locked())
    pooled.tick()
    _until(lambda: pooled.ran == ["A", "A"])


def test_no_more_rounds_run_at_once_than_the_engine_has_workers(pooled):
    pooled.listed[:] = [(ws, "two-way", 0, 1) for ws in ("A", "B", "C")]
    for ws in ("A", "B", "C"):
        pooled.gates[ws] = threading.Event()
        pooled.request(ws, 0)
    pooled.tick()
    _until(lambda: len(pooled.ran) == 2)
    time.sleep(0.05)
    assert len(pooled.ran) == 2 and "C" in pooled.pending  # still due, for the next look
    pooled.gates["A"].set()
    pooled.gates["B"].set()
    _until(lambda: not pooled.queued("A") and not pooled.queued("B"))
    pooled.gates["C"].set()
    pooled.tick()
    _until(lambda: sorted(pooled.ran) == ["A", "B", "C"])


def test_the_mirror_keeps_its_names_as_its_engines_objects():
    e = sync_clock.get("mirror")
    assert e is sync_engine._clock and e.client == sync_engine.CLIENT == "sync"
    assert e.debounce == sync_engine.DEBOUNCE_S
    assert sync_engine._pending is e.pending and sync_engine._last_run is e.last_run
    assert sync_engine._wants_change is e.wants_change and sync_engine._dirty is e.dirty
    assert sync_engine._wake is e.wake
    assert ops.commit_listeners.count(sync_engine._on_commit) == 1
    assert sync_engine.request_sync.__defaults__ == (sync_engine.DEBOUNCE_S,)
    assert e.lock("sc-w") is sync_engine._lock("sc-w")  # the round lock stays sync_engine's own
    assert (sync_engine.TICK_S, sync_engine.FIRST_PASS_S) == (sync_clock.TICK_S, sync_clock.FIRST_PASS_S)


def test_another_engines_write_is_a_local_edit_for_the_mirror():
    ws = "sc-plain-ws"  # no mirror: every page counts
    sync_engine._dirty.pop(ws, None)
    try:
        ops.notify_commit(ws, "sync", "p")
        assert not sync_engine.has_local_changes(ws)
        ops.notify_commit(ws, "zotero", "p")
        assert sync_engine.has_local_changes(ws)
    finally:
        sync_engine._dirty.pop(ws, None)
        sync_engine._filters.pop(ws, None)


def test_the_mirror_clock_passes_over_a_mirror_whose_workspace_is_gone():
    ws, gone = make_user("sc_owner", "pw"), "sc-gone-ws"
    with connect_users_db() as conn:
        for w in (ws, gone):
            conn.execute("INSERT INTO mirrors (workspace_id, remote_url, remote_ws, token, owner, created_at) "
                         "VALUES (?, 'http://testserver', 'r', '', ?, ?)", (w, account_of("sc_owner"), page_now()))
        conn.commit()
    try:
        listed = {row[0] for row in sync_engine._refresh_wants()}
        assert ws in listed and gone not in listed
        assert sync_engine._wants_change[ws] is True and gone not in sync_engine._wants_change
    finally:
        with connect_users_db() as conn:
            conn.execute("DELETE FROM mirrors WHERE workspace_id IN (?, ?)", (ws, gone))
            conn.commit()
        sync_engine._refresh_wants()

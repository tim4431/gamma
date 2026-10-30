"""The Zotero stream (gamma/zotero_stream.py) against a stand-in for
stream.zotero.org on 127.0.0.1: one connection subscribes the distinct keys
of the Live links, a library's change asks a round of the links behind it, a
key's new or lost library has the engine forget its access, the key set is
read again and resubscribed, a refused key waits for a new connection of its
account, a dropped connection comes back after the server's retry with
backoff, and a client-error close stops the reconnects and marks the links.

Each test has a data directory of its own (``data_dir``), so only its own
links are Live, and the clock and the engine's hooks are recorders."""

import asyncio
import json
import logging
import threading
import time

import pytest
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosed

from gamma import sync_clock, workspaces, zotero_account, zotero_remote, zotero_stream, zotero_sync
from gamma.db import connect_pages_db, connect_users_db, page_now, update_pref
from gamma.publisher_sessions import seal
from gamma.seed import insert_account

# users.db's own ``zotero_links`` (gamma/db.py); IF NOT EXISTS leaves the table users.db made as it is.
LINKS_TABLE = """CREATE TABLE IF NOT EXISTS zotero_links (
    workspace_id TEXT PRIMARY KEY REFERENCES workspaces(id),
    owner TEXT NOT NULL,
    library TEXT NOT NULL,
    library_name TEXT NOT NULL DEFAULT '',
    mode TEXT NOT NULL DEFAULT 'two-way',
    download TEXT NOT NULL DEFAULT 'all',
    upload INTEGER NOT NULL DEFAULT 1,
    auto_tags INTEGER NOT NULL DEFAULT 0,
    send_unrecorded INTEGER NOT NULL DEFAULT 0,
    poll_s INTEGER NOT NULL DEFAULT 5,
    on_change INTEGER NOT NULL DEFAULT 1,
    status TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
)"""

K1, K2, K3, K4, K5 = (f"zs-key-0123456789abcdef-{n}00{n}" for n in range(1, 6))  # hints …1001 to …5005


class Conn:
    """One connection the stand-in accepted."""

    def __init__(self, ws):
        self.ws = ws
        self.received: list[dict] = []
        self.closed: int | None = None  # the close code, once closed
        self.opened_at = time.monotonic()

    def subscribed(self) -> list[list[str]]:
        """The keys of each createSubscriptions, in order."""
        return [[s["apiKey"] for s in m["subscriptions"]] for m in self.received
                if m.get("action") == "createSubscriptions"]


class FakeStream:
    """stream.zotero.org as the stream uses it: "connected" with ``retry``
    on open, createSubscriptions answered with ``subscriptionsCreated``
    (keys in ``refuse_keys`` under ``errors``), and on request a close with
    ``close_after_subscribing`` right after the answer; ``silent`` never
    says "connected". The server runs on a loop in a thread of its own."""

    def __init__(self):
        self.retry_ms = 100
        self.refuse_keys: dict[str, str] = {}
        self.close_after_subscribing: tuple | None = None
        self.silent = False  # accept the socket and never say "connected"
        self.connections: list[Conn] = []
        self.cond = threading.Condition()
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self.loop.run_forever, name="fake-zotero-stream", daemon=True)

    def __enter__(self):
        self.thread.start()

        async def open_server():
            return await serve(self._handler, "127.0.0.1", 0)

        self.server = self._call(open_server())
        self.url = f"ws://127.0.0.1:{self.server.sockets[0].getsockname()[1]}"
        return self

    def __exit__(self, *exc):
        async def close_server():
            self.server.close()
            await self.server.wait_closed()

        self._call(close_server())
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join(5)
        self.loop.close()

    def _call(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop).result(10)

    async def _handler(self, ws):
        conn = Conn(ws)
        with self.cond:
            self.connections.append(conn)
            self.cond.notify_all()
        try:
            if not self.silent:
                await ws.send(json.dumps({"event": "connected", "retry": self.retry_ms}))
            async for raw in ws:
                message = json.loads(raw)
                with self.cond:
                    conn.received.append(message)
                    self.cond.notify_all()
                if message.get("action") == "createSubscriptions":
                    subs, errors = [], []
                    for entry in message["subscriptions"]:
                        key = entry["apiKey"]
                        if key in self.refuse_keys:
                            errors.append({"apiKey": key, "error": self.refuse_keys[key]})
                        else:
                            subs.append({"apiKey": key, "topics": ["/users/1", "/users/1/publications"]})
                    await ws.send(json.dumps({"event": "subscriptionsCreated", "subscriptions": subs,
                                              "errors": errors}))
                    if self.close_after_subscribing:
                        await ws.close(*self.close_after_subscribing)
        except ConnectionClosed:
            pass
        finally:
            with self.cond:
                conn.closed = ws.close_code
                self.cond.notify_all()

    def wait(self, predicate, timeout=5.0, what=""):
        with self.cond:
            assert self.cond.wait_for(predicate, timeout), what or "the stand-in waited in vain"

    def wait_subscribed(self, n: int, keys: list[str], timeout=5.0) -> Conn:
        """Connection ``n`` (0-based) once it has subscribed ``keys``."""
        self.wait(lambda: len(self.connections) > n and keys in self.connections[n].subscribed(), timeout,
                  f"connection {n} never subscribed {keys}: {[c.subscribed() for c in self.connections]}")
        return self.connections[n]

    def send(self, conn: Conn, event) -> None:
        self._call(conn.ws.send(event if isinstance(event, str) else json.dumps(event)))

    def close(self, conn: Conn, code: int, reason: str = "") -> None:
        self._call(conn.ws.close(code, reason))


class Clock:
    """The "zotero" engine as the stream sees it: the rounds asked for."""

    def __init__(self):
        self.asked: list[tuple[str, float]] = []
        self.cond = threading.Condition()

    def request(self, ws, delay=None):
        with self.cond:
            self.asked.append((ws, delay))
            self.cond.notify_all()

    def wait(self, n: int, timeout=5.0) -> list[tuple[str, float]]:
        with self.cond:
            assert self.cond.wait_for(lambda: len(self.asked) >= n, timeout), self.asked
            return list(self.asked)


class Engine:
    """zotero_sync's hooks as the stream calls them."""

    def __init__(self):
        self.calls: list[tuple] = []
        self.cond = threading.Condition()

    def _add(self, call):
        with self.cond:
            self.calls.append(call)
            self.cond.notify_all()

    def forget_access(self, ws):
        self._add(("forget", ws))

    def record_error(self, ws, message, kind="other"):
        self._add(("error", ws, kind, message))

    def wait(self, n: int, timeout=5.0) -> list[tuple]:
        with self.cond:
            assert self.cond.wait_for(lambda: len(self.calls) >= n, timeout), self.calls
            return list(self.calls)


@pytest.fixture
def server(monkeypatch, data_dir):
    monkeypatch.setenv("GAMMA_SYNC_INTERVAL", "30")
    monkeypatch.setattr(zotero_stream, "MIN_RETRY_S", 0.01)
    with FakeStream() as fake:
        monkeypatch.setenv("GAMMA_ZOTERO_STREAM_URL", fake.url)
        try:
            yield fake
        finally:
            zotero_stream.stop()


@pytest.fixture
def clock(monkeypatch):
    fake, real = Clock(), sync_clock.get
    monkeypatch.setattr(sync_clock, "get", lambda name: fake if name == "zotero" else real(name))
    return fake


@pytest.fixture
def engine(monkeypatch):
    hooks = Engine()
    monkeypatch.setattr(zotero_sync, "forget_access", hooks.forget_access)
    monkeypatch.setattr(zotero_sync, "record_error", hooks.record_error)
    return hooks


def account(name: str, key: str | None = None) -> tuple[str, str]:
    """A new account with its personal workspace, connected to Zotero with
    ``key`` when given: (user id, workspace id)."""
    with connect_users_db() as conn:
        user_id = insert_account(conn, name, "")
        conn.commit()
    ws = workspaces.ensure_personal(user_id)
    if key:
        connect_key(user_id, key)
    return user_id, ws


def connect_key(user_id: str, key: str) -> None:
    """The account's Zotero connection as zotero_account.connect stores it,
    without asking Zotero: a new ``connected_at`` each time."""
    time.sleep(0.002)  # two connections never share a stamp
    value = {"user_id": 1, "username": "zs", "key": seal(key), "hint": f"…{key[-4:]}", "access": {},
             "via": "key", "connected_at": page_now()}
    update_pref(user_id, zotero_account.PREF_KEY, lambda _: value)


def link(ws: str, owner: str, library: str, *, mode="two-way", poll_s=5, version: int | None = 0,
         state_library: str | None = None) -> None:
    """A zotero_links row, and the link's listing at ``version`` (of
    ``state_library``, the link's own library by default)."""
    with connect_users_db() as conn:
        conn.execute(LINKS_TABLE)
        conn.execute("INSERT OR REPLACE INTO zotero_links (workspace_id, owner, library, mode, poll_s, created_at) "
                     "VALUES (?, ?, ?, ?, ?, ?)", (ws, owner, library, mode, poll_s, page_now()))
        conn.commit()
    if version is not None:
        with connect_pages_db(ws) as conn:
            zotero_remote.set_state(conn, library=library if state_library is None else state_library,
                                    version=version)


def set_links(spaces, **fields) -> None:
    """Change the links of ``spaces`` in one transaction, so a read of the
    key set sees all of the change or none of it."""
    with connect_users_db() as conn:
        for ws in spaces:
            for name, value in fields.items():
                conn.execute(f"UPDATE zotero_links SET {name} = ? WHERE workspace_id = ?", (value, ws))
        conn.commit()


def ensure_links_table() -> None:
    with connect_users_db() as conn:
        conn.execute(LINKS_TABLE)
        conn.commit()


def stream_threads() -> list[threading.Thread]:
    return [t for t in threading.enumerate() if t.name == "zotero-stream" and t.is_alive()]


def test_the_stream_is_off_while_the_sync_loops_are_and_start_and_stop_are_idempotent(monkeypatch, data_dir):
    monkeypatch.setenv("GAMMA_ZOTERO_STREAM_URL", "ws://127.0.0.1:9")  # no Live link: never dialled
    ensure_links_table()
    monkeypatch.setenv("GAMMA_SYNC_INTERVAL", "0")
    zotero_stream.start()
    assert not zotero_stream.running() and stream_threads() == []
    zotero_stream.stop()
    zotero_stream.refresh()

    monkeypatch.setenv("GAMMA_SYNC_INTERVAL", "30")
    try:
        zotero_stream.start()
        zotero_stream.start()
        assert zotero_stream.running() and len(stream_threads()) == 1
        zotero_stream.refresh()
    finally:
        zotero_stream.stop()
    zotero_stream.stop()
    assert not zotero_stream.running() and stream_threads() == []
    try:
        zotero_stream.start()
        assert zotero_stream.running()
    finally:
        zotero_stream.stop()
    assert stream_threads() == []


def test_one_connection_subscribes_each_distinct_key_of_the_live_links_once(server, clock):
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K1)  # the same key pasted into a second account
    cat, cat_ws = account("zs_cat", K2)
    dan, dan_ws = account("zs_dan", K3)
    eve, eve_ws = account("zs_eve", K4)
    fay, fay_ws = account("zs_fay")
    gus, _ = account("zs_gus", K5)
    link(ann_ws, ann, "users/11")
    link(ben_ws, ben, "groups/5")
    link(cat_ws, cat, "users/12", mode="pull")  # receive-only links are Live too
    link(dan_ws, dan, "users/13", poll_s=30)    # polled, not Live
    link(eve_ws, eve, "users/14", mode="off")   # detached
    link(fay_ws, fay, "users/15")               # Live, but no Zotero connection
    link("zs-gone", gus, "users/16", version=None)  # its workspace is gone

    assert [tuple(lk) for lk in zotero_stream.live_links()] == sorted(
        [(ann_ws, ann, "users/11"), (ben_ws, ben, "groups/5"), (cat_ws, cat, "users/12"), (fay_ws, fay, "users/15")])
    subs = zotero_stream.subscriptions(zotero_stream.live_links())
    assert list(subs) == [K1, K2]
    assert sorted(subs[K1].workspaces) == sorted([ann_ws, ben_ws]) and len(subs[K1].since) == 2
    assert subs[K2].workspaces == (cat_ws,)

    zotero_stream.start()
    conn = server.wait_subscribed(0, [K1, K2])
    assert conn.received == [{"action": "createSubscriptions", "subscriptions": [{"apiKey": K1}, {"apiKey": K2}]}]
    time.sleep(0.3)
    assert len(server.connections) == 1 and conn.closed is None and clock.asked == []


def test_a_library_change_asks_a_round_of_each_live_link_whose_listing_is_behind(server, clock):
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K2)
    cat, cat_ws = account("zs_cat", K3)
    dan, dan_ws = account("zs_dan", K4)
    eve, eve_ws = account("zs_eve", K5)
    link(ann_ws, ann, "users/11", version=10)
    link(ben_ws, ben, "groups/5", version=20)
    link(cat_ws, cat, "groups/5", version=30)  # already listed past the change
    link(dan_ws, dan, "groups/5", version=0, poll_s=300)  # not Live: its own poll
    link(eve_ws, eve, "groups/5", version=99, state_library="groups/6")  # relinked: the state is wiped next round

    zotero_stream.start()
    conn = server.wait_subscribed(0, [K1, K2, K3, K5])
    server.send(conn, {"event": "topicUpdated", "topic": "/groups/5", "version": 25})
    assert sorted(clock.wait(2)) == sorted([(ben_ws, 0.5), (eve_ws, 0.5)])

    server.send(conn, {"event": "topicUpdated", "topic": "/users/1/publications", "version": 50})
    server.send(conn, "not json")
    server.send(conn, {"event": "somethingNew", "topic": "/users/11"})
    server.send(conn, ["not", "an", "event"])
    server.send(conn, {"event": "topicUpdated", "topic": "/users/11", "version": 10})  # nothing past the listing
    server.send(conn, {"event": "topicUpdated", "topic": "/users/11", "version": 11})
    assert clock.wait(3)[2:] == [(ann_ws, 0.5)]
    server.send(conn, {"event": "topicUpdated", "topic": "/users/11"})  # no version: a round anyway
    assert clock.wait(4)[3:] == [(ann_ws, 0.5)]
    time.sleep(0.2)
    assert len(clock.asked) == 4 and len(server.connections) == 1 and conn.closed is None

    assert zotero_stream.rounds_for_update("groups/5", 31) == sorted([ben_ws, cat_ws, eve_ws])
    assert zotero_stream.rounds_for_update("groups/77", 1) == []


def test_a_library_added_or_removed_has_the_engine_forget_the_keys_access_and_asks_a_round(server, clock, engine,
                                                                                         monkeypatch):
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K1)
    cat, cat_ws = account("zs_cat", K2)
    link(ann_ws, ann, "users/11")
    link(ben_ws, ben, "groups/5")
    link(cat_ws, cat, "groups/5")

    zotero_stream.start()
    conn = server.wait_subscribed(0, [K1, K2])
    server.send(conn, {"event": "topicAdded", "apiKey": K1, "topic": "/groups/7"})
    assert sorted(engine.wait(2)) == sorted([("forget", ann_ws), ("forget", ben_ws)])
    assert sorted(clock.wait(2)) == sorted([(ann_ws, 0.5), (ben_ws, 0.5)])

    server.send(conn, {"event": "topicRemoved", "apiKey": K2, "topic": "/groups/5"})
    assert engine.wait(3)[2:] == [("forget", cat_ws)]
    assert clock.wait(3)[2:] == [(cat_ws, 0.5)]

    # no key named: the links on the topic's library
    server.send(conn, {"event": "topicAdded", "topic": "/users/11"})
    assert clock.wait(4)[3:] == [(ann_ws, 0.5)]
    assert engine.wait(4)[3:] == [("forget", ann_ws)] and conn.closed is None


def test_the_key_set_is_read_again_new_keys_join_and_a_dropped_key_resubscribes(server, clock, monkeypatch):
    monkeypatch.setattr(zotero_stream, "RECHECK_S", 0.2)
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K2)
    cat, cat_ws = account("zs_cat", K3)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    first = server.wait_subscribed(0, [K1])

    link(ben_ws, ben, "users/12")  # a new Live link: its key joins the open connection
    server.wait_subscribed(0, [K2])
    assert first.subscribed() == [[K1], [K2]] and len(server.connections) == 1

    set_links([ann_ws], poll_s=30)  # no longer Live: its key goes, the rest subscribe anew
    second = server.wait_subscribed(1, [K2])
    server.wait(lambda: first.closed == 1000, what="the old connection was not closed")
    assert second.subscribed() == [[K2]]

    monkeypatch.setattr(zotero_stream, "RECHECK_S", 60)
    time.sleep(0.3)  # the watch now waits a minute
    link(cat_ws, cat, "groups/5")
    zotero_stream.refresh()
    server.wait_subscribed(1, [K3], timeout=3)
    assert second.subscribed() == [[K2], [K3]]

    monkeypatch.setattr(zotero_stream, "RECHECK_S", 0.2)
    zotero_stream.refresh()
    set_links([ben_ws, cat_ws], mode="off")
    server.wait(lambda: second.closed == 1000, what="the connection outlived its last Live link")
    time.sleep(0.6)
    assert len(server.connections) == 2


def test_a_key_zotero_refuses_waits_for_its_account_to_connect_again(server, clock, monkeypatch, caplog):
    monkeypatch.setattr(zotero_stream, "RECHECK_S", 0.2)
    server.refuse_keys = {K2: "Invalid API key"}
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K2)
    cat, cat_ws = account("zs_cat", K3)
    link(ann_ws, ann, "users/11")
    link(ben_ws, ben, "users/12")
    with caplog.at_level(logging.INFO, logger="gamma"):
        zotero_stream.start()
        conn = server.wait_subscribed(0, [K1, K2])
        time.sleep(0.7)  # a few checks: the refused key is not offered again
        link(cat_ws, cat, "users/13")
        server.wait_subscribed(0, [K3])
        assert conn.subscribed() == [[K1, K2], [K3]]

        connect_key(ben, K2)  # the account connects again: the key is offered again
        server.wait_subscribed(0, [K2])
        assert conn.subscribed() == [[K1, K2], [K3], [K2]] and len(server.connections) == 1
    assert "refused the subscription of key …2002: Invalid API key" in caplog.text
    assert not any(key in caplog.text for key in (K1, K2, K3))


def test_a_connection_whose_only_key_is_refused_closes_until_that_changes(server, clock, monkeypatch):
    monkeypatch.setattr(zotero_stream, "RECHECK_S", 0.2)
    server.refuse_keys = {K1: "Invalid API key"}
    ann, ann_ws = account("zs_ann", K1)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    conn = server.wait_subscribed(0, [K1])
    server.wait(lambda: conn.closed == 1000, what="a connection with nothing to listen for stayed open")
    time.sleep(0.6)
    assert len(server.connections) == 1

    server.refuse_keys = {}
    connect_key(ann, K1)
    server.wait_subscribed(1, [K1])


def test_a_dropped_connection_comes_back_after_the_servers_retry_with_backoff(server, clock, monkeypatch):
    assert zotero_stream.reconnect_delay(10, 0) == 10
    assert zotero_stream.reconnect_delay(10, 1) == 20
    assert zotero_stream.reconnect_delay(10, 5) == 320
    assert zotero_stream.reconnect_delay(10, 6) == 600
    assert zotero_stream.reconnect_delay(10, 10_000) == 600

    attempts, real = [], zotero_stream.connect

    def connect(url):
        attempts.append(time.monotonic())
        if len(attempts) in (2, 3):
            raise OSError("[WinError 1225] The remote computer refused the network connection")
        return real(url)

    monkeypatch.setattr(zotero_stream, "connect", connect)
    server.retry_ms = 100
    ann, ann_ws = account("zs_ann", K1)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    first = server.wait_subscribed(0, [K1])
    dropped = time.monotonic()
    server.close(first, 1011, "restarting")
    second = server.wait_subscribed(1, [K1], timeout=5)
    assert len(attempts) == 4
    gaps = [attempts[1] - dropped, attempts[2] - attempts[1], attempts[3] - attempts[2]]
    for gap, expected in zip(gaps, (0.1, 0.2, 0.4)):
        assert expected * 0.9 <= gap < expected + 1.5, gaps

    closed = time.monotonic()
    server.close(second, 4429, "Too many requests")  # the one 44xx that is tried again
    server.wait_subscribed(2, [K1], timeout=5)
    assert len(attempts) == 5 and attempts[4] - closed >= 0.09  # a working connection: back to retry


def test_a_server_that_never_says_connected_is_left_and_tried_again_later(server, clock, monkeypatch):
    monkeypatch.setattr(zotero_stream, "HELLO_S", 0.3)
    monkeypatch.setattr(zotero_stream, "RETRY_S", 0.1)  # no "connected" yet: the wait before any retry is known
    server.silent = True
    ann, ann_ws = account("zs_ann", K1)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    server.wait(lambda: len(server.connections) >= 2 and server.connections[0].closed is not None, timeout=5,
                what="the silent connection was not left and tried again")
    first, second = server.connections[:2]
    assert first.received == [] and second.opened_at - first.opened_at >= 0.3 + 0.1 * 0.9
    server.silent = False
    server.wait(lambda: any([K1] in c.subscribed() for c in server.connections), timeout=5,
                what="no connection subscribed once the server spoke again")


def test_a_client_error_close_stops_the_reconnects_and_marks_the_links(server, clock, engine, monkeypatch):
    monkeypatch.setattr(zotero_stream, "RECHECK_S", 0.2)
    server.retry_ms = 50
    server.close_after_subscribing = (4403, "Forbidden")
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K2)
    cat, cat_ws = account("zs_cat", K3)
    link(ann_ws, ann, "users/11")
    link(ben_ws, ben, "groups/5")
    link(cat_ws, cat, "users/13", poll_s=300)  # not on the connection
    zotero_stream.start()
    first = server.wait_subscribed(0, [K1, K2])
    calls = engine.wait(2)
    assert sorted((c[0], c[1], c[2]) for c in calls) == sorted([("error", ann_ws, "auth"), ("error", ben_ws, "auth")])
    assert "4403: Forbidden" in calls[0][3] and "Reconnect your Zotero account" in calls[0][3]
    time.sleep(0.8)  # retry is 50 ms and the key set is read every 200 ms: no second try
    assert len(server.connections) == 1 and first.closed == 4403 and len(engine.calls) == 2

    server.close_after_subscribing = None
    connect_key(ann, K1)  # a new connection of the account: try again
    second = server.wait_subscribed(1, [K1, K2])
    time.sleep(0.3)
    assert second.closed is None and len(engine.calls) == 2 and clock.asked == []


def test_an_event_whose_round_cannot_be_asked_drops_no_connection(server, monkeypatch):
    class Broken:
        def request(self, ws, delay=None):
            raise RuntimeError("the clock is gone")

    monkeypatch.setattr(sync_clock, "get", lambda name: Broken())
    ann, ann_ws = account("zs_ann", K1)
    ben, ben_ws = account("zs_ben", K2)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    conn = server.wait_subscribed(0, [K1])
    server.send(conn, {"event": "topicUpdated", "topic": "/users/11", "version": 5})
    server.send(conn, {"event": "topicAdded", "apiKey": K1, "topic": "/groups/9"})
    link(ben_ws, ben, "users/12")
    zotero_stream.refresh()
    server.wait_subscribed(0, [K2])  # the stream lives on
    assert conn.closed is None


def test_stop_closes_the_connection_and_a_new_start_opens_another(server, clock):
    ann, ann_ws = account("zs_ann", K1)
    link(ann_ws, ann, "users/11")
    zotero_stream.start()
    first = server.wait_subscribed(0, [K1])
    zotero_stream.stop()
    server.wait(lambda: first.closed == 1000, what="stop left the connection open")
    assert not zotero_stream.running() and stream_threads() == []
    zotero_stream.start()
    server.wait_subscribed(1, [K1])

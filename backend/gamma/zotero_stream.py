"""Zotero's streaming API as the trigger of Live Zotero links
(docs/dev/zotero_sync.md): Zotero says a library changed, and the link's
round starts half a second later instead of at its 30-second safety poll.

One daemon thread runs an asyncio loop that keeps one WebSocket open to
``config.zotero_stream_url()`` (wss://stream.zotero.org). When the server
says ``connected`` (with ``retry``, how many milliseconds a client should
wait before it reconnects), the stream sends ``createSubscriptions`` with
one entry per distinct key of the Live links. A Live link is a
``zotero_links`` row with mode not off and ``poll_s`` 5 whose workspace
is a personal one its owner owns (the clock's rows, ``zotero_sync_rows.LINKS``).
Each entry is the key alone, so it covers every library the key can
read. Then the stream reacts to what the server sends:

- ``subscriptionsCreated`` lists each key's topics and an ``errors`` list.
  A key named in ``errors`` is dropped until its account's connection
  changes (its ``connected_at``), so a refused key is not offered again at
  every check.
- ``topicUpdated {topic, version}``: each Live link on that library (topic
  ``/users/<id>`` or ``/groups/<id>``) whose ``zotero_state.version`` is
  below the event's version gets a round, through the clock's "zotero"
  engine (``request(ws, 0.5)``). Other topics, such as
  ``/users/<id>/publications``, are passed by.
- ``topicAdded`` / ``topicRemoved``: the key gained or lost a library. Each
  of the key's Live links has the engine forget what it read of the key's
  access (``zotero_sync.forget_access(ws)``) and gets a round.

The key set is read again every ``RECHECK_S`` seconds, or at once after
``refresh``. A new key is subscribed on the open connection. When a key is
no longer wanted the connection is closed, and the next one subscribes the
keys that are left. A connection that fails or closes is tried again after
the server's ``retry``, doubling while the tries keep failing, up to
``BACKOFF_MAX_S``. The exception is a close code from 4400 to 4499 other
than 4429, which means Zotero refuses this client. The links on that
connection then get ``last_error`` with ``error_kind`` "auth" (through
``zotero_sync.record_error``), and no connection is made until the key set
changes.

The engine is correct without the stream, because its own poll lists
Zotero's changes. The stream is off when ``GAMMA_SYNC_INTERVAL=0`` turns the
sync loops off (the tests). ``start`` and ``stop`` are idempotent.
"""

import asyncio
import contextlib
import json
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import NamedTuple

from websockets.asyncio.client import connect as _ws_connect
from websockets.exceptions import ConnectionClosed, WebSocketException

from . import config, sync_clock, zotero_account, zotero_sync
from .db import connect_pages_db, connect_users_db
from .logbuf import log
from .zotero_remote import get_state
from .zotero_sync_rows import ENGINE, LINKS, LIVE_POLL_S, LIVE_SAFETY_S

ROUND_DELAY_S = 0.5      # how long a round asked for by an event waits, so a burst is one round
RECHECK_S = 10           # how often the key set is read again
RETRY_S = 10.0           # the reconnect wait until the server names one
MIN_RETRY_S = 1.0        # the shortest reconnect wait, whatever the server says
BACKOFF_MAX_S = 600      # the longest wait between two tries
HELLO_S = 30             # how long a new connection may take to open and say "connected"
CLOSE_S = 2              # how long a close waits for the server's answer
STOP_S = 5               # how long ``stop`` waits for the thread
_TOPIC_RE = re.compile(r"/(users|groups)/(\d+)")
REFUSED = ("Zotero's live updates refused this server (WebSocket close {code}{reason}). Changes from Zotero "
           "still arrive every {every} seconds. Reconnect your Zotero account in Settings → Account & sync "
           "to get live updates again")


def connect(url: str):
    """Open the stream's WebSocket, as ``async with connect(url) as ws``.
    The tests replace it."""
    return _ws_connect(url, open_timeout=HELLO_S, close_timeout=CLOSE_S)


class Link(NamedTuple):
    workspace_id: str
    owner: str
    library: str  # "users/<id>" | "groups/<id>"


class Subscription(NamedTuple):
    since: tuple       # the connected_at of each account using the key: when it changes, the connection is new
    workspaces: tuple  # the Live links that use the key


def live_links(library: str = "") -> list[Link]:
    """The Live links the clock runs (``LINKS.where``: the workspace a
    personal one its owner owns), or only those on ``library``."""
    sql = ("SELECT t.workspace_id, t.owner, t.library FROM zotero_links t JOIN workspaces w ON w.id = t.workspace_id "
           f"WHERE t.mode != 'off' AND t.poll_s = ? AND {LINKS.where}")
    args: list = [LIVE_POLL_S]
    if library:
        sql += " AND t.library = ?"
        args.append(library)
    with connect_users_db() as conn:
        rows = conn.execute(sql + " ORDER BY t.workspace_id", args).fetchall()
    return [Link(*row) for row in rows]


def subscriptions(links) -> dict[str, Subscription]:
    """The keys of ``links``, one entry per distinct key, sorted by key.
    Links whose owner has no readable connection are left out: their rounds
    report it."""
    owners: dict[str, tuple[str, str]] = {}
    for owner in sorted({link.owner for link in links}):
        try:
            key = zotero_account.api_key(owner)
        except zotero_account.AccountError:
            continue  # sealed under another encryption key: the link's rounds ask for a reconnect
        if key:
            owners[owner] = (key, str(zotero_account.status(owner).get("connected_at") or ""))
    found: dict[str, tuple[set, list]] = {}
    for link in links:
        if link.owner in owners:
            key, since = owners[link.owner]
            stamps, spaces = found.setdefault(key, (set(), []))
            stamps.add(since)
            spaces.append(link.workspace_id)
    return {key: Subscription(tuple(sorted(stamps)), tuple(spaces)) for key, (stamps, spaces) in sorted(found.items())}


def topic_library(topic) -> str:
    """The library of a stream topic ("/users/1" → "users/1"); "" for any
    other topic."""
    match = _TOPIC_RE.fullmatch(topic) if isinstance(topic, str) else None
    return f"{match[1]}/{match[2]}" if match else ""


def listed_version(link: Link) -> int:
    """How far the link's listing of Zotero's changes has come
    (``zotero_state.version``). It is 0 when the state belongs to another
    library, because the next round wipes it, and 0 when the state cannot
    be read."""
    try:
        with connect_pages_db(link.workspace_id) as conn:
            state = get_state(conn)
    except Exception as e:  # noqa: BLE001 — the round asked for reports whatever this is
        log.info(f"[zotero-stream] {link.workspace_id}: the link's state cannot be read: {e}")
        return 0
    return int(state.get("version") or 0) if state.get("library") == link.library else 0


def rounds_for_update(library: str, version: int | None) -> list[str]:
    """``topicUpdated``: a round for each Live link on ``library`` whose
    listing is behind ``version`` (every one when the event named no
    version). Returns the workspaces asked for."""
    asked = []
    for link in live_links(library):
        if version is None or listed_version(link) < version:
            _request(link.workspace_id)
            asked.append(link.workspace_id)
    return asked


def access_changed(api_key: str, library: str) -> list[str]:
    """``topicAdded`` / ``topicRemoved``: the key's Live links (or, when the
    event named no key, the Live links on ``library``) have the engine
    forget the access it read and get a round. Returns the workspaces."""
    links = live_links()
    if api_key:
        sub = subscriptions(links).get(api_key)
        spaces = list(sub.workspaces) if sub else []
    else:
        spaces = [link.workspace_id for link in links if library and link.library == library]
    for ws in spaces:
        zotero_sync.forget_access(ws)
        _request(ws)
    return spaces


def record_refusal(spaces, code: int, reason: str) -> None:
    """A close code Zotero means as "do not come back": each link on the
    connection gets the error, ``error_kind`` "auth", through the engine's
    ``record_error``. The log gets one line either way."""
    message = REFUSED.format(code=code, reason=f": {reason[:200]}" if reason else "", every=LIVE_SAFETY_S)
    spaces = sorted(set(spaces))
    for ws in spaces:
        try:
            zotero_sync.record_error(ws, message, kind="auth")
        except Exception as e:  # noqa: BLE001 — one link's status must not keep the others'
            log.warning(f"[zotero-stream] {ws}: recording the refusal failed: {e}")
    log.warning(f"[zotero-stream] {message} ({len(spaces)} link(s))")


def reconnect_delay(retry_s: float, failures: int) -> float:
    """The wait before the next try: the server's ``retry`` after a working
    connection ends, doubled for each try in a row that failed since, at
    most ``BACKOFF_MAX_S``."""
    return min(retry_s * 2 ** min(failures, 20), BACKOFF_MAX_S)


def _request(ws: str) -> None:
    """A round of ``ws`` from the link's clock, ``ROUND_DELAY_S`` on, so a
    burst of events is one round (looked up by name, as a trigger from
    outside the engine's module asks)."""
    sync_clock.get(ENGINE).request(ws, ROUND_DELAY_S)


def _signature(subs: dict) -> frozenset:
    return frozenset((key, sub.since) for key, sub in subs.items())


def _hint(key) -> str:
    """A key as the log may show it."""
    return f"…{key[-4:]}" if isinstance(key, str) and len(key) >= 8 else "…"


def _decode(raw) -> dict:
    try:
        message = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    return message if isinstance(message, dict) else {}


def _version(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class _Stream:
    """The thread, its loop, and what the stream knows across connections."""

    def __init__(self):
        self.loop = asyncio.new_event_loop()
        self.thread = threading.Thread(target=self._run, name="zotero-stream", daemon=True)
        self.task: asyncio.Task | None = None
        self.wake = asyncio.Event()             # set by refresh: read the key set now
        self.retry_s = RETRY_S
        self.failures = 0                        # tries in a row that came to nothing
        self.refused: dict[str, tuple] = {}      # key -> its Subscription.since when Zotero refused it
        self.blocked: frozenset | None = None    # the subscriptions a 44xx close refused
        # this connection's:
        self.sent: dict[str, Subscription] = {}  # keys subscribed that Zotero did not refuse
        self.subscribed = False                  # subscriptionsCreated came: the connection works
        self.planned = False                     # closed by the stream itself, to resubscribe

    # The thread

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        # the link reads in one thread of their own, so the loop never waits on a database
        self.loop.set_default_executor(ThreadPoolExecutor(1, thread_name_prefix="zotero-stream-db"))
        self.task = self.loop.create_task(self._main())
        try:
            self.loop.run_until_complete(self.task)
        except asyncio.CancelledError:
            pass
        except Exception as e:  # noqa: BLE001 — _main never ends but by stop; say so if it does
            log.warning(f"[zotero-stream] stopped: {e}")
        finally:
            try:
                self.loop.run_until_complete(self.loop.shutdown_default_executor())
            finally:
                self.loop.close()

    def poke(self) -> None:
        with contextlib.suppress(RuntimeError):  # the loop has closed
            self.loop.call_soon_threadsafe(self.wake.set)

    def stop(self, timeout: float) -> None:
        def cancel():
            if self.task is not None:
                self.task.cancel()

        with contextlib.suppress(RuntimeError):
            self.loop.call_soon_threadsafe(cancel)
        self.thread.join(timeout)

    # The loop

    async def _main(self) -> None:
        while True:
            try:
                wanted = await self._wanted()
            except Exception as e:  # noqa: BLE001 — read again at the next check
                log.warning(f"[zotero-stream] reading the Live links failed: {e}")
                wanted = {}
            if not wanted or _signature(wanted) == self.blocked:
                await self._pause(RECHECK_S)
                continue
            self.blocked = None
            try:
                delay = await self._connection(wanted)
            except Exception as e:  # noqa: BLE001 — never the end of the stream
                log.warning(f"[zotero-stream] the connection failed: {e}")
                delay = self._next_delay()
            if delay:
                await asyncio.sleep(delay)

    async def _wanted(self) -> dict[str, Subscription]:
        """The Live links' keys, less those Zotero refused on the
        connection they still have."""
        found = await asyncio.to_thread(lambda: subscriptions(live_links()))
        for key in [k for k, since in self.refused.items() if k in found and found[k].since != since]:
            del self.refused[key]  # its account connected again: offered again
        return {key: sub for key, sub in found.items() if key not in self.refused}

    async def _pause(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self.wake.wait(), seconds)
        self.wake.clear()

    def _next_delay(self) -> float:
        delay = reconnect_delay(self.retry_s, self.failures)
        self.failures += 1
        return delay

    async def _connection(self, wanted: dict[str, Subscription]) -> float | None:
        """One connection, from its opening to its end. Returns the wait
        before the next one, or None when Zotero refused the client and
        none is made until the key set changes."""
        self.sent, self.subscribed, self.planned = {}, False, False
        code, reason = None, ""
        try:
            async with connect(config.zotero_stream_url()) as ws:
                await self._hello(ws)
                await self._subscribe(ws, wanted)
                watch = asyncio.create_task(self._watch(ws))
                try:
                    async for raw in ws:
                        await self._react(_decode(raw))
                finally:
                    watch.cancel()
                    # waited for, not awaited: a stop that lands here must not be taken for the watch's end
                    await asyncio.wait([watch])
                    if not watch.cancelled() and watch.exception() is not None:  # a send on a closing socket
                        log.info(f"[zotero-stream] the key check ended: {watch.exception()}")
                code, reason = ws.close_code, ws.close_reason or ""
        except ConnectionClosed as e:
            if e.rcvd is not None:
                code, reason = e.rcvd.code, e.rcvd.reason
        except (OSError, TimeoutError, WebSocketException) as e:
            log.info(f"[zotero-stream] cannot reach Zotero's stream: {e}")
        if self.planned:
            self.failures = 0
            return 0
        if code is not None and 4400 <= code < 4500 and code != 4429:
            refused = self.sent or wanted
            self.blocked = _signature(refused)
            spaces = [ws for sub in refused.values() for ws in sub.workspaces]
            await asyncio.to_thread(record_refusal, spaces, code, reason)
            return None
        if self.subscribed:
            self.failures = 0
        return self._next_delay()

    async def _hello(self, ws) -> None:
        async with asyncio.timeout(HELLO_S):
            while True:
                message = _decode(await ws.recv())
                if message.get("event") == "connected":
                    self._retry(message)
                    return
                await self._react(message)

    async def _subscribe(self, ws, subs: dict[str, Subscription]) -> None:
        await ws.send(json.dumps({"action": "createSubscriptions",
                                  "subscriptions": [{"apiKey": key} for key in subs]}))
        self.sent.update(subs)
        log.info(f"[zotero-stream] subscribing {len(subs)} Zotero key(s): {', '.join(map(_hint, subs))}")

    async def _watch(self, ws) -> None:
        """Every ``RECHECK_S`` (or at ``refresh``): subscribe new keys on
        this connection, or close it when a key is no longer wanted (the
        next connection subscribes the rest) or none is left."""
        while True:
            await self._pause(RECHECK_S)
            try:
                wanted = await self._wanted()
            except Exception as e:  # noqa: BLE001 — read again at the next check
                log.warning(f"[zotero-stream] reading the Live links failed: {e}")
                continue
            new = {key: sub for key, sub in wanted.items() if key not in self.sent}
            if set(self.sent) - set(wanted) or not (self.sent or new):
                self.planned = True
                await ws.close(1000, "resubscribing")
                return
            self.sent.update({key: wanted[key] for key in self.sent})  # their links as they are now
            if new:
                await self._subscribe(ws, new)

    async def _react(self, message: dict) -> None:
        event = message.get("event")
        try:
            if event == "connected":
                self._retry(message)
            elif event == "subscriptionsCreated":
                self._created(message)
            elif event == "topicUpdated":
                library = topic_library(message.get("topic"))
                if library:
                    await asyncio.to_thread(rounds_for_update, library, _version(message.get("version")))
            elif event in ("topicAdded", "topicRemoved"):
                key = message.get("apiKey") if isinstance(message.get("apiKey"), str) else ""
                await asyncio.to_thread(access_changed, key, topic_library(message.get("topic")))
        except Exception as e:  # noqa: BLE001 — one event must not drop the connection
            log.warning(f"[zotero-stream] {event}: {e}")

    def _retry(self, message: dict) -> None:
        retry = message.get("retry")
        if isinstance(retry, (int, float)) and not isinstance(retry, bool) and retry > 0:
            self.retry_s = min(max(retry / 1000, MIN_RETRY_S), BACKOFF_MAX_S)

    def _created(self, message: dict) -> None:
        for error in message.get("errors") or []:
            if not isinstance(error, dict):
                continue
            key = error.get("apiKey")
            sub = self.sent.pop(key, None) if isinstance(key, str) else None
            if sub is not None:
                self.refused[key] = sub.since
            log.warning(f"[zotero-stream] Zotero refused the subscription of key {_hint(key)}: "
                        f"{error.get('error') or 'no reason given'}")
        self.subscribed, self.failures = True, 0
        if not self.sent:
            self.wake.set()  # nothing left to listen for: the watch closes the connection


_lock = threading.Lock()
_stream: _Stream | None = None


def start() -> None:
    """Start the stream's thread, unless ``GAMMA_SYNC_INTERVAL=0`` turns the
    sync loops off or it is running already."""
    global _stream
    if config.sync_interval_s() <= 0:
        return
    with _lock:
        if _stream is not None and _stream.thread.is_alive():
            return
        _stream = _Stream()
        _stream.thread.start()


def stop(timeout: float = STOP_S) -> None:
    """Close the connection and end the thread; nothing when it is not
    running."""
    global _stream
    with _lock:
        stream, _stream = _stream, None
    if stream is not None:
        stream.stop(timeout)


def refresh() -> None:
    """Read the key set now instead of at the next check, for example after
    a link was made or its cadence changed."""
    stream = _stream
    if stream is not None:
        stream.poke()


def running() -> bool:
    """Whether the stream's thread is running."""
    stream = _stream
    return stream is not None and stream.thread.is_alive()

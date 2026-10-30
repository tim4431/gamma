"""The clock of the background sync engines: when each linked workspace gets
a round.

Two engines keep a workspace in step with something outside it: the mirror
(gamma/sync_engine.py, a copy of a workspace on another Gamma server) and the
Zotero link (gamma/zotero_sync.py). Each registers an ``Engine`` here. Its
loop gives each of its workspaces a round when it is due: its own cadence
come round (``poll_s`` seconds after the last round the loop started, 0 =
only by hand), or a round asked for with ``request`` once its quiet time is
over. Every committed write reaches ``on_commit`` (``ops.commit_listeners``):
it marks the workspace dirty (``has_local_changes``, true until a round that
started after the write ends cleanly and ``settle``s it) and, when the
workspace syncs on change, asks for a round ``debounce`` seconds on, so a
burst of typing is one round. Only the engine's own writes (its op-log
``client``) do neither. Another engine's writes count: a workspace linked to
Zotero can be another server's clone origin, and that clone's pushes are
edits to send on.

Each engine keeps its own marks and runs its own loop thread, so a slow
Zotero round never holds up a mirror's and one engine's refresh never
touches another's marks. An engine registered with ``workers`` > 1 hands
each round to a pool of that many threads of its own, so one workspace's
long round (a first fill, a large file) never holds up its other
workspaces; the round lock still keeps one workspace's rounds apart, and a
workspace whose round is in the pool is passed over until it ends. The
mirror runs its rounds on the loop thread itself (``workers`` 1). A loop
never waits on a round lock: when a workspace's round is already running
outside the loop (Sync now, a background round), it looks again
``BUSY_RETRY_S`` later. A request for a workspace that has been detached or
removed meanwhile is dropped once it is due, instead of waking the loop
every few milliseconds.

``LinkTable`` is an engine's stored rows as the clock reads them (users.db
``mirrors``, ``zotero_links``): the clock's rows, the startup reset of the
rounds a restart cut short, and the status JSON patched under one lock per
table.

``start`` runs at app startup (``sync_engine.start_loop``). It runs every
engine's ``reset``, which marks a round the previous process left running
as interrupted, and then starts the loops, unless ``GAMMA_SYNC_INTERVAL=0``
(the tests, which run rounds through the API and call ``refresh`` by hand).
"""

import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import config, ops
from .db import connect_users_db
from .logbuf import log

TICK_S = 1            # a loop looks at its rows at least this often
DEBOUNCE_S = 1.0      # the default quiet time between a local write and its round
FIRST_PASS_S = 5      # the first look after startup: a round cut short by a restart goes on at once
BUSY_RETRY_S = 5      # a workspace whose round is already running is looked at again this much later
MIN_WAIT_S = 0.05     # the shortest sleep between two looks
POLL_MAX_S = 86400    # the longest cadence (poll_s) a link takes

_engines: list["Engine"] = []


class Engine:
    """One engine's clock.

    - ``rows()`` lists the engine's workspaces as ``(ws, mode, poll_s,
      on_change)``; mode ``"off"`` (detached) never runs.
    - ``run(ws)`` is one round. The loop calls it holding ``lock(ws)``,
      which it takes without waiting.
    - ``client`` is the op-log client of the engine's own writes.
    - ``lock(ws)`` gives the workspace's round lock. Without one the clock
      keeps a lock per workspace, and the engine takes it through
      ``Engine.lock`` too.
    - ``reset()`` runs at startup.
    - ``counts(ws, page_id)`` says whether a write to that page concerns
      the engine. Without it every write does.
    - ``debounce`` is the quiet time ``request`` waits by default.
    - ``interval(ws, poll_s)`` turns a stored cadence into seconds between
      rounds. Without it the cadence is used as stored.
    - ``workers``: how many rounds of different workspaces may run at
      once, each on a thread of the engine's pool (started with the
      loop); 1 runs every round on the loop thread, one after another.

    ``pending``, ``last_run``, ``wants_change`` and ``dirty`` are plain
    dicts keyed by workspace id, and ``wake`` is an Event. They are never
    rebound, so an engine module can keep its own names for them."""

    def __init__(self, name: str, *, rows, run, client: str, lock=None, reset=None, counts=None,
                 debounce: float = DEBOUNCE_S, interval=None, workers: int = 1):
        self.name, self.client, self.debounce = name, client, debounce
        self._rows, self._run, self._lock_of, self._reset = rows, run, lock, reset
        self._counts, self._interval = counts, interval
        self._workers = max(1, int(workers))
        self._pool: ThreadPoolExecutor | None = None  # the rounds' threads while the loop runs (workers > 1)
        self._running: set[str] = set()                # workspaces whose round is in the pool
        self.pending: dict[str, float] = {}      # ws -> earliest monotonic time a requested round may run
        self.last_run: dict[str, float] = {}     # ws -> monotonic time of the last round the loop started
        self.wants_change: dict[str, bool] = {}  # ws -> a round after a local write (mode on, on_change set)
        self.dirty: dict[str, float] = {}        # ws -> monotonic time of the last write no round has settled
        self.wake = threading.Event()            # set by request: the loop looks again at once
        self._locks: dict[str, threading.Lock] = {}
        self._guard = threading.Lock()           # the dirty marks' read-then-write, the clock's own locks
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def lock(self, ws: str) -> threading.Lock:
        """The round lock of ``ws``: the engine's own, or one the clock keeps."""
        if self._lock_of is not None:
            return self._lock_of(ws)
        with self._guard:
            return self._locks.setdefault(ws, threading.Lock())

    def request(self, ws: str, delay: float | None = None) -> None:
        """A round for ``ws`` once things have been quiet for ``delay``
        seconds (the engine's ``debounce`` by default). A further request
        within the delay moves the round back, so a burst is one round."""
        self.pending[ws] = time.monotonic() + (self.debounce if delay is None else delay)
        self.wake.set()

    def on_commit(self, ws: str, client: str, page_id: str = "") -> None:
        """The ``ops.commit_listeners`` entry: a write that is not the
        engine's own marks ``ws`` dirty and, when it syncs on change, asks
        for a round."""
        if client == self.client or (self._counts is not None and not self._counts(ws, page_id)):
            return
        with self._guard:
            self.dirty[ws] = time.monotonic()
        if self.wants_change.get(ws):
            self.request(ws)

    def has_local_changes(self, ws: str) -> bool:
        """Whether a write happened that no clean round has settled yet.
        This is kept in memory only: after a restart the first pass runs a
        round anyway."""
        return ws in self.dirty

    def settle(self, ws: str, started: float) -> None:
        """A round that started at ``started`` (monotonic) ended without
        errors: every write made before it went out with it."""
        with self._guard:
            if self.dirty.get(ws, float("inf")) <= started:
                del self.dirty[ws]

    def refresh(self) -> list[tuple]:
        """Read the rows and update ``wants_change`` from them. The dict is
        updated in place, and a workspace that is still listed never reads
        as unwanted in between."""
        rows = list(self._rows())
        wants = {ws: mode != "off" and bool(on_change) for ws, mode, _, on_change in rows}
        for ws in [w for w in self.wants_change if w not in wants]:
            self.wants_change.pop(ws, None)
        self.wants_change.update(wants)
        return rows

    def due_now(self, ws: str, mode: str, poll_s: int, now: float) -> bool:
        """Whether ``ws`` gets a round at ``now``. Either a requested round's
        quiet time is over, or the cadence has come round since the last
        round the loop started."""
        if mode == "off":
            return False
        due = self.pending.get(ws)
        if due is not None and due <= now:
            return True
        every = self._interval(ws, poll_s) if self._interval is not None else poll_s
        return every > 0 and now - self.last_run.get(ws, float("-inf")) >= every

    def tick(self) -> float:
        """One look. Every workspace that is due gets its round (on the
        loop thread, or handed to the pool), a request nobody will run is
        dropped, and the answer is how long to sleep before the next look."""
        now = time.monotonic()  # taken before the rows, so a request made after it waits for the next look
        rows = self.refresh()
        live = {ws for ws, mode, _, _ in rows if mode != "off"}
        for ws, due in list(self.pending.items()):
            if due <= now and ws not in live:
                self.pending.pop(ws, None)  # detached or removed since it was asked for: nothing would run it
        for ws, mode, poll_s, _ in rows:
            if self._stop.is_set() or not self.due_now(ws, mode, int(poll_s or 0), now):
                continue
            with self._guard:
                if ws in self._running:
                    continue  # its round is in the pool: a request stays for after it (its end wakes the loop)
                if self._pool is not None and len(self._running) >= self._workers:
                    continue  # every worker is busy: still due at the next look
            self.last_run[ws] = now
            lock = self.lock(ws)
            if not lock.acquire(blocking=False):
                # its round is already running (Sync now, a background round): look again
                # shortly, or sooner if a request already asks for that
                due = self.pending.get(ws)
                self.pending[ws] = min(due, now + BUSY_RETRY_S) if due is not None and due > now else now + BUSY_RETRY_S
                continue
            self.pending.pop(ws, None)
            pool = self._pool
            if pool is None:
                self._held(ws, lock, pooled=False)
                continue
            with self._guard:
                self._running.add(ws)
            try:
                pool.submit(self._held, ws, lock, pooled=True)
            except RuntimeError:  # the pool is shutting down (``stop``): no round now
                with self._guard:
                    self._running.discard(ws)
                lock.release()
        now = time.monotonic()
        return min([TICK_S, *(max(MIN_WAIT_S, due - now) for due in list(self.pending.values()))])

    def _held(self, ws: str, lock: threading.Lock, *, pooled: bool) -> None:
        """One round of ``ws`` with its round lock held, let go after it.
        After a round of the pool the loop looks again: a round asked for
        during it is due now."""
        try:
            self._run(ws)
        except Exception as e:  # noqa: BLE001 — one workspace's round must not stop the others
            log.warning(f"[{self.name}] {ws}: {e}")
        finally:
            lock.release()
            if pooled:
                with self._guard:
                    self._running.discard(ws)
                self.wake.set()

    def queued(self, ws: str) -> bool:
        """Whether a round of ``ws`` is asked for or running in the pool."""
        with self._guard:
            running = ws in self._running
        return running or ws in self.pending

    def looping(self) -> bool:
        """Whether the loop thread runs, so a round asked for with
        ``request`` comes without anyone else running it."""
        return self._thread is not None and self._thread.is_alive() and not self._stop.is_set()

    def _loop(self) -> None:
        self._stop.wait(FIRST_PASS_S)
        while not self._stop.is_set():
            try:
                wait = self.tick()
            except Exception as e:  # noqa: BLE001 — the loop must survive anything
                log.warning(f"[{self.name}] loop: {e}")
                wait = TICK_S
            self.wake.wait(wait)
            self.wake.clear()

    def start_loop(self) -> None:
        """Start the loop thread (and, for ``workers`` > 1, the rounds'
        pool), unless it is running already."""
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        if self._workers > 1 and self._pool is None:
            self._pool = ThreadPoolExecutor(max_workers=self._workers, thread_name_prefix=f"{self.name}-round")
        self._thread = threading.Thread(target=self._loop, name=f"{self.name}-loop", daemon=True)
        self._thread.start()

    def stop(self, timeout: float | None = None) -> None:
        """Stop the loop thread after the look it is in, and the pool after
        the rounds it runs (used by the tests)."""
        self._stop.set()
        self.wake.set()
        if self._thread is not None:
            self._thread.join(timeout)
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.shutdown(wait=True)


def register(name: str, **kw) -> Engine:
    """Create the engine ``name`` (``Engine``'s keyword arguments) and add
    its ``on_commit`` to ``ops.commit_listeners``. Engine modules register
    at import. The app imports them before ``start`` runs."""
    if any(e.name == name for e in _engines):
        raise ValueError(f"a sync engine named {name!r} is registered already")
    engine = Engine(name, **kw)
    _engines.append(engine)
    ops.commit_listeners.append(engine.on_commit)
    return engine


def get(name: str) -> Engine | None:
    """The registered engine ``name``, e.g. for a trigger that asks for
    rounds from outside its engine's module."""
    return next((e for e in _engines if e.name == name), None)


def start() -> None:
    """At app startup: every engine's ``reset``, then one loop thread per
    engine unless ``GAMMA_SYNC_INTERVAL=0`` turns the loops off. Calling it
    again runs the resets again and starts only loops that are not running."""
    for engine in list(_engines):
        if engine._reset is not None:
            try:
                engine._reset()
            except Exception as e:  # noqa: BLE001 — one engine's reset must not keep the server down
                log.warning(f"[{engine.name}] reset: {e}")
    if config.sync_interval_s() <= 0:
        return
    for engine in list(_engines):
        engine.start_loop()


# --- the stored rows the clock reads ----------------------------------------------------

def json_dict(raw) -> dict:
    """A JSON column as a dict ({} for anything else: empty, malformed, not
    an object)."""
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


class LinkTable:
    """An engine's users.db table of linked workspaces (``mirrors``,
    ``zotero_links``: one row per workspace with ``mode``, ``poll_s``,
    ``on_change`` and a ``status`` JSON), as the clock and the engine read
    and write it. ``tag`` names the engine in log lines; ``where`` is a
    further SQL condition on the clock's rows (the table is ``t``, its
    workspace ``w``). The status is only ever changed by ``patch_status``,
    one read-modify-write at a time under ``guard``."""

    def __init__(self, table: str, tag: str, *, where: str = ""):
        self.table, self.tag, self.where = table, tag, where
        self.guard = threading.Lock()  # every read-modify-write of a row's status JSON

    def save(self, ws: str, **fields) -> None:
        """Write ``fields`` into the workspace's row (``status`` a dict or its JSON)."""
        if "status" in fields and not isinstance(fields["status"], str):
            fields["status"] = json.dumps(fields["status"])
        sets = ", ".join(f"{k} = ?" for k in fields)
        with connect_users_db() as conn:
            conn.execute(f"UPDATE {self.table} SET {sets} WHERE workspace_id = ?", (*fields.values(), ws))
            conn.commit()

    def status(self, ws: str) -> dict:
        """The row's status ({} for no row, or one that does not read)."""
        with connect_users_db() as conn:
            row = conn.execute(f"SELECT status FROM {self.table} WHERE workspace_id = ?", (ws,)).fetchone()
        return json_dict(row[0]) if row else {}

    def patch_status(self, ws: str, patch, *, drop=()) -> dict:
        """Change some keys of the stored status and leave the rest as they
        are now, in one read-modify-write under ``guard``, so a round and
        what is done while it runs (a detach, a force, a settings change)
        never overwrite each other's keys. ``patch`` is a dict laid over the
        status or a function of it; ``drop`` names keys removed first.
        Returns the status as saved."""
        with self.guard:
            status = {k: v for k, v in self.status(ws).items() if k not in drop}
            status = patch(status) if callable(patch) else {**status, **patch}
            self.save(ws, status=status)
        return status

    def stored_mode(self, ws: str) -> str:
        """The row's mode as stored now ("off" when there is no row)."""
        with connect_users_db() as conn:
            row = conn.execute(f"SELECT mode FROM {self.table} WHERE workspace_id = ?", (ws,)).fetchone()
        return row[0] if row else "off"

    def rows(self) -> list[tuple]:
        """The clock's rows: every row whose workspace still exists (and
        that meets ``where``), as ``(ws, mode, poll_s, on_change)``."""
        sql = (f"SELECT t.workspace_id, t.mode, t.poll_s, t.on_change FROM {self.table} t "
               f"JOIN workspaces w ON w.id = t.workspace_id")
        with connect_users_db() as conn:
            return conn.execute(sql + (f" WHERE {self.where}" if self.where else "")).fetchall()

    def reset_interrupted(self, settle=None) -> list[str]:
        """At startup: a row the previous process left ``running`` is not
        running any more (``interrupted``, its progress gone); the next
        round picks up where it was. ``settle(ws, mode, status)``, when
        given, then sees every row's status and may change it in place
        (True when it did). The workspaces marked interrupted."""
        marked = []
        with connect_users_db() as conn:
            for ws, mode, raw in conn.execute(f"SELECT workspace_id, mode, status FROM {self.table}").fetchall():
                status, changed = json_dict(raw), False
                if status.get("running"):
                    status.update(running=False, interrupted=True)
                    status.pop("progress", None)
                    marked.append(ws)
                    changed = True
                    log.warning(f"[{self.tag}] {ws}: the last round was interrupted; it continues at the next one")
                if settle is not None and settle(ws, mode, status):
                    changed = True
                if changed:
                    conn.execute(f"UPDATE {self.table} SET status = ? WHERE workspace_id = ?",
                                 (json.dumps(status), ws))
            conn.commit()
        return marked

"""Claiming the Sends an agent on this computer answers, and running them
(docs/dev/local_agents.md "Claiming and the run").

Every write of a request reaches ``on_request`` (a ``requests.change_listeners``
entry, ``register``). A Send of a work folder whose vault here has an agent
(``store.agent_of``), made in Gamma (``via`` ``session``: never a token's) by
the vault's maker, is queued for that vault; a withdraw of one that runs
sets its stop. One worker thread a vault takes its
queue in order, at most ``MAX_RUNS`` runs at once on this server, so a folder
never has two agents in its directory. A run (``_Run``):

1. claims the request (``requests.claim``: ``running``; of two takers, or a
   taker and a withdraw, one wins) after checking again that the vault, its
   agent and its maker still hold, and records the run (``store.start_run``);
2. probes the agent afresh: one not ready fails the request with its problem;
3. reads the request's entries and keeps those with something new to answer
   (``prompt.pending``), at most ``MAX_THREADS``;
4. notes the folder's files (``_snapshot``), starts one turn of the agent in
   the vault's directory with ``prompt.build`` (gamma/local_agents/events.py),
   and shows its steps as the request's detail, at most every
   ``DETAIL_EVERY_S``, never naming a path outside the folder or a whole
   command; a watchdog thread stops the turn on a withdraw, an agent turned
   off, the vault paused or removed, the server closing, or
   ``RUN_TIMEOUT_S`` after the turn began;
5. once the turn is done, compares the files, writes each answer as the
   agent's reply signed with its label (``agent_id`` ``local:<name>``, so the
   comment reads as an agent on this computer's), resolves the fixed ones,
   and sets the request ``done`` with the summary and the files changed, or
   ``failed`` with a sentence a member may read; the agent's whole error and
   its stderr stay in the run's row on this computer.

A withdraw meanwhile writes nothing more in Gamma: the run ends ``stopped``.
Every write of the runner expects the request ``running`` (``only_from``), so
a withdraw that lands between two of them is never overwritten.

Locking: ``_lock`` guards the queues, the workers, the active runs and their
stop events, and nothing else; no call into ``requests``, ``store``,
``catalog`` or a driver is made while it is held (the runner's own writes
come back through ``on_request`` on the same thread).
"""

import atexit
import collections
import os
import re
import shlex
import threading
import time
import traceback
from pathlib import Path

from fastapi import HTTPException

from ..db import account_names, connect_pages_db, connect_users_db
from ..feedback import requests
from ..feedback.entries import FeedbackError
from ..logbuf import log
from . import catalog, enabled, events, procs, prompt, store

RUN_TIMEOUT_S = 1800              # env GAMMA_AGENT_RUN_TIMEOUT: a turn running this long is stopped
MAX_RUNS = 2                      # runs at once on this server, at most one per folder
MAX_THREADS = 100                 # comments one run takes
DETAIL_EVERY_S = 3                # the request's progress is written at most this often
SNAPSHOT_FILES = 20_000           # files a snapshot of the folder notes at most
SNAPSHOT_S = 3.0                  # and how long it may take
CHANGES_NAMED = 5                 # source files the done detail names
SKIPPED_DIRS = {".git", ".gamma", "node_modules", "__pycache__", ".venv", "venv"}
# What a build writes beside the sources: a change to one of these is the
# build's, not the agent's edit.
BUILD_OUTPUTS = (".pdf", ".aux", ".log", ".synctex.gz", ".synctex", ".fls", ".fdb_latexmk", ".out", ".toc", ".bbl",
                 ".blg", ".bcf", ".run.xml", ".xdv", ".dvi", ".nav", ".snm", ".lof", ".lot", ".idx", ".ind", ".ilg",
                 ".bak")
MAX_ERROR = 16_000                # characters of a run's error record

# Why a run's stop was set: the words its request is failed with (a withdraw
# writes nothing). "link" is decided when the run ends, from what the vault
# is then (``_link_reason``).
WITHDRAWN, CLOSING, LINK = "withdrawn", "closing", "link"
STOPPED = {"off": "Stopped: the agent was turned off for this folder.",
           "paused": "Stopped: the work folder was paused.",
           "removed": "Stopped: the work folder is no longer kept on this computer.",
           "changed": "Stopped: the work folder changed on this computer.",
           CLOSING: "Stopped: Gamma is closing."}

_lock = threading.Lock()
_queues: dict[str, collections.deque] = {}     # link id -> (ws, request id) waiting, oldest first
_workers: dict[str, threading.Thread] = {}     # link id -> the thread taking its queue
_queued: set[tuple[str, str]] = set()          # (ws, request id) in some queue
_active: dict[tuple[str, str], dict] = {}      # (ws, request id) -> {link_id, stop, reason, claimed, agent}
_slots = threading.BoundedSemaphore(MAX_RUNS)
_registered = False


def _run_timeout() -> float:
    try:
        return max(0.1, float(os.environ.get("GAMMA_AGENT_RUN_TIMEOUT") or RUN_TIMEOUT_S))
    except ValueError:
        return RUN_TIMEOUT_S


# --- hearing the requests ------------------------------------------------------------

def register() -> None:
    """``on_request`` hears every request's write, and ``shutdown`` runs at
    exit; once."""
    global _registered
    if on_request not in requests.change_listeners:
        requests.change_listeners.append(on_request)
    if not _registered:
        _registered = True
        atexit.register(shutdown)


def on_request(ws: str, request_id: str) -> None:
    """A request was written: a Send this computer's agent answers is
    queued, a withdraw of one that runs stops it. Cheap (a few reads, no
    process), never blocks on a run, never raises."""
    try:
        if enabled():
            _consider(ws, request_id)
    except Exception as e:  # noqa: BLE001 — a listener never fails the write it hears of
        log.warning(f"[agents] hearing request {request_id} of workspace {ws}: {e}")


def _request_row(ws: str, request_id: str) -> tuple | None:
    """``(state, detail, requester, work folder, via)`` of the request, None
    for none."""
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT state, detail, by, work_folder, via FROM feedback_requests WHERE id = ?",
                            (request_id,)).fetchone()


def _ours(link: dict | None, requester: str, via: str) -> bool:
    """Whether a Send by ``requester``, made ``via``, is the agent's of the
    vault ``link``: made in Gamma by the vault's maker, the vault having an
    agent (a Send a token made, or one from before Sends said how they
    were made, never is)."""
    return (link is not None and via == requests.SESSION and requester == link["created_by"]
            and store.agent_of(link["id"]) is not None)


def _consider(ws: str, request_id: str) -> bool:
    """Whether the request is this computer's to answer, queued now or
    already."""
    row = _request_row(ws, request_id)
    if row is None:
        return False
    state, detail, requester, w, via = row
    key = (ws, request_id)
    if state == "failed":
        with _lock:
            run = _active.get(key)
            if run is not None and not run["stop"].is_set():
                run["reason"] = WITHDRAWN if detail == requests.WITHDRAWN else "failed"
                run["stop"].set()
        return False
    if state != "sent":
        return False
    link = requests._local_link(ws, w)
    if not _ours(link, requester, via):
        return False
    with _lock:
        run = _active.get(key)
        # A run that waits for a slot and was stopped meanwhile (its vault
        # paused and resumed, its agent changed) drops the Send unclaimed:
        # queue it again for the vault as it is now.
        if key in _queued or (run is not None and not (run["stop"].is_set() and not run["claimed"])):
            return True
        _queued.add(key)
        _queues.setdefault(link["id"], collections.deque()).append(key)
        if link["id"] not in _workers:
            worker = threading.Thread(target=_work, args=(link["id"],), name=f"agent-{link['id']}", daemon=True)
            _workers[link["id"]] = worker
            worker.start()
    return True


def waiting(ws: str, link: dict) -> list[str]:
    """The Sends of the vault ``link``'s work folder that wait (``sent``)
    and are its agent's to take (``_ours``, the vault's agent aside), oldest
    first: what turning the agent on hands it, and what the panel's
    question counts."""
    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT id, by, via FROM feedback_requests WHERE work_folder = ? AND state = 'sent' "
                            "ORDER BY rev", (link["folder_id"],)).fetchall()
    return [request_id for request_id, by, via in rows
            if via == requests.SESSION and by == link["created_by"]]


def queue_folder(ws: str, link_id: str) -> int:
    """Hand the agent of the vault ``link_id`` its work folder's Sends that
    wait (``waiting``) → how many it takes."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    if not enabled():
        return 0
    link = folder_links.get_link(link_id)
    if link is None or link["workspace_id"] != ws:
        return 0
    count = 0
    for request_id in waiting(ws, link):
        try:
            count += _consider(ws, request_id)
        except Exception as e:  # noqa: BLE001 — one request never keeps the others
            log.warning(f"[agents] queueing request {request_id} of workspace {ws}: {e}")
    return count


# --- what the routes ask ----------------------------------------------------------------

def folder_agent(ws: str, w: str) -> dict | None:
    """The agent of the vault here that keeps the work folder ``w`` of
    ``ws``, paused or not: ``{name, label, link_id, maker, set_by, set_at,
    writable}`` (``maker`` the account that made the vault, ``writable``
    whether the vault may write its directory now: not paused, not choosing
    its files), None when no vault here keeps it or it has no agent. Runs
    no process."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = [link for link in folder_links.all_links()
             if link["workspace_id"] == ws and link["folder_id"] == w and folder_links.work_folder_link(link)]
    agents = store.agents_of([link["id"] for link in links])
    for link in links:
        row = agents.get(link["id"])
        if row:
            return {"name": row["agent"], "label": catalog.label(row["agent"]), "link_id": link["id"],
                    "maker": link["created_by"], "set_by": row["set_by"], "set_at": row["set_at"],
                    "writable": not link["status"].get(folder_links.PAUSED) and not folder_links.choosing(link)}
    return None


def running(ws: str, request_id: str) -> bool:
    """Whether an agent on this computer has claimed the request and works
    on it now."""
    with _lock:
        run = _active.get((ws, request_id))
        return bool(run and run["claimed"])


def stop_link(link_id: str) -> None:
    """Stop the run of the vault ``link_id``, if one runs: its agent was
    turned off or changed, the vault paused, relinked, removed or its push
    lowered (what fits is read when the run ends). The Sends still queued
    for it are dropped from the queue: a vault that takes them again
    (``queue_folder``) finds them sent."""
    with _lock:
        for (ws, request_id), run in _active.items():
            if run["link_id"] == link_id and not run["stop"].is_set():
                run["reason"] = LINK
                run["stop"].set()
        for key in _queues.get(link_id) or ():
            _queued.discard(key)
        if link_id in _queues:
            _queues[link_id].clear()


def shutdown(timeout: float = 5.0) -> None:
    """Stop every run (Gamma is closing), then every agent's process."""
    with _lock:
        for run in _active.values():
            if not run["stop"].is_set():
                run["reason"] = CLOSING
                run["stop"].set()
        for queue in _queues.values():
            queue.clear()
        _queued.clear()
    procs.stop_all(timeout)


def wait_idle(timeout: float = 30) -> bool:
    """Whether every queue and run ends within ``timeout`` seconds (tests)."""
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        with _lock:
            if not _workers and not _active:
                return True
        time.sleep(0.05)
    with _lock:
        return not _workers and not _active


def reset() -> None:
    """Stop and forget every queue, worker and run (tests)."""
    global _slots
    with _lock:
        for run in _active.values():
            run["reason"] = run["reason"] or CLOSING
            run["stop"].set()
        for queue in _queues.values():
            queue.clear()
        _queued.clear()
    wait_idle(30)
    with _lock:
        _queues.clear()
        _workers.clear()
        _active.clear()
        _slots = threading.BoundedSemaphore(MAX_RUNS)


# --- the startup -------------------------------------------------------------------------

RECOVER_WAIT_S = 600   # how long the startup waits for a workspace behind on its migrations to be upgraded
RECOVER_POLL_S = 5


def recover() -> None:
    """At startup: the runs a server that stopped left open fail, each its
    request too ("Send again"); the agents of vaults gone meanwhile are
    forgotten; then, in the background, each vault with an agent takes the
    Sends that still wait for it (``waiting``), as turning it on does. A
    workspace behind on its migrations is waited for (the background walk
    upgrades it, ``migrations.warming``), at most ``RECOVER_WAIT_S``."""
    from .. import folder_links, migrations  # local: folder_links imports this package's modules inside functions

    for run in store.open_runs():
        label = catalog.label(run["agent"])
        try:
            with connect_pages_db(run["ws"]) as conn:
                requests.set_state(run["ws"], conn, run["request_id"], "failed",
                                   detail=f"Gamma stopped while {label} worked on this. Send again.",
                                   by_name=label, writer="agent", only_from=("running",))
        except FeedbackError:
            pass  # withdrawn, or gone: nothing to say
        except Exception as e:  # noqa: BLE001 — one workspace never keeps the others
            log.warning(f"[agents] failing the run {run['id']} left open in workspace {run['ws']}: {e}")
        store.end_run(run["id"], state="failed", error="Gamma stopped while the run was open.")
    links = {link["id"]: link for link in folder_links.all_links()}
    store.forget_links(set(links))
    agents = store.agents_of(links)

    def take_waiting():
        left, end = list(agents), time.monotonic() + RECOVER_WAIT_S
        while left:
            behind = []
            for link_id in left:
                ws = links[link_id]["workspace_id"]
                try:
                    if migrations.is_behind(ws):
                        behind.append(link_id)
                    else:
                        queue_folder(ws, link_id)
                except Exception as e:  # noqa: BLE001 — one vault never keeps the others
                    log.warning(f"[agents] the waiting Sends of vault {link_id}: {e}")
            left = behind
            if left and time.monotonic() >= end:
                log.warning(f"[agents] {len(left)} vault(s) not upgraded in time: their waiting Sends wait for the "
                            "next Send, pause or start")
                return
            if left:
                time.sleep(RECOVER_POLL_S)

    if agents:
        threading.Thread(target=take_waiting, name="agent-recover", daemon=True).start()


# --- the workers ---------------------------------------------------------------------------

def _work(link_id: str) -> None:
    """The vault's worker: its queue's runs in order, one at a time; it
    leaves (and forgets itself) in the same step that finds the queue
    empty."""
    while True:
        with _lock:
            queue = _queues.get(link_id)
            if not queue:
                _queues.pop(link_id, None)
                if _workers.get(link_id) is threading.current_thread():
                    del _workers[link_id]
                return
            key = queue.popleft()
            _queued.discard(key)
            run = {"link_id": link_id, "stop": threading.Event(), "reason": "", "claimed": False, "agent": ""}
            _active[key] = run
            slots = _slots
        try:
            with slots:
                _Run(key[0], key[1], link_id, run).go()
        except Exception:  # noqa: BLE001 — _Run never raises; a worker never dies of one run
            log.exception(f"[agents] the run of request {key[1]} in workspace {key[0]}")
        finally:
            with _lock:
                if _active.get(key) is run:
                    del _active[key]


def _snapshot(root: Path) -> tuple[dict, bool]:
    """``({path: (size, mtime_ns)}, whole)`` of the folder's files, its
    relative paths with ``/``, leaving out ``SKIPPED_DIRS`` and every
    directory whose name starts with a dot; at most ``SNAPSHOT_FILES``
    files and ``SNAPSHOT_S`` seconds, ``whole`` false when either cut the
    walk short."""
    out: dict = {}
    end = time.monotonic() + SNAPSHOT_S
    for top, dirs, names in os.walk(root):
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRS and not d.startswith(".")]
        rel_top = Path(top).relative_to(root).as_posix()
        for name in names:
            try:
                st = os.stat(os.path.join(top, name))
            except OSError:
                continue
            out[name if rel_top == "." else f"{rel_top}/{name}"] = (st.st_size, st.st_mtime_ns)
            if len(out) >= SNAPSHOT_FILES:
                return out, False
        if time.monotonic() >= end:
            return out, False
    return out, True


def _changed_files(before: tuple[dict, bool], after: tuple[dict, bool]) -> tuple[list[str], bool]:
    """``(sources, built)``: the source files added, changed or deleted
    between two snapshots, by path, and whether a build output changed.
    When either walk was cut short, a file one of them did not reach is no
    change: only the files both saw are compared."""
    (old, whole_before), (new, whole_after) = before, after
    if whole_before and whole_after:
        paths = old.keys() | new.keys()
    else:
        paths = old.keys() & new.keys()
    touched = sorted({p for p in paths if old.get(p) != new.get(p)}, key=str.casefold)
    sources = [p for p in touched if not p.lower().endswith(BUILD_OUTPUTS)]
    return sources[:store.MAX_CHANGES], len(sources) < len(touched)


def _inside(path: str) -> bool:
    """Whether a driver's path (relative when inside the folder) is the
    folder's."""
    return bool(path) and not (Path(path).is_absolute() or re.match(r"^[A-Za-z]:", path)
                               or path.startswith(("/", "\\", "../", "..\\")) or path == "..")


def _named(path: str, tracked: set[str] | None) -> str:
    """How the request's words name a file the agent touched: by its path
    when members can see it already (the vault tracks it, or an entry of
    the request names it as its source), else "a file in the folder" or "a
    file outside the folder", so a file the person left out of Gamma is
    never named to the workspace. ``tracked`` None names every file inside
    the folder (this computer's own record of the run)."""
    if not _inside(path):
        return "a file outside the folder"
    if tracked is not None and path.casefold() not in tracked:
        return "a file in the folder"
    return events.one_line(path, 200)


# A shell a CLI wraps its commands in (Codex on Windows runs each through
# PowerShell): the program a member is told of is the one inside it.
_WRAPPERS = {"powershell", "pwsh", "cmd", "bash", "sh", "zsh"}
_WRAPPER_FLAGS = re.compile(r"^(?:-(?:command|c|lc|ic|noprofile|nologo|noninteractive|executionpolicy)|/[cdsk])$",
                            re.I)


def _program(command: str) -> str:
    """The name of the program a command line runs (``latexmk`` of
    ``latexmk -pdf main.tex``, and of ``powershell.exe -Command 'latexmk
    -pdf main.tex'``), never its arguments."""
    def words(line: str) -> list[str]:
        try:
            return shlex.split(line, posix=True)
        except ValueError:
            return line.split()

    parts = words(command.replace("\\", "/")) if command.strip() else []
    for _ in range(3):  # a wrapper inside a wrapper, at most
        if not parts:
            return ""
        name = re.sub(r"\.(exe|cmd|bat|com)$", "", parts[0].strip("\"'").rsplit("/", 1)[-1], flags=re.I)
        if name.lower() not in _WRAPPERS:
            return events.one_line(name, 60)
        rest = [p for p in parts[1:] if not _WRAPPER_FLAGS.match(p) and p.lower() not in ("bypass", "unrestricted")]
        parts = words(rest[0]) if len(rest) == 1 else rest
    return ""


def _said(event: dict, tracked: set[str] | None = None) -> str:
    """What a step shows a member: ``Reading main.tex``, ``Editing
    sections/intro.tex``, ``Running latexmk``, ``Searching the sources``,
    ``Working``; a file the vault does not track is named as no file
    (``_named``)."""
    kind = event.get("kind")
    if kind in ("read", "edit"):
        what = _named(event.get("path") or "", tracked)
        return f"{'Reading' if kind == 'read' else 'Editing'} {what}"
    if kind == "run":
        program = _program(event.get("command") or "")
        return f"Running {program}" if program else "Running a command"
    if kind == "search":
        return "Searching the sources"
    return "Working"


# An absolute path left in an error once the folder's and the home
# directory's are rewritten: a drive's, a share's, a POSIX root's.
_ABSOLUTE = re.compile(r"(?<![\w.~/:\\])(?:[A-Za-z]:[\\/]|\\\\[^\s\\]+\\|/(?=[A-Za-z]))[^\s\"'`<>|,;]*")


def _redact(text: str, root: Path) -> str:
    """``text`` with the paths below the folder made relative to it, the
    home directory written ``~``, and any other absolute path "a file
    outside the folder": what a member may read of an error."""
    out = text or ""
    for base, instead in ((str(root), ""), (str(Path.home()), "~")):
        for form in dict.fromkeys((base, base.replace("\\", "/"))):
            if len(form) <= 3:  # a drive or the filesystem's root names nothing of the user's
                continue
            for sep in ("\\", "/"):
                out = out.replace(form + sep, f"{instead}/" if instead else "")
            out = out.replace(form, instead or ".")
    return _ABSOLUTE.sub("a file outside the folder", out)


def _span(seconds: float) -> str:
    if seconds >= 120:
        return f"{round(seconds / 60)} minutes"
    if seconds >= 60:
        return "1 minute"
    whole = max(1, round(seconds))
    return f"{whole} second{'' if whole == 1 else 's'}"


def _cut(text: str) -> str:
    text = " ".join((text or "").split())
    return text if len(text) <= requests.MAX_DETAIL else text[:requests.MAX_DETAIL - 1] + "…"


class _Stop(Exception):
    """The run ends here; its state and the words were set."""


class _Run:
    """One request worked by one turn of the vault's agent (the module doc's
    steps). ``go`` never raises and always ends the run it recorded."""

    def __init__(self, ws: str, request_id: str, link_id: str, run: dict):
        self.ws, self.request_id, self.link_id, self.run = ws, request_id, link_id, run
        self.stop = run["stop"]
        self.agent, self.label = "", "the agent"
        self.link: dict | None = None
        self.record: dict | None = None
        self.maker, self.maker_name = "", ""
        self.run_id = ""
        self.turn = None
        self.timed_out = False
        self.steps: list[dict] = []
        self._step_at: dict[str, int] = {}
        self.end = {"state": "failed", "detail": "", "error": ""}
        self.changes: list[str] = []
        self.built = False
        self.unwritten = False  # the request's last write failed: the run's row stays open for ``recover``
        self.tracked: set[str] = set()  # the vault's files, by path folded: what the request's words may name
        self._shown, self._shown_at = "", 0.0

    # --- the request ---------------------------------------------------------------

    def _write(self, state: str, detail: str) -> bool:
        """The request ``state`` with ``detail``, as the agent, only while
        it is running → whether it was written (False: withdrawn
        meanwhile)."""
        try:
            with connect_pages_db(self.ws) as conn:
                requests.set_state(self.ws, conn, self.request_id, state, detail=_cut(detail), by_name=self.label,
                                   writer="agent", only_from=("running",))
            return True
        except FeedbackError as e:
            if e.status == 409 or e.status == 404:
                return False
            raise

    def _fail(self, detail: str, error: str = "") -> None:
        """The request failed with ``detail`` (what members read), the run
        with ``error`` (this computer's record); stopped when the request
        was withdrawn first."""
        if self._withdrawn() or not self._write("failed", detail):
            self.end = {"state": "stopped", "detail": "", "error": error}
        else:
            self.end = {"state": "failed", "detail": _cut(detail), "error": error}
        raise _Stop

    def _withdrawn(self) -> bool:
        """Whether the request's requester withdrew it (or it is no longer
        running for any other reason): nothing more is written in Gamma."""
        if self.run["reason"] == WITHDRAWN:
            return True
        row = _request_row(self.ws, self.request_id)
        return row is None or row[0] != "running"

    def _check_stop(self) -> None:
        """At a step's end: a run stopped meanwhile ends here."""
        if self.stop.is_set():
            self._stopped()

    def _stopped(self, error: str = "") -> None:
        """The run's stop was set: a withdrawn request is left as it is
        (the run ``stopped``), any other fails with why."""
        if self._withdrawn():
            self.end = {"state": "stopped", "detail": "", "error": error}
            raise _Stop
        self._fail(self._stop_words(), error)

    def _stop_words(self) -> str:
        reason = self.run["reason"]
        if reason == CLOSING:
            return STOPPED[CLOSING]
        return STOPPED[self._link_reason()]

    def _link_reason(self) -> str:
        """Why the vault stopped its run, from what it is now."""
        from .. import folder_links  # local: folder_links imports this package's modules inside functions

        link = folder_links.get_link(self.link_id)
        if link is None:
            return "removed"
        row = store.agent_of(self.link_id)
        if row is None or row["agent"] != self.agent:
            return "off"
        if link["status"].get(folder_links.PAUSED):
            return "paused"
        return "changed"

    # --- the steps ----------------------------------------------------------------------

    def go(self) -> None:
        try:
            if not self._claim():
                return
            self._work()
        except _Stop:
            pass
        except Exception as e:  # noqa: BLE001 — a run never leaves its request running
            log.exception(f"[agents] {self.label} on request {self.request_id} in workspace {self.ws}")
            error = f"{e!r}\n{traceback.format_exc()}"
            try:
                self._fail(f"Gamma could not run {self.label}.", error)
            except _Stop:
                pass
            except Exception:  # noqa: BLE001 — the request could not be written either: recover fails it
                self.end = {"state": "failed", "detail": "", "error": error}
                self.unwritten = True
        finally:
            if self.turn is not None:
                self.turn.close()  # a turn whose events were never read still ends its process
            if self.run_id and self.unwritten:
                # The request could not be told how the run ended (its
                # database refused): the run stays open, so the next start
                # fails the request rather than leave it "running".
                log.warning(f"[agents] request {self.request_id} of workspace {self.ws} left running; "
                            "the next start fails it")
            elif self.run_id:
                store.end_run(self.run_id, state=self.end["state"], detail=self.end.get("detail", ""),
                              error=(self.end.get("error") or "")[-MAX_ERROR:],
                              session=getattr(self.turn, "session", "") or "", changes=self.changes,
                              built=self.built, steps=self.steps, pid=getattr(self.turn, "pid", 0) or 0)

    def _claim(self) -> bool:
        """Step 1: the vault, its agent and its maker checked again, the
        request taken and the run recorded → whether it is this run's."""
        from .. import workspaces

        if self.stop.is_set():
            return False  # stopped while it waited for a slot: the Send stays sent (``_consider`` queues it again)
        row = _request_row(self.ws, self.request_id)
        if row is None or row[0] != "sent":
            return False
        link = requests._local_link(self.ws, row[3])
        if link is None or link["id"] != self.link_id or not _ours(link, row[2], row[4]):
            return False
        agent = store.agent_of(link["id"])
        if agent is None or agent["agent"] not in catalog.AGENTS:
            return False
        if not workspaces.at_least(workspaces.role_of(self.ws, link["created_by"]), "editor"):
            return False
        self.link, self.agent, self.label = link, agent["agent"], catalog.label(agent["agent"])
        self.maker = link["created_by"]
        with connect_users_db() as users:
            self.maker_name = account_names(users, {self.maker}).get(self.maker, "")
        self.run["agent"] = self.agent
        with connect_pages_db(self.ws) as conn:
            self.record = requests.claim(self.ws, conn, self.request_id, by_name=self.label,
                                         detail=f"Starting {self.label}")
        if self.record is None:
            return False  # someone else took it, or it was withdrawn
        with _lock:
            self.run["claimed"] = True
        self.run_id = store.start_run(ws=self.ws, link_id=self.link_id, request_id=self.request_id, agent=self.agent)
        return True

    def _work(self) -> None:
        from .. import hosted, workspaces
        from ..feedback import entries
        from ..vault import store as vault_store

        # 2. the agent, asked afresh, and whether this server writes
        found = catalog.probe(self.agent, fresh=True)
        self._check_stop()
        if not found["ready"]:
            self._fail(found["problem"] or f"{self.label} is not ready on this computer.")
        refused = hosted.read_only()
        if not refused:
            try:
                workspaces.refuse_writes(self.ws)
            except HTTPException as e:
                refused = str(e.detail)
        if refused:
            self._fail(refused)

        # 3. what there is to answer
        files = vault_store.files(self.link_id)
        self.tracked = {path.casefold() for path in files}
        with connect_pages_db(self.ws) as conn:
            found_entries = requests.entries_of(self.ws, conn, self.record, paths=entries.paths_of(files))
        answered = {t["id"] for t in self.record["threads"] if t["answered"]}
        todo = prompt.pending(found_entries, answered)
        self.tracked |= {e["source"]["file"].casefold() for e in todo if (e.get("source") or {}).get("file")}
        if not todo:
            nothing = "Nothing new since the last answers."
            self.end = {"state": "done" if self._write("done", nothing) else "stopped",
                        "detail": nothing, "error": ""}
            return
        if len(todo) > MAX_THREADS:
            self._fail(f"{self.label} takes at most {MAX_THREADS} comments at a time: send a file or a directory of "
                       "this work folder.")
        self._show(f"Reading {len(todo)} comment{'' if len(todo) == 1 else 's'}", force=True)

        # 4. the turn
        root = Path(self.link["path"])
        before = _snapshot(root)
        self._check_stop()
        argv = catalog.command(self.agent)
        if argv is None:
            self._fail(f"{self.label} is not on this computer.")
        text = prompt.build(agent=self.agent, owner=self.maker_name, work_folder=self.record["work_folder"]["name"],
                            note=self.record["note"], entries=todo, root=root)
        self.turn = events.start(self.agent, argv=argv, cwd=str(root), prompt=text, schema=prompt.ANSWER_SCHEMA,
                                 images=prompt.picture_paths(todo, root))
        if self.turn.pid:
            store.update_run(self.run_id, pid=self.turn.pid)
        ended = threading.Event()
        threading.Thread(target=self._watch, args=(ended,), name=f"agent-watch-{self.request_id}",
                         daemon=True).start()
        try:
            done = self._follow()
        finally:
            ended.set()

        # 5. what it did, and its answers
        self.changes, self.built = _changed_files(before, _snapshot(root))
        record = self._record(done)
        if self.stop.is_set() and not self.timed_out:
            self._stopped(record)
        if not done["ok"]:
            if self.timed_out:
                self._fail(f"{self.label} took longer than {_span(_run_timeout())} and was stopped.", record)
            first = events.one_line(_redact(done.get("error") or "", root), requests.MAX_DETAIL)
            self._fail(f"{self.label} could not finish: {first or 'it stopped unexpectedly.'}", record)
        valid, summary = prompt.answers(done.get("answer"), {e["id"] for e in todo})
        if not valid:
            self._fail(f"{self.label} finished without answering in the form asked for.", record)
        if self._withdrawn():
            self.end = {"state": "stopped", "detail": "", "error": record}
            return
        written, refusal = self._answer(valid)
        if self.stop.is_set():
            self._stopped(record)
        if not written:
            self._fail(refusal or f"{self.label}'s answers could not be written.", record)
        detail = summary or f"Answered {written} of {len(todo)} comment{'' if len(todo) == 1 else 's'}"
        if not detail.endswith((".", "!", "?")):
            detail += "."
        if self.changes:  # by name only the files the vault tracks (``_named``)
            named = [p for p in self.changes if p.casefold() in self.tracked][:CHANGES_NAMED]
            more = len(self.changes) - len(named)
            if named:
                detail += f" Changed {', '.join(named)}" + (f" and {more} more." if more > 0 else ".")
            else:
                detail += f" Changed {more} file{'' if more == 1 else 's'}."
        if self.built:
            detail += " The PDF was rebuilt."
        if not self._write("done", detail):
            self.end = {"state": "stopped", "detail": "", "error": record}
            return
        self.end = {"state": "done", "detail": _cut(detail), "error": record}

    def _record(self, done: dict) -> str:
        """This computer's record of the turn: its error and the agent's
        stderr tail."""
        parts = [done.get("error") or ""]
        tail = self.turn.stderr_tail() if self.turn is not None else ""
        if tail:
            parts.append(f"stderr:\n{tail}")
        return "\n".join(p for p in parts if p)

    def _watch(self, ended: threading.Event) -> None:
        """Stop the turn on the run's stop or at its deadline, counted from
        the turn's start (not from the queue's wait)."""
        deadline = time.monotonic() + _run_timeout()
        while not ended.is_set():
            if self.stop.is_set():
                self.turn.stop()
                return
            if time.monotonic() >= deadline:
                self.timed_out = True
                self.turn.stop()
                return
            ended.wait(0.1)

    def _follow(self) -> dict:
        """The turn's events: its session and steps kept, its progress
        shown → its done."""
        done = None
        stream = self.turn.events()
        try:
            for event in stream:
                done = self._heard(event) or done
        finally:
            stream.close()  # a turn left early still ends its process (events.Turn)
        return done or events.done(ok=False, error=f"{self.label} stopped unexpectedly.")

    def _heard(self, event: dict) -> dict | None:
        """One event of the turn → itself when it is the done."""
        kind = event.get("type")
        if kind == "done":
            return event
        if kind == "session":
            store.update_run(self.run_id, session=event["id"])
        elif kind == "tool":
            # The run's own steps (this computer's record) name every file
            # in the folder; the request's words only those members see.
            said = _said(event, self.tracked)
            at = self._step_at.get(event.get("id") or "")
            if at is not None:
                self.steps[at]["status"] = event.get("status") or ""
            elif len(self.steps) < store.MAX_STEPS:
                self._step_at[event.get("id") or ""] = len(self.steps)
                self.steps.append({"kind": event.get("kind") or "other", "title": _said(event),
                                   "status": event.get("status") or ""})
            if event.get("status") == "started":
                self._show(said)
        return None

    def _show(self, detail: str, *, force: bool = False) -> None:
        """The request's progress ``detail``, at most every
        ``DETAIL_EVERY_S`` and only when it changes; a refusal (the request
        withdrawn) stops the turn."""
        now = time.monotonic()
        if self.stop.is_set() or detail == self._shown or (not force and now - self._shown_at < DETAIL_EVERY_S):
            return
        self._shown, self._shown_at = detail, now
        if not self._write("running", detail):
            with _lock:
                if not self.stop.is_set():
                    self.run["reason"] = WITHDRAWN
                    self.stop.set()

    def _answer(self, valid: list[dict]) -> tuple[int, str]:
        """Each answer written as the agent's reply, the fixed ones resolved,
        until a withdraw (the run's stop) ends the writing → ``(replies
        written, the first refusal's words)``."""
        from .. import comments
        from ..blocks_store import new_block_id
        from ..feedback import agent as feedback_agent
        from ..integrations import Connection

        # Signed as the agent's label; its id ``local:<name>`` makes the
        # comment read as an agent on this computer's (comments.LOCAL_AGENT).
        connection = Connection(user_id=self.maker, workspaces=[self.ws], scope="feedback",
                                token_id=f"{comments.LOCAL_AGENT}{self.agent}", name=self.label,
                                username=self.maker_name)
        written, refusal = 0, ""
        for item in valid:
            if self.stop.is_set():  # withdrawn meanwhile: nothing more is written in Gamma
                break
            try:
                feedback_agent.reply(connection, self.ws, "", {"thread_id": item["thread_id"], "body": item["reply"],
                                                               "id": new_block_id()})
                written += 1
                if item["outcome"] == "fixed":
                    feedback_agent.resolve(connection, self.ws, "", {"thread_id": item["thread_id"]})
            except ValueError as e:
                refusal = refusal or str(e)
        return written, refusal

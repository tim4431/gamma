"""One turn of an agent as plain events, whichever agent it is: the shapes,
the base class both drivers share (gamma/local_agents/codex.py, claude.py)
and ``start``.

A turn's ``events()`` gives, in order as they happen:

- ``{"type": "session", "id"}``: the agent's session (Codex's thread), to
  resume it later;
- ``{"type": "delta", "text"}``, only in a turn started with ``stream``:
  the next piece of the message the agent is writing; the whole ``text``
  that follows is that message, in place of what its pieces built;
- ``{"type": "text", "text"}``: a whole message the agent wrote;
- ``{"type": "tool", "id", "kind", "title", "status", ...}``: a step, once
  when it starts and once when it ends (``tool``);
- ``{"type": "ask", "id", "kind", "step", ...}``, only in a turn started
  with ``ask``: the agent asks to do what its confinement keeps from it
  (``ask``), ``id`` the driver's own key for the answer, ``step`` the id of
  the tool event it is about ("" for none);
- ``{"type": "ask_gone", "id"}``: the agent withdrew that ask;
- ``{"type": "done", "ok", "answer", "text", "error", "stopped", "usage"}``:
  exactly one, last, however the turn ends: its end, ``stop()``, a crash,
  a broken pipe, ten minutes of silence (``done``).

An ask is answered with ``answer()``, from any thread; while one waits,
silence is no idleness (an agent says nothing while it waits for a person),
and a stop declines it first. Without ``ask`` a driver asks nothing and
refuses whatever its agent asks, as it always did.

The thread that reads ``events()`` does all the work: ``stop()`` only sets
a flag, which that thread sees within ``POLL_S``; it asks the agent to stop
where it can (Codex's interrupt, Claude Code's in a turn with ``ask``,
``STOP_GRACE_S`` to end) and kills it otherwise. A turn that ends on its
own, or whose output ends, closes the CLI's stdin and gives it
``EXIT_GRACE_S`` to exit (Codex saves its thread and runs the user's
turn-end hook), then kills its process tree
(gamma/local_agents/procs.py); a stop past its grace, ten minutes of
silence, a failed protocol step (``TurnFailed``) or a crash closes stdin and
kills the tree at once. ``close()`` ends the process of a turn whose
events nobody will read.
"""

import json
import re
import threading
import time
from pathlib import Path

from ..logbuf import log
from . import procs

TURN_IDLE_S = 600     # no line for this long: the turn is stopped
POLL_S = 0.2          # how soon the reading thread sees a stop
STOP_GRACE_S = 5.0    # after an interrupt, how long the agent has to end its turn
EXIT_GRACE_S = 5.0    # at a turn's end, how long the CLI has to exit after its stdin closes
MAX_OUTPUT = 2000     # characters of a command's output a tool event carries
MAX_TITLE = 200
MAX_DIFF = 20_000     # characters of a file change's diff an ask carries
MAX_REASON = 2000

KINDS = ("read", "edit", "run", "search", "other")
# An agent's session (Codex's thread, Claude Code's session): what a driver
# resumes and a terminal is told to, nothing else.
SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
ASK_KINDS = ("command", "files", "tool")
DECISIONS = ("once", "chat", "deny")   # allow it once, allow it in this chat, decline it

# A folder's parts no agent may change, asked or not (a git hook, an agent's
# or an editor's settings run code the next time something opens them), as
# Claude Code's --restricted holds them: a directory of these anywhere in
# the path, and a dotfile at the folder's top (.gitconfig, .bashrc,
# .latexmkrc). Both drivers refuse a change to one without asking
# (``protected``).
PROTECTED_DIRS = frozenset({".git", ".codex", ".claude", ".vscode", ".husky", ".idea"})
CHANGE_REFUSED = "Gamma does not let an agent change that file."

# A log line on stderr (a timestamp first) says nothing to a person reading
# why a turn failed; a plain last line usually does.
_LOG_LINE = re.compile(r"^\W{0,3}(\d{4}-\d{2}-\d{2}[T ]\d{1,2}:\d{2}|\d{1,2}:\d{2}:\d{2})")
_ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
_FENCE = re.compile(r"^```[A-Za-z]*\s*\n(.*?)\n?```\s*$", re.S)


def tool(tool_id: str, kind: str, title: str, status: str, *, path: str = "", command: str = "",
         output: str | None = None) -> dict:
    """A step: ``kind`` one of ``KINDS``, ``title`` one line (Read
    sections/intro.tex, Run latexmk -pdf main.tex), ``status`` started,
    completed or failed; ``path`` for a read or an edit, ``command`` for a
    run, ``output`` (its tail) once a run ends."""
    event = {"type": "tool", "id": tool_id, "kind": kind, "title": one_line(title, MAX_TITLE), "status": status}
    if kind in ("read", "edit"):
        event["path"] = path
    if kind == "run":
        event["command"] = command
        if output is not None:
            event["output"] = output[-MAX_OUTPUT:]
    return event


def ask(ask_id: str, kind: str, *, step: str = "", command: str | None = None, cwd: str | None = None,
        host: str = "", paths: list[str] | None = None, diff: str | None = None, reason: str = "",
        tool: str | None = None) -> dict:
    """An ask: ``kind`` command (``command``, its ``cwd`` and the network
    ``host`` it would reach, if any), files (``paths`` and their ``diff``,
    cut to ``MAX_DIFF``, ``cut_diff``) or tool (``tool``, its name), with
    the agent's ``reason``; ``step`` the tool event it is about. Paths
    relative to the folder (``rel_path``), absolute outside it."""
    event = {"type": "ask", "id": ask_id, "kind": kind, "step": step}
    if command is not None:
        event["command"] = command
    if cwd is not None:
        event["cwd"] = cwd
    if host:
        event["host"] = host
    if paths is not None:
        event["paths"] = list(paths)
    if diff is not None:
        event["diff"] = cut_diff(diff)
    if reason:
        event["reason"] = reason[:MAX_REASON]
    if tool is not None:
        event["tool"] = tool
    return event


MAX_MODELS = 50       # models an agent's listing keeps


def model(model_id, label, efforts, effort, default) -> dict | None:
    """A model an agent offers, as Gamma keeps it: ``{id, label, efforts,
    effort, default}``, ``efforts`` the reasoning efforts it takes (none
    for a model without), ``effort`` its own default one (None when the
    agent names none), ``default`` whether the agent uses it when none is
    chosen; None for an entry without an id."""
    if not isinstance(model_id, str) or not model_id.strip():
        return None
    efforts = [e.strip()[:40] for e in efforts if isinstance(e, str) and e.strip()] if isinstance(efforts, list) else []
    effort = effort.strip()[:40] if isinstance(effort, str) and effort.strip() else None
    return {"id": model_id.strip()[:100], "label": one_line(label if isinstance(label, str) else "", 100)
            or model_id.strip()[:100], "efforts": efforts, "effort": effort, "default": default is True}


def cut_diff(text: str, limit: int = MAX_DIFF) -> str:
    """``text`` whole when it fits in ``limit`` characters, else cut at a
    line's end and closed with a line that says so: ``… (cut: N more
    characters)``, the whole within ``limit``."""
    text = text or ""
    if len(text) <= limit:
        return text
    room = max(0, limit - 40)  # the closing line's
    end = text.rfind("\n", 0, room + 1)
    kept = text[:end + 1] if end >= 0 else text[:room] + "\n"
    left = len(text) - (end + 1 if end >= 0 else room)
    return f"{kept}… (cut: {left} more characters)"


def protected(path: str, cwd: str) -> bool:
    """Whether ``path`` (a driver's, absolute or relative to ``cwd``) is a
    part of the folder ``cwd`` no agent may change (``PROTECTED_DIRS``
    anywhere in it, or a dotfile at its top). A path outside the folder is
    not the folder's: whether it may be changed is asked."""
    rel = rel_path(path, cwd)
    if not rel or rel == "." or Path(rel).is_absolute() or rel.startswith("../") or re.match(r"^[A-Za-z]:", rel):
        return False
    parts = [part for part in rel.split("/") if part and part != "."]
    if any(part.casefold() in PROTECTED_DIRS for part in parts):
        return True
    return len(parts) == 1 and parts[0].startswith(".")


def done(*, ok: bool, answer=None, text: str = "", error: str = "", stopped: bool = False,
         usage: dict | None = None) -> dict:
    return {"type": "done", "ok": ok, "answer": answer, "text": text or "", "error": "" if ok else error,
            "stopped": stopped, "usage": usage or {}}


def one_line(text: str, limit: int = MAX_TITLE) -> str:
    """The first non-empty line of ``text``, stripped, cut to ``limit``."""
    for line in str(text or "").splitlines():
        if line.strip():
            line = line.strip()
            return line if len(line) <= limit else line[:limit - 1] + "…"
    return ""


def rel_path(path: str, cwd: str) -> str:
    """``path`` relative to ``cwd`` with ``/`` when it is inside it, else
    the absolute path (a relative ``path`` is taken from ``cwd``)."""
    if not path:
        return ""
    try:
        base = Path(cwd).resolve()
        full = (Path(path) if Path(path).is_absolute() else base / path).resolve()
    except (OSError, ValueError):
        return str(path)
    try:
        return full.relative_to(base).as_posix()
    except ValueError:
        return str(full)


def parse_answer(text: str):
    """The agent's last message as JSON (a ```json fence around it
    stripped), None when it is not."""
    text = (text or "").strip()
    fenced = _FENCE.match(text)
    if fenced:
        text = fenced.group(1).strip()
    try:
        return json.loads(text) if text else None
    except ValueError:
        return None


def stderr_reason(tail: str) -> str:
    """The last line of stderr when it reads as a reason (not a timestamped
    log line), else ""."""
    lines = [_ANSI.sub("", line).strip() for line in (tail or "").splitlines()]
    lines = [line for line in lines if line]
    if not lines or _LOG_LINE.match(lines[-1]):
        return ""
    return one_line(lines[-1], 500)


class TurnFailed(Exception):
    """A turn that cannot go on: its message is ``done``'s error."""


class _Stopped(Exception):
    pass


class _Idle(Exception):
    pass


class Turn:
    """One turn of one agent, its CLI started at construction.

    ``session`` is the agent's session id ("" until it is known), ``pid``
    the CLI's process (0 when it could not start), ``env`` what a driver
    adds to the environment it runs in (``procs.spawn``). ``events()`` may be read
    once; ``stop()`` and ``answer()`` may be called from any thread, any
    number of times, and return at once. A driver subclasses it with
    ``_run`` (the events of the turn, a ``done`` last), when the agent can
    be asked to stop ``_on_stop``, and when it asks ``_reply``.
    """

    label = "The agent"

    def __init__(self, argv: list[str], *, cwd: str, ask: bool = False, env: dict | None = None):
        self.session = ""
        self.pid = 0
        self.cwd = cwd
        self.ask = ask
        self._asks: dict[str, object] = {}   # the asks waiting for an answer: key -> what the driver answers with
        self._asking = threading.Lock()
        self._stop = threading.Event()
        self._kill_at: float | None = None
        self._read_once = False
        self._error = ""  # the agent's own error message, preferred over stderr
        self.child: procs.Child | None = None
        self._spawn_error = ""
        try:
            self.child = procs.spawn(argv, cwd=cwd, env=env)
            self.pid = self.child.proc.pid
        except (OSError, ValueError) as e:
            self._spawn_error = str(e)

    # --- what callers use ---------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    def answer(self, ask_id: str, decision: str, note: str = "") -> bool:
        """The person's answer to the ask ``ask_id``: ``decision`` once,
        chat or deny (``DECISIONS``), ``note`` what a decline tells the
        agent → whether it was written (False: no such ask waits, the agent
        withdrew it or is gone). ValueError for another decision."""
        if decision not in DECISIONS:
            raise ValueError(f"a decision is one of {', '.join(DECISIONS)}")
        with self._asking:
            held = self._asks.pop(ask_id, None)
        if held is None or self.child is None:
            return False
        try:
            self._reply(held, decision, note)
        except EOFError:
            return False
        return True

    def close(self) -> None:
        """Stop the turn and kill its CLI now, whether or not ``events()``
        was read: what a caller that gives up on the turn does last.
        Idempotent; never raises."""
        self._stop.set()
        if self.child is not None:
            self.child.close_stdin()
            procs.kill_tree(self.child, grace=1.0)

    def stderr_tail(self) -> str:
        """The CLI's last 8 KB of stderr, for the run's record on this
        computer (never shown to members)."""
        return self.child.stderr_tail() if self.child else self._spawn_error

    def events(self):
        if self._read_once:
            return
        self._read_once = True
        if self.child is None:
            yield done(ok=False, error=f"{self.label} could not start: {self._spawn_error}")
            return
        end = None
        graceful = True
        try:
            for event in self._run():
                if event["type"] == "done":
                    end = event
                    break
                if event["type"] == "session":
                    self.session = event["id"]
                yield event
        except _Stopped:
            graceful = False
        except _Idle:
            graceful = False
            end = done(ok=False, error=f"{self.label} said nothing for 10 minutes.")
        except TurnFailed as e:
            graceful = False
            end = done(ok=False, error=str(e))
        except EOFError:
            end = self._ended()
        except GeneratorExit:  # the reader gave up on the turn
            graceful = False
            raise
        except Exception as e:  # noqa: BLE001 — a turn always ends with a done
            log.exception(f"[agents] a turn of {self.label}")
            graceful = False
            end = done(ok=False, error=f"Gamma lost track of {self.label}: {e}")
        finally:
            self._finish(graceful)
        if self._stop.is_set() and (end is None or not end["ok"] or self._kill_at is not None):
            end = done(ok=False, stopped=True, usage=(end or {}).get("usage"))
        yield end or done(ok=False, error=f"{self.label} stopped unexpectedly.")

    # --- for the drivers ----------------------------------------------------------

    def _run(self):
        raise NotImplementedError
        yield

    def _on_stop(self) -> float:
        """Called once, on the reading thread, when a stop is seen: ask the
        agent to stop and return how many seconds it has to end, or 0 to
        kill it now."""
        return 0.0

    def _reply(self, held, decision: str, note: str) -> None:
        """Write the answer to an ask the driver held as ``held``; EOFError
        when the agent is gone."""
        raise EOFError

    def _asked(self, ask_id: str, held) -> None:
        """An ask waits for its answer, which the driver writes with
        ``held``."""
        with self._asking:
            self._asks[ask_id] = held

    def _unasked(self, ask_id: str):
        """The ask taken back (the agent withdrew it, or Gamma answers it
        itself) → what the driver held, None when it waits no more."""
        with self._asking:
            return self._asks.pop(ask_id, None)

    def _all_asks(self) -> list:
        """Every ask that waits, taken back: what a stop declines."""
        with self._asking:
            held, self._asks = list(self._asks.values()), {}
        return held

    def _read(self, deadline: float | None = None) -> str | None:
        """The agent's next line; None once ``deadline`` (monotonic) passes.
        EOFError at the end of its output, _Stopped once a stop's grace ran
        out, _Idle after ``TURN_IDLE_S`` without a line while no ask
        waits."""
        quiet_since = time.monotonic()
        while True:
            if self._stop.is_set():
                if self._kill_at is None:
                    self._kill_at = time.monotonic() + self._on_stop()
                if time.monotonic() >= self._kill_at:
                    raise _Stopped
            line = self.child.line(POLL_S)
            if line is not None:
                return line
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return None
            if self._asks:  # the agent waits for a person: its silence is no idleness
                quiet_since = now
            if now - quiet_since >= TURN_IDLE_S:
                raise _Idle

    def _send(self, *messages: dict) -> None:
        """A JSON line to the agent per message, in one write; EOFError when
        it is gone."""
        if not self.child.send("".join(json.dumps(m, ensure_ascii=False) + "\n" for m in messages)):
            raise EOFError

    def _ended(self) -> dict:
        """The done of an agent whose output ended before its turn did:
        its own error message, else stderr's last line, else its exit."""
        code = self.child.wait(EXIT_GRACE_S)
        reason = self._error or stderr_reason(self.child.stderr_tail(wait=1.0))
        if not reason:
            reason = f"{self.label} stopped unexpectedly" + (f" (exit {code})." if code is not None else ".")
        return done(ok=False, error=reason)

    def _finish(self, graceful: bool) -> None:
        """The end of every turn: stdin closed, ``EXIT_GRACE_S`` to exit
        when the turn ended ``graceful`` (on its own), then the tree
        killed."""
        child = self.child
        child.close_stdin()
        if graceful:
            child.wait(EXIT_GRACE_S)
        procs.kill_tree(child, grace=1.0)


def start(name: str, **kw) -> Turn:
    """Start a turn of the agent ``name`` (``codex``, ``claude``): the
    driver's ``start`` with ``argv``, ``cwd``, ``prompt``, ``schema``,
    ``session``, ``images``, ``ask``, ``stream`` (its text as it is
    written, ``delta``), ``model`` and ``effort`` (one the agent listed,
    ``catalog.probe``'s ``models``; "" for its default)."""
    if name == "codex":
        from . import codex

        return codex.start(**kw)
    if name == "claude":
        from . import claude

        return claude.start(**kw)
    raise ValueError(f"no agent is named {name!r}")

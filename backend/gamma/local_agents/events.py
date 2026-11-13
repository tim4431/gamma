"""One turn of an agent as plain events, whichever agent it is: the shapes,
the base class both drivers share (gamma/local_agents/codex.py, claude.py)
and ``start``.

A turn's ``events()`` gives, in order as they happen:

- ``{"type": "session", "id"}``: the agent's session (Codex's thread), to
  resume it later;
- ``{"type": "text", "text"}``: a whole message the agent wrote;
- ``{"type": "tool", "id", "kind", "title", "status", ...}``: a step, once
  when it starts and once when it ends (``tool``);
- ``{"type": "done", "ok", "answer", "text", "error", "stopped", "usage"}``:
  exactly one, last, however the turn ends: its end, ``stop()``, a crash,
  a broken pipe, ten minutes of silence (``done``).

The thread that reads ``events()`` does all the work: ``stop()`` only sets
a flag, which that thread sees within ``POLL_S``; it asks the agent to stop
where it can (Codex's interrupt, ``STOP_GRACE_S`` to end) and kills it
otherwise. A turn that ends on its own, or whose output ends, closes the
CLI's stdin and gives it ``EXIT_GRACE_S`` to exit (Codex saves its thread
and runs the user's turn-end hook), then kills its process tree
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

KINDS = ("read", "edit", "run", "search", "other")

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
    the CLI's process (0 when it could not start). ``events()`` may be read
    once; ``stop()`` may be called from any thread, any number of times,
    and returns at once. A driver subclasses it with ``_run`` (the events of
    the turn, a ``done`` last) and, when the agent can be asked to stop,
    ``_on_stop``.
    """

    label = "The agent"

    def __init__(self, argv: list[str], *, cwd: str):
        self.session = ""
        self.pid = 0
        self.cwd = cwd
        self._stop = threading.Event()
        self._kill_at: float | None = None
        self._read_once = False
        self._error = ""  # the agent's own error message, preferred over stderr
        self.child: procs.Child | None = None
        self._spawn_error = ""
        try:
            self.child = procs.spawn(argv, cwd=cwd)
            self.pid = self.child.proc.pid
        except (OSError, ValueError) as e:
            self._spawn_error = str(e)

    # --- what callers use ---------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

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

    def _read(self, deadline: float | None = None) -> str | None:
        """The agent's next line; None once ``deadline`` (monotonic) passes.
        EOFError at the end of its output, _Stopped once a stop's grace ran
        out, _Idle after ``TURN_IDLE_S`` without a line."""
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
            if now - quiet_since >= TURN_IDLE_S:
                raise _Idle

    def _send(self, message: dict) -> None:
        """One JSON line to the agent; EOFError when it is gone."""
        if not self.child.send(json.dumps(message, ensure_ascii=False) + "\n"):
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
    ``session``, ``images``."""
    if name == "codex":
        from . import codex

        return codex.start(**kw)
    if name == "claude":
        from . import claude

        return claude.start(**kw)
    raise ValueError(f"no agent is named {name!r}")

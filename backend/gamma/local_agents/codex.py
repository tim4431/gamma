"""Codex CLI over ``codex app-server``: JSON-RPC on the CLI's stdin and
stdout, one object per line with no ``"jsonrpc"`` key (the protocol as
codex-cli 0.167.0 generates its schema).

A turn: ``initialize`` (the streaming delta notifications opted out: only
whole items are used) and ``initialized``; on Windows
``windowsSandbox/readiness``; ``config/read``; ``thread/start`` (or
``thread/resume`` of the session) in the work folder with the
workspace-write sandbox, approvals off and the override ``_quiet``;
``turn/start`` with the prompt, the pictures, the answer's schema and a
sandbox policy writable in the folder only, without network. Items map to
events (``_item``), approvals are refused (``_REFUSALS``), and
``turn/completed`` ends it. One loop reads every message in every phase:
notifications and the server's requests can come before the response a
request waits for.

A turn with ``stream`` (a chat turn) keeps ``item/agentMessage/delta``,
each a ``delta`` event before its message's whole ``text``; a turn with a
``model`` starts (or resumes) its thread with it, one with an ``effort``
asks it of ``turn/start``. What Codex offers is ``inspect``'s: the models
of ``model/list`` on a short-lived app-server, which uses no model.

A turn with ``ask`` (a chat turn of a folder whose agent may ask) starts its
thread with ``approvalPolicy`` ``on-request`` and ``approvalsReviewer``
``user`` (both checked in the answer, as the sandbox is) in the same
sandbox: Codex then asks to step outside it. A command's approval and a
file change's become ``ask`` events, keyed by the request's JSON-RPC id (a
file change's paths and diff are its item's, kept from ``item/started`` and
``item/fileChange/patchUpdated``, its diff a unified one with a header a
change, ``_diff``), a command's network host its own field. Gamma declines
at once, never asking, a command approval of another kind than a command
(input to a running terminal, a managed checkpoint) and a file change to a
protected part of the folder (``events.protected``). The answer is ``accept``,
``acceptForSession`` or ``decline`` (a note follows as a ``turn/steer``),
and a stop answers ``cancel``. ``serverRequest/resolved`` for one Gamma did
not answer withdraws it. Every other request is refused as without.

No MCP server and no web search, whatever the user's config.toml says (a
feedback run needs the folder alone, and nothing would ask before such a
tool ran). Verified on this machine (codex-cli 0.167.0, three MCP servers
in config.toml, a zero-token probe of ``thread/start`` counting
``mcpServer/startupStatus/updated``): ``config.mcp_servers = {}`` does
nothing (it merges: 6 notifications); ``mcp_servers.<name>.enabled =
false`` for every server ``config/read`` lists keeps the user's servers
down but ChatGPT's apps server (``codex_apps``) still starts (2);
``features.apps = false`` beside it leaves none (0). ``web_search =
"disabled"`` is the documented switch of the search tool. A server that
reports starting all the same fails the turn (``_dispatch``).
"""

import json
import sys
import tempfile
import threading
import time

from .. import version
from ..logbuf import log
from . import events, procs

LABEL = "Codex"
INITIALIZE_S = 30
START_S = 120          # thread/start, thread/resume, turn/start: they wait on start-up work
MAX_IMAGES = 6
SANDBOX_PROBLEM = ("Codex's Windows sandbox is not set up on this computer: open Codex once in a terminal "
                   "and finish its setup.")
CHECK_SANDBOX = sys.platform == "win32"   # module-level so a test can ask for the check anywhere

QUIET = ["item/agentMessage/delta", "item/commandExecution/outputDelta", "item/reasoning/summaryTextDelta",
         "item/reasoning/summaryPartAdded", "item/reasoning/textDelta", "item/plan/delta",
         "item/fileChange/outputDelta"]

# The server's requests and the answers that refuse them, in the shapes the
# schema requires. approvalPolicy never makes them rare (an administrator's
# requirements can still put it back to on-request: this machine's do).
# The legacy two answer with a ReviewDecision, whose refusal says why.
REFUSED = "Gamma does not let an agent do that here."
_LEGACY_REFUSAL = {"decision": {"denied": {"rejection": REFUSED}}}
_REFUSALS = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "applyPatchApproval": _LEGACY_REFUSAL,
    "execCommandApproval": _LEGACY_REFUSAL,
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "decline"},
}
# What a turn with ``ask`` relays to the person: the request's ask kind.
_ASKED = {"item/commandExecution/requestApproval": "command", "item/fileChange/requestApproval": "files"}
# A command approval's kinds a person is asked: a command (older servers
# send none). Input to a terminal already running (writeStdin: its params
# carry the original command, not what would be typed) and a managed
# checkpoint (humanCommand) are declined at once.
_COMMAND_KINDS = (None, "command")
_DECISIONS = {"once": "accept", "chat": "acceptForSession", "deny": "decline"}
APPROVAL_POLICY = "on-request"   # a turn with ask: Codex asks to step outside its sandbox


TurnFailed = events.TurnFailed


class RequestError(TurnFailed):
    """Codex answered a request with a JSON-RPC error."""


DELTA = "item/agentMessage/delta"   # what a turn with ``stream`` does not opt out of
MAX_MODEL_PAGES = 5
MODELS_S = 15                       # how long model/list may take, its pages together


def _initialize(stream: bool = False) -> dict:
    quiet = [method for method in QUIET if not (stream and method == DELTA)]
    return {"clientInfo": {"name": "gamma", "title": "Gamma", "version": version.VERSION or "dev"},
            "capabilities": {"optOutNotificationMethods": quiet}}


def _quiet(config: dict) -> dict:
    """The thread's config override that starts no MCP server and no web
    search: every server the user's config names turned off, ChatGPT's apps
    off, web search disabled."""
    servers = config.get("mcp_servers") if isinstance(config, dict) else None
    names = list(servers) if isinstance(servers, dict) else []
    return {"mcp_servers": {name: {"enabled": False} for name in names}, "features": {"apps": False},
            "web_search": "disabled"}


def _refusal(message: dict) -> dict:
    method = message.get("method")
    if method in _REFUSALS:
        return {"id": message["id"], "result": _REFUSALS[method]}
    return {"id": message["id"], "error": {"code": -32601, "message": "not supported"}}


def sandbox_ready(argv: list[str]) -> tuple[bool, str]:
    """Whether Codex's Windows sandbox is set up (``windowsSandbox/readiness``
    of a short-lived app-server): ``(True, "")`` or ``(False, problem)``.
    Elsewhere there is nothing to set up. A Codex too old to know the
    request is let through: its turn says what is wrong. Never raises."""
    if not CHECK_SANDBOX:
        return True, ""
    ready, problem, _ = inspect(argv, models=False)
    return ready, problem


def inspect(argv: list[str], *, models: bool = True) -> tuple[bool, str, list[dict]]:
    """One short-lived app-server, which uses no model: whether Codex's
    Windows sandbox is set up (``sandbox_ready``; elsewhere it is) and, with
    ``models``, the models it offers (``model/list``, its pages followed,
    the hidden ones left out, ``_models``) → ``(ready, problem, models)``.
    Models it could not list are none. Never raises."""
    try:
        child = procs.spawn(argv + ["app-server"], cwd=tempfile.gettempdir())
    except (OSError, ValueError) as e:
        return (False, f"Codex could not start: {e}", []) if CHECK_SANDBOX else (True, "", [])
    silent = (False, "Codex did not answer Gamma on this computer.", []) if CHECK_SANDBOX else (True, "", [])
    try:
        deadline = time.monotonic() + INITIALIZE_S
        if _ask(child, 1, "initialize", _initialize(), deadline) is None:
            return silent
        child.send(json.dumps({"method": "initialized"}) + "\n")
        if CHECK_SANDBOX:
            answer = _ask(child, 2, "windowsSandbox/readiness", None, deadline)
            if answer is None:
                return silent
            if "error" not in answer and (answer.get("result") or {}).get("status") != "ready":
                return False, SANDBOX_PROBLEM, []
        return True, "", _list_models(child, min(deadline, time.monotonic() + MODELS_S)) if models else []
    except Exception as e:  # noqa: BLE001 — never raises
        log.warning(f"[agents] asking Codex on this computer: {e}")
        return silent
    finally:
        child.close_stdin()
        child.wait(2)
        procs.kill_tree(child, grace=1.0)


def _list_models(child: procs.Child, deadline: float) -> list[dict]:
    """``model/list``'s every page (at most ``MAX_MODEL_PAGES``) as
    ``_models`` keeps them; [] when Codex refuses it or does not answer."""
    found, cursor = [], None
    for page in range(MAX_MODEL_PAGES):
        answer = _ask(child, 10 + page, "model/list", {"cursor": cursor} if cursor else {}, deadline)
        if answer is None or "error" in answer:
            if answer is not None:
                log.info(f"[agents] Codex did not list its models: {(answer.get('error') or {}).get('message')}")
            return []
        result = answer.get("result") or {}
        found += [m for m in result.get("data") or [] if isinstance(m, dict)]
        cursor = result.get("nextCursor")
        if not isinstance(cursor, str) or not cursor:
            break
    return _models(found)


def _models(data: list[dict]) -> list[dict]:
    """Codex's models (``{model, displayName, hidden, isDefault,
    supportedReasoningEfforts, defaultReasoningEffort}``) as Gamma keeps
    them (``events.model``), the hidden ones left out, each once."""
    out, seen = [], set()
    for item in data:
        if item.get("hidden") is True:
            continue
        efforts = [e.get("reasoningEffort") for e in item.get("supportedReasoningEfforts") or []
                   if isinstance(e, dict)]
        kept = events.model(item.get("model") or item.get("id"), item.get("displayName"), efforts,
                            item.get("defaultReasoningEffort"), item.get("isDefault"))
        if kept and kept["id"] not in seen:
            seen.add(kept["id"])
            out.append(kept)
    return out[:events.MAX_MODELS]


def _ask(child: procs.Child, request_id: int, method: str, params, deadline: float) -> dict | None:
    """Send one request and read until its response (refusing the server's
    own requests meanwhile); None at the deadline or the end of output."""
    message = {"id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    if not child.send(json.dumps(message) + "\n"):
        return None
    while time.monotonic() < deadline:
        try:
            line = child.line(events.POLL_S)
        except EOFError:
            return None
        message = _parse(line) if line else None
        if not message:
            continue
        if message.get("id") == request_id and "method" not in message:
            return message
        if "id" in message and "method" in message:
            child.send(json.dumps(_refusal(message)) + "\n")
    return None


def _diff(changes: list[dict], cwd: str) -> str:
    """A file change's changes as one unified diff, each under a header
    naming it (``--- a/<path>`` / ``+++ b/<path>``, ``/dev/null`` for an
    added or a deleted file): an update's diff as Codex gives it, an added
    or a deleted file's text (which Codex gives whole, not as a diff) a line
    each after ``+`` or ``-``."""
    out = []
    for change in changes:
        path = events.rel_path(str(change.get("path") or ""), cwd)
        kind = change.get("kind") if isinstance(change.get("kind"), dict) else {}
        text = str(change.get("diff") or "")
        if kind.get("type") == "add":
            out.append(f"--- /dev/null\n+++ b/{path}\n" + "".join(f"+{line}\n" for line in text.splitlines()))
        elif kind.get("type") == "delete":
            out.append(f"--- a/{path}\n+++ /dev/null\n" + "".join(f"-{line}\n" for line in text.splitlines()))
        else:
            moved = events.rel_path(str(kind.get("move_path") or ""), cwd) or path
            body = text if not text or text.endswith("\n") else text + "\n"
            out.append(body if text.startswith("--- ") else f"--- a/{path}\n+++ b/{moved}\n{body}")
    return "".join(out)


def _parse(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class CodexTurn(events.Turn):
    label = LABEL

    def __init__(self, argv: list[str], *, cwd: str, prompt: str, schema: dict | None, session: str,
                 images: list[str], ask: bool = False, stream: bool = False, model: str = "", effort: str = ""):
        super().__init__(argv + ["app-server"], cwd=cwd, ask=ask)
        self._prompt = prompt
        self._schema = schema
        self._resume = session
        self.stream = stream
        self._model, self._effort = model, effort
        self._images = list(images or ())[:MAX_IMAGES]
        self._next_id = 0
        self._ids = threading.Lock()     # a steer is sent from the thread that answers
        self._thread = ""
        self._turn_id = ""
        self._pending: list[dict] = []   # events met while a request waited for its response
        self._changes: dict[str, list] = {}   # a fileChange item's id -> its changes, for its ask
        self._commands: dict[str, str] = {}   # a commandExecution item that started -> its command
        self._last_text = ""
        self._usage: dict = {}
        self._completed: dict | None = None

    # --- the protocol -------------------------------------------------------------

    def _request(self, method: str, params, timeout: float, *, wait: bool = True) -> dict:
        """Send a request; with ``wait``, its result, every other message
        dispatched meanwhile. TurnFailed for a JSON-RPC error or no answer
        within ``timeout`` seconds."""
        with self._ids:
            self._next_id += 1
            request_id = self._next_id
        message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self._send(message)
        if not wait:
            return {}
        deadline = time.monotonic() + timeout
        while True:
            line = self._read(deadline)
            if line is None:
                raise TurnFailed(f"Codex did not answer {method} within {int(timeout)} seconds.")
            message = _parse(line)
            if message is None:
                continue
            if message.get("id") == request_id and "method" not in message:
                if "error" in message:
                    error = message.get("error") or {}
                    raise RequestError(str(error.get("message") or f"Codex refused {method}."))
                return message.get("result") or {}
            self._dispatch(message)

    def _dispatch(self, message: dict) -> None:
        method = message.get("method")
        if not method:
            return  # the response to a request nobody waits for (turn/interrupt)
        if "id" in message:
            if self.ask and method in _ASKED and self._ours(message.get("params") or {}):
                refused = self._unaskable(message)
                if refused is None:
                    self._pending.append(self._relay(message))
                    return
                log.info(f"[agents] Codex asked {method} ({refused}): declined")
                self._send(_refusal(message))
                self._pending += self._declined(message.get("params") or {})
                return
            log.info(f"[agents] Codex asked {method}: refused")
            self._send(_refusal(message))
            return
        params = message.get("params") or {}
        if method in ("item/started", "item/completed"):
            if self._ours(params):
                self._pending += self._item(params.get("item") or {}, started=method == "item/started")
        elif method == DELTA:
            if self.stream and self._ours(params) and params.get("delta"):
                self._pending.append({"type": "delta", "text": str(params["delta"])})
        elif method == "item/fileChange/patchUpdated":
            if self._ours(params) and params.get("itemId"):
                self._changes[str(params["itemId"])] = list(params.get("changes") or [])
        elif method == "serverRequest/resolved":
            # Resolved without Gamma's answer (the turn interrupted): the ask goes.
            ask_id = str(params.get("requestId"))
            if params.get("threadId") in (None, self._thread) and self._unasked(ask_id) is not None:
                self._pending.append({"type": "ask_gone", "id": ask_id})
        elif method == "turn/started":
            turn = params.get("turn") or {}
            if not self._turn_id and params.get("threadId") == self._thread and turn.get("id"):
                self._turn_id = turn["id"]
        elif method == "turn/completed":
            turn = params.get("turn") or {}
            if params.get("threadId") == self._thread and (not self._turn_id or turn.get("id") == self._turn_id):
                self._completed = turn
        elif method == "error":
            if self._ours(params) and not params.get("willRetry"):
                self._error = str((params.get("error") or {}).get("message") or "") or self._error
        elif method == "thread/tokenUsage/updated":
            if self._ours(params) and isinstance(params.get("tokenUsage"), dict):
                self._usage = params["tokenUsage"]
        elif method == "mcpServer/startupStatus/updated":
            # Before thread/start answers, the thread's id is not known yet:
            # any server starting then is this turn's.
            if params.get("status") in ("starting", "ready") and (
                    not self._thread or params.get("threadId") in (None, self._thread)):
                name = params.get("name") or "?"
                raise TurnFailed(f"Codex started the MCP server {name} although Gamma asked for none.")

    def _ours(self, params: dict) -> bool:
        """A notification of this thread's turn (one that comes before
        turn/start's response belongs to it)."""
        if params.get("threadId") not in (None, self._thread):
            return False
        return not self._turn_id or params.get("turnId") in (None, self._turn_id)

    def _item(self, item: dict, *, started: bool) -> list[dict]:
        kind, item_id = item.get("type"), str(item.get("id") or "")
        status = "started" if started else ("completed" if item.get("status") == "completed" else "failed")
        if kind == "commandExecution":
            command = str(item.get("command") or "")
            if started:
                self._commands[item_id] = command
            else:
                self._commands.pop(item_id, None)
            if not started and item.get("exitCode") != 0:
                status = "failed"
            return [events.tool(item_id, "run", f"Run {command}", status, command=command,
                                output=None if started else str(item.get("aggregatedOutput") or ""))]
        if kind == "fileChange":
            self._changes[item_id] = list(item.get("changes") or [])
            out = []
            for i, change in enumerate(item.get("changes") or []):
                path = events.rel_path(str((change or {}).get("path") or ""), self.cwd)
                if path:
                    out.append(events.tool(f"{item_id}:{i}", "edit", f"Edit {path}", status, path=path))
            return out
        if kind == "mcpToolCall":
            return [events.tool(item_id, "other", f"Use {item.get('server')}.{item.get('tool')}", status)]
        if kind == "webSearch":
            return [events.tool(item_id, "search", f"Search {item.get('query') or 'the web'}", status)]
        if kind == "agentMessage" and not started:
            self._last_text = str(item.get("text") or "")
            # With stream, even an empty message ends what its deltas built.
            return [{"type": "text", "text": self._last_text}] if self._last_text.strip() or self.stream else []
        return []

    def _unaskable(self, message: dict) -> str | None:
        """Why Gamma declines an approval itself, never asking: a command
        approval of another kind than a command (``_COMMAND_KINDS``), a file
        change to a protected part of the folder (``events.protected``, the
        rule Claude Code's edits are held to); None for one the person is
        asked."""
        params = message.get("params") or {}
        if _ASKED[message["method"]] == "command":
            kind = params.get("kind")
            return None if kind in _COMMAND_KINDS else f"kind {kind}"
        for change in self._changes.get(str(params.get("itemId") or "")) or []:
            kind = change.get("kind") if isinstance(change, dict) and isinstance(change.get("kind"), dict) else {}
            paths = (change.get("path"), kind.get("move_path")) if isinstance(change, dict) else ()
            if any(path and events.protected(str(path), self.cwd) for path in paths):
                return "a protected file"
        return None

    def _declined(self, params: dict) -> list[dict]:
        """The steps of an approval Gamma declined itself, failed: the
        command's when it started, or the file change's."""
        item_id = str(params.get("itemId") or "")
        if item_id in self._commands:
            command = self._commands[item_id]
            return [events.tool(item_id, "run", f"Run {command}", "failed", command=command, output=REFUSED)]
        out = []
        for i, change in enumerate(self._changes.get(item_id) or []):
            path = events.rel_path(str(change.get("path") or ""), self.cwd) if isinstance(change, dict) else ""
            if path:
                out.append(events.tool(f"{item_id}:{i}", "edit", f"Edit {path}", "failed", path=path))
        return out

    def _relay(self, message: dict) -> dict:
        """A command's or a file change's approval, held by its JSON-RPC id
        (its exact type, which the answer echoes) → its ask event. A
        command's network host is a field of its own, and named in its
        reason."""
        params = message.get("params") or {}
        ask_id, item_id = str(message["id"]), str(params.get("itemId") or "")
        self._asked(ask_id, message["id"])
        reason = str(params.get("reason") or "").strip()
        if _ASKED[message["method"]] == "command":
            host = str((params.get("networkApprovalContext") or {}).get("host") or "").strip()
            if host:
                reason = f"{reason} (network: {host})" if reason else f"Network: {host}"
            return events.ask(ask_id, "command", step=item_id, command=str(params.get("command") or ""),
                              cwd=events.rel_path(str(params.get("cwd") or self.cwd), self.cwd), host=host,
                              reason=reason)
        changes = [c for c in self._changes.get(item_id) or [] if isinstance(c, dict)]
        paths = [events.rel_path(str(c.get("path") or ""), self.cwd) for c in changes]
        return events.ask(ask_id, "files", step=f"{item_id}:0" if changes else "", paths=[p for p in paths if p],
                          diff=_diff(changes, self.cwd), reason=reason)

    def _reply(self, held, decision: str, note: str) -> None:
        self._send({"id": held, "result": {"decision": _DECISIONS[decision]}})
        if decision == "deny" and note and self._turn_id:
            # Codex's decline carries no words: they follow as input to the turn.
            self._request("turn/steer", {"threadId": self._thread, "expectedTurnId": self._turn_id,
                                         "input": [{"type": "text", "text": f"The owner declined that: {note}"}]},
                          0, wait=False)

    def _flush(self):
        pending, self._pending = self._pending, []
        return pending

    def _on_stop(self) -> float:
        for held in self._all_asks():  # a stop declines what waits, and cancel interrupts the turn
            try:
                self._send({"id": held, "result": {"decision": "cancel"}})
            except EOFError:
                return 0.0
        if not self._turn_id:
            return 0.0
        try:
            self._request("turn/interrupt", {"threadId": self._thread, "turnId": self._turn_id}, 0, wait=False)
        except EOFError:
            return 0.0
        return events.STOP_GRACE_S

    # --- the turn -----------------------------------------------------------------

    def _run(self):
        self._request("initialize", _initialize(self.stream), INITIALIZE_S)
        self._send({"method": "initialized"})
        if CHECK_SANDBOX:
            try:
                ready = self._request("windowsSandbox/readiness", None, INITIALIZE_S).get("status") == "ready"
            except RequestError:
                ready = True  # a Codex that does not know it: its turn says what is wrong
            if not ready:
                yield events.done(ok=False, error=SANDBOX_PROBLEM)
                return
        try:
            config = self._request("config/read", {"cwd": self.cwd}, INITIALIZE_S).get("config") or {}
        except RequestError as e:
            raise TurnFailed(f"Codex did not say which MCP servers it would start: {e}") from None
        thread_params = {"cwd": self.cwd, "sandbox": "workspace-write", "approvalPolicy": "never",
                         "config": _quiet(config)}
        if self.ask:
            thread_params.update(approvalPolicy=APPROVAL_POLICY, approvalsReviewer="user")
        if self._model:
            thread_params["model"] = self._model
        started = None
        if self._resume:
            try:
                started = self._request("thread/resume", {"threadId": self._resume, **thread_params,
                                                          "excludeTurns": True}, START_S)
            except RequestError as e:  # a thread that is gone: a new one does the work
                log.info(f"[agents] Codex could not resume {self._resume}: {e}")
        if started is None:
            started = self._request("thread/start", thread_params, START_S)
        yield from self._flush()
        self._thread = str((started.get("thread") or {}).get("id") or "")
        if not self._thread:
            raise TurnFailed("Codex started no thread.")
        yield {"type": "session", "id": self._thread}
        if (started.get("sandbox") or {}).get("type") != "workspaceWrite":
            yield events.done(ok=False, error="Codex did not take the workspace sandbox.")
            return
        if self.ask and (started.get("approvalPolicy") != APPROVAL_POLICY
                         or started.get("approvalsReviewer") != "user"):
            # Another policy or reviewer (an administrator's, a subagent's) would not ask the person.
            yield events.done(ok=False, error="Codex did not take Gamma's way of asking you.")
            return
        turn_params = {"threadId": self._thread,
                       "input": [{"type": "text", "text": self._prompt}]
                       + [{"type": "localImage", "path": path} for path in self._images],
                       "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [self.cwd],
                                         "networkAccess": False}}
        if self._schema is not None:
            turn_params["outputSchema"] = self._schema
        if self._effort:
            turn_params["effort"] = self._effort
        turn =self._request("turn/start", turn_params, START_S).get("turn") or {}
        self._turn_id = self._turn_id or str(turn.get("id") or "")
        while True:
            yield from self._flush()
            if self._completed is not None:
                break
            message = _parse(self._read())
            if message is not None:
                self._dispatch(message)
        turn = self._completed
        status = turn.get("status")
        answer = events.parse_answer(self._last_text)
        if status == "completed":
            yield events.done(ok=True, answer=answer, text=self._last_text, usage=self._usage)
        elif status == "interrupted" and self._stop.is_set():
            yield events.done(ok=False, stopped=True, text=self._last_text, usage=self._usage)
        else:
            error = str((turn.get("error") or {}).get("message") or "") or self._error
            yield events.done(ok=False, answer=answer, text=self._last_text, usage=self._usage,
                              error=error or ("Codex stopped its turn." if status == "interrupted"
                                              else "Codex's turn failed."))


def start(*, argv: list[str], cwd: str, prompt: str, schema: dict | None = None, session: str = "",
          images: list[str] = (), ask: bool = False, stream: bool = False, model: str = "",
          effort: str = "") -> CodexTurn:
    """Start Codex's app-server in ``cwd`` for one turn of ``prompt``
    (``session`` a thread to resume; ``ask``, it may ask to step outside its
    sandbox; ``stream``, its text as it is written; ``model`` its thread's,
    ``effort`` its turn's); read the turn with ``events()``."""
    return CodexTurn(list(argv), cwd=cwd, prompt=prompt, schema=schema, session=session, images=list(images),
                     ask=ask, stream=stream, model=model, effort=effort)

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
_REFUSALS = {
    "item/commandExecution/requestApproval": {"decision": "decline"},
    "item/fileChange/requestApproval": {"decision": "decline"},
    "applyPatchApproval": {"decision": "denied"},
    "execCommandApproval": {"decision": "denied"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "mcpServer/elicitation/request": {"action": "decline"},
}


TurnFailed = events.TurnFailed


class RequestError(TurnFailed):
    """Codex answered a request with a JSON-RPC error."""


def _initialize() -> dict:
    return {"clientInfo": {"name": "gamma", "title": "Gamma", "version": version.VERSION or "dev"},
            "capabilities": {"optOutNotificationMethods": QUIET}}


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
    try:
        child = procs.spawn(argv + ["app-server"], cwd=tempfile.gettempdir())
    except (OSError, ValueError) as e:
        return False, f"Codex could not start: {e}"
    try:
        deadline = time.monotonic() + INITIALIZE_S
        if _ask(child, 1, "initialize", _initialize(), deadline) is None:
            return False, "Codex did not answer Gamma on this computer."
        child.send(json.dumps({"method": "initialized"}) + "\n")
        answer = _ask(child, 2, "windowsSandbox/readiness", None, deadline)
        if answer is None:
            return False, "Codex did not answer Gamma on this computer."
        if "error" in answer:
            return True, ""
        status = (answer.get("result") or {}).get("status")
        return (True, "") if status == "ready" else (False, SANDBOX_PROBLEM)
    except Exception as e:  # noqa: BLE001 — never raises
        log.warning(f"[agents] Codex's sandbox check: {e}")
        return False, "Codex did not answer Gamma on this computer."
    finally:
        child.close_stdin()
        child.wait(2)
        procs.kill_tree(child, grace=1.0)


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


def _parse(line: str) -> dict | None:
    try:
        message = json.loads(line)
    except ValueError:
        return None
    return message if isinstance(message, dict) else None


class CodexTurn(events.Turn):
    label = LABEL

    def __init__(self, argv: list[str], *, cwd: str, prompt: str, schema: dict | None, session: str,
                 images: list[str]):
        super().__init__(argv + ["app-server"], cwd=cwd)
        self._prompt = prompt
        self._schema = schema
        self._resume = session
        self._images = list(images or ())[:MAX_IMAGES]
        self._next_id = 0
        self._thread = ""
        self._turn_id = ""
        self._pending: list[dict] = []   # events met while a request waited for its response
        self._last_text = ""
        self._usage: dict = {}
        self._completed: dict | None = None

    # --- the protocol -------------------------------------------------------------

    def _request(self, method: str, params, timeout: float, *, wait: bool = True) -> dict:
        """Send a request; with ``wait``, its result, every other message
        dispatched meanwhile. TurnFailed for a JSON-RPC error or no answer
        within ``timeout`` seconds."""
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
            log.info(f"[agents] Codex asked {method}: refused")
            self._send(_refusal(message))
            return
        params = message.get("params") or {}
        if method in ("item/started", "item/completed"):
            if self._ours(params):
                self._pending += self._item(params.get("item") or {}, started=method == "item/started")
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
            if not started and item.get("exitCode") != 0:
                status = "failed"
            return [events.tool(item_id, "run", f"Run {command}", status, command=command,
                                output=None if started else str(item.get("aggregatedOutput") or ""))]
        if kind == "fileChange":
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
            return [{"type": "text", "text": self._last_text}] if self._last_text.strip() else []
        return []

    def _flush(self):
        pending, self._pending = self._pending, []
        return pending

    def _on_stop(self) -> float:
        if not self._turn_id:
            return 0.0
        try:
            self._request("turn/interrupt", {"threadId": self._thread, "turnId": self._turn_id}, 0, wait=False)
        except EOFError:
            return 0.0
        return events.STOP_GRACE_S

    # --- the turn -----------------------------------------------------------------

    def _run(self):
        self._request("initialize", _initialize(), INITIALIZE_S)
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
        turn_params = {"threadId": self._thread,
                       "input": [{"type": "text", "text": self._prompt}]
                       + [{"type": "localImage", "path": path} for path in self._images],
                       "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [self.cwd],
                                         "networkAccess": False}}
        if self._schema is not None:
            turn_params["outputSchema"] = self._schema
        turn = self._request("turn/start", turn_params, START_S).get("turn") or {}
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
          images: list[str] = ()) -> CodexTurn:
    """Start Codex's app-server in ``cwd`` for one turn of ``prompt``
    (``session`` a thread to resume); read the turn with ``events()``."""
    return CodexTurn(list(argv), cwd=cwd, prompt=prompt, schema=schema, session=session, images=list(images))

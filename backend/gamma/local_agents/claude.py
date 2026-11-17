"""Claude Code over ``claude -p --input-format stream-json --output-format
stream-json``: the prompt a ``user`` line on stdin, which stays open while
the turn runs, one JSON object per line on stdout, a ``result`` line last.

Claude Code gets no shell: its tools are Read, Edit, Write, Glob and Grep
(``--tools``), Bash and the web ones are denied, and it starts no MCP server
of the user's (``--strict-mcp-config``). A rule such as
``Bash(latexmk *)`` would let it run anything: latexmk takes Perl with
``-e`` and runs any ``.latexmkrc`` the agent writes. It runs
``--restricted``: no settings file is read (a folder's
``.claude/settings.json`` could otherwise add hooks, which ``-p`` runs
without asking whether the folder is trusted, and a Codex run may write
one), the file tools stay inside the working directory, and a write to a
settings, git or tool-configuration file needs a person (a planted
``.git/hooks/pre-commit`` is refused). ``acceptEdits`` accepts the other
edits inside the directory, and with permission prompts off
(``--permission-prompts none``) anything that would ask is denied. The
flags are those of Claude Code 2.1.318's ``--help``, checked against it
(the hook refused, an edit outside refused);
``--tools`` and ``--disallowedTools`` take several values, so nothing
positional follows them (the prompt is on stdin).

Without ``ask`` Gamma speaks no control protocol to it, so there is no
interrupt: ``stop()`` kills it.

A message the person steers into the turn (``steer``) is one more ``user``
line on stdin, its ``uuid`` the steer's id. Claude Code reads it once the
tool calls running finish and answers it in the same response
(``--replay-user-messages`` echoes it then, ``isReplay`` with that
``uuid``: the ``steered`` event); one that comes after the turn's last
call starts another turn in the same process, a second ``result`` line
after the first (both checked against Claude Code 2.1.235). So a
``result`` while a steer written is not echoed yet does not end the turn:
the next one does, their token reports added up. Once the turn ends, a
steer is refused.

A turn with ``ask`` (a chat turn of a folder whose agent may ask) has flags
of its own (``ASK_FLAGS``): Bash among the tools and an ask rule for it
(``--settings``, which loads under ``--restricted``), so that every command
reaches Gamma as a permission prompt (``--permission-prompt-tool stdio``,
the control protocol the Agent SDK speaks: stream-json both ways). It
writes ``initialize`` and the prompt as a user message (``client_composed``:
no ``@path`` or ``/command`` read in it) and keeps stdin open until the
turn's ``result``, since closing it cancels a prompt. A ``can_use_tool``
for Bash is an ``ask`` event keyed by its ``request_id`` (``background``
when it asks to run in the background), answered allow (with the input cut
to ``ALLOWED_INPUT``: the command as asked, in the foreground, so Stop ends
it; never a permission rule written anywhere) or deny with the owner's note
("The owner declined: …", Gamma's own "Gamma declined: …"). Gamma itself denies at once, never asking, every
other tool that reaches it: ``acceptEdits`` accepts the edits inside the
directory before it is asked, so an edit that reaches it is one
``--restricted`` leaves to a person or the handler (a settings, git or
tool-configuration file: ``.git``, ``.claude``, ``.vscode``, ``.husky``,
``.gitconfig``: ``events.protected``, the rule Codex's file changes are
held to too) or one outside the directory, and so is a Bash call with a
``blocked_path``. Its process runs with ``ASK_ENV``: each Bash call starts
in the folder, the ``cwd`` its ask names. A ``control_cancel_request`` withdraws an ask. A stop
denies what waits and interrupts the turn, ``STOP_GRACE_S`` to end.

A turn with ``stream`` (a chat turn) adds ``--include-partial-messages``:
each ``stream_event`` ``content_block_delta`` of type ``text_delta`` is a
``delta`` event, and the ``assistant`` text block that follows is the whole
message. A turn's ``model`` and ``effort`` are ``--model`` (none for the
listing's ``default``) and ``--effort``. What Claude Code offers is
``models``: ``initialize``'s answer, no message written.

A turn with ``gamma`` (Gamma's tools, gamma/local_agents/tools.py) adds
``--mcp-config`` with one HTTP server, ``gamma_turn`` (``events.MCP_NAME``):
this server's ``/mcp`` with the header ``Authorization: Bearer
${GAMMA_AGENT_MCP_TOKEN}``, which Claude Code fills from its own
environment (``events.TOKEN_ENV``, set for that process alone), so the
token is on no command line; and ``--allowedTools mcp__gamma_turn``, so its
calls run without a prompt (C2 of docs/research/agents-as-participants-plan.md,
Claude Code 2.1.318: ``--tools`` limits the built-in tools only, and the
other flags hold as they are). ``system/init`` must then name
``gamma_turn`` ``connected``, else the turn fails with why
(``events.GammaDown``); each ``mcp__gamma_turn__<tool>`` call is a
``gamma`` step named by its tool. In a
turn with ``ask`` an allowed Bash call can read the variable: the token
reads only and lives for the turn (docs/dev/local_agents.md "Trust").

A session's tool calls are worded as steps alike whether a turn makes them
or they are read from its file (``tool_started``, ``tool_ended``:
gamma/local_agents/sessions.py).
"""

import json
import tempfile
import threading
import time

from ..logbuf import log
from . import events, procs

LABEL = "Claude Code"
FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--replay-user-messages",
         "--restricted", "--permission-mode", "acceptEdits",
         "--permission-prompts", "none", "--strict-mcp-config", "--tools", "Read,Edit,Write,Glob,Grep",
         "--disallowedTools", "Bash,WebFetch,WebSearch"]
ASK_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
             "--replay-user-messages", "--restricted",
             "--permission-mode", "acceptEdits", "--permission-prompt-tool", "stdio", "--strict-mcp-config",
             "--tools", "Read,Edit,Write,Glob,Grep,Bash", "--disallowedTools", "WebFetch,WebSearch",
             "--settings", json.dumps({"permissions": {"ask": ["Bash"]}}, separators=(",", ":"))]
# A turn with ask runs each Bash call in the folder (Claude Code's documented
# setting: the shell goes back to the project's directory after every call),
# so the cwd an ask names is the one the command runs in.
ASK_ENV = {"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"}
SESSION = events.SESSION
STREAM_FLAG = "--include-partial-messages"   # a turn with stream: its text as it is written
DEFAULT_MODEL = "default"   # the listing's entry for the model Claude Code picks itself: no --model
# What asking which models Claude Code offers starts (``models``): the
# control protocol's initialize, then stdin closed with no message, so no
# model is used; no settings file and no MCP server, as a turn.
MODELS_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--restricted",
                "--strict-mcp-config"]
MODELS_S = 15
GAMMA_TOOLS = f"mcp__{events.MCP_NAME}"   # what --allowedTools names: every tool of Gamma's server
GAMMA_PREFIX = GAMMA_TOOLS + "__"         # a call of one: mcp__gamma_turn__<tool>
EDITS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
CHANGE_REFUSED = events.CHANGE_REFUSED
TOOL_REFUSED = "Gamma does not offer that tool here."
STOPPED = "The owner stopped the turn."
INIT, INTERRUPT = "gamma-init", "gamma-stop"   # the ids of Gamma's own control requests
# What an allowed Bash call runs with: the command the person read, its
# description and its timeout, nothing else (``run_in_background`` would let
# it outlive Stop and the turn).
ALLOWED_INPUT = ("command", "description", "timeout")


def allowed_input(args: dict) -> dict:
    """A Bash call's input cut to ``ALLOWED_INPUT``: the command as its ask
    showed it, the description when it is text, the timeout when it is a
    whole number."""
    out = {"command": str(args.get("command") or "")}
    if isinstance(args.get("description"), str):
        out["description"] = args["description"]
    if isinstance(args.get("timeout"), int) and not isinstance(args.get("timeout"), bool):
        out["timeout"] = args["timeout"]
    return out


def tool_started(block: dict, cwd: str, *, gamma: bool) -> dict:
    """A ``tool_use`` block as its step's started tool event: a read, an
    edit (paths relative to ``cwd``), a command, a search, with ``gamma`` a
    call of Gamma's own server a ``gamma`` step, any other tool's use."""
    name, tool_id = str(block.get("name") or ""), str(block.get("id") or "")
    args = block.get("input") if isinstance(block.get("input"), dict) else {}
    if name == "Read":
        path = events.rel_path(str(args.get("file_path") or ""), cwd)
        return events.tool(tool_id, "read", f"Read {path}", "started", path=path)
    if name in EDITS:
        path = events.rel_path(str(args.get("file_path") or args.get("notebook_path") or ""), cwd)
        return events.tool(tool_id, "edit", f"Edit {path}", "started", path=path)
    if name == "Bash":
        command_line = str(args.get("command") or "")
        return events.tool(tool_id, "run", f"Run {command_line}", "started", command=command_line)
    if name in ("Glob", "Grep"):
        return events.tool(tool_id, "search", f"Search {args.get('pattern') or 'the files'}", "started")
    if gamma and name.startswith(GAMMA_PREFIX):
        return events.gamma_step(tool_id, name[len(GAMMA_PREFIX):], "started")
    return events.tool(tool_id, "other", f"Use {name}", "started")


def tool_ended(begun: dict, block: dict) -> dict:
    """The step ``begun`` (``tool_started``'s) once its ``tool_result``
    block came: failed for an error, a command's output its content."""
    status = "failed" if block.get("is_error") else "completed"
    output = None
    if begun["kind"] == "run":
        content = block.get("content")
        if isinstance(content, list):
            content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
        output = str(content or "")
    return events.tool(begun["id"], begun["kind"], begun["title"], status, path=begun.get("path", ""),
                       command=begun.get("command", ""), output=output, name=begun.get("tool", ""))


def mcp_config(url: str) -> str:
    """``--mcp-config``'s JSON: Gamma's server at ``url``, its token named
    by its variable, which Claude Code expands from its environment."""
    return json.dumps({"mcpServers": {events.MCP_NAME: {
        "type": "http", "url": url, "headers": {"Authorization": f"Bearer ${{{events.TOKEN_ENV}}}"}}}},
        separators=(",", ":"))


def command(argv: list[str], *, session: str = "", ask: bool = False, stream: bool = False, model: str = "",
            effort: str = "", gamma_url: str = "") -> list[str]:
    """The whole command line of a turn: ``--model`` and ``--effort`` said
    each time (``--restricted`` reads no settings file that would); with
    ``gamma_url``, Gamma's server and its tools allowed (``GAMMA_TOOLS``),
    the token never in it."""
    out = list(argv) + (ASK_FLAGS if ask else FLAGS)
    if gamma_url:
        out += ["--mcp-config", mcp_config(gamma_url), "--allowedTools", GAMMA_TOOLS]
    if stream:
        out.append(STREAM_FLAG)
    if model and model != DEFAULT_MODEL:
        out += ["--model", model]
    if effort:
        out += ["--effort", effort]
    if session and SESSION.match(session):
        out += ["--resume", session]
    return out


def models(argv: list[str]) -> list[dict]:
    """The models Claude Code offers (``initialize``'s ``models``, ``{value,
    displayName, supportsEffort, supportedEffortLevels}``) as Gamma keeps
    them (``events.model``; ``default`` the entry Claude Code picks itself);
    [] when it does not say within ``MODELS_S``. Starts and closes one
    process, which uses no model. Never raises."""
    try:
        child = procs.spawn(list(argv) + MODELS_FLAGS, cwd=tempfile.gettempdir())
    except (OSError, ValueError) as e:
        log.info(f"[agents] Claude Code did not start to list its models: {e}")
        return []
    try:
        request = {"type": "control_request", "request_id": INIT, "request": {"subtype": "initialize"}}
        if not child.send(json.dumps(request) + "\n"):
            return []
        deadline = time.monotonic() + MODELS_S
        while time.monotonic() < deadline:
            try:
                line = child.line(events.POLL_S)
            except EOFError:
                return []
            try:
                message = json.loads(line) if line else None
            except ValueError:
                continue
            response = message.get("response") if isinstance(message, dict) else None
            if (message or {}).get("type") != "control_response" or not isinstance(response, dict) \
                    or response.get("request_id") != INIT:
                continue
            body = response.get("response") if isinstance(response.get("response"), dict) else {}
            out, seen = [], set()
            for item in body.get("models") or []:
                if not isinstance(item, dict):
                    continue
                kept = events.model(item.get("value"), item.get("displayName"),
                                    item.get("supportedEffortLevels") if item.get("supportsEffort") else [],
                                    None, item.get("value") == DEFAULT_MODEL)
                if kept and kept["id"] not in seen:
                    seen.add(kept["id"])
                    out.append(kept)
            return out[:events.MAX_MODELS]
        return []
    except Exception as e:  # noqa: BLE001 — never raises
        log.warning(f"[agents] asking Claude Code its models: {e}")
        return []
    finally:
        child.close_stdin()
        child.wait(2)
        procs.kill_tree(child, grace=1.0)


class ClaudeTurn(events.Turn):
    label = LABEL

    def __init__(self, argv: list[str], *, cwd: str, prompt: str, session: str, ask: bool = False,
                 stream: bool = False, model: str = "", effort: str = "", gamma: events.Gamma | None = None):
        super().__init__(command(argv, session=session, ask=ask, stream=stream, model=model, effort=effort,
                                 gamma_url=gamma.url if gamma else ""),
                         cwd=cwd, ask=ask, env=ASK_ENV if ask else None, gamma=gamma)
        self.stream = stream
        self._tools: dict[str, dict] = {}   # tool_use id → its started event
        self._steering: set[str] = set()   # the uuids of the steers written that Claude Code has not echoed
        self._ending = False               # the turn's last result came: a steer is refused
        self._steers = threading.Lock()
        self._prompted = threading.Event()  # the prompt is on stdin: a steer never goes before it
        self._results: list[dict] = []     # the results before the last, of turns a steer kept going
        if self.child is not None:
            # Claude Code waits about 3 s for stdin: the prompt goes at once,
            # from a thread of its own, as a long one may fill the pipe.
            threading.Thread(target=self._write_prompt, args=(prompt,), name=f"agent-in-{self.pid}",
                             daemon=True).start()

    def _write_prompt(self, prompt: str) -> None:
        # stdin stays open (``_finish`` closes it): the messages steered in go
        # there, and with ask the answers to its prompts
        lines = [_user_line(prompt)]
        if self.ask:
            lines.insert(0, {"type": "control_request", "request_id": INIT, "request": {"subtype": "initialize"}})
        for line in lines:
            if not self.child.send(json.dumps(line, ensure_ascii=False) + "\n"):
                return
        self._prompted.set()

    def _steer(self, text: str, steer_id: str) -> bool:
        with self._steers:
            if self._ending or not self._prompted.is_set():
                return False
            self._steering.add(steer_id)
        self._send(_user_line(text, steer_id))
        return True

    def _started(self, block: dict) -> dict | None:
        event = tool_started(block, self.cwd, gamma=bool(self.gamma_url))
        self._tools[event["id"]] = event
        return event

    def _ended_tool(self, block: dict) -> dict | None:
        begun = self._tools.pop(str(block.get("tool_use_id") or ""), None)
        return tool_ended(begun, block) if begun is not None else None

    def _connected(self, line: dict) -> None:
        """Where the turn has Gamma's tools, ``system/init``'s MCP servers
        name Gamma's ``connected``; events.GammaDown with what they say
        otherwise (a token missing or refused: Claude Code would go on
        without the tools)."""
        if not self.gamma_url:
            return
        status = next((str(s.get("status") or "") for s in line.get("mcp_servers") or []
                       if isinstance(s, dict) and s.get("name") == events.MCP_NAME), "")
        if status != "connected":
            raise events.GammaDown("Gamma's tools did not connect for Claude Code"
                                    + (f" (it says {events.one_line(status, 60)})." if status else "."))

    def _run(self):
        while True:
            try:
                line = json.loads(self._read())
            except ValueError:
                continue
            if not isinstance(line, dict):
                continue
            kind = line.get("type")
            if kind == "system" and line.get("subtype") == "init":
                session = str(line.get("session_id") or "")
                if session and session != self.session:
                    yield {"type": "session", "id": session}
                self._connected(line)
            elif kind == "assistant":
                content = (line.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        event = self._started(block)
                        if event:
                            yield event
                    elif block.get("type") == "text" and (str(block.get("text") or "").strip() or self.stream):
                        yield {"type": "text", "text": str(block["text"])}
            elif kind == "user":
                if line.get("isReplay"):
                    with self._steers:
                        taken = line.get("uuid") in self._steering
                        self._steering.discard(line.get("uuid"))
                    if taken:
                        yield events.steered(str(line["uuid"]))
                    continue
                content = (line.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        event = self._ended_tool(block)
                        if event:
                            yield event
            elif kind == "stream_event" and self.stream:
                delta = self._delta(line)
                if delta:
                    yield {"type": "delta", "text": delta}
            elif kind == "control_request" and self.ask:
                yield from self._control(line)
            elif kind == "control_cancel_request" and self.ask:
                request_id = str(line.get("request_id") or "")
                if self._unasked(request_id) is not None:
                    yield {"type": "ask_gone", "id": request_id}
            elif kind == "result":
                with self._steers:
                    self._ending = not self._steering
                if not self._ending:
                    self._results.append(line)  # a steer not read yet: Claude Code starts a turn for it
                    continue
                self._all_asks()  # the turn is over: nothing waits on an answer any more
                yield self._result(line)
                return

    @staticmethod
    def _delta(line: dict) -> str:
        """The text a partial message's line adds (a ``content_block_delta``
        of type ``text_delta``, the main thread's), "" for any other."""
        if line.get("parent_tool_use_id"):
            return ""
        event = line.get("event") if isinstance(line.get("event"), dict) else {}
        delta = event.get("delta") if isinstance(event.get("delta"), dict) else {}
        if event.get("type") != "content_block_delta" or delta.get("type") != "text_delta":
            return ""
        return str(delta.get("text") or "")

    # --- the permission prompts (a turn with ask) ---------------------------------------

    def _control(self, line: dict):
        """A control request of the CLI's: a Bash call's prompt an ask, any
        other tool's denied at once (its step failed), any other subtype
        answered with an error."""
        request_id = str(line.get("request_id") or "")
        request = line.get("request") if isinstance(line.get("request"), dict) else {}
        if request.get("subtype") != "can_use_tool":
            self._send({"type": "control_response", "response": {
                "subtype": "error", "request_id": request_id,
                "error": f"Gamma does not answer {str(request.get('subtype') or 'that')}."}})
            return
        name, tool_id = str(request.get("tool_name") or ""), str(request.get("tool_use_id") or "")
        args = request.get("input") if isinstance(request.get("input"), dict) else {}
        if name == "Bash" and not request.get("blocked_path"):
            self._asked(request_id, (request_id, tool_id, args))
            yield events.ask(request_id, "command", step=tool_id, command=str(args.get("command") or ""), cwd=".",
                             reason=str(args.get("description") or "").strip(),
                             background=bool(args.get("run_in_background")))
            return
        refused = CHANGE_REFUSED if name in EDITS or name == "Bash" else TOOL_REFUSED
        self._respond(request_id, {"behavior": "deny", "message": refused, "toolUseID": tool_id})
        begun = self._tools.pop(tool_id, None)
        if begun is not None:
            yield events.tool(tool_id, begun["kind"], begun["title"], "failed", path=begun.get("path", ""),
                              command=begun.get("command", ""), output=refused if begun["kind"] == "run" else None)

    def _respond(self, request_id: str, response: dict) -> None:
        self._send({"type": "control_response",
                    "response": {"subtype": "success", "request_id": request_id, "response": response}})

    def _reply(self, held, decision: str, note: str, by: str) -> None:
        request_id, tool_id, args = held
        if decision == "deny" and by == "gamma":
            answer = {"behavior": "deny", "message": f"Gamma declined: {note}" if note else "Gamma declined."}
        elif decision == "deny":
            answer = {"behavior": "deny", "message": "The owner declined" + (f": {note}" if note else ".")}
        else:  # once or in this chat: this call, as asked and shown; Gamma keeps the chat's grant itself
            answer = {"behavior": "allow", "updatedInput": allowed_input(args)}
        self._respond(request_id, {**answer, "toolUseID": tool_id})

    def _on_stop(self) -> float:
        """With ``ask``, what waits denied and the turn interrupted; without,
        nothing to ask (its stdin is closed): killed."""
        if not self.ask:
            return 0.0
        try:
            for request_id, tool_id, _ in self._all_asks():
                self._respond(request_id, {"behavior": "deny", "message": STOPPED, "toolUseID": tool_id})
            self._send({"type": "control_request", "request_id": INTERRUPT, "request": {"subtype": "interrupt"}})
        except EOFError:
            return 0.0
        return events.STOP_GRACE_S

    def _result(self, line: dict) -> dict:
        """The done of the turn's last ``result``, with the token reports of
        the results before it added in."""
        ok = line.get("subtype") == "success" and not line.get("is_error")
        text = line.get("result") if isinstance(line.get("result"), str) else ""
        usage = {}
        for each in [*self._results, line]:
            reported = each.get("usage") if isinstance(each.get("usage"), dict) else {}
            for key, value in reported.items():
                counted = isinstance(value, int) and not isinstance(value, bool)
                usage[key] = usage.get(key, 0) + value if counted else value
        return events.done(ok=ok, text=text, usage=usage,
                           error=text or str(line.get("subtype") or "") or "Claude Code's turn failed.")


def _user_line(text: str, uuid: str = "") -> dict:
    """A message on stdin: the prompt, or a message steered in under its id
    (``uuid``, which the echo names); ``client_composed``, so Claude Code
    reads no ``@path`` or ``/command`` in it."""
    return {"type": "user", "session_id": "", "message": {"role": "user", "content": [{"type": "text", "text": text}]},
            "parent_tool_use_id": None, "client_composed": True, **({"uuid": uuid} if uuid else {})}


def start(*, argv: list[str], cwd: str, prompt: str, session: str = "", images: list[str] = (), ask: bool = False,
          stream: bool = False, model: str = "", effort: str = "", gamma: events.Gamma | None = None) -> ClaudeTurn:
    """Start ``claude -p`` in ``cwd`` for one turn of ``prompt`` (``session``
    one to resume; ``ask``, it may ask to run commands; ``stream``, its text
    as it is written; ``model``, ``effort``; ``gamma``, Gamma's tools).
    ``images`` are not passed: the prompt names the pictures, which Claude
    Code opens with Read."""
    return ClaudeTurn(list(argv), cwd=cwd, prompt=prompt, session=session, ask=ask, stream=stream, model=model,
                      effort=effort, gamma=gamma)

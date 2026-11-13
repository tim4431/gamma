"""Claude Code over ``claude -p --output-format stream-json``: the prompt on
stdin, one JSON object per line on stdout, a ``result`` line last.

Claude Code gets no shell: its tools are Read, Edit, Write, Glob and Grep
(``--tools``), Bash and the web ones are denied, and it starts no MCP server
(``--strict-mcp-config`` with no ``--mcp-config``). A rule such as
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
(the hook refused, an edit outside refused, the structured answer kept);
``--tools`` and ``--disallowedTools`` take several values, so nothing
positional follows them (the prompt is on stdin).

Without ``ask``, ``-p`` has no interrupt: ``stop()`` kills it.

A turn with ``ask`` (a chat turn of a folder whose agent may ask) has flags
of its own (``ASK_FLAGS``): Bash among the tools and an ask rule for it
(``--settings``, which loads under ``--restricted``), so that every command
reaches Gamma as a permission prompt (``--permission-prompt-tool stdio``,
the control protocol the Agent SDK speaks: stream-json both ways). It
writes ``initialize`` and the prompt as a user message (``client_composed``:
no ``@path`` or ``/command`` read in it) and keeps stdin open until the
turn's ``result``, since closing it cancels a prompt. A ``can_use_tool``
for Bash is an ``ask`` event keyed by its ``request_id``, answered allow
(with the input as asked, never a permission rule written anywhere) or deny
with the owner's note. Gamma itself denies at once, never asking, every
other tool that reaches it: ``acceptEdits`` accepts the edits inside the
directory before it is asked, so an edit that reaches it is one
``--restricted`` leaves to a person or the handler (a settings, git or
tool-configuration file: ``.git``, ``.claude``, ``.vscode``, ``.husky``,
``.gitconfig``: ``events.protected``, the rule Codex's file changes are
held to too) or one outside the directory, and so is a Bash call with a
``blocked_path``. Its process runs with ``ASK_ENV``: each Bash call starts
in the folder, the ``cwd`` its ask names. A ``control_cancel_request`` withdraws an ask. A stop
denies what waits and interrupts the turn, ``STOP_GRACE_S`` to end.
"""

import json
import re
import threading

from . import events

LABEL = "Claude Code"
FLAGS = ["-p", "--output-format", "stream-json", "--verbose", "--restricted", "--permission-mode", "acceptEdits",
         "--permission-prompts", "none", "--strict-mcp-config", "--tools", "Read,Edit,Write,Glob,Grep",
         "--disallowedTools", "Bash,WebFetch,WebSearch"]
ASK_FLAGS = ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose", "--restricted",
             "--permission-mode", "acceptEdits", "--permission-prompt-tool", "stdio", "--strict-mcp-config",
             "--tools", "Read,Edit,Write,Glob,Grep,Bash", "--disallowedTools", "WebFetch,WebSearch",
             "--settings", json.dumps({"permissions": {"ask": ["Bash"]}}, separators=(",", ":"))]
# A turn with ask runs each Bash call in the folder (Claude Code's documented
# setting: the shell goes back to the project's directory after every call),
# so the cwd an ask names is the one the command runs in.
ASK_ENV = {"CLAUDE_BASH_MAINTAIN_PROJECT_WORKING_DIR": "1"}
SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STRUCTURED = "StructuredOutput"   # the tool --json-schema answers through: not a step
EDITS = ("Edit", "MultiEdit", "Write", "NotebookEdit")
CHANGE_REFUSED = events.CHANGE_REFUSED
TOOL_REFUSED = "Gamma does not offer that tool here."
STOPPED = "The owner stopped the turn."
INIT, INTERRUPT = "gamma-init", "gamma-stop"   # the ids of Gamma's own control requests


def command(argv: list[str], *, schema: dict | None = None, session: str = "", ask: bool = False) -> list[str]:
    """The whole command line of a turn."""
    out = list(argv) + (ASK_FLAGS if ask else FLAGS)
    if schema is not None:
        out += ["--json-schema", json.dumps(schema)]
    if session and SESSION.match(session):
        out += ["--resume", session]
    return out


class ClaudeTurn(events.Turn):
    label = LABEL

    def __init__(self, argv: list[str], *, cwd: str, prompt: str, schema: dict | None, session: str,
                 ask: bool = False):
        super().__init__(command(argv, schema=schema, session=session, ask=ask), cwd=cwd, ask=ask,
                         env=ASK_ENV if ask else None)
        self._tools: dict[str, dict] = {}   # tool_use id → its started event
        if self.child is not None:
            # Claude Code waits about 3 s for stdin: the prompt goes at once,
            # from a thread of its own, as a long one may fill the pipe.
            threading.Thread(target=self._write_prompt, args=(prompt,), name=f"agent-in-{self.pid}",
                             daemon=True).start()

    def _write_prompt(self, prompt: str) -> None:
        if not self.ask:
            self.child.send(prompt)
            self.child.close_stdin()
            return
        # stdin stays open: the answers to its prompts go there (``_finish`` closes it)
        for line in ({"type": "control_request", "request_id": INIT, "request": {"subtype": "initialize"}},
                     {"type": "user", "session_id": "",
                      "message": {"role": "user", "content": [{"type": "text", "text": prompt}]},
                      "parent_tool_use_id": None, "client_composed": True}):
            if not self.child.send(json.dumps(line, ensure_ascii=False) + "\n"):
                return

    def _started(self, block: dict) -> dict | None:
        name, tool_id = str(block.get("name") or ""), str(block.get("id") or "")
        args = block.get("input") if isinstance(block.get("input"), dict) else {}
        if name == STRUCTURED:
            return None  # its result is dropped too: it never enters _tools
        if name == "Read":
            path = events.rel_path(str(args.get("file_path") or ""), self.cwd)
            event = events.tool(tool_id, "read", f"Read {path}", "started", path=path)
        elif name in EDITS:
            path = events.rel_path(str(args.get("file_path") or args.get("notebook_path") or ""), self.cwd)
            event = events.tool(tool_id, "edit", f"Edit {path}", "started", path=path)
        elif name == "Bash":
            command_line = str(args.get("command") or "")
            event = events.tool(tool_id, "run", f"Run {command_line}", "started", command=command_line)
        elif name in ("Glob", "Grep"):
            event = events.tool(tool_id, "search", f"Search {args.get('pattern') or 'the files'}", "started")
        else:
            event = events.tool(tool_id, "other", f"Use {name}", "started")
        self._tools[tool_id] = event
        return event

    def _ended_tool(self, block: dict) -> dict | None:
        tool_id = str(block.get("tool_use_id") or "")
        begun = self._tools.pop(tool_id, None)
        if begun is None:
            return None
        status = "failed" if block.get("is_error") else "completed"
        output = None
        if begun["kind"] == "run":
            content = block.get("content")
            if isinstance(content, list):
                content = "\n".join(str(part.get("text") or "") for part in content if isinstance(part, dict))
            output = str(content or "")
        return events.tool(tool_id, begun["kind"], begun["title"], status, path=begun.get("path", ""),
                           command=begun.get("command", ""), output=output)

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
            elif kind == "assistant":
                content = (line.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if not isinstance(block, dict):
                        continue
                    if block.get("type") == "tool_use":
                        event = self._started(block)
                        if event:
                            yield event
                    elif block.get("type") == "text" and str(block.get("text") or "").strip():
                        yield {"type": "text", "text": str(block["text"])}
            elif kind == "user":
                content = (line.get("message") or {}).get("content")
                for block in content if isinstance(content, list) else []:
                    if isinstance(block, dict) and block.get("type") == "tool_result":
                        event = self._ended_tool(block)
                        if event:
                            yield event
            elif kind == "control_request" and self.ask:
                yield from self._control(line)
            elif kind == "control_cancel_request" and self.ask:
                request_id = str(line.get("request_id") or "")
                if self._unasked(request_id) is not None:
                    yield {"type": "ask_gone", "id": request_id}
            elif kind == "result":
                self._all_asks()  # the turn is over: nothing waits on an answer any more
                yield self._result(line)
                return

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
                             reason=str(args.get("description") or "").strip())
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

    def _reply(self, held, decision: str, note: str) -> None:
        request_id, tool_id, args = held
        if decision == "deny":
            answer = {"behavior": "deny", "message": "The owner declined" + (f": {note}" if note else ".")}
        else:  # once or in this chat: this call, as asked; Gamma keeps the chat's grant itself
            answer = {"behavior": "allow", "updatedInput": args}
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
        ok = line.get("subtype") == "success" and not line.get("is_error")
        text = line.get("result") if isinstance(line.get("result"), str) else ""
        answer = line.get("structured_output")
        if answer is None:
            answer = events.parse_answer(text)
        usage = line.get("usage") if isinstance(line.get("usage"), dict) else {}
        return events.done(ok=ok, answer=answer, text=text, usage=usage,
                           error=text or str(line.get("subtype") or "") or "Claude Code's turn failed.")


def start(*, argv: list[str], cwd: str, prompt: str, schema: dict | None = None, session: str = "",
          images: list[str] = (), ask: bool = False) -> ClaudeTurn:
    """Start ``claude -p`` in ``cwd`` for one turn of ``prompt`` (``session``
    one to resume; ``ask``, it may ask to run commands). ``images`` are not
    passed: the prompt names the pictures, which Claude Code opens with
    Read."""
    return ClaudeTurn(list(argv), cwd=cwd, prompt=prompt, schema=schema, session=session, ask=ask)

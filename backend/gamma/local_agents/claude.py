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

``-p`` has no interrupt: ``stop()`` kills it.
"""

import json
import re
import threading

from . import events

LABEL = "Claude Code"
FLAGS = ["-p", "--output-format", "stream-json", "--verbose", "--restricted", "--permission-mode", "acceptEdits",
         "--permission-prompts", "none", "--strict-mcp-config", "--tools", "Read,Edit,Write,Glob,Grep",
         "--disallowedTools", "Bash,WebFetch,WebSearch"]
SESSION = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
STRUCTURED = "StructuredOutput"   # the tool --json-schema answers through: not a step
EDITS = ("Edit", "MultiEdit", "Write", "NotebookEdit")


def command(argv: list[str], *, schema: dict | None = None, session: str = "") -> list[str]:
    """The whole command line of a turn."""
    out = list(argv) + FLAGS
    if schema is not None:
        out += ["--json-schema", json.dumps(schema)]
    if session and SESSION.match(session):
        out += ["--resume", session]
    return out


class ClaudeTurn(events.Turn):
    label = LABEL

    def __init__(self, argv: list[str], *, cwd: str, prompt: str, schema: dict | None, session: str):
        super().__init__(command(argv, schema=schema, session=session), cwd=cwd)
        self._tools: dict[str, dict] = {}   # tool_use id → its started event
        if self.child is not None:
            # Claude Code waits about 3 s for stdin: the prompt goes at once,
            # from a thread of its own, as a long one may fill the pipe.
            threading.Thread(target=self._write_prompt, args=(prompt,), name=f"agent-in-{self.pid}",
                             daemon=True).start()

    def _write_prompt(self, prompt: str) -> None:
        self.child.send(prompt)
        self.child.close_stdin()

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
            elif kind == "result":
                yield self._result(line)
                return

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
          images: list[str] = ()) -> ClaudeTurn:
    """Start ``claude -p`` in ``cwd`` for one turn of ``prompt`` (``session``
    one to resume). ``images`` are not passed: the prompt names the
    pictures, which Claude Code opens with Read."""
    return ClaudeTurn(list(argv), cwd=cwd, prompt=prompt, schema=schema, session=session)

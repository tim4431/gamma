"""A stand-in for Claude Code (gamma/local_agents/claude.py), standard
library only: ``--version``, ``auth status`` and ``-p --output-format
stream-json`` with the prompt on stdin, writing the lines Claude Code
2.1.318 writes (system/init, assistant blocks, user tool results, result).

Scripted by ``FAKE_AGENT`` as tests/fake_agents/fake_codex.py is: ``edits``
(or ``files``), ``answer`` / ``each`` with ``summary``, ``delay``, ``log``,
and ``fail`` one of ``exit``, ``garbage``, ``no_answer``, ``not_signed_in``
(the status says so, and a turn ends in a result that is an error),
``failed`` (a result that is an error), ``hang`` (with ``grandchild``),
``resume`` (a ``--resume`` turn exits 1 with nothing on stdout, as for a
session that is gone); ``distinct`` gives each new session an id of its
own. The log gets ``{"argv", "cwd", "env"}`` and ``{"stdin": text}``.
"""

import json
import os
import re
import subprocess
import sys
import time
import uuid

FAKE = json.loads(os.environ.get("FAKE_AGENT") or "{}")
FAIL = FAKE.get("fail", "")

sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stdin.reconfigure(encoding="utf-8")


def log(entry: dict) -> None:
    if FAKE.get("log"):
        with open(FAKE["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def out(line: dict) -> None:
    sys.stdout.write(json.dumps(line) + "\n")
    sys.stdout.flush()


def flag(args: list[str], name: str) -> str | None:
    return args[args.index(name) + 1] if name in args and args.index(name) + 1 < len(args) else None


def assistant(session: str, message_id: str, block: dict) -> None:
    out({"type": "assistant", "message": {"id": message_id, "type": "message", "role": "assistant",
                                          "content": [block], "stop_reason": None},
         "parent_tool_use_id": None, "session_id": session})


def tool_result(session: str, tool_id: str, text: str, error: bool = False) -> None:
    block = {"type": "tool_result", "tool_use_id": tool_id, "content": text}
    if error:
        block["is_error"] = True
    out({"type": "user", "message": {"role": "user", "content": [block]}, "parent_tool_use_id": None,
         "session_id": session})


def answer_of(prompt: str):
    if "answer" in FAKE:
        return FAKE["answer"]
    ids = list(dict.fromkeys(re.findall(r'"thread_id":\s*"([^"]+)"', prompt)))
    outcome = FAKE.get("each", "fixed")
    return {"answers": [{"thread_id": t, "outcome": outcome, "reply": f"Done for {t}."} for t in ids],
            "summary": FAKE.get("summary", "Fixed the comments.")}


def turn(args: list[str]) -> int:
    prompt = sys.stdin.read()
    log({"stdin": prompt})
    if FAIL == "resume" and flag(args, "--resume"):
        sys.stderr.write(f"No conversation found with session ID: {flag(args, '--resume')}\n")
        sys.stderr.flush()
        return 1
    session = flag(args, "--resume") or ("5e55-fake-session-" + uuid.uuid4().hex[:8] if FAKE.get("distinct")
                                         else "5e55-fake-session")
    cwd = os.getcwd()
    out({"type": "system", "subtype": "init", "session_id": session, "cwd": cwd, "tools": ["Read", "Edit"],
         "mcp_servers": [], "permissionMode": "acceptEdits"})
    if FAIL == "exit":
        sys.stderr.write("Error: fake claude could not reach the model\n")
        sys.stderr.flush()
        return 3
    if FAIL == "garbage":
        for _ in range(3):
            sys.stdout.write("<<not json>>\n")
        sys.stdout.flush()
        return 0
    if FAIL in ("not_signed_in", "failed"):
        text = "Not logged in · Please run /login" if FAIL == "not_signed_in" else "API Error: overloaded"
        out({"type": "result", "subtype": "success", "is_error": True, "result": text, "session_id": session,
             "usage": {}})
        return 1
    assistant(session, "msg_1", {"type": "thinking", "thinking": "", "signature": "x"})
    for n, edit in enumerate(FAKE.get("edits") or FAKE.get("files") or []):
        path = os.path.join(cwd, edit["path"])
        assistant(session, "msg_1", {"type": "tool_use", "id": f"toolu_read{n}", "name": "Read",
                                     "input": {"file_path": path}})
        tool_result(session, f"toolu_read{n}", "1\t...")
        assistant(session, "msg_1", {"type": "tool_use", "id": f"toolu_edit{n}", "name": "Edit",
                                     "input": {"file_path": path, "old_string": edit.get("old", ""),
                                               "new_string": edit.get("new", ""), "replace_all": False}})
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(edit.get("old", ""), edit.get("new", ""), 1))
        tool_result(session, f"toolu_edit{n}", "The file has been updated.")
    assistant(session, "msg_2", {"type": "tool_use", "id": "toolu_grep", "name": "Grep",
                                 "input": {"pattern": "TODO"}})
    tool_result(session, "toolu_grep", "No matches", error=True)
    if FAIL == "hang":
        if FAKE.get("grandchild"):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            log({"grandchild": child.pid})
        while True:
            time.sleep(1)
    time.sleep(float(FAKE.get("delay") or 0))
    schema = flag(args, "--json-schema")
    if FAIL == "no_answer":
        result, structured = "I looked at the comments.", None
    else:
        structured = answer_of(prompt)
        result = json.dumps(structured)
    if schema and structured is not None:
        assistant(session, "msg_3", {"type": "tool_use", "id": "toolu_out", "name": "StructuredOutput",
                                     "input": structured})
        tool_result(session, "toolu_out", "Structured output provided successfully")
    assistant(session, "msg_3", {"type": "text", "text": result})
    line = {"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": session,
            "num_turns": 3, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if schema and structured is not None:
        line["structured_output"] = structured
    out(line)
    return 0


def main() -> int:
    args = sys.argv[1:]
    log({"argv": args, "cwd": os.getcwd(),
         "env": sorted(name for name in os.environ
                       if name.upper().startswith(("GAMMA_", "CLAUDE", "CODEX_THREAD", "CODEX_SANDBOX")))})
    if args in (["--version"], ["-v"]):
        print("2.1.0 (Claude Code)")
        return 0
    if args[:2] == ["auth", "status"]:
        print(json.dumps({"loggedIn": FAIL != "not_signed_in", "authMethod": "claude.ai"}, indent=2))
        return 0 if FAIL != "not_signed_in" else 1
    if "-p" in args or "--print" in args:
        return turn(args)
    print(f"fake claude: unknown command {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

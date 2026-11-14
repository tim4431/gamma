"""A stand-in for Claude Code (gamma/local_agents/claude.py), standard
library only: ``--version``, ``auth status`` and ``-p --output-format
stream-json`` with the prompt on stdin, writing the lines Claude Code
2.1.318 writes (system/init, assistant blocks, user tool results, result).
With ``--input-format stream-json`` it speaks the control protocol: it
answers ``initialize`` and ``interrupt`` (any other subtype with an error),
takes the prompt from the user message, asks ``can_use_tool`` for each of
``ask`` (``{"command", "description"?, "background"?, "timeout"?,
"extra"?}`` a Bash call, ``background`` its ``run_in_background``,
``extra`` one more input field, ``{"edit"}`` an Edit
of that path, ``{"tool"}`` another tool; ``blocked``, a path, is its
``blocked_path``) and waits for each answer as long as stdin is open,
withdrawing it (``control_cancel_request``) when an interrupt comes
meanwhile; an interrupt ends the turn in an ``error_during_execution``
result. It reads stdin to its end after the result, and exits at once when
stdin ends before any user message (what listing the models does). Its
``initialize`` answers ``models`` (``MODELS``, or the scripted ``models``).
With ``--include-partial-messages`` its final text comes first as
``stream_event`` deltas (``deltas``, else its words, ``typing`` seconds
apart; a subagent's and a thinking delta beside them). The final message names
the last Bash decision: ``Ran it.`` or ``Did not run it.``.

Scripted by ``FAKE_AGENT`` as tests/fake_agents/fake_codex.py is: ``edits``
(or ``files``), ``answer`` / ``each`` with ``summary``, ``chat``, ``delay``, ``log``,
and ``fail`` one of ``exit``, ``garbage``, ``no_answer``, ``not_signed_in``
(the status says so, and a turn ends in a result that is an error),
``failed`` (a result that is an error), ``hang`` (with ``grandchild``),
``resume`` (a ``--resume`` turn exits 1 with nothing on stdout, as for a
session that is gone); ``distinct`` gives each new session an id of its
own. The log gets ``{"argv", "cwd", "env"}`` and ``{"stdin": text}`` (the
prompt), and with stream-json ``{"in": line}`` (each line read) and
``{"answer": control_response}`` (to a ``can_use_tool``).
"""

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
import uuid

FAKE = json.loads(os.environ.get("FAKE_AGENT") or "{}")
FAIL = FAKE.get("fail", "")

sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stdin.reconfigure(encoding="utf-8")


_logging = threading.Lock()   # the stdin reader and the turn both log: one whole line at a time


def log(entry: dict) -> None:
    if FAKE.get("log"):
        with _logging, open(FAKE["log"], "a", encoding="utf-8") as f:
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


def chat_message(prompt: str) -> str | None:
    """The person's message of a chat turn's prompt, None for a Send's."""
    found = re.search(r'"message":\s*("(?:[^"\\]|\\.)*")', prompt)
    return json.loads(found.group(1)) if found else None


def answer_of(prompt: str):
    if "answer" in FAKE:
        return FAKE["answer"]
    ids = list(dict.fromkeys(re.findall(r'"thread_id":\s*"([^"]+)"', prompt)))
    outcome = FAKE.get("each", "fixed")
    return {"answers": [{"thread_id": t, "outcome": outcome, "reply": f"Done for {t}."} for t in ids],
            "summary": FAKE.get("summary", "Fixed the comments.")}


def _model(value: str, name: str, efforts: list[str] | None) -> dict:
    return {"value": value, "resolvedModel": f"claude-{value}-fake", "displayName": name,
            "description": f"{name}, a stand-in", "supportsEffort": efforts is not None,
            "supportedEffortLevels": efforts or [], "supportsAutoMode": True}


MODELS = [_model("default", "Default (recommended)", ["low", "medium", "high"]),
          _model("sonnet", "Sonnet", ["low", "medium", "high", "max"]), _model("haiku", "Haiku", None)]


def stream_text(session: str, text: str) -> None:
    """A text block's partial messages (``--include-partial-messages``):
    its deltas, ``deltas`` when scripted, else its words, ``typing``
    seconds between two."""
    def event(body: dict) -> None:
        out({"type": "stream_event", "event": body, "parent_tool_use_id": None, "uuid": uuid.uuid4().hex,
             "session_id": session})

    event({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
    for piece in FAKE.get("deltas") or re.findall(r"\s*\S+", text) or [text]:
        event({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": piece}})
        time.sleep(float(FAKE.get("typing") or 0))
    # a subagent's text and a thinking delta are no part of the reply
    out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
                                           "delta": {"type": "text_delta", "text": "SUBAGENT"}},
         "parent_tool_use_id": "toolu_task", "session_id": session})
    event({"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "hmm"}})


inbox: queue.Queue = queue.Queue()
INTERRUPTED = object()


def _reader() -> None:
    for line in sys.stdin:
        try:
            message = json.loads(line)
        except ValueError:
            continue
        log({"in": message})
        inbox.put(message)
    inbox.put(None)


def wait_for(predicate, timeout: float | None):
    """The next message ``predicate`` takes, answering the control requests
    meanwhile; INTERRUPTED once an interrupt came, None at the end of stdin
    or the timeout."""
    end = None if timeout is None else time.monotonic() + timeout
    while True:
        left = None if end is None else end - time.monotonic()
        if left is not None and left <= 0:
            return None
        try:
            message = inbox.get(timeout=left)
        except queue.Empty:
            return None
        if message is None:
            inbox.put(None)
            return None
        if predicate(message):
            return message
        if message.get("type") != "control_request":
            continue
        subtype = (message.get("request") or {}).get("subtype")
        if subtype == "initialize":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": message["request_id"],
                                                          "response": {"commands": [],
                                                                       "models": FAKE.get("models", MODELS),
                                                                       "pending_permission_requests": []}}})
        elif subtype == "interrupt":
            out({"type": "control_response", "response": {"subtype": "success", "request_id": message["request_id"],
                                                          "response": {"still_queued": []}}})
            return INTERRUPTED
        else:
            out({"type": "control_response", "response": {"subtype": "error", "request_id": message["request_id"],
                                                          "error": f"Unsupported control request subtype: {subtype}"}})


def ask_all(session: str, cwd: str) -> tuple[list[str], bool]:
    """Each of ``ask`` asked and waited for → ``(the Bash decisions,
    whether an interrupt or the end of stdin cut it short)``."""
    decisions = []
    for n, asked in enumerate(FAKE.get("ask") or []):
        tool_id, request_id = f"toolu_ask{n}", f"req-{n}"
        if "command" in asked:
            name, args = "Bash", {"command": asked["command"], "description": asked.get("description", ""),
                                  **({"run_in_background": True} if asked.get("background") else {}),
                                  **{k: asked[k] for k in ("timeout", "extra") if k in asked}}
        elif "edit" in asked:
            name, args = "Edit", {"file_path": os.path.join(cwd, asked["edit"]), "old_string": "a", "new_string": "b"}
        else:
            name, args = asked["tool"], {"url": "https://example.com/"}
        assistant(session, f"msg_ask{n}", {"type": "tool_use", "id": tool_id, "name": name, "input": args})
        out({"type": "keep_alive"})
        request = {"subtype": "can_use_tool", "tool_name": name, "display_name": name, "input": args,
                   "tool_use_id": tool_id, "description": asked.get("description", "")}
        if asked.get("blocked"):
            request["blocked_path"] = os.path.join(cwd, asked["blocked"])
        out({"type": "control_request", "request_id": request_id, "request": request})
        answer = wait_for(lambda m, r=request_id: m.get("type") == "control_response"
                          and (m.get("response") or {}).get("request_id") == r, None)
        if answer is None or answer is INTERRUPTED:
            out({"type": "control_cancel_request", "request_id": request_id})
            tool_result(session, tool_id, "Interrupted", error=True)
            return decisions, True
        log({"answer": answer})
        decided = answer["response"]["response"]
        if name == "Bash":
            decisions.append(decided["behavior"])
        if decided["behavior"] == "allow":
            tool_result(session, tool_id, "ok")
        else:
            tool_result(session, tool_id, decided.get("message") or "denied", error=True)
    return decisions, False


def turn(args: list[str]) -> int:
    stream = flag(args, "--input-format") == "stream-json"
    if stream:
        threading.Thread(target=_reader, daemon=True).start()
        message = wait_for(lambda m: m.get("type") == "user", None)
        if message is None:  # stdin closed with no message (asking the models): nothing to do
            return 0
        content = (message.get("message") or {}).get("content") or []
        prompt = "".join(part.get("text", "") for part in content if part.get("type") == "text")
    else:
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
    decisions, cut = ask_all(session, cwd) if stream else ([], False)
    if stream and not cut:  # an interrupt that comes meanwhile ends the turn
        cut = wait_for(lambda m: False, max(0.2, float(FAKE.get("delay") or 0))) is INTERRUPTED
    if cut:
        out({"type": "result", "subtype": "error_during_execution", "is_error": True, "session_id": session,
             "usage": {}})
        wait_for(lambda m: False, None)  # until stdin ends
        return 0
    if FAIL == "hang":
        if FAKE.get("grandchild"):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            log({"grandchild": child.pid})
        while True:
            time.sleep(1)
    if not stream:
        time.sleep(float(FAKE.get("delay") or 0))
    schema = flag(args, "--json-schema")
    message = chat_message(prompt) if not schema else None
    if message is not None and decisions and not FAKE.get("chat"):
        result, structured = "Ran it." if decisions[-1] == "allow" else "Did not run it.", None
    elif message is not None:
        result, structured = FAKE.get("chat") or f"Done: {message}", None
    elif FAIL == "no_answer":
        result, structured = "I looked at the comments.", None
    else:
        structured = answer_of(prompt)
        result = json.dumps(structured)
    if schema and structured is not None:
        assistant(session, "msg_3", {"type": "tool_use", "id": "toolu_out", "name": "StructuredOutput",
                                     "input": structured})
        tool_result(session, "toolu_out", "Structured output provided successfully")
    if "--include-partial-messages" in args:
        stream_text(session, result)
    assistant(session, "msg_3", {"type": "text", "text": result})
    if "--include-partial-messages" in args:
        out({"type": "stream_event", "event": {"type": "content_block_stop", "index": 0}, "parent_tool_use_id": None,
             "session_id": session})
    line = {"type": "result", "subtype": "success", "is_error": False, "result": result, "session_id": session,
            "num_turns": 3, "usage": {"input_tokens": 10, "output_tokens": 5}}
    if schema and structured is not None:
        line["structured_output"] = structured
    out(line)
    if stream:
        wait_for(lambda m: False, None)  # until stdin ends: a result ends the turn, not the process
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

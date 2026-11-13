"""A stand-in for Codex CLI (gamma/local_agents/codex.py), standard library
only: ``--version``, ``login status`` and ``app-server`` (initialize,
initialized, windowsSandbox/readiness, config/read, thread/start,
thread/resume, turn/start, turn/interrupt; the item notifications, the
final agentMessage and turn/completed), as codex-cli 0.167.0 answers them.

Scripted by the environment variable ``FAKE_AGENT``, a JSON object (every
key optional):

- ``edits`` (or ``files``): ``[{"path", "old", "new"}]``, a path relative to
  the working directory; each is one fileChange item, the text replaced;
- ``commands``: command lines reported as commandExecution items (exit 0,
  output ``ok``), none of them run;
- ``answer``: the final message's JSON, whole; or ``each``: an outcome
  (``fixed``) given to every ``"thread_id": "<id>"`` in the prompt, with
  ``summary``;
- ``chat``: the final message of a chat turn (a turn with no output schema
  whose prompt carries a ``"message"``, gamma/local_agents/prompt.py
  ``build_chat``), plain text; by default ``Done: <the message>``;
- ``delay``: seconds before the turn ends (turn/interrupt ends it sooner);
- ``fail``: ``exit`` (dies after turn/start, a line on stderr), ``garbage``
  (lines that are not JSON, then exits), ``no_answer`` (a final message
  that is not JSON), ``not_signed_in``, ``sandbox_not_ready``, ``hang``
  (never ends its turn, ignores the interrupt; with ``grandchild`` it starts
  a child process that sleeps), ``approval`` (asks the methods in
  ``approvals``, the command approval by default, and records each
  answer), ``failed`` (an error notification and a failed turn), ``mcp``
  (starts its MCP server whatever the config says), ``mcp_early`` (the
  same, said before thread/start answers, with the new thread's id),
  ``read_only`` (the thread gets a read-only sandbox whatever was asked),
  ``resume`` (thread/resume is an error: the thread is gone);
- ``distinct``: each thread/start makes a thread of its own id (else every
  one is ``THREAD``);
- ``log``: a file receiving one JSON line per thing seen: ``{"argv",
  "cwd", "env"}`` (the GAMMA_* and agent-session variable names it got),
  ``{"in": message}``, ``{"answer": message}`` (to a request it made),
  ``{"grandchild": pid}``.
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
THREAD = "019a0000-fake-thread"
TURN = "019a0000-fake-turn"
SERVER = "fake_server"

sys.stdout.reconfigure(encoding="utf-8", newline="\n")
sys.stdin.reconfigure(encoding="utf-8")


def log(entry: dict) -> None:
    if FAKE.get("log"):
        with open(FAKE["log"], "a", encoding="utf-8") as f:
            f.write(json.dumps(entry) + "\n")


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message) + "\n")
    sys.stdout.flush()


def notify(method: str, params: dict) -> None:
    send({"method": method, "params": params})


inbox: queue.Queue = queue.Queue()


def _reader() -> None:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError:
            continue
        log({"in": message})
        inbox.put(message)
    inbox.put(None)


def wait_for(predicate, timeout: float | None):
    """The next message ``predicate`` takes, answering the others as the
    server would meanwhile; None at the end of stdin or the timeout."""
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
        handle(message)


state = {"turn": None, "interrupted": False, "config": None}


def handle(message: dict) -> None:
    method, request_id, params = message.get("method"), message.get("id"), message.get("params") or {}
    if method is None or request_id is None:
        return  # a notification (initialized) or an answer nobody waits for
    if method == "initialize":
        send({"id": request_id, "result": {"userAgent": "fake-codex/0.0", "codexHome": "", "platformFamily": "",
                                            "platformOs": ""}})
    elif method == "windowsSandbox/readiness":
        send({"id": request_id, "result": {"status": "notConfigured" if FAIL == "sandbox_not_ready" else "ready"}})
    elif method == "config/read":
        send({"id": request_id, "result": {"config": {"mcp_servers": {SERVER: {"command": "fake-mcp"}},
                                                       "web_search": "live"}, "origins": {}}})
    elif method == "thread/resume" and FAIL == "resume":
        send({"id": request_id, "error": {"code": -32600, "message": f"no rollout found for thread id "
                                                                     f"{params.get('threadId')}"}})
    elif method in ("thread/start", "thread/resume"):
        state["config"] = params.get("config") or {}
        thread = params.get("threadId") or (f"{THREAD}-{uuid.uuid4().hex[:8]}" if FAKE.get("distinct") else THREAD)
        sandbox = {"workspace-write": {"type": "workspaceWrite", "writableRoots": [], "networkAccess": False},
                   "read-only": {"type": "readOnly"}}.get(params.get("sandbox"), {"type": "readOnly"})
        if FAIL == "read_only":  # an administrator's requirements can hold a thread to read-only
            sandbox = {"type": "readOnly"}
        if FAIL == "mcp_early":  # servers start while thread/start is still pending
            notify("mcpServer/startupStatus/updated", {"name": SERVER, "status": "starting", "threadId": thread})
        send({"id": request_id, "result": {"thread": {"id": thread, "status": {"type": "idle"}, "turns": []},
                                            "sandbox": sandbox, "approvalPolicy": params.get("approvalPolicy"),
                                            "approvalsReviewer": "user", "cwd": params.get("cwd") or os.getcwd(),
                                            "model": "fake", "modelProvider": "fake"}})
        config = state["config"]
        server_off = (config.get("mcp_servers") or {}).get(SERVER, {}).get("enabled") is False
        if FAIL == "mcp" or not server_off:
            notify("mcpServer/startupStatus/updated", {"name": SERVER, "status": "starting", "threadId": None})
    elif method == "turn/start":
        state["turn"] = {"id": request_id, "params": params, "thread": params.get("threadId")}
    elif method == "turn/interrupt":
        send({"id": request_id, "result": {}})
        if FAIL != "hang":
            state["interrupted"] = True
    else:
        send({"id": request_id, "error": {"code": -32601, "message": f"fake codex: no method {method}"}})


def prompt_of(params: dict) -> str:
    return "\n".join(part.get("text", "") for part in params.get("input") or [] if part.get("type") == "text")


def chat_message(prompt: str) -> str | None:
    """The person's message of a chat turn's prompt, None for a Send's."""
    found = re.search(r'"message":\s*("(?:[^"\\]|\\.)*")', prompt)
    return json.loads(found.group(1)) if found else None


def answer_text(prompt: str, schema=None) -> str:
    message = chat_message(prompt) if schema is None else None
    if message is not None:
        return FAKE.get("chat") or f"Done: {message}"
    if FAIL == "no_answer":
        return "I looked at the comments but have nothing to say in JSON."
    if "answer" in FAKE:
        return json.dumps(FAKE["answer"])
    outcome = FAKE.get("each", "fixed")
    ids = list(dict.fromkeys(re.findall(r'"thread_id":\s*"([^"]+)"', prompt)))
    return json.dumps({"answers": [{"thread_id": t, "outcome": outcome, "reply": f"Done for {t}."} for t in ids],
                       "summary": FAKE.get("summary", "Fixed the comments.")})


def item(method: str, thread: str, body: dict) -> None:
    notify(method, {"threadId": thread, "turnId": TURN, "item": body, "startedAtMs": 0, "completedAtMs": 0})


def complete(thread: str, status: str, error: str = "") -> None:
    notify("thread/tokenUsage/updated", {"threadId": thread, "turnId": TURN, "tokenUsage": {
        "total": {"inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 5, "reasoningOutputTokens": 0,
                  "totalTokens": 15},
        "last": {"inputTokens": 10, "cachedInputTokens": 0, "outputTokens": 5, "reasoningOutputTokens": 0,
                 "totalTokens": 15}}})
    notify("turn/completed", {"threadId": thread, "turn": {
        "id": TURN, "status": status, "items": [], "error": {"message": error} if error else None}})


def run_turn() -> None:
    turn = state["turn"]
    thread, params = turn["thread"], turn["params"]
    notify("turn/started", {"threadId": thread, "turn": {"id": TURN, "status": "inProgress", "items": []}})
    send({"id": turn["id"], "result": {"turn": {"id": TURN, "status": "inProgress", "items": []}}})
    if FAIL == "exit":
        sys.stderr.write("2026-11-13T10:00:00Z INFO starting\nfake codex: the model refused to start\n")
        sys.stderr.flush()
        os._exit(2)
    if FAIL == "garbage":
        for _ in range(3):
            sys.stdout.write("this is not json {\n")
        sys.stdout.flush()
        os._exit(0)
    if FAIL == "approval":
        for n, method in enumerate(FAKE.get("approvals") or ["item/commandExecution/requestApproval"]):
            ask = f"srv-{n}"
            send({"id": ask, "method": method, "params": {"threadId": thread, "turnId": TURN, "itemId": "i0"}})
            answer = wait_for(lambda m, ask=ask: m.get("id") == ask and "method" not in m, 10)
            log({"answer": answer})
    if FAIL == "failed":
        notify("error", {"threadId": thread, "turnId": TURN, "willRetry": True,
                         "error": {"message": "reconnecting"}})
        notify("error", {"threadId": thread, "turnId": TURN, "willRetry": False,
                         "error": {"message": "The usage limit was reached."}})
        complete(thread, "failed")
        return
    cwd = os.getcwd()
    for n, edit in enumerate(FAKE.get("edits") or FAKE.get("files") or []):
        path = os.path.join(cwd, edit["path"])
        change = {"path": path, "kind": {"type": "update", "move_path": None}, "diff": ""}
        item("item/started", thread, {"type": "fileChange", "id": f"fc{n}", "changes": [change],
                                      "status": "inProgress"})
        with open(path, encoding="utf-8") as f:
            text = f.read()
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(text.replace(edit.get("old", ""), edit.get("new", ""), 1))
        item("item/completed", thread, {"type": "fileChange", "id": f"fc{n}", "changes": [change],
                                        "status": "completed"})
    for n, command in enumerate(FAKE.get("commands") or []):
        body = {"type": "commandExecution", "id": f"ce{n}", "command": command, "cwd": cwd, "commandActions": [],
                "status": "inProgress", "aggregatedOutput": None, "exitCode": None}
        item("item/started", thread, body)
        item("item/completed", thread, {**body, "status": "completed", "aggregatedOutput": "ok\n", "exitCode": 0})
    if FAIL == "hang":
        if FAKE.get("grandchild"):
            child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(600)"])
            log({"grandchild": child.pid})
        notify("item/started", {"threadId": thread, "turnId": TURN, "startedAtMs": 0, "item": {
            "type": "reasoning", "id": "r0", "summary": [], "content": []}})
        while True:
            time.sleep(1)
    end = time.monotonic() + float(FAKE.get("delay") or 0)
    while time.monotonic() < end and not state["interrupted"]:
        wait_for(lambda m: False, min(0.1, max(0.0, end - time.monotonic())))
    if state["interrupted"]:
        complete(thread, "interrupted")
        return
    text = answer_text(prompt_of(params), params.get("outputSchema"))
    item("item/completed", thread, {"type": "agentMessage", "id": "am0", "text": text})
    complete(thread, "completed")


def app_server() -> None:
    threading.Thread(target=_reader, daemon=True).start()
    while True:
        message = wait_for(lambda m: m.get("method") == "turn/start" and "id" in m, None)
        if message is None:
            return
        handle(message)
        run_turn()
        state["turn"] = None


def main() -> int:
    args = sys.argv[1:]
    log({"argv": args, "cwd": os.getcwd(),
         "env": sorted(name for name in os.environ
                       if name.upper().startswith(("GAMMA_", "CLAUDE", "CODEX_THREAD", "CODEX_SANDBOX")))})
    if args == ["--version"]:
        print("codex-cli 0.0.0-fake")
        return 0
    if args == ["login", "status"]:
        if FAIL == "not_signed_in":
            print("Not logged in")
            return 1
        print("Logged in using ChatGPT")
        return 0
    if args[:1] == ["app-server"]:
        app_server()
        return 0
    print(f"fake codex: unknown command {args}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())

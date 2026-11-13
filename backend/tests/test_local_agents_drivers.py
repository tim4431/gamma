"""The agent drivers (gamma/local_agents/events.py, codex.py, claude.py)
against the stand-ins in tests/fake_agents/, through the real procs.spawn:
what each sends, the events it makes of what comes back, the answer, and
every way a turn ends (its end, a stop, a crash, a stream that is not
JSON, silence) with exactly one done and no process left."""

import json
import threading
import time

import pytest

from fake_agents import ARGV, alive, gone, read_log, requests_in, script_agent
from gamma.local_agents import claude, codex, events, procs

PROMPT = 'The comments:\n```json\n{\n "thread_id": "t1"\n}\n```\n```json\n{\n "thread_id": "t2"\n}\n```\n'
SCHEMA = {"type": "object", "properties": {"answers": {"type": "array"}, "summary": {"type": "string"}},
          "required": ["answers", "summary"], "additionalProperties": False}


@pytest.fixture(autouse=True)
def _nothing_left(monkeypatch):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    monkeypatch.setattr(events, "STOP_GRACE_S", 1.0)
    monkeypatch.setenv("GAMMA_SHELL_SECRET", "the-server-s-own")
    yield
    left = list(procs._live)
    procs.stop_all(timeout=1)
    assert not left, f"children left running: {[c.proc.pid for c in left]}"


@pytest.fixture
def work(tmp_path):
    folder = tmp_path / "work"
    (folder / "sections").mkdir(parents=True)
    (folder / "main.tex").write_text("\\section{Intro}\nthe the cavity\n", encoding="utf-8")
    (folder / "fig.png").write_bytes(b"\x89PNG")
    return folder


@pytest.fixture
def log(tmp_path):
    return tmp_path / "agent.log"


def _turn(name, work, **kw):
    return events.start(name, argv=ARGV[name], cwd=str(work), prompt=kw.pop("prompt", PROMPT), **kw)


def _all(turn) -> list[dict]:
    out = list(turn.events())
    assert [e["type"] for e in out].count("done") == 1 and out[-1]["type"] == "done", out
    assert turn.child is None or turn.child.proc.poll() is not None
    return out


def _stop_after(turn, seconds):
    threading.Timer(seconds, turn.stop).start()


def _tools(out):
    return [(e["kind"], e["title"], e["status"]) for e in out if e["type"] == "tool"]


# --- the shared parts ----------------------------------------------------------------

def test_the_event_helpers(tmp_path):
    inside = tmp_path / "w"
    (inside / "a").mkdir(parents=True)
    assert events.rel_path(str(inside / "a" / "b.tex"), str(inside)) == "a/b.tex"
    assert events.rel_path("a/b.tex", str(inside)) == "a/b.tex"
    outside = tmp_path / "other.tex"
    assert events.rel_path(str(outside), str(inside)) == str(outside.resolve())
    assert events.parse_answer('```json\n{"a": 1}\n```') == {"a": 1}
    assert events.parse_answer(' {"a": [1]} ') == {"a": [1]}
    assert events.parse_answer("Done, nothing to say.") is None
    assert events.stderr_reason("2026-11-13T10:00:00Z ERROR codex_core: boom\n") == ""
    assert events.stderr_reason("[12:00:01] warn\nError: the model is overloaded\n\n") == \
        "Error: the model is overloaded"
    assert events.one_line("\n  first\nsecond") == "first"
    run = events.tool("x", "run", "Run latexmk -pdf main.tex\nmore", "completed", command="latexmk",
                      output="o" * 3000)
    assert run["title"] == "Run latexmk -pdf main.tex" and len(run["output"]) == events.MAX_OUTPUT
    assert "path" not in run and events.tool("y", "read", "Read a", "started", path="a")["path"] == "a"
    with pytest.raises(ValueError):
        events.start("gemini", argv=["x"], cwd=".", prompt="")


def test_a_cli_that_cannot_start_ends_in_a_done(work):
    for name, label in (("codex", "Codex"), ("claude", "Claude Code")):
        turn = events.start(name, argv=[str(work / "no-such-program")], cwd=str(work), prompt="hi")
        out = _all(turn)
        assert out == [events.done(ok=False, error=out[-1]["error"])]
        assert out[0]["error"].startswith(f"{label} could not start")
        assert turn.pid == 0


# --- Codex -----------------------------------------------------------------------------

def test_codex_answers_a_turn(monkeypatch, work, log):
    script_agent(monkeypatch, log, edits=[{"path": "main.tex", "old": "the the", "new": "the"}],
                 commands=["latexmk -pdf main.tex"], each="fixed", summary="Fixed both.")
    turn = _turn("codex", work, schema=SCHEMA, images=[str(work / "fig.png")])
    out = _all(turn)
    assert out[0] == {"type": "session", "id": "019a0000-fake-thread"} and turn.session == out[0]["id"]
    assert _tools(out) == [("edit", "Edit main.tex", "started"), ("edit", "Edit main.tex", "completed"),
                           ("run", "Run latexmk -pdf main.tex", "started"),
                           ("run", "Run latexmk -pdf main.tex", "completed")]
    edit = next(e for e in out if e["type"] == "tool" and e["kind"] == "edit")
    assert edit["path"] == "main.tex"
    ran = [e for e in out if e["type"] == "tool" and e["kind"] == "run"]
    assert ran[0]["command"] == "latexmk -pdf main.tex" and "output" not in ran[0] and ran[1]["output"] == "ok\n"
    done = out[-1]
    assert done["ok"] and not done["stopped"] and done["error"] == ""
    assert done["answer"] == {"answers": [{"thread_id": "t1", "outcome": "fixed", "reply": "Done for t1."},
                                          {"thread_id": "t2", "outcome": "fixed", "reply": "Done for t2."}],
                              "summary": "Fixed both."}
    assert [e for e in out if e["type"] == "text"] == [{"type": "text", "text": done["text"]}]
    assert done["usage"]["total"]["totalTokens"] == 15
    assert (work / "main.tex").read_text(encoding="utf-8") == "\\section{Intro}\nthe cavity\n"

    first = read_log(log)[0]
    assert first["argv"] == ["app-server"] and first["cwd"] == str(work)
    assert not [n for n in first["env"] if n.startswith("GAMMA_") or n == "CLAUDECODE" or n.startswith("CLAUDE_CODE_")]
    sent = requests_in(log)
    assert [m.get("method") for m in sent] == ["initialize", "initialized", "config/read", "thread/start",
                                               "turn/start"]
    assert [m["id"] for m in sent if "id" in m] == [1, 2, 3, 4]
    init = sent[0]["params"]
    assert init["clientInfo"]["name"] == "gamma" and init["clientInfo"]["title"] == "Gamma"
    assert init["capabilities"]["optOutNotificationMethods"] == codex.QUIET
    assert "params" not in sent[1] and "jsonrpc" not in sent[0]
    assert sent[2]["params"] == {"cwd": str(work)}
    assert sent[3]["params"] == {"cwd": str(work), "sandbox": "workspace-write", "approvalPolicy": "never",
                                 "config": {"mcp_servers": {"fake_server": {"enabled": False}},
                                            "features": {"apps": False}, "web_search": "disabled"}}
    assert sent[4]["params"] == {
        "threadId": "019a0000-fake-thread",
        "input": [{"type": "text", "text": PROMPT}, {"type": "localImage", "path": str(work / "fig.png")}],
        "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(work)], "networkAccess": False},
        "outputSchema": SCHEMA}


def test_codex_resumes_its_thread_and_checks_the_sandbox_on_windows(monkeypatch, work, log):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", True)
    script_agent(monkeypatch, log, answer={"answers": [], "summary": ""})
    turn = _turn("codex", work, session="019a-earlier-thread", images=[str(work / f"{i}.png") for i in range(9)])
    out = _all(turn)
    assert out[0] == {"type": "session", "id": "019a-earlier-thread"} and out[-1]["ok"]
    sent = requests_in(log)
    assert [m.get("method") for m in sent] == ["initialize", "initialized", "windowsSandbox/readiness",
                                               "config/read", "thread/resume", "turn/start"]
    assert "params" not in sent[2]
    resume = sent[4]["params"]
    assert resume["threadId"] == "019a-earlier-thread" and resume["excludeTurns"] is True
    assert resume["sandbox"] == "workspace-write" and resume["config"]["web_search"] == "disabled"
    start = sent[5]["params"]
    assert len(start["input"]) == 1 + codex.MAX_IMAGES and "outputSchema" not in start


def test_codex_sandbox_not_ready(monkeypatch, work, log):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", True)
    script_agent(monkeypatch, log, fail="sandbox_not_ready")
    out = _all(_turn("codex", work))
    assert out == [events.done(ok=False, error=codex.SANDBOX_PROBLEM)]
    assert "thread/start" not in [m.get("method") for m in requests_in(log)]
    assert codex.sandbox_ready(ARGV["codex"]) == (False, codex.SANDBOX_PROBLEM)
    script_agent(monkeypatch, log)
    assert codex.sandbox_ready(ARGV["codex"]) == (True, "")
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    assert codex.sandbox_ready(["no-such-program"]) == (True, "")  # elsewhere: nothing to set up


def test_codex_refuses_every_approval_in_the_schema_s_shape(monkeypatch, work, log):
    asked = ["item/commandExecution/requestApproval", "item/fileChange/requestApproval", "applyPatchApproval",
             "execCommandApproval", "item/permissions/requestApproval", "mcpServer/elicitation/request",
             "item/tool/requestUserInput"]
    script_agent(monkeypatch, log, fail="approval", approvals=asked)
    out = _all(_turn("codex", work))
    assert out[-1]["ok"]
    answers = [entry["answer"] for entry in read_log(log) if "answer" in entry]
    assert [a["id"] for a in answers] == [f"srv-{n}" for n in range(len(asked))]
    assert [a.get("result") for a in answers[:6]] == [
        {"decision": "decline"}, {"decision": "decline"}, {"decision": "denied"}, {"decision": "denied"},
        {"permissions": {}, "scope": "turn"}, {"action": "decline"}]
    assert answers[6]["error"] == {"code": -32601, "message": "not supported"} and "result" not in answers[6]


def test_codex_stop_interrupts_the_turn(monkeypatch, work, log):
    script_agent(monkeypatch, log, delay=30)
    turn = _turn("codex", work)
    _stop_after(turn, 1.5)
    started = time.monotonic()
    out = _all(turn)
    assert time.monotonic() - started < 15
    assert out[-1] == events.done(ok=False, stopped=True, usage=out[-1]["usage"]) and out[-1]["error"] == ""
    interrupt = [m for m in requests_in(log) if m.get("method") == "turn/interrupt"]
    assert interrupt and interrupt[0]["params"] == {"threadId": "019a0000-fake-thread",
                                                    "turnId": "019a0000-fake-turn"}
    turn.stop()  # idempotent, after the end too


def test_codex_that_ignores_the_interrupt_is_killed_with_its_child(monkeypatch, work, log):
    script_agent(monkeypatch, log, fail="hang", grandchild=True)
    turn = _turn("codex", work)
    _stop_after(turn, 2.0)
    out = _all(turn)
    assert out[-1]["stopped"] and not out[-1]["ok"] and out[-1]["error"] == ""
    grandchild = next(entry["grandchild"] for entry in read_log(log) if "grandchild" in entry)
    assert gone(grandchild) and not alive(turn.pid)


def test_codex_stopped_before_its_turn_began_is_killed_at_once(monkeypatch, work, log):
    script_agent(monkeypatch, log)
    turn = _turn("codex", work)
    turn.stop()
    started = time.monotonic()
    out = _all(turn)
    assert out == [events.done(ok=False, stopped=True)] and time.monotonic() - started < 5
    assert "turn/start" not in [m.get("method") for m in requests_in(log)]


@pytest.mark.parametrize("fail, error", [
    ("exit", "fake codex: the model refused to start"),        # stderr's last line, the log line before it skipped
    ("garbage", "Codex stopped unexpectedly (exit 0)."),
    ("failed", "The usage limit was reached."),                # the error notification that does not retry
    ("mcp", "Codex started the MCP server fake_server although Gamma asked for none."),
    ("mcp_early", "Codex started the MCP server fake_server although Gamma asked for none."),  # before the thread
    ("read_only", "Codex did not take the workspace sandbox."),
])
def test_codex_failures_name_the_reason(monkeypatch, work, log, fail, error):
    script_agent(monkeypatch, log, fail=fail)
    out = _all(_turn("codex", work))
    assert not out[-1]["ok"] and not out[-1]["stopped"] and out[-1]["error"] == error


def test_codex_without_an_answer_in_json(monkeypatch, work, log):
    script_agent(monkeypatch, log, fail="no_answer")
    done = _all(_turn("codex", work))[-1]
    assert done["ok"] and done["answer"] is None and done["text"].startswith("I looked")


def test_codex_silent_for_too_long_is_stopped(monkeypatch, work, log):
    monkeypatch.setattr(events, "TURN_IDLE_S", 1.0)
    script_agent(monkeypatch, log, fail="hang")
    done = _all(_turn("codex", work))[-1]
    assert done == events.done(ok=False, error="Codex said nothing for 10 minutes.")


# --- Claude Code ---------------------------------------------------------------------

def test_claude_answers_a_turn(monkeypatch, work, log):
    script_agent(monkeypatch, log, edits=[{"path": "main.tex", "old": "the the", "new": "the"}], summary="Both.")
    turn = _turn("claude", work, schema=SCHEMA, images=[str(work / "fig.png")])
    out = _all(turn)
    assert out[0] == {"type": "session", "id": "5e55-fake-session"} and turn.session == "5e55-fake-session"
    assert _tools(out) == [("read", "Read main.tex", "started"), ("read", "Read main.tex", "completed"),
                           ("edit", "Edit main.tex", "started"), ("edit", "Edit main.tex", "completed"),
                           ("search", "Search TODO", "started"), ("search", "Search TODO", "failed")]
    assert all(e["path"] == "main.tex" for e in out if e["type"] == "tool" and e["kind"] in ("read", "edit"))
    done = out[-1]
    assert done["ok"] and done["answer"]["summary"] == "Both."
    assert [a["thread_id"] for a in done["answer"]["answers"]] == ["t1", "t2"]
    assert done["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert (work / "main.tex").read_text(encoding="utf-8") == "\\section{Intro}\nthe cavity\n"
    entries = read_log(log)
    argv = entries[0]["argv"]
    assert argv == claude.FLAGS + ["--json-schema", json.dumps(SCHEMA)]
    assert argv[:14] == ["-p", "--output-format", "stream-json", "--verbose", "--restricted", "--permission-mode",
                         "acceptEdits", "--permission-prompts", "none", "--strict-mcp-config", "--tools",
                         "Read,Edit,Write,Glob,Grep", "--disallowedTools", "Bash,WebFetch,WebSearch"]
    assert not [n for n in entries[0]["env"] if n.startswith("GAMMA_") or n == "CLAUDECODE"]
    assert next(e["stdin"] for e in entries if "stdin" in e) == PROMPT


def test_claude_resumes_only_a_session_id_that_looks_like_one(monkeypatch, work, log):
    script_agent(monkeypatch, log)
    out = _all(_turn("claude", work, session="46a1f3dd-abe3-441d-8c99-a73f846098ea"))
    assert out[0]["id"] == "46a1f3dd-abe3-441d-8c99-a73f846098ea"
    assert read_log(log)[0]["argv"][-2:] == ["--resume", "46a1f3dd-abe3-441d-8c99-a73f846098ea"]
    assert claude.command(["claude"], session="x; rm -rf /") == ["claude"] + claude.FLAGS
    assert claude.command(["claude"], session="a" * 129) == ["claude"] + claude.FLAGS
    # Without a schema the answer is the result's text read as JSON.
    done = out[-1]
    assert done["ok"] and done["answer"] == json.loads(done["text"]) and "--json-schema" not in read_log(log)[0]["argv"]


def test_claude_stop_kills_it_and_its_child(monkeypatch, work, log):
    script_agent(monkeypatch, log, fail="hang", grandchild=True)
    turn = _turn("claude", work)
    _stop_after(turn, 2.0)
    started = time.monotonic()
    out = _all(turn)
    assert time.monotonic() - started < 15
    assert out[-1] == events.done(ok=False, stopped=True)
    grandchild = next(entry["grandchild"] for entry in read_log(log) if "grandchild" in entry)
    assert gone(grandchild) and not alive(turn.pid)


@pytest.mark.parametrize("fail, error", [
    ("exit", "Error: fake claude could not reach the model"),
    ("garbage", "Claude Code stopped unexpectedly (exit 0)."),
    ("failed", "API Error: overloaded"),
    ("not_signed_in", "Not logged in · Please run /login"),
])
def test_claude_failures_name_the_reason(monkeypatch, work, log, fail, error):
    script_agent(monkeypatch, log, fail=fail)
    out = _all(_turn("claude", work, schema=SCHEMA))
    assert not out[-1]["ok"] and not out[-1]["stopped"] and out[-1]["error"] == error


def test_claude_without_an_answer_in_json(monkeypatch, work, log):
    script_agent(monkeypatch, log, fail="no_answer")
    done = _all(_turn("claude", work, schema=SCHEMA))[-1]
    assert done["ok"] and done["answer"] is None and done["text"] == "I looked at the comments."


def test_claude_silent_for_too_long_is_stopped(monkeypatch, work, log):
    monkeypatch.setattr(events, "TURN_IDLE_S", 1.0)
    script_agent(monkeypatch, log, fail="hang")
    done = _all(_turn("claude", work))[-1]
    assert done == events.done(ok=False, error="Claude Code said nothing for 10 minutes.")


def test_every_step_is_one_of_the_kinds(monkeypatch, work, log):
    script_agent(monkeypatch, log, edits=[{"path": "main.tex", "old": "x", "new": "y"}], commands=["latexmk main.tex"])
    for name in ("codex", "claude"):
        out = _all(_turn(name, work))
        assert out[-1]["type"] == "done"
        assert {e["kind"] for e in out if e["type"] == "tool"} <= set(events.KINDS)


def test_a_turn_closed_unread_ends_its_process(monkeypatch, work, log):
    script_agent(monkeypatch, log, delay=30)
    turn = _turn("codex", work)
    assert turn.pid
    turn.close()  # nobody reads its events: close still ends the CLI
    end = time.monotonic() + 10
    while turn.child.proc.poll() is None and time.monotonic() < end:
        time.sleep(0.05)
    assert turn.child.proc.poll() is not None
    turn.close()  # again: nothing to do

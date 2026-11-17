"""The agent drivers (gamma/local_agents/events.py, codex.py, claude.py)
against the stand-ins in tests/fake_agents/, through the real procs.spawn:
what each sends, the events it makes of what comes back, its text, and
every way a turn ends (its end, a stop, a crash, a stream that is not
JSON, silence) with exactly one done and no process left; Gamma's tools
(step 2.7 of docs/research/agents-as-participants-plan.md): the config and
the flags each driver adds, the token in the agent's environment alone, a
server that did not start or connect failing the turn, a call a ``gamma``
step, a new Codex thread named, and the turn's token on a real ``/mcp``
(``serve_mcp``): read with while it lives, refused once deleted, refused
by every route of the HTTP API, never listed, reading its one
workspace."""

import json
import threading
import time
from types import SimpleNamespace

import pytest

from conftest import account_of, login, make_page, make_user
from fake_agents import ARGV, alive, gone, read_log, requests_in, script_agent, serve_mcp
from gamma import auth, integrations, mcp_server, workspaces
from gamma.local_agents import claude, codex, events, procs, tools
from test_mcp import call, rpc

PROMPT = 'The message:\n```json\n{\n "message": "Fix the intro."\n}\n```\n'


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
                 commands=["latexmk -pdf main.tex"])
    turn = _turn("codex", work, images=[str(work / "fig.png")])
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
    assert done["ok"] and not done["stopped"] and done["error"] == "" and done["text"] == "Done: Fix the intro."
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
        "sandboxPolicy": {"type": "workspaceWrite", "writableRoots": [str(work)], "networkAccess": False}}


def test_codex_resumes_its_thread_and_checks_the_sandbox_on_windows(monkeypatch, work, log):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", True)
    script_agent(monkeypatch, log)
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


def test_codex_names_a_new_thread_with_its_title(monkeypatch, work, log):
    # a new thread takes the conversation's title, which `codex resume` lists it by (C3)
    script_agent(monkeypatch, log, fail="resume")
    assert _all(_turn("codex", work, session="019a-gone", title="Tighten the method"))[-1]["ok"]
    sent = requests_in(log)
    assert [m.get("method") for m in sent][-4:] == ["thread/resume", "thread/start", "thread/name/set", "turn/start"]
    assert sent[-2]["params"] == {"threadId": "019a0000-fake-thread", "name": "Tighten the method"}
    # a thread resumed keeps its name, and one with no title gets none
    for session, title in (("019a-earlier", "Tighten the method"), ("", "")):
        log.unlink()
        script_agent(monkeypatch, log)
        assert _all(_turn("codex", work, session=session, title=title))[-1]["ok"]
        assert "thread/name/set" not in [m.get("method") for m in requests_in(log)]
    # a Codex that names no thread, or never answers, runs the turn all the same
    for fail in ("name", "name_silent"):
        script_agent(monkeypatch, log, fail=fail)
        assert _all(_turn("codex", work, title="Tighten the method"))[-1]["ok"]


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
    legacy = {"decision": {"denied": {"rejection": "Gamma does not let an agent do that here."}}}  # a ReviewDecision
    assert [a.get("result") for a in answers[:6]] == [
        {"decision": "decline"}, {"decision": "decline"}, legacy, legacy,
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


def test_codex_silent_for_too_long_is_stopped(monkeypatch, work, log):
    monkeypatch.setattr(events, "TURN_IDLE_S", 1.0)
    script_agent(monkeypatch, log, fail="hang")
    done = _all(_turn("codex", work))[-1]
    assert done == events.done(ok=False, error="Codex said nothing for 10 minutes.")


def _steered_on(turn, when, text="Only the abstract."):
    """The turn's events, ``text`` steered in (id ``s1``) at the first event
    ``when`` takes → (the events, what ``steer`` answered)."""
    out, answered = [], []
    for event in turn.events():
        out.append(event)
        if not answered and when(event):
            answered.append(turn.steer(f'```json\n{{"message": "{text}"}}\n```', "s1"))
    return out, answered


def test_codex_takes_a_steer_at_once_and_refuses_one_after_its_end(monkeypatch, work, log):
    script_agent(monkeypatch, log, commands=["ls"], delay=1.0)
    turn = _turn("codex", work)
    out, answered = _steered_on(turn, lambda e: e["type"] == "tool" and e["status"] == "completed")
    assert answered == [True]
    kinds = [e["type"] for e in out]
    assert kinds.index("steered") < kinds.index("text") and out[kinds.index("steered")] == events.steered("s1")
    assert out[-1]["ok"] and out[-1]["text"] == "Done: Fix the intro.; Only the abstract."
    sent = next(m["params"] for m in requests_in(log) if m.get("method") == "turn/steer")
    assert sent["clientUserMessageId"] == "s1" and sent["expectedTurnId"]
    assert turn.steer("too late", "s2") is False


# --- Claude Code ---------------------------------------------------------------------

def test_claude_answers_a_turn(monkeypatch, work, log):
    script_agent(monkeypatch, log, edits=[{"path": "main.tex", "old": "the the", "new": "the"}])
    turn = _turn("claude", work, images=[str(work / "fig.png")])
    out = _all(turn)
    assert out[0] == {"type": "session", "id": "5e55-fake-session"} and turn.session == "5e55-fake-session"
    assert _tools(out) == [("read", "Read main.tex", "started"), ("read", "Read main.tex", "completed"),
                           ("edit", "Edit main.tex", "started"), ("edit", "Edit main.tex", "completed"),
                           ("search", "Search TODO", "started"), ("search", "Search TODO", "failed")]
    assert all(e["path"] == "main.tex" for e in out if e["type"] == "tool" and e["kind"] in ("read", "edit"))
    done = out[-1]
    assert done["ok"] and done["text"] == "Done: Fix the intro."
    assert done["usage"] == {"input_tokens": 10, "output_tokens": 5}
    assert (work / "main.tex").read_text(encoding="utf-8") == "\\section{Intro}\nthe cavity\n"
    entries = read_log(log)
    argv = entries[0]["argv"]
    assert argv == claude.FLAGS
    assert argv[:17] == ["-p", "--input-format", "stream-json", "--output-format", "stream-json", "--verbose",
                         "--replay-user-messages", "--restricted", "--permission-mode", "acceptEdits",
                         "--permission-prompts", "none", "--strict-mcp-config", "--tools",
                         "Read,Edit,Write,Glob,Grep", "--disallowedTools", "Bash,WebFetch,WebSearch"]
    assert not [n for n in entries[0]["env"] if n.startswith("GAMMA_") or n == "CLAUDECODE"]
    assert next(e["stdin"] for e in entries if "stdin" in e) == PROMPT


def test_claude_reads_a_steer_after_its_tool_calls_and_answers_it_in_the_same_response(monkeypatch, work, log):
    script_agent(monkeypatch, log, steer_wait=3.0)
    turn = _turn("claude", work)
    out, answered = _steered_on(turn, lambda e: e["type"] == "tool" and e["kind"] == "search")
    assert answered == [True]
    kinds = [e["type"] for e in out]
    assert kinds.index("steered") < kinds.index("text")
    assert out[-1]["ok"] and out[-1]["text"] == "Done: Fix the intro.; Only the abstract."
    written = [e["in"] for e in read_log(log) if "in" in e and e["in"].get("type") == "user"]
    assert written[1]["uuid"] == "s1" and written[1]["client_composed"] is True
    assert turn.steer("too late", "s2") is False


def test_a_claude_steer_its_turn_did_not_read_keeps_it_going_into_the_next(monkeypatch, work, log):
    # Written after the turn's last call: Claude Code answers it in a second
    # turn of the same process, and that result ends Gamma's turn.
    script_agent(monkeypatch, log, late_steers=True, delay=0.5)
    turn = _turn("claude", work)
    out, answered = _steered_on(turn, lambda e: e["type"] == "tool" and e["kind"] == "search")
    assert answered == [True]
    kinds = [e["type"] for e in out]
    assert kinds.count("text") == 2 and kinds.index("text") < kinds.index("steered")
    assert out[-1]["ok"] and out[-1]["text"] == "Done: Only the abstract."
    assert out[-1]["usage"] == {"input_tokens": 20, "output_tokens": 10}


def test_claude_resumes_only_a_session_id_that_looks_like_one(monkeypatch, work, log):
    script_agent(monkeypatch, log)
    out = _all(_turn("claude", work, session="46a1f3dd-abe3-441d-8c99-a73f846098ea"))
    assert out[0]["id"] == "46a1f3dd-abe3-441d-8c99-a73f846098ea"
    assert read_log(log)[0]["argv"][-2:] == ["--resume", "46a1f3dd-abe3-441d-8c99-a73f846098ea"]
    assert claude.command(["claude"], session="x; rm -rf /") == ["claude"] + claude.FLAGS
    assert claude.command(["claude"], session="a" * 129) == ["claude"] + claude.FLAGS
    assert out[-1]["ok"] and out[-1]["text"] == "Done: Fix the intro."


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
    out = _all(_turn("claude", work))
    assert not out[-1]["ok"] and not out[-1]["stopped"] and out[-1]["error"] == error


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


# --- Gamma's tools ---------------------------------------------------------------------

UNREACHED = events.Gamma("http://127.0.0.1:9/mcp", "gamma_the-turns-own-token")   # nothing answers there


def _gamma_steps(out):
    return [(e["kind"], e["tool"], e["status"]) for e in out if e["type"] == "tool" and e["kind"] == "gamma"]


def test_codex_gets_gammas_server_beside_the_rule_that_starts_no_other(monkeypatch, work, log):
    script_agent(monkeypatch, log, gamma=[{"tool": "list_pages", "arguments": {"title_contains": "x"}}])
    out = _all(_turn("codex", work, gamma=UNREACHED))
    assert out[-1]["ok"]
    # the call is a gamma step named by its tool (it failed: nothing answers there)
    assert _gamma_steps(out) == [("gamma", "list_pages", "started"), ("gamma", "list_pages", "failed")]
    assert next(e for e in out if e["type"] == "tool")["title"] == "Read Gamma: list_pages"
    # a writing tool's call is worded as a write, done and while it runs
    assert [events.gamma_words(name) for name in ("comment", "resolve", "highlight", "")] == [
        "Wrote in Gamma: comment", "Wrote in Gamma: resolve", "Wrote in Gamma: highlight", "Read Gamma"]
    assert events.gamma_words("comment", now=True) == "Writing in Gamma: comment"
    started = next(m for m in requests_in(log) if m.get("method") == "thread/start")["params"]
    assert started["config"] == {
        "mcp_servers": {"fake_server": {"enabled": False}, "gamma_turn": {
            "enabled": True, "url": UNREACHED.url, "bearer_token_env_var": events.TOKEN_ENV}},
        "features": {"apps": False}, "web_search": "disabled",
        "shell_environment_policy": {"exclude": [events.TOKEN_ENV]}}
    # the token is in the app-server's environment, never in what it was sent or on its command line
    first = read_log(log)[0]
    assert [n for n in first["env"] if n.startswith("GAMMA_")] == [events.TOKEN_ENV]
    assert UNREACHED.token not in log.read_text(encoding="utf-8")


@pytest.mark.parametrize("user_exclude, exclude", [
    (["AWS_*", "*_SECRET"], ["AWS_*", "*_SECRET", events.TOKEN_ENV]),
    ([events.TOKEN_ENV, "AWS_*"], [events.TOKEN_ENV, "AWS_*"]),
])
def test_codex_keeps_the_users_own_server_named_gamma_off_and_their_excludes(monkeypatch, work, log, user_exclude,
                                                                             exclude):
    # an override replaces a list: the user's excludes stay, the token's variable added once; the user's
    # own connection to Gamma (`codex mcp add gamma`) stays off beside the turn's server
    script_agent(monkeypatch, log, config={
        "shell_environment_policy": {"inherit": "core", "exclude": user_exclude},
        "mcp_servers": {"gamma": {"url": "https://gamma.example/mcp", "enabled_tools": ["search_library"]}}})
    assert _all(_turn("codex", work, gamma=UNREACHED))[-1]["ok"]
    config = next(m for m in requests_in(log) if m.get("method") == "thread/start")["params"]["config"]
    assert config["shell_environment_policy"] == {"exclude": exclude}
    assert config["mcp_servers"]["gamma"] == {"enabled": False}
    assert config["mcp_servers"]["gamma_turn"]["url"] == UNREACHED.url


@pytest.mark.parametrize("fail, error", [
    ("gamma_down", "Gamma's tools did not start for Codex: MCP client for `gamma_turn` failed to start: "
                   "Environment variable GAMMA_AGENT_MCP_TOKEN for MCP server 'gamma_turn' is not set"),
    ("mcp", "Codex started the MCP server fake_server although Gamma asked for no other."),
])
def test_codex_fails_a_turn_whose_servers_are_not_gammas_alone(monkeypatch, work, log, fail, error):
    script_agent(monkeypatch, log, fail=fail)
    out = _all(_turn("codex", work, gamma=UNREACHED))
    assert not out[-1]["ok"] and out[-1]["error"] == error
    assert out[-1]["gamma_down"] is (fail == "gamma_down")


def test_claude_gets_gammas_server_and_its_tools_under_its_flags(monkeypatch, work, log):
    script_agent(monkeypatch, log, gamma=[{"tool": "read_page", "arguments": {"page_id": "p1"}}])
    out = _all(_turn("claude", work, gamma=UNREACHED))
    assert out[-1]["ok"]
    assert _gamma_steps(out) == [("gamma", "read_page", "started"), ("gamma", "read_page", "failed")]
    argv = read_log(log)[0]["argv"]
    assert argv == claude.FLAGS + ["--mcp-config", claude.mcp_config(UNREACHED.url),
                                   "--allowedTools", "mcp__gamma_turn"]
    assert json.loads(claude.mcp_config(UNREACHED.url)) == {"mcpServers": {"gamma_turn": {
        "type": "http", "url": UNREACHED.url, "headers": {"Authorization": "Bearer ${GAMMA_AGENT_MCP_TOKEN}"}}}}
    assert [n for n in read_log(log)[0]["env"] if n.startswith("GAMMA_")] == [events.TOKEN_ENV]
    assert UNREACHED.token not in log.read_text(encoding="utf-8")
    # an ask turn: the same, under its own flags
    assert claude.command(["claude"], ask=True, stream=True, gamma_url=UNREACHED.url) == [
        "claude", *claude.ASK_FLAGS, "--mcp-config", claude.mcp_config(UNREACHED.url),
        "--allowedTools", "mcp__gamma_turn", claude.STREAM_FLAG]


def test_claude_fails_a_turn_whose_gamma_did_not_connect(monkeypatch, work, log):
    script_agent(monkeypatch, log, fail="gamma_down", gamma=[{"tool": "list_pages"}])
    out = _all(_turn("claude", work, gamma=UNREACHED))
    assert not out[-1]["ok"] and out[-1]["error"] == "Gamma's tools did not connect for Claude Code (it says failed)."
    assert out[-1]["gamma_down"] and not _gamma_steps(out)


def test_the_address_comes_from_a_request_this_computer_sent_straight(monkeypatch):
    monkeypatch.setattr(tools, "_url", "")

    def scope(server, client="127.0.0.1", scheme="http", headers=()):
        return {"type": "http", "server": server, "client": (client, 50123), "scheme": scheme, "headers": list(headers)}

    for ignored in (scope(("testserver", 80), client="testclient"),            # no socket of this computer's
                    scope(("192.168.1.5", 9001), client="192.168.1.9"),        # another computer's
                    scope(("192.168.1.5", 9001)),                              # an address MCP does not take
                    scope(("127.0.0.1", 9001), client="10.0.0.4"),             # from elsewhere, through a tunnel
                    scope(("127.0.0.1", 9001), headers=[(b"x-forwarded-proto", b"https")]),   # a proxy's
                    {"type": "websocket", "server": ("127.0.0.1", 9001), "client": ("127.0.0.1", 1)}):
        tools.heard(ignored)
        assert tools.mcp_url() == ""
    assert tools.mint("someone", "ws", "Codex", 60) is None   # no address: no token either
    tools.heard(scope(("::1", 9124), client="::1"))
    assert tools.mcp_url() == "http://[::1]:9124/mcp"
    tools.heard(scope(("127.0.0.1", 9001), scheme="https"))
    assert tools.mcp_url() == "http://[::1]:9124/mcp", "the first one names it"
    monkeypatch.setattr(tools, "_url", "")
    tools.heard(scope(("127.0.0.1", 9443), scheme="https"))
    assert tools.mcp_url() == "https://127.0.0.1:9443/mcp"


@pytest.fixture(scope="module")
def reader(client):
    """An account with two workspaces, a page in its own, and this server's
    /mcp on a socket of its own."""
    ws = make_user("turn_reader", "pw-turn")
    other = workspaces.create("Elsewhere", account_of("turn_reader"), kind="shared")["id"]
    c = login("turn_reader", "pw-turn")
    page = make_page(c, "Heliotrope notes")
    with serve_mcp() as url:
        yield SimpleNamespace(ws=ws, other=other, client=c, page=page, url=url, user=account_of("turn_reader"))


@pytest.mark.parametrize("name", ["codex", "claude"])
def test_a_turn_reads_gamma_with_its_token_and_is_refused_once_it_is_gone(monkeypatch, work, log, reader, name):
    token_id, token = integrations.mint_turn(reader.user, reader.ws, "Codex", 60)
    gamma = events.Gamma(reader.url, token)
    script_agent(monkeypatch, log, gamma=[{"tool": "list_pages", "arguments": {"title_contains": "Heliotrope"}}])
    out = _all(_turn(name, work, gamma=gamma))
    assert out[-1]["ok"] and _gamma_steps(out)[-1] == ("gamma", "list_pages", "completed")
    called = [e for e in read_log(log) if "gamma" in e][-1]
    assert called["http"] == 200 and not called["error"] and reader.page["id"] in called["text"]
    integrations.drop(token_id)
    out = _all(_turn(name, work, gamma=gamma))
    called = [e for e in read_log(log) if "gamma" in e][-1]
    assert called["http"] == 401 and _gamma_steps(out)[-1] == ("gamma", "list_pages", "failed")


def test_a_turns_token_opens_mcp_in_its_workspace_alone_and_nothing_else(reader, anon, client):
    token_id, token = integrations.mint_turn(reader.user, reader.ws, "Claude Code", 60)
    try:
        bearer = {"Authorization": f"Bearer {token}", "X-Gamma-Workspace": reader.ws}
        for path in ("/api/pages", f"/api/pages/{reader.page['id']}/family", "/api/session",
                     "/api/integrations/tokens", "/api/computers/poll"):
            r = anon.get(path, headers=bearer)
            assert (r.status_code, r.json()["detail"]) == (403, auth.TURN_ONLY), path
        # never a connection the person sees or manages
        listed = reader.client.get("/api/integrations/tokens").json()["tokens"]
        assert token_id not in [t["id"] for t in listed]
        assert reader.client.delete(f"/api/integrations/tokens/{token_id}").json()["revoked"] is False
        assert reader.client.patch(f"/api/integrations/tokens/{token_id}",
                                   json={"all_workspaces": True}).status_code == 404
        # MCP: the read tools and the writing tools, in its workspace alone; no note edits
        connection = integrations.resolve_connection(token)
        assert (connection.scope, connection.workspaces, connection.editable, connection.commentable,
                connection.name) == ("turn", [reader.ws], (), (reader.ws,), "Claude Code")
        offered = {t["name"] for t in rpc(client, token, "tools/list").json()["result"]["tools"]}
        assert offered == mcp_server.READ_TOOLS | mcp_server.WRITING_TOOLS | {"list_workspaces", "read_gamma_link",
                                                                              "export_page"}
        read = call(client, token, "list_workspaces", {})["structuredContent"]["workspaces"]
        assert [w["id"] for w in read] == [reader.ws]
        unknown = call(client, token, "resolve", {"thread_id": "nosuchthread"})  # past the level, to the thread
        assert unknown["isError"] and "No comment thread has that id" in unknown["content"][0]["text"]
        assert call(client, token, "edit_block", {"block_id": reader.page["id"], "content": "x"})["isError"]
    finally:
        integrations.drop(token_id)
    assert integrations.resolve_connection(token) is None
    # a crash's leftovers go at the next start (runner.recover)
    integrations.mint_turn(reader.user, reader.ws, "Codex", 60)
    assert integrations.drop_turns() == 1 and integrations.drop_turns() == 0

"""The agents on this computer (gamma/local_agents/catalog.py) and the
switch that turns the whole feature on (gamma/local_agents ``enabled``),
against the stand-ins in tests/fake_agents/: never the real CLIs, which
the fixture hides (no PATH, no fallbacks)."""

import json
import sys
import threading
import time
from pathlib import Path

import pytest

from fake_agents import ARGV, script_agent, use_fakes
from gamma import config, local_agents, version
from gamma.local_agents import catalog, codex, procs

WINDOWS = sys.platform == "win32"


@pytest.fixture(autouse=True)
def _fakes(monkeypatch, tmp_path):
    use_fakes(monkeypatch, tmp_path / "agent.log")
    monkeypatch.setattr(codex, "CHECK_SANDBOX", False)
    catalog.forget()
    yield
    for _ in range(100):  # a background probe of cached() still running
        if not catalog._refreshing:
            break
        time.sleep(0.05)
    catalog.forget()
    assert not procs._live


def _counting(monkeypatch, delay: float = 0) -> list:
    calls = []
    real = procs.run_quick

    def run_quick(argv, **kw):
        calls.append((threading.current_thread().name, argv[-1]))
        time.sleep(delay)
        return real(argv, **kw)
    monkeypatch.setattr(procs, "run_quick", run_quick)
    return calls


# --- on or off ----------------------------------------------------------------------

@pytest.mark.parametrize("links, secret, setting, updates, on", [
    ("", "s", "", "", False),       # not the desktop app's own server
    ("1", "s", "", "", True),       # the desktop app started it
    ("1", "", "", "", False),       # a server run by hand
    ("1", "", "1", "", True),       # ... that asks for it (tests, the e2e server)
    ("1", "s", "off", "", False),   # turned off always
    ("1", "s", "", "mas", False),   # the Mac App Store build
    ("1", "", "yes", "mas", False),
])
def test_enabled(monkeypatch, links, secret, setting, updates, on):
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", links)
    monkeypatch.setenv("GAMMA_SHELL_SECRET", secret)
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", setting)
    monkeypatch.setattr(version, "UPDATES", updates)
    assert local_agents.enabled() is on


@pytest.mark.parametrize("value, setting", [("", None), ("1", True), ("On", True), ("0", False),
                                            ("off", False), ("no", False), ("maybe", None)])
def test_local_agents_setting(monkeypatch, value, setting):
    monkeypatch.setenv("GAMMA_LOCAL_AGENTS", value)
    assert config.local_agents_setting() is setting


# --- finding them -------------------------------------------------------------------

def test_the_known_agents():
    assert list(catalog.AGENTS) == ["codex", "claude"]
    assert [catalog.label(n) for n in catalog.AGENTS] == ["Codex", "Claude Code"]
    assert catalog.label("other") == "other"
    assert catalog.AGENTS["codex"].status == ("login", "status")
    assert catalog.AGENTS["claude"].status == ("auth", "status")


def test_command_takes_the_environment_then_the_path_then_the_fallbacks(monkeypatch, tmp_path):
    assert catalog.command("codex") == ARGV["codex"]
    assert catalog.command("nobody") is None
    exe = tmp_path / ("codex.exe" if WINDOWS else "codex")
    exe.write_text("")
    exe.chmod(0o755)
    monkeypatch.setenv("GAMMA_AGENT_CODEX", str(exe))
    assert catalog.command("codex") == [str(exe)]
    monkeypatch.setenv("GAMMA_AGENT_CODEX", str(tmp_path / "missing.exe"))
    assert catalog.command("codex") is None
    monkeypatch.setenv("GAMMA_AGENT_CODEX", '["not", 1]')
    assert catalog.command("codex") is None
    monkeypatch.delenv("GAMMA_AGENT_CODEX")
    assert catalog.command("codex") is None  # no PATH, no fallbacks here
    monkeypatch.setattr(catalog, "FALLBACKS", {"codex": [str(tmp_path / "nope"), str(exe)], "claude": []})
    assert catalog.command("codex") == [str(exe)]
    on_path = tmp_path / "bin" / exe.name
    on_path.parent.mkdir()
    on_path.write_text("")
    on_path.chmod(0o755)
    monkeypatch.setattr(catalog.shutil, "which", lambda name, *a, **k: str(on_path) if name == "codex" else None)
    assert catalog.command("codex") == [str(on_path)]


@pytest.mark.skipif(not WINDOWS, reason="npm shims are .cmd files on Windows")
def test_an_npm_shim_starts_as_node_and_its_script(monkeypatch, tmp_path):
    shim = tmp_path / "npm" / "codex.cmd"
    shim.parent.mkdir()
    shim.write_text("@echo off\r\n")
    monkeypatch.setenv("GAMMA_AGENT_CODEX", str(shim))
    assert catalog.command("codex") is None
    answer = catalog.probe("codex", fresh=True)
    assert answer["found"] is False and answer["ready"] is False
    assert answer["problem"] == ("Codex is installed through npm in a way Gamma cannot start: install the "
                                 "standalone Codex.")
    script = shim.parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
    script.parent.mkdir(parents=True)
    script.write_text("")
    (shim.parent / "node.exe").write_text("")
    assert catalog.command("codex") == [str(shim.parent / "node.exe"), str(script)]
    (shim.parent / "node.exe").unlink()
    monkeypatch.setattr(catalog.shutil, "which", lambda name, *a, **k: "C:/node/node.exe" if name == "node" else None)
    assert catalog.command("codex") == ["C:/node/node.exe", str(script)]


def test_the_fallbacks_are_the_installers_places():
    fallbacks = {name: catalog._fallbacks(agent.exe) for name, agent in catalog.AGENTS.items()}
    if WINDOWS:
        assert fallbacks["codex"][0].endswith(str(Path("Programs/OpenAI/Codex/bin/codex.exe")))
        assert fallbacks["claude"][0].endswith(str(Path(".local/bin/claude.exe")))
    else:
        assert "/opt/homebrew/bin/codex" in fallbacks["codex"] and "/usr/local/bin/claude" in fallbacks["claude"]


# --- asking them --------------------------------------------------------------------

def test_probe_both_ready(monkeypatch):
    answers = catalog.probes()
    assert [(a["name"], a["label"], a["found"], a["ready"], a["problem"]) for a in answers] == [
        ("codex", "Codex", True, True, ""), ("claude", "Claude Code", True, True, "")]
    assert answers[0]["version"] == "codex-cli 0.0.0-fake" and answers[1]["version"] == "2.1.0 (Claude Code)"
    assert all(a["at"] > 0 for a in answers)


def test_probe_not_signed_in(monkeypatch, tmp_path):
    script_agent(monkeypatch, tmp_path / "agent.log", fail="not_signed_in")
    codex_answer, claude_answer = catalog.probes(fresh=True)
    assert codex_answer["found"] and not codex_answer["ready"]
    assert codex_answer["problem"] == "Codex is not signed in on this computer: run `codex login` in a terminal."
    assert claude_answer["problem"] == ("Claude Code is not signed in on this computer: run `claude` in a "
                                        "terminal and sign in.")


def test_probe_reads_claude_s_status_as_json_or_text(monkeypatch):
    claude = catalog.AGENTS["claude"]
    assert catalog._signed_in(claude, 1, '{"loggedIn": true}')
    assert not catalog._signed_in(claude, 0, 'warning\n{\n "loggedIn": false,\n "authMethod": "none"\n}')
    assert catalog._signed_in(claude, 0, "Login method: Claude Max account\nEmail: a@b.c")
    assert not catalog._signed_in(claude, 1, "Login method: none")
    codex_agent = catalog.AGENTS["codex"]
    assert catalog._signed_in(codex_agent, 0, "Logged in using ChatGPT")
    assert not catalog._signed_in(codex_agent, 0, "Not logged in")
    assert not catalog._signed_in(codex_agent, 1, "Logged in using an API key")


def test_probe_codex_s_windows_sandbox(monkeypatch, tmp_path):
    monkeypatch.setattr(codex, "CHECK_SANDBOX", True)
    assert catalog.probe("codex", fresh=True)["ready"] is True
    script_agent(monkeypatch, tmp_path / "agent.log", fail="sandbox_not_ready")
    answer = catalog.probe("codex", fresh=True)
    assert answer["found"] and answer["ready"] is False and answer["problem"] == codex.SANDBOX_PROBLEM


def test_probe_an_agent_that_does_not_answer(monkeypatch):
    monkeypatch.setenv("GAMMA_AGENT_CODEX", json.dumps([sys.executable, "-c", "import sys; sys.exit(3)"]))
    answer = catalog.probe("codex", fresh=True)
    assert answer["found"] and not answer["ready"]
    assert answer["problem"] == "Codex did not answer `codex --version`."
    monkeypatch.delenv("GAMMA_AGENT_CODEX")
    answer = catalog.probe("codex", fresh=True)
    assert (answer["found"], answer["ready"], answer["problem"]) == (False, False, "Codex is not on this computer.")


def test_probe_never_raises(monkeypatch):
    monkeypatch.setattr(catalog, "_probe", lambda agent: 1 / 0)
    answer = catalog.probe("claude", fresh=True)
    assert answer["found"] is False and answer["problem"] == "Claude Code is not on this computer."
    assert catalog.probe("gemini")["found"] is False


def test_probe_keeps_its_answer_and_shares_one_probe(monkeypatch):
    calls = _counting(monkeypatch)
    catalog.probe("claude")
    catalog.probe("claude")
    assert len(calls) == 2  # --version and the status, once
    catalog.probe("claude", fresh=True)
    assert len(calls) == 4
    monkeypatch.setattr(catalog, "PROBE_TTL_S", 0)
    catalog.probe("claude")
    assert len(calls) == 6  # stale
    calls = _counting(monkeypatch, delay=0.5)
    together = [threading.Thread(target=catalog.probe, args=("claude",), kwargs={"fresh": True})
                for _ in range(4)]
    for t in together:
        t.start()
    for t in together:
        t.join(60)
    assert len(calls) == 2  # four callers at once, one probe


def test_cached_never_runs_a_process(monkeypatch):
    calls = _counting(monkeypatch)
    me = threading.current_thread().name
    first = catalog.cached("codex")
    assert (first["found"], first["ready"], first["version"], first["at"]) == (None, None, "", 0)
    for _ in range(200):
        answer = catalog.cached("codex")
        if answer["found"] is not None:
            break
        time.sleep(0.05)
    assert answer["ready"] is True
    assert calls and all(thread != me for thread, _ in calls)
    calls.clear()
    assert catalog.cached("codex")["ready"] is True and not calls  # fresh enough: nothing runs
    monkeypatch.setattr(catalog, "PROBE_TTL_S", 0)
    stale = catalog.cached("codex")
    assert stale["ready"] is True  # the stale answer meanwhile
    for _ in range(200):
        if not catalog._refreshing:
            break
        time.sleep(0.05)
    assert calls and all(thread != me for thread, _ in calls)

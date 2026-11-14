"""gamma/agents.py — who an agent is: its id by kind (Gamma AI, a computer's
agent, a connection), its name and, for a computer's agent, its computer as
it is called now; the agent a request, a connection and a turn's token
write as; the stamp a block it makes keeps; and the agents of an account."""

import time
from types import SimpleNamespace

import pytest

from conftest import account_of, make_user, workspace_of
from gamma import agents, integrations, local_agents
from gamma.computers import channel
from gamma.db import connect_users_db, connect_vaults_db, page_now
from gamma.local_agents import store, tools

USER = "agents-tim"


@pytest.fixture
def tim(monkeypatch):
    """An account on a server that runs agents, this computer named
    TIM-PC; the computers and agents it adds are gone after."""
    make_user(USER, "pw")
    monkeypatch.setattr(local_agents, "enabled", lambda: True)
    monkeypatch.setattr(channel, "computer_name", lambda: "TIM-PC")
    me = account_of(USER)
    yield SimpleNamespace(id=me, ws=workspace_of(USER))
    with connect_vaults_db() as conn:
        conn.execute("DELETE FROM computer_agents")
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computers WHERE user_id = ?", (me,))
        conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (me,))


def _computer(tim, token_id: str, computer: str, name: str, assistants: str = "[]") -> None:
    with connect_users_db() as conn:
        conn.execute("INSERT INTO computers (token_id, user_id, workspace_id, computer, name, assistants, seen_at, "
                     "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                     (token_id, tim.id, tim.ws, computer, name, assistants, page_now(), page_now()))


def test_an_ids_kind():
    assert [agents.kind_of(i) for i in ("", "gamma", "computer:PC:codex", "computer::codex", "3f9a")] == [
        "", "gamma", "computer", "computer", "connection"]
    assert agents.gamma() == agents.Agent("gamma", "gamma", "Gamma AI")
    assert agents.computer_agent("PC", "claude", "TIM-PC") == agents.Agent(
        "computer:PC:claude", "computer", "Claude Code", "TIM-PC")
    assert agents.computer_agent("PC", "claude", "TIM-PC").label == "Claude Code on TIM-PC"
    assert agents.computer_agent("", "codex").label == "Codex"


def test_this_computers_agent(tim):
    """This computer's id is stable, no install id itself, and its agent is
    named with the computer's name."""
    from gamma.cloud_auth import device

    here = agents.here("codex")
    assert here.id == f"computer:{agents.here_id()}:codex" and agents.here_id() == agents.here_id()
    assert device()[0] not in here.id and len(agents.here_id()) == 22
    assert (here.name, here.on, here.label) == ("Codex", "TIM-PC", "Codex on TIM-PC")


def test_a_channels_computer_agent(tim):
    """A run a computer holds is its agent there, by the computer's id for
    this server and the name it announced; a token no computer announced
    with is an agent on a computer not known."""
    _computer(tim, "agTok1", "LAPTOP-ID", "LAPTOP")
    assert agents.of_channel("agTok1", "claude") == agents.Agent(
        "computer:LAPTOP-ID:claude", "computer", "Claude Code", "LAPTOP")
    assert agents.of_channel("agTokNone", "codex") == agents.Agent("computer::codex", "computer", "Codex")


def test_reading_agents_names_their_computers_as_now(tim):
    """``read_all`` names a computer's agent by the catalog and its computer
    as it is called now, this computer where agents run here; a connection
    by the name it wrote with; none for a person."""
    _computer(tim, "agTok2", "NAS-PC", "OLD NAME")
    here = agents.here("claude").id
    stored = {(tim.id, i): name for i, name in (("gamma", ""), (here, "Claude Code"), ("computer:NAS-PC:codex", "Codex"),
                                                 ("computer:GONE:codex", "Codex"), ("tokX", "Script"))}
    got = agents.read_all(stored)
    assert [got[k].label for k in stored] == ["Gamma AI", "Claude Code on TIM-PC", "Codex on OLD NAME", "Codex",
                                              "Script"]
    assert [got[k].kind for k in stored] == ["gamma", "computer", "computer", "computer", "connection"]
    nas = (tim.id, "computer:NAS-PC:codex")
    with connect_users_db() as conn:
        conn.execute("UPDATE computers SET name = 'NEW NAME' WHERE token_id = 'agTok2'")
    assert agents.read_all({nas: ""})[nas].on == "NEW NAME"
    other = ("someone else", nas[1])
    assert agents.read_all({other: ""})[other].on == "", "only the account's own computers name its agent's"
    assert agents.read_all({}) == {}


def test_who_a_token_writes_as(tim, monkeypatch):
    """A connection writes as itself, named by its display name; a turn's
    token is named with its agent's id (``tools.mint``) and writes as that
    agent; a session's request is no agent's, a token's its token's."""
    oauth = integrations.Connection(tim.id, [tim.ws], "read", "tok9", "Claude Code (OAuth)", USER)
    assert agents.of_connection(oauth) == agents.Agent("tok9", "connection", "Claude Code")
    monkeypatch.setattr(tools, "_url", "http://127.0.0.1:9100/mcp")
    _gamma, token_id = tools.mint(tim.id, tim.ws, "codex", 60)
    with connect_users_db() as conn:
        name, scope = conn.execute("SELECT name, scope FROM integration_tokens WHERE id = ?", (token_id,)).fetchone()
    assert (name, scope) == (agents.here("codex").id, integrations.TURN)
    assert agents.of_token(token_id, name, scope) == agents.here("codex")
    integrations.drop(token_id)
    assert agents.of_request(SimpleNamespace(state=SimpleNamespace(auth="session"))) is None
    token = SimpleNamespace(state=SimpleNamespace(auth="token", token_id="tok8", token_name="Script", token_scope="write"))
    assert agents.of_request(token) == agents.Agent("tok8", "connection", "Script")


def test_the_stamp_of_a_block():
    assert agents.stamp(agents.computer_agent("PC", "codex", "TIM-PC"), "tim") == {
        "id": "computer:PC:codex", "name": "Codex", "on": "TIM-PC", "for": "tim"}
    assert agents.stamp(agents.gamma(), "tim") == {"id": "gamma", "name": "Gamma AI", "for": "tim"}


def test_an_accounts_agents(tim):
    """Gamma AI; the agents it turned on here; those its computers shown to
    the workspace announce on for it; its live connections that may comment
    there (a write token of that workspace, a read token at the Comment
    level or the edit level there, or in every workspace), never a read
    token that reads alone, one of another workspace or an expired one."""
    assert agents.of_account(tim.id, tim.ws) == [agents.gamma()]
    store.set_agent("claude", True, by=tim.id)
    computer = integrations.create_token(tim.id, tim.ws, "Gamma desktop: LAPTOP", 30, scope="computer")
    _computer(tim, computer["id"], "LAPTOP-ID", "LAPTOP",
              '[{"name": "codex", "enabled": true}, {"name": "claude", "enabled": false}]')
    commenting = integrations.create_token(tim.id, tim.ws, "Claude Code (OAuth)", 30, comments=f'["{tim.ws}"]')
    everywhere = integrations.create_token(tim.id, tim.ws, "Codex", 30, workspaces="*", comments="*")
    integrations.create_token(tim.id, tim.ws, "A reader", 30, scope="read")
    integrations.create_token(tim.id, tim.ws, "Elsewhere's commenter", 30, workspaces='["elsewhere", "far"]',
                              comments='["far"]')
    integrations.create_token(tim.id, "elsewhere", "Another lab's", 30, scope="write")
    expired = integrations.create_token(tim.id, tim.ws, "Old script", 30, scope="write")
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET expires_at = ? WHERE id = ?", (int(time.time()) - 1, expired["id"]))
    assert [a.label for a in agents.of_account(tim.id, tim.ws)] == [
        "Gamma AI", "Claude Code on TIM-PC", "Codex on LAPTOP", "Claude Code", "Codex"]
    assert [a.id for a in agents.of_account(tim.id, tim.ws)][3:] == [commenting["id"], everywhere["id"]]
    assert agents.of_account("nobody", tim.ws) == [agents.gamma()]

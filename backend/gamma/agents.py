"""Who an agent is (docs/dev/comments.md "Who wrote a comment",
docs/dev/block_centric.md "Made by an agent"): its id, its kind, its name
and, for a computer's agent, the computer it runs on. No table holds them:
the agents of an account are Gamma AI, the agents its computers have on and
its connections that may comment (``of_account``).

An agent's id is one of

- ``gamma``: Gamma AI, the chat's own;
- ``computer:<computer>:<name>``: the agent ``name`` (``codex``,
  ``claude``) on a computer, ``<computer>`` the id this server knows it by:
  a computer of the channel its own id for this server
  (gamma/computers/registry.py), this computer ``here_id``, and "" for one
  that cannot be known any more (a comment of before step 60,
  gamma/migrations.py);
- anything else: the id of the integration token (an assistant's
  connection, an API token) that wrote it.

What an agent writes is its account's: a comment's ``author``, a block
op's actor, so every role check reads the account as it always did. The
agent is said beside it: a comment's ``agent`` (its name) and ``agent_id``,
a block's ``properties.agent`` (``stamp``). A computer's agent names its
computer as the computer is called now (``read_all``), so a computer
renamed reads renamed; a connection keeps the name it wrote with.
"""

import base64
import hashlib
import hmac
import time
from typing import NamedTuple

GAMMA = "gamma"
COMPUTER = "computer:"
KINDS = ("gamma", "computer", "connection")
GAMMA_NAME = "Gamma AI"


class Agent(NamedTuple):
    """An agent as a comment or a block names it: ``id``, ``kind`` (one of
    ``KINDS``), ``name`` ("Gamma AI", "Codex", a connection's display name)
    and ``on``, a computer's agent's computer by its name ("" when it is not
    known)."""
    id: str
    kind: str
    name: str
    on: str = ""

    @property
    def label(self) -> str:
        """"Codex on TIM-PC", or the name alone: what a server's own words
        (the Inbox, the annotations' Markdown, a resolve) call it."""
        return f"{self.name} on {self.on}" if self.on else self.name


def kind_of(agent_id: str) -> str:
    """The kind an id names (``KINDS``), "" for none (a person's)."""
    if not agent_id:
        return ""
    return "gamma" if agent_id == GAMMA else "computer" if agent_id.startswith(COMPUTER) else "connection"


def _parts(agent_id: str) -> tuple[str, str]:
    """A computer agent's id as ``(computer, name)``."""
    computer, _, name = agent_id[len(COMPUTER):].rpartition(":")
    return computer, name


def gamma() -> Agent:
    return Agent(GAMMA, "gamma", GAMMA_NAME)


def computer_agent(computer: str, name: str, on: str = "") -> Agent:
    """The agent ``name`` on the computer of id ``computer``, called ``on``."""
    from .local_agents import catalog  # local: the agents' package is loaded where an agent is named

    return Agent(f"{COMPUTER}{computer}:{name}", "computer", catalog.label(name), on)


def here_id() -> str:
    """This computer's id as its own server knows it: an HMAC of the
    install's id (``cloud_auth.device``), as its id for each server it
    announces itself to is (gamma/computers/channel.py ``computer_id``), so
    no server can match the ids of one computer."""
    from .cloud_auth import device  # local: cloud_auth imports the workspaces

    digest = hmac.new(device()[0].encode(), b"this server", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]


def here(name: str) -> Agent:
    """The agent ``name`` of this computer, a turn or a Send this server
    runs."""
    from .computers.channel import computer_name

    return computer_agent(here_id(), name, computer_name())


def of_channel(token_id: str, name: str) -> Agent:
    """The agent ``name`` of the computer whose channel token is
    ``token_id`` (a run a computer holds of this server's request)."""
    from .computers import registry

    found = registry.computer_of(token_id)
    return computer_agent(found[0], name, found[1]) if found else computer_agent("", name)


def of_token(token_id: str, token_name: str, scope: str) -> Agent:
    """The agent an integration token writes as: a turn's token is its
    agent's (``integrations.mint_turn`` names it with the agent's id), any
    other its connection, named by ``integrations.display_name``."""
    from .integrations import TURN, display_name

    if scope == TURN and kind_of(token_name) == "computer":
        return here(_parts(token_name)[1])
    return Agent(token_id, "connection", display_name(token_name))


def of_request(request) -> Agent | None:
    """The agent an API request writes as (its token's, ``of_token``),
    None for a person's session."""
    from .auth import is_token

    if not is_token(request):
        return None
    return of_token(request.state.token_id, request.state.token_name, request.state.token_scope)


def of_connection(connection) -> Agent:
    """The agent an MCP connection (``integrations.Connection``) writes as."""
    return of_token(connection.token_id, connection.name, connection.scope)


def read_all(stored: dict[tuple[str, str], str]) -> dict[tuple[str, str], Agent]:
    """``{(account id, agent id): Agent}`` for the keys of ``stored``
    (``{(account id, agent id): the name it was written with}``, the account
    the agent wrote for): Gamma AI, a computer's agent named by the catalog
    on its computer as the computer is called now (this computer by its own
    name; another by the name it last announced here for that account, so
    no other account's row names it; none for a computer no longer known),
    a connection by its stored name. One read of the computers for every
    key."""
    from .computers import registry
    from .computers.channel import computer_name

    this = here_id() if _runs_agents() else None
    names = registry.names_of({(user, _parts(i)[0]) for user, i in stored
                               if kind_of(i) == "computer" and _parts(i)[0] not in ("", this)})
    out = {}
    for (user, agent_id), name in stored.items():
        kind = kind_of(agent_id)
        if kind == "gamma":
            out[user, agent_id] = gamma()
        elif kind == "computer":
            computer, agent = _parts(agent_id)
            on = computer_name() if computer == this else names.get((user, computer), "")
            out[user, agent_id] = (computer_agent(computer, agent, on) if agent
                                   else Agent(agent_id, kind, name or "an agent"))
        elif kind:
            out[user, agent_id] = Agent(agent_id, kind, name or "an agent")
    return out


def _runs_agents() -> bool:
    """Whether agents run on this server, so a comment may be this
    computer's (``local_agents.enabled``)."""
    from . import local_agents

    return local_agents.enabled()


def stamp(agent: Agent, person: str) -> dict:
    """What a block an agent makes carries as ``properties.agent``: ``{id,
    name, on?, for}``, ``for`` the username of the account it acts for,
    whose block it is (docs/dev/block_centric.md "Made by an agent")."""
    return {"id": agent.id, "name": agent.name, **({"on": agent.on} if agent.on else {}), "for": person}


def of_stamp(made: dict) -> Agent:
    """The agent a stamp (``stamp``) names: what a tool's scope carries
    (``agent``) to say who writes, read back for a comment."""
    return Agent(made["id"], kind_of(made["id"]), made["name"], made.get("on", ""))


def of_account(user_id: str, ws: str) -> list[Agent]:
    """The agents of the account ``user_id`` in the workspace ``ws``: Gamma
    AI, the agents it turned on on this computer (where agents run here),
    those its computers shown to ``ws`` announce on for it, and its live
    connections that may comment there (a write token of ``ws``, a read
    token at the Comment level or the edit level there:
    ``integrations.comments_in``), in that order."""
    from .computers import registry
    from .db import connect_users_db
    from .integrations import ALL, comments_in, granted

    out = [gamma()]
    if _runs_agents():
        from .local_agents import store

        out += [here(name) for name, row in store.enabled_agents().items() if row["set_by"] == user_id]
    for computer in registry.of_account(user_id, ws):
        out += [computer_agent(computer["id"], a["name"], computer["name"])
                for a in computer["assistants"] if isinstance(a, dict) and a.get("enabled") and a.get("name")]
    with connect_users_db() as conn:
        rows = conn.execute("SELECT id, name, scope, workspace_id, workspaces, edits, comments FROM integration_tokens "
                            "WHERE user_id = ? AND expires_at > ? AND scope IN ('read', 'write') ORDER BY created_at",
                            (user_id, int(time.time()))).fetchall()
    for token_id, name, scope, primary, value, edits, comments in rows:
        reads = granted(primary, value)
        if (reads == ALL or ws in reads) and comments_in(scope, edits, comments, ws):
            out.append(of_token(token_id, name, scope))
    return list({agent.id: agent for agent in out}.values())

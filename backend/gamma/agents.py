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

A computer's agent may act for a co-author its owner named on the computer
(docs/dev/local_agents.md "A co-author's turn"): what it writes then is the
co-author's, the account the turn is for, and the agent carries its
``owner``, the username of the account whose agent it is, which a block's
stamp and a comment (``agent_owner``) keep as it was then and a comment's
reader says.
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
    ``KINDS``), ``name`` ("Gamma AI", "Codex", a connection's display name),
    ``on``, a computer's agent's computer by its name ("" when it is not
    known), and ``owner``, the username of the account whose agent it is
    when it acts for another (a co-author's turn), "" otherwise."""
    id: str
    kind: str
    name: str
    on: str = ""
    owner: str = ""

    @property
    def label(self) -> str:
        """"Codex on TIM-PC", or the name alone: what a server's own words
        (the annotations' Markdown, a prompt) call it."""
        return f"{self.name} on {self.on}" if self.on else self.name

    def shown(self) -> dict:
        """The agent as the API names it beside what it did (an Inbox
        item's ``agent``, a thread's ``resolved_agent``): ``{id, kind, name,
        on?, owner?}``, which the client words in the reader's language
        (frontend/src/chat/agents.js ``madeBy``). An agent known by its
        words alone (an Inbox item of before step 66 whose agent could not
        be found) has an empty ``id`` and ``kind``."""
        return {"id": self.id, "kind": self.kind, "name": self.name, **({"on": self.on} if self.on else {}),
                **({"owner": self.owner} if self.owner else {})}


def kind_of(agent_id: str) -> str:
    """The kind an id names (``KINDS``), "" for none (a person's)."""
    if not agent_id:
        return ""
    return "gamma" if agent_id == GAMMA else "computer" if agent_id.startswith(COMPUTER) else "connection"


def parts(agent_id: str) -> tuple[str, str]:
    """A computer agent's id as ``(computer, name)``."""
    computer, _, name = agent_id[len(COMPUTER):].rpartition(":")
    return computer, name


def gamma() -> Agent:
    return Agent(GAMMA, "gamma", GAMMA_NAME)


def computer_agent(computer: str, name: str, on: str = "", owner: str = "") -> Agent:
    """The agent ``name`` on the computer of id ``computer``, called ``on``,
    ``owner`` its owner's username when it acts for another."""
    from .local_agents import catalog  # local: the agents' package is loaded where an agent is named

    return Agent(f"{COMPUTER}{computer}:{name}", "computer", catalog.label(name), on, owner)


def here_id(device_id: str = "") -> str:
    """This computer's id as its own server knows it: an HMAC of the
    install's id (``cloud_auth.device``; ``device_id`` where the caller read
    it itself, a users.db step), as its id for each server it announces
    itself to is (gamma/computers/channel.py ``computer_id``), so no server
    can match the ids of one computer."""
    if not device_id:
        from .cloud_auth import device  # local: cloud_auth imports the workspaces

        device_id = device()[0]
    digest = hmac.new(device_id.encode(), b"this server", hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")[:22]


def here(name: str, person: str = "") -> Agent:
    """The agent ``name`` of this computer, of a turn this server runs;
    with ``person`` (the account the turn is for), its ``owner`` when
    another account owns it (``_owner_here``)."""
    from .computers.channel import computer_name

    return computer_agent(here_id(), name, computer_name(), _owner_here(name, person) if person else "")


def _owner_here(name: str, person: str) -> str:
    """The username of the account that owns the agent ``name`` here, when
    it is not ``person``; "" otherwise, and where agents do not run."""
    if not _runs_agents():
        return ""
    from .db import account_names, connect_users_db
    from .local_agents import store

    owner = (store.agents().get(name) or {}).get("set_by") or ""
    if not owner or owner == person:
        return ""
    with connect_users_db() as conn:
        return account_names(conn, {owner}).get(owner, "")


def of_token(token_id: str, token_name: str, scope: str, user_id: str = "", ws: str = "") -> Agent:
    """The agent an integration token of the account ``user_id`` writes
    as: a turn's token is its agent's (``integrations.mint_turn`` names it
    with the agent's id): this computer's (a co-author's turn names its
    owner, ``here``), or another computer's whose turn this server handed
    it (gamma/routers/computers.py), named as that computer announced
    itself in the token's workspace ``ws`` (``registry.far_name``: the
    owner too for a co-author's); any other its connection, named by
    ``integrations.display_name``."""
    from .integrations import TURN, display_name

    if scope == TURN and kind_of(token_name) == "computer":
        computer, name = parts(token_name)
        if computer == here_id() and _runs_agents():
            return here(name, user_id)
        from .computers import registry

        on, owner = registry.far_name(ws, user_id, computer)
        return computer_agent(computer, name, on, owner)
    return Agent(token_id, "connection", display_name(token_name))


def of_request(request) -> Agent | None:
    """The agent an API request writes as (its token's, ``of_token``),
    None for a person's session."""
    from .auth import is_token

    if not is_token(request):
        return None
    return of_token(request.state.token_id, request.state.token_name, request.state.token_scope,
                    request.state.user_id, getattr(request.state, "token_ws", "") or "")


def of_connection(connection) -> Agent:
    """The agent an MCP connection (``integrations.Connection``) writes as
    (a turn's token is of one workspace, its first)."""
    return of_token(connection.token_id, connection.name, connection.scope, connection.user_id,
                    connection.workspaces[0] if connection.workspaces else "")


def read_all(stored: dict[tuple[str, str, str], str], ws: str) -> dict[tuple[str, str, str], Agent]:
    """``{(account id, agent id, owner): Agent}`` for the keys of ``stored``
    (``{(account id, agent id, owner): the name it was written with}``, the
    account the agent wrote for and ``owner`` the username of the agent's
    owner kept with what it wrote, "" for the account's own): Gamma AI, a
    computer's agent named by the catalog on its computer as the computer
    is called now (this computer by its own name; another by the name it
    last announced here for that account, or, for a co-author's, by the
    name its owner's computer announced in the workspace ``ws`` while its
    agent served that account, ``registry.names_of``, so no other account's
    row names it; none for a computer no longer known), with that
    ``owner``, a connection by its stored name. One read of the computers
    for every key."""
    from .computers import registry
    from .computers.channel import computer_name

    this = here_id() if _runs_agents() else None
    names = registry.names_of({(user, parts(i)[0], owner) for user, i, owner in stored
                               if kind_of(i) == "computer" and parts(i)[0] not in ("", this)}, ws)
    out = {}
    for (user, agent_id, owner), name in stored.items():
        kind = kind_of(agent_id)
        key = (user, agent_id, owner)
        if kind == "gamma":
            out[key] = gamma()
        elif kind == "computer":
            computer, agent = parts(agent_id)
            on = computer_name() if computer == this else names.get((user, computer, owner), "")
            out[key] = (computer_agent(computer, agent, on, owner) if agent
                        else Agent(agent_id, kind, name or "an agent"))
        elif kind:
            out[key] = Agent(agent_id, kind, name or "an agent")
    return out


def _runs_agents() -> bool:
    """Whether agents run on this server, so a comment may be this
    computer's (``local_agents.enabled``)."""
    from . import local_agents

    return local_agents.enabled()


def stamp(agent: Agent, person: str) -> dict:
    """What a block an agent makes carries as ``properties.agent``: ``{id,
    name, on?, for, owner?}``, ``for`` the username of the account it acts
    for, whose block it is, ``owner`` the agent's owner's where that is
    another account (a co-author's turn; docs/dev/block_centric.md "Made by
    an agent")."""
    return {"id": agent.id, "name": agent.name, **({"on": agent.on} if agent.on else {}), "for": person,
            **({"owner": agent.owner} if agent.owner else {})}


def of_stamp(made: dict) -> Agent:
    """The agent a stamp (``stamp``) names: what a tool's scope carries
    (``agent``) to say who writes, read back for a comment."""
    return Agent(made["id"], kind_of(made["id"]), made["name"], made.get("on", ""), made.get("owner", ""))


def of_account(user_id: str, ws: str) -> list[Agent]:
    """The agents of the account ``user_id`` in the workspace ``ws``: Gamma
    AI, the agents it turned on on this computer (where agents run here) and
    those their owners here let it use in ``ws`` (``store.lent_to``), those
    its computers shown to ``ws`` announce on for it and those another
    account's computers announce it may use (``registry.of_account``'s
    ``lent_by``), and its live connections that may comment there (a write
    token of ``ws``, a read token at the Comment level or the edit level
    there: ``integrations.comments_in``), in that order."""
    from .computers import registry
    from .db import connect_users_db
    from .integrations import ALL, comments_in, granted

    out = [gamma()]
    if _runs_agents():
        from .local_agents import store

        out += [here(name) for name, row in store.enabled_agents().items() if row["set_by"] == user_id]
        out += [here(name, user_id) for name in store.lent_to(ws, user_id)]
    for computer in registry.of_account(user_id, ws):
        out += [computer_agent(computer["id"], a["name"], computer["name"], computer.get("lent_by") or "")
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

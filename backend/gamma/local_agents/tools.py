"""Gamma's tools for a turn of an agent on this computer
(docs/dev/local_agents.md "Gamma's tools"): the address of this server's
``/mcp`` as the agent's process reaches it, and the token of the turn.

The address (``mcp_url``) is learned from the requests this server is sent
(``heard``, called by the session middleware): the first that came straight
to its socket from this computer names it, a loopback client to
``127.0.0.1`` or ``::1`` (the hosts MCP's transport takes,
``server_settings.mcp_allowed_hosts``) with no ``X-Forwarded-Proto``, whose
scheme uvicorn would take in place of the socket's. The desktop app's window
sends one before any turn; a server reached only through a proxy or from
other computers learns none, and its turns run without Gamma's tools.

The token (``mint``) is ``integrations.mint_turn``'s: the turn's account
and workspace, the agent's label, the turn's timeout. It reaches the agent
in ``events.TOKEN_ENV`` of that one process (``events.Gamma``), never on its
command line, in its prompt or in a file. The runner deletes it when the
turn ends, however it ends (``integrations.drop``), keeps it alive while the
turn's deadline moves (``integrations.renew_turn``) and deletes every one a
crash left at start (``integrations.drop_turns``)."""

from .. import host_scope
from ..logbuf import log
from . import events

# The socket addresses an agent's MCP client may name: MCP's transport takes
# 127.0.0.1 and [::1] at any port.
_HOSTS = {"127.0.0.1": "127.0.0.1", "::ffff:127.0.0.1": "127.0.0.1", "::1": "[::1]"}
_url = ""


def heard(scope) -> None:
    """A request came in: the first that came straight from this computer
    to a loopback socket names ``mcp_url``. Cheap: once it is known, this
    returns at once."""
    global _url
    if _url or scope.get("type") != "http":
        return
    server, client = scope.get("server") or (), scope.get("client") or ()
    if len(server) != 2 or not isinstance(server[1], int) or server[0] not in _HOSTS or not client:
        return
    if host_scope.literal(str(client[0])) != host_scope.LOOPBACK:
        return
    if any(name == b"x-forwarded-proto" for name, _ in scope.get("headers") or ()):
        return
    _url = f"{scope.get('scheme') or 'http'}://{_HOSTS[server[0]]}:{server[1]}/mcp"


def mcp_url() -> str:
    """This server's ``/mcp`` as an agent on this computer reaches it, ""
    until a request named it (``heard``)."""
    return _url


def mint(user_id: str, ws: str, label: str, seconds: float) -> tuple[events.Gamma, str] | None:
    """Gamma's tools for one turn of ``label`` for ``user_id`` in ``ws``:
    ``(events.Gamma(url, token), the token's id)``, the token living
    ``seconds``; None while this server's address is unknown (the turn
    runs without them)."""
    from .. import integrations  # local: integrations imports workspaces

    url = mcp_url()
    if not url:
        log.info(f"[agents] no request came to this server from this computer yet: {label} runs without "
                 "Gamma's tools")
        return None
    token_id, token = integrations.mint_turn(user_id, ws, label, seconds)
    return events.Gamma(url, token), token_id

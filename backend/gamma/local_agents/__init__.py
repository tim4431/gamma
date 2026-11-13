"""An agent on this computer: Gamma itself is the connector that answers a
work folder's Sends (docs/dev/local_agents.md). On the desktop app's own
server, the vault's maker turns on Codex CLI or Claude Code for a work
folder; a Send made in Gamma then starts it in the folder's directory with
the comments as the prompt, and its answers come back as replies.

- ``catalog``: the known agents, where each is on this computer, whether it
  is signed in.
- ``procs``: running a CLI with no console window, in a Job object (Windows)
  or its own session (POSIX), so it dies with this server.
- ``events``, ``codex``, ``claude``: the drivers, one turn of an agent as a
  stream of plain events.
- ``store``, ``prompt``, ``runner``: what this computer keeps, what the
  agent is told, claiming the requests and running them.

This module imports only ``config`` and ``version``: folder_links, the
feedback modules and the routers import the rest inside functions.
"""

from .. import config, version


def enabled() -> bool:
    """Whether this server runs agents: it keeps folders on disk (the
    desktop app's own server), it is not the Mac App Store build (which may
    not run code it was not reviewed with), and either the desktop app
    started it (its shell secret is set) or GAMMA_LOCAL_AGENTS is on.
    GAMMA_LOCAL_AGENTS off turns it off always."""
    setting = config.local_agents_setting()
    if setting is False or not config.folder_links_enabled() or version.UPDATES == "mas":
        return False
    return bool(config.shell_secret()) or setting is True

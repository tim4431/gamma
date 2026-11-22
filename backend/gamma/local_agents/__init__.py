"""The agents on this computer: Codex CLI and Claude Code, turned on in
Settings → AI → Local agents and used from the chat and from a comment ticked
*Send to* (docs/dev/local_agents.md). On the desktop app's own server a
turn runs the agent in the directory its conversation works in (a synced
folder's, one the computer offers, one of its own), with Gamma's tools, and
its answer comes back in the chat or in the thread.

- ``catalog``: the known agents, where each is on this computer, whether it
  is signed in.
- ``procs``: running a CLI with no console window, in a Job object (Windows)
  or its own session (POSIX), so it dies with this server.
- ``events``, ``codex``, ``claude``: the drivers, one turn of an agent as a
  stream of plain events.
- ``store``, ``prompt``, ``runner``: what this computer keeps, what the
  agent is told, and the turns' queues and runs.
- ``chats``, ``home``, ``threads``, ``mention``, ``tools``: the
  conversations, where a turn's conversation lives, a comment sent to an
  agent, an ``@name`` read as one, and the token of a turn's Gamma tools.

This module imports only ``config`` and ``version``: folder_links, the
annotation modules and the routers import the rest inside functions.
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

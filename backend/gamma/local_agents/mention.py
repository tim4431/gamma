"""An agent's ``@name`` in a comment (docs/dev/local_agents.md "A comment sent
to an agent"): the comment field ticks *Send to {agent}* when the person
types ``@codex``, and a client that sent no ``to`` has it read here, so the
server decides once: ``to_of`` is the agent a person's comment newly
names, among those its writer may use on the page (gamma/agents.py ids:
this computer's agent the account turned on or its owner lends it, else
the agent of the computer of the account's, or of the owner's that lends
it, that keeps the page's work folder, ``chats.remote_of``), "" for none. Everywhere else ``@codex`` is what it
was: plain text, or a member named codex.

This module is imported inside functions (gamma/local_agents/__init__.py).
"""

from .. import agents, mentions
from . import catalog, chats, enabled, store


def to_of(ws: str, event: dict) -> str:
    """The id of the agent a person's committed comment write (a create, a
    reply, an edit: ``comments.written``'s event) names with ``@name``
    outside code that it did not name before (an edit's text before), ""
    for none: the agent of that name of the computer of the writer's that
    keeps the page's work folder, where no vault here keeps it (the chat's
    rule: the page's chat is that computer's agent's), else this
    computer's agent of that name the writer turned on here or its owner
    lends it (``store.usable``), in the catalog's order."""
    from ..computers import registry  # local: the registry reaches the workspaces

    names = mentions.people_in(event.get("body") or "")
    if event.get("kind") == "edit":
        names -= mentions.people_in(event.get("before") or "")
    named = [name for name in catalog.AGENTS if name in names]
    if not named:
        return ""
    actor, page_id = event.get("actor") or "", event["page_id"]
    place = chats.place_of(ws, page_id, actor) if enabled() else None
    far = chats.remote_of(ws, page_id, actor) if place is None or place["link"] is None else None
    if far is not None:
        held = registry.computer_of(far["token_id"]) if far["agent"] in named else None
        return agents.computer_agent(held[0], far["agent"]).id if held else ""
    mine = next((name for name in named if store.usable(actor, ws, name)), "") if place is not None else ""
    return agents.here(mine).id if mine else ""

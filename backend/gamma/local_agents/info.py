"""What a work folder's vault says of the agent on this computer that works
in it, for the folder-links API's links (``_info``) and a page's chip
(``folder_links.page_vault``): ``{name, label, ready, problem, running}``,
the agent at work there now, else the first agent on of the vault's maker
(``store.agent_for``), ``running`` ``{detail}`` while a turn of it works
there, else null; and null for a vault with neither, one that is no work folder (of this server or another,
``folder_links.work_vault``), and on a server that runs no agents. What
an agent may run is the computer's (``GET /api/local-agents``), not the
vault's.

A read path: nothing here runs a process. Whether the agent is ready is
``catalog.cached``'s (a stale answer starts one probe in the background),
the store is asked once for a whole listing, and a turn is the runner's
while it runs (``runner.at_work``). A member
who cannot act on the vault reads no detail: it may name its files.
"""

from . import catalog, enabled, runner, store


def listing(links, *, can_act=lambda link: True) -> dict[str, dict | None]:
    """``{link id: its agent's info or None}`` of ``links``, the store asked
    once for them all; ``can_act(link)``: whether the asking account may
    read the turn's detail."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = list(links)
    out: dict[str, dict | None] = {link["id"]: None for link in links}
    if not enabled():
        return out
    work = [link for link in links if folder_links.work_vault(link)]
    on = store.enabled_agents() if work else {}
    runs = runner.at_work([link["id"] for link in work]) if work else {}
    for link in work:
        run = runs.get(link["id"])
        name = (run or {}).get("agent") or next(
            (row["agent"] for row in on.values() if row["set_by"] == link["created_by"]), "")
        if name not in catalog.AGENTS:
            continue
        found = catalog.cached(name)
        running = {"detail": run["detail"] if can_act(link) else ""} if run is not None else None
        out[link["id"]] = {"name": name, "label": catalog.label(name), "ready": found["ready"],
                           "problem": found["problem"], "running": running}
    return out


def agent_info(link: dict, *, can_act: bool = True) -> dict | None:
    """The agent's info of one vault (``listing``'s), None for none."""
    return listing([link], can_act=lambda _link: can_act)[link["id"]]

"""What a work folder's vault says of the agent on this computer that works
in it, for the folder-links API's links (``_info``) and a page's chip
(``folder_links.page_vault``): ``{name, label, ready, problem, running}``,
the agent at work there now, else the first agent on of the vault's maker
(``store.agent_for``), ``running`` what it works on now, ``{kind: "send",
request_id, detail}`` for a Send or ``{kind: "chat", request_id: null,
detail}`` for a turn of the chat's computer side, or null; and null for a
vault with neither, one that is no work folder (of this server or another,
``folder_links.agent_folder``), and on a server that runs no agents. What
an agent may run is the computer's (``GET /api/local-agents``), not the
vault's.

A read path: nothing here runs a process. Whether the agent is ready is
``catalog.cached``'s (a stale answer starts one probe in the background),
the store is asked once for a whole listing, and a run is the runner's
while it holds the request or runs the turn (``runner.at_work``). A member
who cannot act on the vault reads no detail: it may name its files.
"""

from ..db import connect_pages_db
from . import catalog, enabled, runner, store


def _detail(ws: str, request_id: str) -> str:
    """The progress the request shows now, "" once it is no longer running."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT state, detail FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
    return row[1] if row and row[0] == "running" else ""


def listing(links, *, can_act=lambda link: True) -> dict[str, dict | None]:
    """``{link id: its agent's info or None}`` of ``links``, the store asked
    once for them all; ``can_act(link)``: whether the asking account may
    read the run's detail."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = list(links)
    out: dict[str, dict | None] = {link["id"]: None for link in links}
    if not enabled():
        return out
    work = [link for link in links if folder_links.agent_folder(link)]
    on = store.enabled_agents() if work else {}
    runs = runner.at_work([link["id"] for link in work]) if work else {}
    for link in work:
        run = runs.get(link["id"])
        name = (run or {}).get("agent") or next(
            (row["agent"] for row in on.values() if row["set_by"] == link["created_by"]), "")
        if name not in catalog.AGENTS:
            continue
        found = catalog.cached(name)
        running = None
        if run is not None and run["kind"] == runner.CHAT:
            running = {"kind": runner.CHAT, "request_id": None, "detail": run["detail"] if can_act(link) else ""}
        elif run is not None:
            # A Send of another server's work folder: the progress its run wrote there last.
            detail = run["detail"] if run["remote"] else _detail(run["ws"], run["id"])
            running = {"kind": runner.SEND, "request_id": run["id"], "detail": detail if can_act(link) else ""}
        out[link["id"]] = {"name": name, "label": catalog.label(name), "ready": found["ready"],
                           "problem": found["problem"], "running": running}
    return out


def agent_info(link: dict, *, can_act: bool = True) -> dict | None:
    """The agent's info of one vault (``listing``'s), None for none."""
    return listing([link], can_act=lambda _link: can_act)[link["id"]]

"""What a work folder's vault says of its agent on this computer, for the
folder-links API's links (``_info``) and a page's chip
(``folder_links.page_vault``): ``{name, label, ready, problem, running,
runs}``, ``running`` what it works on now, ``{kind: "send", request_id,
detail}`` for a Send or ``{kind: "chat", request_id: null, detail}`` for a
turn of the chat's computer side, or null, ``runs`` what it may run
(``never`` or ``ask``, ``store.runs_of``); and null for a vault with no
agent, one that is no work folder, and on a server that runs no agents.

A read path: nothing here runs a process. Whether the agent is ready is
``catalog.cached``'s (a stale answer starts one probe in the background), a
listing asks the store once for all its vaults' agents (``store.agents_of``),
and a run is the runner's while it holds the request or runs the turn
(``runner.at_work``). A member who cannot act on the vault reads no detail:
it may name its files.
"""

from ..db import connect_pages_db
from . import catalog, enabled, runner, store


def _detail(ws: str, request_id: str) -> str:
    """The progress the request shows now, "" once it is no longer running."""
    with connect_pages_db(ws) as conn:
        row = conn.execute("SELECT state, detail FROM feedback_requests WHERE id = ?", (request_id,)).fetchone()
    return row[1] if row and row[0] == "running" else ""


def listing(links, *, can_act=lambda link: True) -> dict[str, dict | None]:
    """``{link id: its agent's info or None}`` of ``links``, in one query of
    the store for them all; ``can_act(link)``: whether the asking account
    may read the run's detail."""
    from .. import folder_links  # local: folder_links imports this package's modules inside functions

    links = list(links)
    out: dict[str, dict | None] = {link["id"]: None for link in links}
    if not enabled():
        return out
    work = [link for link in links if folder_links.work_folder_link(link)]
    rows = store.agents_of([link["id"] for link in work]) if work else {}
    runs = runner.at_work(rows) if rows else {}
    policies = store.policies_of(rows) if rows else {}
    for link in work:
        row = rows.get(link["id"])
        if row is None:
            continue
        found = catalog.cached(row["agent"])
        run = runs.get(link["id"])
        running = None
        if run is not None and run["kind"] == runner.CHAT:
            running = {"kind": runner.CHAT, "request_id": None, "detail": run["detail"] if can_act(link) else ""}
        elif run is not None:
            running = {"kind": runner.SEND, "request_id": run["id"],
                       "detail": _detail(run["ws"], run["id"]) if can_act(link) else ""}
        out[link["id"]] = {"name": row["agent"], "label": catalog.label(row["agent"]), "ready": found["ready"],
                           "problem": found["problem"], "running": running,
                           "runs": policies.get(link["id"], "never")}
    return out


def agent_info(link: dict, *, can_act: bool = True) -> dict | None:
    """The agent's info of one vault (``listing``'s), None for none."""
    return listing([link], can_act=lambda _link: can_act)[link["id"]]

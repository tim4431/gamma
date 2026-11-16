"""Detach: the one way a work folder leaves Gamma (docs/dev/work_folders.md
"Detaching a work folder"). The interface calls it a synced folder. Every
server answers it for a work folder's root W of its own workspace, so a page
asks its own server, in every window (gamma/routers/folders.py).

What it does depends on what keeps W:

- **a synced vault of this server** (the desktop app's own server, where
  links are kept): the vault is removed with the choice
  (gamma/folder_links.py ``delete_link``);
- **a computer's vault over its channel** (a work folder of this server
  kept on a PC, docs/dev/computers.md), **or none any more**: the folder is
  let go in place with the choice (``takeover.let_go``), and each computer
  that announced a vault of W is woken (``let_go``), so its vault finds the
  mark gone at once (``folder_links.LET_GO_THERE``), its agent's run there
  stops, and it is forgotten there; one offline finds it at its next
  round. Once it is let go, the chat turns queued for a computer to work
  in it fail (``chats.fail_detached``); a refused let go fails none.

The choice (``pages``): ``keep``, every file stays in Gamma as an ordinary
page filed where it was, its bytes stored (a file too large to keep stays
listed); ``remove``, the files not in the library go to Recently deleted.
Files in the library stay library pages either way. The directory is never
touched beyond Gamma's own marker and ``.gamma/`` files.

Who may: the maker of the vault of this server that keeps W, alone; where
none does, the workspace's owner, and an editor's computer that announced a
vault of W (its account). The computer is named to the account it is of
alone (computers.md "Trust").
"""

from .. import blocks_store, config  # blocks_store as a module: it imports this package
from ..db import connect_pages_db
from . import elsewhere, takeover
from .library import work_folder_of
from .marks import Tree


def tally(pages: dict) -> dict:
    """``{files, in_library, too_large}`` of a work folder's live files
    (``{page id: properties}``, a remote's ``work_pages``): every file, those
    in the library already, and those listed without their bytes
    (``documents.model.is_listed``), which a keep leaves listed."""
    files = [props for props in pages.values() if work_folder_of(props)]
    return {"files": len(files),
            "in_library": sum(1 for props in files if props.get("library") is True),
            "too_large": sum(1 for props in files if isinstance(props.get("doc"), dict)
                             and props["doc"].get("at") == "listed")}


def _link_of(ws: str, w: str) -> dict | None:
    """The folder vault of this server that keeps ``w``, whoever made it;
    None where no links are kept (a stale row there keeps nothing)."""
    from .. import folder_links  # local: it imports this package

    if not config.folder_links_enabled():
        return None
    return next((link for link in folder_links.all_links() if link["kind"] == "folder" and not link["remote_url"]
                 and link["workspace_id"] == ws and link["folder_id"] == w), None)


def plan(ws: str, w: str, user_id: str, owner: bool) -> dict:
    """What detaching ``w`` would do, to say before it is asked: ``{may,
    files, in_library, too_large, bytes, computer}``, the files and those
    in the library already (``tally``), the bytes a keep
    stores (the files read in place on this computer; a computer's vault
    pushed its files' bytes here already) and ``computer: {name, online}``,
    the computer that keeps it, for the account it is of (null for anyone
    else, and when none keeps it). ``{may: false, why}`` alone for a caller
    who may not, ``why`` the rule's word for the menu to say: ``elsewhere``
    in a workspace something else keeps in step, whose marks are copies;
    ``maker`` where a vault of this server keeps it (its maker alone may);
    ``keeper`` otherwise (the workspace's owner, or the account whose
    computer keeps it: ``allowed``)."""
    from ..computers import channel, registry
    from .. import folder_links

    if elsewhere.kept_elsewhere(ws):
        return {"may": False, "why": "elsewhere"}
    link = _link_of(ws, w)
    if not allowed(ws, w, user_id, owner):
        return {"may": False, "why": "maker" if link is not None else "keeper"}
    if link is not None:
        said = folder_links.removal(link)
        return {"may": True, **{key: said[key] for key in ("files", "in_library", "too_large", "bytes")},
                "computer": {"name": channel.computer_name(), "online": True}}
    mine = [c for c in registry.keepers(ws, w) if c["user_id"] == user_id]
    with connect_pages_db(ws) as conn:
        files = {r[0]: blocks_store.load_props(r[1]) for r in conn.execute(
            "SELECT id, properties FROM unified_blocks WHERE work_folder = ? AND +parent_id = 'root'", (w,))}
    return {"may": True, **tally(files), "bytes": 0,
            "computer": {"name": mine[0]["name"], "online": mine[0]["online"]} if mine else None}


def allowed(ws: str, w: str, user_id: str, owner: bool) -> bool:
    """Whether ``user_id`` may detach ``w``: the maker of this server's link
    that keeps it alone (its directory is on that account's computer, as
    ``routers/folder_links.py`` ``_made`` holds); where none does, the
    workspace's owner (``owner``), or the account of a computer that
    announced a vault of it, while it may edit the workspace (a viewer's
    computer token announces whatever it likes)."""
    from ..computers import registry
    from .. import workspaces

    link = _link_of(ws, w)
    if link is not None:
        return link["created_by"] == user_id
    return owner or (workspaces.at_least(workspaces.role_of(ws, user_id), "editor")
                     and any(c["user_id"] == user_id for c in registry.keepers(ws, w)))


def detach(ws: str, w: str, actor: str, pages: str) -> dict:
    """``w`` detached by ``actor`` with the choice ``pages`` (the module doc)
    → ``{ok, kept, trashed, mark_stays?}``, the files kept as pages and
    those sent to Recently deleted. ``folder_links.LinkError`` when the
    vault of this server cannot be removed now (a PDF that cannot be stored,
    a file another program holds): nothing was detached. The caller checks
    ``allowed`` and the mark."""
    from ..computers import registry
    from ..local_agents import chats
    from .. import folder_links

    link = _link_of(ws, w)
    if link is not None:
        return folder_links.delete_link(link["id"], pages) or {"ok": True, "kept": 0, "trashed": 0}
    keeping = registry.keepers(ws, w)
    done = takeover.let_go(ws, w, actor, trash_files=pages == "remove")
    chats.fail_detached(ws, w)
    for computer in keeping:
        registry.wake(computer["token_id"], "let_go")
    return {"ok": True, **done}


def marked(ws: str, w: str) -> bool:
    """Whether ``w`` is a work folder's root: the folder itself carries the
    mark."""
    with connect_pages_db(ws) as conn:
        return Tree.read(conn).mark(w) is not None

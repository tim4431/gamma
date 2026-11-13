"""Whether a workspace is kept in step by something else that writes it too
(docs/dev/work_folders.md "A work folder of another server"): a clone, which
copies another workspace, and a Zotero link, which keeps one in step with a
library. No vault pushes everything into such a workspace: the computer asks
before it makes one of its own (gamma/folder_links.py ``check_vault``), and
every server asks before a vault's mark lands on one of its folders, a
computer's of another server among them (gamma/ops.py ``tree_rule``)."""

from ..db import connect_users_db


def kept_elsewhere(ws: str) -> str:
    """Why nothing may push everything into ``ws`` ("" when it may): a clone
    copies another workspace and a Zotero link keeps one in step with a
    library, and each writes it too."""
    from ..sync_engine import get_mirror  # local: it imports the routers

    mirror = get_mirror(ws)
    if mirror is not None and mirror["page_filter"] is None:
        return "This workspace is a clone of another: a vault in it pulls, or pushes PDFs, but cannot push everything."
    with connect_users_db() as conn:
        if conn.execute("SELECT 1 FROM zotero_links WHERE workspace_id = ?", (ws,)).fetchone():
            return ("This workspace is kept in step with a Zotero library: a vault in it pulls, or pushes PDFs, "
                    "but cannot push everything.")
    return ""

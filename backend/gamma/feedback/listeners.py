"""What keeps feedback current (docs/dev/feedback.md "What keeps it
current"): two listeners, registered by ``register`` (app.create_app calls
it, so every server process has them).

- On every committed comment write (``comments.write_listeners``): the
  page's work folder's files are due (gamma/feedback/files.py), the
  requests waiting on the thread hear a reply or a resolve
  (gamma/feedback/requests.py ``answered``), and the commented version is
  kept or released (gamma/feedback/kept.py).
- On every committed op batch (``ops.commit_listeners``): the page's files
  are due, whoever wrote it, a vault included: a relocation, a trash or a
  restore and an orphaning delete move an entry. Nothing is read here; the
  writer resolves the marked pages to their work folders.

Each part runs on its own, its module loaded when it is first asked for,
so one that fails or is missing never stops the others, and none of them
ever fails the write.
"""

from .. import comments, config
from ..logbuf import log
from ..ops import commit_listeners


def _on_comment(ws: str, event: dict) -> None:
    page_id = event.get("page_id") or ""
    if page_id and config.folder_links_enabled():
        try:
            from . import files

            files.mark(ws, page_id)
        except Exception as e:  # noqa: BLE001 — each part on its own
            log.warning(f"[feedback] marking page {page_id} of workspace {ws}: {e}")
    try:
        from . import requests

        requests.answered(ws, event)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[feedback] the requests after a {event.get('kind')} in workspace {ws}: {e}")
    try:
        from . import kept

        kept.after_comment(ws, event)
    except Exception as e:  # noqa: BLE001
        log.warning(f"[feedback] the kept version after a {event.get('kind')} in workspace {ws}: {e}")


def _on_commit(ws: str, client: str, page_id: str) -> None:
    if not page_id or not config.folder_links_enabled():
        return
    from . import files

    files.mark(ws, page_id)


def register() -> None:
    """The two listeners registered, once."""
    if _on_comment not in comments.write_listeners:
        comments.write_listeners.append(_on_comment)
    if _on_commit not in commit_listeners:
        commit_listeners.append(_on_commit)

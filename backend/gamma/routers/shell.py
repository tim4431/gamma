"""The desktop shell's word (desktop/docs/architecture.md "The shell's
word"): the routes of the desktop app's own server that a page of Gamma,
which shares its origin and session, must not reach by itself. One that
names a path on this computer (the shell's native dialog picked it), turns
an agent on or lets it ask (the computer's decision) takes the shell's
secret in its header when the app started this server with one
(``from_shell``); one that sends this computer's tokens on or decides what
it trusts takes it always, a server started without a secret refusing it
too (``shell_strict``). The folder-links, computer-links, remote-servers
and local-agents routers ask here."""

import secrets

from fastapi import HTTPException, Request

from .. import config
from ..auth import require_personal_user_id

SIGN_IN = "Sign in with an account to sync with this computer."
ELSEWHERE = ("Synced vaults are kept by the Gamma desktop app: open this server in it and use a folder's "
             "“Sync with this computer…” menu item or the sync panel's “Add a synced vault…”.")
SHELL_HEADER = "x-gamma-shell"
PATH_ONLY = "Only the desktop app itself may name a path on this computer."


def account(request: Request) -> str:
    """The signed-in account, on the one server that keeps links."""
    if not config.folder_links_enabled():
        raise HTTPException(status_code=404, detail=ELSEWHERE)
    return require_personal_user_id(request, SIGN_IN)


def _said(request: Request, secret: str) -> bool:
    return secrets.compare_digest(request.headers.get(SHELL_HEADER, "").encode(), secret.encode())


def from_shell(request: Request, refused: str = PATH_ONLY) -> str:
    """``account`` for a route that takes the shell's word: when the app
    started this server with a secret (``config.shell_secret``), a request
    without it is a page speaking for itself, 403 with ``refused``."""
    user_id = account(request)
    secret = config.shell_secret()
    if secret and not _said(request, secret):
        raise HTTPException(status_code=403, detail=refused)
    return user_id


def shell_strict(request: Request, refused: str) -> str:
    """``from_shell`` for a route that sends this computer's tokens on or
    decides what it trusts (routers/remote_servers.py): a server started
    with no secret refuses it too (403), so it never passes unchecked."""
    user_id = account(request)
    secret = config.shell_secret()
    if not secret or not _said(request, secret):
        raise HTTPException(status_code=403, detail=refused)
    return user_id

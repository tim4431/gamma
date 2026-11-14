"""The other Gamma servers this computer's links name (gamma/remote_servers.py,
docs/dev/https.md): the desktop app's own server only, as synced vaults (404
elsewhere), a signed-in account's session only. What an address over plain
http may be used for is the session's to ask (``POST /check``: it says
nothing the account does not see); moving the account's links to a server's
new address, and the certificates the shell trusts (gamma/tls_trust.py), take
the shell's word, with no secret configured refused too
(routers/folder_links.py ``_shell_strict``)."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .. import folder_links, remote_servers, tls_trust
from .folder_links import _account, _shell_strict

router = APIRouter(prefix="/api/remote-servers", tags=["remote-servers"])

SHELL_ONLY = "Only the desktop app itself may change the addresses of this computer's links or what it trusts."


class Check(BaseModel):
    url: str = Field(min_length=1, max_length=400)   # the other server's address, as the shell opened it
    vault: str = Field(default="", max_length=100)      # the vault whose token is renewed, none for a new link
    workspace: str = Field(default="", max_length=100)  # the workspace there of the channel the token is for


class Move(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    frm: str = Field(alias="from", min_length=1, max_length=400)   # the address the links have
    to: str = Field(min_length=1, max_length=400)                  # the server's new one
    dry_run: bool = False      # only count what would move: no request is made
    plain_ok: bool = False     # the shell asked the person about a new address over http://


class Pin(BaseModel):
    origin: str = ""
    sha256: str = ""
    subject: str = ""
    issuer: str = ""
    not_before: str = ""
    not_after: str = ""


class Pins(BaseModel):
    pins: list[Pin] = Field(default_factory=list, max_length=1000)   # the shell's whole list (tls_trust.MAX_PINS)


@router.post("/check")
def check(payload: Check, request: Request):
    """``{url, vault?, workspace?}`` → ``{origin, scope: "loopback" | "lan"
    | "far", plain, existing, unsure, refused, warning}``
    (``remote_servers.check``): what the desktop app asks before it mints a
    token of that server, ``vault`` naming the vault a renewed token is
    for, ``workspace`` the channel's workspace there (none: a new vault or
    offline copy). 400 for an address that does not do."""
    try:
        return remote_servers.check(payload.url, _account(request), vault=payload.vault, workspace=payload.workspace)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/move")
def move(payload: Move, request: Request):
    """``{from, to, dry_run?, plain_ok?}`` → with ``dry_run`` ``{from, to,
    kind, plain, moved: {vaults, computers, mirrors}}``, no request made
    (``remote_servers.plan``); else the account's links moved
    (``remote_servers.move``): ``{from, to, kind, moved, revoke:
    [{workspace_id, token_id}]}``, ``revoke`` the tokens at ``to`` the
    desktop app revokes there. 400 for an address that does not do, the
    same address, http:// to a host on the internet, or http:// without
    ``plain_ok``; 409 for a token refused there or another workspace
    there; 502 for a new address not reached as that server (not
    answering, redirected, blocked, its certificate not trusted)."""
    user_id = _shell_strict(request, SHELL_ONLY)
    try:
        if payload.dry_run:
            return remote_servers.plan(payload.frm, payload.to, user_id)
        return remote_servers.move(payload.frm, payload.to, user_id, plain_ok=payload.plain_ok)
    except remote_servers.MoveError as e:
        raise HTTPException(status_code=e.status, detail=str(e))


def _pins_kept_here(request: Request) -> str:
    """``_shell_strict``, on a server that keeps pins (``tls_trust.enabled``:
    not a hosted one), else 404."""
    user_id = _shell_strict(request, SHELL_ONLY)
    if not tls_trust.enabled():
        raise HTTPException(status_code=404, detail="This server trusts no certificate by its fingerprint.")
    return user_id


@router.get("/pins")
def list_pins(request: Request):
    """``{pins: [{origin, sha256, subject, issuer, not_before, not_after,
    pinned_at}]}``: the certificates this server's requests accept by their
    fingerprint (``tls_trust.pins``)."""
    _pins_kept_here(request)
    return {"pins": tls_trust.pins()}


@router.put("/pins")
def replace_pins(payload: Pins, request: Request):
    """``{pins: [{origin, sha256, subject, issuer, not_before,
    not_after}]}``, the shell's whole list, in place of this server's →
    ``{pins}`` as ``GET`` answers them; ``pinned_at`` kept for a pin that
    did not change, now for one that did. 400 with why for one that does
    not do (``tls_trust.replace_pins``), the list kept as it was. What a
    certificate failure rests tries again at once
    (``folder_links.certificates_changed``)."""
    _pins_kept_here(request)
    try:
        pins = tls_trust.replace_pins([pin.model_dump() for pin in payload.pins])
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    folder_links.certificates_changed()
    return {"pins": pins}

"""This computer's channels to other Gamma servers (gamma/computers/channel.py):
the desktop app's own server only, as synced vaults (404 elsewhere), a
signed-in account's session only. Making or removing one also takes the
shell's word (routers/folder_links.py ``_from_shell``): the desktop app mints the
``computer`` token on that server with the window's session and hands it
over here, and a page of Gamma never does. Listing is the account's own
channels, never a token."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import gamma_sync, remote_servers
from ..computers import channel
from .folder_links import _account, _from_shell

router = APIRouter(prefix="/api/computer-links", tags=["computer-links"])

SHELL_ONLY = "Only the desktop app itself may show this computer to another server."


class LinkCreate(BaseModel):
    remote_url: str = Field(min_length=1, max_length=400)   # the other server's address
    workspace: str = Field(min_length=1, max_length=64)     # the token's workspace there
    token: str = Field(min_length=1, max_length=400)        # a token of that server of the ``computer`` scope
    token_id: str = Field(default="", max_length=120)       # its id there, for whoever revokes it


def _info(link: dict) -> dict:
    return {**{key: link[key] for key in ("id", "remote_url", "workspace_id", "account", "username", "status",
                                          "created_at")}, "plain": remote_servers.plain(link["remote_url"])}


@router.get("")
def list_links(request: Request):
    """``{links: [{id, remote_url, plain, workspace_id, account, username,
    status: {state, error, at, error_kind?, move_to?, upgrade?, cert?,
    blocked?}, created_at}]}``: the account's channels; ``plain`` whether
    the address is plain http (``remote_servers.plain``, no DNS asked)."""
    return {"links": [_info(link) for link in channel.links(_account(request))]}


@router.post("", status_code=201)
def create_link(payload: LinkCreate, request: Request):
    """``{remote_url, workspace, token, token_id}`` → ``{link, replaced:
    {token_id} | null}``: the channel, checked with one announce and
    started; ``replaced`` the token of the one it took the place of (the
    same server, workspace and account), the desktop app's to revoke there.
    400 for a token that server refuses, one of another workspace or plain
    http to a host on the internet, 409 for a server older than this app or
    one that redirects (``remote_servers.redirected``), 502 for one that
    cannot be reached, or not as that server (a certificate not trusted
    here, no HTTPS, a sign-in or proxy in the way)."""
    user_id = _from_shell(request, SHELL_ONLY)
    try:
        link, replaced = channel.make(payload.remote_url, payload.workspace, payload.token, payload.token_id, user_id)
    except gamma_sync.Denied:
        raise HTTPException(status_code=400, detail="That server refused this computer's token.")
    except channel.Older:
        raise HTTPException(status_code=409, detail="That server is older than this app: it cannot show this computer.")
    except gamma_sync.Redirected as e:
        raise HTTPException(status_code=409, detail=remote_servers.redirected(e, "show this computer from there"))
    except (gamma_sync.CertificateRefused, gamma_sync.NotHttps, gamma_sync.Blocked) as e:
        raise HTTPException(status_code=502, detail=str(e))
    except gamma_sync.SyncError as e:
        raise HTTPException(status_code=502, detail=f"That server could not be reached: {e}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"link": _info(link), "replaced": replaced}


@router.delete("/{link_id}")
def delete_link(link_id: str, request: Request):
    """The channel stops and is forgotten, the agents of the work folders it
    announced turned off (``channel.delete``): ``{ok: true, token_id}``, the
    token's id on that server (the desktop app revokes it there). 404 for
    another account's or none."""
    token_id = channel.delete(link_id, _from_shell(request, SHELL_ONLY))
    if token_id is None:
        raise HTTPException(status_code=404, detail="no such link")
    return {"ok": True, "token_id": token_id}

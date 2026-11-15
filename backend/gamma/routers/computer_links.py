"""This computer's channels to other Gamma servers (gamma/computers/channel.py):
the desktop app's own server only, as synced vaults (404 elsewhere), a
signed-in account's session only. Making or removing one also takes the
shell's word (routers/shell.py ``from_shell``), and letting its server
answer what this computer's agents ask takes it even on a server started
with no secret (``shell_strict``): the desktop app mints the ``computer``
token on that server with the window's session and hands it over here, and
a page of Gamma never does. Listing is the account's own channels, never a
token; Settings → AI → Agents reads it in the desktop app."""

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from .. import gamma_sync, remote_servers
from ..computers import channel
from .shell import account, from_shell, shell_strict

router = APIRouter(prefix="/api/computer-links", tags=["computer-links"])

SHELL_ONLY = "Only the desktop app itself may show this computer to another server."
ASKS_SHELL_ONLY = "Only the desktop app itself may let another server answer what this computer's agents ask."
NO_LINK = "no such link"


class LinkCreate(BaseModel):
    remote_url: str = Field(min_length=1, max_length=400)   # the other server's address
    workspace: str = Field(min_length=1, max_length=64)     # the token's workspace there
    token: str = Field(min_length=1, max_length=400)        # a token of that server of the ``computer`` scope
    token_id: str = Field(default="", max_length=120)       # its id there, for whoever revokes it


class LinkChange(BaseModel):
    asks: bool   # whether that server may answer what this computer's agents ask


def _info(link: dict) -> dict:
    return {**{key: link[key] for key in ("id", "remote_url", "workspace_id", "workspace_name", "account", "username",
                                          "status", "asks", "created_at")},
            "plain": remote_servers.plain(link["remote_url"])}


@router.get("")
def list_links(request: Request):
    """``{links: [{id, remote_url, plain, workspace_id, workspace_name,
    account, username, status: {state, error, at, error_kind?, move_to?,
    upgrade?, cert?, blocked?}, asks, created_at}]}``: the account's
    channels; ``plain`` whether the address is plain http
    (``remote_servers.plain``, no DNS asked), ``asks`` whether that server
    may answer what this computer's agents ask."""
    return {"links": [_info(link) for link in channel.links(account(request))]}


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
    user_id = from_shell(request, SHELL_ONLY)
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


@router.patch("/{link_id}")
def change_link(link_id: str, payload: LinkChange, request: Request):
    """``{asks}`` → the link: whether its server may answer what this
    computer's agents ask (decision 7 of
    docs/research/agents-as-participants-plan.md), announced there at the
    channel's next turn. Turning it on decides what this computer trusts
    and takes the shell's word, whose native question names the server
    (``shell_strict``: a server started with no secret refuses it); off
    takes the session alone. 404 for another account's or none."""
    user_id = shell_strict(request, ASKS_SHELL_ONLY) if payload.asks else account(request)
    link = channel.set_asks(link_id, user_id, payload.asks)
    if link is None:
        raise HTTPException(status_code=404, detail=NO_LINK)
    return _info(link)


@router.delete("/{link_id}")
def delete_link(link_id: str, request: Request):
    """The channel stops and is forgotten, the turns of that server its
    vaults run here stopped (``channel.delete``): ``{ok: true, token_id}``,
    the token's id on that server (the desktop app revokes it there, which
    fails there what its runs held). 404 for another account's or none."""
    token_id = channel.delete(link_id, from_shell(request, SHELL_ONLY))
    if token_id is None:
        raise HTTPException(status_code=404, detail=NO_LINK)
    return {"ok": True, "token_id": token_id}

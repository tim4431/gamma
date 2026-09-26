"""The portal pages (HTML). The data behind them comes from ``/api``."""

from contextlib import closing
from urllib.parse import urlencode

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import accounts, connect, db, identities, oidc, pages, providers, servers, sessions
from .external import sign_in_page

router = APIRouter()
NO_STORE = pages.NO_STORE


def _app_page(request: Request, render):
    """An app page: ``render(account, data)`` gives its HTML, or None for a
    404; ``data`` holds ``devices`` (live grants), ``servers`` (them merged
    with the linked servers, ``servers.merge``), ``identities``,
    ``browsers`` and ``connected`` (the servers this account connected).
    Signed out goes to the login page and comes back here."""
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        if not account:
            return RedirectResponse("/login?" + urlencode({"next": request.url.path}), status_code=302)
        devices = oidc.devices(conn, account["id"])
        data = {"devices": devices,
                "servers": servers.merge(devices, servers.of_account(conn, account["id"], grants=True)),
                "identities": identities.of_account(conn, account["id"]),
                "browsers": sessions.of_account(conn, account["id"], request),
                "connected": connect.of_account(conn, account["id"])}
        conn.commit()
    html = render(accounts.public(account), data)
    if html is None:
        return HTMLResponse(pages.error_page("Not found", "There is no such page."), status_code=404)
    return HTMLResponse(html, headers=NO_STORE)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, mail: str = ""):
    """``?mail=failed``: registration could not send the confirmation mail."""
    return _app_page(request, lambda account, d: pages.overview_page(
        account, d["devices"], d["servers"], mail_failed=mail == "failed"))


@router.get("/devices", response_class=HTMLResponse)
def devices(request: Request):
    return _app_page(request, lambda account, d: pages.devices_page(account, d["servers"], d["browsers"], d["connected"]))


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return _app_page(request, lambda account, d: pages.settings_page(account, d["identities"], providers.enabled()))


@router.get("/admin", response_class=HTMLResponse)
def admin(request: Request):
    return _app_page(request, lambda account, _: pages.admin_page(account) if account["is_admin"] else None)


@router.get("/login", response_class=HTMLResponse)
def login(request: Request, next: str = "/"):
    with closing(db.connect()) as conn:
        if sessions.resolve(conn, request):
            return RedirectResponse(identities.safe_next(next), status_code=302)
    return sign_in_page(request, pages.login_page, next_url=next)


@router.get("/register", response_class=HTMLResponse)
def register(request: Request):
    return sign_in_page(request, pages.register_page)


@router.get("/verify", response_class=HTMLResponse)
def verify(token: str = ""):
    return HTMLResponse(pages.verify_page(token), headers=NO_STORE)


@router.get("/email/confirm", response_class=HTMLResponse)
def email_confirm(token: str = ""):
    return HTMLResponse(pages.email_confirm_page(token), headers=NO_STORE)


@router.get("/reset", response_class=HTMLResponse)
def reset():
    return HTMLResponse(pages.reset_page(), headers=NO_STORE)


@router.get("/reset/confirm", response_class=HTMLResponse)
def reset_confirm(token: str = ""):
    return HTMLResponse(pages.reset_confirm_page(token), headers=NO_STORE)


@router.get("/api/health")
def health():
    return {"ok": True}

"""The portal pages (HTML). The data behind them comes from ``/api``."""

from contextlib import closing
from urllib.parse import urlencode

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from .. import (accounts, billing, connect, db, entrance, identities, oidc, pages, pages_billing, providers, servers,
                sessions)
from .external import sign_in_page

router = APIRouter()
NO_STORE = pages.NO_STORE


def _app_page(request: Request, render):
    """An app page: ``render(account, data)`` gives its HTML, or None for a
    404; ``data`` holds ``devices`` (live grants), ``servers`` (them merged
    with the linked servers, ``servers.merge``), ``identities``,
    ``browsers``, ``connected`` (the servers this account connected),
    ``billing`` (``billing.summary``) and ``places``
    (``entrance.destinations``). Signed out goes to the login page
    and comes back here."""
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        if not account:
            return RedirectResponse("/login?" + urlencode({"next": request.url.path}), status_code=302)
        devices = oidc.devices(conn, account["id"])
        data = {"devices": devices,
                "servers": servers.merge(devices, servers.of_account(conn, account["id"], grants=True)),
                "identities": identities.of_account(conn, account["id"]),
                "browsers": sessions.of_account(conn, account["id"], request),
                "connected": connect.of_account(conn, account["id"]),
                "billing": billing.summary(conn, account["id"]),
                "places": entrance.destinations(conn, account["id"])}
        public = accounts.public(account, conn)
        conn.commit()
    html = render(public, data)
    if html is None:
        return HTMLResponse(pages.error_page("Not found", "There is no such page."), status_code=404)
    return HTMLResponse(html, headers=NO_STORE)


@router.get("/", response_class=HTMLResponse)
def home(request: Request, mail: str = ""):
    """``?mail=failed``: registration could not send the confirmation mail."""
    return _app_page(request, lambda account, d: pages.overview_page(
        account, d["devices"], d["servers"], d["billing"], mail_failed=mail == "failed", can_open=bool(d["places"])))


@router.get("/open")
def open_gamma(request: Request):
    """The portal's Open Gamma: straight to the account's Gamma when it has
    one place to go (``entrance.destinations``), through that server's
    cloud sign-in, so the person lands signed in; a card to choose from
    when it has several. Signed out goes to the login page and comes back;
    an unconfirmed address gets the Overview, which says so, and an account
    with nowhere to go (a free plan) the Plan page."""
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        if not account:
            return RedirectResponse("/login?" + urlencode({"next": "/open"}), status_code=302)
        places = entrance.destinations(conn, account["id"]) if account["email_verified_at"] else []
        public = accounts.public(account, conn)
        conn.commit()
    if not places:
        return RedirectResponse("/plan" if public["email_verified"] else "/", status_code=302)
    if len(places) == 1:
        return RedirectResponse(entrance.start_url(places[0]["url"]), status_code=302, headers=NO_STORE)
    return HTMLResponse(pages.where_page(public, places), headers=NO_STORE)


@router.get("/devices", response_class=HTMLResponse)
def devices(request: Request):
    return _app_page(request, lambda account, d: pages.devices_page(account, d["servers"], d["browsers"], d["connected"]))


@router.get("/settings", response_class=HTMLResponse)
def settings(request: Request):
    return _app_page(request, lambda account, d: pages.settings_page(account, d["identities"], providers.enabled()))


@router.get("/plan", response_class=HTMLResponse)
def plan(request: Request, checkout: str = "", returned: str = Query("", alias="billing")):
    """The Plan page (``pages_billing``). ``?checkout=success`` is Stripe
    sending the browser back from Checkout, ``?billing=<flow>`` from a
    finished portal flow. Unlike ``_app_page`` it needs only the account
    and ``billing.summary``; it renders the stored copy and never calls
    Stripe (its script's ``GET /api/billing/me`` refreshes a stale row and
    ``GET /api/billing/details`` reads the invoices)."""
    with closing(db.connect()) as conn:
        account = sessions.resolve(conn, request)
        if not account:
            return RedirectResponse("/login?" + urlencode({"next": request.url.path}), status_code=302)
        conn.commit()  # the session touch; nothing below writes
        html = pages_billing.plan_page(accounts.public(account, conn), billing.summary(conn, account["id"]),
                                       checkout=checkout, returned=returned)
    return HTMLResponse(html, headers=NO_STORE)


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
def register(request: Request, invite: str = ""):
    """``?invite=CODE``: the Admin page's copied link fills the invite field."""
    return sign_in_page(request, lambda social: pages.register_page(social, invite[:64]))


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

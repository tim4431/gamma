"""A sign-in a native app started (pages.in_app): the Gamma server's
authorize URL carries ``gamma_app=1``, ``/authorize`` keeps it in the
``gc_app`` cookie, and the auth pages after it drop their links to where
a plan is bought — no top bar to gammapdf.com and the Overview, a new
account sent back to the sign-in, no Devices, plans or download links —
all but the terms' and the privacy policy's, which are still open."""

import re
from urllib.parse import urlencode

from conftest import invite, last_link, register, verify
from test_entrance import APP_CALLBACK, cloud, signed_up  # noqa: F401  (cloud is a fixture)
from test_oidc import authorize_params, pkce, request_id_from

from gammacloud import config, mail, pages

SITE_LINK = f"href='{pages.SITE}'"
HOME_LINK = "href='/'"
PRIVACY = f"{pages.SITE}/privacy/"
TERMS = f"{pages.SITE}/terms/"


def site_links(page):
    """Every gammapdf.com address a page links to."""
    return set(re.findall(r"href=['\"]?(https://gammapdf\.com[^'\" >]*)", page))


def ask(client, app=True, **kw):
    _, challenge = pkce()
    params = authorize_params(challenge, **kw)
    if app:
        params[pages.APP_PARAM] = "1"
    return client.get("/authorize", params=params, follow_redirects=False)


def test_a_browser_of_its_own_keeps_the_top_bar(client):
    r = ask(client, app=False)
    assert r.status_code == 200 and "class=authtop" in r.text
    assert SITE_LINK in r.text and HOME_LINK in r.text
    assert pages.APP_COOKIE not in r.cookies
    assert "<a href='/register'>Create an account</a>" in r.text      # a new account lands on the Overview
    for path in ("/login", "/register", "/reset"):
        page = client.get(path).text
        assert SITE_LINK in page and HOME_LINK in page, path


def test_the_apps_sign_in_and_sign_up_pages_drop_their_portal_links(client):
    r = ask(client)
    assert r.status_code == 200 and "Sign in to Gamma desktop app" in r.text
    assert r.cookies.get(pages.APP_COOKIE) == "1"
    assert f"Max-Age={config.APP_BROWSER_TTL}" in r.headers["set-cookie"]
    # the brand stays, without its links
    assert "class=authtop" in r.text and SITE_LINK not in r.text and HOME_LINK not in r.text
    rid = request_id_from(r.text)
    back = "/authorize/resume?" + urlencode({"request_id": rid})
    register_link = "/register?" + urlencode({"next": back})
    assert f"href='{register_link.replace('&', '&amp;')}'>Create an account</a>" in r.text
    # the pages after it, by the cookie
    for path in ("/login", register_link, "/reset", "/authorize/resume?request_id=gone"):
        page = client.get(path).text
        assert "class=authtop" in page and SITE_LINK not in page and HOME_LINK not in page, path
    # the gammapdf.com links left: the terms and the privacy policy, whose site header links Pricing (still open)
    for path in ("/authorize/resume?" + urlencode({"request_id": rid}), "/login", register_link, "/reset"):
        assert site_links(client.get(path).text) <= {PRIVACY, TERMS}, path
    assert "This sign-in request expired" in client.get("/authorize/resume?request_id=gone").text
    # a new account comes back to the sign-in, whose verify notice has no way to the Overview
    r = client.post("/api/register", json={"email": "alice@example.org", "username": "alice",
                                           "password": "correct horse battery", "invite": invite(), "next": back})
    assert r.status_code == 201 and r.json()["redirect"] == back
    assert "href='/plan'" in client.get("/").text, "the account's own pages are not the sign-in's"
    page = client.get(back).text
    assert "confirmed yet" in page and HOME_LINK not in page and "Your account" not in page
    # its resend sends the new link back to the sign-in too, whose page has no Overview link
    assert f"data-next='{back.replace('&', '&amp;')}'" in page and "r.dataset.next" in page
    assert client.post("/api/verify/resend", json={"next": back}).status_code == 200
    token = last_link("/verify")
    token_page = client.get("/verify", params={"token": token}).text
    assert "Overview" not in token_page and HOME_LINK not in token_page
    assert client.post("/api/verify", json={"token": token}).json()["next"] == back
    # confirmed, the card names Devices without linking it
    page = client.get(back).text
    assert "Continue" in page and "href=/devices" not in page and "Devices page" in page


def test_a_sign_in_no_app_started_clears_the_mark(client):
    assert ask(client).cookies.get(pages.APP_COOKIE) == "1"
    r = ask(client, app=False)
    assert pages.APP_COOKIE not in client.cookies and SITE_LINK in r.text
    assert SITE_LINK in client.get("/login").text
    # a cookie alone does not mark /authorize: only the Gamma server's parameter does
    client.cookies.set(pages.APP_COOKIE, "1")
    assert SITE_LINK in ask(client, app=False).text


def test_a_free_account_in_the_app_is_not_shown_the_plans(client, cloud):  # noqa: F811
    signed_up(client, plan="free")
    r = ask(client, client_id=cloud, redirect=APP_CALLBACK, scope="openid email profile offline_access prefs")
    assert r.status_code == 200 and "Your plan has no online library" in r.text
    assert "href='/plan'" not in r.text and f"{pages.SITE}/download" not in r.text
    r = ask(client, app=False, client_id=cloud, redirect=APP_CALLBACK, scope="openid email profile offline_access prefs")
    assert "href='/plan'>See plans" in r.text


def test_the_consent_card_links_devices_outside_the_app(client):
    register(client)
    verify(client)
    assert "<a href=/devices>Devices</a>" in ask(client, app=False).text
    page = ask(client).text
    assert "Continue" in page and "href=/devices" not in page


def test_a_new_account_whose_mail_failed_still_comes_back_to_the_sign_in(client, monkeypatch):
    def boom(*a, **k):
        raise mail.MailError("domain not verified")
    monkeypatch.setattr(mail, "send", boom)
    back = "/authorize/resume?" + urlencode({"request_id": request_id_from(ask(client).text)})
    body = {"email": "alice@example.org", "username": "alice", "password": "correct horse battery",
            "invite": invite(), "next": back}
    r = client.post("/api/register", json=body)
    assert r.status_code == 201 and r.json()["mailed"] is False and r.json()["redirect"] == back
    page = client.get(back).text
    assert "confirmed yet" in page and "data-resend" in page and HOME_LINK not in page
    # outside the app a failed mail still lands on the Overview's notice
    client.cookies.clear()
    r = client.post("/api/register", json={**body, "email": "bob@example.org", "username": "bob", "invite": invite()})
    assert r.json()["redirect"] == "/?mail=failed"

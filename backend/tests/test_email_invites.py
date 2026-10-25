"""Invitations by e-mail address (gamma/email_invites.py): an admin invites
an address to this server, a shared workspace's owner to the workspace, a
share's managers to the page or folder. The account server mails it
(cloud_auth.send_invitation, on the inviter's own Gamma Cloud grant) and
only the sign-in through its link (the link id, a secret apart from the id
the lists show) is admitted by it and claims it: a new account, a
membership, a share entry, an Inbox item. The address itself is matched
against nothing here. A row that goes (withdrawn, claimed, gone with its
target) has its link dropped at the account server. Every call runs
through the fake account server of tests/test_cloud_auth.py, which keeps
the bodies it is asked to mail and to cancel, against a server signing in
with a client of its own (a mailed link can reach it)."""

import json
import logging
import socket
import urllib.error
from types import SimpleNamespace

import pytest
from conftest import account_of, drop_user, fresh_client, guest_name, login, make_folder, make_page, make_user
from test_cloud_auth import RETURN, app_start, browser, callback, claim, cloud, returned, start  # noqa: F401

from gamma import cloud_auth, email_invites, hosted, mcp_oauth, workspaces
from gamma.auth import share_access, share_lookup
from gamma.db import connect_users_db, delete_shares
from gamma.integrations import create_token
from gamma.server_settings import _set_raw

CLIENT = "gc_ei"   # this server's own client
DESKTOP = "This server signs in through the desktop app's client, so a mailed link could not reach it."
ADMIN_ONLY = ("While this server refuses new Gamma Cloud accounts, only an admin invites by e-mail, "
              "from Settings → Users.")
LONG_AGO = "2000-01-01T00:00:00.000000Z"
LATER = "2999-01-01T00:00:00.000000Z"
HERE = "http://testserver"
LAB = "https://gamma.lab.example"  # another origin requests come in on (a proxy), no public URL confirmed
SEND = cloud_auth.send_invitation  # the real one, which _meanwhile wraps


@pytest.fixture(scope="module")
def owner():
    make_user("ei_owner", "ei-owner-pw1")
    return login("ei_owner", "ei-owner-pw1")


@pytest.fixture(scope="module")
def boss():
    make_user("ei_boss", "ei-boss-pw1", is_admin=1)
    yield login("ei_boss", "ei-boss-pw1")
    drop_user("ei_boss")  # test_admin_users knows every admin there is


@pytest.fixture
def mail(cloud, owner, boss, monkeypatch):  # noqa: F811
    """The fake account server; this server with a client of its own under
    the ``invited`` policy, and the address it remembers (where a link
    whose row recorded no address is dropped); the owner's and the
    admin's accounts linked to Gamma Cloud, whose grants mail the
    invitations; the calls that withdraw links at the account server made
    at once rather than on a thread."""
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", CLIENT)
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_SECRET", "ei-secret")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "invited")
    monkeypatch.setattr(email_invites, "threading", SimpleNamespace(Thread=_Inline))
    _set_raw("cloud_server_url", HERE)  # the cloud fixture clears it
    _link(cloud, owner, "ei-owner")
    _link(cloud, boss, "ei-boss")
    yield cloud
    with connect_users_db() as conn:
        conn.execute("DELETE FROM email_invites")
        conn.commit()


class _Inline:
    """A thread whose target runs at once, when it is started."""

    def __init__(self, target, args=(), **_kwargs):
        self._run = lambda: target(*args)

    def start(self):
        self._run()


def _at(origin, username="ei_owner", password="ei-owner-pw1"):
    """A client of its own signed in as ``username`` on requests to ``origin``."""
    c = fresh_client(base_url=origin)
    r = c.post("/api/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return c


def _person(fake, name, email=None):
    """Who the fake account server signs in next: ``name``, with ``email``."""
    fake.person.update({"sub": f"sub-{name}", "preferred_username": name, "email": email or f"{name}@example.org"})
    fake.refresh = f"rt-{name}"


def _link(fake, client, name):
    """The signed-in ``client`` links the Gamma Cloud account ``name``."""
    _person(fake, name)
    assert callback(client, start(client, link="1")).headers["location"] == "/"


def _sign_in(fake, name, email=None, **params):
    """``name``'s Gamma Cloud sign-in (with the address ``email``) in a
    browser of its own, ``params`` on its start (``next``, ``invite``):
    (the browser, where the callback sent it)."""
    _person(fake, name, email)
    c = browser()
    r = callback(c, start(c, **params))
    assert r.status_code == 302, r.text
    return c, r.headers["location"]


def _lab(boss, name, owner="ei_owner"):
    r = boss.post("/api/workspaces", json={"name": name, "kind": "shared", "owner": owner})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _shared_page(client, title, ws=None):
    """A new page shared with its invited people only: (page id, token)."""
    headers = {"X-Gamma-Workspace": ws} if ws else {}
    r = client.post("/api/blocks", json={"parent_id": "root", "content": title}, headers=headers)
    assert r.status_code == 200, r.text
    page = r.json()["id"]
    r = client.post(f"/api/share/{page}", json={"audience": "list"}, headers=headers)
    assert r.status_code == 200, r.text
    return page, r.json()["token"]


def _items(user_id, kind):
    """The account's Inbox items of ``kind``: ``[(actor, workspace, share token)]``."""
    with connect_users_db() as conn:
        return conn.execute("SELECT actor, workspace_id, share_token FROM inbox_items WHERE kind = ? AND user_id = ?",
                            (kind, user_id)).fetchall()


def _rows():
    with connect_users_db() as conn:
        return conn.execute("SELECT kind, email_norm FROM email_invites ORDER BY kind, email_norm").fetchall()


def _mailed(fake):
    """The link id the last mail carried: what the account server was told
    as ``invite_id``, and what its continue link brings back to ``start``."""
    return fake.invitations[-1]["invite_id"]


def _link_of(invite_id):
    """The link id of the invitation ``invite_id`` (its public handle)."""
    with connect_users_db() as conn:
        return conn.execute("SELECT link_id FROM email_invites WHERE id = ?", (invite_id,)).fetchone()[0]


def _cancel(link, by, at=HERE):
    """The body the account server gets to drop the link ``link`` it keeps
    under this server's address ``at``, asked on the grant of the cloud
    account ``by``."""
    return {"server_url": at, "invite_id": link, "by": f"sub-{by}"}


def _ungrant(username):
    """This server no longer holds the account's Gamma Cloud grant (unlinked
    elsewhere, revoked)."""
    with connect_users_db() as conn:
        conn.execute("UPDATE identities SET refresh_token = '' WHERE user_id = ?", (account_of(username),))
        conn.commit()


def _waiting(kind, email, invite_id, link_id, ws="", token=""):
    """An invitation another request wrote, straight into the table."""
    with connect_users_db() as conn:
        conn.execute("INSERT INTO email_invites (id, link_id, email, email_norm, kind, workspace_id, share_token, "
                     "created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (invite_id, link_id, email, email.lower(), kind, ws, token, LONG_AGO, LATER))
        conn.commit()


def _meanwhile(monkeypatch, then):
    """While the account server mails the next invitation, ``then()`` runs
    here, as another request would in the meantime."""
    def sending(*args, **kwargs):
        monkeypatch.setattr(cloud_auth, "send_invitation", SEND)  # this one only
        answer = SEND(*args, **kwargs)
        then()
        return answer

    monkeypatch.setattr(cloud_auth, "send_invitation", sending)


def _silent(monkeypatch, meanwhile=lambda: None):
    """The account server answers no send in time (``meanwhile()`` runs
    here before the timeout); the link ids of the sends it got, and the
    timeouts they were given."""
    fake = cloud_auth._http
    asked = []

    def slow(url, *args, **kwargs):
        if url.endswith("/api/invitations"):
            asked.append((json.loads(kwargs["data"])["invite_id"], kwargs["timeout"]))
            meanwhile()
            raise cloud_auth.CloudAuthError("cannot reach the account server: timed out") from TimeoutError("timed out")
        return fake(url, *args, **kwargs)

    monkeypatch.setattr(cloud_auth, "_http", slow)
    return asked


def test_where_invitations_by_email_can_go_out(cloud, monkeypatch):  # noqa: F811
    c = browser()

    def flags():
        return c.get("/api/server-config").json()["cloud"]["email_invites"]

    # the desktop app's public client (the fixture's): a mailed link could not reach this server
    assert email_invites.available() == {"server": False, "members": False, "reason": DESKTOP}
    assert flags() == {"server": False, "members": False}
    # a client of its own, under a policy that refuses newcomers: only an admin invites
    monkeypatch.setenv("GAMMA_CLOUD_CLIENT_ID", CLIENT)
    for policy in ("refuse", "claim"):
        monkeypatch.setenv("GAMMA_CLOUD_POLICY", policy)
        assert email_invites.available() == {"server": True, "members": False, "reason": ADMIN_ONLY}
    assert flags() == {"server": True, "members": False}
    for policy in ("provision", "invited"):
        monkeypatch.setenv("GAMMA_CLOUD_POLICY", policy)
        assert email_invites.available() == {"server": True, "members": True, "reason": ""}
    assert flags() == {"server": True, "members": True}
    # a server at a public address still on the desktop client: not connected yet
    monkeypatch.delenv("GAMMA_CLOUD_CLIENT_ID")
    _set_raw("public_url", "https://gamma.example.org")
    try:
        assert email_invites.available()["reason"] == "This server is not connected to Gamma Cloud yet."
    finally:
        _set_raw("public_url", "")
    # cloud sign-in off
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    monkeypatch.setattr(cloud_auth, "_get_raw", lambda key, default="": "")
    assert email_invites.available() == {"server": False, "members": False,
                                         "reason": "Gamma Cloud sign-in is off on this server."}
    assert flags() == {"server": False, "members": False}


def test_each_kind_tells_the_account_server_what_and_where(mail, owner, boss):
    ws = _lab(boss, "EI lab")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": " Ann@Example.org ", "role": "viewer"})
    assert r.status_code == 200, r.text
    entry = r.json()["invited"]["pending"]
    assert (entry["email"], entry["role"], entry["invited_by"]) == ("Ann@Example.org", "viewer", "ei_owner")
    assert entry["mailed_at"] and entry["expires_at"] > entry["mailed_at"] and len(entry["id"]) >= 16
    # the account server is told the link id, a secret of its own that no entry shows
    link = _link_of(entry["id"])
    assert len(link) >= 32 and link != entry["id"] and link not in entry.values()
    assert mail.invitations == [{"email": "Ann@Example.org", "server_url": HERE, "server_name": "testserver",
                                 "kind": "workspace", "what": "EI lab", "invite_id": link, "next": f"/?ws={ws}",
                                 "by": "sub-ei-owner"}]
    # the members list shows it after the members, by its address
    assert r.json()["members"][-1] == {"email": "Ann@Example.org", "id": entry["id"], "role": "viewer",
                                       "added_by": "ei_owner", "added_at": entry["created_at"],
                                       "expires_at": entry["expires_at"], "pending": True}
    assert owner.get(f"/api/workspaces/{ws}/invites").json() == {"invites": [], "email_invites": [entry]}

    # a page share: the page's title (its first line), the share's link
    page, token = _shared_page(owner, "EI paper\nwith a second line")
    r = owner.post(f"/api/share-settings/{page}/invites", json={"email": "bo@example.org", "role": "edit"})
    assert r.status_code == 200, r.text
    entry = r.json()["invited"]["pending"]
    assert r.json()["pending"] == [entry] and entry["role"] == "edit"
    sent = mail.invitations[-1]
    assert (sent["kind"], sent["what"], sent["next"], sent["invite_id"]) == ("page", "EI paper", f"/?share={token}",
                                                                              _link_of(entry["id"]))
    # a folder share: the folder's name; view unless said otherwise
    folder = make_folder(owner, "EI folder")
    token = owner.post(f"/api/share/folder/{folder}", json={"audience": "list"}).json()["token"]
    r = owner.post(f"/api/share-settings/folder/{folder}/invites", json={"email": "cy@example.org"})
    assert r.status_code == 200, r.text
    assert r.json()["invited"]["pending"]["role"] == "view"
    sent = mail.invitations[-1]
    assert (sent["kind"], sent["what"], sent["next"]) == ("folder", "EI folder", f"/?share={token}")

    # this server, by an admin
    r = boss.post("/api/admin/invitations", json={"email": "dee@example.org"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["available"] is True and body["reason"] == ""
    assert body["invitations"] == [body["invited"]["pending"]] and body["invited"]["pending"]["role"] == ""
    sent = mail.invitations[-1]
    assert (sent["kind"], sent["what"], sent["next"], sent["by"]) == ("server", "", "/", "sub-ei-boss")
    assert boss.get("/api/admin/invitations").json()["invitations"] == body["invitations"]
    assert boss.post("/api/admin/invitations", json={"email": "not an address"}).status_code == 400


def test_an_address_linked_here_is_mailed_and_joins_only_through_the_link(mail, owner, boss):
    # an account here whose Gamma Cloud identity has the address is not looked up: the invitation is mailed
    # and waits, so the inviter learns nothing of whose the address is
    make_user("ei_kim", "ei-kim-pw1")
    kim = account_of("ei_kim")
    with connect_users_db() as conn:
        cloud_auth.link(conn, kim, {"sub": "sub-ei-kim", "preferred_username": "ei-kim", "email": "Kim@Example.org"},
                        "rt-ei-kim")
        conn.commit()
    ws = _lab(boss, "EI linked lab")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "kim@example.org", "role": "editor"})
    assert r.status_code == 200, r.text
    assert list(r.json()["invited"]) == ["pending"] and "ei_kim" not in json.dumps(r.json())
    ws_link = _mailed(mail)
    assert mail.invitations[-1]["email"] == "kim@example.org"
    page, token = _shared_page(owner, "EI linked paper")
    assert owner.put(f"/api/share-settings/{page}", json={"users": [{"name": "ei_kim", "role": "edit"}]}
                     ).status_code == 200
    r = owner.post(f"/api/share-settings/{page}/invites", json={"email": "KIM@example.org", "role": "view"})
    assert r.status_code == 200, r.text
    assert list(r.json()["invited"]) == ["pending"]
    share_link = _mailed(mail)
    assert len(mail.invitations) == 2 and workspaces.role_of(ws, kim) is None and _items(kim, "invited") == []
    told = len(_items(kim, "shared"))
    # their own address too: it is mailed like any other
    r = owner.post(f"/api/share-settings/{page}/invites", json={"email": "ei-owner@example.org"})
    assert r.status_code == 200 and list(r.json()["invited"]) == ["pending"]
    # an admin's invitation to this server is refused for them: they sign in here already
    r = boss.post("/api/admin/invitations", json={"email": "kim@example.org"})
    assert r.status_code == 400 and r.json()["detail"] == "kim@example.org already has an account here: ei_kim."
    assert len(mail.invitations) == 3

    # signing in with that address but without a link claims nothing
    c, location = _sign_in(mail, "ei-kim", email="Kim@Example.org", next="/")
    assert location == "/" and c.get("/api/session").json()["user"] == "ei_kim"
    assert workspaces.role_of(ws, kim) is None
    assert _rows() == [("share", "ei-owner@example.org"), ("share", "kim@example.org"), ("workspace", "kim@example.org")]
    # through the link they join, told in their Inbox, and the used link goes at the account server
    _, location = _sign_in(mail, "ei-kim", email="Kim@Example.org", next=f"/?ws={ws}", invite=ws_link)
    assert location == f"/?ws={ws}" and workspaces.role_of(ws, kim) == "editor"
    assert _items(kim, "invited") == [(account_of("ei_owner"), ws, "")]
    assert mail.cancels == [_cancel(ws_link, "ei-kim")]
    # the share's link keeps the entry they have: an invitation never lowers a role, and tells no one
    _sign_in(mail, "ei-kim", email="Kim@Example.org", next=f"/?share={token}", invite=share_link)
    assert share_access(share_lookup(token), kim, False) == ("edit", "")
    assert len(_items(kim, "shared")) == told
    assert _rows() == [("share", "ei-owner@example.org")]


def test_the_address_alone_admits_and_claims_nothing(mail, owner, monkeypatch):
    page, token = _shared_page(owner, "EI address paper")
    assert owner.post(f"/api/share-settings/{page}/invites", json={"email": "lu@example.org",
                                                                   "role": "edit"}).status_code == 200
    link = _mailed(mail)
    # under invited, a newcomer with the invited address and no link is not let in: they ask to join
    _, location = _sign_in(mail, "ei-lu", email="LU@example.org", next=f"/?share={token}")
    assert "cloud_join=" in location and not account_of("ei-lu")
    # where newcomers are taken anyway, the account is made, and the invitation still waits for its link
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    c, location = _sign_in(mail, "ei-lu", email="LU@example.org", next=f"/?share={token}")
    lu = account_of("ei-lu")
    assert lu and share_access(share_lookup(token), lu, False)[0] is None
    assert _items(lu, "shared") == [] and _rows() == [("share", "lu@example.org")] and mail.cancels == []
    # the link then lets them in
    _sign_in(mail, "ei-lu", email="LU@example.org", next=f"/?share={token}", invite=link)
    assert share_access(share_lookup(token), lu, False) == ("edit", "")
    assert c.get(f"/api/share/{token}").json()["can_edit"] is True
    assert _items(lu, "shared") == [(account_of("ei_owner"), workspaces.default_workspace(account_of("ei_owner")),
                                     token)]
    assert owner.get(f"/api/share-settings/{page}").json()["pending"] == []


def test_inviting_again_keeps_the_id_and_changes_the_role(mail, owner, boss):
    ws = _lab(boss, "EI again lab")
    url = f"/api/workspaces/{ws}/invites"
    first = owner.post(url, json={"email": "eve@example.org", "role": "viewer"}).json()["invited"]["pending"]
    again = owner.post(url, json={"email": "EVE@example.org", "role": "editor"}).json()["invited"]["pending"]
    assert again["id"] == first["id"] and again["role"] == "editor"
    assert [b["invite_id"] for b in mail.invitations] == [_link_of(first["id"])] * 2  # mailed again, the same link
    assert [e["role"] for e in email_invites.listing("workspace", ws=ws)] == ["editor"]
    # the role alone changes with no mail
    r = owner.post(url, json={"invitation": first["id"], "role": "viewer"})
    assert r.status_code == 200, r.text
    assert [m["role"] for m in r.json()["members"] if m.get("email")] == ["viewer"]
    assert len(mail.invitations) == 2
    assert owner.post(url, json={"invitation": "no-such-invitation-x", "role": "viewer"}).status_code == 404
    assert owner.post(url, json={"invitation": first["id"], "role": "owner"}).status_code == 400  # never ownership
    assert owner.post(url, json={"email": "eve@example.org", "username": "ei-eve"}).status_code == 400  # one at a time
    assert owner.post(url, json={"role": "viewer"}).status_code == 400
    # a share's the same way
    page, _ = _shared_page(owner, "EI again paper")
    entry = owner.post(f"/api/share-settings/{page}/invites", json={"email": "eve@example.org"}).json()
    entry = entry["invited"]["pending"]
    r = owner.post(f"/api/share-settings/{page}/invites", json={"invitation": entry["id"], "role": "edit"})
    assert r.status_code == 200 and [p["role"] for p in r.json()["pending"]] == ["edit"]
    r = owner.post(f"/api/share-settings/{page}/invites", json={"invitation": entry["id"], "role": "editor"})
    assert r.status_code == 400


def test_resend_and_withdraw(mail, owner, boss):
    page, _ = _shared_page(owner, "EI resend paper")
    entry = owner.post(f"/api/share-settings/{page}/invites", json={"email": "fay@example.org"}).json()
    entry = entry["invited"]["pending"]
    link = _link_of(entry["id"])
    r = owner.post(f"/api/share-settings/{page}/email-invites/{entry['id']}/resend")
    assert r.status_code == 200, r.text
    resent = r.json()["pending"][0]
    assert resent["id"] == entry["id"] and resent["mailed_at"] > entry["mailed_at"]
    assert resent["expires_at"] > entry["expires_at"]  # its 14 days start over
    assert [b["invite_id"] for b in mail.invitations] == [link] * 2
    r = owner.delete(f"/api/share-settings/{page}/email-invites/{entry['id']}")
    assert r.status_code == 200 and r.json()["pending"] == []
    assert mail.cancels == [_cancel(link, "ei-owner")]  # its link goes too
    assert owner.delete(f"/api/share-settings/{page}/email-invites/{entry['id']}").status_code == 404
    assert owner.post(f"/api/share-settings/{page}/email-invites/{entry['id']}/resend").status_code == 404

    # a folder share's, a workspace's and the admin's the same way
    folder = make_folder(owner, "EI resend folder")
    owner.post(f"/api/share/folder/{folder}", json={"audience": "list"})
    entry = owner.post(f"/api/share-settings/folder/{folder}/invites", json={"email": "gil@example.org"}).json()
    entry = entry["invited"]["pending"]
    assert owner.post(f"/api/share-settings/folder/{folder}/email-invites/{entry['id']}/resend").status_code == 200
    r = owner.delete(f"/api/share-settings/folder/{folder}/email-invites/{entry['id']}")
    assert r.status_code == 200 and r.json()["pending"] == []
    ws = _lab(boss, "EI resend lab")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "gil@example.org"}).json()["invited"]["pending"]
    r = owner.post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend")
    assert r.status_code == 200 and r.json()["members"][-1]["id"] == entry["id"]
    r = owner.delete(f"/api/workspaces/{ws}/email-invites/{entry['id']}")
    assert r.status_code == 200 and not [m for m in r.json()["members"] if m.get("pending")]
    entry = boss.post("/api/admin/invitations", json={"email": "gil@example.org"}).json()["invited"]["pending"]
    r = boss.post(f"/api/admin/invitations/{entry['id']}/resend")
    assert r.status_code == 200 and r.json()["invitations"][0]["invited_by"] == "ei_boss"
    assert boss.delete(f"/api/admin/invitations/{entry['id']}").json()["invitations"] == []
    assert boss.delete(f"/api/admin/invitations/{entry['id']}").status_code == 404
    # an invitation is found only under its own target
    page2, _ = _shared_page(owner, "EI other paper")
    entry = owner.post(f"/api/share-settings/{page2}/invites", json={"email": "hal@example.org"}).json()
    assert owner.delete(f"/api/workspaces/{ws}/email-invites/{entry['invited']['pending']['id']}").status_code == 404
    assert len(mail.cancels) == 4


def test_a_withdrawal_asks_on_a_grant_this_server_holds(mail, owner, boss, caplog):
    # the account server takes a cancel on any grant of this server's client: the withdrawer's, else the
    # inviter's, else an admin's
    ws = _lab(boss, "EI grant lab")
    make_user("ei_ned", "ei-ned-pw1")
    workspaces.set_member(ws, account_of("ei_ned"), "editor", by=account_of("ei_owner"))
    editor, headers = login("ei_ned", "ei-ned-pw1"), {"X-Gamma-Workspace": ws}
    page, _ = _shared_page(owner, "EI grant paper", ws=ws)

    def invited(email):
        r = owner.post(f"/api/share-settings/{page}/invites", json={"email": email}, headers=headers)
        return r.json()["invited"]["pending"]["id"], _mailed(mail)

    def withdrawn(invite_id):
        r = editor.delete(f"/api/share-settings/{page}/email-invites/{invite_id}", headers=headers)
        assert r.status_code == 200, r.text

    # an editor with no Gamma Cloud link of their own withdraws: the inviter's grant asks
    invite_id, link = invited("one@example.org")
    withdrawn(invite_id)
    assert mail.cancels == [_cancel(link, "ei-owner")]
    # the inviter's grant gone too: an admin's
    invite_id, link = invited("two@example.org")
    _ungrant("ei_owner")
    withdrawn(invite_id)
    assert mail.cancels[-1] == _cancel(link, "ei-boss")
    # nobody's: the row goes all the same, and the log says why the link could not
    _link(mail, owner, "ei-owner")
    invite_id, link = invited("three@example.org")
    _ungrant("ei_owner")
    _ungrant("ei_boss")
    with caplog.at_level(logging.WARNING, logger="gamma"):
        withdrawn(invite_id)
    assert len(mail.cancels) == 2 and _rows() == []
    assert any("no account here holds a Gamma Cloud grant" in r.message for r in caplog.records)


def test_the_caps_on_waiting_invitations(mail, owner, boss, monkeypatch):
    monkeypatch.setattr(email_invites, "MAX_PER_TARGET", 1)
    monkeypatch.setattr(email_invites, "MAX_SERVER", 1)
    ws = _lab(boss, "EI capped lab")
    url = f"/api/workspaces/{ws}/invites"
    assert owner.post(url, json={"email": "ida@example.org"}).status_code == 200
    r = owner.post(url, json={"email": "ivo@example.org"})
    assert r.status_code == 400 and "Too many invitations" in r.json()["detail"]
    assert owner.post(url, json={"email": "IDA@example.org", "role": "viewer"}).status_code == 200  # no new one
    assert boss.post("/api/admin/invitations", json={"email": "ida@example.org"}).status_code == 200
    assert boss.post("/api/admin/invitations", json={"email": "ivo@example.org"}).status_code == 400
    assert len(mail.invitations) == 3
    # counted again where the row is written: another request filled the last place while the mail went
    page, token = _shared_page(owner, "EI capped paper")
    _meanwhile(monkeypatch, lambda: _waiting("share", "uma@example.org", "ei-other-invitation", "ei-other-link-0001",
                                             token=token))
    r = owner.post(f"/api/share-settings/{page}/invites", json={"email": "una@example.org"})
    assert r.status_code == 400 and "Too many invitations" in r.json()["detail"]
    assert ("share", "una@example.org") not in _rows() and ("share", "uma@example.org") in _rows()
    assert mail.cancels == [_cancel(_mailed(mail), "ei-owner")]


def test_what_the_account_server_answers_reaches_the_inviter(mail, owner, boss):
    ws = _lab(boss, "EI refused lab")
    url = f"/api/workspaces/{ws}/invites"
    for (status, detail), answered, words in [
            ((400, "That address is not one this server's sign-in client is registered for."), 400, "registered for"),
            ((403, "A server signing in through the desktop app cannot send invitations."), 400, "desktop app"),
            ((404, "Not Found"), 503, "does not take e-mail invitations yet"),
            ((429, "Too many attempts."), 429, "Too many invitations for now"),
            ((409, "This invitation was sent through another sign-in client of this server."), 400,
             "another sign-in client"),
            ((503, "We could not send the e-mail right now. Try again in a few minutes."), 503, "could not send"),
            ((500, "Internal Server Error"), 503, "answered 500")]:
        mail.refuse_invitation = (status, detail)
        r = owner.post(url, json={"email": "jan@example.org"})
        assert (r.status_code, words in r.json()["detail"]) == (answered, True), (status, r.text)
    mail.refuse_invitation = None
    assert mail.cancels == []  # an answer says nothing was stored there
    # no answer at all: the mail may have gone, so its link is withdrawn (here, offline, in vain)
    mail.offline = True
    before = len(mail.calls)
    r = owner.post(url, json={"email": "jan@example.org"})
    assert r.status_code == 503 and r.json()["detail"] == "Gamma Cloud could not be reached. Try again later."
    assert ("POST", "/api/invitations/cancel") in mail.calls[before:]
    mail.offline = False
    assert _rows() == []  # nothing waits for a mail that did not go
    # an access token the account server no longer takes is dropped, and the next call refreshes the grant
    mail.access.clear()
    r = owner.post(url, json={"email": "jan@example.org"})
    assert r.status_code == 503 and cloud_auth.cached_access("sub-ei-owner") == ""
    assert owner.post(url, json={"email": "jan@example.org"}).status_code == 200
    # an inviter whose own account is not linked to Gamma Cloud
    make_user("ei_solo", "ei-solo-pw1")
    solo_ws = _lab(boss, "EI solo lab", owner="ei_solo")
    r = login("ei_solo", "ei-solo-pw1").post(f"/api/workspaces/{solo_ws}/invites", json={"email": "jan@example.org"})
    assert r.status_code == 503 and "Link your own Gamma Cloud account" in r.json()["detail"]


def test_a_member_invites_only_where_newcomers_are_taken(mail, owner, boss, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    ws = _lab(boss, "EI refusing lab")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "kai@example.org"})
    assert (r.status_code, r.json()["detail"]) == (400, ADMIN_ONLY)
    page, _ = _shared_page(owner, "EI refusing paper")
    assert owner.post(f"/api/share-settings/{page}/invites", json={"email": "kai@example.org"}).status_code == 400
    assert boss.post("/api/admin/invitations", json={"email": "kai@example.org"}).status_code == 200
    # a server on the desktop client mails nothing, not even an admin's
    monkeypatch.delenv("GAMMA_CLOUD_CLIENT_ID")
    body = boss.get("/api/admin/invitations").json()
    assert (body["available"], body["reason"], len(body["invitations"])) == (False, DESKTOP, 1)
    assert boss.post("/api/admin/invitations", json={"email": "kai2@example.org"}).json()["detail"] == DESKTOP
    assert len(mail.invitations) == 1


def test_who_may_invite_by_email(mail, owner, boss, guest):
    ws = _lab(boss, "EI who lab")
    make_user("ei_ed", "ei-ed-pw1")
    make_user("ei_view", "ei-view-pw1")
    make_user("ei_out", "ei-out-pw1")
    workspaces.set_member(ws, account_of("ei_ed"), "editor", by=account_of("ei_owner"))
    workspaces.set_member(ws, account_of("ei_view"), "viewer", by=account_of("ei_owner"))
    ed, viewer, outsider = login("ei_ed", "ei-ed-pw1"), login("ei_view", "ei-view-pw1"), login("ei_out", "ei-out-pw1")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "lea@example.org"}).json()["invited"]["pending"]
    # the workspace's: its owners (and admins) only
    assert ed.post(f"/api/workspaces/{ws}/invites", json={"email": "lia@example.org"}).status_code == 403
    assert ed.delete(f"/api/workspaces/{ws}/email-invites/{entry['id']}").status_code == 403
    assert ed.post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend").status_code == 403
    assert outsider.post(f"/api/workspaces/{ws}/invites", json={"email": "lia@example.org"}).status_code == 404
    assert boss.post(f"/api/workspaces/{ws}/invites", json={"email": "lia@example.org"}).status_code == 200
    # a share's: the workspace's editors and owners, never a viewer
    page, _ = _shared_page(owner, "EI who paper", ws=ws)
    headers = {"X-Gamma-Workspace": ws}
    url = f"/api/share-settings/{page}/invites"
    assert viewer.post(url, json={"email": "lia@example.org"}, headers=headers).status_code == 403
    r = ed.post(url, json={"email": "lia@example.org"}, headers=headers)
    assert r.status_code == 503 and "Link your own Gamma Cloud account" in r.json()["detail"]  # an editor, no grant
    # never an integration token, though it may write: the grant is its owner's own
    bearer = browser()
    write = create_token(account_of("ei_owner"), ws, "ei", 1, scope="write")["token"]
    bearer.headers["Authorization"] = f"Bearer {write}"
    assert bearer.post(url, json={"email": "lia@example.org"}).status_code == 403
    assert bearer.post(f"/api/workspaces/{ws}/invites", json={"email": "lia@example.org"}).status_code == 403
    # nor a guest, on a page of its own
    guest_page = make_page(guest, "EI guest page")["id"]
    assert guest.post(f"/api/share/{guest_page}", json={"audience": "list"}).status_code == 200
    r = guest.post(f"/api/share-settings/{guest_page}/invites", json={"email": "lia@example.org"})
    assert r.status_code == 403
    # this server's: admins only
    assert owner.get("/api/admin/invitations").status_code == 403
    assert owner.post("/api/admin/invitations", json={"email": "lia@example.org"}).status_code == 403
    assert owner.post(f"/api/admin/invitations/{entry['id']}/resend").status_code == 403
    assert all(b["by"] in ("sub-ei-owner", "sub-ei-boss") for b in mail.invitations)


def test_sending_is_rate_limited_per_inviter(mail, boss):
    for n in range(email_invites.SENDS_PER_HOUR):
        assert boss.post("/api/admin/invitations", json={"email": f"rate{n}@example.org"}).status_code == 200
    assert boss.post("/api/admin/invitations", json={"email": "one-more@example.org"}).status_code == 429
    entry = boss.get("/api/admin/invitations").json()["invitations"][0]
    assert boss.post(f"/api/admin/invitations/{entry['id']}/resend").status_code == 429  # a resend counts too


def test_the_link_admits_a_newcomer_and_the_claim_makes_them_a_member(mail, owner, boss):
    # under the invited policy, a workspace invitation: the person signs up
    # with another address, and the link id the link carried admits them
    ws = _lab(boss, "EI admitted lab")
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"email": "jo@example.org",
                                                             "role": "editor"}).status_code == 200
    link = _mailed(mail)
    # nothing remembered here: the link goes at the address the row recorded, the one its mail went out with
    _set_raw("cloud_server_url", "")
    c, location = _sign_in(mail, "ei-jo", email="jo.elsewhere@example.org", next=f"/?ws={ws}", invite=link)
    assert location == f"/?ws={ws}"
    jo = account_of("ei-jo")
    assert c.get("/api/session").json()["user"] == "ei-jo"
    assert workspaces.role_of(ws, jo) == "editor"
    assert _items(jo, "invited") == [(account_of("ei_owner"), ws, "")]
    # the claim took it: the same link admits nothing more, here or at the account server (asked on the
    # newcomer's own grant, at the address its mail went out with)
    assert email_invites.listing("workspace", ws=ws) == []
    with connect_users_db() as conn:
        assert not email_invites.admits(conn, link, "invited")
    assert mail.cancels == [_cancel(link, "ei-jo")]


def test_a_server_invitation_admits_whatever_the_policy(mail, owner, boss, monkeypatch):
    ws = _lab(boss, "EI policy lab")
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"email": "mo@example.org"}).status_code == 200
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    # a workspace's invitation admits no one where newcomers are refused
    _, location = _sign_in(mail, "ei-mo", email="mo@example.org", next="/", invite=_mailed(mail))
    assert "not+linked" in location and not account_of("ei-mo")
    assert len(email_invites.listing("workspace", ws=ws)) == 1  # it still waits
    # the server's does
    assert boss.post("/api/admin/invitations", json={"email": "max@example.org"}).status_code == 200
    c, location = _sign_in(mail, "ei-max", email="max.other@example.org", next="/", invite=_mailed(mail))
    assert location == "/" and c.get("/api/session").json()["user"] == "ei-max"
    assert boss.get("/api/admin/invitations").json()["invitations"] == []
    # under claim too; the plan's account cap still has the last word
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "claim")
    assert boss.post("/api/admin/invitations", json={"email": "nia@example.org"}).status_code == 200
    monkeypatch.setattr(hosted, "account_cap", lambda conn: "This server has reached its plan's 3 accounts.")
    _, location = _sign_in(mail, "ei-nia", email="nia@example.org", next="/", invite=_mailed(mail))
    assert "plan%27s+3+accounts" in location and not account_of("ei-nia")
    monkeypatch.setattr(hosted, "account_cap", lambda conn: "")
    _, location = _sign_in(mail, "ei-nia", email="nia@example.org", next="/", invite=_mailed(mail))
    assert location == "/" and account_of("ei-nia")
    # an invitation never hands over an account here that is not linked: the name's own refusal stands
    make_user("ei-pat", "ei-pat-pw1")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    assert boss.post("/api/admin/invitations", json={"email": "pat@example.org"}).status_code == 200
    _, location = _sign_in(mail, "ei-pat", email="pat@example.org", next="/", invite=_mailed(mail))
    assert "sign+in+with+its+password" in location
    assert cloud_auth.status_of(account_of("ei-pat")) is None


def test_the_desktop_app_carries_the_invitation_too(mail, boss, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    assert boss.post("/api/admin/invitations", json={"email": "ole@example.org"}).status_code == 200
    window, system_browser = browser(), browser()
    auth, verifier = app_start(window, next="/", invite=_mailed(mail))
    _person(mail, "ei-ole", "ole.other@example.org")
    where, query = returned(callback(system_browser, auth))
    assert where == RETURN and "result" in query, query
    assert claim(window, query["result"], verifier).status_code == 303
    assert window.get("/api/session").json()["user"] == "ei-ole"
    # an id of another shape is not kept
    auth, _ = app_start(browser(), invite="not an invitation id")
    assert mcp_oauth.load("cloud_login", HERE, auth["state"])["invite"] == ""


def test_an_expired_invitation_admits_nothing_and_the_sweep_takes_it(mail, boss, monkeypatch):
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    server = boss.post("/api/admin/invitations", json={"email": "ned@example.org"}).json()["invited"]["pending"]
    with connect_users_db() as conn:
        conn.execute("UPDATE email_invites SET expires_at = ? WHERE id = ?", (LONG_AGO, server["id"]))
        conn.commit()
    assert boss.get("/api/admin/invitations").json()["invitations"] == []  # no longer listed
    _, location = _sign_in(mail, "ei-ned", email="ned@example.org", next="/", invite=_mailed(mail))
    assert "not+linked" in location and not account_of("ei-ned")
    assert email_invites.sweep() == 1 and _rows() == []
    assert email_invites.sweep() == 0


def test_the_invitations_go_with_their_target_and_their_links_too(mail, owner, boss):
    ws = _lab(boss, "EI cascade lab")
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"email": "ws@example.org"}).status_code == 200
    ws_link = _mailed(mail)
    page, _ = _shared_page(owner, "EI cascade paper")
    assert owner.post(f"/api/share-settings/{page}/invites", json={"email": "page@example.org"}).status_code == 200
    page_link = _mailed(mail)
    assert _rows() == [("share", "page@example.org"), ("workspace", "ws@example.org")]
    # stopping the share: no request reaches the cascade, so its inviter's grant asks, at the address the row recorded
    assert owner.delete(f"/api/share-settings/{page}").status_code == 200
    assert _rows() == [("workspace", "ws@example.org")] and mail.cancels == [_cancel(page_link, "ei-owner")]
    # making the workspace personal
    r = boss.put(f"/api/workspaces/{ws}", json={"kind": "personal"})
    assert r.status_code == 200, r.text
    assert _rows() == [] and mail.cancels[-1] == _cancel(ws_link, "ei-owner")
    # deleting a folder stops its share
    folder = make_folder(owner, "EI cascade folder")
    owner.post(f"/api/share/folder/{folder}", json={"audience": "list"})
    assert owner.post(f"/api/share-settings/folder/{folder}/invites", json={"email": "dir@example.org"}).status_code \
        == 200
    folder_link = _mailed(mail)
    assert owner.delete(f"/api/folders/{folder}").json()["shares"] == 1
    assert _rows() == [] and mail.cancels[-1] == _cancel(folder_link, "ei-owner")
    # deleting a workspace: its own invitations and its shares'
    lab = _lab(boss, "EI doomed lab")
    assert owner.post(f"/api/workspaces/{lab}/invites", json={"email": "ws@example.org"}).status_code == 200
    page, _ = _shared_page(owner, "EI doomed paper", ws=lab)
    assert owner.post(f"/api/share-settings/{page}/invites", json={"email": "page@example.org"},
                      headers={"X-Gamma-Workspace": lab}).status_code == 200
    links = {sent["invite_id"] for sent in mail.invitations[-2:]}
    assert len(_rows()) == 2
    assert owner.delete(f"/api/workspaces/{lab}").status_code == 200
    assert _rows() == [] and {c["invite_id"] for c in mail.cancels[-2:]} == links and len(mail.cancels) == 5


def test_a_cascade_drops_each_link_where_its_mail_went_out(mail, owner, boss):
    # no confirmed public URL: a mail goes out at the inviter's origin, while the address this server remembers
    # is the loopback one a sign-in on this machine came in on, under which the account server keeps nothing
    assert cloud_auth.public_url_settings()["public_url"] == ""
    _set_raw("cloud_server_url", "http://127.0.0.1:9001")
    lab = _at(LAB)
    page, _ = _shared_page(lab, "EI proxied paper")
    assert lab.post(f"/api/share-settings/{page}/invites", json={"email": "far@example.org"}).status_code == 200
    page_link = _mailed(mail)
    ws = _lab(boss, "EI proxied lab")
    assert lab.post(f"/api/workspaces/{ws}/invites", json={"email": "far@example.org"}).status_code == 200
    ws_link = _mailed(mail)
    assert {sent["server_url"] for sent in mail.invitations} == {LAB}
    # stopping the share, from another origin: no request reaches the cascade, and the link goes where it went out
    assert owner.delete(f"/api/share-settings/{page}").status_code == 200
    assert mail.cancels == [_cancel(page_link, "ei-owner", LAB)]
    # deleting the workspace likewise
    assert owner.delete(f"/api/workspaces/{ws}").status_code == 200
    assert mail.cancels[-1] == _cancel(ws_link, "ei-owner", LAB) and len(mail.cancels) == 2 and _rows() == []


def test_a_link_with_no_address_of_its_own_goes_at_this_servers(mail, owner):
    # a row that recorded no address (written straight into the table here) has its link dropped at the
    # address this server remembers, on an admin's grant (the row names no inviter)
    page, token = _shared_page(owner, "EI addressless paper")
    _waiting("share", "nil@example.org", "ei-nil-invitation", "ei-nil-link-000001", token=token)
    assert owner.delete(f"/api/share-settings/{page}").status_code == 200
    assert _rows() == [] and mail.cancels == [_cancel("ei-nil-link-000001", "ei-boss")]
    # neither a confirmed public URL nor a remembered address: the account server could not tell which server
    # asks, so the row goes here alone
    page, token = _shared_page(owner, "EI nowhere paper")
    _waiting("share", "nul@example.org", "ei-nul-invitation", "ei-nul-link-000001", token=token)
    _set_raw("cloud_server_url", "")
    assert owner.delete(f"/api/share-settings/{page}").status_code == 200
    assert _rows() == [] and len(mail.cancels) == 1


def test_a_mail_sent_again_from_another_address_drops_the_link_at_the_first(mail, owner):
    # the account server keeps a link under each address it was mailed with: once the row records the newer
    # one, the copy at the other goes
    lab = _at(LAB)
    page, _ = _shared_page(owner, "EI two-address paper")
    entry = lab.post(f"/api/share-settings/{page}/invites", json={"email": "two@example.org"}).json()
    entry = entry["invited"]["pending"]
    link = _link_of(entry["id"])
    assert owner.post(f"/api/share-settings/{page}/email-invites/{entry['id']}/resend").status_code == 200
    assert mail.cancels == [_cancel(link, "ei-owner", LAB)]
    # inviting the address again from the first origin: the same link, and its copy at this address goes
    r = lab.post(f"/api/share-settings/{page}/invites", json={"email": "two@example.org", "role": "edit"})
    assert r.status_code == 200 and _link_of(entry["id"]) == link
    assert mail.cancels[-1] == _cancel(link, "ei-owner")
    # mailed again at the same address: the account server replaced its copy itself
    assert lab.post(f"/api/share-settings/{page}/email-invites/{entry['id']}/resend").status_code == 200
    assert [sent["server_url"] for sent in mail.invitations] == [LAB, HERE, LAB, LAB] and len(mail.cancels) == 2
    # withdrawn from this origin: dropped where the last mail went out
    assert owner.delete(f"/api/share-settings/{page}/email-invites/{entry['id']}").status_code == 200
    assert mail.cancels[-1] == _cancel(link, "ei-owner", LAB) and _rows() == []


def test_a_deletion_from_the_command_line_drops_the_links_before_it_returns(mail, boss, monkeypatch):
    # manage.py delete-user (release_now) exits right after the deletion, which would end a thread before it
    # reached the account server: there the links go before delete_account returns, elsewhere on a thread
    threads = []

    class Kept:
        """A thread that is started and never runs."""

        def __init__(self, target, args=(), name="", **_kwargs):
            threads.append(name)

        def start(self):
            pass

    links = {}
    for name in ("ei_rex", "ei_sue"):
        make_user(name, f"{name}-pw1")
        ws = _lab(boss, f"EI lab of {name}", owner=name)
        r = boss.post(f"/api/workspaces/{ws}/invites", json={"email": f"{name}.friend@example.org"})
        assert r.status_code == 200, r.text
        links[name] = _mailed(mail)
    monkeypatch.setattr(email_invites, "threading", SimpleNamespace(Thread=Kept))
    workspaces.delete_account(account_of("ei_rex"))  # the admin API's and the guest expiry's way
    assert threads == ["cloud-invite-cancel"] and mail.cancels == []
    workspaces.delete_account(account_of("ei_sue"), release_now=True)
    assert threads == ["cloud-invite-cancel"] and mail.cancels == [_cancel(links["ei_sue"], "ei-boss")]
    assert _rows() == []


def test_what_admits_and_what_is_claimed(mail, owner, boss, guest):
    ws = _lab(boss, "EI unit lab")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "quinn@example.org"}).json()
    entry = entry["invited"]["pending"]
    link = _mailed(mail)
    with connect_users_db() as conn:
        assert email_invites.admits(conn, link, "invited")
        assert email_invites.admits(conn, link, "provision")
        assert not email_invites.admits(conn, link, "refuse")
        assert not email_invites.admits(conn, entry["id"], "invited")  # the handle the lists show is no key
        assert not email_invites.admits(conn, None, "invited")
        assert not email_invites.admits(conn, "", "invited")
    # a guest claims nothing, nor the handle, and the invitation waits
    assert email_invites.claim(account_of(guest_name()), link) == ""
    assert email_invites.claim(account_of("ei_owner"), entry["id"]) == ""
    assert email_invites.claim(account_of("ei_owner"), "") == ""
    assert len(email_invites.listing("workspace", ws=ws)) == 1 and mail.cancels == []
    # a workspace no longer shared (here behind the cascade's back) lets no one in, and its invitation is
    # taken all the same; an account without a grant of its own has the inviter's drop the link
    make_user("ei_quinn", "ei-quinn-pw1")
    with connect_users_db() as conn:
        conn.execute("UPDATE workspaces SET kind = 'personal' WHERE id = ?", (ws,))
        conn.commit()
    try:
        with connect_users_db() as conn:
            assert not email_invites.admits(conn, link, "invited")
        assert email_invites.claim(account_of("ei_quinn"), link) == entry["id"]
        assert workspaces.role_of(ws, account_of("ei_quinn")) is None and _rows() == []
        assert mail.cancels == [_cancel(link, "ei-owner")]
    finally:
        with connect_users_db() as conn:
            conn.execute("UPDATE workspaces SET kind = 'shared' WHERE id = ?", (ws,))
            conn.commit()


def _strings(value):
    """Every string in a JSON answer, however deep."""
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return [value] if isinstance(value, str) else []


def test_only_the_people_who_manage_them_see_the_invitations(mail, owner, boss):
    # an entry is what withdraws, resends or changes an invitation, and its address is personal data:
    # a workspace's owners and the admins see its invitations, a share's managers with a session see its own
    ws = _lab(boss, "EI private lab")
    make_user("ei_vic", "ei-vic-pw1")
    make_user("ei_eda", "ei-eda-pw1")
    workspaces.set_member(ws, account_of("ei_vic"), "viewer", by=account_of("ei_owner"))
    workspaces.set_member(ws, account_of("ei_eda"), "editor", by=account_of("ei_owner"))
    viewer, editor = login("ei_vic", "ei-vic-pw1"), login("ei_eda", "ei-eda-pw1")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "ann@example.org", "role": "editor"}).json()
    entry = entry["invited"]["pending"]
    headers = {"X-Gamma-Workspace": ws}
    page, _ = _shared_page(owner, "EI private paper", ws=ws)
    shared = owner.post(f"/api/share-settings/{page}/invites", json={"email": "bo@example.org", "role": "edit"},
                        headers=headers).json()["invited"]["pending"]

    def mailed(who):
        return [m["id"] for m in who.get(f"/api/workspaces/{ws}").json()["members"] if m.get("email")]

    for who in (owner, boss):
        assert mailed(who) == [entry["id"]]
        assert who.get(f"/api/workspaces/{ws}/invites").json()["email_invites"] == [entry]
    for who in (viewer, editor):
        assert mailed(who) == []
        assert who.get(f"/api/workspaces/{ws}/invites").json() == {"invites": [], "email_invites": []}
    assert editor.get(f"/api/share-settings/{page}", headers=headers).json()["pending"] == [shared]
    assert viewer.get(f"/api/share-settings/{page}", headers=headers).json()["pending"] == []
    for scope in ("read", "write"):  # an integration token neither withdraws nor resends
        token = create_token(account_of("ei_owner"), ws, "ei", 1, scope=scope)["token"]
        bearer = browser()
        bearer.headers["Authorization"] = f"Bearer {token}"
        r = bearer.get(f"/api/share-settings/{page}")
        assert r.status_code == 200 and r.json()["pending"] == [], scope


def test_nothing_listed_admits_only_the_mailed_link_does(mail, owner, boss):
    ws = _lab(boss, "EI keyed lab")
    headers = {"X-Gamma-Workspace": ws}
    page, _ = _shared_page(owner, "EI keyed paper", ws=ws)
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "ann@example.org", "role": "editor"}).json()
    entry = entry["invited"]["pending"]
    owner.post(f"/api/share-settings/{page}/invites", json={"email": "bo@example.org", "role": "edit"}, headers=headers)
    boss.post("/api/admin/invitations", json={"email": "cy@example.org"})
    links = [sent["invite_id"] for sent in mail.invitations]
    listed = set(_strings([owner.get(f"/api/workspaces/{ws}").json(), owner.get(f"/api/workspaces/{ws}/invites").json(),
                           owner.get(f"/api/share-settings/{page}", headers=headers).json(),
                           boss.get("/api/admin/invitations").json()]))
    assert len(links) == 3 and entry["id"] in listed and not listed & set(links)
    with connect_users_db() as conn:
        assert not [value for value in listed if email_invites.admits(conn, value, "invited")]
        assert all(email_invites.admits(conn, link, "invited") for link in links)
    # a stranger's cloud account that starts with the handle the lists show is not let in: it asks to join
    _, location = _sign_in(mail, "ei-rex", email="rex@elsewhere.example", next=f"/?ws={ws}", invite=entry["id"])
    assert "cloud_join=" in location and not account_of("ei-rex")
    assert len(email_invites.listing("workspace", ws=ws)) == 1  # Ann's invitation still waits
    # the link the mail carried lets its holder in, with the invitation's role
    _, location = _sign_in(mail, "ei-ann", email="ann.other@example.org", next=f"/?ws={ws}", invite=links[0])
    assert location == f"/?ws={ws}" and workspaces.role_of(ws, account_of("ei-ann")) == "editor"


def test_a_send_that_gets_no_answer_withdraws_its_link(mail, owner, boss, monkeypatch):
    ws = _lab(boss, "EI slow lab")
    asked = _silent(monkeypatch)
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "sol@example.org"})
    assert (r.status_code, r.json()["detail"]) == (503, "Gamma Cloud did not answer in time. Try again.")
    [(link, timeout)] = asked
    assert timeout == cloud_auth.INVITATION_TIMEOUT == 60  # longer than the account server's mail relay may take
    # the account server mails before it answers: the mail may have gone, and its link now leads nowhere
    assert mail.cancels == [_cancel(link, "ei-owner")] and _rows() == []


def test_a_resend_or_a_new_invitation_that_gets_no_answer_keeps_the_link_it_had(mail, owner, boss, monkeypatch):
    # the row here still admits through its link, whichever of the account server's rows survived, so the link
    # the person holds stays alive and the invitation as it was
    ws = _lab(boss, "EI unanswered lab")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "tia@example.org", "role": "viewer"}).json()
    entry = entry["invited"]["pending"]
    link = _link_of(entry["id"])
    asked = _silent(monkeypatch)
    r = owner.post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend")
    assert (r.status_code, r.json()["detail"]) == (503, "Gamma Cloud did not answer in time. Try again.")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "TIA@example.org", "role": "editor"})
    assert r.status_code == 503
    assert [a[0] for a in asked] == [link, link] and mail.cancels == []
    assert email_invites.listing("workspace", ws=ws) == [entry]  # the old role, mailed_at and expiry
    # mailed from another origin, though, the account server kept that copy apart, which no row here records
    assert _at(LAB).post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend").status_code == 503
    assert mail.cancels == [_cancel(link, "ei-owner", LAB)] and email_invites.listing("workspace", ws=ws) == [entry]

    # withdrawn while the resend hung: no row here carries the link any more, so it goes
    def withdrawn():
        with connect_users_db() as conn:
            conn.execute("DELETE FROM email_invites WHERE id = ?", (entry["id"],))
            conn.commit()

    _silent(monkeypatch, withdrawn)
    assert owner.post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend").status_code == 503
    assert mail.cancels[1:] == [_cancel(link, "ei-owner")] and _rows() == []


def test_a_timeout_is_told_from_a_refused_connection():
    # an answer that never comes: a socket that listens but never accepts
    with socket.socket() as silent:
        silent.bind(("127.0.0.1", 0))
        silent.listen()
        with pytest.raises(cloud_auth.CloudAuthError) as raised:
            cloud_auth._http(f"http://127.0.0.1:{silent.getsockname()[1]}/api/invitations", data=b"{}", timeout=0.3)
    assert raised.value.status is None and cloud_auth._timed_out(raised.value)
    # one while connecting comes wrapped in a URLError; a refused connection is no timeout
    for reason, timed_out in [(TimeoutError("timed out"), True), (ConnectionRefusedError(10061, "refused"), False)]:
        e = cloud_auth.CloudAuthError("cannot reach the account server")
        e.__cause__ = urllib.error.URLError(reason)
        assert cloud_auth._timed_out(e) is timed_out


def test_what_changes_while_the_mail_goes(mail, owner, boss, monkeypatch):
    def cancelled():
        return mail.cancels[-1]["invite_id"] if mail.cancels else None

    # the share stopped meanwhile (here behind the cascade's back): nothing is kept, and the mailed link goes
    page, token = _shared_page(owner, "EI stopped paper")

    def stop():
        with connect_users_db() as conn:
            delete_shares(conn, "token = ?", (token,))
            conn.commit()

    _meanwhile(monkeypatch, stop)
    r = owner.post(f"/api/share-settings/{page}/invites", json={"email": "val@example.org"})
    assert (r.status_code, r.json()["detail"]) == (400, "it is not shared any more")
    assert _rows() == [] and cancelled() == _mailed(mail)
    # the workspace made personal meanwhile
    ws = _lab(boss, "EI unshared lab")
    _meanwhile(monkeypatch, lambda: workspaces.update(ws, {"kind": "personal"}))
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "val@example.org"})
    assert (r.status_code, r.json()["detail"]) == (400, "the workspace is not shared any more")
    assert _rows() == [] and cancelled() == _mailed(mail)
    # withdrawn while it was mailed again: not found, and the link mailed again goes
    ws = _lab(boss, "EI withdrawn lab")
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "wes@example.org"}).json()["invited"]["pending"]
    link = _link_of(entry["id"])

    def withdrawn():
        with connect_users_db() as conn:
            conn.execute("DELETE FROM email_invites WHERE id = ?", (entry["id"],))
            conn.commit()

    _meanwhile(monkeypatch, withdrawn)
    assert owner.post(f"/api/workspaces/{ws}/email-invites/{entry['id']}/resend").status_code == 404
    assert _mailed(mail) == link and cancelled() == link
    # another request mailed the same address meanwhile: the newest invitation stays, the other's link goes
    _meanwhile(monkeypatch, lambda: _waiting("workspace", "Xia@example.org", "ei-racing-invitation",
                                             "ei-racing-link-0001", ws=ws))
    entry = owner.post(f"/api/workspaces/{ws}/invites", json={"email": "xia@example.org"}).json()["invited"]["pending"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT id, link_id FROM email_invites WHERE email_norm = 'xia@example.org'").fetchall() \
            == [(entry["id"], _mailed(mail))]
    assert mail.cancels[-1] == _cancel("ei-racing-link-0001", "ei-owner") and len(mail.cancels) == 4

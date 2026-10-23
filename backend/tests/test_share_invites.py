"""A share's invitations by Gamma Cloud username (gamma/shares.py): a page
or folder share invites a cloud account before it has an account here; the
invitation waits in ``pending_share_users`` keyed by the share's token and
the cloud subject, admits the person under the ``invited`` sign-in policy,
and becomes a ``share_users`` row when their sign-in creates, claims or
links the account (cloud_auth.resolve_account → shares.claim_pending). Most
tests replace the account server's username lookup by a fake directory (its
wire shape is tests/test_pending_memberships.py's); one runs the browser's
round trip through the fake account server."""

import pytest
from conftest import account_of, login, make_folder, make_page, make_user
from test_cloud_auth import browser, callback, cloud, start  # noqa: F401  (the fake account server fixture)

from gamma import cloud_auth, shares
from gamma.auth import share_access, share_lookup
from gamma.db import connect_users_db
from gamma.integrations import create_token

ISSUER = "https://account.test"

# The fake account server's directory: cloud username → subject.
DIRECTORY = {name: f"sub-{name}" for name in ("si-ann", "si-bo", "si-cy", "si-dee", "si-fay", "si-gil", "si-hal",
                                              "si-owner")}


@pytest.fixture
def cloud_on(monkeypatch):
    """Cloud sign-in on, the username lookup answered from DIRECTORY."""
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    monkeypatch.delenv("GAMMA_CLOUD_ADMIN_SUBJECT", raising=False)
    calls = []

    def fake_lookup(name, by=""):
        calls.append((name, by))
        return {"sub": DIRECTORY[name], "username": name} if name in DIRECTORY else None

    monkeypatch.setattr(cloud_auth, "lookup_username", fake_lookup)
    yield calls
    with connect_users_db() as conn:
        conn.execute("DELETE FROM identities WHERE subject LIKE 'sub-si-%'")
        conn.commit()


@pytest.fixture(scope="module")
def owner():
    make_user("si_owner", "si-owner-pw1")
    return login("si_owner", "si-owner-pw1")


def _claims(sub, username):
    return {"sub": sub, "preferred_username": username, "email": f"{username}@example.org", "email_verified": True}


def _shared_page(client, title, **settings):
    """A new page of the client's, shared with ``settings``: (page id, token)."""
    page = make_page(client, title)["id"]
    r = client.post(f"/api/share/{page}", json=settings)
    assert r.status_code == 200, r.text
    return page, r.json()["token"]


def _invite(client, page, **body):
    return client.post(f"/api/share-settings/{page}/invites", json=body)


def _shared_items(user_id):
    """The account's ``shared`` Inbox items: ``[(actor, share token)]``."""
    with connect_users_db() as conn:
        return conn.execute("SELECT actor, share_token FROM inbox_items WHERE kind = 'shared' AND user_id = ?",
                            (user_id,)).fetchall()


def test_an_invitation_waits_and_the_first_sign_in_claims_it(owner, cloud_on, monkeypatch):
    page, token = _shared_page(owner, "SI paper", audience="list")
    r = _invite(owner, page, username="@SI-Ann ", role="edit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invited"]["pending"]["subject"] == "sub-si-ann"
    assert cloud_on[-1] == ("si-ann", account_of("si_owner"))  # the typed name cleaned, the inviter named
    assert [(p["username"], p["role"], p["invited_by"]) for p in body["pending"]] == [("si-ann", "edit", "si_owner")]
    assert body["users"] == []
    assert owner.get(f"/api/share-settings/{page}").json()["pending"] == body["pending"]
    # named by its subject, the invitation changes its role without another lookup
    r = _invite(owner, page, subject="sub-si-ann", role="view")
    assert r.status_code == 200, r.text
    assert len(cloud_on) == 1
    assert [(p["username"], p["role"]) for p in r.json()["pending"]] == [("si-ann", "view")]

    # the first sign-in makes the account, and the share lets it in with its role
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    user_id, name = cloud_auth.resolve_account(_claims("sub-si-ann", "si-ann"))
    assert name == "si-ann"
    settings = owner.get(f"/api/share-settings/{page}").json()
    assert settings["pending"] == [] and settings["users"] == [{"name": "si-ann", "role": "view"}]
    assert share_access(share_lookup(token), user_id, False) == ("view", "")
    assert _shared_items(user_id) == [(account_of("si_owner"), token)]  # an Inbox item from the inviter


def test_the_invited_policy_admits_a_share_invitation(owner, cloud, cloud_on, monkeypatch):  # noqa: F811
    # the browser's round trip: a newcomer with an invitation to one page,
    # and nothing else here, signs in from its link and lands on it
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "invited")
    page, token = _shared_page(owner, "SI invited paper", audience="list")
    assert _invite(owner, page, username="si-bo", role="edit").status_code == 200
    cloud.person.update({"sub": "sub-si-bo", "preferred_username": "si-bo", "email": "si-bo@example.org"})
    c = browser()
    r = callback(c, start(c, next=f"/?share={token}"))
    assert r.headers["location"] == f"/?share={token}"
    assert c.get("/api/session").json()["user"] == "si-bo"
    assert c.get(f"/api/share/{token}").json()["can_edit"] is True
    assert shares.pending(token) == []


def test_stopping_the_share_withdraws_its_invitations(owner, cloud_on, monkeypatch):
    page, token = _shared_page(owner, "SI stopped paper", audience="list")
    assert _invite(owner, page, username="si-cy").status_code == 200
    with connect_users_db() as conn:
        assert shares.waiting(conn, "sub-si-cy")
    assert owner.delete(f"/api/share-settings/{page}").status_code == 200
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pending_share_users WHERE token = ?", (token,)).fetchone() == (0,)
        assert not shares.waiting(conn, "sub-si-cy")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "invited")
    with pytest.raises(cloud_auth.NotInvited):  # nothing admits them any more
        cloud_auth.resolve_account(_claims("sub-si-cy", "si-cy"))


def test_someone_linked_here_is_invited_at_once(owner, cloud_on):
    make_user("si_dee", "si-dee-pw1")
    with connect_users_db() as conn:
        cloud_auth.link(conn, account_of("si_dee"), _claims("sub-si-dee", "si-dee"))
        conn.commit()
    page, token = _shared_page(owner, "SI linked paper", audience="list")
    r = _invite(owner, page, username="si-dee", role="edit")
    assert r.status_code == 200, r.text
    assert r.json()["invited"] == {"user": "si_dee", "username": "si-dee"}
    assert r.json()["users"] == [{"name": "si_dee", "role": "edit"}] and r.json()["pending"] == []
    assert _shared_items(account_of("si_dee")) == [(account_of("si_owner"), token)]
    # again: the role changes, and nobody is told twice
    r = _invite(owner, page, username="si-dee", role="view")
    assert r.json()["users"] == [{"name": "si_dee", "role": "view"}]
    assert len(_shared_items(account_of("si_dee"))) == 1
    # one's own cloud account is no one to invite
    with connect_users_db() as conn:
        cloud_auth.link(conn, account_of("si_owner"), _claims("sub-si-owner", "si-owner"))
        conn.commit()
    r = _invite(owner, page, username="si-owner")
    assert r.status_code == 400 and "your own" in r.json()["detail"]


def test_invitation_rules(owner, cloud_on, monkeypatch):
    page, token = _shared_page(owner, "SI rules paper", audience="list")
    assert _invite(owner, page, username="nobody-here").json()["detail"] == "no Gamma Cloud account is named nobody-here"
    assert _invite(owner, page, username="no_underscores").status_code == 400
    assert _invite(owner, page, username="si-fay", role="owner").status_code == 400
    assert _invite(owner, page, subject="sub-unknown").json()["detail"] == "no such invitation or request"
    # withdrawn, once
    assert _invite(owner, page, username="si-fay").status_code == 200
    r = owner.delete(f"/api/share-settings/{page}/invites/sub-si-fay")
    assert r.status_code == 200 and r.json()["pending"] == []
    assert owner.delete(f"/api/share-settings/{page}/invites/sub-si-fay").status_code == 404
    # a page that is not shared, an account that does not have the page
    unshared = make_page(owner, "SI unshared paper")["id"]
    assert _invite(owner, unshared, username="si-fay").status_code == 404
    make_user("si_other", "si-other-pw1")
    assert _invite(login("si_other", "si-other-pw1"), page, username="si-fay").status_code == 404
    # an integration token does not spend its owner's cloud grant
    bearer = browser()
    write = create_token(account_of("si_owner"), owner.get("/api/session").json()["default_workspace"], "si", 1,
                         scope="write")["token"]
    bearer.headers["Authorization"] = f"Bearer {write}"
    assert _invite(bearer, page, username="si-fay").status_code == 403
    # cloud sign-in off: refused before any lookup
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    before = len(cloud_on)
    r = _invite(owner, page, username="si-fay")
    assert r.status_code == 400 and "not set up" in r.json()["detail"] and len(cloud_on) == before


def test_a_guest_invites_no_one_by_cloud_username(guest, cloud_on):
    page = make_page(guest, "SI guest page")["id"]
    assert guest.post(f"/api/share/{page}", json={"audience": "list"}).status_code == 200
    r = _invite(guest, page, username="si-fay")
    assert r.status_code == 403 and not cloud_on


def test_too_many_waiting_invitations(owner, cloud_on, monkeypatch):
    monkeypatch.setattr(shares, "MAX_PENDING_PER_SHARE", 1)
    page, _ = _shared_page(owner, "SI capped paper", audience="list")
    assert _invite(owner, page, username="si-gil").status_code == 200
    r = _invite(owner, page, username="si-hal")
    assert r.status_code == 400 and "too many" in r.json()["detail"]
    assert _invite(owner, page, subject="sub-si-gil", role="edit").status_code == 200  # a change is no new one


def test_lookups_share_the_budget_of_workspace_invitations(cloud_on, monkeypatch):
    monkeypatch.setattr(cloud_auth, "LOOKUPS_PER_10_MIN", 2)
    make_user("si_rate", "si-rate-pw1")
    c = login("si_rate", "si-rate-pw1")
    page, _ = _shared_page(c, "SI rate paper", audience="list")
    assert [_invite(c, page, username="nobody-x").status_code for _ in range(3)] == [400, 400, 429]


def test_a_folder_share_invites_too(owner, cloud_on, monkeypatch):
    folder = make_folder(owner, "si-lab")
    r = owner.post(f"/api/share/folder/{folder}", json={"audience": "list"})
    assert r.status_code == 200, r.text
    token = r.json()["token"]
    r = owner.post(f"/api/share-settings/folder/{folder}/invites", json={"username": "si-hal", "role": "edit"})
    assert r.status_code == 200, r.text
    assert [p["username"] for p in r.json()["pending"]] == ["si-hal"]
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    user_id, _ = cloud_auth.resolve_account(_claims("sub-si-hal", "si-hal"))
    assert owner.get(f"/api/share-settings/folder/{folder}").json()["users"] == [{"name": "si-hal", "role": "edit"}]
    assert share_access(share_lookup(token), user_id, False) == ("edit", "")
    assert owner.delete(f"/api/share-settings/folder/{folder}/invites/sub-si-hal").status_code == 404  # claimed

"""Requests to join (gamma/join_requests.py): under the ``invited`` sign-in
policy a Gamma Cloud account nothing admits is refused, and its sign-in
asks to join instead. The browser goes back to the page it came from with
``?cloud_join=``; the share it came through shows the request to the
people who manage it (and tells its maker and the workspace's owners in
their Inbox), who approve it into a waiting invitation or decline it; an
admin approves, declines or removes any request. Every sign-in runs through
the fake account server of tests/test_cloud_auth.py."""

import pytest
from conftest import account_of, drop_user, login, make_page, make_user
from test_cloud_auth import RETURN, app_start, browser, callback, cloud, returned, start  # noqa: F401

from gamma import join_requests, notices, workspaces
from gamma.db import connect_users_db


@pytest.fixture(autouse=True, scope="module")
def no_requests():
    """The module counts every request there is: it starts and ends with none."""
    def clear():
        with connect_users_db() as conn:
            conn.execute("DELETE FROM join_requests")
            conn.commit()
    clear()
    yield
    clear()


@pytest.fixture(scope="module")
def owner():
    make_user("jr_owner", "jr-owner-pw1")
    return login("jr_owner", "jr-owner-pw1")


@pytest.fixture(scope="module")
def boss():
    make_user("jr_boss", "jr-boss-pw1", is_admin=1)
    yield login("jr_boss", "jr-boss-pw1")
    drop_user("jr_boss")  # test_admin_users knows every admin there is


@pytest.fixture
def invited(cloud, monkeypatch):  # noqa: F811
    """The fake account server, and this server admitting invited people only."""
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "invited")
    return cloud


def _shared_page(client, title, **settings):
    page = make_page(client, title)["id"]
    r = client.post(f"/api/share/{page}", json=settings)
    assert r.status_code == 200, r.text
    return page, r.json()["token"]


def _sign_in(cloud, name, next="/"):
    """``name``'s Gamma Cloud sign-in from ``next``, in a browser of its
    own: (the browser, where the callback sent it)."""
    cloud.person.update({"sub": f"sub-{name}", "preferred_username": name, "email": f"{name}@example.org"})
    c = browser()
    r = callback(c, start(c, next=next))
    assert r.status_code == 302, r.text
    return c, r.headers["location"]


def _join_items(client, **match):
    r = client.get("/api/inbox", params={"filter": "unread", "limit": 100})
    assert r.status_code == 200, r.text
    return [i for i in r.json()["items"] if i["kind"] == "join_request" and all(i[k] == v for k, v in match.items())]


def _status(subject):
    with connect_users_db() as conn:
        row = conn.execute("SELECT status FROM join_requests WHERE subject = ?", (subject,)).fetchone()
    return row[0] if row else None


def test_a_refused_sign_in_from_a_share_asks_to_join(owner, invited):
    page, token = _shared_page(owner, "JR paper", audience="list")
    c, location = _sign_in(invited, "jr-cat", f"/?share={token}")
    assert location == f"/?share={token}&cloud_join=sent"  # back on the link, which says so
    assert c.get("/api/session").json()["user"] is None
    assert invited.revoked == ["rt-1"]  # the refused sign-in leaves no device behind
    assert c.get("/api/server-config").json()["cloud"]["join_requests"] is True
    requests = owner.get(f"/api/share-settings/{page}").json()["requests"]
    assert [(q["subject"], q["username"], q["email"]) for q in requests] == [("sub-jr-cat", "jr-cat", "jr-cat@example.org")]
    # the share's maker hears of it: the snippet names the person, who has no account to name
    items = _join_items(owner, page_id=page)
    assert [(i["snippet"], i["actor"], i["page_title"]) for i in items] == [("jr-cat", "", "JR paper")]
    # asking again (from the login page) waits; the request keeps the page it came through
    _, location = _sign_in(invited, "jr-cat")
    assert location == "/?cloud_join=waiting"
    assert [q["username"] for q in owner.get(f"/api/share-settings/{page}").json()["requests"]] == ["jr-cat"]
    assert len(_join_items(owner, page_id=page)) == 1  # told once
    # what an earlier round trip left in the address gives way to the new outcome
    _, location = _sign_in(invited, "jr-cat", f"/?share={token}&cloud_error=old")
    assert location == f"/?share={token}&cloud_join=waiting"


def test_approving_a_request_lets_them_in_with_the_page(owner, invited):
    page, token = _shared_page(owner, "JR approved paper", audience="list")
    _sign_in(invited, "jr-dot", f"/?share={token}")
    r = owner.post(f"/api/share-settings/{page}/invites", json={"subject": "sub-jr-dot", "role": "view"})
    assert r.status_code == 200, r.text
    assert r.json()["requests"] == [] and [p["username"] for p in r.json()["pending"]] == ["jr-dot"]
    assert _status("sub-jr-dot") is None  # answered: a waiting invitation now
    assert _join_items(owner, page_id=page) == []  # the Inbox item settled with it
    r = owner.post(f"/api/share-settings/{page}/invites", json={"subject": "sub-jr-dot", "role": "edit"})
    assert [(p["username"], p["role"]) for p in r.json()["pending"]] == [("jr-dot", "edit")]
    # their next sign-in from the link (its gate's address still saying what
    # the last one became) gets them an account and the page as it was
    c, location = _sign_in(invited, "jr-dot", f"/?share={token}&cloud_join=sent")
    assert location == f"/?share={token}"
    assert c.get("/api/session").json()["user"] == "jr-dot"
    assert c.get(f"/api/share/{token}").json()["can_edit"] is True


def test_a_declined_person_asks_nothing_until_an_admin_removes_it(owner, boss, invited):
    page, token = _shared_page(owner, "JR declined paper", audience="list")
    _sign_in(invited, "jr-eli", f"/?share={token}")
    assert owner.delete(f"/api/share-settings/{page}/requests/sub-jr-eli").json()["requests"] == []
    assert owner.delete(f"/api/share-settings/{page}/requests/sub-jr-eli").status_code == 404
    assert _join_items(owner, page_id=page) == []
    _, location = _sign_in(invited, "jr-eli", f"/?share={token}")
    assert location == f"/?share={token}&cloud_join=declined"
    listed = {q["subject"]: q for q in boss.get("/api/admin/join-requests").json()["requests"]}
    assert (listed["sub-jr-eli"]["status"], listed["sub-jr-eli"]["decided_by"],
            listed["sub-jr-eli"]["shared_by"]) == ("declined", "jr_owner", "jr_owner")
    # a declined request is no invitation to approve
    r = owner.post(f"/api/share-settings/{page}/invites", json={"subject": "sub-jr-eli"})
    assert r.status_code == 400
    # an admin removes it, and they may ask again
    assert boss.delete("/api/admin/join-requests/sub-jr-eli").status_code == 200
    assert boss.delete("/api/admin/join-requests/sub-jr-eli").status_code == 404
    _, location = _sign_in(invited, "jr-eli", f"/?share={token}")
    assert location == f"/?share={token}&cloud_join=sent"


def test_an_admin_decides_any_request(owner, boss, invited):
    _, location = _sign_in(invited, "jr-fox")  # from the login page: no share to tell
    assert location == "/?cloud_join=sent"
    listed = {q["subject"]: q for q in boss.get("/api/admin/join-requests").json()["requests"]}
    assert (listed["sub-jr-fox"]["status"], listed["sub-jr-fox"]["shared_by"]) == ("pending", "")
    assert owner.get("/api/admin/join-requests").status_code == 403
    # the admins' notice counts what waits
    count, newest = join_requests.waiting()
    mine = [n for n in notices.for_user(account_of("jr_boss"), True) if n["id"] == "join-requests"]
    assert [(n["fingerprint"], n["pane"], n["params"]) for n in mine] == [(newest, "server", {"n": count})]
    assert not [n for n in notices.for_user(account_of("jr_owner"), False) if n["id"] == "join-requests"]
    # approved, the next sign-in gets them an account, and the request goes
    r = boss.put("/api/admin/join-requests/sub-jr-fox", json={"status": "approved"})
    assert r.status_code == 200 and _status("sub-jr-fox") == "approved"
    c, location = _sign_in(invited, "jr-fox")
    assert location == "/" and c.get("/api/session").json()["user"] == "jr-fox"
    assert _status("sub-jr-fox") is None
    # declined by an admin, as from a share
    _sign_in(invited, "jr-gus")
    assert boss.put("/api/admin/join-requests/sub-jr-gus", json={"status": "declined"}).status_code == 200
    assert _sign_in(invited, "jr-gus")[1] == "/?cloud_join=declined"
    assert boss.put("/api/admin/join-requests/sub-nobody", json={"status": "approved"}).status_code == 404
    assert boss.put("/api/admin/join-requests/sub-jr-gus", json={"status": "maybe"}).status_code == 422


def test_who_hears_of_a_request_and_who_decides_it(owner, invited):
    # a shared workspace: the request came through a page its editor shared
    make_user("jr_ed", "jr-ed-pw1")
    make_user("jr_vie", "jr-vie-pw1")
    info = workspaces.create("JR lab", account_of("jr_owner"), kind="shared")
    workspaces.set_member(info["id"], account_of("jr_ed"), "editor", by=account_of("jr_owner"))
    workspaces.set_member(info["id"], account_of("jr_vie"), "viewer", by=account_of("jr_owner"))
    ed, vie = login("jr_ed", "jr-ed-pw1"), login("jr_vie", "jr-vie-pw1")
    for c in (ed, vie, owner):
        c.headers["X-Gamma-Workspace"] = info["id"]
    try:
        page, token = _shared_page(ed, "JR lab paper", audience="list")
        _sign_in(invited, "jr-hal", f"/?share={token}")
        # its maker and the workspace's owner hear of it; a viewer neither hears nor sees it
        assert len(_join_items(ed, page_id=page)) == len(_join_items(owner, page_id=page)) == 1
        assert _join_items(vie) == []
        assert "requests" not in vie.get(f"/api/share-settings/{page}").json()
        assert [q["username"] for q in owner.get(f"/api/share-settings/{page}").json()["requests"]] == ["jr-hal"]
        assert vie.delete(f"/api/share-settings/{page}/requests/sub-jr-hal").status_code == 403
        # one manager declines it: the other's Inbox item settles
        assert ed.delete(f"/api/share-settings/{page}/requests/sub-jr-hal").status_code == 200
        assert _join_items(owner, page_id=page) == []
    finally:
        for c in (ed, vie, owner):
            c.headers.pop("X-Gamma-Workspace", None)


def test_requests_are_capped_and_other_policies_ask_nothing(owner, invited, monkeypatch):
    monkeypatch.setattr(join_requests, "MAX_PENDING", join_requests.waiting()[0])
    page, token = _shared_page(owner, "JR capped paper", audience="list")
    _, location = _sign_in(invited, "jr-ivy", f"/?share={token}")
    assert location.startswith(f"/?share={token}&cloud_error=") and "invited+people+only" in location
    assert _status("sub-jr-ivy") is None
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "refuse")
    _, location = _sign_in(invited, "jr-ivy", f"/?share={token}")
    assert "cloud_error=" in location and _status("sub-jr-ivy") is None
    c = browser()
    assert c.get("/api/server-config").json()["cloud"]["join_requests"] is False


def test_a_desktop_sign_in_hears_of_its_request(invited):
    c = browser()
    invited.person.update({"sub": "sub-jr-jay", "preferred_username": "jr-jay", "email": "jr-jay@example.org"})
    auth, _verifier = app_start(c)
    assert returned(callback(c, auth)) == (RETURN, {"error": join_requests.MESSAGES["sent"]})
    assert _status("sub-jr-jay") == "pending"

"""Invitations by Gamma Cloud username (gamma/workspaces.py invite_cloud):
a shared workspace's owner invites a cloud account before it has ever
signed in here; the invitation waits in ``pending_memberships`` keyed by the
cloud subject and becomes a membership when that subject's cloud sign-in
creates, claims or links a local account (cloud_auth.resolve_account →
workspaces.claim_pending_memberships). Most tests replace the account
server's username lookup by a fake directory; its wire shape is tested on
its own, and one test runs the whole way: the inviter's own linked grant
refreshed into an access token, the lookup at the fake account server, the
pending row."""

import pytest
from conftest import login, make_user
from test_cloud_auth import browser, callback, cloud, start  # noqa: F401  (the fake account server fixture)

from gamma import cloud_auth, workspaces
from gamma.db import connect_users_db

ISSUER = "https://account.test"

# The fake account server's directory: cloud username → subject.
DIRECTORY = {"pm-alice": "sub-pm-alice", "pm-bob": "sub-pm-bob", "pm-carl": "sub-pm-carl",
             "pm-dora": "sub-pm-dora", "pm-eve": "sub-pm-eve", "pm-flo": "sub-pm-flo"}


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

    monkeypatch.setattr(workspaces, "cloud_lookup_username", fake_lookup)
    yield calls
    with connect_users_db() as conn:
        conn.execute("DELETE FROM identities WHERE subject LIKE 'sub-pm-%'")
        conn.commit()


@pytest.fixture(scope="module")
def owner():
    make_user("pm_owner", "pm-owner-pw1")
    return login("pm_owner", "pm-owner-pw1")


@pytest.fixture(scope="module")
def boss():
    make_user("pm_boss", "pm-boss-pw1", is_admin=1)
    return login("pm_boss", "pm-boss-pw1")


def _shared(boss, name, owner="pm_owner"):
    r = boss.post("/api/workspaces", json={"name": name, "kind": "shared", "owner": owner})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _claims(sub, username, **extra):
    return {"sub": sub, "preferred_username": username, "email": f"{username}@example.org",
            "email_verified": True, **extra}


def _pending(info):
    return [m for m in info["members"] if m.get("pending")]


def test_invite_waits_and_provisioned_sign_in_claims_it(owner, boss, cloud_on, monkeypatch):
    ws = _shared(boss, "PM lab")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"username": "@PM-Alice ", "role": "viewer"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["invited"]["pending"]["subject"] == "sub-pm-alice"
    assert cloud_on[-1] == ("pm-alice", "pm_owner")  # typed name normalized, the inviter named
    # one list: real members, then the pending invitation
    assert [(m["username"], m["role"], m.get("pending", False)) for m in body["members"]] == [
        ("pm_owner", "owner", False), ("pm-alice", "viewer", True)]
    assert owner.get(f"/api/workspaces/{ws}/invites").json()["invites"][0]["invited_by"] == "pm_owner"
    # inviting again changes the role, still one row
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-alice", "role": "editor"}).status_code == 200
    assert [(p["username"], p["role"]) for p in workspaces.pending_invites(ws)] == [("pm-alice", "editor")]
    # the session count and the workspace list count explicit members only
    assert workspaces.members(ws) == [m for m in body["members"] if not m.get("pending")]

    # her first cloud sign-in creates the account, and the membership is waiting
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    assert cloud_auth.resolve_account(_claims("sub-pm-alice", "pm-alice")) == "pm-alice"
    assert workspaces.role_of(ws, "pm-alice") == "editor"
    assert workspaces.pending_invites(ws) == []
    info = owner.get(f"/api/workspaces/{ws}").json()
    assert ("pm-alice", "editor", "pm_owner") in [(m["username"], m["role"], m["added_by"]) for m in info["members"]]
    assert _pending(info) == []

    # someone already linked here joins at once, no pending row
    ws2 = _shared(boss, "PM lab two")
    r = owner.post(f"/api/workspaces/{ws2}/invites", json={"username": "pm-alice", "role": "viewer"})
    assert r.json()["invited"] == {"member": "pm-alice", "username": "pm-alice"}
    assert workspaces.role_of(ws2, "pm-alice") == "viewer" and workspaces.pending_invites(ws2) == []
    r = owner.post(f"/api/workspaces/{ws2}/invites", json={"username": "pm-alice", "role": "editor"})
    assert r.status_code == 400 and "already a member" in r.json()["detail"]


def test_claim_policy_and_link_claim_waiting_invitations(owner, boss, cloud_on, monkeypatch):
    ws = _shared(boss, "PM claim lab")
    for name in ("pm-bob", "pm-carl"):
        assert owner.post(f"/api/workspaces/{ws}/invites", json={"username": name, "role": "viewer"}).status_code == 200
    # claim: the cloud username matches an unlinked local account
    make_user("pm-bob", "pm-bob-pw1")
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "claim")
    assert cloud_auth.resolve_account(_claims("sub-pm-bob", "pm-bob")) == "pm-bob"
    assert workspaces.role_of(ws, "pm-bob") == "viewer"
    # link: a signed-in local account under another name attaches the cloud account
    make_user("pm_carlos", "pm-carlos-pw1")
    assert cloud_auth.resolve_account(_claims("sub-pm-carl", "pm-carl", _link_user="pm_carlos")) == "pm_carlos"
    assert workspaces.role_of(ws, "pm_carlos") == "viewer"
    assert workspaces.pending_invites(ws) == []


def test_refused_sign_in_leaves_the_invitation_waiting(owner, boss, cloud_on):
    ws = _shared(boss, "PM refused lab")
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-dora"}).status_code == 200
    with pytest.raises(cloud_auth.CloudAuthError):  # policy refuse, no account named pm-dora
        cloud_auth.resolve_account(_claims("sub-pm-dora", "pm-dora"))
    assert [p["role"] for p in workspaces.pending_invites(ws)] == ["editor"]  # role defaults to editor


def test_claim_keeps_existing_roles_and_drops_stale_invitations(owner, boss, cloud_on):
    keep = _shared(boss, "PM keep lab")
    gone = _shared(boss, "PM gone lab")
    solo = _shared(boss, "PM solo lab")
    for ws in (keep, gone, solo):
        assert owner.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-eve", "role": "viewer"}).status_code == 200
    make_user("pm-eve", "pm-eve-pw1")
    assert owner.put(f"/api/workspaces/{keep}/members/pm-eve", json={"role": "editor"}).status_code == 200
    # deleting a workspace removes its invitations; converting to personal drops them
    assert owner.delete(f"/api/workspaces/{gone}").status_code == 200
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM pending_memberships WHERE workspace_id = ?", (gone,)).fetchone()[0] == 0
    assert boss.put(f"/api/workspaces/{solo}", json={"kind": "personal"}).status_code == 200
    assert workspaces.pending_invites(solo) == []
    joined = workspaces.claim_pending_memberships("pm-eve", "sub-pm-eve")
    assert joined == [] and workspaces.role_of(keep, "pm-eve") == "editor"  # the explicit role stays
    assert workspaces.pending_invites(keep) == []


def test_invite_rules(owner, boss, cloud_on, monkeypatch):
    ws = _shared(boss, "PM rules lab")
    url = f"/api/workspaces/{ws}/invites"
    assert owner.post(url, json={"username": "nobody-here"}).json()["detail"] == "no Gamma Cloud account is named nobody-here"
    assert owner.post(url, json={"username": "no_underscores"}).status_code == 400
    assert owner.post(url, json={"username": "pm-flo", "role": "owner"}).status_code == 400
    # an editor cannot invite; a stranger does not learn the workspace exists
    make_user("pm_ed", "pm-ed-pw1")
    assert owner.put(f"/api/workspaces/{ws}/members/pm_ed", json={"role": "editor"}).status_code == 200
    ed = login("pm_ed", "pm-ed-pw1")
    assert ed.post(url, json={"username": "pm-flo"}).status_code == 403
    make_user("pm_stranger", "pm-stranger-pw1")
    assert login("pm_stranger", "pm-stranger-pw1").get(url).status_code == 404
    # an admin needs no membership
    assert boss.post(url, json={"username": "pm-flo", "role": "viewer"}).json()["invited"]["pending"]["invited_by"] == "pm_boss"
    # withdraw
    assert ed.delete(f"{url}/sub-pm-flo").status_code == 403
    assert owner.delete(f"{url}/sub-pm-flo").json() == {"ok": True}
    assert owner.delete(f"{url}/sub-pm-flo").status_code == 404
    # personal workspaces have no other members
    personal = owner.get("/api/session").json()["default_workspace"]
    r = owner.post(f"/api/workspaces/{personal}/invites", json={"username": "pm-flo"})
    assert r.status_code == 400 and "personal" in r.json()["detail"]
    # cloud sign-in off: refused before any lookup
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER")
    before = len(cloud_on)
    r = owner.post(url, json={"username": "pm-flo"})
    assert r.status_code == 400 and "not set up" in r.json()["detail"] and len(cloud_on) == before


def test_lookups_are_rate_limited_per_account(owner, boss, cloud_on, monkeypatch):
    from gamma.routers import workspaces as ws_router
    monkeypatch.setattr(ws_router, "LOOKUPS_PER_10_MIN", 2)
    ws = _shared(boss, "PM limit lab")
    url = f"/api/workspaces/{ws}/invites"
    assert [owner.post(url, json={"username": "nobody-x"}).status_code for _ in range(3)] == [400, 400, 429]
    assert boss.post(url, json={"username": "nobody-x"}).status_code == 400  # another account has its own budget


def test_lookup_transport(monkeypatch):
    seen = []

    def fake_get(url, headers):
        seen.append((url, headers))
        name = url.rsplit("u=", 1)[1]
        if name == "known":
            return 200, {"sub": "acct-1", "username": "Known"}
        if name == "gone":
            return 404, {"error": "not_found"}
        return 401, {"error": "invalid_token"}

    monkeypatch.setattr(workspaces, "_get_json", fake_get)
    assert workspaces.lookup_with_token(ISSUER, "tok", "known") == {"sub": "acct-1", "username": "known"}
    assert seen[0] == (ISSUER + "/api/lookup/username?u=known", {"Authorization": "Bearer tok"})
    assert workspaces.lookup_with_token(ISSUER, "tok", "gone") is None
    with pytest.raises(workspaces.CloudLookupError, match="invalid_token"):
        workspaces.lookup_with_token(ISSUER, "tok", "other")
    # cloud sign-in off, or an inviter with no linked grant: the lookup cannot be made
    monkeypatch.delenv("GAMMA_CLOUD_ISSUER", raising=False)
    monkeypatch.setattr(cloud_auth, "_get_raw", lambda key: "")
    with pytest.raises(workspaces.CloudLookupError, match="not set up"):
        workspaces.cloud_lookup_username("known")
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    with pytest.raises(workspaces.CloudLookupError, match="Link your own Gamma Cloud account"):
        workspaces.cloud_lookup_username("known", by="pm_owner")
    monkeypatch.setattr(workspaces, "_cloud_access_token", lambda by: "tok-" + by)
    assert workspaces.lookup_cloud_username(" Known", by="pm_owner") == {"sub": "acct-1", "username": "known"}
    assert seen[-1][1] == {"Authorization": "Bearer tok-pm_owner"}


def test_lookup_unavailable_is_a_503(owner, boss, monkeypatch):
    # the real transport for an inviter whose account here is not linked to Gamma Cloud
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    ws = _shared(boss, "PM unavailable lab")
    r = owner.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-flo"})
    assert r.status_code == 503 and "Link your own Gamma Cloud account" in r.json()["detail"]


def test_invite_through_the_account_server(owner, boss, cloud, monkeypatch):  # noqa: F811
    # The whole way: the inviter links her own Gamma Cloud account, the
    # server turns that grant into an access token (a refresh, rotated and
    # saved), asks the account server for the username, and the invitation
    # waits for the subject.
    monkeypatch.setattr(workspaces, "_get_json", cloud.get_json)
    ws = _shared(boss, "PM wire lab")
    c = login("pm_owner", "pm-owner-pw1")
    cloud.person.update({"sub": "sub-pm-owner", "preferred_username": "pm-owner", "email": "pm-owner@example.org"})
    assert callback(c, start(c, link="1")).headers["location"] == "/"
    cloud_auth._access.clear()  # no access token cached: the lookup refreshes the grant
    cloud.directory["pm-hana"] = "sub-pm-hana"
    r = c.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-hana", "role": "viewer"})
    assert r.status_code == 200, r.text
    assert r.json()["invited"]["pending"]["subject"] == "sub-pm-hana"
    assert [(p["username"], p["role"]) for p in workspaces.pending_invites(ws)] == [("pm-hana", "viewer")]
    assert [f["grant_type"] for f in cloud.token_calls[-2:]] == ["authorization_code", "refresh_token"]
    assert cloud_auth.refresh_token_of("pm_owner") == "rt-1+"  # the rotated token was kept
    assert ("GET", "/api/lookup/username?u=pm-hana") in cloud.calls
    # an unknown username is the account server's 404: refused, nothing pending
    r = c.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-nobody"})
    assert r.status_code == 400 and "no Gamma Cloud account" in r.json()["detail"]


def test_callback_sign_in_claims_the_invitation(owner, boss, cloud, cloud_on, monkeypatch):  # noqa: F811
    # the whole browser round trip through the fake account server
    ws = _shared(boss, "PM callback lab")
    DIRECTORY["pm-gus"] = "sub-pm-gus"
    assert owner.post(f"/api/workspaces/{ws}/invites", json={"username": "pm-gus", "role": "viewer"}).status_code == 200
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    cloud.person.update({"sub": "sub-pm-gus", "preferred_username": "pm-gus", "email": "pm-gus@example.org"})
    c = browser()
    assert callback(c, start(c)).headers["location"] == "/"
    s = c.get("/api/session").json()
    assert s["user"] == "pm-gus"
    assert {w["id"]: w["role"] for w in s["workspaces"]}[ws] == "viewer"

"""Publishing a page to the free share host (gamma/publish.py,
routers/publish.py) and the mirror page filter under it
(gamma/sync_engine.py): both servers are this one process, as in
test_mirror.py — the "share host" is a workspace of the same app reached
through a cookie-less TestClient, the account server is the fake of
test_cloud_auth.py grown a /userinfo and a share host address."""

import io
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from fractional_indexing import generate_key_between

from conftest import login, make_user
from test_cloud_auth import ISSUER, FakeAccountServer

from gamma import cloud_auth, cloud_sync, publish, sync_engine, workspaces
from gamma.db import connect_users_db, ws_uploads_dir
from gamma.integrations import create_token, resolve_token

PDF = b"%PDF-1.4 publish test\n" + b"p" * 1500
HOST = "http://testserver"


def anonymous():
    from gamma.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _transport(monkeypatch):
    """Every call to "another server" goes through a cookie-less TestClient;
    rounds run only when a test (or the publish call) asks."""
    c = anonymous()

    def fetch(method, path, body, headers):
        r = c.request(method, path, content=body, headers=headers)
        return r.status_code, r.content

    monkeypatch.setattr(sync_engine, "default_fetch", fetch)
    monkeypatch.setattr(sync_engine, "sync_in_background", lambda ws: None)


class Cloud(FakeAccountServer):
    """The fake account server, plus what the share host asks of it
    (``/userinfo``) and what the publishing server reads from its discovery
    document (``gamma_share_host``)."""

    def __init__(self):
        super().__init__()
        self.people = {}          # subject -> the claims /userinfo answers
        self.share_host = HOST

    def http(self, url, data=None, headers=None, method=None, timeout=None):
        if url == ISSUER + "/userinfo":
            self.calls.append(("GET", "/userinfo"))
            subject = self._bearer(headers)
            return dict(self.people[subject])
        out = super().http(url, data=data, headers=headers, method=method, timeout=timeout)
        if url == ISSUER + "/.well-known/openid-configuration":
            out = {**out, "userinfo_endpoint": ISSUER + "/userinfo", "gamma_share_host": self.share_host}
        return out

    def person_of(self, subject, username, verified=True):
        self.people[subject] = {"sub": subject, "preferred_username": username, "email": f"{username}@example.org",
                                "email_verified": verified, "plan": "free"}

    def access_for(self, subject):
        return self._mint_access(subject)


@pytest.fixture
def cloud(monkeypatch):
    fake = Cloud()
    monkeypatch.setattr(cloud_auth, "_http", fake.http)
    monkeypatch.setattr(cloud_auth, "revoke_later", lambda tokens: None)
    monkeypatch.setattr(cloud_sync, "_background", lambda fn: fn())
    monkeypatch.setenv("GAMMA_CLOUD_ISSUER", ISSUER)
    monkeypatch.setenv("GAMMA_CLOUD_POLICY", "provision")
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "1")
    for var in ("GAMMA_CLOUD_CLIENT_ID", "GAMMA_CLOUD_CLIENT_SECRET", "GAMMA_CLOUD_ADMIN_SUBJECT"):
        monkeypatch.delenv(var, raising=False)
    cloud_auth._discovery_cache.clear()
    cloud_auth._jwks_cache.clear()
    cloud_auth._access.clear()
    yield fake
    cloud_auth._access.clear()
    cloud_auth._discovery_cache.clear()
    with connect_users_db() as conn:
        conn.execute("DELETE FROM identities")
        conn.commit()


def ops(client, page_id, batch, **params):
    r = client.post(f"/api/pages/{page_id}/ops", json={"client": "t", "ops": batch}, params=params)
    assert r.status_code == 200, r.text
    return r.json()


def insert(client, page_id, bid, content):
    return ops(client, page_id, [{"op": "insert", "id": bid, "parent": page_id,
                                  "position": generate_key_between(None, None), "content": content}])


def texts(client, page_id):
    r = client.get(f"/api/blocks/{page_id}/subtree")
    return None if r.status_code != 200 else {c["id"]: c["content"] for c in r.json()["block"]["children"]}


def page_ids(client):
    return {b["id"] for b in client.get("/api/blocks/root/children").json()["children"]}


def bound(client, ws):
    client.headers["X-Gamma-Workspace"] = ws
    return client


def sync(ws):
    status = sync_engine.sync_workspace(ws)
    assert not status.get("last_error"), status
    return status


def pdf_page(client, name="paper.pdf"):
    up = client.post("/api/uploads", files={"file": (name, io.BytesIO(PDF), "application/pdf")}).json()
    page = client.post(f"/api/blocks/by-doc/{up['doc_id']}", json={"default_title": name}).json()
    return page, up["doc_id"]


# --- the page filter ------------------------------------------------------------------

def test_a_filtered_mirror_moves_only_its_pages():
    make_user("pf_remote", "pw")
    make_user("pf_local", "pw")
    remote = bound(login("pf_remote", "pw"), workspaces.default_workspace("pf_remote"))
    local_ws = workspaces.default_workspace("pf_local")
    local = bound(login("pf_local", "pw"), local_ws)
    shown, doc = pdf_page(local)                                         # in the filter, with its PDF
    insert(local, shown["id"], "pf_a1", "published note")
    hidden = local.post("/api/pages", json={"title": "Private"}).json()  # outside it
    img = local.post("/api/upload-file", files={"file": ("pic.png", io.BytesIO(b"\x89PNG\r\n\x1a\n" + b"q" * 90),
                                                                   "image/png")}).json()
    insert(local, hidden["id"], "pf_h1", f"![pic]({img['url']})")
    theirs = remote.post("/api/pages", json={"title": "Remote only"}).json()
    # the same id on both sides, outside the filter: neither side's deletion may travel
    twin = local.post("/api/pages", json={"title": "Twin"}).json()
    assert remote.post("/api/pages", json={"id": twin["id"], "title": "Twin there"}).status_code == 200
    token = create_token("pf_remote", remote.headers["X-Gamma-Workspace"], "mirror", 30, scope="write")["token"]
    mirror = sync_engine.create_mirror("pf_local", HOST, token, workspace_id=local_ws, adopt="mine",
                                       page_filter=[shown["id"]])
    assert mirror["page_filter"] == [shown["id"]]
    assert local.get(f"/api/mirrors/{local_ws}").json()["page_filter"] == [shown["id"]]

    sync(local_ws)
    remote_ws = remote.headers["X-Gamma-Workspace"]
    assert texts(remote, shown["id"]) == {"pf_a1": "published note"}
    assert (ws_uploads_dir(remote_ws) / f"{doc}.pdf").read_bytes() == PDF
    assert hidden["id"] not in page_ids(remote) and theirs["id"] not in page_ids(local)
    assert not (ws_uploads_dir(remote_ws) / img["url"].rsplit("/", 1)[1]).exists()

    # edits outside the filter stay where they are, and do not mark the copy as waiting
    sync_engine._dirty.pop(local_ws, None)
    insert(local, hidden["id"], "pf_h2", "still private")
    assert not sync_engine.has_local_changes(local_ws)
    insert(remote, theirs["id"], "pf_r1", "remote only")
    insert(local, shown["id"], "pf_a2", "more")
    assert sync_engine.has_local_changes(local_ws)
    sync(local_ws)
    assert texts(remote, shown["id"]) == {"pf_a1": "published note", "pf_a2": "more"}
    assert hidden["id"] not in page_ids(remote) and theirs["id"] not in page_ids(local)

    # tombstones of pages outside it are ignored both ways
    assert remote.delete(f"/api/blocks/{twin['id']}").status_code == 200
    sync(local_ws)
    assert twin["id"] in page_ids(local)
    assert remote.post("/api/pages", json={"id": twin["id"], "title": "Twin there"}).status_code == 200
    assert local.delete(f"/api/blocks/{twin['id']}").status_code == 200
    sync(local_ws)
    assert twin["id"] in page_ids(remote)

    # a page added later is new: it goes over whole at the next round
    with sync_engine.round_lock(local_ws):
        sync_engine.filter_add(local_ws, hidden["id"])
    sync(local_ws)
    assert texts(remote, hidden["id"]) == {"pf_h1": f"![pic]({img['url']})", "pf_h2": "still private"}
    assert (ws_uploads_dir(remote_ws) / img["url"].rsplit("/", 1)[1]).exists()

    # a published page deleted there leaves the filter; the page here stays
    assert remote.delete(f"/api/blocks/{hidden['id']}").status_code == 200
    sync(local_ws)
    assert hidden["id"] in page_ids(local)
    assert sync_engine.get_mirror(local_ws)["page_filter"] == [shown["id"]]

    # a page deleted here goes there too, then leaves the filter
    assert local.delete(f"/api/blocks/{shown['id']}").status_code == 200
    sync(local_ws)
    assert shown["id"] not in page_ids(remote)
    sync(local_ws)
    assert sync_engine.get_mirror(local_ws)["page_filter"] == []
    # an empty filter asks nothing of the remote
    calls = []
    sync_engine.sync_workspace(local_ws, fetch=lambda *a: calls.append(a) or (500, b""))
    assert calls == []


# --- the exchange (the share host's side) -----------------------------------------------

def exchange(access, server="Laptop"):
    return anonymous().post("/api/auth/cloud/exchange", json={"server": server},
                            headers={"Authorization": f"Bearer {access}"} if access else {})


def test_the_exchange_provisions_and_mints_one_token_per_server(cloud, monkeypatch):
    cloud.person_of("sub-px_new", "px_new")
    access = cloud.access_for("sub-px_new")
    assert exchange("").status_code == 401
    assert exchange("at-unknown").status_code == 401
    first = exchange(access)
    assert first.status_code == 200, first.text
    out = first.json()
    ws = workspaces.default_workspace("px_new")
    assert out["username"] == "px_new" and out["workspace_id"] == ws and out["url"] == HOST
    assert resolve_token(out["token"]) == ("px_new", ws)
    with connect_users_db() as conn:
        assert conn.execute("SELECT password_hash FROM users WHERE username = 'px_new'").fetchone() == ("",)
        assert conn.execute("SELECT username FROM identities WHERE subject = 'sub-px_new'").fetchone() == ("px_new",)
    # the same calling server again: the old token dies, one row stays
    again = exchange(access).json()
    assert resolve_token(out["token"]) is None and resolve_token(again["token"]) == ("px_new", ws)
    other = exchange(access, "Desktop").json()
    assert resolve_token(again["token"]) and resolve_token(other["token"])
    with connect_users_db() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM integration_tokens WHERE username = 'px_new'")]
    assert sorted(names) == ["Published pages from Desktop", "Published pages from Laptop"]
    # an unconfirmed e-mail is refused, and no account is made
    cloud.person_of("sub-px_unverified", "px_unverified", verified=False)
    assert exchange(cloud.access_for("sub-px_unverified")).status_code == 403
    assert not workspaces.default_workspace("px_unverified")
    # the switch off: refused before the account server is asked
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "0")
    asked = len(cloud.calls)
    assert exchange(access).status_code == 403 and len(cloud.calls) == asked


def test_a_share_host_has_no_guest_and_no_account_directory(cloud):
    make_user("px_member", "pw")
    make_user("px_other", "pw")
    c = login("px_member", "pw")
    assert anonymous().post("/api/login-guest").status_code == 403
    assert anonymous().get("/api/server-config").json()["guest"] is False
    assert c.get("/api/accounts").json()["accounts"] == []
    assert c.get("/api/accounts?q=px_other").json()["accounts"] == [{"username": "px_other", "is_admin": False}]
    assert c.get("/api/accounts?q=px_oth").json()["accounts"] == []


# --- publishing (the publishing server's side) ------------------------------------------

def linked(cloud, username):
    """A password account here with a linked cloud identity holding a
    refresh token, and a second personal workspace to publish from (its
    default one is its workspace on "the share host")."""
    make_user(username, "pw")
    subject = f"sub-{username}"
    cloud.person_of(subject, username)
    refresh = f"rt-{username}"
    cloud.live[refresh] = subject
    with connect_users_db() as conn:
        cloud_auth.link(conn, username, {"sub": subject, "preferred_username": username}, refresh)
        conn.commit()
    local_ws = workspaces.create("Laptop library", username)["id"]
    return bound(login(username, "pw"), local_ws), local_ws


@pytest.fixture
def publishing(cloud, monkeypatch):
    # this one process is both servers: the publishing side must not see the share-host switch
    monkeypatch.setattr(publish, "publishing_blocked", lambda: False)
    return cloud


def test_publish_end_to_end(publishing, monkeypatch):
    local, local_ws = linked(publishing, "pb_ann")
    host_ws = workspaces.default_workspace("pb_ann")
    host = bound(login("pb_ann", "pw"), host_ws)
    page, doc = pdf_page(local)
    insert(local, page["id"], "pb_n1", "my note")
    private = local.post("/api/pages", json={"title": "Not published"}).json()
    assert local.get(f"/api/pages/{page['id']}/publish").json() == {"published": False, "can_publish": True}

    r = local.post(f"/api/pages/{page['id']}/publish")
    assert r.status_code == 200, r.text
    out = r.json()
    token = out["share"]["token"]
    assert out["url"] == f"{HOST}/?share={token}"
    assert out["share"]["audience"] == "anyone" and out["share"]["role"] == "view"
    assert out["mirror"]["ws"] == local_ws and out["mirror"]["page_filter"] == [page["id"]]
    assert out["mirror"]["status"]["last_sync"]
    mirror = sync_engine.get_mirror(local_ws)
    assert mirror["remote_ws"] == host_ws and mirror["remote_name"] and mirror["mode"] == "two-way"
    # a publication is not a clone: the workspace lists as an ordinary one that publishes
    listed = next(w for w in local.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == local_ws)
    assert listed["mirror_of"] == "" and listed["publishing"] is True
    # the page is on the share host under its id, with its PDF; nothing else went
    assert texts(host, page["id"]) == {"pb_n1": "my note"}
    assert (ws_uploads_dir(host_ws) / f"{doc}.pdf").read_bytes() == PDF
    assert private["id"] not in page_ids(host)
    # the link resolves there for anyone
    shared = anonymous().get(f"/api/share/{token}").json()
    assert shared["page_id"] == page["id"] and shared["workspace_id"] == host_ws and shared["doc_id"] == doc

    # publishing again changes the share, not the link; an edit through it comes back
    r = local.post(f"/api/pages/{page['id']}/publish", json={"role": "edit"})
    assert r.json()["share"]["token"] == token and r.json()["share"]["role"] == "edit"
    ops(anonymous(), page["id"], [{"op": "set", "id": "pb_n1", "content": "my note, edited there"}], share=token)
    sync(local_ws)
    assert texts(local, page["id"]) == {"pb_n1": "my note, edited there"}

    state = local.get(f"/api/pages/{page['id']}/publish").json()
    assert state["published"] is True and state["url"] == out["url"] and state["share"]["role"] == "edit"
    assert state["can_publish"] is True and state["status"]["last_sync"] and state["mirror"]["ws"] == local_ws

    # the share host out of reach: unpublishing changes nothing here
    working = sync_engine.default_fetch

    def offline(*_):
        raise sync_engine.RemoteError(0, "cannot reach the share host")

    monkeypatch.setattr(sync_engine, "default_fetch", offline)
    assert local.delete(f"/api/pages/{page['id']}/publish").status_code == 502
    assert sync_engine.get_mirror(local_ws)["page_filter"] == [page["id"]]
    monkeypatch.setattr(sync_engine, "default_fetch", working)

    # unpublish: the link dies, the copy there goes, the page here stays
    r = local.delete(f"/api/pages/{page['id']}/publish")
    assert r.status_code == 200, r.text
    assert r.json()["published"] is False and r.json()["mirror"]["page_filter"] == []
    # nothing published: the workspace no longer reads as publishing (no header pill)
    assert next(w for w in local.get("/api/workspaces/mine").json()["workspaces"] if w["id"] == local_ws)["publishing"] is False
    assert anonymous().get(f"/api/share/{token}").status_code == 404
    assert page["id"] not in page_ids(host)
    assert texts(local, page["id"]) == {"pb_n1": "my note, edited there"}
    sync(local_ws)                                   # the tombstone there never reaches the page here
    assert page["id"] in page_ids(local)
    assert local.get(f"/api/pages/{page['id']}/publish").json()["published"] is False
    assert local.delete(f"/api/pages/{page['id']}/publish").status_code == 409

    # published again: new there, a new link
    again = local.post(f"/api/pages/{page['id']}/publish").json()
    assert again["share"]["token"] != token
    assert texts(host, page["id"]) == {"pb_n1": "my note, edited there"}

    # the mirror's token revoked on the share host: the next publish exchanges a new one
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE username = 'pb_ann'")
        conn.commit()
    r = local.post(f"/api/pages/{page['id']}/publish", json={"audience": "users"})
    assert r.status_code == 200, r.text
    assert r.json()["share"]["token"] == again["share"]["token"] and r.json()["share"]["audience"] == "users"


def test_publish_needs_a_cloud_identity_and_a_home_of_its_own(publishing, monkeypatch):
    make_user("pb_plain", "pw")
    plain = bound(login("pb_plain", "pw"), workspaces.default_workspace("pb_plain"))
    page = plain.post("/api/pages", json={"title": "Draft"}).json()
    r = plain.post(f"/api/pages/{page['id']}/publish")
    assert r.status_code == 409 and r.json()["detail"] == "Sign in with Gamma Cloud to publish."
    assert plain.get(f"/api/pages/{page['id']}/publish").json()["can_publish"] is False
    # a workspace that is a copy of another server publishes from there
    local, local_ws = linked(publishing, "pb_lab")
    make_user("pb_nas", "pw")
    token = create_token("pb_nas", workspaces.default_workspace("pb_nas"), "mirror", 30, scope="write")["token"]
    sync_engine.create_mirror("pb_lab", HOST, token, workspace_id=local_ws)
    publishing.share_host = "https://share.example.org"
    cloud_auth._discovery_cache.clear()
    page = local.post("/api/pages", json={"title": "Lab notes"}).json()
    r = local.post(f"/api/pages/{page['id']}/publish")
    assert r.status_code == 409 and "publish from there" in r.json()["detail"]
    state = local.get(f"/api/pages/{page['id']}/publish").json()
    assert state["can_publish"] is False and "publish from there" in state["reason"]
    assert state["published"] is False and "mirror" not in state
    # a share host publishes nothing itself
    monkeypatch.setattr(publish, "publishing_blocked", lambda: True)
    assert local.post(f"/api/pages/{page['id']}/publish").status_code == 409
    # and a block is not a page
    insert(local, page["id"], "pb_blk", "x")
    monkeypatch.setattr(publish, "publishing_blocked", lambda: False)
    assert local.post("/api/pages/pb_blk/publish").status_code == 400


# --- the plan's page cap (the share host) ----------------------------------------------

CAP_DETAIL = "Free plan: up to 5 published pages. Unpublish one, or upgrade your Gamma Cloud plan."


def test_the_share_host_caps_published_pages_by_plan(publishing, monkeypatch):
    local, local_ws = linked(publishing, "pbcap")
    host_ws = workspaces.default_workspace("pbcap")
    host = bound(login("pbcap", "pw"), host_ws)
    pages = [local.post("/api/pages", json={"title": f"Paper {n}"}).json() for n in range(7)]
    for page in pages[:5]:
        r = local.post(f"/api/pages/{page['id']}/publish")
        assert r.status_code == 200, r.text
    assert len(page_ids(host)) == 5
    # the local workspace is not the one the exchange publishes into: no cap there
    assert len(page_ids(local)) == 7

    # the state reads the count there
    state = local.get(f"/api/pages/{pages[5]['id']}/publish").json()
    assert state["limit"] == {"used": 5, "max": 5, "plan": "free"} and state["can_publish"] is True
    assert anonymous().get("/api/publish/limit").status_code == 401
    assert host.get("/api/publish/limit").json() == {"used": 5, "max": 5, "plan": "free"}

    # the sixth: 409 here with the share host's words and the count; nothing moved, nothing left in the filter
    r = local.post(f"/api/pages/{pages[5]['id']}/publish")
    assert r.status_code == 409, r.text
    assert r.json() == {"detail": CAP_DETAIL, "limit": {"used": 5, "max": 5, "plan": "free"}}
    assert pages[5]["id"] not in sync_engine.get_mirror(local_ws)["page_filter"]
    assert pages[5]["id"] not in page_ids(host)
    # the count unreadable beforehand (a share host that fills up meanwhile): the round's 402 says the same
    read_limit = publish._read_limit
    monkeypatch.setattr(publish, "_read_limit", lambda remote: None)
    r = local.post(f"/api/pages/{pages[5]['id']}/publish")
    assert r.status_code == 409 and r.json()["detail"] == CAP_DETAIL
    assert pages[5]["id"] not in sync_engine.get_mirror(local_ws)["page_filter"]
    assert not sync_engine.get_mirror(local_ws)["status"]["last_error"]
    monkeypatch.setattr(publish, "_read_limit", read_limit)
    # ...and 402 on the share host itself, for any new page
    r = host.post("/api/pages", json={"title": "One more"})
    assert r.status_code == 402
    assert r.json() == {"detail": CAP_DETAIL, "limit": 5, "used": 5, "plan": "free"}
    # a page that is already there answers as before (a round re-creating it tolerates the 409)
    assert host.post("/api/pages", json={"id": pages[0]["id"], "title": "x"}).status_code == 409
    # the cap never stops a round of a page already published
    insert(local, pages[0]["id"], "pbcap_n1", "an edit at the cap")
    sync(local_ws)
    assert texts(host, pages[0]["id"]) == {"pbcap_n1": "an edit at the cap"}

    # unpublishing one frees a slot
    assert local.delete(f"/api/pages/{pages[1]['id']}/publish").status_code == 200
    r = local.post(f"/api/pages/{pages[5]['id']}/publish")
    assert r.status_code == 200, r.text
    assert len(page_ids(host)) == 5

    # an upgrade counts at the next publish: the full workspace is exchanged again, the plan read afresh
    publishing.people["sub-pbcap"]["plan"] = "plus"
    r = local.post(f"/api/pages/{pages[6]['id']}/publish")
    assert r.status_code == 200, r.text
    assert len(page_ids(host)) == 6
    assert host.get("/api/publish/limit").json() == {"used": 6, "max": None, "plan": "plus"}
    assert host.post("/api/pages", json={"title": "Seventh"}).status_code == 200

    # GAMMA_FREE_PAGE_LIMIT sets the free plan's number
    publishing.people["sub-pbcap"]["plan"] = "free"
    with connect_users_db() as conn:
        cloud_auth.link(conn, "pbcap", publishing.people["sub-pbcap"])
        conn.commit()
    monkeypatch.setenv("GAMMA_FREE_PAGE_LIMIT", "10")
    assert host.get("/api/publish/limit").json() == {"used": 7, "max": 10, "plan": "free"}
    monkeypatch.setenv("GAMMA_FREE_PAGE_LIMIT", "0")
    assert host.get("/api/publish/limit").json()["max"] is None
    monkeypatch.delenv("GAMMA_FREE_PAGE_LIMIT")

    # a server that is not a share host ignores the table
    monkeypatch.setenv("GAMMA_CLOUD_SHARE_HOST", "0")
    assert host.post("/api/pages", json={"title": "Not a share host"}).status_code == 200
    assert host.get("/api/publish/limit").status_code == 404


# --- public addresses: slugs and page hosts ------------------------------------------------

SLUGS = json.loads((Path(__file__).resolve().parents[2] / "tests" / "shared" / "slug.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("case", SLUGS["slug"], ids=[c["note"] for c in SLUGS["slug"]])
def test_slug(case):
    assert publish.slug(case["input"]) == case["output"]


def test_the_page_host_pattern(monkeypatch):
    pattern = "{username}-pages.gammapdf.com"
    assert publish.page_host_user(pattern, "tim-pages.gammapdf.com") == "tim"
    assert publish.page_host_user(pattern, "Tim-Pages.GammaPDF.com:8443") == "tim"
    assert publish.page_host_user(pattern, "a-pages-pages.gammapdf.com") == "a-pages"
    for other in ("api.gammapdf.com", "x.tim-pages.gammapdf.com", "-pages.gammapdf.com", "tim-pages.gammapdf.com.evil"):
        assert publish.page_host_user(pattern, other) == "", other
    assert publish.public_url("https://share.gammapdf.com", pattern, "Tim", "Élan vital", "p-1") \
        == "https://tim-pages.gammapdf.com/elan-vital-p-1"
    assert publish.public_url("http://127.0.0.1:9001", pattern, "tim", "量子", "abc") \
        == "http://tim-pages.gammapdf.com:9001/abc"
    assert publish.public_url("https://share.gammapdf.com", pattern, "tim_x", "t", "abc") == ""
    assert publish.public_url("https://share.gammapdf.com", "", "tim", "t", "abc") == ""
    # checked at startup
    for good in ("", "{username}-pages.gammapdf.com", "{username}.pages.example.org"):
        monkeypatch.setenv("GAMMA_PAGE_HOST", good)
        publish.check_config()
    for bad in ("pages.gammapdf.com", "{username}-{username}.x.org", "https://{username}.x.org", "{username}.x.org:8443",
                "{username}_pages.x.org", "{username}"):
        monkeypatch.setenv("GAMMA_PAGE_HOST", bad)
        with pytest.raises(ValueError):
            publish.check_config()
    monkeypatch.delenv("GAMMA_PAGE_HOST")
    monkeypatch.setenv("GAMMA_FREE_PAGE_LIMIT", "five")
    with pytest.raises(ValueError):
        publish.check_config()


def resolve(host, path):
    return anonymous().get("/api/pages/resolve-public", params={"host": host, "path": path})


def test_the_resolver_opens_a_shared_page_by_its_pretty_address(monkeypatch):
    monkeypatch.setenv("GAMMA_PAGE_HOST", "{username}-pages.example.org")
    ws = make_user("pbresolve", "pw")
    make_user("pbresolve2", "pw")
    owner = bound(login("pbresolve", "pw"), ws)
    page = owner.post("/api/pages", json={"title": "My paper"}).json()
    dashed = owner.post("/api/pages", json={"id": "ab-cd-ef", "title": "Dashed id"}).json()
    unshared = owner.post("/api/pages", json={"title": "Private"}).json()
    insert(owner, page["id"], "pbresolve_b1", "a block")
    token = owner.post(f"/api/share/{page['id']}").json()["token"]
    dashed_token = owner.post(f"/api/share/{dashed['id']}").json()["token"]
    assert anonymous().get("/api/server-config").json()["page_host"] == "{username}-pages.example.org"

    want = {"share": token, "page_id": page["id"]}
    host = "pbresolve-pages.example.org"
    assert resolve(host, f"/my-paper-{page['id']}").json() == want
    assert resolve(host, f"/{page['id']}").json() == want                   # no slug
    assert resolve(host, f"/renamed-since-{page['id']}/").json() == want    # the slug is decoration
    assert resolve("PBResolve-Pages.example.org:8443", f"/x-{page['id']}").json() == want
    assert resolve(host, "/dashed-id-ab-cd-ef").json() == {"share": dashed_token, "page_id": "ab-cd-ef"}
    assert resolve(host, "/ab-cd-ef").json()["page_id"] == "ab-cd-ef"
    for miss_host, path in (
        ("pbresolve2-pages.example.org", f"/my-paper-{page['id']}"),   # another account's host
        ("nobody-pages.example.org", f"/{page['id']}"),                # no such account
        ("pbresolve.example.org", f"/{page['id']}"),                   # not a page host
        (host, f"/private-{unshared['id']}"),                          # not shared
        (host, "/pbresolve_b1"),                                       # not a page
        (host, "/"), (host, f"/a/{page['id']}"),
    ):
        assert resolve(miss_host, path).status_code == 404, (miss_host, path)
    # only the account's default personal workspace is served
    other_ws = workspaces.create("Second", "pbresolve")["id"]
    second = bound(login("pbresolve", "pw"), other_ws)
    other = second.post("/api/pages", json={"title": "Elsewhere"}).json()
    second.post(f"/api/share/{other['id']}")
    assert resolve(host, f"/elsewhere-{other['id']}").status_code == 404

    # the share's own audience still rules: signed-in only is the share view's 401
    owner.put(f"/api/share-settings/{page['id']}", json={"audience": "users"})
    assert resolve(host, f"/my-paper-{page['id']}").json() == want
    assert anonymous().get(f"/api/share/{token}").status_code == 401
    # the page host off: nothing resolves
    monkeypatch.delenv("GAMMA_PAGE_HOST")
    assert resolve(host, f"/my-paper-{page['id']}").status_code == 404
    assert anonymous().get("/api/server-config").json()["page_host"] == ""


def test_publish_answers_carry_the_public_address(publishing, monkeypatch):
    monkeypatch.setenv("GAMMA_PAGE_HOST", "{username}-pages.example.org")
    local, local_ws = linked(publishing, "pbpretty")
    page = local.post("/api/pages", json={"title": "Café Notes: Part 1"}).json()
    out = local.post(f"/api/pages/{page['id']}/publish").json()
    token = out["share"]["token"]
    assert out["url"] == f"{HOST}/?share={token}"
    assert out["public_url"] == f"http://pbpretty-pages.example.org/cafe-notes-part-1-{page['id']}"
    state = local.get(f"/api/pages/{page['id']}/publish").json()
    assert state["public_url"] == out["public_url"] and state["url"] == out["url"]
    assert resolve("pbpretty-pages.example.org", f"/cafe-notes-part-1-{page['id']}").json() \
        == {"share": token, "page_id": page["id"]}
    # without page hosts the public address is the token link
    monkeypatch.delenv("GAMMA_PAGE_HOST")
    assert local.get(f"/api/pages/{page['id']}/publish").json()["public_url"] == out["url"]

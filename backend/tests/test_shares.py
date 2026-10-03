"""Share links are keyed by page, not by PDF — with per-share permissions.

Every page is a root block; a paper's PDF is just a doc_id/source_url on it.
Shares follow the block model: a token names one page (in one workspace) and
confines reads (and edit writes) to that page's subtree and assets — so note
pages without any PDF share exactly like papers. Rows minted by the old
doc-keyed model were re-keyed once by the schema migration
(tests/test_migrations.py covers it).

Permissions: audience (anyone / signed-in users / a list of usernames) gates
who may open the link; role (view / edit) says what they may do. Editing is
scoped to the page's block tree and needs a signed-in editor.
"""


import bcrypt
import pytest
from fastapi.testclient import TestClient

from conftest import account_of, login, make_folder, make_user, make_page
from gamma.seed import insert_account


@pytest.fixture(scope="module")
def bob():
    make_user("bob_share", "bobpw1234567")
    return login("bob_share", "bobpw1234567")


@pytest.fixture
def anon():
    from gamma.app import app
    return TestClient(app)  # no cookies


def _child(client, parent_id, content):
    r = client.post("/api/blocks", json={"parent_id": parent_id, "content": content})
    assert r.status_code == 200, r.text
    return r.json()


def test_note_page_without_pdf_is_shareable(bob, anon):
    page = make_page(bob, "Plain notes")            # no doc_id, no source_url
    note = _child(bob, page["id"], "a private thought")
    other = make_page(bob, "Another note page")

    r = bob.post(f"/api/share/{page['id']}")
    assert r.status_code == 200, r.text
    token = r.json()["token"]

    resolved = anon.get(f"/api/share/{token}")
    assert resolved.status_code == 200
    assert resolved.json()["page_id"] == page["id"]
    assert resolved.json()["doc_id"] == ""
    assert resolved.json()["username"] == "bob_share"

    q = {"share": token}
    assert anon.get(f"/api/blocks/{page['id']}", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{page['id']}/children", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{note['id']}", params=q).status_code == 200
    tree = anon.get(f"/api/blocks/{page['id']}/subtree", params=q).json()["block"]
    assert [c["content"] for c in tree["children"]] == ["a private thought"]
    # the read-only export path works for a note page too
    assert anon.get(f"/api/pages/{page['id']}/export", params=q).status_code == 200

    # nothing beyond the page
    assert anon.get(f"/api/blocks/{other['id']}", params=q).status_code == 403
    assert anon.get(f"/api/blocks/{other['id']}/children", params=q).status_code == 403
    assert anon.get("/api/blocks/root/children", params=q).status_code == 403
    assert anon.get(f"/api/pages/{other['id']}/export", params=q).status_code == 403
    # and no token at all is still a 401
    assert anon.get(f"/api/blocks/{page['id']}").status_code == 401


def test_only_page_roots_can_be_shared(bob):
    page = make_page(bob, "Root only")
    child = _child(bob, page["id"], "child block")
    assert bob.post(f"/api/share/{child['id']}").status_code == 400
    assert bob.post("/api/share/does_not_exist").status_code == 404


def test_shared_chat_is_scoped_and_read_only(bob, carol, anon):
    page = make_page(bob, "Shared conversation")
    other = make_page(bob, "Private conversation")
    folder = make_folder(bob, "private/folder")
    saved = {"messages": [{"role": "user", "text": "Explain this page"},
                           {"role": "assistant", "text": "Saved answer"}],
             "title": "Page discussion"}
    assert bob.put(f"/api/chats/{page['id']}", json=saved).status_code == 200
    token = bob.post(f"/api/share/{page['id']}").json()["token"]
    q = {"share": token}

    for viewer in (anon, carol, bob):
        assert viewer.get(f"/api/chats/{page['id']}", params=q).json() == saved
        for bucket in (other["id"], "home", folder):
            assert viewer.get(f"/api/chats/{bucket}", params=q).status_code == 403
    assert anon.get(f"/api/chats/{page['id']}").status_code == 401
    assert anon.get(f"/api/chats/{page['id']}", params={"share": "invalid"}).status_code == 401

    # Even a page-edit share, or its owner, cannot change chat via the link.
    bob.put(f"/api/share-settings/{page['id']}", json={"audience": "users", "role": "edit"})
    for viewer in (anon, carol, bob):
        for method, path, body in (
            ("PUT", f"/api/chats/{page['id']}", {"messages": []}),
            ("DELETE", f"/api/chats/{page['id']}", None),
            ("DELETE", f"/api/folders/{folder}", None),
            ("POST", "/api/chat-history/archive", {"bucket": page["id"]}),
            ("POST", "/api/chat-history/entry/open", {"bucket": page["id"]}),
            ("PUT", "/api/chat-history/entry", {"title": "Changed"}),
            ("DELETE", "/api/chat-history/entry", None),
        ):
            assert viewer.request(method, path, params=q, json=body).status_code == 403
    own = bob.get(f"/api/chats/{page['id']}").json()
    assert {k: v for k, v in own.items() if k != "updated_at"} == saved  # a member's read carries the version
    assert anon.get(f"/api/chats/{page['id']}", params=q).status_code == 401
    bob.put(f"/api/share-settings/{page['id']}", json={"audience": "list", "role": "view"})
    assert carol.get(f"/api/chats/{page['id']}", params=q).status_code == 403
    bob.delete(f"/api/share-settings/{page['id']}")
    assert anon.get(f"/api/chats/{page['id']}", params=q).status_code == 401


def test_share_token_is_stable_per_page(bob):
    page = make_page(bob, "Stable link")
    t1 = bob.post(f"/api/share/{page['id']}").json()["token"]
    t2 = bob.post(f"/api/share/{page['id']}").json()["token"]
    assert t1 == t2
    other = make_page(bob, "Different page")
    assert bob.post(f"/api/share/{other['id']}").json()["token"] != t1


def test_a_share_token_names_its_workspace(bob, anon, monkeypatch):
    """A token is <workspace id>.<secret> (db.share_token_workspace), so a
    router can place share traffic by its prefix without a lookup. One of
    another shape (the bare secret a link minted before schema version 32
    carries), or whose prefix is not its share's workspace, is refused like
    an unknown token and counted as a miss, on every path that reads one."""
    from starlette.websockets import WebSocketDisconnect
    from gamma import auth, ratelimit
    from gamma.app import app
    from gamma.db import connect_users_db, page_now, share_token_workspace
    page = make_page(bob, "Placed by its prefix")
    token = _share(bob, page["id"])["token"]
    ws = anon.get(f"/api/share/{token}").json()["workspace_id"]
    prefix, _dot, secret = token.partition(".")
    assert prefix == ws and share_token_workspace(token) == ws and len(secret) == 16
    assert share_token_workspace(_folder_share(bob, make_folder(bob, "placed"))["token"]) == ws
    assert [share_token_workspace(t) for t in (secret, f".{secret}", f"{ws}.", f"a/b.{secret}", None)] == [None] * 5
    # a row whose token names another workspace than its own (none is minted so)
    stray = make_page(bob, "Stray prefix")
    with connect_users_db() as conn:
        conn.execute("INSERT INTO shares (token, workspace_id, page_id, created_by, created_at) VALUES (?, ?, ?, '', ?)",
                     (f"elsewhere.{secret}x", ws, stray["id"], page_now()))

    monkeypatch.setattr(auth, "SHARE_MISSES_PER_5_MIN", 5)
    ratelimit._buckets.clear()
    try:
        assert anon.get(f"/api/share/{secret}").status_code == 404  # an old link's bare token
        assert anon.get(f"/api/share/elsewhere.{secret}").status_code == 404  # another workspace's prefix
        assert anon.get(f"/api/share/elsewhere.{secret}x").status_code == 404  # the prefix is not the row's
        assert anon.get(f"/api/blocks/{page['id']}", params={"share": secret}).status_code == 401
        with TestClient(app) as sock_client, pytest.raises(WebSocketDisconnect):
            with sock_client.websocket_connect(f"/api/ws/page/{page['id']}?share={secret}"):
                pass
        # each of the five was a miss (the socket's too): the sixth is over the limit
        assert anon.get(f"/api/share/elsewhere.{secret}").status_code == 429
    finally:
        ratelimit._buckets.clear()
    assert anon.get(f"/api/share/{token}").status_code == 200
    assert anon.get(f"/api/blocks/{page['id']}", params={"share": token}).status_code == 200


def test_share_requires_a_session(anon, bob):
    page = make_page(bob, "No anon sharing")
    assert anon.post(f"/api/share/{page['id']}").status_code == 401


def test_share_reads_only_assets_its_page_references(bob, anon):
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    up = bob.post("/api/upload-image", files={"file": ("a.png", png, "image/png")})
    assert up.status_code == 200, up.text
    url = up.json()["url"]

    with_img = make_page(bob, "Has the image")
    _child(bob, with_img["id"], f"look: ![fig]({url})")
    without = make_page(bob, "No image here")

    t_with = bob.post(f"/api/share/{with_img['id']}").json()["token"]
    t_without = bob.post(f"/api/share/{without['id']}").json()["token"]
    assert anon.get(url, params={"share": t_with}).status_code == 200
    assert anon.get(url, params={"share": t_without}).status_code == 403
    assert anon.get(url).status_code == 401


def test_upload_share_refusals_match_the_other_read_endpoints(bob, carol, anon):
    """A wrong or unknown share token on an asset gets auth._share_denied's
    statuses like every other read endpoint: 403 for a signed-in visitor,
    401 for an anonymous one (signing in could help)."""
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    url = bob.post("/api/upload-image", files={"file": ("b.png", png, "image/png")}).json()["url"]
    page = make_page(bob, "Without the image")
    token = bob.post(f"/api/share/{page['id']}").json()["token"]
    assert carol.get(url, params={"share": token}).status_code == 403     # not this page's asset
    assert carol.get(url, params={"share": "no-such-token"}).status_code == 403
    assert anon.get(url, params={"share": "no-such-token"}).status_code == 401
    assert carol.get(url).status_code == 404                               # her own uploads: absent


def test_pdf_proxy_only_serves_the_pages_own_source(bob, anon):
    paper = make_page(bob, "Remote paper",
                      properties={"doc_id": "remote_doc", "source_url": "https://example.com/paper.pdf"})
    notes = make_page(bob, "Just notes")
    t_paper = bob.post(f"/api/share/{paper['id']}").json()["token"]
    t_notes = bob.post(f"/api/share/{notes['id']}").json()["token"]
    other = {"source_url": "https://example.com/other.pdf"}
    assert anon.get("/api/pdf", params={**other, "share": t_paper}).status_code == 403
    assert anon.get("/api/pdf", params={**other, "share": t_notes}).status_code == 403
    assert anon.get("/api/pdf", params={"source_url": "https://example.com/paper.pdf",
                                         "share": t_notes}).status_code == 403

    # a copy the proxy cached earlier answers with a redirect to the upload —
    # which must keep the token, or the browser's follow-up lands in the
    # visitor's own (missing) library
    import hashlib
    from conftest import workspace_of
    from gamma.db import ws_uploads_dir
    from gamma.storage import DIGEST_CHARS
    cached = hashlib.sha256(b"https://example.com/paper.pdf").hexdigest()[:DIGEST_CHARS]
    uploads = ws_uploads_dir(workspace_of("bob_share"))
    uploads.mkdir(parents=True, exist_ok=True)
    (uploads / f"{cached}.pdf").write_bytes(b"%PDF-1.4 cached copy")
    bob.put(f"/api/blocks/{paper['id']}", json={"properties": {"doc_id": cached}})
    r = anon.get("/api/pdf", params={"source_url": "https://example.com/paper.pdf", "share": t_paper},
                 follow_redirects=False)
    assert r.status_code == 302, r.text
    assert r.headers["location"] == f"/api/uploads/{cached}.pdf?share={t_paper}"
    assert anon.get(r.headers["location"]).status_code == 200
    assert anon.get(f"/api/uploads/{cached}.pdf").status_code == 401


# --- permissions: who may open, what they may do -----------------------------

@pytest.fixture(scope="module")
def carol():
    make_user("carol_share", "carolpw123456")
    return login("carol_share", "carolpw123456")


@pytest.fixture(scope="module")
def dave():
    make_user("dave_share", "davepw1234567")
    return login("dave_share", "davepw1234567")


def _share(owner, page_id, **settings):
    r = owner.post(f"/api/share/{page_id}")
    assert r.status_code == 200, r.text
    if settings:
        r = owner.put(f"/api/share-settings/{page_id}", json=settings)
        assert r.status_code == 200, r.text
    return r.json()


def test_share_settings_roundtrip(bob, carol, anon):
    page = make_page(bob, "Settings page")
    assert bob.get(f"/api/share-settings/{page['id']}").json()["token"] is None

    created = bob.post(f"/api/share/{page['id']}").json()
    assert (created["audience"], created["role"], created["users"]) == ("anyone", "view", [])
    token = created["token"]
    assert bob.get(f"/api/share-settings/{page['id']}").json()["token"] == token

    # any audience may edit — anyone-with-the-link included (the link is the key)
    r = bob.put(f"/api/share-settings/{page['id']}", json={"role": "edit"})
    assert r.status_code == 200 and (r.json()["audience"], r.json()["role"]) == ("anyone", "edit")
    r = bob.put(f"/api/share-settings/{page['id']}", json={"audience": "users", "role": "edit"})
    assert r.status_code == 200 and r.json()["role"] == "edit"
    assert r.json()["token"] == token  # the link itself never changes
    # unknown names are refused; the owner and duplicates are dropped
    r = bob.put(f"/api/share-settings/{page['id']}", json={"audience": "list", "users": ["nobody_here"]})
    assert r.status_code == 400 and "nobody_here" in r.json()["detail"]
    r = bob.put(f"/api/share-settings/{page['id']}",
                json={"audience": "list", "users": [" carol_share ", "bob_share", "carol_share"]})
    assert r.status_code == 200 and r.json()["users"] == [{"name": "carol_share", "role": "view"}]
    assert bob.put(f"/api/share-settings/{page['id']}",
                   json={"users": [{"name": "carol_share", "role": "owner"}]}).status_code == 400
    assert bob.put(f"/api/share-settings/{page['id']}", json={"audience": "everyone"}).status_code == 400
    assert bob.put(f"/api/share-settings/{page['id']}", json={"role": "admin"}).status_code == 400
    # only the owner manages the share
    assert carol.get(f"/api/share-settings/{page['id']}").status_code == 404
    assert carol.put(f"/api/share-settings/{page['id']}", json={"role": "view"}).status_code == 404
    assert anon.put(f"/api/share-settings/{page['id']}", json={"role": "view"}).status_code == 401

    # stop sharing kills the token; sharing again mints a new one
    assert bob.delete(f"/api/share-settings/{page['id']}").json()["removed"] == 1
    assert anon.get(f"/api/share/{token}").status_code == 404
    assert bob.get(f"/api/share-settings/{page['id']}").json()["token"] is None
    assert bob.post(f"/api/share/{page['id']}").json()["token"] != token


def test_a_new_share_takes_the_access_it_is_created_with(bob, carol, anon):
    """The Share popover creates a share with the audience picked, or Invited
    only with the first person invited — never anyone-with-the-link by
    default. The body applies to a NEW link only."""
    page = make_page(bob, "Invite first")
    r = bob.post(f"/api/share/{page['id']}", json={"audience": "list", "users": [{"name": "carol_share", "role": "view"}]})
    assert r.status_code == 200, r.text
    created = r.json()
    assert (created["audience"], created["role"], created["users"]) == (
        "list", "view", [{"name": "carol_share", "role": "view"}])
    assert anon.get(f"/api/share/{created['token']}").status_code == 401
    assert carol.get(f"/api/share/{created['token']}").status_code == 200
    # an existing link is returned unchanged, whatever the body says
    again = bob.post(f"/api/share/{page['id']}", json={"audience": "anyone"}).json()
    assert (again["token"], again["audience"]) == (created["token"], "list")
    # validated like a PUT: nothing is created from a bad body
    other = make_page(bob, "Bad first share")
    assert bob.post(f"/api/share/{other['id']}", json={"audience": "list", "users": ["nobody_here"]}).status_code == 400
    assert bob.post(f"/api/share/{other['id']}", json={"audience": "everyone"}).status_code == 400
    assert bob.get(f"/api/share-settings/{other['id']}").json()["token"] is None
    r = bob.post(f"/api/share/{other['id']}", json={"audience": "users"})
    assert (r.json()["audience"], r.json()["role"]) == ("users", "view")


def test_signed_in_visitor_reads_the_owners_page(bob, carol):
    """A ?share= token decides WHOSE data is read — a signed-in visitor sees
    the owner's page, not a lookup in their own library."""
    page = make_page(bob, "Bob's public page")
    token = _share(bob, page["id"])["token"]
    r = carol.get(f"/api/blocks/{page['id']}", params={"share": token})
    assert r.status_code == 200 and r.json()["content"] == "Bob's public page"
    # ...and still only that page
    other = make_page(bob, "Bob's other page")
    assert carol.get(f"/api/blocks/{other['id']}", params={"share": token}).status_code == 403
    # an unknown token never falls back to the visitor's own account
    assert carol.get(f"/api/blocks/{page['id']}", params={"share": "bogus-token"}).status_code == 403


def test_users_audience_needs_a_real_account(bob, carol, guest, anon):
    page = make_page(bob, "Members only")
    token = _share(bob, page["id"], audience="users")["token"]
    assert anon.get(f"/api/share/{token}").status_code == 401
    assert anon.get(f"/api/blocks/{page['id']}", params={"share": token}).status_code == 401
    assert guest.get(f"/api/share/{token}").status_code == 401
    r = carol.get(f"/api/share/{token}")
    assert r.status_code == 200
    assert (r.json()["can_edit"], r.json()["viewer"]) == (False, "carol_share")
    assert carol.get(f"/api/blocks/{page['id']}/subtree", params={"share": token}).status_code == 200
    # the owner always gets in — and may edit
    assert bob.get(f"/api/share/{token}").json()["can_edit"] is True


def test_list_audience_admits_only_named_users(bob, carol, dave, anon):
    page = make_page(bob, "For Carol")
    token = _share(bob, page["id"], audience="list", users=["carol_share"])["token"]
    assert carol.get(f"/api/share/{token}").status_code == 200
    assert carol.get(f"/api/blocks/{page['id']}", params={"share": token}).status_code == 200
    r = dave.get(f"/api/share/{token}")
    assert r.status_code == 403 and "specific people" in r.json()["detail"]
    assert dave.get(f"/api/blocks/{page['id']}", params={"share": token}).status_code == 403
    assert anon.get(f"/api/share/{token}").status_code == 401


def test_invited_people_get_in_with_their_own_role(bob, carol, dave, anon):
    """Invitations are additive to general access: an invited person opens the
    page whatever the audience is, with the role on their invitation."""
    page = make_page(bob, "Invite-only draft")
    token = _share(bob, page["id"], audience="anyone",
                   users=[{"name": "carol_share", "role": "edit"}])["token"]
    assert carol.get(f"/api/share/{token}").json()["can_edit"] is True
    assert dave.get(f"/api/share/{token}").json()["can_edit"] is False   # general access: view
    assert anon.get(f"/api/share/{token}").json()["can_edit"] is False
    # closing general access keeps the invitation working
    bob.put(f"/api/share-settings/{page['id']}", json={"audience": "list"})
    assert carol.get(f"/api/share/{token}").json()["can_edit"] is True
    assert carol.put(f"/api/blocks/{page['id']}/children", params={"share": token},
                     json={"blocks": [{"id": "inv1", "content": "hi", "properties": {}, "children": []}]}).status_code == 200
    assert dave.get(f"/api/share/{token}").status_code == 403
    # an invited person's role can be lowered
    bob.put(f"/api/share-settings/{page['id']}", json={"users": [{"name": "carol_share", "role": "view"}]})
    assert carol.get(f"/api/share/{token}").json()["can_edit"] is False
    assert carol.put(f"/api/blocks/{page['id']}/children", params={"share": token},
                     json={"blocks": []}).status_code == 403


def test_invitations_name_the_account_not_its_name(bob, anon):
    """An invitation is a share_users row by account id: the dialog shows the
    username, a deleted account's invitation goes with it (an account made
    later under the same name is a stranger to the link), and stopping the
    share drops its rows."""
    from gamma import workspaces
    from gamma.db import connect_users_db

    make_user("erin_share", "erinpw1234567")
    erin_id = account_of("erin_share")
    page = make_page(bob, "For Erin")
    token = _share(bob, page["id"], audience="list", users=[{"name": "erin_share", "role": "edit"}])["token"]
    with connect_users_db() as conn:
        assert conn.execute("SELECT user_id, role FROM share_users WHERE token = ?", (token,)).fetchall() == [
            (erin_id, "edit")]
    assert bob.get(f"/api/share-settings/{page['id']}").json()["users"] == [{"name": "erin_share", "role": "edit"}]

    workspaces.delete_account(erin_id)
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM share_users WHERE token = ?", (token,)).fetchone()[0] == 0
    assert bob.get(f"/api/share-settings/{page['id']}").json()["users"] == []
    with connect_users_db() as conn:  # the name again, as a new account
        insert_account(conn, "erin_share", bcrypt.hashpw(b"erinpw1234567", bcrypt.gensalt()).decode())
    workspaces.ensure_personal(account_of("erin_share"))
    assert account_of("erin_share") != erin_id
    assert login("erin_share", "erinpw1234567").get(f"/api/share/{token}").status_code == 403

    bob.put(f"/api/share-settings/{page['id']}", json={"users": ["erin_share"]})
    with connect_users_db() as conn:
        assert conn.execute("SELECT user_id FROM share_users WHERE token = ?", (token,)).fetchall() == [
            (account_of("erin_share"),)]
    assert bob.delete(f"/api/share-settings/{page['id']}").status_code == 200
    with connect_users_db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM share_users WHERE token = ?", (token,)).fetchone()[0] == 0


def test_view_role_never_writes(bob, carol, anon):
    page = make_page(bob, "Look, don't touch")
    token = _share(bob, page["id"], audience="users")["token"]  # role view
    q = {"share": token}
    r = carol.put(f"/api/blocks/{page['id']}/children", params=q, json={"blocks": []})
    assert r.status_code == 403 and "view-only" in r.json()["detail"]
    assert carol.post("/api/blocks", params=q, json={"parent_id": page["id"], "content": "x"}).status_code == 403
    assert carol.put(f"/api/blocks/{page['id']}", params=q, json={"content": "renamed"}).status_code == 403
    # anonymous viewers of an "anyone" link can't write either (their grant
    # is valid but view-only, hence 403 rather than a sign-in 401)
    _share(bob, page["id"], audience="anyone")
    assert anon.put(f"/api/blocks/{page['id']}/children", params=q, json={"blocks": []}).status_code == 403
    assert bob.get(f"/api/blocks/{page['id']}").json()["content"] == "Look, don't touch"


def test_anyone_edit_link_lets_a_stranger_write_under_a_display_name(bob, guest, anon):
    page = make_page(bob, "Open draft", properties={"doc_id": "open_doc"})
    line = _child(bob, page["id"], "owner's line")
    other = make_page(bob, "Bob's private page")
    token = _share(bob, page["id"], audience="anyone", role="edit")["token"]
    q = {"share": token}
    assert anon.get(f"/api/share/{token}").json()["can_edit"] is True

    # a write with a display name is logged as link:<name> …
    r = anon.post(f"/api/pages/{page['id']}/ops", params=q, headers={"X-Gamma-Name": "  Otter\x07 the  Bold "},
                  json={"client": "c1", "ops": [{"op": "set", "id": line["id"], "content": "edited by a stranger"}]})
    assert r.status_code == 200, r.text
    assert bob.get(f"/api/blocks/{line['id']}").json()["content"] == "edited by a stranger"
    batches = bob.get(f"/api/pages/{page['id']}/ops").json()["batches"]
    assert batches[-1]["actor"] == "link:Otter the Bold"
    # … without one as link:Anonymous, and the guest account counts as a visitor too
    r = anon.post("/api/blocks", params=q, json={"parent_id": page["id"], "content": "anon line"})
    assert r.status_code == 200, r.text
    assert bob.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["actor"] == "link:Anonymous"
    r = guest.post("/api/blocks", params=q, headers={"X-Gamma-Name": "Guest Heron"},
                   json={"parent_id": page["id"], "content": "guest line"})
    assert r.status_code == 200, r.text
    assert bob.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["actor"] == "link:Guest Heron"
    # the header is percent-encoded UTF-8 (fetch cannot carry non-Latin-1 header values)
    r = anon.post("/api/blocks", params=q, headers={"X-Gamma-Name": "%E5%B0%8F%E6%98%8E"},
                  json={"parent_id": page["id"], "content": "cjk"})
    assert r.status_code == 200 and bob.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["actor"] == "link:小明"
    # the name is capped, the scope is still the page
    r = anon.post("/api/blocks", params=q, headers={"X-Gamma-Name": "N" * 80},
                  json={"parent_id": page["id"], "content": "capped"})
    assert r.status_code == 200 and bob.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["actor"] == "link:" + "N" * 40
    # a bulk write (the subtree replace, logged as a reload) carries the same name
    r = anon.put(f"/api/blocks/{line['id']}/children", params=q, headers={"X-Gamma-Name": "Otter"},
                 json={"blocks": [{"content": "replaced by a stranger"}]})
    assert r.status_code == 200, r.text
    assert bob.get(f"/api/pages/{page['id']}/ops").json()["batches"][-1]["actor"] == "link:Otter"
    assert anon.post("/api/blocks", params=q, json={"parent_id": other["id"], "content": "x"}).status_code == 403
    assert anon.put(f"/api/blocks/{page['id']}", params=q, json={"properties": {"doc_id": "evil"}}).status_code == 403
    assert bob.get(f"/api/blocks/{page['id']}").json()["properties"]["doc_id"] == "open_doc"
    # images too, into the owner's uploads
    r = anon.post("/api/upload-image", params=q, headers={"X-Gamma-Name": "Otter"},
                  files={"file": ("dot.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")})
    assert r.status_code == 200, r.text
    # flipping general access back to view revokes the link's writes at once
    _share(bob, page["id"], role="view")
    assert anon.post("/api/blocks", params=q, json={"parent_id": page["id"], "content": "late"}).status_code == 403
    assert anon.get(f"/api/share/{token}").json()["can_edit"] is False


def test_unknown_share_tokens_are_throttled_per_ip_and_logged(bob, anon, monkeypatch):
    from gamma import auth
    from gamma.logbuf import tail
    monkeypatch.setattr(auth, "SHARE_MISSES_PER_5_MIN", 3)
    page = make_page(bob, "Probed page")
    token = _share(bob, page["id"])["token"]
    before = tail()[-1]["seq"] if tail() else 0
    for i in range(3):
        assert anon.get(f"/api/share/bogus{i}").status_code == 404
    # the fourth miss is refused, and every share-token read from that address with it
    assert anon.get("/api/share/bogus3").status_code == 429
    assert anon.get(f"/api/blocks/{page['id']}", params={"share": "bogus4"}).status_code == 429
    warnings = [e for e in tail(before) if e["level"] == "WARNING" and "unknown share links" in e["msg"]]
    assert len(warnings) == 1, warnings  # one line per window, not per request
    # a real token still opens for everyone else (misses are per address; the
    # test client is one address, so check the grant path with a fresh window)
    monkeypatch.setattr(auth, "SHARE_MISSES_PER_5_MIN", 30)
    from gamma import ratelimit
    ratelimit._buckets.clear()
    assert anon.get(f"/api/share/{token}").status_code == 200


def test_link_visitors_are_rate_limited_per_ip(bob, anon, monkeypatch):
    from gamma.routers import collab as collab_router, uploads as uploads_router
    monkeypatch.setattr(collab_router, "LINK_OPS_PER_MINUTE", 2)
    monkeypatch.setattr(uploads_router, "LINK_UPLOADS_PER_5_MIN", 1)
    page = make_page(bob, "Flooded page")
    token = _share(bob, page["id"], audience="anyone", role="edit")["token"]
    q = {"share": token}
    body = lambda i: {"client": "c", "ops": [{"op": "insert", "id": f"fl{i}", "parent": page["id"], "content": "x"}]}
    assert anon.post(f"/api/pages/{page['id']}/ops", params=q, json=body(1)).status_code == 200
    assert anon.post(f"/api/pages/{page['id']}/ops", params=q, json=body(2)).status_code == 200
    assert anon.post(f"/api/pages/{page['id']}/ops", params=q, json=body(3)).status_code == 429
    from gamma.logbuf import tail
    assert any(e["level"] == "WARNING" and "link visitor" in e["msg"] and "ops" in e["msg"] for e in tail()[-5:])
    png = {"file": ("dot.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64, "image/png")}
    assert anon.post("/api/upload-image", params=q, files=png).status_code == 200
    assert anon.post("/api/upload-image", params=q, files=png).status_code == 429
    # the owner's own writes are never counted
    for i in range(4):
        assert bob.post(f"/api/pages/{page['id']}/ops", json=body(10 + i)).status_code == 200


def test_edit_role_writes_inside_the_page_only(bob, carol, dave):
    page = make_page(bob, "Shared draft", properties={"doc_id": "draft_doc"})
    keep = _child(bob, page["id"], "owner's line")
    other = make_page(bob, "Bob's private page")
    token = _share(bob, page["id"], audience="list",
                   users=[{"name": "carol_share", "role": "edit"}])["token"]
    q = {"share": token}

    # the editor autosaves the whole tree (what the frontend does)
    tree = [{"id": keep["id"], "content": "owner's line, edited", "properties": {}, "children": []},
            {"id": "carolblk1", "content": "carol's addition", "properties": {}, "children": []}]
    r = carol.put(f"/api/blocks/{page['id']}/children", params=q, json={"blocks": tree})
    assert r.status_code == 200, r.text
    got = bob.get(f"/api/blocks/{page['id']}/subtree").json()["block"]["children"]
    assert [c["content"] for c in got] == ["owner's line, edited", "carol's addition"]

    # single-block writes inside the page
    r = carol.post("/api/blocks", params=q, json={"parent_id": page["id"], "content": "one more"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert carol.put(f"/api/blocks/{new_id}", params=q, json={"content": "one more, fixed"}).status_code == 200
    assert carol.post(f"/api/blocks/{new_id}/reorder", params=q,
                      json={"parent_id": keep["id"], "before": None, "after": None}).status_code == 200
    assert carol.delete(f"/api/blocks/{new_id}", params=q).status_code == 200
    # renaming the page is a content write — allowed; its properties are not
    assert carol.put(f"/api/blocks/{page['id']}", params=q, json={"content": "Shared draft (v2)"}).status_code == 200
    r = carol.put(f"/api/blocks/{page['id']}", params=q, json={"properties": {"source_url": "https://evil/x.pdf"}})
    assert r.status_code == 403
    assert bob.get(f"/api/blocks/{page['id']}").json()["properties"]["doc_id"] == "draft_doc"

    # nothing outside the page, and never the page itself
    assert carol.delete(f"/api/blocks/{page['id']}", params=q).status_code == 403
    assert carol.post("/api/blocks", params=q, json={"parent_id": "root", "content": "new page"}).status_code == 403
    assert carol.post("/api/blocks", params=q, json={"parent_id": other["id"], "content": "x"}).status_code == 403
    assert carol.put(f"/api/blocks/{other['id']}/children", params=q, json={"blocks": []}).status_code == 403
    assert carol.put(f"/api/blocks/{other['id']}", params=q, json={"content": "x"}).status_code == 403
    assert carol.post(f"/api/blocks/{keep['id']}/reorder", params=q,
                      json={"parent_id": other["id"], "before": None, "after": None}).status_code == 403
    assert carol.post(f"/api/blocks/{keep['id']}/reorder", params=q,
                      json={"parent_id": "root", "before": None, "after": None}).status_code == 403
    assert bob.get(f"/api/blocks/{other['id']}").json()["content"] == "Bob's private page"
    # someone not on the list gets nothing, even with the edit link
    assert dave.put(f"/api/blocks/{page['id']}/children", params=q, json={"blocks": tree}).status_code == 403
    # and without the token the editor's own account is untouched by all this
    assert carol.get(f"/api/blocks/{page['id']}").status_code == 404


def test_share_editor_images_land_in_the_owners_uploads(bob, carol):
    page = make_page(bob, "Draft with figure")
    token = _share(bob, page["id"], audience="users", role="edit")["token"]
    png = b"\x89PNG\r\n\x1a\n" + b"\x01" * 80
    r = carol.post("/api/upload-image", params={"share": token}, files={"file": ("f.png", png, "image/png")})
    assert r.status_code == 200, r.text
    url = r.json()["url"]
    assert bob.get(url).status_code == 200          # it is bob's file now
    assert carol.get(url).status_code == 404        # not in carol's own uploads
    # view-only links can't upload
    _share(bob, page["id"], role="view")
    r = carol.post("/api/upload-image", params={"share": token}, files={"file": ("g.png", png, "image/png")})
    assert r.status_code == 403


def test_share_reads_are_cors_open_and_importable(bob, anon):
    """Another Gamma imports a shared page browser-side: resolve the token,
    fetch the page as a Gamma export, merge the zip into its own library. Share
    GETs answer Access-Control-Allow-Origin: * for that; nothing else does."""
    page = make_page(bob, "Shared across Gammas")
    _child(bob, page["id"], "carried along")
    token = bob.post(f"/api/share/{page['id']}").json()["token"]

    resolved = anon.get(f"/api/share/{token}")
    assert resolved.status_code == 200
    assert resolved.headers.get("access-control-allow-origin") == "*"
    assert resolved.json()["viewer"] == ""
    assert resolved.json()["viewer_is_guest"] is False

    zipped = anon.get(f"/api/pages/{page['id']}/export", params={"mode": "gamma", "share": token})
    assert zipped.status_code == 200, zipped.text
    assert zipped.headers.get("access-control-allow-origin") == "*"

    # Writes and non-share reads keep the same-origin default.
    assert "access-control-allow-origin" not in bob.get("/api/blocks/root/children").headers
    assert "access-control-allow-origin" not in bob.post(f"/api/share/{page['id']}").headers

    make_user("dana_share", "danapw1234567")
    dana = login("dana_share", "danapw1234567")
    r = dana.post("/api/import-data?mode=merge",
                   files={"file": ("shared-page.zip", zipped.content, "application/zip")})
    assert r.status_code == 200, r.text
    assert r.json()["pages_added"] == 1
    # Same block ids, so the link's page id opens straight in dana's library.
    got = dana.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    assert got["content"] == "Shared across Gammas"
    assert [c["content"] for c in got["children"]] == ["carried along"]


# ---- folder shares -----------------------------------------------------------
# A share may name a folder instead of a page: the pages filed in that folder
# or below it, read live (gamma/auth.py ShareScope). Same audience / role /
# people model; the token confines reads and writes to those pages.

def _folder_share(client, folder_id, **settings):
    r = client.post(f"/api/share/folder/{folder_id}", json=settings or None)
    assert r.status_code == 200, r.text
    return r.json()


def _move(client, folder_id, parent):
    r = client.post("/api/pages/folders/ops", json={"ops": [{"op": "move", "id": folder_id, "parent": parent}]})
    assert r.status_code == 200, r.text


def test_folder_share_reaches_the_pages_filed_in_it(bob, anon):
    lab, readout = make_folder(bob, "lab"), make_folder(bob, "lab/readout")
    sub, other = make_folder(bob, "lab/readout/sub"), make_folder(bob, "lab/other")
    inside = make_page(bob, "In the folder", {"folders": [readout]})
    deeper = make_page(bob, "In a subfolder", {"folders": [sub, make_folder(bob, "elsewhere")]})
    outside = make_page(bob, "Outside", {"folders": [other]})
    note = _child(bob, inside["id"], "a note in the folder")

    assert bob.post("/api/share/folder/no-such-folder").status_code == 404
    assert bob.post(f"/api/share/folder/{inside['id']}").status_code == 404  # a page is no folder
    share = _folder_share(bob, readout)
    token = share["token"]
    assert share["folder"] == readout and share["page_id"] == ""
    assert _folder_share(bob, readout)["token"] == token             # stable, like a page's
    assert bob.get(f"/api/share-settings/folder/{readout}").json()["token"] == token
    assert bob.get(f"/api/share-settings/folder/{lab}").json()["token"] is None

    resolved = anon.get(f"/api/share/{token}")
    assert resolved.status_code == 200, resolved.text
    data = resolved.json()
    assert data["folder"] == readout and data["folder_name"] == "readout"
    assert data["page_id"] == "" and "doc_id" not in data

    q = {"share": token}
    # the library listing through the link is the folder's pages, with previews,
    # and the shared folder's own tree (no seq: a link joins no tree's room)
    listing = anon.get("/api/blocks/root/children", params=q)
    assert listing.status_code == 200, listing.text
    children = listing.json()["children"]
    assert {c["id"] for c in children} == {inside["id"], deeper["id"]}
    assert next(c for c in children if c["id"] == inside["id"])["preview"] == "a note in the folder"
    folders = listing.json()["folders"]
    assert "seq" not in folders and [f["id"] for f in folders["children"]] == [readout]
    assert [f["id"] for f in folders["children"][0]["children"]] == [sub]
    assert anon.get("/api/blocks/folders/subtree", params=q).status_code == 403
    assert anon.get("/api/pages/folders/ops", params=q).status_code == 403
    assert anon.get(f"/api/blocks/{inside['id']}", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{note['id']}", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{deeper['id']}/subtree", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{outside['id']}", params=q).status_code == 403
    assert anon.get(f"/api/pages/{inside['id']}/ops", params=q).status_code == 200
    assert anon.get(f"/api/pages/{outside['id']}/ops", params=q).status_code == 403
    assert anon.get(f"/api/chats/{inside['id']}", params=q).status_code == 200
    assert anon.get(f"/api/chats/{outside['id']}", params=q).status_code == 403
    # whole-folder reads: the shared folder and its subfolders, nothing beside them
    assert anon.get(f"/api/folders/{readout}/export", params={**q, "mode": "readable"}).status_code == 200
    assert anon.get(f"/api/folders/{sub}/export", params={**q, "mode": "readable"}).status_code == 200
    assert anon.get(f"/api/folders/{lab}/export", params={**q, "mode": "readable"}).status_code == 403

    # membership is live: a page filed later joins, one moved out leaves, and
    # a folder moved in brings its pages along (moved out, takes them away)
    later = make_page(bob, "Filed later", {"folders": [readout]})
    assert anon.get(f"/api/blocks/{later['id']}", params=q).status_code == 200
    assert {c["id"] for c in anon.get("/api/blocks/root/children", params=q).json()["children"]} == {
        inside["id"], deeper["id"], later["id"]}
    bob.put(f"/api/blocks/{inside['id']}", json={"properties": {"folders": [other]}})
    assert anon.get(f"/api/blocks/{inside['id']}", params=q).status_code == 403
    _move(bob, other, readout)
    assert anon.get(f"/api/blocks/{inside['id']}", params=q).status_code == 200
    assert anon.get(f"/api/blocks/{outside['id']}", params=q).status_code == 200
    _move(bob, other, lab)
    assert anon.get(f"/api/blocks/{outside['id']}", params=q).status_code == 403

    r = bob.delete(f"/api/share-settings/folder/{readout}")
    assert r.json()["removed"] == 1
    assert anon.get(f"/api/share/{token}").status_code == 404
    assert bob.get(f"/api/share-settings/folder/{readout}").json()["token"] is None


def test_folder_share_reads_only_its_pages_assets(bob, anon):
    png = (b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
    urls = []
    for name in ("in.png", "out.png"):
        up = bob.post("/api/upload-image", files={"file": (name, png + name.encode(), "image/png")})
        assert up.status_code == 200, up.text
        urls.append(up.json()["url"])
    shared = make_folder(bob, "assets/shared")
    inside = make_page(bob, "Figure inside", {"folders": [shared]})
    _child(bob, inside["id"], f"![in]({urls[0]})")
    outside = make_page(bob, "Figure outside")
    _child(bob, outside["id"], f"![out]({urls[1]})")
    token = _folder_share(bob, shared)["token"]
    assert anon.get(urls[0], params={"share": token}).status_code == 200
    assert anon.get(urls[1], params={"share": token}).status_code == 403


def test_folder_edit_share_writes_inside_the_folder_only(bob, carol):
    drafts = make_folder(bob, "team/drafts")
    page = make_page(bob, "Draft in folder", {"folders": [drafts]})
    outside = make_page(bob, "Not shared")
    token = _folder_share(bob, drafts, audience="list",
                          users=[{"name": "carol_share", "role": "edit"}])["token"]
    q = {"share": token}

    r = carol.post("/api/blocks", params=q, json={"parent_id": page["id"], "content": "carol's line"})
    assert r.status_code == 200, r.text
    new_id = r.json()["id"]
    assert carol.put(f"/api/blocks/{new_id}", params=q, json={"content": "carol's line, fixed"}).status_code == 200
    r = carol.post(f"/api/pages/{page['id']}/ops", params=q, json={
        "client": "c1", "ops": [{"op": "set", "id": new_id, "content": "carol's line, via ops"}]})
    assert r.status_code == 200, r.text
    # renaming the page is fine; re-filing it (its properties) is not — a
    # share editor could otherwise move pages into or out of the share
    assert carol.put(f"/api/blocks/{page['id']}", params=q, json={"content": "Draft, renamed"}).status_code == 200
    assert carol.put(f"/api/blocks/{page['id']}", params=q,
                     json={"properties": {"folders": [make_folder(bob, "team")]}}).status_code == 403
    # nor the folders themselves: a share link reaches no tree
    assert carol.post("/api/pages/folders/ops", params=q, json={"ops": [
        {"op": "set", "id": drafts, "content": "renamed"}]}).status_code == 403
    assert carol.delete(f"/api/folders/{drafts}", params=q).status_code == 403
    # never other pages, never new pages, never deleting a shared page
    assert carol.post("/api/blocks", params=q, json={"parent_id": outside["id"], "content": "x"}).status_code == 403
    assert carol.post(f"/api/pages/{outside['id']}/ops", params=q, json={"client": "c1", "ops": []}).status_code == 403
    assert carol.post("/api/blocks", params=q, json={"parent_id": "root", "content": "new page"}).status_code == 403
    assert carol.delete(f"/api/blocks/{page['id']}", params=q).status_code == 403
    assert carol.post(f"/api/blocks/{new_id}/reorder", params=q,
                      json={"parent_id": outside["id"], "before": None, "after": None}).status_code == 403
    got = bob.get(f"/api/blocks/{page['id']}/subtree").json()["block"]
    assert got["content"] == "Draft, renamed"
    assert [c["content"] for c in got["children"]] == ["carol's line, via ops"]
    assert bob.get(f"/api/blocks/{page['id']}").json()["properties"]["folders"] == [drafts]


def test_folder_share_survives_renames_and_moves_and_dies_with_the_folder(bob, anon):
    old, old_x, beside = make_folder(bob, "old"), make_folder(bob, "old/x"), make_folder(bob, "beside")
    page = make_page(bob, "Moving page", {"folders": [old_x]})
    whole = _folder_share(bob, old)["token"]
    inner = _folder_share(bob, old_x)["token"]
    page_token = bob.post(f"/api/share/{page['id']}").json()["token"]

    # a rename and a move are one op on the tree: the shares name the folder by id
    r = bob.post("/api/pages/folders/ops", json={"ops": [{"op": "set", "id": old, "content": "new"},
                                                          {"op": "move", "id": old_x, "parent": beside}]})
    assert r.status_code == 200, r.text
    assert bob.get(f"/api/share-settings/folder/{old_x}").json()["token"] == inner
    assert anon.get(f"/api/share/{whole}").json()["folder_name"] == "new"
    assert {c["id"] for c in anon.get("/api/blocks/root/children", params={"share": inner}).json()["children"]} == {
        page["id"]}
    # x left "old": its page went with it
    assert anon.get("/api/blocks/root/children", params={"share": whole}).json()["children"] == []

    # deleting a folder drops its shares and those of the folders below it
    _move(bob, old_x, old)
    r = bob.delete(f"/api/folders/{old}")
    assert r.status_code == 200 and r.json()["shares"] == 2
    for token in (whole, inner):
        assert anon.get(f"/api/share/{token}").status_code == 404
    assert anon.get(f"/api/share/{page_token}").status_code == 200
    assert anon.delete(f"/api/folders/{beside}", params={"share": page_token}).status_code == 403


def test_block_search_through_a_share_sees_only_what_it_opens(bob, anon):
    # The share view resolves [[ref]] chips and Gamma-link titles through
    # /block-search?ids=; a hit outside the share is dropped, never refused,
    # so nothing tells a visitor whether it exists.
    page = make_page(bob, "Searchable shared")
    note = _child(bob, page["id"], "a shared quokkaneedle")
    vault = make_folder(bob, "vault")
    other = make_page(bob, "Private elsewhere", {"folders": [vault]})
    secret = _child(bob, other["id"], "a private quokkaneedle")
    q = {"share": bob.post(f"/api/share/{page['id']}").json()["token"]}

    r = anon.get("/api/block-search", params={**q, "ids": f"{page['id']},{note['id']},{secret['id']}"})
    assert r.status_code == 200, r.text
    assert {b["id"] for b in r.json()["blocks"]} == {page["id"], note["id"]}
    assert r.json()["blocks"][0]["page_title"] == "Searchable shared"
    assert [b["id"] for b in anon.get("/api/block-search", params={**q, "q": "quokkaneedle"}).json()["blocks"]] == [note["id"]]
    assert {b["id"] for b in anon.get("/api/block-search", params=q).json()["blocks"]} == {page["id"], note["id"]}
    assert anon.get("/api/block-search", params={"ids": note["id"]}).status_code == 401
    # the owner's own search still reaches everything
    assert {b["id"] for b in bob.get("/api/block-search", params={"q": "quokkaneedle"}).json()["blocks"]} == {
        note["id"], secret["id"]}

    # a folder share: the pages filed in the folder, and nothing beside them
    fq = {"share": _folder_share(bob, vault)["token"]}
    hits = anon.get("/api/block-search", params={**fq, "q": "quokkaneedle"}).json()["blocks"]
    assert [b["id"] for b in hits] == [secret["id"]]
    assert anon.get("/api/block-search", params={**fq, "ids": note["id"]}).json()["blocks"] == []

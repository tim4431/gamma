"""Integration tokens on the HTTP API (auth.py): a bearer token is the account
behind it, confined to the token's workspace, read-only unless made with
the write scope (the comments of one at the Comment level aside), and never a session
that manages tokens or accounts."""

from fastapi.testclient import TestClient

from conftest import account_of, login, make_folder, make_page, make_user, workspace_of
from gamma.integrations import create_token


def _bearer(token, ws=None):
    from gamma.app import app
    c = TestClient(app)
    c.headers["Authorization"] = f"Bearer {token}"
    if ws:
        c.headers["X-Gamma-Workspace"] = ws
    return c


def test_read_token_reads_and_cannot_write(guest):
    make_user("tok_reader", "pw")
    owner = login("tok_reader", "pw")
    ws = workspace_of("tok_reader")
    page = make_page(owner, "Token page")
    token = create_token(account_of("tok_reader"), ws, "reader", 90)["token"]
    c = _bearer(token)
    me = c.get("/api/sync/whoami").json()
    assert me == {"user": "tok_reader", "workspace": {"id": ws, "name": me["workspace"]["name"]},
                  "role": "owner", "scope": "read", "work_folders": 1, "computer_asks": 1}
    assert c.get(f"/api/blocks/{page['id']}").json()["content"] == "Token page"
    r = c.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page["id"], "content": "renamed"}]})
    assert r.status_code == 403 and "read-only" in r.text
    assert c.post("/api/pages", json={"title": "x"}).status_code == 403


def test_write_token_writes_under_the_account(guest):
    make_user("tok_writer", "pw")
    owner = login("tok_writer", "pw")
    ws = workspace_of("tok_writer")
    page = make_page(owner, "Writable")
    token = create_token(account_of("tok_writer"), ws, "mirror", 90, scope="write")["token"]
    c = _bearer(token, ws)
    r = c.post(f"/api/pages/{page['id']}/ops", json={"client": "sync", "ops": [
        {"op": "insert", "id": "tokblk", "parent": page["id"], "position": "a0", "content": "from token"}]})
    assert r.status_code == 200, r.text
    log = owner.get(f"/api/pages/{page['id']}/ops?since=0").json()["batches"]
    assert log[-1]["actor"] == account_of("tok_writer") and log[-1]["client"] == "sync"
    filed = make_folder(owner, "f")
    created = c.post("/api/pages", json={"id": "tokpage_1", "title": "Brought over",
                                         "properties": {"folders": [filed]}})
    assert created.status_code == 200 and created.json()["id"] == "tokpage_1"
    assert created.json()["properties"]["folders"] == [filed]
    assert c.post("/api/pages", json={"id": "tokpage_1", "title": "again"}).status_code == 409
    assert c.post("/api/pages", json={"id": "bad id!", "title": "again"}).status_code == 400


def test_a_blocks_agent_stamp_is_the_servers(guest):
    """A block's ``agent`` stamp is written by the server's writers: a
    person's batch, or a write token's under its own client, drops it from
    an insert and a set, the other properties kept; the mirror's batch
    keeps it, as it keeps every property."""
    make_user("tok_stamp", "pw")
    owner = login("tok_stamp", "pw")
    ws = workspace_of("tok_stamp")
    page = make_page(owner, "Stamped")
    stamp = {"id": "gamma", "name": "Gamma AI", "for": "alice"}
    token = create_token(account_of("tok_stamp"), ws, "mirror", 90, scope="write")["token"]
    c = _bearer(token, ws)

    def props_of(block_id):
        return owner.get(f"/api/blocks/{block_id}").json()["properties"]

    for writer, client, block_id in ((owner, "tab1", "stmpblk1"), (c, "script", "stmpblk2")):
        r = writer.post(f"/api/pages/{page['id']}/ops", json={"client": client, "ops": [
            {"op": "insert", "id": block_id, "parent": page["id"], "position": "a0", "content": "x",
             "props": {"agent": stamp, "color": "#ffe28fa6"}}]})
        assert r.status_code == 200, r.text
        assert "agent" not in props_of(block_id) and props_of(block_id)["color"] == "#ffe28fa6"
        r = writer.post(f"/api/pages/{page['id']}/ops", json={"client": client, "ops": [
            {"op": "set", "id": block_id, "props": {"agent": stamp}}]})
        assert r.status_code == 200 and "agent" not in props_of(block_id)
    r = c.post(f"/api/pages/{page['id']}/ops", json={"client": "sync", "ops": [
        {"op": "insert", "id": "stmpblk3", "parent": page["id"], "position": "a1", "content": "y",
         "props": {"agent": stamp}}]})
    assert r.status_code == 200 and props_of("stmpblk3")["agent"] == stamp


def test_token_is_confined_and_is_no_session(guest):
    make_user("tok_confined", "pw")
    ws = workspace_of("tok_confined")
    other = make_user("tok_other", "pw")
    token = create_token(account_of("tok_confined"), ws, "reader", 90)["token"]
    assert _bearer(token, other).get("/api/sync/whoami").status_code == 403
    c = _bearer(token)
    assert c.get("/api/integrations/tokens").status_code == 403
    assert c.post("/api/integrations/tokens", json={"name": "x"}).status_code == 403
    assert c.get("/api/admin/backups").status_code in (401, 403)
    assert _bearer("gamma_nope").get("/api/sync/whoami").status_code == 401
    # a viewer cannot mint a write token for a shared workspace
    make_user("tok_admin", "pw", is_admin=1)
    admin = login("tok_admin", "pw")
    shared = admin.post("/api/workspaces", json={"name": "Tok shared", "kind": "shared"}).json()
    admin.put(f"/api/workspaces/{shared['id']}/members/tok_confined", json={"role": "viewer"}).raise_for_status()
    viewer = login("tok_confined", "pw")
    viewer.headers["X-Gamma-Workspace"] = shared["id"]
    r = viewer.post("/api/integrations/tokens", json={"name": "w", "scope": "write"})
    assert r.status_code == 403, r.text
    assert viewer.post("/api/integrations/tokens", json={"name": "r"}).status_code == 201
    listed = viewer.get("/api/integrations/tokens").json()["tokens"]
    assert listed[0]["scope"] == "read"


def test_a_token_at_the_comment_level_reads_as_a_read_token_in_its_own_workspace(guest):
    """But for its comments (test_comments.py), a read token at the Comment
    level is a read token on the HTTP API: its own workspace alone,
    whatever else its MCP connection reads or comments in."""
    from gamma import workspaces

    make_user("tok_answerer", "pw")
    owner = login("tok_answerer", "pw")
    ws = workspace_of("tok_answerer")
    lab = workspaces.create("Answer lab", account_of("tok_answerer"), kind="shared")["id"]
    page = make_page(owner, "Answered page")
    token = create_token(account_of("tok_answerer"), ws, "Claude Code", 90, workspaces=f'["{ws}", "{lab}"]',
                         comments=f'["{ws}", "{lab}"]')["token"]
    c = _bearer(token)
    assert c.get("/api/sync/whoami").json()["scope"] == "read"
    assert c.get(f"/api/blocks/{page['id']}").json()["content"] == "Answered page"
    r = c.post(f"/api/pages/{page['id']}/ops", json={"client": "t", "ops": [
        {"op": "set", "id": page["id"], "content": "renamed"}]})
    assert r.status_code == 403 and "read-only" in r.text
    assert c.post("/api/pages", json={"title": "x"}).status_code == 403
    assert c.post(f"/api/pages/{page['id']}/comments", json={"block_id": page["id"], "body": "ok"}).status_code == 201
    assert _bearer(token, lab).get("/api/sync/whoami").status_code == 403


def test_the_tokens_pane_makes_tokens_at_the_comment_level(guest):
    """``POST /api/integrations/tokens`` with ``comment_workspaces`` (or
    ``comment_all``): a read token that may read several workspaces, all of
    them, and change them later, commenting in those named, which a viewer
    may make (viewers comment), never a write token; its HTTP API stays its
    first workspace's. The scope ``feedback`` is gone."""
    from gamma import workspaces

    make_user("tok_fb_admin", "pw", is_admin=1)
    admin = login("tok_fb_admin", "pw")
    make_user("tok_fb_viewer", "pw")
    ws = workspace_of("tok_fb_viewer")
    shared = admin.post("/api/workspaces", json={"name": "Feedback shared", "kind": "shared"}).json()
    admin.put(f"/api/workspaces/{shared['id']}/members/tok_fb_viewer", json={"role": "viewer"}).raise_for_status()
    viewer = login("tok_fb_viewer", "pw")
    viewer.headers["X-Gamma-Workspace"] = shared["id"]
    assert viewer.post("/api/integrations/tokens", json={"name": "w", "scope": "write"}).status_code == 403
    made = viewer.post("/api/integrations/tokens", json={"name": "Claude Code", "comment_workspaces": [shared["id"]]})
    assert made.status_code == 201, made.text
    assert (made.json()["scope"], made.json()["workspace_id"], made.json()["workspaces"],
            made.json()["comment_workspaces"]) == ("read", shared["id"], [shared["id"]], [shared["id"]])
    for scope in ("feedback", "answer"):
        assert viewer.post("/api/integrations/tokens", json={"name": "x", "scope": scope}).status_code == 422
    # only where it reads; several workspaces, then all of them, then others
    assert viewer.post("/api/integrations/tokens", json={"name": "x", "comment_workspaces": [ws]}).status_code == 400
    assert viewer.post("/api/integrations/tokens", json={"name": "x", "comment_all": True}).status_code == 400
    several = viewer.post("/api/integrations/tokens", json={"name": "Agent", "workspaces": [ws, shared["id"]],
                                                            "comment_workspaces": [ws, shared["id"]]})
    assert several.status_code == 201, several.text
    assert several.json()["workspaces"] == [ws, shared["id"]] and several.json()["workspace_id"] == ws
    every = viewer.post("/api/integrations/tokens", json={"name": "All", "all_workspaces": True, "comment_all": True})
    assert every.status_code == 201 and every.json()["all_workspaces"] and every.json()["comment_all"]
    changed = viewer.patch(f"/api/integrations/tokens/{made.json()['id']}", json={"workspaces": [shared["id"], ws],
                                                                                  "comment_workspaces": [ws]})
    assert changed.status_code == 200, changed.text
    assert (changed.json()["workspaces"], changed.json()["comment_workspaces"]) == ([shared["id"], ws], [ws])
    listed = {t["id"]: t for t in viewer.get("/api/integrations/tokens").json()["tokens"]}
    assert listed[several.json()["id"]]["comment_workspaces"] == [ws, shared["id"]]
    assert listed[every.json()["id"]]["all_workspaces"] and listed[every.json()["id"]]["comment_all"]
    # the HTTP API reads the first workspace alone, whatever else MCP reads
    token = several.json()["token"]
    assert _bearer(token).get("/api/sync/whoami").json()["scope"] == "read"
    assert _bearer(token, shared["id"]).get("/api/sync/whoami").status_code == 403
    lab = workspaces.create("Feedback lab", account_of("tok_fb_viewer"))["id"]
    try:
        assert viewer.patch(f"/api/integrations/tokens/{several.json()['id']}",
                            json={"all_workspaces": True}).json()["all_workspaces"]
        assert _bearer(token, lab).get("/api/sync/whoami").status_code == 403
    finally:
        workspaces.delete(lab)

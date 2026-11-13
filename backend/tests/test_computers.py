"""A computer's channel and its presence (gamma/computers/, step 4a of
docs/research/agent-collab.md): the ``computer`` token, minted with a
session and refused everywhere but the channel; the announce and the held
poll on the server, what a page's maker is shown of the computers that keep
it, and nobody else; and the computer's side, its links to other servers
taken with the shell's word and the thread that announces and polls.

One process plays both sides here (the "other server" is a TestClient
reached through ``gamma_sync.default_open``), so the tests assert the
channel itself: the server's ``computers`` row, written only by an
announce, is what shows the computer."""

import io
import json
import logging
import threading
import time

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_page, make_user, workspace_of
from gamma import collab, folder_links, gamma_sync, workspaces
from gamma.computers import channel, registry
from gamma.db import connect_users_db
from gamma.folder_sync import ROOT
from gamma.integrations import create_token, forget_workspace, grant, resolve_connection
from gamma.local_agents import catalog

PASSWORD = "cm-pass-1234"
SECRET = "cm-shell-secret"
SHELL = {"X-Gamma-Shell": SECRET}
REAL_START = channel.start


@pytest.fixture(scope="module")
def owner():
    make_user("cptr_owner", PASSWORD)
    return login("cptr_owner", PASSWORD)


@pytest.fixture(scope="module")
def stranger():
    make_user("cptr_stranger", PASSWORD)
    return login("cptr_stranger", PASSWORD)


@pytest.fixture(autouse=True)
def _forget_presence():
    """No channel, presence or token of a test outlives it (an account
    holds at most 20 tokens)."""
    yield
    channel.stop_all()
    registry.reset()
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of("cptr_owner"),))
        conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of("cptr_owner"),))


@pytest.fixture
def told(monkeypatch):
    """The accounts each ``{t: "computer"}`` went to."""
    sent = []
    monkeypatch.setattr(collab, "publish_account", lambda user_id, message: sent.append((user_id, message)))
    return sent


def _mint(c, name="Gamma desktop: TIM-PC", **headers) -> dict:
    r = c.post("/api/integrations/tokens", json={"name": name, "scope": "computer", "expires_in_days": 365},
               headers=headers)
    assert r.status_code == 201, r.text
    return r.json()


def _as(token: str):
    return fresh_client(headers={"Authorization": f"Bearer {token}"})


def _body(*vaults, assistants=(), name="TIM-PC", computer_id="cm-computer-1") -> dict:
    return {"computer": {"id": computer_id, "name": name}, "app": "v9.9.9", "vaults": list(vaults),
            "assistants": list(assistants)}


def _announce(c, body) -> dict:
    r = c.post("/api/computers/announce", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def _row(token_id: str):
    with connect_users_db() as conn:
        row = conn.execute("SELECT computer, name, vaults, assistants, seen_at FROM computers WHERE token_id = ?",
                           (token_id,)).fetchone()
    return row and {"computer": row[0], "name": row[1], "vaults": json.loads(row[2]),
                    "assistants": json.loads(row[3]), "seen_at": row[4]}


def _shown(c, page_id: str, **headers) -> list:
    r = c.get(f"/api/pages/{page_id}/computer", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()["computers"]


def _until(check, seconds=5.0):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if check():
            return True
        time.sleep(0.02)
    return False


# --- the scope ------------------------------------------------------------------------------------

def test_a_computer_token_is_minted_with_a_session_and_opens_its_channel_alone(owner, anon):
    page = make_page(owner, "CM scope paper")
    item = _mint(owner)
    assert item["scope"] == "computer" and item["workspace_id"] == workspace_of("cptr_owner")
    listed = {t["id"]: t["scope"] for t in owner.get("/api/integrations/tokens").json()["tokens"]}
    assert listed[item["id"]] == "computer"
    computer = _as(item["token"])
    refused = "This token is a computer's channel; it opens nothing else."
    for path in (f"/api/blocks/{page['id']}", "/api/sync/whoami", "/api/feedback/requests", "/api/integrations/tokens",
                 f"/api/pages/{page['id']}/computer"):
        r = computer.get(path)
        assert (r.status_code, r.json()["detail"]) == (403, refused), path
    assert computer.post(f"/api/pages/{page['id']}/comments", json={"block_id": page["id"], "body": "hi"}).status_code == 403
    assert resolve_connection(item["token"]) is None                          # no MCP connection either
    assert computer.post("/mcp", json={}, headers={"Host": "localhost"}).status_code == 401
    # only a session mints one: a token (this one or any other) and no one at all cannot
    write = owner.post("/api/integrations/tokens", json={"name": "Script", "scope": "write"}).json()["token"]
    assert computer.post("/api/integrations/tokens", json={"name": "x", "scope": "computer"}).status_code == 403
    assert _as(write).post("/api/integrations/tokens", json={"name": "x", "scope": "computer"}).status_code == 403
    assert anon.post("/api/integrations/tokens", json={"name": "x", "scope": "computer"}).status_code == 401
    with pytest.raises(Exception) as e:
        grant(account_of("cptr_owner"), "*", "computer")
    assert e.value.detail == "A computer's token is for one workspace."
    # the channel's routes refuse a session and every other token
    for c in (owner, _as(write)):
        r = c.get("/api/computers/poll", params={"wait": 0})
        assert (r.status_code, r.json()["detail"]) == (403, "Only a computer's token opens its channel.")
        assert c.post("/api/computers/announce", json=_body()).status_code == 403
    assert anon.get("/api/computers/poll").status_code == 403


# --- the announce ---------------------------------------------------------------------------------

def test_the_announce_is_kept_normalised_with_the_workspaces_own_vaults_only(owner, stranger, told):
    folder = make_folder(owner, "CM announce")
    page = make_page(owner, "CM announce file")
    foreign = make_page(stranger, "CM not theirs")
    item = _mint(owner)
    computer = _as(item["token"])
    answer = _announce(computer, {
        "computer": {"id": "cm_PC-1", "name": "  TIM-PC  "}, "app": "v" * 100,
        "vaults": [{"kind": "folder", "folder_id": folder, "paused": True}, {"kind": "folder", "folder_id": folder},
                   {"kind": "folder", "folder_id": "no-such-folder"}, {"kind": "file", "page_id": page["id"]},
                   {"kind": "file", "page_id": foreign["id"]}],
        "assistants": [{"name": "codex", "label": "Codex", "found": True, "ready": True, "version": "9" * 100,
                        "code": ""},
                       {"name": "gpt", "label": "GPT", "found": True, "ready": True},
                       {"name": "claude", "label": "Claude Code", "found": True, "ready": False, "code": "on fire"}]})
    me = account_of("cptr_owner")
    assert answer["account"] == {"id": me, "username": "cptr_owner"} and answer["computer"] == {"id": "cm_PC-1", "name": "TIM-PC"}
    assert answer["workspace"] == {"id": workspace_of("cptr_owner")} and answer["wait"] == registry.POLL_S and answer["version"]
    row = _row(item["id"])
    assert row["computer"] == "cm_PC-1" and row["name"] == "TIM-PC"
    assert row["vaults"] == [{"kind": "folder", "folder_id": folder, "paused": True},
                             {"kind": "file", "page_id": page["id"], "paused": False}]
    assert row["assistants"] == [
        {"name": "codex", "label": "Codex", "found": True, "ready": True, "version": "9" * 64, "code": ""},
        {"name": "claude", "label": "Claude Code", "found": True, "ready": False, "version": "", "code": "not_ready"}]
    assert told == [(me, {"t": "computer"})]                      # online now: the maker's windows alone
    _announce(computer, {**_body(), "computer": {"id": "cm_PC-1", "name": "TIM-PC"},
                         "vaults": [{"kind": "folder", "folder_id": folder, "paused": True},
                                    {"kind": "file", "page_id": page["id"]}],
                         "assistants": row["assistants"]})
    assert len(told) == 1                                         # nothing a window shows changed

    for bad in ({"computer": {"id": "bad id!", "name": "PC"}}, {"computer": {"id": "x", "name": ""}},
                {"computer": {"id": "x", "name": "PC"}, "vaults": "all"},
                {"computer": {"id": "x", "name": "PC"}, "vaults": [{"kind": "disk", "path": "C:/"}]},
                {"computer": {"id": "x", "name": "PC"}, "vaults": [{"kind": "folder"}]},
                {"computer": {"id": "x", "name": "PC"}, "vaults": [{"kind": "file", "page_id": "p"}] * 501}, []):
        assert computer.post("/api/computers/announce", json=bad).status_code == 400, bad
    assert computer.post("/api/computers/announce", content=b"not json").status_code == 400
    r = computer.post("/api/computers/announce", content=b"{" + b" " * (65 * 1024) + b"}")
    assert r.status_code == 413


# --- the poll -------------------------------------------------------------------------------------

def _poll(c, version, wait=0):
    r = c.get("/api/computers/poll", params={"v": version, "wait": wait})
    return r.status_code, r.json()


def test_a_poll_asks_an_unknown_computer_to_announce_holds_and_is_woken(owner, told):
    item = _mint(owner)
    computer = _as(item["token"])
    assert _poll(computer, "") == (200, {"announce": True})                   # unknown here (a restart)
    version = _announce(computer, _body())["version"]
    assert _poll(computer, "an-older-one") == (200, {"announce": True})
    assert _poll(computer, version) == (200, {})                              # a hold of no time
    for reason, answer in (("announce", {"announce": True}), ("sends", {"wake": ["sends"]})):
        got = []
        poller = threading.Thread(target=lambda: got.append(_poll(computer, version, wait=10)))
        started = time.monotonic()
        poller.start()
        assert _until(lambda: registry._live[item["id"]].holding == 1)
        registry.wake(item["id"], reason)
        poller.join(5)
        assert got == [(200, answer)] and time.monotonic() - started < 5
    assert registry._live[item["id"]].holding == 0


def test_the_poll_reads_off_the_loop_and_is_no_slow_request(owner, caplog, monkeypatch):
    from gamma.routers import computers as computers_api

    item = _mint(owner)
    computer = _as(item["token"])
    version = _announce(computer, _body())["version"]
    seen = []
    real = computers_api.resolve_token_scope

    def probe(*args):
        import asyncio
        try:
            asyncio.get_running_loop()
            seen.append("loop")
        except RuntimeError:
            seen.append("thread")
        return real(*args)

    monkeypatch.setattr(computers_api, "resolve_token_scope", probe)
    with caplog.at_level(logging.INFO, logger="gamma"):
        assert _poll(computer, version, wait=2.2) == (200, {})
    assert seen == ["thread"]
    assert not [r.message for r in caplog.records if r.message.startswith("[http]") and "/api/computers/poll" in r.message]
    from gamma.logbuf import _QuietPolls
    record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s - "%s %s HTTP/%s" %d',
                               ("127.0.0.1", "GET", f"/api/computers/poll?v={version}&wait=25", "1.1", 200), None)
    assert _QuietPolls().filter(record) is False


def test_a_computer_goes_offline_after_the_grace_and_only_its_maker_is_told(owner, monkeypatch, told):
    monkeypatch.setattr(registry, "GRACE_S", 0.3)
    page = make_page(owner, "CM offline paper")
    item = _mint(owner)
    computer = _as(item["token"])
    version = _announce(computer, _body({"kind": "file", "page_id": page["id"]}))["version"]
    assert _poll(computer, version) == (200, {})
    shown = _shown(owner, page["id"])
    assert [(c["id"], c["online"], c["since"]) for c in shown] == [("cm-computer-1", True, None)]
    me = account_of("cptr_owner")
    assert _until(lambda: len(told) == 2)
    assert told == [(me, {"t": "computer"})] * 2                  # online, then offline: to the maker alone
    shown = _shown(owner, page["id"])
    assert shown[0]["online"] is False and shown[0]["since"] == _row(item["id"])["seen_at"]
    assert _poll(computer, version) == (200, {})                  # back: told again
    assert len(told) == 3 and _shown(owner, page["id"])[0]["online"] is True


def test_a_revoked_token_is_refused_and_its_computer_forgotten(owner, told):
    item = _mint(owner)
    computer = _as(item["token"])
    version = _announce(computer, _body())["version"]
    assert owner.delete(f"/api/integrations/tokens/{item['id']}").json() == {"ok": True, "revoked": True}
    assert _row(item["id"]) is None and item["id"] not in registry._live
    assert owner.delete(f"/api/integrations/tokens/{item['id']}").json() == {"ok": True, "revoked": False}
    assert computer.get("/api/computers/poll", params={"v": version, "wait": 0}).status_code == 401

    # revoked while a poll is held: the poll ends refused, and the row goes with it
    item = _mint(owner)
    computer = _as(item["token"])
    version = _announce(computer, _body())["version"]
    got = []
    poller = threading.Thread(target=lambda: got.append(_poll(computer, version, wait=10)))
    poller.start()
    assert _until(lambda: registry._live[item["id"]].holding == 1)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM integration_tokens WHERE id = ?", (item["id"],))
    registry.wake(item["id"], "announce")
    poller.join(5)
    assert got and got[0][0] == 401 and _row(item["id"]) is None


# --- the page's computers --------------------------------------------------------------------------

def test_the_page_shows_its_makers_computers_and_nobody_elses(owner, stranger, anon):
    lab = make_folder(owner, "CM lab")
    sub = make_folder(owner, "CM lab/Deeper")
    filed = make_page(owner, "CM deep paper", {"folders": [sub]})
    loose = make_page(owner, "CM loose paper")
    elsewhere = make_page(owner, "CM elsewhere")
    pc, laptop = _mint(owner), _mint(owner, "Gamma desktop: LAPTOP")
    _announce(_as(pc["token"]), _body({"kind": "folder", "folder_id": lab, "paused": True}))
    computer = _as(laptop["token"])
    version = _announce(computer, _body({"kind": "file", "page_id": loose["id"]},
                                        assistants=[{"name": "codex", "label": "Codex", "found": True, "ready": True,
                                                     "version": "0.167.0", "code": ""}],
                                        name="LAPTOP", computer_id="cm-laptop"))["version"]
    assert _poll(computer, version) == (200, {})
    shown = _shown(owner, filed["id"])                       # a subfolder's page: the folder vault keeps it
    assert [(c["name"], c["vault"]) for c in shown] == [("TIM-PC", {"kind": "folder", "name": "CM lab", "paused": True})]
    shown = _shown(owner, loose["id"])
    assert shown == [{"id": "cm-laptop", "name": "LAPTOP", "online": True, "since": None,
                      "vault": {"kind": "file", "name": "CM loose paper", "paused": False},
                      "assistants": [{"name": "codex", "label": "Codex", "found": True, "ready": True,
                                      "version": "0.167.0", "code": ""}]}]
    assert _shown(owner, elsewhere["id"]) == [] and _shown(owner, "no-such-page") == []

    # a restart forgets who is online, never when each was last seen
    registry.reset()
    shown = _shown(owner, loose["id"])
    assert shown[0]["online"] is False and shown[0]["since"] == _row(laptop["id"])["seen_at"]

    # nobody else: another member of a shared workspace, a stranger, a token, a guest, a share
    me, them = account_of("cptr_owner"), account_of("cptr_stranger")
    shared = workspaces.create("CM shared", me, kind="shared")["id"]
    workspaces.set_member(shared, them, "editor", me)
    there = {"X-Gamma-Workspace": shared}
    shared_page = owner.post("/api/blocks", json={"parent_id": "root", "content": "CM shared paper"}, headers=there).json()
    item = _mint(owner, **there)
    _announce(_as(item["token"]), _body({"kind": "file", "page_id": shared_page["id"]}))
    assert [c["name"] for c in _shown(owner, shared_page["id"], **there)] == ["TIM-PC"]
    assert _shown(stranger, shared_page["id"], **there) == []
    assert stranger.get(f"/api/pages/{loose['id']}/computer",
                        headers={"X-Gamma-Workspace": workspace_of("cptr_owner")}).status_code == 403
    read = owner.post("/api/integrations/tokens", json={"name": "Reader", "scope": "read"}).json()["token"]
    assert _as(read).get(f"/api/pages/{loose['id']}/computer").status_code == 403
    assert owner.get(f"/api/pages/{loose['id']}/computer", params={"share": "x"}).status_code == 404
    assert anon.get(f"/api/pages/{loose['id']}/computer").status_code == 401
    anon.post("/api/login-guest")
    assert anon.get(f"/api/pages/{loose['id']}/computer").status_code == 403


def test_an_expired_token_s_computer_goes_when_the_page_is_read(owner):
    page = make_page(owner, "CM expired paper")
    item = _mint(owner)
    _announce(_as(item["token"]), _body({"kind": "file", "page_id": page["id"]}))
    with connect_users_db() as conn:
        conn.execute("UPDATE integration_tokens SET expires_at = 1 WHERE id = ?", (item["id"],))
    assert _shown(owner, page["id"]) == [] and _row(item["id"]) is None


def test_the_vault_that_keeps_the_page_is_named_and_a_workspace_deleted_takes_its_computers(owner):
    nest = make_folder(owner, "CM nest")
    inner = make_folder(owner, "CM nest/Inner")
    page = make_page(owner, "CM nested paper", {"folders": [inner]})
    item = _mint(owner)
    computer = _as(item["token"])
    vaults = [{"kind": "folder", "folder_id": ROOT, "paused": True}, {"kind": "folder", "folder_id": inner},
              {"kind": "folder", "folder_id": nest, "paused": True}]
    _announce(computer, _body(*vaults))           # the whole library first, as the computer lists them by age
    assert _shown(owner, page["id"])[0]["vault"] == {"kind": "folder", "name": "Inner", "paused": False}
    _announce(computer, _body(*vaults, {"kind": "file", "page_id": page["id"], "paused": True}))
    assert _shown(owner, page["id"])[0]["vault"] == {"kind": "file", "name": "CM nested paper", "paused": True}

    # its workspace deleted, a computer's row and its presence go in that transaction
    me = account_of("cptr_owner")
    shared = workspaces.create("CM gone", me, kind="shared")["id"]
    gone = _mint(owner, **{"X-Gamma-Workspace": shared})
    _announce(_as(gone["token"]), _body())
    assert gone["id"] in registry._live
    with connect_users_db() as conn:
        forget_workspace(conn, shared)
    assert gone["id"] not in registry._live and _row(gone["id"]) is None


# --- the computer's side ----------------------------------------------------------------------------

@pytest.fixture
def host(owner, tmp_path, monkeypatch):
    """This server as the desktop app runs it (links kept here, its shell's
    secret set), the "other server" a TestClient of its own reached through
    ``gamma_sync.default_open`` (``answers`` overrides an answer by path,
    ``down`` makes it unreachable), polls held no time, the catalog's
    agents stood in for, and no thread unless a test starts one. What a test
    made goes with it."""
    monkeypatch.setenv("GAMMA_FOLDER_LINKS", "1")
    monkeypatch.setenv("GAMMA_SHELL_SECRET", SECRET)
    monkeypatch.setattr(folder_links, "run_in_background", lambda link_id, **kw: None)
    monkeypatch.setattr(channel, "POLL_S", 0)
    started = []
    monkeypatch.setattr(channel, "start", lambda link_id: started.append(link_id))
    found = {
        "codex": {"name": "codex", "label": "Codex", "found": True, "version": "codex-cli 0.167.0", "ready": True,
                  "problem": "", "models": [], "at": 0},
        "claude": {"name": "claude", "label": "Claude Code", "found": True, "version": "2.1.318", "ready": False,
                   "problem": catalog.NOT_SIGNED_IN["claude"], "models": [], "at": 0}}
    monkeypatch.setattr(catalog, "cached", lambda name: found[name])
    monkeypatch.setattr(catalog, "known", lambda name: found[name])
    http, answers, down = fresh_client(), {}, []

    def open_(method, path, headers, body=None):
        if down:
            raise gamma_sync.Unreachable("cannot reach http://testserver: refused")
        bare = path.split("?")[0]
        if bare in answers:
            return answers[bare], {}, io.BytesIO(b'{"detail": "Not Found"}')
        r = http.request(method, path, headers=headers, content=body)
        return r.status_code, r.headers, io.BytesIO(r.content)

    monkeypatch.setattr(gamma_sync, "default_open", open_)
    links_before = {link["id"] for link in folder_links.all_links()}
    yield {"url": "http://testserver", "started": started, "answers": answers, "down": down, "dir": tmp_path}
    channel.stop_all()
    for link in folder_links.all_links():
        if link["id"] not in links_before:
            folder_links._forget(link)
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE created_by = ?", (account_of("cptr_owner"),))


def _vault(host, ws, **fields) -> dict:
    """A vault of the "other server" on this computer, its row alone (no
    directory, no round): what the channel announces."""
    path = str(host["dir"] / f"vault-{time.monotonic_ns()}")
    return folder_links._insert(folder_links._new(ws, path, account_of("cptr_owner"),
                                                  **{"remote_url": host["url"], "token_id": "vault-token", **fields}))


def _link(owner, host, item, ws, **headers):
    return owner.post("/api/computer-links", json={"remote_url": host["url"] + "/", "workspace": ws,
                                                   "token": item["token"], "token_id": item["id"]},
                      headers={**SHELL, **headers})


def test_the_computer_links_take_the_shells_word_and_check_the_token(owner, stranger, host, monkeypatch):
    ws = workspace_of("cptr_owner")
    item = _mint(owner)
    assert _link(owner, host, item, ws, **{"X-Gamma-Shell": "wrong"}).status_code == 403
    r = _link(owner, host, item, ws)
    assert r.status_code == 201, r.text
    link = r.json()["link"]
    assert r.json()["replaced"] is None and host["started"] == [link["id"]]
    assert link["remote_url"] == host["url"] and link["workspace_id"] == ws and link["status"]["state"] == "connecting"
    assert (link["account"], link["username"]) == (account_of("cptr_owner"), "cptr_owner") and "token" not in link
    assert owner.get("/api/computer-links").json()["links"] == [link]
    assert stranger.get("/api/computer-links").json()["links"] == []
    with connect_users_db() as conn:
        sealed = conn.execute("SELECT token FROM computer_links WHERE id = ?", (link["id"],)).fetchone()[0]
    assert sealed and sealed != item["token"] and channel.get(link["id"], with_token=True)["token"] == item["token"]

    # a new token for the same server, workspace and account takes the link's place; the old one is the shell's
    again = _mint(owner)
    r = _link(owner, host, again, ws)
    assert r.status_code == 201 and r.json()["link"]["id"] == link["id"] and r.json()["replaced"] == {"token_id": item["id"]}

    # refused, of another workspace, older, unreachable
    owner.delete(f"/api/integrations/tokens/{item['id']}")
    r = _link(owner, host, item, ws)
    assert (r.status_code, r.json()["detail"]) == (400, "That server refused this computer's token.")
    assert _link(owner, host, again, "another-workspace").status_code == 400
    host["answers"]["/api/computers/announce"] = 404
    r = _link(owner, host, again, ws)
    assert (r.status_code, r.json()["detail"]) == (409, "That server is older than this app: it cannot show this computer.")
    host["answers"].clear()
    host["down"].append(True)
    assert _link(owner, host, again, ws).status_code == 502
    host["down"].clear()

    # removed with the shell's word, by its maker alone
    assert owner.delete(f"/api/computer-links/{link['id']}").status_code == 403
    assert stranger.delete(f"/api/computer-links/{link['id']}", headers=SHELL).status_code == 404
    r = owner.delete(f"/api/computer-links/{link['id']}", headers=SHELL)
    assert r.json() == {"ok": True, "token_id": again["id"]} and owner.get("/api/computer-links").json()["links"] == []

    # a token of another account there never takes a link: its vaults were made with the first one's tokens
    me, them = account_of("cptr_owner"), account_of("cptr_stranger")
    shared = workspaces.create("CM shared link", me, kind="shared")["id"]
    workspaces.set_member(shared, them, "editor", me)
    mine = _mint(owner, **{"X-Gamma-Workspace": shared})
    assert _link(owner, host, mine, shared).status_code == 201
    theirs = _mint(stranger, **{"X-Gamma-Workspace": shared})
    r = _link(owner, host, theirs, shared)
    assert r.status_code == 400 and "to another account there (cptr_owner)" in r.json()["detail"], r.text
    assert _row(theirs["id"])["vaults"] == []          # checked with an announce that named no vault
    stranger.delete(f"/api/integrations/tokens/{theirs['id']}")

    # nowhere else: a server that keeps no folders answers 404
    monkeypatch.delenv("GAMMA_FOLDER_LINKS")
    assert owner.get("/api/computer-links").status_code == 404
    assert _link(owner, host, again, ws).status_code == 404


def test_the_channel_announces_this_computers_vaults_of_that_server_and_shows_it(owner, host, told):
    ws = workspace_of("cptr_owner")
    folder = make_folder(owner, "CM kept")
    filed = make_page(owner, "CM kept paper", {"folders": [folder]})
    single = make_page(owner, "CM kept file")
    kept = _vault(host, ws, folder_id=folder)
    _vault(host, ws, kind="file", page_id=single["id"], pull=0, push=folder_links.PUSH_PDFS)
    _vault(host, ws, folder_id=folder, remote_url="http://elsewhere.example")      # another server's: not announced
    item = _mint(owner)
    link = _link(owner, host, item, ws).json()["link"]
    registry.reset()
    with connect_users_db() as conn:                                    # what the check's announce left goes
        conn.execute("DELETE FROM computers WHERE token_id = ?", (item["id"],))
    assert _shown(owner, filed["id"]) == []

    turn = channel._Channel(link["id"])
    assert turn.once() >= 0
    row = _row(item["id"])                                              # the channel's announce wrote it
    assert row["computer"] == channel.computer_id(host["url"]) and len(row["computer"]) == 22
    assert row["vaults"] == [{"kind": "folder", "folder_id": folder, "paused": False},
                             {"kind": "file", "page_id": single["id"], "paused": False}]
    assert [(a["name"], a["ready"], a["code"]) for a in row["assistants"]] == [("codex", True, ""),
                                                                                 ("claude", False, "signed_out")]
    assert channel.get(link["id"])["status"]["state"] == "connected"
    shown = _shown(owner, filed["id"])
    assert [(c["id"], c["online"], c["vault"]["name"]) for c in shown] == [(row["computer"], True, "CM kept")]
    assert "C:" not in json.dumps(row) and str(host["dir"]) not in json.dumps(row)    # never a path

    # a vault paused: the next turn announces it
    folder_links.set_paused(kept["id"], True)
    turn.once()
    assert _row(item["id"])["vaults"][0]["paused"] is True and _shown(owner, filed["id"])[0]["vault"]["paused"] is True
    # the server restarted: its poll asks for an announce, and the turn after announces again
    registry.reset()
    assert 0 < turn.once() <= channel.QUICK_S and turn.announced is None   # announces again, after a moment
    turn.once()
    assert registry.online(registry._live[item["id"]])


def test_the_channel_says_refused_older_and_unreachable(owner, host):
    ws = workspace_of("cptr_owner")
    item = _mint(owner)
    link = _link(owner, host, item, ws).json()["link"]
    turn = channel._Channel(link["id"])
    host["down"].append(True)
    assert (turn.once(), turn.once()) == (channel.RETRY_FIRST_S, channel.RETRY_FIRST_S * 2)
    status = channel.get(link["id"])["status"]
    assert status["state"] == "unreachable" and "cannot reach" in status["error"]
    host["down"].clear()
    turn.once()
    assert channel.get(link["id"])["status"]["state"] == "connected" and turn.backoff == channel.RETRY_FIRST_S
    host["answers"]["/api/computers/poll"] = 404
    assert turn.once() == channel.OLDER_S and channel.get(link["id"])["status"]["state"] == "older"
    host["answers"].clear()
    owner.delete(f"/api/integrations/tokens/{item['id']}")
    assert turn.once() == channel.REFUSED_S and channel.get(link["id"])["status"]["state"] == "refused"
    at = channel.get(link["id"])["status"]["at"]
    turn.once()
    assert channel.get(link["id"])["status"]["at"] == at                 # written only when it changes


def test_the_channels_thread_runs_until_its_link_goes(owner, host, monkeypatch):
    monkeypatch.setattr(channel, "start", REAL_START)
    ws = workspace_of("cptr_owner")
    item = _mint(owner)
    link = _link(owner, host, item, ws).json()["link"]
    running = channel._channels[link["id"]]
    assert _until(lambda: channel.get(link["id"])["status"]["state"] == "connected")
    assert owner.delete(f"/api/computer-links/{link['id']}", headers=SHELL).status_code == 200
    running.thread.join(5)
    assert not running.thread.is_alive() and link["id"] not in channel._channels

    # the tick starts a channel kept here and stops one whose row went
    item = _mint(owner)
    link = _link(owner, host, item, ws).json()["link"]
    channel.stop(link["id"])
    checked = []
    monkeypatch.setattr(channel, "check", lambda: (checked.append(True), REAL_CHECK())[1])
    monkeypatch.setattr(folder_links, "all_links", lambda: [])
    folder_links.tick()
    assert checked and channel._channels[link["id"]].thread.is_alive()
    running = channel._channels[link["id"]]
    with connect_users_db() as conn:
        conn.execute("DELETE FROM computer_links WHERE id = ?", (link["id"],))
    channel.check()
    running.thread.join(5)
    assert not running.thread.is_alive() and link["id"] not in channel._channels


REAL_CHECK = channel.check

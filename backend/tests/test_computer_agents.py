"""The agent of a work folder a computer keeps, the server's side (step 4d of
docs/research/agent-collab.md; gamma/computers/registry.py,
gamma/routers/computers.py): the agent a computer announces on for the
token's account, the agent of the work folders it keeps, shown to that
account alone and never another member's computer's; an older app's
announce naming its agent on the work folder's vault; the channel's routes
with a computer token alone; a mention made here a turn of its thread the
computer takes, a token's comment none, and none once the agent is off
there.

One process plays both sides (as tests/test_computers.py): the routes are
driven directly with the computer's token, as the computer's runner would.
The computer's side of a turn is tests/test_remote_chat.py's."""

import json
from types import SimpleNamespace

import pytest

from conftest import account_of, fresh_client, login, make_folder, make_user, mark_work_folder, token_for, \
    work_props
from gamma import storage, workspaces
from gamma.auth import DISK_CLIENT
from gamma.blocks_store import create_page
from gamma.computers import registry
from gamma.db import connect_pages_db, connect_users_db
from gamma.documents.model import document

MAKER, EDITOR, STRANGER = "cps_maker", "cps_editor", "cps_stranger"
FRAME = {"width": 600, "height": 800}
CODEX = [{"name": "codex", "label": "Codex", "found": True, "ready": True, "version": "0.167.0", "code": "",
          "enabled": True, "runs": "never"}]
OFF = [{**CODEX[0], "enabled": False}]   # Codex there, turned off


def _pdf() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    doc.new_page(width=600, height=800).insert_text((72, 100), "The loss is defined as follows.", fontsize=14)
    return doc.tobytes()


def _bearer(token: str):
    return fresh_client(headers={"Authorization": f"Bearer {token}"})


def _ok(r, status=200):
    assert r.status_code == status, r.text
    return r.json()


@pytest.fixture(scope="module")
def lab():
    """A shared workspace of cps_maker's, cps_editor an editor there: the
    work folder ``cps-paper`` with ``main.pdf`` (a stored one-page PDF),
    another work folder ``cps-other`` with ``other.pdf``, a plain folder."""
    for name in (MAKER, EDITOR, STRANGER):
        make_user(name, f"pw-{name}")
    maker, editor, stranger = (login(n, f"pw-{n}") for n in (MAKER, EDITOR, STRANGER))
    ws = workspaces.create("Computer sends lab", account_of(MAKER), kind="shared")["id"]
    workspaces.set_member(ws, account_of(EDITOR), "editor", by=account_of(MAKER))
    for c in (maker, editor):
        c.headers["X-Gamma-Workspace"] = ws
    W, W2, plain = make_folder(maker, "cps-paper"), make_folder(maker, "cps-other"), make_folder(maker, "cps-plain")
    mark_work_folder(maker, W)
    mark_work_folder(maker, W2)
    pdf, _ = storage.store_pdf(ws, _pdf())
    with connect_pages_db(ws) as conn:
        main = create_page(conn, "main", work_props(W, "main.pdf", doc=document(pdf, name="main.pdf")),
                           actor=account_of(MAKER), client=DISK_CLIENT)["id"]
        other = create_page(conn, "other", work_props(W2, "other.pdf"), actor=account_of(MAKER),
                            client=DISK_CLIENT)["id"]
    return SimpleNamespace(ws=ws, maker=maker, editor=editor, stranger=stranger, W=W, W2=W2, plain=plain, main=main,
                           other=other, passages=0)


@pytest.fixture(autouse=True)
def _forget(lab):
    """No computer, token or turn waiting of a test outlives it."""
    yield
    registry.reset()
    with connect_users_db() as conn:
        for name in (MAKER, EDITOR, STRANGER):
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of(name),))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of(name),))
    with connect_pages_db(lab.ws) as conn:
        conn.execute("UPDATE computer_chat_runs SET state = 'stopped' WHERE state IN ('queued', 'running')")
        conn.commit()


def _mint(c, name="Gamma desktop: TIM-PC") -> dict:
    return _ok(c.post("/api/integrations/tokens", json={"name": name, "scope": "computer", "expires_in_days": 365}),
               201)


def _announce(c, *vaults, name="TIM-PC", computer_id="cps-pc", assistants=CODEX) -> str:
    return _ok(c.post("/api/computers/announce", json={"computer": {"id": computer_id, "name": name}, "app": "v9",
                                                       "vaults": list(vaults), "assistants": assistants}))["version"]


def _kept(lab, **more) -> dict:
    return {"kind": "folder", "folder_id": lab.W, **more}


@pytest.fixture
def pc(lab):
    """The maker's computer, TIM-PC, keeping ``cps-paper`` with Codex."""
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    version = _announce(c, _kept(lab))
    return SimpleNamespace(c=c, id=item["id"], version=version)


def _passage(lab) -> str:
    """A new passage on main.pdf's page (a block of its own): its id."""
    lab.passages += 1
    block = f"cpsH{lab.passages}"
    pos = {"pageNumber": 1, **FRAME, "boundingRect": {"x1": 72, "y1": 86, "x2": 400, "y2": 104},
           "rects": [{"x1": 72, "y1": 86, "x2": 400, "y2": 104}]}
    _ok(lab.maker.post(f"/api/pages/{lab.main}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": block, "parent": lab.main, "content": "",
         "props": {"pdf_position": pos, "quote": "The loss is defined as follows"}}]}))
    return block


# --- the binding --------------------------------------------------------------------------------------

def test_the_announced_agent_is_its_work_folders_shown_to_its_account_alone(lab):
    item = _mint(lab.maker)
    c = _bearer(item["token"])
    _announce(c, _kept(lab, agent="claude"), {"kind": "folder", "folder_id": lab.plain},
              {"kind": "file", "page_id": lab.other},
              assistants=[*CODEX, {"name": "claude", "label": "Claude Code", "found": True, "ready": True,
                                   "enabled": True, "runs": "always"}, {"name": "gpt", "enabled": True}])
    with connect_users_db() as conn:
        vaults, assistants = (json.loads(v) for v in conn.execute(
            "SELECT vaults, assistants FROM computers WHERE token_id = ?", (item["id"],)).fetchone())
    # a vault names no agent (one a vault says is dropped); the assistants say which are on, an unknown one none
    assert vaults == [{"kind": "folder", "folder_id": lab.W, "paused": False},
                      {"kind": "folder", "folder_id": lab.plain, "paused": False},
                      {"kind": "file", "page_id": lab.other, "paused": False}]
    assert [(a["name"], a["enabled"], a["runs"]) for a in assistants] == [("codex", True, "never"),
                                                                          ("claude", True, "never")]
    # only a work folder's root here counts, with the first agent on, in the catalog's order
    assert registry.agent_folders(item["id"], lab.ws) == {lab.W: {"agent": "codex", "paused": False}}
    found = registry.agent_of(lab.ws, lab.W, account_of(MAKER))
    assert found == {
        "token_id": item["id"], "user_id": account_of(MAKER), "agent": "codex", "label": "Codex", "computer": "TIM-PC",
        "online": True, "paused": False, "ready": True, "code": "", "lent_by": ""}
    assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR)) is None        # another account's computer: none
    assert registry.agent_of(lab.ws, lab.W2, account_of(MAKER)) is None
    assert registry.agent_of(lab.ws, lab.plain, account_of(MAKER)) is None
    shown = _ok(lab.maker.get(f"/api/pages/{lab.main}/computer"))["computers"]
    assert shown[0]["vault"] == {"kind": "folder", "name": "cps-paper", "paused": False,
                                 "agent": {"name": "codex", "label": "Codex"}}
    assert _ok(lab.editor.get(f"/api/pages/{lab.main}/computer"))["computers"] == []
    # every channel route takes the computer's token alone, and it opens nothing outside the channel
    for c in (lab.maker, _bearer(token_for(lab.maker, "write"))):
        assert c.get("/api/computers/chats").status_code == 403
        assert c.post("/api/computers/chats/x/claim", json={"run": "r", "agent": "codex"}).status_code == 403
    assert _bearer(item["token"]).get("/api/annotations").status_code == 403


def test_an_older_apps_announce_names_its_agent_on_the_work_folders_vault(lab):
    """An app older than the computer's agents names none ``enabled`` and
    puts the agent on the folder vaults instead: that one is on, never
    asking."""
    item = _mint(lab.maker)
    old = [{k: v for k, v in CODEX[0].items() if k not in ("enabled", "runs")},
           {"name": "claude", "label": "Claude Code", "found": True, "ready": True}]
    _announce(_bearer(item["token"]), _kept(lab, agent="codex"), assistants=old)
    assert registry.agent_folders(item["id"], lab.ws) == {lab.W: {"agent": "codex", "paused": False}}
    assert registry.agent_of(lab.ws, lab.W, account_of(MAKER))["agent"] == "codex"
    _announce(_bearer(item["token"]), _kept(lab), assistants=old)          # no vault names one: none on
    assert registry.agent_folders(item["id"], lab.ws) == {}


def test_another_members_computer_never_takes_the_makers_binding(lab, pc):
    """An editor's computer announcing the maker's work folder with an agent
    serves that editor's own turns alone; a viewer's keeps no agent."""
    evil = _mint(lab.editor, "Gamma desktop: EVIL-PC")
    _announce(_bearer(evil["token"]), _kept(lab), name="EVIL-PC", computer_id="cps-evil")   # announced last, online
    assert registry.agent_of(lab.ws, lab.W, account_of(MAKER))["computer"] == "TIM-PC"
    assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR))["computer"] == "EVIL-PC"
    # the maker's mention (a turn of its thread) still goes to TIM-PC
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments",
                           json={"block_id": _passage(lab), "body": "@codex please tighten the intro"}), 201)
    assert [t["id"] for t in _ok(pc.c.get("/api/computers/chats"))["turns"]] == [r["turn"]["turn_id"]]
    # the editor's own @codex is the editor's turn, never listed to the maker's computer
    r = _ok(lab.editor.post(f"/api/pages/{lab.main}/comments", json={"block_id": _passage(lab), "body": "@codex hi"}),
            201)
    assert r["turn"]["turn_id"] not in [t["id"] for t in _ok(pc.c.get("/api/computers/chats"))["turns"]]
    assert [t["id"] for t in _ok(_bearer(evil["token"]).get("/api/computers/chats"))["turns"]] == \
        [r["turn"]["turn_id"]]
    # a viewer's computer keeps no agent
    viewer = "cps_viewer"
    make_user(viewer, f"pw-{viewer}")
    workspaces.set_member(lab.ws, account_of(viewer), "viewer", by=account_of(MAKER))
    v = login(viewer, f"pw-{viewer}")
    v.headers["X-Gamma-Workspace"] = lab.ws
    try:
        seen = _mint(v, "Gamma desktop: VIEW-PC")
        _announce(_bearer(seen["token"]), _kept(lab), name="VIEW-PC", computer_id="cps-view")
        with connect_users_db() as conn:
            kept = json.loads(conn.execute("SELECT vaults FROM computers WHERE token_id = ?",
                                           (seen["id"],)).fetchone()[0])
        assert kept == [{"kind": "folder", "folder_id": lab.W, "paused": False}]
        assert registry.agent_folders(seen["id"], lab.ws) == {}
        assert registry.agent_of(lab.ws, lab.W, account_of(viewer)) is None
        # an editor demoted to viewer: their computer serves nothing from then on
        workspaces.set_member(lab.ws, account_of(EDITOR), "viewer", by=account_of(MAKER))
        assert registry.agent_folders(evil["id"], lab.ws) == {}
        assert registry.agent_of(lab.ws, lab.W, account_of(EDITOR)) is None
    finally:
        workspaces.set_member(lab.ws, account_of(EDITOR), "editor", by=account_of(MAKER))
        with connect_users_db() as conn:
            conn.execute("DELETE FROM integration_tokens WHERE user_id = ?", (account_of(viewer),))
            conn.execute("DELETE FROM computers WHERE user_id = ?", (account_of(viewer),))


# --- the mention ----------------------------------------------------------------------------------------

def test_a_mention_made_here_is_a_turn_of_its_thread_the_computer_takes(lab, pc):
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments",
                           json={"block_id": _passage(lab), "body": "@codex please tighten the intro"}), 201)
    turn = r["turn"]
    assert (turn["agent"], turn["state"]) == ("Codex", "queued")
    assert [t["id"] for t in _ok(pc.c.get("/api/computers/chats"))["turns"]] == [turn["turn_id"]]
    claimed = _ok(pc.c.post(f"/api/computers/chats/{turn['turn_id']}/claim", json={"run": "cps-run-t", "agent": "codex"}))
    assert (claimed["turn"]["text"], claimed["turn"]["thread_id"]) == ("please tighten the intro", r["thread"]["id"])
    assert claimed["thread"]["id"] == r["thread"]["id"] and claimed["thread"]["place"]["page"] == 1
    # another member's @codex is no turn, nor a token's; nor the maker's once the agent is off there
    r = _ok(lab.editor.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "body": "@codex hi"}), 201)
    assert "turn" not in r
    script = _bearer(token_for(lab.maker, "write"))
    script.headers["X-Gamma-Workspace"] = lab.ws
    assert "turn" not in _ok(script.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main,
                                                                                   "body": "@codex hi"}), 201)
    _announce(pc.c, _kept(lab), assistants=OFF)
    assert registry.agent_folders(pc.id, lab.ws) == {} and registry.workspaces_of(account_of(MAKER)) == []
    r = _ok(lab.maker.post(f"/api/pages/{lab.main}/comments", json={"block_id": lab.main, "body": "@codex hi"}), 201)
    assert "turn" not in r

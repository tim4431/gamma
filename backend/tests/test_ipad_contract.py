"""The server answers the iPad's Swift host reads directly, pinned.

The app's sync logic is the web app's JavaScript (frontend/src/replica,
bundled by ipad/scripts/build-core.mjs): the browser suite's `replica`
group runs it against a real server and the shared fixtures (tests/shared)
pin its pure rules against the Python engine. What is Swift's alone has no
other test on this side: ipad/GammaIPad/Core/Remote.swift signs in through
the web session, mints the workspace's write token and moves files by
name, and ipad/GammaIPad/Core/Store.swift finds upload references with a
regular expression. Each test names the Swift that reads the answer; a
change to one changes the other (docs/dev/ipad.md "Keeping the host in
step")."""

import io
import re
import secrets

from fastapi.testclient import TestClient

from conftest import login, make_user, workspace_of
from gamma.app import app

PDF = b"%PDF-1.4\n" + b"ipad contract " * 40 + b"\n%%EOF\n"
# the smallest gamma-ink file: the generic upload refuses an .ink that is not one
INK = b'{"format":"gamma-ink","version":1,"space":{"kind":"pdf-page","page":1,"width":612,"height":792},"strokes":[]}'
# Store.swift: the pattern an upload reference is found by in a block
UPLOAD_REF = re.compile(r"/api/uploads/([0-9A-Za-z_-]+\.[0-9A-Za-z]{1,12})(?![0-9A-Za-z])")


def _ipad():
    make_user("ipad_contract", "pw")
    return login("ipad_contract", "pw"), workspace_of("ipad_contract")


def test_the_session_names_the_user_and_the_workspaces_the_app_offers():
    # ServerSetup.session: `user`, and `workspaces[]` with id, name, role, personal
    c, ws = _ipad()
    s = c.get("/api/session").json()
    assert s["user"] == "ipad_contract"
    mine = [w for w in s["workspaces"] if w["id"] == ws]
    assert mine and set(mine[0]) >= {"id", "name", "role", "personal"}
    assert mine[0]["personal"] is True and mine[0]["role"] == "owner"


def test_a_write_token_is_minted_with_the_web_session_and_opens_the_sync_feed():
    # ServerSetup.mintToken: POST with the session cookie and the workspace
    # header, 201 or 200, `token` in the body; the replica then starts every
    # round with GET /api/sync/whoami under that token
    c, ws = _ipad()
    r = c.post("/api/integrations/tokens", headers={"X-Gamma-Workspace": ws},
               json={"name": "iPad: contract", "scope": "write", "expires_in_days": 365})
    assert r.status_code in (200, 201), r.text
    token = r.json()["token"]
    device = TestClient(app)  # the token alone, as the replica's requests carry it
    bearer = {"Authorization": f"Bearer {token}", "X-Gamma-Workspace": ws}
    who = device.get("/api/sync/whoami", headers=bearer)
    assert who.status_code == 200, who.text
    assert who.json()["scope"] == "write" and who.json()["workspace"]["id"] == ws
    # a token is no session: minting another token with it is refused
    assert device.post("/api/integrations/tokens", headers=bearer,
                       json={"name": "x", "scope": "write"}).status_code in (401, 403)


def test_files_move_by_name_the_way_the_host_sends_and_fetches_them():
    # Remote.upload: a PDF to /api/uploads, anything else to /api/upload-file,
    # one multipart field `file`; Remote.head and Remote.download by name
    c, ws = _ipad()
    h = {"X-Gamma-Workspace": ws}
    r = c.post("/api/uploads", headers=h, files={"file": ("paper.pdf", io.BytesIO(PDF), "application/octet-stream")})
    assert r.status_code == 200, r.text
    pdf_name = f"{r.json()['doc_id']}.pdf"
    assert UPLOAD_REF.search(r.json()["source_url"]).group(1) == pdf_name
    r = c.post("/api/upload-file", headers=h, files={"file": ("group.ink", io.BytesIO(INK), "application/octet-stream")})
    assert r.status_code == 200, r.text
    ink_name = UPLOAD_REF.search(r.json()["url"]).group(1)
    assert ink_name.endswith(".ink")
    head = c.head(f"/api/uploads/{pdf_name}", headers=h)
    assert head.status_code == 200 and int(head.headers["content-length"]) == len(PDF)
    assert c.get(f"/api/uploads/{pdf_name}", headers=h).content == PDF
    assert c.get(f"/api/uploads/{ink_name}", headers=h).content == INK
    assert c.head(f"/api/uploads/{secrets.token_hex(12)}.pdf", headers=h).status_code == 404

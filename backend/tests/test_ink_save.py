"""Atomic open-format saves used by the iPad bridge; no native asset format."""
import copy
import uuid

from conftest import login, make_page, make_user


def ink():
    return {"format": "gamma-ink", "version": 1,
            "space": {"kind": "pdf-page", "page": 1, "width": 612, "height": 792},
            "strokes": [{"id": "stroke", "tool": "pen", "brush": "monoline", "ch": "xypt",
                         "pts": [1000, 2000, 200, 0, 3000, 0, 800, 16]}]}


def test_create_retry_conflict_and_empty_preserves_notes(guest):
    page = make_page(guest, "Native handwriting")
    target = uuid.uuid4().hex
    url = f"/api/blocks/{target}/ink"
    body = {"parent_id": page["id"], "expected_url": None, "ink": ink()}
    r = guest.put(url, json=body)
    assert r.status_code == 200, r.text
    saved = r.json()
    before = guest.get(f"/api/blocks/{page['id']}/children").json()
    retry = guest.put(url, json=body)
    assert retry.status_code == 200 and retry.json()["unchanged"]
    assert guest.get(f"/api/blocks/{page['id']}/children").json() == before
    assert guest.get(saved["properties"]["ink_url"]).json()["strokes"][0]["brush"] == "monoline"
    guest.put(f"/api/blocks/{target}", json={"content": "Keep my caption"})
    child = guest.post("/api/blocks", json={"parent_id": target, "content": "Keep my child"}).json()
    body["ink"]["strokes"][0]["pts"][0] = 5000
    assert guest.put(url, json=body).status_code == 409
    body["expected_url"] = saved["properties"]["ink_url"]
    r = guest.put(url, json=body)
    assert r.status_code == 200, r.text
    # The old base can never overwrite an intervening save.
    old = copy.deepcopy(body)
    old["ink"]["strokes"][0]["pts"][0] = 7000
    assert guest.put(url, json=old).status_code == 409
    body["expected_url"] = r.json()["properties"]["ink_url"]
    body["ink"]["strokes"] = []
    assert guest.put(url, json=body).status_code == 200
    block = guest.get(f"/api/blocks/{target}").json()
    assert block["content"] == "Keep my caption" and block["properties"]["ink_strokes"] == 0
    assert guest.get(f"/api/blocks/{child['id']}").status_code == 200
    guest.delete(f"/api/blocks/{target}")
    assert guest.put(url, json=body).status_code == 409


def test_scope_permissions_and_invalid_targets(anon):
    make_user("ipad_owner", "ipad-owner-password", is_admin=1)
    make_user("ipad_viewer", "ipad-viewer-password")
    owner = login("ipad_owner", "ipad-owner-password")
    viewer = login("ipad_viewer", "ipad-viewer-password")
    page = make_page(owner, "Private native ink")
    target = uuid.uuid4().hex
    url = f"/api/blocks/{target}/ink"
    body = {"parent_id": page["id"], "expected_url": None, "ink": ink()}
    assert anon.put(url, json=body).status_code == 401
    assert viewer.put(url, json=body).status_code == 404
    assert owner.put(f"/api/blocks/{page['id']}/ink", json=body).status_code == 404
    note = owner.post("/api/blocks", json={"parent_id": page["id"], "content": "ordinary"}).json()
    assert owner.put(f"/api/blocks/{note['id']}/ink", json=body).status_code == 409
    assert owner.put(url, json={**body, "ink": {"bad": True}}).status_code == 400
    assert owner.put(url, json={**body, "parent_id": "root"}).status_code == 404
    ws = owner.post("/api/workspaces", json={"name": "iPad shared", "kind": "shared", "owner": "ipad_owner"}).json()["id"]
    assert owner.put(f"/api/workspaces/{ws}/members/ipad_viewer", json={"role": "viewer"}).status_code == 200
    assert viewer.put(url, json=body, headers={"X-Gamma-Workspace": ws}).status_code == 403


def test_share_editor_cannot_write_outside_shared_page():
    make_user("ipad_share_owner", "ipad-share-password")
    make_user("ipad_share_editor", "ipad-share-password")
    owner = login("ipad_share_owner", "ipad-share-password")
    editor = login("ipad_share_editor", "ipad-share-password")
    page = make_page(owner, "Shared ink")
    other = make_page(owner, "Private")
    token = owner.post(f"/api/share/{page['id']}", json={"role": "edit"}).json()["token"]
    body = {"parent_id": page["id"], "expected_url": None, "ink": ink()}
    r = editor.put(f"/api/blocks/{uuid.uuid4().hex}/ink", params={"share": token}, json=body)
    assert r.status_code == 200, r.text
    body["parent_id"] = other["id"]
    assert editor.put(f"/api/blocks/{uuid.uuid4().hex}/ink", params={"share": token}, json=body).status_code == 403

"""One compare-and-set contract protects every client's ink replacements."""

from conftest import make_page


def test_stale_ink_batch_is_atomic_and_returns_current_group(guest):
    page = make_page(guest)["id"]
    def send(ops):
        return guest.post(f"/api/pages/{page}/ops", json={"client": "guard", "ops": ops})
    first = {"ink_url": "/api/uploads/aa.ink", "pdf_page": 1, "ink_strokes": 1}
    assert send([{"op": "insert", "id": "guardInk", "parent": page, "content": "caption", "props": first},
                 {"op": "insert", "id": "guardChild", "parent": "guardInk", "content": "nested note"}]).status_code == 200
    replacement = {"op": "set", "id": "guardInk", "base_props": {"ink_url": first["ink_url"]},
                   "props": {"ink_url": "/api/uploads/bb.ink", "ink_strokes": 2}}
    assert send([replacement]).status_code == 200
    assert send([replacement]).status_code == 200  # exact retry is harmless
    stale = {**replacement, "props": {"ink_url": "/api/uploads/cc.ink", "ink_strokes": 3}}
    response = send([{"op": "set", "id": "guardChild", "content": "must roll back"}, stale])
    assert response.status_code == 409
    body = response.json()
    assert body["conflict"] == "property_changed" and body["index"] == 1
    assert body["current"]["properties"]["ink_url"] == "/api/uploads/bb.ink"
    assert guest.get("/api/blocks/guardChild").json()["content"] == "nested note"
    assert send([{**stale, "base_props": None}]).status_code == 428
    # Geometry is protected even when the caller repeats the current URL.
    assert send([{**stale, "props": {"ink_url": "/api/uploads/bb.ink", "ink_strokes": 99}}]).status_code == 409
    assert send([{"op": "set", "id": "guardInk", "props": {"ink_strokes": 99}}]).status_code == 428
    # Empty handwriting is a saved group, not an instruction to erase notes.
    empty = {"ink_url": "/api/uploads/dd.ink", "ink_strokes": 0, "pdf_position": None}
    response = guest.put("/api/blocks/guardInk", json={"properties": empty, "base_props": {"ink_url": "/api/uploads/bb.ink"}})
    assert response.status_code == 200, response.text
    response = guest.put("/api/blocks/guardInk", json={"properties": first, "base_props": {"ink_url": "/api/uploads/bb.ink"}})
    assert response.status_code == 409 and response.json()["conflict"] == "property_changed"
    tree = guest.get("/api/blocks/guardInk/subtree").json()["block"]
    assert tree["content"] == "caption" and tree["children"][0]["content"] == "nested note"
    assert guest.get("/api/session").json()["capabilities"]["ink_base_props"] is True


def test_ink_conflict_cannot_reveal_another_page(guest):
    from fastapi.testclient import TestClient
    from gamma.app import app
    page = make_page(guest)["id"]
    group = guest.post("/api/blocks", json={"parent_id": page, "properties": {"ink_url": "/api/uploads/aa.ink"}}).json()
    outsider = TestClient(app)
    response = outsider.post(f"/api/pages/{page}/ops", json={"ops": [{"op": "set", "id": group["id"],
                           "props": {"ink_url": "/api/uploads/bb.ink"}, "base_props": {"ink_url": None}}]})
    assert response.status_code in (401, 403)
    assert "current" not in response.json()

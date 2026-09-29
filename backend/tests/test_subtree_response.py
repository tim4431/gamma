"""GET /blocks/{id}/subtree answers a response it serialized in its worker
thread: a returned dict would be encoded (jsonable_encoder, then json.dumps)
on the event loop, which a 5,000-block page stalls for ~175 ms."""

import fastapi.routing

from conftest import make_page


def test_the_subtree_is_serialized_in_the_worker_thread(guest, monkeypatch):
    real = fastapi.routing.jsonable_encoder

    def watching(obj, *args, **kwargs):
        assert not (isinstance(obj, dict) and "block" in obj), "the subtree was encoded on the event loop"
        return real(obj, *args, **kwargs)

    monkeypatch.setattr(fastapi.routing, "jsonable_encoder", watching)
    page = make_page(guest, "Serialized in the worker")
    guest.post("/api/blocks", json={"parent_id": page["id"], "content": "a child"}).raise_for_status()
    r = guest.get(f"/api/blocks/{page['id']}/subtree")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["block"]["id"] == page["id"] and body["block"]["children"][0]["content"] == "a child"
    assert isinstance(body["seq"], int)

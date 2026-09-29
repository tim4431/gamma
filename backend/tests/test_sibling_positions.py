"""Sibling keys are unique under a parent, whichever writer mints them: a
page made through POST /api/blocks without neighbours goes last in the
library (the app's "Duplicate" sends that), and every key a client names —
a page's, a cross-page move's — is re-keyed like an op's when a sibling
holds it (blocks_store.free_position)."""

from conftest import login, make_user


def _keys(client, parent):
    return [b["position"] for b in client.get(f"/api/blocks/{parent}/children").json()["children"]]


def _unique(keys):
    return len(keys) == len(set(keys))


def test_pages_made_without_neighbours_go_last_with_their_own_keys():
    make_user("sp_pages", "sppagespw1")
    c = login("sp_pages", "sppagespw1")
    made = [c.post("/api/blocks", json={"parent_id": "root", "content": f"Copy {i}"}).json() for i in range(3)]
    keys = _keys(c, "root")
    assert _unique(keys)
    listing = [b["id"] for b in c.get("/api/blocks/root/children").json()["children"]]
    assert listing[-3:] == [p["id"] for p in made]
    # two tabs appending after the same last page name the same key: the
    # second is re-keyed, not stored twice
    last = keys[-1]
    one = c.post("/api/blocks", json={"parent_id": "root", "content": "Tab 1", "before": last}).json()
    two = c.post("/api/blocks", json={"parent_id": "root", "content": "Tab 2", "before": last}).json()
    assert one["position"] != two["position"]
    assert _unique(_keys(c, "root"))


def test_a_cross_page_move_is_rekeyed_on_a_clash():
    make_user("sp_moves", "spmovespw1")
    c = login("sp_moves", "spmovespw1")
    dst = c.post("/api/pages", json={"title": "Destination"}).json()["id"]
    src = c.post("/api/pages", json={"title": "Source"}).json()["id"]
    c.post("/api/blocks", json={"parent_id": dst, "content": "A already here", "after": "a1"}).raise_for_status()
    for i in range(2):
        b = c.post("/api/blocks", json={"parent_id": src, "content": f"B{i} moves over"}).json()
        r = c.post(f"/api/blocks/{b['id']}/reorder", json={"parent_id": dst})
        assert r.status_code == 200, r.text
    keys = _keys(c, dst)
    assert len(keys) == 3 and _unique(keys)

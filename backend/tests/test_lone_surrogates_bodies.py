"""Half an emoji ("\\ud83d" in the JSON a browser sends) never makes a 500:
the request bodies of the metadata edit, a folder's rename (an op on the
folder tree) and the chat writes store it — and answer it — as U+FFFD (ops.StorableBody)."""

import json

from conftest import login, make_folder, make_user

HALF = "\\ud83d"  # the JSON escape of a lone high surrogate


def _post(client, method, path, body: str):
    return client.request(method, path, content=body.encode(), headers={"Content-Type": "application/json"})


def test_metadata_folders_and_chats_take_half_an_emoji():
    make_user("lsb_owner", "lsbownerpw1")
    c = login("lsb_owner", "lsbownerpw1")
    folder = make_folder(c, "Reading")
    page = c.post("/api/pages", json={"title": "Paper", "folders": [folder]}).json()

    r = _post(c, "POST", "/api/metadata/update",
              json.dumps({"block_id": page["id"], "meta": {"title": "x"}}).replace('"x"', f'"Title {HALF}"'))
    assert r.status_code == 200, r.text
    assert r.json()["meta"]["title"] == "Title �"

    r = _post(c, "POST", "/api/pages/folders/ops",
              f'{{"ops": [{{"op": "set", "id": "{folder}", "content": "Read {HALF}"}}]}}')
    assert r.status_code == 200, r.text
    assert c.get(f"/api/blocks/{folder}").json()["content"] == "Read �"

    r = _post(c, "PUT", f"/api/chats/{page['id']}",
              f'{{"messages": [{{"role": "user", "text": "hi {HALF}"}}], "title": "T {HALF}"}}')
    assert r.status_code == 200, r.text
    got = c.get(f"/api/chats/{page['id']}").json()
    assert got["title"] == "T �" and got["messages"][0]["text"] == "hi �"

    r = _post(c, "POST", "/api/chat-history/archive",
              f'{{"bucket": "{page["id"]}", "messages": [{{"role": "user", "text": "old {HALF}"}}], "title": "A {HALF}"}}')
    assert r.status_code == 200, r.text
    entry = r.json()["id"]
    r = _post(c, "PUT", f"/api/chat-history/{entry}", f'{{"title": "Renamed {HALF}"}}')
    assert r.status_code == 200, r.text
    sessions = c.get(f"/api/chat-history?bucket={page['id']}").json()["sessions"]
    assert any(s["title"] == "Renamed �" for s in sessions)

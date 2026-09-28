"""Deleting a folder never deletes a conversation: "Keep pages" and "Delete
pages too" both send POST /folders/rename with dst "" — the folder's
active chats are filed into their history, which stays under the folder's
bucket, and the pages' own chats stay with the pages in Recently deleted,
so a restored page (and the folder it brings back) finds them all."""


def _say(text):
    return [{"role": "user", "text": text}]


def _sessions(client, bucket):
    return [s["preview"] for s in client.get(f"/api/chat-history?bucket={bucket}").json()["sessions"]]


def test_delete_folder_with_its_pages_keeps_every_chat(guest):
    page = guest.post("/api/pages", json={"title": "In lab", "properties": {"folder": "fdk-lab"}}).json()
    guest.put(f"/api/chats/{page['id']}", json={"messages": _say("page thread")}).raise_for_status()
    guest.put("/api/chats/home:fdk-lab", json={"messages": _say("folder thread")}).raise_for_status()
    guest.post("/api/chat-history/archive",
               json={"bucket": "home:fdk-lab/sub", "messages": _say("older sub thread")}).raise_for_status()

    # "Delete 1 page too": the page goes to Recently deleted, then the folder's buckets follow
    assert guest.delete(f"/api/blocks/{page['id']}").status_code == 200
    r = guest.post("/api/folders/rename", json={"src": "fdk-lab", "dst": ""})
    assert r.status_code == 200 and r.json()["archived"] == 1

    assert guest.get("/api/chats/home:fdk-lab").json()["messages"] == []
    assert _sessions(guest, "home:fdk-lab") == ["folder thread"]
    assert _sessions(guest, "home:fdk-lab/sub") == ["older sub thread"]

    # the page comes back with its folder label and its chat
    assert guest.post(f"/api/trash/{page['id']}/restore").status_code == 200
    assert guest.get(f"/api/chats/{page['id']}").json()["messages"] == _say("page thread")
    back = guest.get(f"/api/blocks/{page['id']}").json()
    assert back["properties"].get("folder") == "fdk-lab"


def test_merging_folders_files_the_source_chat_into_the_destination(guest):
    guest.post("/api/chat-history/archive",
               json={"bucket": "home:fdk-a", "messages": _say("older a")}).raise_for_status()
    guest.put("/api/chats/home:fdk-a", json={"messages": _say("from a")}).raise_for_status()
    guest.put("/api/chats/home:fdk-b", json={"messages": _say("in b")}).raise_for_status()
    r = guest.post("/api/folders/rename", json={"src": "fdk-a", "dst": "fdk-b"})
    assert r.json() == {"ok": True, "moved": 1, "history_moved": 1, "archived": 1, "shares_moved": 0}
    assert guest.get("/api/chats/home:fdk-b").json()["messages"] == _say("in b")
    assert sorted(_sessions(guest, "home:fdk-b")) == ["from a", "older a"]
    assert _sessions(guest, "home:fdk-a") == []

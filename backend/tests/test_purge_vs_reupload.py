"""The 30-day purge and a re-upload of the same bytes never cross: the
upload's dedup hit re-dates the stored file under the lock the purge holds
from its check to its delete (upload_gc.guard), so a picture pasted again
while its old copy is being purged is on disk when the block naming it is
written."""

import os
import threading
import time

from conftest import login, make_user, workspace_of
from gamma import upload_gc
from gamma.db import connect_pages_db, ws_uploads_dir
from gamma.storage import store_file

DATA = b"\x89PNG\r\n\x1a\n" + b"an old picture, pasted again"


def test_a_reupload_during_the_purge_keeps_the_file(monkeypatch):
    make_user("pvr_owner", "pvrownerpw1")
    c = login("pvr_owner", "pvrownerpw1")
    ws = workspace_of("pvr_owner")
    name, _ = store_file(ws, DATA, ".png")
    path = ws_uploads_dir(ws) / name
    pid = c.post("/api/pages", json={"title": "Some page"}).json()["id"]
    c.post(f"/api/pages/{pid}/ops", json={"client": "x", "ops": [
        {"op": "insert", "id": "pvrKeep", "parent": pid, "content": "text"}]}).raise_for_status()
    old = time.time() - 40 * 86400
    os.utime(path, (old, old))
    assert name in upload_gc.reconcile(ws)["recorded"]
    with connect_pages_db(ws) as conn:
        conn.execute("UPDATE upload_orphans SET since = '2020-01-01T00:00:00Z'")
        conn.commit()

    real_blocker = upload_gc.purge_blocker
    answers = []

    def slow_blocker(conn, due, stored):
        # the user pastes the same picture while the purge checks the database
        paste = threading.Thread(target=lambda: answers.append(store_file(ws, DATA, ".png")))
        paste.start()
        time.sleep(0.3)
        return real_blocker(conn, due, stored)

    monkeypatch.setattr(upload_gc, "purge_blocker", slow_blocker)
    result = upload_gc.reconcile(ws)
    for _ in range(100):
        if answers:
            break
        time.sleep(0.05)
    assert answers and answers[0][0] == name
    assert result["purged"] == [name]  # the purge went first ...
    assert path.is_file()  # ... and the paste stored the bytes again after it

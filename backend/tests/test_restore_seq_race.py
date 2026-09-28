"""A replace restore holds the workspace's write lock from the moment it
reads the live op log until the restored copy is in (ws_backup._replace): a
batch committed while it runs waits and lands above the restore's
``reload``, so no seq a client was given is ever handed out again."""

import threading
import time

from conftest import login, make_user, workspace_of
from gamma import ops, ws_backup
from gamma.db import connect_pages_db


def test_a_batch_committed_during_a_replace_restore_lands_above_its_reload(monkeypatch):
    make_user("rsr_owner", "rsrownerpw1")
    c = login("rsr_owner", "rsrownerpw1")
    ws = workspace_of("rsr_owner")
    pid = c.post("/api/pages", json={"title": "restore race"}).json()["id"]

    def post(batch, client):
        r = c.post(f"/api/pages/{pid}/ops", json={"client": client, "ops": batch})
        assert r.status_code == 200, r.text
        return r.json()["seq"]

    post([{"op": "insert", "id": "rsrK", "parent": pid, "content": "in the backup"}], "tabA")
    snap = ws_backup.create(ws, label="before")
    for i in range(3):
        post([{"op": "insert", "id": f"rsrL{i}", "parent": pid, "content": f"after the backup {i}"}], "tabA")

    real = ops.log_reload
    acked = {}

    def tab_b_types():
        acked["seq"] = ops.commit_ops(ws, pid, [{"op": "insert", "id": "rsrB", "parent": pid,
                                                "content": "typed during the restore"}],
                                      actor="rsr_owner", client="tabB")["seq"]

    writer = threading.Thread(target=tab_b_types)

    def log_reload_while_tab_b_types(conn, page_id, actor, *, after=0):
        if not writer.is_alive() and "seq" not in acked:
            writer.start()  # tab B's flush arrives while the restore is running
            time.sleep(0.3)
        return real(conn, page_id, actor, after=after)

    monkeypatch.setattr(ops, "log_reload", log_reload_while_tab_b_types)
    r = c.post(f"/api/workspaces/{ws}/backups/{snap['name']}/restore?mode=replace")
    assert r.status_code == 200, r.text
    writer.join(timeout=30)
    assert "seq" in acked

    with connect_pages_db(ws) as conn:
        log = conn.execute("SELECT seq, client, ops FROM page_ops WHERE page_id = ? ORDER BY seq", (pid,)).fetchall()
    seqs = [row[0] for row in log]
    assert len(seqs) == len(set(seqs))
    reload_seq = next(row[0] for row in log if row[2] == '[{"op": "reload"}]')
    # the batch tab B was told about is in the log under its seq, above the reload
    assert acked["seq"] > reload_seq
    assert next(row[1] for row in log if row[0] == acked["seq"]) == "tabB"
    assert c.get(f"/api/blocks/{pid}/subtree").json()["seq"] == acked["seq"]

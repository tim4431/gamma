"""The per-page op log is bounded by rows, age and payload bytes (a set logs
the block's whole text, so typing in one long block used to keep 2,000
copies of it), and a catch-up longer than the client should replay answers
410 — the client refetches the tree instead."""

from datetime import datetime, timedelta, timezone

from conftest import login, make_page, make_user

from gamma import ops
from gamma.db import connect_pages_db

FMT = "%Y-%m-%dT%H:%M:%S.%fZ"


def _log_rows(ws, page_id):
    with connect_pages_db(ws) as conn:
        return conn.execute("SELECT seq, at, length(ops) FROM page_ops WHERE page_id = ? ORDER BY seq",
                            (page_id,)).fetchall()


def _setup(name):
    ws = make_user(name, "ob-password-1")
    client = login(name, "ob-password-1")
    page = make_page(client, "Long note")["id"]
    return ws, client, page


def _type(ws, page, block, text, n):
    """``n`` flushed typing batches on one block, each logging its whole text."""
    with connect_pages_db(ws) as conn:
        for i in range(n):
            text += "x" if i % 10 else " "
            ops.apply_ops(conn, page, [{"op": "set", "id": block, "content": text}], actor="t")
    return text


def _contiguous(rows):
    seqs = [r[0] for r in rows]
    return seqs == list(range(seqs[0], seqs[0] + len(seqs)))


def test_long_block_typing_keeps_the_log_small():
    """The audit's repro, scaled: every set of a 20 KB block logs 20 KB. The
    log stays near KEEP_OPS_BYTES instead of KEEP_OPS copies of the text,
    and a catch-up from far back is a 410, not a response of megabytes."""
    ws, client, page = _setup("ob_typing")
    body = ("A long literature-review paragraph with $math$ and [[refs]]. " * 330)[:20000]
    assert client.post(f"/api/pages/{page}/ops", json={"client": "t", "ops": [
        {"op": "insert", "id": "long", "parent": page, "content": body}]}).status_code == 200
    _type(ws, page, "long", body, 600)
    rows = _log_rows(ws, page)
    total = sum(r[2] for r in rows)
    batch = rows[-1][2]
    assert total <= ops.KEEP_OPS_BYTES + ops.PRUNE_EVERY * batch, total
    assert rows[-1][0] == 601 and _contiguous(rows)
    r = client.get(f"/api/pages/{page}/ops", params={"since": 601 - 590})
    assert r.status_code == 410
    # a short catch-up still replays
    r = client.get(f"/api/pages/{page}/ops", params={"since": 598})
    assert r.status_code == 200 and [b["seq"] for b in r.json()["batches"]] == [599, 600, 601]


def test_row_bound(monkeypatch):
    monkeypatch.setattr(ops, "KEEP_OPS", 20)
    monkeypatch.setattr(ops, "PRUNE_EVERY", 4)
    ws, client, page = _setup("ob_rows")
    with connect_pages_db(ws) as conn:
        for i in range(50):
            ops.apply_ops(conn, page, [{"op": "insert", "id": f"r{i}", "parent": page, "content": "x"}], actor="t")
    rows = _log_rows(ws, page)
    assert 20 <= len(rows) < 20 + 4 and rows[-1][0] == 50 and _contiguous(rows)


def test_age_bound_keeps_the_newest_row(monkeypatch):
    ws, client, page = _setup("ob_age")
    old = datetime.now(timezone.utc) - timedelta(hours=ops.KEEP_OPS_HOURS + 2)
    monkeypatch.setattr(ops, "page_now", lambda: old.strftime(FMT))
    with connect_pages_db(ws) as conn:
        for i in range(5):
            ops.apply_ops(conn, page, [{"op": "insert", "id": f"a{i}", "parent": page, "content": "x"}], actor="t")
    assert len(_log_rows(ws, page)) == 5  # no prune check yet
    monkeypatch.undo()
    monkeypatch.setattr(ops, "PRUNE_EVERY", 1)
    with connect_pages_db(ws) as conn:
        ops.apply_ops(conn, page, [{"op": "insert", "id": "fresh", "parent": page, "content": "x"}], actor="t")
    assert [r[0] for r in _log_rows(ws, page)] == [6]  # the day-old rows went, the newest stays
    # seq continues from the kept row, and a client at seq 5 can still catch up
    r = client.get(f"/api/pages/{page}/ops", params={"since": 5})
    assert r.status_code == 200 and [b["seq"] for b in r.json()["batches"]] == [6]
    assert client.get(f"/api/pages/{page}/ops", params={"since": 4}).status_code == 410


def test_an_oversized_batch_prunes_at_once_and_stays(monkeypatch):
    monkeypatch.setattr(ops, "KEEP_OPS_BYTES", 40_000)
    ws, client, page = _setup("ob_big")
    with connect_pages_db(ws) as conn:
        for i in range(3):
            ops.apply_ops(conn, page, [{"op": "insert", "id": f"b{i}", "parent": page, "content": "y" * 1000}],
                          actor="t")
        # bigger than the whole budget: checked right away, older rows dropped, itself kept
        ops.apply_ops(conn, page, [{"op": "insert", "id": "huge", "parent": page, "content": "z" * 60_000}],
                      actor="t")
        assert [r[0] for r in _log_rows(ws, page)] == [4]
        assert ops.apply_ops(conn, page, [{"op": "set", "id": "huge", "content": "small"}], actor="t")["seq"] == 5


def test_catchup_caps(monkeypatch):
    ws, client, page = _setup("ob_catchup")
    with connect_pages_db(ws) as conn:
        for i in range(12):
            ops.apply_ops(conn, page, [{"op": "insert", "id": f"c{i}", "parent": page, "content": "c" * 100}],
                          actor="t")
    monkeypatch.setattr(ops, "CATCHUP_MAX_BATCHES", 5)
    r = client.get(f"/api/pages/{page}/ops", params={"since": 7})
    assert r.status_code == 200 and [b["seq"] for b in r.json()["batches"]] == [8, 9, 10, 11, 12]
    assert client.get(f"/api/pages/{page}/ops", params={"since": 6}).status_code == 410
    monkeypatch.setattr(ops, "CATCHUP_MAX_BATCHES", 200)
    monkeypatch.setattr(ops, "CATCHUP_MAX_BYTES", 500)
    assert client.get(f"/api/pages/{page}/ops", params={"since": 11}).status_code == 200
    assert client.get(f"/api/pages/{page}/ops", params={"since": 7}).status_code == 410

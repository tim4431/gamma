"""A Zotero link is settled after a test's rounds: one round more writes
nothing on either side ("a round after a round writes nothing"; see
docs/dev/zotero_sync.md#tests). No write request reaches the fake Zotero, no
op is logged on any page, no page moves in the change feed and no block
changes.

The engine harness's ``me`` fixture (zotero_engine) runs ``Idle.check`` on
the owner's links before it removes them (detached links, and links no
round ran for, are passed by). A test that leaves a link unsettled on
purpose (a unit stubbed out, a failure it injects that outlives it) says so
with ``idle.skip(reason)``."""

import hashlib
import json

WRITES = ("POST", "PATCH", "PUT", "DELETE")


def snapshot(ws: str) -> dict:
    """What a round could change on Gamma's side: each page's newest logged
    op, the change feed's newest seq, and a digest of every block."""
    from gamma.db import connect_pages_db

    with connect_pages_db(ws) as conn:
        ops = dict(conn.execute("SELECT page_id, MAX(seq) FROM page_ops GROUP BY page_id").fetchall())
        feed = conn.execute("SELECT COALESCE(MAX(seq), 0) FROM page_changes").fetchone()[0]
        digest = hashlib.sha256()
        for row in conn.execute("SELECT id, parent_id, position, content, properties, updated_at "
                                "FROM unified_blocks ORDER BY id"):
            digest.update(json.dumps(row, ensure_ascii=False).encode("utf-8"))
    return {"ops": ops, "feed": feed, "blocks": digest.hexdigest()}


def _ops_since(ws: str, before: dict) -> list:
    from gamma.db import connect_pages_db

    with connect_pages_db(ws) as conn:
        rows = conn.execute("SELECT page_id, seq, actor, client, ops FROM page_ops").fetchall()
    return [(page, seq, actor, client, ops[:300]) for page, seq, actor, client, ops in rows
            if seq > before["ops"].get(page, 0)]


def assert_idle(fake, ws: str) -> dict:
    """Run one more round of the link ``ws`` (as the clock would: no Sync
    now's lifts) and fail unless it wrote nothing on either side. Returns
    its status."""
    from zotero_engine import drop_pending

    from gamma import zotero_sync

    link = zotero_sync.get_link(ws)
    if link is None or link["mode"] == "off" or not (link["status"].get("last_sync")
                                                    or link["status"].get("last_attempt")):
        return {}  # detached, or no round ran: the next one is its first
    drop_pending(ws)
    calls, before = len(fake.calls), snapshot(ws)
    status = zotero_sync.sync_workspace(ws)
    writes = [(method, path) for method, path, _ in fake.calls[calls:] if method in WRITES]
    assert writes == [], f"a round after the test's rounds wrote to Zotero: {writes}"
    after = snapshot(ws)
    assert after["ops"] == before["ops"], f"a round after the test's rounds wrote ops: {_ops_since(ws, before)}"
    assert after == before, "a round after the test's rounds changed blocks or the change feed"
    return status


class Idle:
    """The check the ``me`` fixture (zotero_engine) runs on the owner's links
    after the test."""

    def __init__(self):
        self.reason = ""

    def skip(self, reason: str) -> None:
        """This test leaves its links unsettled on purpose (``reason``)."""
        self.reason = reason

    def check(self, fake, workspaces) -> None:
        if self.reason:
            return
        for ws in workspaces:
            assert_idle(fake, ws)

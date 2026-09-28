"""Endpoints that touch a database are sync defs and run side by side in the
threadpool (docs/dev/debugging.md "Gotchas"), so every check-then-write
among them must be one step: a write lock taken before the check, or an
insert that lets a constraint decide. Each test starts several requests at
once on a barrier, with the gap between the check and the write widened
where there is one to widen, and looks at what is left."""

import threading
import time
import traceback
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from conftest import login, make_user

PASSWORD = "cw-pass-1234"


@pytest.fixture(scope="module")
def owner():
    make_user("cw_owner", PASSWORD)
    return login("cw_owner", PASSWORD)


def _clients(template: TestClient, n: int) -> list[TestClient]:
    """``n`` clients with the template's session: one per thread."""
    from gamma.app import app
    out = []
    for _ in range(n):
        c = TestClient(app)
        c.cookies.update(template.cookies)
        out.append(c)
    return out


def _at_once(calls):
    """Run the callables in threads started together; their results (or the
    exceptions they raised) in order."""
    barrier = threading.Barrier(len(calls))
    results = [None] * len(calls)

    def run(i, fn):
        barrier.wait()
        started = time.monotonic()
        try:
            results[i] = fn()
        except Exception as e:  # noqa: BLE001 — the test reads it; the note says where and when
            where = traceback.extract_tb(e.__traceback__)[-4:]
            e.add_note(f"after {time.monotonic() - started:.2f} s at " +
                       " <- ".join(f"{Path(f.filename).name}:{f.lineno} {f.line}" for f in reversed(where)))
            results[i] = e

    threads = [threading.Thread(target=run, args=(i, fn)) for i, fn in enumerate(calls)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(60)
    return results


def _failed(results, ok=(200,)) -> list[str]:
    """The results that are exceptions (with where they were raised) or
    another status than ``ok``."""
    return [f"{r!r} {' '.join(getattr(r, '__notes__', []))}" if isinstance(r, Exception)
            else f"{r.status_code} {r.text[:200]}"
            for r in results if isinstance(r, Exception) or r.status_code not in ok]


def _slowed(monkeypatch, module, name, seconds=0.05):
    """Make ``module.name`` take a moment: the window between a request's
    check and its write, wide enough for the others to walk into."""
    real = getattr(module, name)

    def slow(*args, **kwargs):
        out = real(*args, **kwargs)
        time.sleep(seconds)
        return out
    monkeypatch.setattr(module, name, slow)


def test_sharing_a_page_at_once_makes_one_link(owner, monkeypatch):
    from gamma.routers import shares
    page = owner.post("/api/pages", json={"title": "Shared at once"}).json()["id"]
    _slowed(monkeypatch, shares, "_validated")  # between "no share yet" and the insert
    results = _at_once([lambda c=c: c.post(f"/api/share/{page}") for c in _clients(owner, 6)])
    assert not _failed(results), _failed(results)
    assert len({r.json()["token"] for r in results}) == 1
    assert owner.get(f"/api/share-settings/{page}").json()["token"] == results[0].json()["token"]


def test_creating_a_page_under_one_id_at_once_makes_one_page(owner, monkeypatch):
    from gamma import publish
    _slowed(monkeypatch, publish, "cap_refusal")  # between the id check and the insert
    results = _at_once([lambda c=c: c.post("/api/pages", json={"id": "cw-mirrored-page", "title": "Brought over"})
                        for c in _clients(owner, 6)])
    assert not _failed(results, (200, 400, 409)), _failed(results, (200, 400, 409))
    assert sorted(r.status_code for r in results) == [200] + [409] * 5
    listing = owner.get("/api/blocks/root/children").json()["children"]
    assert [p["id"] for p in listing].count("cw-mirrored-page") == 1


def test_two_owners_demoting_each_other_at_once_leave_an_owner(owner, monkeypatch):
    from gamma import workspaces
    make_user("cw_second", PASSWORD, is_admin=0)
    second = login("cw_second", PASSWORD)
    info = workspaces.create("Two owners", "cw_owner", kind="shared")
    ws = info["id"]
    workspaces.set_member(ws, "cw_second", "owner", by="cw_owner")
    _slowed(monkeypatch, workspaces, "_is_last_owner")  # between the owner count and the write
    results = _at_once([
        lambda: owner.put(f"/api/workspaces/{ws}/members/cw_second", json={"role": "editor"}),
        lambda: second.put(f"/api/workspaces/{ws}/members/cw_owner", json={"role": "editor"}),
    ])
    assert not _failed(results, (200, 400, 409)), _failed(results, (200, 400, 409))
    assert sorted(r.status_code for r in results) == [200, 400]
    assert [m["username"] for m in workspaces.members(ws) if m["role"] == "owner"] != []


def test_op_batches_posted_at_once_all_land_in_order(owner):
    """The op endpoint in several threads at once: every batch applied, each
    under its own seq, the log without gaps."""
    page = owner.post("/api/pages", json={"title": "Typed at once"}).json()["id"]
    n = 8

    def batch(i, c):
        return c.post(f"/api/pages/{page}/ops", json={
            "client": f"cw{i}", "ops": [{"op": "insert", "id": f"cw-block-{i}", "parent": page,
                                         "content": f"line {i}", "props": {}}]})

    results = _at_once([lambda i=i, c=c: batch(i, c) for i, c in enumerate(_clients(owner, n))])
    assert not _failed(results), _failed(results)
    seqs = sorted(r.json()["seq"] for r in results)
    assert seqs == list(range(seqs[0], seqs[0] + n))
    log = owner.get(f"/api/pages/{page}/ops", params={"since": seqs[0] - 1}).json()
    assert [b["seq"] for b in log["batches"]] == seqs
    tree = owner.get(f"/api/blocks/{page}/subtree").json()["block"]
    assert sorted(c["content"] for c in tree["children"]) == sorted(f"line {i}" for i in range(n))


def test_cover_snapshots_written_at_once_keep_the_newest(owner):
    from gamma.db import get_page_snaps
    from gamma.workspaces import default_workspace
    img = "data:image/jpeg;base64,/9j/"
    stamps = [f"2026-01-01T00:00:{s:02d}.000000Z" for s in range(10)]
    results = _at_once([lambda c=c, at=at, i=i: c.put("/api/page-snaps/cw-cover", json={"img": img + str(i), "at": at})
                        for i, (c, at) in enumerate(zip(_clients(owner, 10), stamps))])
    assert not _failed(results), _failed(results)
    snap = get_page_snaps(default_workspace("cw_owner"))["cw-cover"]
    assert snap["at"] == stamps[-1] and snap["img"] == img + "9"

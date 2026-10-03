"""A PDF's upload in parts (gamma/upload_parts.py, POST /api/uploads/parts):
the parts land in the store's partial directory and the whole is stored by a
rename under its hash; the limits are checked before a byte travels; a part
at the wrong offset says where the client stands; what is no PDF, or never
finished, leaves nothing behind; idle sessions go, open ones are bounded."""

import io

import pytest

from conftest import login, make_user, workspace_of
from gamma import blobs, storage, upload_parts

PDF = b"%PDF-1.4\n" + bytes(range(256)) * 1500 + b"\n%%EOF\n"  # 384 KB
MB = 1024 * 1024


@pytest.fixture
def cam():
    make_user("up_cam", "pw-up-1")
    return login("up_cam", "pw-up-1"), workspace_of("up_cam")


def _start(c, size, name="big.pdf"):
    return c.post("/api/uploads/parts", json={"size": size, "name": name})


def _part(c, token, offset, data):
    return c.post(f"/api/uploads/parts/{token}", data={"offset": str(offset)},
                  files={"part": ("part", io.BytesIO(data), "application/octet-stream")})


def _finish(c, token):
    return c.post(f"/api/uploads/parts/{token}/finish")


def _partial(ws):
    folder = blobs.partial_dir(ws)
    return sorted(p.name for p in folder.iterdir()) if folder.is_dir() else []


def _send(c, data, cuts):
    """Open an upload of ``data`` and send it in the parts ``cuts`` delimit;
    the token, ready to finish."""
    r = _start(c, len(data))
    assert r.status_code == 200, r.text
    token, at = r.json()["token"], 0
    for to in [*cuts, len(data)]:
        r = _part(c, token, at, data[at:to])
        assert r.status_code == 200, r.text
        assert r.json()["received"] == to
        at = to
    return token


def test_a_pdf_in_parts_is_stored_whole_under_its_hash(cam):
    c, ws = cam
    used = c.get("/api/quota").json()["workspace_bytes"]
    r = _start(c, len(PDF))
    assert r.status_code == 200, r.text
    assert r.json()["part_bytes"] == upload_parts.PART_BYTES and r.json()["received"] == 0
    token = r.json()["token"]
    assert len(_partial(ws)) == 1  # the file the parts go into
    third = len(PDF) // 3
    for at, to in ((0, third), (third, 2 * third), (2 * third, len(PDF))):
        assert _part(c, token, at, PDF[at:to]).json()["received"] == to
    r = _finish(c, token)
    assert r.status_code == 200, r.text
    doc_id = storage.content_digest(PDF)
    assert r.json() == {"doc_id": doc_id, "source_url": f"/api/uploads/{doc_id}.pdf", "size": len(PDF),
                        "already_existed": False}
    assert c.get(r.json()["source_url"]).content == PDF
    assert _partial(ws) == []  # renamed into place, nothing left behind
    assert _finish(c, token).status_code == 404  # the session is gone
    assert c.get("/api/quota").json()["workspace_bytes"] == used + len(PDF)

    # the same bytes again: a dedup hit, no new bytes
    r = _finish(c, _send(c, PDF, [third]))
    assert r.status_code == 200 and r.json()["already_existed"] is True
    assert _partial(ws) == [] and c.get(r.json()["source_url"]).content == PDF
    assert c.get("/api/quota").json()["workspace_bytes"] == used + len(PDF)


def test_a_part_at_the_wrong_offset_says_where_the_upload_stands(cam):
    c, ws = cam
    token = _start(c, len(PDF)).json()["token"]
    half = len(PDF) // 2
    assert _part(c, token, 0, PDF[:half]).json()["received"] == half
    r = _part(c, token, 0, PDF[:half])  # the first reply was lost: the client sends the part again
    assert r.status_code == 409 and r.json()["received"] == half and "holds" in r.json()["detail"]
    assert _part(c, token, half, PDF[half:]).json()["received"] == len(PDF)  # and goes on from there
    assert _finish(c, token).json()["doc_id"] == storage.content_digest(PDF)


def test_the_limits_are_checked_before_a_byte_travels(cam):
    c, ws = cam
    make_user("up_admin", "pw-up-2", is_admin=1)
    admin = login("up_admin", "pw-up-2")
    assert admin.put("/api/admin/users/up_cam", json={"max_upload_mb": 1}).status_code == 200
    r = _start(c, 2 * MB)
    assert r.status_code == 413 and "max 1 MB" in r.json()["detail"]
    assert admin.put("/api/admin/users/up_cam", json={"max_upload_mb": None, "quota_mb": 1}).status_code == 200
    assert _start(c, 2 * MB).status_code == 507
    assert admin.put("/api/admin/users/up_cam", json={"quota_mb": None}).status_code == 200
    assert _start(c, 0).status_code == 400
    assert _partial(ws) == []


def test_what_is_no_pdf_is_refused_at_the_end_and_leaves_nothing(cam):
    c, ws = cam
    data = b"<html>a login page</html>" * 100
    token = _send(c, data, [len(data) // 2])
    r = _finish(c, token)
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    assert _partial(ws) == [] and _finish(c, token).status_code == 404
    assert not blobs.exists(ws, f"{storage.content_digest(data)}.pdf")


def test_more_than_announced_or_than_a_part_may_hold_is_refused(cam, monkeypatch):
    c, ws = cam
    token = _start(c, 100).json()["token"]
    assert _part(c, token, 0, b"x" * 200).status_code == 413
    assert _part(c, token, 0, b"%PDF" + b"x" * 96).json()["received"] == 100  # the file was left as it was
    assert _finish(c, token).status_code == 200
    monkeypatch.setattr(upload_parts, "PART_BYTES_MAX", 50)
    token = _start(c, 100).json()["token"]
    assert _part(c, token, 0, b"x" * 60).status_code == 413
    assert _part(c, token, 0, b"x" * 50).json()["received"] == 50
    assert _part(c, token, 50, b"").status_code == 400  # an empty part
    assert c.delete(f"/api/uploads/parts/{token}").status_code == 200


def test_an_unfinished_upload_is_refused_and_can_be_dropped(cam):
    c, ws = cam
    token = _start(c, len(PDF)).json()["token"]
    _part(c, token, 0, PDF[:1000])
    r = _finish(c, token)
    assert r.status_code == 400 and f"1000 of {len(PDF)} bytes" in r.json()["detail"]
    assert len(_partial(ws)) == 1  # still open
    assert c.delete(f"/api/uploads/parts/{token}").json() == {"ok": True}
    assert _partial(ws) == [] and c.delete(f"/api/uploads/parts/{token}").status_code == 404
    assert _part(c, token, 1000, PDF[1000:]).status_code == 404


def test_a_token_belongs_to_its_workspace(cam):
    c, ws = cam
    make_user("up_other", "pw-up-3")
    other = login("up_other", "pw-up-3")
    token = _start(c, len(PDF)).json()["token"]
    assert _part(other, token, 0, PDF).status_code == 404
    assert _finish(other, token).status_code == 404
    assert other.delete(f"/api/uploads/parts/{token}").status_code == 404
    assert _finish(c, "nope").status_code == 404
    assert c.delete(f"/api/uploads/parts/{token}").status_code == 200


def test_idle_sessions_are_dropped_and_open_ones_are_bounded(cam, monkeypatch):
    c, ws = cam
    monkeypatch.setattr(upload_parts, "MAX_PER_WS", 2)
    stale = upload_parts.get(ws, _start(c, 10).json()["token"])
    stale.touched -= upload_parts.IDLE_S + 1
    a = _start(c, 10).json()["token"]  # an open drops what idled too long
    assert not stale.path.exists() and upload_parts.open_count(ws) == 1
    assert _finish(c, stale.token).status_code == 404
    b = _start(c, 10).json()["token"]
    r = _start(c, 10)
    assert r.status_code == 429 and "2 uploads" in r.json()["detail"]
    assert c.delete(f"/api/uploads/parts/{a}").status_code == 200
    d = _start(c, 10).json()["token"]
    assert upload_parts.open_count(ws) == 2
    for token in (b, d):
        assert c.delete(f"/api/uploads/parts/{token}").status_code == 200
    assert upload_parts.open_count(ws) == 0 and _partial(ws) == []

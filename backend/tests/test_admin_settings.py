"""Storage limits: the server-wide default quota, per-user quota overrides, enforcement."""

import time

import pytest

from conftest import account_of, drop_user, login as _login, make_user as _make_user, workspace_of


def _pdf_of_mb(mb, filler=b"x"):
    return b"%PDF-1.4 " + filler * (mb * 1024 * 1024)


@pytest.fixture(scope="module")
def sizeadmin(client):
    _make_user("sizeadmin", "sizeadminpw", is_admin=1)
    yield _login("sizeadmin", "sizeadminpw")
    drop_user("sizeadmin")


@pytest.fixture(scope="module")
def sizeuser(client):
    _make_user("sizeuser", "sizeuserpw", is_admin=0)
    yield _login("sizeuser", "sizeuserpw")
    drop_user("sizeuser")


@pytest.fixture(autouse=True)
def restore_defaults():
    """Each test starts from stock limits (no quota, no per-user overrides)
    and empty uploads dirs so order doesn't matter."""
    from gamma.db import connect_users_db, ws_uploads_dir
    from gamma.server_settings import DEFAULT_QUOTA_MB, set_default_quota_mb

    def reset():
        set_default_quota_mb(DEFAULT_QUOTA_MB)
        with connect_users_db() as conn:
            conn.execute("DELETE FROM settings WHERE key = 'max_upload_mb'")
            conn.execute("UPDATE users SET max_upload_mb = NULL, quota_mb = NULL "
                         "WHERE username IN ('sizeadmin', 'sizeuser')")
            conn.commit()
        for username in ("sizeadmin", "sizeuser"):
            uploads = ws_uploads_dir(workspace_of(username))
            if uploads.exists():
                for f in uploads.iterdir():
                    if not f.is_file():
                        continue
                    # A stored PDF's manifest walk (pdf_meta.schedule) runs on
                    # a background thread and may still hold the file open;
                    # Windows refuses the unlink until it closes.
                    for _ in range(40):
                        try:
                            f.unlink()
                            break
                        except PermissionError:
                            time.sleep(0.05)

    reset()
    yield
    reset()


def test_defaults_and_quota_endpoint(sizeadmin, sizeuser):
    r = sizeadmin.get("/api/admin/settings")
    assert r.status_code == 200
    assert r.json()["quota_mb"] == 0  # unlimited
    assert "max_upload_mb" not in r.json()  # no per-file cap
    # any logged-in user reads their effective limits + usage from /api/quota
    q = sizeuser.get("/api/quota").json()
    assert q["quota_mb"] == 0 and "max_upload_mb" not in q
    assert isinstance(q["used_bytes"], int)
    # ...and /api/session carries no limits (identity only)
    assert "quota_mb" not in sizeuser.get("/api/session").json()


def test_admin_only(sizeuser):
    assert sizeuser.get("/api/admin/settings").status_code == 403
    assert sizeuser.put("/api/admin/settings", json={"quota_mb": 999}).status_code == 403
    assert sizeuser.put("/api/admin/users/sizeuser", json={"quota_mb": 0}).status_code == 403


def test_no_per_file_cap(sizeadmin, sizeuser):
    """One file has no size cap of its own: a per-file cap saved before
    (the settings key, an account's ``max_upload_mb``) is not read, and a
    request still naming one changes nothing."""
    from gamma.db import connect_users_db

    with connect_users_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('max_upload_mb', '1', '')")
        conn.execute("UPDATE users SET max_upload_mb = 1 WHERE username = 'sizeuser'")
        conn.commit()
    assert sizeadmin.put("/api/admin/settings", json={"max_upload_mb": 1}).status_code == 200
    big = _pdf_of_mb(2)
    r = sizeuser.post("/api/uploads", files={"file": ("big.pdf", big, "application/pdf")})
    assert r.status_code == 200, r.text
    r = sizeuser.post("/api/upload-file", files={"file": ("big.bin", big[1:], "application/octet-stream")})
    assert r.status_code == 200 and r.json()["size"] == len(big) - 1, r.text


def test_storage_quota_enforced_and_dedup_free(sizeadmin, sizeuser):
    r = sizeadmin.put("/api/admin/users/sizeuser", json={"quota_mb": 3})
    assert r.status_code == 200
    me = next(u for u in r.json()["users"] if u["username"] == "sizeuser")
    assert me["quota_mb"] == 3 and "max_upload_mb" not in me
    # only the GET listing pays for disk usage; mutations omit it
    assert "used_bytes" not in me
    listed = next(u for u in sizeadmin.get("/api/admin/users").json()["users"]
                  if u["username"] == "sizeuser")
    assert isinstance(listed["used_bytes"], int)

    a = _pdf_of_mb(2, b"a")
    r = sizeuser.post("/api/uploads", files={"file": ("a.pdf", a, "application/pdf")})
    assert r.status_code == 200, r.text

    b = _pdf_of_mb(2, b"b")
    r = sizeuser.post("/api/uploads", files={"file": ("b.pdf", b, "application/pdf")})
    assert r.status_code == 507
    assert "storage quota exceeded" in r.json()["detail"]

    # re-uploading already-stored bytes costs nothing, so it stays allowed
    r = sizeuser.post("/api/uploads", files={"file": ("a.pdf", a, "application/pdf")})
    assert r.status_code == 200 and r.json()["already_existed"] is True

    q = sizeuser.get("/api/quota").json()
    assert q["quota_mb"] == 3 and q["used_bytes"] >= 2 * 1024 * 1024

    # explicit null clears the override back to the (unlimited) default
    r = sizeadmin.put("/api/admin/users/sizeuser", json={"quota_mb": None})
    assert r.status_code == 200
    r = sizeuser.post("/api/uploads", files={"file": ("b.pdf", b, "application/pdf")})
    assert r.status_code == 200, r.text


def test_validation(sizeadmin):
    for bad in ({"quota_mb": -1}, {"quota_mb": 2 * 1024 * 1024}):
        assert sizeadmin.put("/api/admin/settings", json=bad).status_code == 400, bad
        assert sizeadmin.put("/api/admin/users/sizeuser", json=bad).status_code == 400, bad
    # empty update is a no-op, not an error
    r = sizeadmin.put("/api/admin/settings", json={})
    assert r.status_code == 200 and r.json()["quota_mb"] == 0


def test_guest_quota_settable_but_not_credentials(sizeadmin, guest_account):
    from gamma.server_settings import GUEST_DEFAULT_QUOTA_MB, user_limits

    assert user_limits(account_of(guest_account))["quota_mb"] == GUEST_DEFAULT_QUOTA_MB  # bounded by is_guest, not by name
    r = sizeadmin.put(f"/api/admin/users/{guest_account}", json={"quota_mb": 1})
    assert r.status_code == 200, r.text
    g = next(u for u in r.json()["users"] if u["username"] == guest_account)
    assert g["quota_mb"] == 1 and g["is_guest"] is True
    assert sizeadmin.put(f"/api/admin/users/{guest_account}", json={"password": "x"}).status_code == 400
    assert sizeadmin.put(f"/api/admin/users/{guest_account}", json={"is_admin": True}).status_code == 400
    sizeadmin.put(f"/api/admin/users/{guest_account}", json={"quota_mb": None})


@pytest.fixture()
def guest_account():
    """A guest account of this test's own (gamma/guests.py), deleted after."""
    from gamma import guests, workspaces
    user_id, name = guests.new_guest()
    yield name
    workspaces.delete_account(user_id)


def test_corrupt_values_fall_back_to_defaults():
    from gamma.db import connect_users_db
    from gamma.server_settings import user_limits

    with connect_users_db() as conn:
        conn.execute("INSERT OR REPLACE INTO settings (key, value, updated_at) VALUES ('quota_mb', 'junk', '')")
        conn.commit()
    assert user_limits(account_of("sizeuser")) == {"quota_mb": 0}

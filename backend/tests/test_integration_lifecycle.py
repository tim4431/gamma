"""Account/workspace operations preserve or revoke manual and OAuth tokens."""

import os
from pathlib import Path
import secrets
import subprocess
import sys

import pytest

from conftest import account_of, login, make_user
from gamma import workspaces
from gamma.db import connect_users_db
from gamma.integrations import create_token, resolve_token


@pytest.fixture
def account(client):
    username = "token-life-" + secrets.token_hex(6)
    ws = make_user(username, "pw")
    with login(username, "pw") as browser:
        yield username, ws, browser


@pytest.fixture
def admin(client):
    username = "token-admin-" + secrets.token_hex(6)
    make_user(username, "pw", is_admin=1)
    with login(username, "pw") as browser:
        yield browser


@pytest.fixture(params=[None, "http://localhost/mcp"], ids=["manual", "oauth"])
def resource(request):
    return request.param


def issue(user_id, ws, resource):
    item = create_token(user_id, ws, "Lifecycle regression", 90, oauth_resource=resource)
    assert resolve_token(item["token"], resource) == (user_id, ws)
    return item


def assert_removed(item, resource):
    assert resolve_token(item["token"], resource) is None
    with connect_users_db() as conn:
        assert conn.execute("SELECT 1 FROM integration_tokens WHERE id = ?", (item["id"],)).fetchone() is None


def manage(*args):
    result = subprocess.run([sys.executable, "manage.py", *args],
                            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_rename_preserves_token_and_updates_owner(account, admin, resource):
    username, ws, browser = account
    user_id = account_of(username)
    item = issue(user_id, ws, resource)
    renamed = username + "-renamed"
    response = admin.post(f"/api/admin/users/{username}/rename", json={"new_username": renamed})
    assert response.status_code == 200, response.text
    assert browser.get("/api/session").json()["user"] == renamed
    assert resolve_token(item["token"], resource) == (user_id, ws)
    assert item["id"] in {row["id"] for row in browser.get("/api/integrations/tokens").json()["tokens"]}
    with connect_users_db() as conn:
        assert conn.execute("SELECT u.username FROM integration_tokens t JOIN users u ON u.id = t.user_id "
                            "WHERE t.id = ?", (item["id"],)).fetchone()[0] == renamed


@pytest.mark.parametrize("via_cli", [False, True], ids=["admin-api", "manage-cli"])
def test_password_change_revokes_all_account_tokens(account, admin, resource, via_cli):
    username, ws, browser = account
    user_id = account_of(username)
    second_ws = workspaces.create("Second library", user_id)["id"]
    items = [issue(user_id, target, resource) for target in (ws, second_ws)]
    admin_user = account_of(admin.get("/api/session").json()["user"])
    unaffected = issue(admin_user, workspaces.default_workspace(admin_user), resource)
    if via_cli:
        assert f"Password set for '{username}'" in manage("set-password", username, "rotated")
    else:
        response = admin.put(f"/api/admin/users/{username}", json={"password": "rotated"})
        assert response.status_code == 200, response.text
    for item in items:
        assert_removed(item, resource)
    assert browser.get("/api/integrations/tokens").status_code == 401
    assert resolve_token(unaffected["token"], resource) is not None
    with login(username, "rotated") as signed_in:
        assert signed_in.get("/api/integrations/tokens").json()["tokens"] == []


def test_workspace_deletion_revokes_only_its_tokens(account, resource):
    username, ws, browser = account
    user_id = account_of(username)
    second_ws = workspaces.create("Delete this library", user_id)["id"]
    removed = issue(user_id, second_ws, resource)
    retained = issue(user_id, ws, resource)
    response = browser.delete(f"/api/workspaces/{second_ws}")
    assert response.status_code == 200, response.text
    assert_removed(removed, resource)
    assert resolve_token(retained["token"], resource) == (user_id, ws)


@pytest.mark.parametrize("via_cli", [False, True], ids=["admin-api", "manage-cli"])
def test_account_deletion_removes_tokens_in_retained_workspaces(account, admin, resource, via_cli):
    username, ws, _ = account
    user_id = account_of(username)
    other = account_of(admin.get("/api/session").json()["user"])
    shared = workspaces.create("Retained shared library", other, kind="shared")["id"]
    workspaces.set_member(shared, user_id, "viewer", by=other)
    removed = [issue(user_id, target, resource) for target in (ws, shared)]
    retained = issue(other, shared, resource)
    if via_cli:
        assert f"Deleted user '{username}'" in manage("delete-user", username)
    else:
        response = admin.delete(f"/api/admin/users/{username}")
        assert response.status_code == 200, response.text
    for item in removed:
        assert_removed(item, resource)
    assert workspaces.get(ws) is None
    assert workspaces.get(shared) is not None
    assert resolve_token(retained["token"], resource) == (other, shared)

"""manage.py's account-and-workspace commands that name accounts by
username (``_account``): set-member, create-workspace and set-admin — the
success paths, an unknown username, the guest refusals, and set-admin's
toggle — called in-process (their output on capsys) and once through the
real CLI."""

import os
from pathlib import Path
import subprocess
import sys

import pytest

from conftest import account_of, drop_user, make_user
from gamma import guests, workspaces
from gamma.db import connect_users_db

OWNER, MEMBER = "mcli_owner", "mcli_member"


@pytest.fixture(scope="module")
def people(client):
    """Two password accounts and a guest; the admin flag any test set is
    cleared at the end (test_admin_users assumes it knows every admin)."""
    make_user(OWNER, "pw")
    make_user(MEMBER, "pw")
    _, guest_name = guests.new_guest()
    yield {"owner": OWNER, "member": MEMBER, "guest": guest_name}
    with connect_users_db() as conn:
        conn.execute("UPDATE users SET is_admin = 0 WHERE username IN (?, ?)", (OWNER, MEMBER))
        conn.commit()
    drop_user(OWNER)
    drop_user(MEMBER)


def _is_admin(username) -> bool:
    with connect_users_db() as conn:
        return bool(conn.execute("SELECT is_admin FROM users WHERE username = ?", (username,)).fetchone()[0])


def _roles(ws) -> dict:
    return {m["username"]: m["role"] for m in workspaces.members(ws)}


def manage_cli(*args) -> str:
    result = subprocess.run([sys.executable, "manage.py", *args],
                            cwd=Path(__file__).resolve().parents[1], env=os.environ.copy(),
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_create_workspace_by_owner_name(people, capsys):
    import manage

    manage.create_workspace("Lab notes", people["owner"], "shared", "public", "editor")
    out = capsys.readouterr().out
    assert out.startswith("Created shared workspace ") and "'Lab notes' (public)" in out
    assert out.strip().endswith(f"owner {people['owner']}.")
    ws = out.split()[3]
    info = workspaces.get(ws)
    assert info["kind"] == "shared" and info["access"] == "public" and info["public_role"] == "editor"
    assert _roles(ws) == {people["owner"]: "owner"}

    # The default: a private personal workspace.
    manage.create_workspace("Second desk", people["owner"])
    out = capsys.readouterr().out
    second = workspaces.get(out.split()[3])
    assert second["kind"] == "personal" and second["access"] == "private"
    assert second["id"] in workspaces.personal_workspaces(account_of(people["owner"]))
    assert _roles(second["id"]) == {people["owner"]: "owner"}


def test_create_workspace_refusals(people, capsys):
    import manage

    before = {w["id"] for w in workspaces.all_workspaces()}
    manage.create_workspace("Nobody's", "mcli_nobody")
    assert capsys.readouterr().out.strip() == "User 'mcli_nobody' not found."
    manage.create_workspace("Odd access", people["owner"], "shared", "weird")
    assert capsys.readouterr().out.strip() == "Refused: access must be private or public"
    assert {w["id"] for w in workspaces.all_workspaces()} == before


def test_set_member_roles_and_removal(people, capsys):
    import manage

    owner_id = account_of(people["owner"])
    ws = workspaces.create("Shared desk", owner_id, kind="shared")["id"]

    manage.set_member(ws, people["member"], "editor")
    assert capsys.readouterr().out.strip() == f"'{people['member']}' is now editor of workspace {ws}."
    assert _roles(ws) == {people["owner"]: "owner", people["member"]: "editor"}

    manage.set_member(ws, people["member"], "owner")
    capsys.readouterr()
    assert _roles(ws)[people["member"]] == "owner"
    # With two owners the first may step down; the last one may not.
    manage.set_member(ws, people["owner"], "viewer")
    assert "is now viewer" in capsys.readouterr().out
    manage.set_member(ws, people["member"], "viewer")
    assert capsys.readouterr().out.strip() == "Refused: a workspace needs at least one owner"
    assert _roles(ws) == {people["member"]: "owner", people["owner"]: "viewer"}

    manage.set_member(ws, people["owner"], "none")
    assert capsys.readouterr().out.strip() == f"Removed '{people['owner']}' from workspace {ws}."
    assert _roles(ws) == {people["member"]: "owner"}
    manage.set_member(ws, people["owner"], "none")  # not a member any more
    assert capsys.readouterr().out.startswith("Refused: ")


def test_set_member_refusals(people, capsys):
    import manage

    owner_id = account_of(people["owner"])
    ws = workspaces.create("Refusals desk", owner_id, kind="shared")["id"]

    manage.set_member("ws-nope", people["member"], "editor")
    assert capsys.readouterr().out.strip() == "Workspace 'ws-nope' not found."
    manage.set_member(ws, "mcli_nobody", "editor")
    assert capsys.readouterr().out.strip() == "User 'mcli_nobody' not found."
    manage.set_member(ws, people["guest"], "editor")
    assert capsys.readouterr().out.strip() == "Refused: the guest account cannot join workspaces"
    manage.set_member(ws, people["member"], "janitor")
    assert capsys.readouterr().out.strip() == "Refused: role must be owner, editor or viewer"
    assert _roles(ws) == {people["owner"]: "owner"}
    # A personal workspace has no members to set.
    personal = workspaces.default_workspace(owner_id)
    manage.set_member(personal, people["member"], "viewer")
    assert capsys.readouterr().out.startswith("Refused: a personal workspace has no other members")
    assert _roles(personal) == {people["owner"]: "owner"}


def test_set_admin_toggles(people, capsys):
    import manage

    assert not _is_admin(people["member"])
    manage.set_admin(people["member"], "on")
    assert capsys.readouterr().out.strip() == f"Admin privilege granted to '{people['member']}'."
    assert _is_admin(people["member"])
    manage.set_admin(people["member"], "on")  # idempotent
    capsys.readouterr()
    assert _is_admin(people["member"])
    manage.set_admin(people["member"], "off")
    assert capsys.readouterr().out.strip() == f"Admin privilege revoked from '{people['member']}'."
    assert not _is_admin(people["member"])


def test_set_admin_refusals(people, capsys):
    import manage

    manage.set_admin(people["member"], "maybe")
    assert capsys.readouterr().out.startswith("Usage: ")
    manage.set_admin("mcli_nobody", "on")
    assert capsys.readouterr().out.strip() == "User 'mcli_nobody' not found."
    manage.set_admin(people["guest"], "on")
    assert capsys.readouterr().out.strip() == "A guest account cannot be an admin."
    assert not _is_admin(people["member"]) and not _is_admin(people["guest"])


def test_cli_dispatch(people):
    """The same three commands through the command line (main's argument
    handling, the schema guard)."""
    out = manage_cli("create-workspace", "CLI desk", people["owner"], "shared")
    assert out.startswith("Created shared workspace ")
    ws = out.split()[3]
    assert workspaces.get(ws)["access"] == "private"
    assert f"'{people['member']}' is now viewer of workspace {ws}." in manage_cli("set-member", ws, people["member"], "viewer")
    assert _roles(ws) == {people["owner"]: "owner", people["member"]: "viewer"}
    assert "User 'mcli_nobody' not found." in manage_cli("set-member", ws, "mcli_nobody", "viewer")
    try:
        assert f"Admin privilege granted to '{people['owner']}'." in manage_cli("set-admin", people["owner"], "on")
        assert _is_admin(people["owner"])
    finally:
        assert f"Admin privilege revoked from '{people['owner']}'." in manage_cli("set-admin", people["owner"], "off")
    assert not _is_admin(people["owner"])

"""manage.py's account-and-workspace commands that name accounts by
username (``_account``): set-member, create-workspace and set-admin — the
success paths, an unknown username, the guest refusals, and set-admin's
toggle — called in-process (their output on capsys) and once through the
real CLI; and the off-site copies' ``offsite`` (listing, restoring with the
uploads, the refusals, against moto's bucket) and ``litestream-config``,
which run before the schema guard."""

import itertools
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
from contextlib import closing

import pytest

from conftest import S3_TEST_BUCKET, account_of, drop_user, make_user
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


# --- offsite and litestream-config (gamma/offsite.py) -------------------------------

OFFSITE_ENV = {"GAMMA_S3_BUCKET": S3_TEST_BUCKET, "GAMMA_S3_REGION": "us-east-1", "GAMMA_S3_ACCESS_KEY": "testing",
               "GAMMA_S3_SECRET_KEY": "testing"}


def _db_file(path: Path, rows) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")  # as the server's: an open connection is then seen
        conn.execute("CREATE TABLE IF NOT EXISTS notes (text TEXT)")
        conn.executemany("INSERT INTO notes VALUES (?)", [(r,) for r in rows])
        conn.commit()


def _notes(path: Path) -> list[str]:
    with closing(sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True)) as conn:
        return [r[0] for r in conn.execute("SELECT text FROM notes ORDER BY rowid")]


@pytest.fixture
def offsite_dir(data_dir, s3_bucket, monkeypatch):
    """A data directory of the test's own with users.db and one workspace
    (its databases and one upload), the copies to moto's bucket through
    the environment, rounds stamped a second apart."""
    from gamma import offsite

    for var in ("GAMMA_S3_ENDPOINT", "GAMMA_S3_PREFIX", "GAMMA_OFFSITE", "GAMMA_OFFSITE_INTERVAL",
                "GAMMA_OFFSITE_KEEP"):
        monkeypatch.delenv(var, raising=False)
    for var, value in OFFSITE_ENV.items():
        monkeypatch.setenv(var, value)
    monkeypatch.setattr(offsite, "_last_round", None)
    clock = itertools.count(1)
    monkeypatch.setattr(offsite, "_stamp", lambda: f"20261003T10{next(clock):04d}Z")
    _db_file(data_dir / "users.db", ["alice"])
    _db_file(data_dir / "workspaces" / "mcli-ws" / "pages.db", ["page"])
    _db_file(data_dir / "workspaces" / "mcli-ws" / "data.db", ["data"])
    (data_dir / "workspaces" / "mcli-ws" / "uploads").mkdir()
    (data_dir / "workspaces" / "mcli-ws" / "uploads" / "0123abcd.pdf").write_bytes(b"%PDF-1.4 cli" * 100)
    return data_dir


def test_offsite_lists_and_restores_with_the_uploads(offsite_dir, capsys):
    import manage
    from gamma import offsite

    assert offsite.tick()["uploads_copied"] == 1
    manage.offsite(["--list"])
    out = capsys.readouterr().out
    assert "  users.db  1 copy, the newest 20261003T100001Z" in out and "  mcli-ws/pages.db  1 copy" in out
    manage.offsite(["--list", "mcli-ws"])
    out = capsys.readouterr().out
    assert "  20261003T100001Z  mcli-ws/pages.db  " in out and "  mcli-ws/uploads  1 file(s) (0.0 MB)" in out

    ws_dir = offsite_dir / "workspaces" / "mcli-ws"
    _db_file(ws_dir / "pages.db", ["after the copy"])
    with closing(sqlite3.connect(ws_dir / "pages.db")) as held:  # a running server holds it
        held.execute("SELECT count(*) FROM notes").fetchone()
        with pytest.raises(SystemExit) as stop:
            manage.offsite(["--restore", "mcli-ws", "--uploads"])
    assert stop.value.code == 2 and "Refused: in use, so the server is running" in capsys.readouterr().out
    assert not list(ws_dir.glob("*.pre-restore-*"))

    (ws_dir / "uploads" / "0123abcd.pdf").unlink()  # the file lost with the disk
    manage.offsite(["--restore", "mcli-ws", "--at", "20261003T100001Z", "--uploads"])
    out = capsys.readouterr().out
    assert "Restored mcli-ws/pages.db from 20261003T100001Z (the file there is now pages.db.pre-restore-" in out
    assert "Put 1 file(s) (0.0 MB) from the bucket into mcli-ws/uploads" in out
    assert _notes(ws_dir / "pages.db") == ["page"]
    assert (ws_dir / "uploads" / "0123abcd.pdf").read_bytes() == b"%PDF-1.4 cli" * 100

    for args, code, said in ((["--restore", "mcli-ws", "--at", "today"], 2, "Refused: --at takes a stamp"),
                             (["--restore", "../x"], 2, "Refused: '../x' is no workspace id"),
                             (["--list", "../x"], 2, "Refused: unsafe workspace id"),
                             (["--restore"], 1, "Usage: "), ([], 1, "Usage: ")):
        with pytest.raises(SystemExit) as stop:
            manage.offsite(args)
        assert stop.value.code == code and said in capsys.readouterr().out, args


def test_offsite_and_litestream_config_run_before_the_schema_guard(tmp_path):
    """A data directory of any version (here none at all): refused for want
    of a bucket, not of a schema, and no users.db made."""
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GAMMA_S3_", "GAMMA_OFFSITE"))}
    env["GAMMA_DATA_DIR"] = str(tmp_path)
    backend = Path(__file__).resolve().parents[1]

    def run(*args, **extra):
        return subprocess.run([sys.executable, "manage.py", *args], cwd=backend, capture_output=True, text=True,
                              timeout=60, env={**env, **extra})

    listed = run("offsite", "--list")
    assert listed.returncode == 2 and "The bucket cannot be used: no bucket is set" in listed.stdout, listed.stderr
    refused = run("litestream-config")
    assert refused.returncode == 2 and "GAMMA_S3_BUCKET" in refused.stdout
    assert not (tmp_path / "users.db").exists()
    _db_file(tmp_path / "users.db", ["x"])
    text = run("litestream-config", GAMMA_S3_BUCKET="gamma-files", GAMMA_S3_ACCESS_KEY="AK1",
               GAMMA_S3_SECRET_KEY="SK1", GAMMA_S3_PREFIX="tenant-1")
    assert text.returncode == 0, text.stderr
    assert "tenant-1/litestream/users.db" in text.stdout and "AK1" not in text.stdout and "SK1" not in text.stdout

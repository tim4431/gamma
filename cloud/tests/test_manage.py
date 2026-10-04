from contextlib import closing

import pytest
from conftest import register

from gammacloud import accounts, db, settings
import manage


def test_cli_roundtrip(capsys):
    manage.main(["setup"])
    manage.main(["create-account", "carol@example.org", "carol", "--password", "correct horse battery", "--admin", "--verified"])
    manage.main(["set-plan", "carol", "pro"])
    manage.main(["list-accounts"])
    out = capsys.readouterr().out
    assert "carol" in out and "pro" in out and "admin" in out and "unverified" not in out
    manage.main(["invite", "--uses", "2", "--plan", "plus"])
    code = capsys.readouterr().out.strip()
    manage.main(["invites"])
    assert code in capsys.readouterr().out
    manage.main(["create-client", "share", "share-host", "https://share.example/api/auth/cloud/callback"])
    out = capsys.readouterr().out
    assert "client_id=gc_" in out and "client_secret=" in out
    manage.main(["rotate-key"])
    manage.main(["migrate", "--status"])
    assert f"v{db.SCHEMA_VERSION}" in capsys.readouterr().out
    manage.main(["backup"])
    assert "backups" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        manage.main(["set-plan", "nobody", "pro"])


def test_purge_deleted(client):
    account = register(client)
    client.post("/api/me/delete", json={"password": "correct horse battery"})
    with closing(db.connect()) as conn:
        assert accounts.purge_deleted(conn, 30) == 0  # still in the grace period
        conn.execute("UPDATE accounts SET deleted_at = '2000-01-01T00:00:00.000Z' WHERE id = ?", (account["id"],))
        assert accounts.purge_deleted(conn, 30) == 1
        assert conn.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        conn.commit()


def test_restore_and_purge_account(capsys):
    manage.main(["create-account", "dave@example.org", "dave", "--password", "correct horse battery"])
    with pytest.raises(SystemExit):
        manage.main(["restore-account", "dave"])  # not deleted
    manage.main(["delete-account", "dave"])
    manage.main(["restore-account", "dave"])
    with closing(db.connect()) as conn:
        assert accounts.by_username(conn, "dave")
    manage.main(["delete-account", "dave"])
    manage.main(["purge-account", "dave"])
    assert "dave: purged" in capsys.readouterr().out
    with closing(db.connect()) as conn:
        assert not conn.execute("SELECT 1 FROM accounts WHERE username = 'dave'").fetchone()


def test_settings_command(capsys):
    manage.main(["setup"])
    manage.main(["settings"])
    out = capsys.readouterr().out
    assert "registration           invite" in out and "turnstile_secret       (not set)" in out
    manage.main(["settings", "registration", "open"])
    out = capsys.readouterr().out
    assert "registration           open" in out and "warning: registration is open without Turnstile" in out
    assert settings.registration() == "open"
    # the same cleaning the Admin page gets, and its validation
    manage.main(["settings", "blocked_email_domains", "Spam.Example, b.example"])
    assert "spam.example, b.example" in capsys.readouterr().out
    assert settings.blocked_email_domains() == {"spam.example", "b.example"}
    with pytest.raises(SystemExit) as e:
        manage.main(["settings", "registration", "nonsense"])
    assert "open, invite or closed" in str(e.value)
    with pytest.raises(SystemExit):
        manage.main(["settings", "no_such_key", "x"])  # argparse refuses it
    # a secret is shown as set or not, never printed, and an empty value clears it
    manage.main(["settings", "turnstile_secret", "0xS3CR3T"])
    out = capsys.readouterr().out
    assert "0xS3CR3T" not in out and "turnstile_secret       (set)" in out
    assert "warning" in out  # a secret alone leaves the check off
    manage.main(["settings", "turnstile_sitekey", "0xSITE"])
    assert "warning" not in capsys.readouterr().out
    manage.main(["settings", "turnstile_secret", ""])
    assert "turnstile_secret       (not set)" in capsys.readouterr().out
    with closing(db.connect()) as conn:
        details = [r["detail"] for r in conn.execute("SELECT detail FROM audit WHERE event = 'settings.set'")]
    assert "registration=open" in details and "turnstile_secret=set" in details and "0xS3CR3T" not in "".join(details)


def test_newer_db_refused():
    with closing(db.connect()) as conn:
        conn.execute(f"PRAGMA user_version = {db.SCHEMA_VERSION + 1}")
        conn.commit()
    with pytest.raises(db.NewerDataError):
        db.ensure_current()


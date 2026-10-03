"""The sign-up settings an admin edits at runtime: the table behind them, the
admin API, and that the sign-up gate reads them rather than the environment."""

import os
from contextlib import closing

import pytest
from conftest import invite, make_admin, register, set_setting, steps_after, verify

from gammacloud import accounts, captcha, db, settings


# --- the values and their validation -----------------------------------------

def test_defaults_without_a_row(client):
    assert settings.registration() == "invite"
    assert settings.turnstile_sitekey() == "" and settings.turnstile_secret() == ""
    assert settings.blocked_email_domains() == frozenset()


def test_a_write_is_visible_to_the_next_read(client):
    set_setting("registration", "open")
    assert settings.registration() == "open"


def test_a_value_the_table_should_not_hold_reads_as_invite(client):
    """A corrupt row must never be the thing that opens registration."""
    with closing(db.connect()) as conn:
        conn.execute("INSERT INTO settings (key, value, updated_at) VALUES ('registration', 'nonsense', ?)",
                     (db.now(),))
        conn.commit()
    settings.invalidate()
    assert settings.registration() == "invite"


@pytest.mark.parametrize("raw, expected", [
    ("mailinator.com", "mailinator.com"),
    ("  Spam.Example  ", "spam.example"),
    ("a.example, b.example", "a.example\nb.example"),          # commas
    ("a.example\n  b.example\n", "a.example\nb.example"),       # one per line
    ("a.example a.example", "a.example"),                      # deduplicated
    ("@a.example", "a.example"),                               # a leading @
    ("x@a.example", "a.example"),                              # a whole address
    ("a.example.", "a.example"),                               # a trailing dot
    ("", ""),
])
def test_domain_lists_are_cleaned(raw, expected):
    assert settings.clean("blocked_email_domains", raw) == expected


@pytest.mark.parametrize("raw", ["not a domain!", "nodot", "-bad.example", "a..example"])
def test_a_domain_list_refuses_nonsense(raw):
    with pytest.raises(ValueError):
        settings.clean("blocked_email_domains", raw)


@pytest.mark.parametrize("raw", ["wide-open", "", None])
def test_registration_refuses_anything_but_a_mode(raw):
    with pytest.raises(ValueError):
        settings.clean("registration", raw)


def test_too_many_domains():
    with pytest.raises(ValueError):
        settings.clean("blocked_email_domains",
                       "\n".join(f"d{i}.example" for i in range(settings.MAX_BLOCKED_DOMAINS + 1)))


# --- the admin API ------------------------------------------------------------

def admin_client(client):
    register(client, "alice")
    verify(client, "alice")
    make_admin("alice")
    return client


def test_only_an_admin_reads_or_writes_settings(client):
    assert client.get("/api/admin/settings").status_code == 401
    register(client, "alice")
    assert client.get("/api/admin/settings").status_code == 403
    assert client.patch("/api/admin/settings", json={"registration": "open"}).status_code == 403
    assert settings.registration() == "invite"


def test_the_view_never_carries_the_secret(client):
    admin_client(client)
    set_setting("turnstile_secret", "a-real-secret")
    r = client.get("/api/admin/settings")
    assert r.json()["turnstile_secret_set"] is True and "turnstile_secret" not in r.json()
    assert "a-real-secret" not in r.text


def test_an_admin_switches_registration_and_the_form_follows(client):
    admin_client(client)
    assert "Invite code" in client.get("/register").text
    r = client.patch("/api/admin/settings", json={"registration": "open"})
    assert r.status_code == 200
    assert settings.registration() == "open"
    assert "Invite code" not in client.get("/register").text
    # and the public config the pages read
    assert client.get("/api/config").json()["registration"] == "open"


def test_a_blank_secret_keeps_the_stored_one_and_null_clears_it(client):
    admin_client(client)
    set_setting("turnstile_secret", "keep-me")
    client.patch("/api/admin/settings", json={"turnstile_sitekey": "0xABC", "turnstile_secret": ""})
    assert settings.turnstile_secret() == "keep-me" and settings.turnstile_sitekey() == "0xABC"
    client.patch("/api/admin/settings", json={"turnstile_secret": None})
    assert settings.turnstile_secret() == ""


def test_a_rejected_write_changes_nothing(client):
    admin_client(client)
    r = client.patch("/api/admin/settings", json={"blocked_email_domains": "fine.example, not a domain!"})
    assert r.status_code == 400
    assert settings.blocked_email_domains() == frozenset()
    r = client.patch("/api/admin/settings", json={"registration": "open", "nonsense": "1"})
    assert r.status_code == 400 and "Unknown setting" in r.json()["detail"]
    assert settings.registration() == "invite"


def test_a_change_is_audited_without_the_secret(client):
    admin_client(client)
    client.patch("/api/admin/settings", json={"registration": "closed", "turnstile_secret": "hunter2"})
    audit = client.get("/api/admin/audit?limit=50").json()["audit"]
    events = [a for a in audit if a["event"] == "settings.set"]
    assert any(a["detail"] == "registration=closed" for a in events)
    assert any(a["detail"] == "turnstile_secret=set" for a in events)
    assert not any("hunter2" in a["detail"] for a in events)


def test_an_unchanged_save_is_not_audited(client):
    admin_client(client)
    for _ in range(3):
        client.patch("/api/admin/settings", json={"registration": "open"})
    audit = client.get("/api/admin/audit?limit=50").json()["audit"]
    assert [a["detail"] for a in audit if a["event"] == "settings.set"] == ["registration=open"]


def test_the_unguarded_flag_is_what_the_page_warns_on(client):
    admin_client(client)
    assert client.get("/api/admin/settings").json()["unguarded"] is False
    r = client.patch("/api/admin/settings", json={"registration": "open"})
    assert r.json()["unguarded"] is True and r.json()["turnstile_on"] is False
    r = client.patch("/api/admin/settings", json={"turnstile_sitekey": "0xSITE", "turnstile_secret": "a-secret"})
    assert r.json()["unguarded"] is False and r.json()["turnstile_on"] is True


# --- the gate reads the table, not the environment ---------------------------

def test_turnstile_runs_once_both_keys_are_stored(client, monkeypatch):
    assert captcha.verify(None, "203.0.113.9") is True          # no keys: passes
    set_setting("turnstile_secret", "a-secret")
    # A secret alone would refuse every sign-up, since the forms show no widget.
    assert captcha.verify(None, "203.0.113.9") is True
    assert "data-sitekey" not in client.get("/register").text
    set_setting("turnstile_sitekey", "0xSITE")
    assert "data-sitekey=\"0xSITE\"" in client.get("/register").text
    assert captcha.verify(None, "203.0.113.9") is False         # a token is now required
    monkeypatch.setattr(captcha, "VERIFY_URL", "http://127.0.0.1:9/never")
    assert captcha.verify("a-token", "203.0.113.9") is False    # Cloudflare unreachable: refuse


def test_a_blocked_domain_set_by_an_admin_is_refused(client):
    code = invite(uses=10)
    body = {"password": "correct horse battery"}
    assert client.post("/api/register", json={"email": "x@spam.example", "username": "xyz",
                                              "invite": code, **body}).status_code == 201
    set_setting("blocked_email_domains", "spam.example")
    r = client.post("/api/register", json={"email": "y@mail.spam.example", "username": "abc",
                                           "invite": code, **body})
    assert r.status_code == 400 and "not accepted" in r.json()["detail"]


def test_the_retired_variables_are_ignored(client, monkeypatch):
    """The environment and the table do not overlap: setting the old variable
    does nothing, which is why app.py warns about it at startup."""
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_REGISTRATION", "open")
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_TURNSTILE_SECRET", "from-the-env")
    settings.invalidate()
    assert settings.registration() == "invite"
    assert settings.turnstile_secret() == ""
    assert not hasattr(accounts.config, "REGISTRATION")


# --- the upgrade --------------------------------------------------------------

def test_the_upgrade_imports_the_old_variables_once(client, monkeypatch):
    """An existing cloud.db keeps behaving as its .env said, so nobody's
    registration mode changes under them on the way up."""
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE settings")
        conn.execute("PRAGMA user_version = 6")
        conn.commit()
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_REGISTRATION", "open")
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_TURNSTILE_SITEKEY", "0xSITE")
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_TURNSTILE_SECRET", "0xSECRET")
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_BLOCKED_EMAIL_DOMAINS", "a.example, b.example")
    assert db.ensure_current() == steps_after(6)
    settings.invalidate()
    assert settings.registration() == "open"
    assert settings.turnstile_sitekey() == "0xSITE" and settings.turnstile_secret() == "0xSECRET"
    assert settings.blocked_email_domains() == {"a.example", "b.example"}


def test_a_fresh_database_takes_the_defaults(client, monkeypatch):
    """No import on a first install: the Admin page is the only place it is
    configured, so a stray variable cannot open registration quietly."""
    monkeypatch.setitem(os.environ, "GAMMA_CLOUD_REGISTRATION", "open")
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM settings").fetchone()[0] == 0
    settings.invalidate()
    assert settings.registration() == "invite"

"""The server options an admin sets in Settings (gamma/server_options.py),
once environment variables: checked as a whole before any is saved, a
secret sealed and never shown, the routes admin-only, and manage.py
``set-option`` writing the same record."""

import json

import pytest

from conftest import login, make_user
from gamma import server_options
from gamma.server_settings import _peek_raw


def test_options_are_checked_saved_and_their_secrets_sealed(server_option):
    assert (server_options.get("guest_max"), server_options.get("metadata_lookup")) == (500, True)
    for name, value in (("guest_max", 0), ("vault_keep_mb", -1), ("searxng_url", "ftp://searx"),
                        ("openalex_key", "a b"), ("nothing", 1)):
        with pytest.raises(ValueError):
            server_options.update({name: value})
    server_option("openalex_key", "oa-secret-key")
    server_option("metadata_lookup", "off")
    server_option("searxng_url", " http://searx.lan:8080/ ")
    assert server_options.get("openalex_key") == "oa-secret-key" and server_options.get("metadata_lookup") is False
    assert server_options.get("searxng_url") == "http://searx.lan:8080"
    raw = _peek_raw(server_options.KEY)
    assert "oa-secret-key" not in raw and json.loads(raw)["metadata_lookup"] is False
    view = server_options.view()
    assert view["openalex_key_set"] is True and "openalex_key" not in view
    server_option("metadata_lookup", True)  # back to its default: dropped from the record
    assert "metadata_lookup" not in json.loads(_peek_raw(server_options.KEY))


def test_the_environment_sets_the_most_guests_on_the_demo_and_hosted_servers(server_option, monkeypatch):
    server_option("guest_max", 40)
    assert server_options.view()["guest_max"] == 40 and server_options.view()["guest_max_env"] is False
    monkeypatch.setenv("GAMMA_GUEST_MAX", "300")  # the public demo's
    assert server_options.guest_max() == 300 and server_options.view()["guest_max_env"] is True
    with pytest.raises(ValueError, match="GAMMA_GUEST_MAX"):
        server_options.check_admin_change({"guest_max": 50})  # Settings shows it read-only
    monkeypatch.setenv("GAMMA_GUEST_MAX", "0")  # the account server's, on a hosted container
    assert server_options.guest_max() == 0


def test_an_admin_sets_them_in_settings(server_option):
    make_user("opt_admin", "opt-admin-pw", is_admin=1)
    make_user("opt_user", "opt-user-pw")
    admin, user = login("opt_admin", "opt-admin-pw"), login("opt_user", "opt-user-pw")
    server_option("searxng_url", "")  # reset after the test
    server_option("openalex_key", "")
    assert user.put("/api/admin/settings", json={"options": {"guest_max": 9}}).status_code == 403
    r = admin.put("/api/admin/settings", json={"options": {"searxng_url": "http://searx.lan:8080/", "openalex_key": "k-1"}})
    assert r.status_code == 200 and "k-1" not in r.text
    assert (r.json()["options"]["searxng_url"], r.json()["options"]["openalex_key_set"]) == ("http://searx.lan:8080", True)
    bad = admin.put("/api/admin/settings", json={"options": {"model_catalog": False, "vault_keep_mb": -3}})
    assert bad.status_code == 400 and server_options.get("model_catalog") is True  # nothing saved
    assert admin.get("/api/admin/settings").json()["options"]["openalex_key_set"] is True


def test_manage_sets_options(server_option, capsys):
    import manage
    server_option("model_catalog", True)  # reset after the test
    server_option("vault_keep_mb", 64)
    manage.set_options(["model_catalog", "off", "vault_keep_mb", "8"])
    assert "Set model_catalog, vault_keep_mb." in capsys.readouterr().out
    assert (server_options.get("model_catalog"), server_options.vault_keep_bytes()) == (False, 8 << 20)
    with pytest.raises(SystemExit):
        manage.set_options(["vault_keep_mb"])

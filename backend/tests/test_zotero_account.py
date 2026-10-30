"""The stored Zotero connection (gamma/zotero_account.py): connecting
checks the key with Zotero and keeps it sealed in a reserved account-wide
pref, the status shows only a hint, a disconnect (or the account's
deletion) revokes the keys Gamma had made and leaves no row, and an
unreadable key asks for a reconnect. Refusals are AccountErrors with the
status the routes answer."""

import json

import pytest
from conftest import account_of, guest_name, login, make_user
from cryptography.fernet import Fernet
from zotero_fake import FULL_ACCESS, FakeZotero

from gamma import workspaces, zotero_account, zotero_web
from gamma.zotero_account import AccountError
from gamma.db import USER_PREF_KEYS, connect_users_db, pref_scope
from gamma.publisher_sessions import unseal

USER, PASSWORD = "zacct_alice", "pw-zacct"


@pytest.fixture(scope="module")
def alice():
    ws = make_user(USER, PASSWORD)
    return account_of(USER), ws


@pytest.fixture
def fake(monkeypatch, alice):
    fake = FakeZotero().install(monkeypatch)
    yield fake
    zotero_account.disconnect(alice[0], revoke=False)


def _stored_text(user_id):
    with connect_users_db() as conn:
        row = conn.execute("SELECT value FROM user_prefs WHERE user_id = ? AND workspace_id = '' AND key = ?",
                           (user_id, zotero_account.PREF_KEY)).fetchone()
    return row[0] if row else None


def test_connecting_keeps_the_key_sealed_and_the_status_shows_a_hint(fake, alice):
    user_id, _ = alice
    assert zotero_account.status(user_id) == {"connected": False}
    assert zotero_account.api_key(user_id) is None
    status = zotero_account.connect(user_id, "  zkey-good \n")
    assert {k: v for k, v in status.items() if k != "connected_at"} == {
        "connected": True, "user_id": 123, "username": "tim", "hint": "…good", "via": "key",
        "access": {"library": True, "notes": True, "write": True, "files": True, "groups": "write"}}
    assert status["connected_at"]
    assert "zkey-good" not in json.dumps(status) and "key" not in status
    stored = _stored_text(user_id)
    assert "zkey-good" not in stored
    assert unseal(json.loads(stored)["key"]) == "zkey-good"
    assert zotero_account.api_key(user_id) == "zkey-good"
    client = zotero_account.client_for(user_id, heartbeat=lambda: None)
    assert client.key_info()["userID"] == 123 and client.heartbeat is not None


def test_a_key_zotero_refuses_or_that_cannot_read_the_library_is_not_kept(fake, alice):
    user_id, _ = alice
    with pytest.raises(AccountError) as refused:
        zotero_account.connect(user_id, "zkey-unknown")
    assert refused.value.status == 400 and "does not accept this key" in str(refused.value)
    fake.keys["zkey-groups-only"] = {"user": {"library": False}, "groups": {"all": {"library": True}}}
    with pytest.raises(AccountError) as limited:
        zotero_account.connect(user_id, "zkey-groups-only")
    assert limited.value.status == 400 and "Allow library access" in str(limited.value)
    assert "zkey-groups-only" in fake.keys  # pasted: left as it is
    fake.keys["zkey-oauth-groups-only"] = fake.keys["zkey-groups-only"]
    with pytest.raises(AccountError):
        zotero_account.connect(user_id, "zkey-oauth-groups-only", via="oauth")
    assert "zkey-oauth-groups-only" not in fake.keys  # made for this connection, useless to it: revoked
    calls = len(fake.calls)
    for junk in ("", "two words", "x" * 129, None):
        with pytest.raises(AccountError) as malformed:
            zotero_account.connect(user_id, junk)
        assert malformed.value.status == 400 and str(malformed.value) == zotero_account.NOT_A_KEY
    assert len(fake.calls) == calls  # never sent to Zotero
    assert zotero_account.status(user_id) == {"connected": False}


def test_zotero_failing_while_connecting_is_a_502(fake, alice):
    for _ in range(zotero_web.MAX_RETRIES + 1):  # every try, its retries included
        fake.fail_next(500, "/keys/current")
    with pytest.raises(AccountError) as failed:
        zotero_account.connect(alice[0], "zkey-good")
    assert failed.value.status == 502 and "Zotero answered 500" in str(failed.value)
    assert failed.value.kind == "offline"


def test_the_status_words_the_keys_group_access(monkeypatch, alice):
    user_id, _ = alice
    for groups, word in (({}, "none"), ({"all": {"library": True}}, "read"),
                         ({"all": {"library": True, "write": True}}, "write"), ({"9": {"library": True}}, "some")):
        fake = FakeZotero(access={"user": {"library": True}, "groups": groups}).install(monkeypatch)
        access = zotero_account.connect(user_id, fake.api_key)["access"]
        assert access == {"library": True, "notes": False, "write": False, "files": False, "groups": word}
    zotero_account.disconnect(user_id, revoke=False)


def test_a_disconnect_revokes_only_the_keys_gamma_had_made(fake, alice):
    user_id, _ = alice
    zotero_account.connect(user_id, "zkey-good")
    zotero_account.disconnect(user_id)
    assert zotero_account.status(user_id) == {"connected": False}
    assert _stored_text(user_id) is None  # the row goes, no "null" left behind
    zotero_account.disconnect(user_id)  # nothing to forget: fine
    assert "zkey-good" in fake.keys  # pasted: the person's own, maybe used elsewhere
    fake.keys["zkey-made-by-oauth"] = fake.keys["zkey-good"]
    assert zotero_account.connect(user_id, "zkey-made-by-oauth", via="oauth")["via"] == "oauth"
    zotero_account.disconnect(user_id, revoke=False)
    assert "zkey-made-by-oauth" in fake.keys
    zotero_account.connect(user_id, "zkey-made-by-oauth", via="oauth")
    zotero_account.disconnect(user_id)
    assert "zkey-made-by-oauth" not in fake.keys
    assert [path for m, path, _ in fake.calls if m == "DELETE"] == ["/keys/current"]


def test_a_new_connection_revokes_the_oauth_key_it_replaces(fake, alice):
    user_id, _ = alice
    fake.keys["zkey-oauth-a"] = fake.keys["zkey-good"]
    zotero_account.connect(user_id, "zkey-oauth-a", via="oauth")
    zotero_account.connect(user_id, "zkey-oauth-a", via="oauth")  # the same key again: kept
    assert "zkey-oauth-a" in fake.keys
    zotero_account.connect(user_id, "zkey-good")
    assert "zkey-oauth-a" not in fake.keys
    assert zotero_account.api_key(user_id) == "zkey-good"


def test_a_key_sealed_under_another_data_key_asks_for_a_reconnect(fake, alice, monkeypatch):
    user_id, _ = alice
    zotero_account.connect(user_id, "zkey-good", via="oauth")
    monkeypatch.setenv("GAMMA_PUBLISHER_SESSION_KEY", Fernet.generate_key().decode("ascii"))
    for read in (zotero_account.api_key, zotero_account.client_for):
        with pytest.raises(AccountError) as unreadable:
            read(user_id)
        assert unreadable.value.status == 409 and str(unreadable.value) == zotero_account.UNREADABLE
        assert unreadable.value.kind == "auth"  # a link asks for a reconnect
    assert zotero_account.status(user_id)["connected"] is True  # the status never unseals
    zotero_account.disconnect(user_id)  # cannot revoke what it cannot read; forgets it all the same
    assert zotero_account.status(user_id) == {"connected": False}
    assert "zkey-good" in fake.keys


def test_without_a_connection_there_is_no_client(alice):
    with pytest.raises(AccountError) as missing:
        zotero_account.client_for(alice[0])
    assert missing.value.status == 409 and str(missing.value) == zotero_account.NOT_CONNECTED
    assert missing.value.kind == "not_connected"
    assert isinstance(missing.value, ValueError)  # a background sync records it as any refusal


def test_the_pref_is_account_wide_and_refused_by_the_prefs_api(alice):
    assert zotero_account.PREF_KEY in USER_PREF_KEYS
    assert pref_scope(zotero_account.PREF_KEY, alice[1]) == ""
    c = login(USER, PASSWORD)
    assert c.get(f"/api/prefs/{zotero_account.PREF_KEY}").status_code == 400
    assert c.put(f"/api/prefs/{zotero_account.PREF_KEY}", json={"value": {"key": "x"}}).status_code == 400
    assert _stored_text(alice[0]) is None


def test_the_module_leaves_guests_to_the_routes(fake, guest):
    """Refusing guests is the endpoints' part (require_personal_user_id):
    the module works for any account it is handed."""
    user_id = account_of(guest_name())
    try:
        assert zotero_account.connect(user_id, "zkey-good")["connected"] is True
    finally:
        zotero_account.disconnect(user_id, revoke=False)
    assert zotero_account.status(user_id) == {"connected": False}


def test_deleting_the_account_revokes_a_key_gamma_had_made(fake, monkeypatch):
    made, pasted, unlucky = "zacct_gone_oauth", "zacct_gone_key", "zacct_gone_down"
    for name in (made, pasted, unlucky):
        make_user(name, f"pw-{name}")
    fake.keys["zkey-made"] = dict(FULL_ACCESS)
    zotero_account.connect(account_of(made), "zkey-made", via="oauth")
    zotero_account.connect(account_of(pasted), "zkey-good", via="key")
    workspaces.delete_account(account_of(made), release_now=True, by="test")
    assert "zkey-made" not in fake.keys and fake.calls[-1][:2] == ("DELETE", "/keys/current")
    workspaces.delete_account(account_of(pasted), release_now=True, by="test")
    assert "zkey-good" in fake.keys  # the person's own key, perhaps used elsewhere
    assert account_of(made) == account_of(pasted) == ""

    def broken(user_id, *, revoke=True):
        raise RuntimeError("the pref store is locked")

    with monkeypatch.context() as patch:  # Zotero never stops a deletion
        patch.setattr(zotero_account, "disconnect", broken)
        workspaces.delete_account(account_of(unlucky), release_now=True, by="test")
    assert account_of(unlucky) == ""

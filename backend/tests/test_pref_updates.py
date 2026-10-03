"""Read-modify-write of JSON preferences (db.update_pref): the AI provider
entries, the translation engine keys and the seen notices are whole values
edited by several paths at once — two tabs, a sign-in whose save follows
network calls, a token refresh. Each edit runs its read and its write in one
transaction, so none is lost."""

import threading
import time

from conftest import account_of, login, make_user

from gamma import db, notices, translate_engines
from gamma.ai_settings import load_provider_entries, update_provider_entries
from gamma.routers import ai as ai_router


def _later(fn, delay):
    t = threading.Thread(target=lambda: (time.sleep(delay), fn()))
    t.start()
    return t


def test_update_pref_serializes_concurrent_edits():
    make_user("pu_serial", "pu-password-1")
    me = account_of("pu_serial")

    def slow_append(value):
        time.sleep(0.3)  # still inside the first edit's transaction
        return (value or []) + ["slow"]

    t = _later(lambda: db.update_pref(me, "ai-provider", lambda v: (v or []) + ["fast"]), 0.1)
    db.update_pref(me, "ai-provider", slow_append)
    t.join()
    assert sorted(db.get_pref(me, "ai-provider")[0]) == ["fast", "slow"]


def test_update_pref_aborts_on_error_and_skips_unchanged_writes():
    make_user("pu_abort", "pu-password-1")
    me = account_of("pu_abort")
    db.set_pref(me, "ai-provider", ["kept"])
    _, stamp = db.get_pref(me, "ai-provider")

    def boom(value):
        value.append("lost")
        raise ValueError("refused")
    try:
        db.update_pref(me, "ai-provider", boom)
    except ValueError:
        pass
    assert db.get_pref(me, "ai-provider") == (["kept"], stamp)
    # an edit in place that changes nothing writes nothing (no new stamp)
    assert db.update_pref(me, "ai-provider", lambda v: v) == ["kept"]
    assert db.get_pref(me, "ai-provider")[1] == stamp


def test_chatgpt_signin_keeps_a_key_added_during_its_network_calls(monkeypatch):
    """The audit's repro: the sign-in stored the list it had read before the
    live model listing; a key added meanwhile in another tab was lost."""
    make_user("pu_oauth", "pu-password-1")
    me = account_of("pu_oauth")
    tab1, tab2 = login("pu_oauth", "pu-password-1"), login("pu_oauth", "pu-password-1")
    monkeypatch.setattr(ai_router, "redeem_chatgpt_signin", lambda owner, state, callback: {
        "access_token": "a", "refresh_token": "r", "expires_at": int(time.time()) + 3600,
        "account_id": "acct"})

    def slow_models(user, entry_id):
        time.sleep(0.6)
        return "gpt-x"
    monkeypatch.setattr(ai_router, "seeded_chatgpt_models", slow_models)

    out = {}
    t = threading.Thread(target=lambda: out.setdefault("r", tab1.post(
        "/api/ai/oauth/chatgpt/complete", json={"state": "s", "callback": "code"})))
    t.start()
    time.sleep(0.2)
    r = tab2.post("/api/ai/providers", json={"protocol": "anthropic", "name": "My key",
                                             "api_key": "sk-ant-test-0123456789", "models": "claude-x"})
    assert r.status_code == 200, r.text
    t.join()
    assert out["r"].status_code == 200, out["r"].text
    entries = {e["protocol"]: e for e in load_provider_entries(me)}
    assert set(entries) == {"chatgpt", "anthropic"}
    assert entries["chatgpt"]["models"] == "gpt-x"  # the seeded models landed on the entry itself


def test_provider_edits_are_read_modify_write():
    make_user("pu_edit", "pu-password-1")
    me = account_of("pu_edit")
    c = login("pu_edit", "pu-password-1")
    for name in ("one", "two"):
        assert c.post("/api/ai/providers", json={"protocol": "anthropic", "name": name,
                                                 "api_key": f"sk-ant-{name}-0123456789"}).status_code == 200
    ids = {e["name"]: e["id"] for e in load_provider_entries(me)}
    assert c.put(f"/api/ai/providers/{ids['one']}", json={"name": "uno"}).status_code == 200
    assert c.put("/api/ai/providers/nope", json={"name": "x"}).status_code == 404
    assert c.delete(f"/api/ai/providers/{ids['two']}").status_code == 200
    assert [e["name"] for e in load_provider_entries(me)] == ["uno"]


def test_token_refresh_write_keeps_other_entries():
    """A refreshed sign-in writes back only its own tokens: an entry added
    after the refresh read the list survives."""
    make_user("pu_refresh", "pu-password-1")
    me = account_of("pu_refresh")
    update_provider_entries(me, lambda entries: entries.append(
        {"id": "sig", "protocol": "chatgpt", "oauth": {"access_token": "old", "refresh_token": "r1"}}))

    class Flow:
        def needs_refresh(self, oauth):
            return True

        def refresh(self, oauth):
            # another tab adds a key while the identity provider answers
            update_provider_entries(me, lambda entries: entries.append(
                {"id": "key", "protocol": "anthropic", "api_key": "sk-ant-late-0123456789"}))
            return {"access_token": "new", "refresh_token": "r2"}

    from gamma.ai_settings import _refreshed_oauth
    assert _refreshed_oauth(me, "sig", Flow())["access_token"] == "new"
    entries = {e["id"]: e for e in load_provider_entries(me)}
    assert set(entries) == {"sig", "key"}
    assert entries["sig"]["oauth"]["refresh_token"] == "r2"


def test_translate_engine_saves_do_not_drop_each_other(monkeypatch):
    make_user("pu_engines", "pu-password-1")
    me = account_of("pu_engines")
    real = db.update_pref

    def slow_update(user, key, change, ws=""):
        def slow_change(value):
            if (value or {}).get("google") is None:
                time.sleep(0.3)  # the first save holds its transaction a while
            return change(value)
        return real(user, key, slow_change, ws)
    monkeypatch.setattr(translate_engines, "update_pref", slow_update)
    t = _later(lambda: translate_engines.save(me, "youdao", {"app_key": "k", "app_secret": "s"}), 0.1)
    translate_engines.save(me, "google", {"api_key": "g-key-0123"})
    t.join()
    assert set(translate_engines.load(me)) == {"google", "youdao"}
    translate_engines.remove(me, "google")
    assert set(translate_engines.load(me)) == {"youdao"}


def test_mark_seen_keeps_every_notice():
    make_user("pu_seen", "pu-password-1")
    me = account_of("pu_seen")
    threads = [threading.Thread(target=notices.mark_seen, args=(me, f"n{i}", f"fp{i}")) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert notices.seen_map(me) == {f"n{i}": f"fp{i}" for i in range(8)}

"""E-mail invitations a Gamma server sends through the account server: the
mail per kind and without links in the inviter's or the server's words,
the refusals and the rate limits (refused calls spending no shared
budget, the breaker only self-connected servers share, one inbox from one
inviter), the invite code an invitation carries (and whose invitations
may carry one), a re-send, the sending client's hold on its invitations,
cancel, a mail that fails, the invitation page and its Continue,
registering from it (never confirmed by the invitation: a verify link back
to it, whatever the address; a next a browser would read as another
host), a Google/GitHub sign-up and the sign-in page coming back to it,
the Admin list, the purge and the step-13 upgrade."""

import sqlite3
from contextlib import closing
from urllib.parse import parse_qs, urlencode, urlsplit

import manage
import pytest
from conftest import invite, last_link, make_admin, register, set_setting, steps_after, verify
from fastapi.testclient import TestClient
from test_profile import SERVER, SERVER_CALLBACK, bearer, desktop_tokens, lab_tokens

from gammacloud import accounts, config, db, invitations, mail, oidc, providers, ratelimit
from gammacloud.providers import Identity

INVITE_ID = "inv_" + "a" * 20
SHARE_NEXT = "/?share=tok123"


def lab_client(*redirect_uris, kind="server", owner=""):
    """A client for the lab's server: by default one an admin made; with
    ``owner``, one that account connected itself (``kind="server"``) or its
    hosted server's (``kind="container"``)."""
    with closing(db.connect()) as conn:
        client_id, secret = oidc.create_client(conn, name="The lab's server", kind=kind,
                                               redirect_uris=[SERVER_CALLBACK, *redirect_uris], owner=owner)
        conn.commit()
    return client_id, secret


def account_id(username="alice"):
    with closing(db.connect()) as conn:
        return accounts.by_username(conn, username)["id"]


def alice(client, display_name=""):
    """Alice, signed in at the lab's server: her access token's headers."""
    register(client)
    verify(client)
    if display_name:
        client.patch("/api/me", json={"display_name": display_name})
    return bearer(lab_tokens(client, *lab_client()))


def send(client, h, **over):
    body = {"email": "bob@example.org", "server_url": SERVER, "server_name": "The lab", "kind": "page",
            "what": "Reading notes", "invite_id": INVITE_ID, "next": SHARE_NEXT, **over}
    return client.post("/api/invitations", headers=h, json=body)


def row(invite_id=INVITE_ID):
    with closing(db.connect()) as conn:
        found = conn.execute("SELECT * FROM server_invitations WHERE invite_id = ?", (invite_id,)).fetchone()
    return dict(found) if found else None


def code_row(code):
    with closing(db.connect()) as conn:
        return conn.execute("SELECT * FROM invites WHERE code = ?", (code,)).fetchone()


def browser(client):
    """A second browser, signed out, for the person invited."""
    return TestClient(client.app, base_url="http://testserver")


# --- sending ------------------------------------------------------------------

def test_the_mail_names_the_inviter_the_place_and_what(client):
    h = alice(client, display_name="Alice\r\nExample")
    subjects = {}
    for n, (kind, what) in enumerate((("server", ""), ("workspace", "Lab group"), ("page", "Reading notes"),
                                      ("folder", "Papers"))):
        r = send(client, h, kind=kind, what=what, invite_id=f"{INVITE_ID}{n}")
        assert r.status_code == 200, r.text
        assert r.json() == {"ok": True, "expires_at": row(f"{INVITE_ID}{n}")["expires_at"]}
        assert r.json()["expires_at"][:10] == db.after(14 * 86400)[:10]
        subjects[kind] = mail.outbox[-1]["subject"]
    who = "Alice Example (@alice)"
    assert subjects == {"server": f"{who} invited you to join The lab (lab.example.org)",
                        "workspace": f"{who} invited you to join the workspace “Lab group”",
                        "page": f"{who} invited you to join the page “Reading notes”",
                        "folder": f"{who} invited you to join the folder “Papers”"}
    sent = mail.outbox[-1]
    token = last_link("/invite")
    assert sent["to"] == "bob@example.org" and row(f"{INVITE_ID}3")["token_hash"] == db.token_hash(token)
    assert (f"{who} invited you to join the folder “Papers” on the Gamma server The lab (lab.example.org)."
            in sent["body"])
    assert "Accepting takes a free Gamma Cloud account" in sent["body"] and "works for 14 days" in sent["body"]
    assert "Accept the invitation" in sent["html"] and f'href="http://testserver/invite?token={token}"' in sent["html"]
    assert f"{who} invited you to join the Gamma server The lab (lab.example.org)." in mail.outbox[-4]["body"]
    with closing(db.connect()) as conn:
        details = [r[0] for r in conn.execute("SELECT detail FROM audit WHERE event = 'invitation.send'")]
    assert details == ["server lab.example.org", "workspace lab.example.org", "page lab.example.org",
                       "folder lab.example.org"]                              # never the address


def test_a_server_invitation_is_named_by_the_server_alone(client):
    h = alice(client)
    send(client, h, kind="server", what="Reading notes", server_name="")
    assert mail.outbox[-1]["subject"] == "@alice invited you to join lab.example.org"   # the host, not repeated
    assert row()["what"] == ""


def test_a_server_named_by_its_hostname_is_named_by_its_host(client):
    """A Gamma server with no name of its own sends its hostname, without
    the port: on another port the mail names it once, by host and port,
    and never as "[link]"."""
    register(client)
    verify(client)
    h = bearer(lab_tokens(client, *lab_client("https://lab.example.org:8443/api/auth/cloud/callback")))
    for n, (kind, name) in enumerate((("server", "lab.example.org"), ("page", "Lab.Example.org"))):
        assert send(client, h, kind=kind, server_url="https://lab.example.org:8443", server_name=name,
                    invite_id=f"{INVITE_ID}{n}").status_code == 200
        assert row(f"{INVITE_ID}{n}")["server_name"] == "lab.example.org:8443"
    server, page = mail.outbox[-2:]
    assert server["subject"] == "@alice invited you to join lab.example.org:8443"
    assert "@alice invited you to join the Gamma server lab.example.org:8443." in server["body"]
    assert "the page “Reading notes” on the Gamma server lab.example.org:8443." in page["body"]
    for sent in (server, page):
        assert sent["body"].count("lab.example.org") == 1
        assert "[link]" not in sent["subject"] + sent["body"] + sent["html"]


def test_what_a_server_cannot_send(client):
    h = alice(client)
    assert send(client, {}).status_code == 401
    r = send(client, bearer(desktop_tokens(client)))
    assert r.status_code == 403 and "desktop app" in r.json()["detail"]
    assert send(client, h, server_url="https://other.example.org").status_code == 403    # not its client's origin
    assert send(client, h, server_url="ftp://lab.example.org").status_code == 400
    loopback = bearer(lab_tokens(client, *lab_client("http://127.0.0.1:8123/api/auth/cloud/callback")))
    assert send(client, loopback, server_url=SERVER, invite_id=INVITE_ID + "ok").status_code == 200
    assert send(client, loopback, server_url="http://127.0.0.1:8123").status_code == 403  # a link cannot reach it
    ratelimit.clear()
    for bad in ({"next": "//evil.example/x"}, {"next": "https://evil.example"}, {"next": "/a\\b"},
                {"next": "/\t/evil.example"}, {"next": "/" + "x" * 2048}, {"email": "not-an-email"},
                {"invite_id": "short"}, {"invite_id": "x" * 65}, {"invite_id": "has space in it!"},
                {"invite_id": INVITE_ID + "\n"}, {"kind": "team"}):
        assert send(client, h, **bad).status_code == 400, bad
    assert row() is None and [m for m in mail.outbox if "/invite?token=" in m["body"]] == [mail.outbox[-1]]


def test_the_mail_carries_no_link_from_the_inviter(client):
    h = alice(client, display_name="Support www.evil.example")
    send(client, h, what="Restore your account at https://evil.example/x")
    sent = mail.outbox[-1]
    assert sent["subject"] == ("Support [link] (@alice) invited you to join the page “Restore your account at "
                               "[link]”")
    assert "evil.example" not in sent["subject"] + sent["body"] + sent["html"]
    assert "on the Gamma server The lab (lab.example.org)." in sent["body"]   # the server's own place stays
    assert row()["what"] == "Restore your account at https://evil.example/x"  # only the mail loses it


def test_the_server_s_own_name_carries_no_link_either(client):
    """A server someone connected names itself, so its name is the
    inviter's words too; its host, which the link leads to, stays."""
    h = alice(client)
    send(client, h, kind="server", server_name="Restore your account at https://evil.example/x")
    assert mail.outbox[-1]["subject"] == ("@alice invited you to join Restore your account at [link] "
                                          "(lab.example.org)")
    send(client, h, kind="server", server_name="gamma-cloud.services help", invite_id=INVITE_ID + "2")
    sent = mail.outbox[-1]
    assert sent["subject"] == "@alice invited you to join [link] help (lab.example.org)"
    assert "gamma-cloud.services" not in sent["body"] + sent["html"]


@pytest.mark.parametrize("text, shown", [
    ("Restore your account at https://evil.example/x", "Restore your account at [link]"),
    ("ftp://files.example", "[link]"),
    ("go to www.evil.example now", "go to [link] now"),
    ("see evil.example/restore", "see [link]"),
    ("Notes on EVIL.COM", "Notes on [link]"),
    ("write to admin@evil.co.uk", "write to admin@[link]"),
    ("ask acme.com, help.network or gamma-cloud.services", "ask [link], [link] or [link]"),   # any top-level domain
    ("Reading notes, v1.2 (e.g. Paper.pdf)", "Reading notes, v1.2 (e.g. Paper.pdf)"),
    ("attention.pdf, notes.md and my.notes.md on Node.js", "attention.pdf, notes.md and my.notes.md on Node.js"),
])
def test_link_like_words_become_a_mark(text, shown):
    assert invitations.unlinked(text) == shown


def test_sending_is_rate_limited_per_account(client):
    h = alice(client)
    ratelimit.clear()
    for n in range(30):
        assert send(client, h, email=f"bob{n}@example.org", invite_id=f"{INVITE_ID}{n:02d}").status_code == 200
    r = send(client, h, email="bob99@example.org", invite_id=f"{INVITE_ID}99")
    assert r.status_code == 429 and "Retry-After" in r.headers


def test_the_ip_limit_counts_only_tokens_that_fail(client):
    """A server holds many people's tokens behind one address (the share
    host, hosted servers), so its valid ones never fill the IP's bucket."""
    h = alice(client)
    ratelimit.clear()
    for n in range(5):
        assert send(client, h, email=f"bob{n}@example.org", invite_id=f"{INVITE_ID}{n}").status_code == 200
    bad = {"Authorization": "Bearer not-a-token"}
    assert [send(client, bad).status_code for _ in range(61)] == [401] * 60 + [429]
    assert send(client, h).status_code == 200


def fill(key, hits, window):
    """Spend a rate-limit bucket to its limit."""
    for _ in range(hits):
        ratelimit.check(key, hits, window)


def test_the_daily_and_the_client_limits(client):
    h = alice(client)
    send(client, h)
    inviter, client_id = row()["inviter_account_id"], row()["client_id"]
    mailed = len(mail.outbox)
    for key, hits, window in ((f"invitations:day:{inviter}", 100, 86400),
                              (f"invitations:client:{client_id}", 600, 3600)):
        ratelimit.clear()
        fill(key, hits, window)
        r = send(client, h, invite_id=INVITE_ID + "x")
        assert r.status_code == 429 and "Retry-After" in r.headers, key
    assert row(INVITE_ID + "x") is None and len(mail.outbox) == mailed


def test_refused_calls_spend_no_client_or_shared_budget(client):
    """Only a request that will send counts per client and in the breaker,
    so calls refused anyway cannot use up what everyone's invitations
    need."""
    alice(client)
    own_id, own_secret = lab_client(owner=account_id())                   # connected by alice herself
    own = bearer(lab_tokens(client, own_id, own_secret))
    ratelimit.clear()
    assert send(client, bearer(desktop_tokens(client))).status_code == 403
    assert send(client, own, server_url="https://other.example.org").status_code == 403
    assert send(client, own, email="not-an-email").status_code == 400
    assert send(client, own, next="//evil.example").status_code == 400
    assert not [key for key in ratelimit._buckets if key.startswith(("invitations:client:", "invitations:connected"))]
    assert send(client, own).status_code == 200
    assert ratelimit._buckets[f"invitations:client:{own_id}"][1] == 1
    assert ratelimit._buckets["invitations:connected"][1] == 1


def test_only_self_connected_servers_share_the_breaker(client):
    """The servers people connected themselves share one daily breaker; the
    operator's own (a client made on the Admin page, a hosted server) are
    bounded by their own client's limit alone, so a few connected servers
    cannot starve them."""
    h = alice(client)                                                      # a client made on the Admin page
    hosted = bearer(lab_tokens(client, *lab_client(kind="container", owner=account_id())))
    own = bearer(lab_tokens(client, *lab_client(owner=account_id())))
    ratelimit.clear()
    fill("invitations:connected", 3000, 86400)
    r = send(client, own)
    assert r.status_code == 429 and row() is None
    assert send(client, h).status_code == 200
    assert send(client, hosted, invite_id=INVITE_ID + "hosted").status_code == 200
    assert ratelimit._buckets["invitations:connected"][1] == 3001                 # the refused one only


def test_one_inviter_mails_one_inbox_a_few_times_only(client):
    """Five an hour and twenty a day from one inviter to one inbox, however
    the address is written. Nothing across inviters, which would let anyone
    keep a person from being invited at all."""
    h = alice(client)
    for n in range(5):
        assert send(client, h, invite_id=f"{INVITE_ID}{n}").status_code == 200
    r = send(client, h, email="Bob@Example.org", invite_id=INVITE_ID + "5")
    assert r.status_code == 429 and "Retry-After" in r.headers and row(INVITE_ID + "5") is None
    assert send(client, h, email="carol@example.org", invite_id=INVITE_ID + "c").status_code == 200
    with browser(client) as other:
        register(other, "dave")
        verify(other)
        dave = bearer(lab_tokens(other, *lab_client()))
    assert send(client, dave, invite_id=INVITE_ID + "dave").status_code == 200
    ratelimit.clear()
    fill(f"invitations:to-day:bob@example.org:{account_id()}", 20, 86400)
    assert send(client, h, invite_id=INVITE_ID + "6").status_code == 429


def test_cancelling_is_rate_limited_per_account(client):
    h = alice(client)
    ratelimit.clear()
    ref = {"server_url": SERVER, "invite_id": INVITE_ID}
    assert all(client.post("/api/invitations/cancel", headers=h, json=ref).status_code == 200 for _ in range(120))
    assert client.post("/api/invitations/cancel", headers=h, json=ref).status_code == 429


def test_the_code_is_minted_only_for_an_address_without_an_account(client):
    with browser(client) as other:
        register(other, "carol")
    h = alice(client)
    send(client, h)
    code = row()["code"]
    inv = code_row(code)
    assert (inv["uses_total"], inv["plan"], inv["note"]) == (1, "free", "invitation to lab.example.org")
    assert inv["expires_at"][:10] == db.after(14 * 86400)[:10] and inv["created_by"] == row()["inviter_account_id"]
    r = send(client, h, email="CAROL@example.org", invite_id=INVITE_ID + "c")
    assert r.status_code == 200 and set(r.json()) == {"ok", "expires_at"}        # the same answer either way
    assert row(INVITE_ID + "c")["code"] == "" and row(INVITE_ID + "c")["email"] == "carol@example.org"
    assert "Sign in with your Gamma Cloud account to accept." in mail.outbox[-1]["body"]
    set_setting("registration", "closed")
    send(client, h, email="dave@example.org", invite_id=INVITE_ID + "d")
    assert row(INVITE_ID + "d")["code"] == "" and "not taking new ones" in mail.outbox[-1]["body"]
    # open registration needs no code
    set_setting("registration", "open")
    send(client, h, email="erin@example.org", invite_id=INVITE_ID + "e")
    assert row(INVITE_ID + "e")["code"] == "" and "Accepting takes a free Gamma Cloud account" in mail.outbox[-1]["body"]


def test_only_a_server_the_operator_runs_gives_a_code(client):
    """A code gets past invite-only sign-up, so it is the operator's to
    give: a server someone connected themselves gives none until the
    operator lets any connected server."""
    h = alice(client)                                                      # a client made on the Admin page
    hosted = bearer(lab_tokens(client, *lab_client(kind="container", owner=account_id())))
    own = bearer(lab_tokens(client, *lab_client(owner=account_id())))      # connected by alice herself
    send(client, h)
    send(client, hosted, invite_id=INVITE_ID + "hosted")
    assert row()["code"] and row(INVITE_ID + "hosted")["code"]
    send(client, own, invite_id=INVITE_ID + "own")
    assert row(INVITE_ID + "own")["code"] == ""
    assert "and new ones need an invite code right now. If you already have an account" in mail.outbox[-1]["body"]
    token = last_link("/invite")
    with browser(client) as bob:
        page = bob.get(f"/invite?token={token}").text
        here = f"/invite?token={token}"
        assert "new ones need an invite code right now" in page
        assert f"<a class='btn btn--primary' href='/login?{urlencode({'next': here})}'>Sign in</a>" in page
        register_link = "/register?" + urlencode({"email": "bob@example.org", "next": here})
        assert f"href='{register_link.replace('&', '&amp;')}'>Create an account</a>" in page   # the register page judges
    set_setting("invitation_signup", "any")
    send(client, own, invite_id=INVITE_ID + "own")
    assert row(INVITE_ID + "own")["code"] and "Accepting takes a free Gamma Cloud account" in mail.outbox[-1]["body"]


def test_no_code_while_the_allowed_domains_are_set(client):
    """An address at an allowed domain registers without a code; any other
    is what the operator kept out, so an invitation does not let it in."""
    h = alice(client)
    set_setting("allowed_email_domains", "uni.example")
    send(client, h, email="bob@uni.example")
    assert row()["code"] == "" and "Accepting takes a free Gamma Cloud account" in mail.outbox[-1]["body"]
    send(client, h, email="eve@elsewhere.example", invite_id=INVITE_ID + "eve")
    assert row(INVITE_ID + "eve")["code"] == "" and "new ones need an invite code" in mail.outbox[-1]["body"]
    with browser(client) as eve:
        r = eve.post("/api/register", json={"email": "eve@elsewhere.example", "username": "eve",
                                            "password": "correct horse battery"})
        assert r.status_code == 403 and "cannot register without an invite code" in r.json()["detail"]


def test_sending_again_replaces_the_link_and_keeps_the_code(client):
    h = alice(client)
    send(client, h)
    first, code = last_link("/invite"), row()["code"]
    send(client, h, what="Reading notes, part 2")
    second = last_link("/invite")
    assert second != first and row()["code"] == code and row()["what"] == "Reading notes, part 2"
    with closing(db.connect()) as conn:
        assert conn.execute("SELECT COUNT(*) FROM server_invitations").fetchone()[0] == 1
    with browser(client) as bob:
        assert bob.get(f"/invite?token={first}").status_code == 404
        assert bob.get(f"/invite?token={second}").status_code == 200
    # a code that stopped working is replaced, and the dead one goes
    with closing(db.connect()) as conn:
        accounts.set_invite_disabled(conn, code, True, "test")
        conn.commit()
    send(client, h)
    assert row()["code"] not in ("", code) and code_row(code) is None


def test_a_re_send_moves_the_code_along_for_the_same_inbox_only(client):
    h = alice(client)
    send(client, h)
    code, soon = row()["code"], db.after(3 * 86400)
    with closing(db.connect()) as conn:                                    # as if the first mail went out 11 days ago
        conn.execute("UPDATE server_invitations SET expires_at = ?", (soon,))
        conn.execute("UPDATE invites SET expires_at = ? WHERE code = ?", (soon, code))
        conn.commit()
    send(client, h)
    assert row()["code"] == code and code_row(code)["expires_at"] == row()["expires_at"] > soon
    # the same id sent on to another address: the code stays behind, and that address gets one of its own, or
    # none with an account, so the sender cannot learn whether the first address has an account
    send(client, h, email="dave@example.org")
    dave_code = row()["code"]
    assert dave_code not in ("", code) and code_row(code) is None
    with browser(client) as other:
        register(other, "carol")
    send(client, h, email="carol@example.org")
    assert row()["code"] == "" and code_row(dave_code) is None


def test_only_the_client_that_sent_an_invitation_replaces_or_cancels_it(client):
    h = alice(client)
    other = bearer(lab_tokens(client, *lab_client()))                   # another client for the same server
    send(client, h)
    first, mailed = row(), len(mail.outbox)
    r = send(client, other, what="Something else")
    assert r.status_code == 409 and r.json()["detail"] == invitations.OTHER_CLIENT
    ref = {"server_url": SERVER, "invite_id": INVITE_ID}
    r = client.post("/api/invitations/cancel", headers=other, json=ref)
    assert r.status_code == 409 and r.json()["detail"] == invitations.OTHER_CLIENT
    assert row() == first and len(mail.outbox) == mailed
    assert send(client, other, invite_id=INVITE_ID + "other").status_code == 200      # its own ids are its own
    assert client.post("/api/invitations/cancel", headers=h, json=ref).json() == {"ok": True, "removed": True}


def test_cancel_takes_the_link_and_an_unused_code(client):
    h = alice(client)
    send(client, h)
    token, code = last_link("/invite"), row()["code"]
    ref = {"server_url": SERVER, "invite_id": INVITE_ID}
    assert client.post("/api/invitations/cancel", headers=bearer(desktop_tokens(client)), json=ref).status_code == 403
    assert client.post("/api/invitations/cancel", headers=h, json={**ref, "server_url": "https://other.example.org"}
                       ).status_code == 403
    assert client.post("/api/invitations/cancel", headers=h, json=ref).json() == {"ok": True, "removed": True}
    assert row() is None and code_row(code) is None
    with browser(client) as bob:
        assert bob.get(f"/invite?token={token}").status_code == 404
    assert client.post("/api/invitations/cancel", headers=h, json=ref).json() == {"ok": True, "removed": False}
    # a code someone registered with stays: it names the account that came in through it
    send(client, h)
    token, code = last_link("/invite"), row()["code"]
    with browser(client) as bob:
        register(bob, "bob", email="bob@work.example", code=code)
    client.post("/api/invitations/cancel", headers=h, json=ref)
    assert accounts.invite_state(code_row(code)) == "spent"
    with closing(db.connect()) as conn:
        assert "invitation.cancel" in [r[0] for r in conn.execute("SELECT event FROM audit")]


def test_a_mail_that_fails_takes_the_invitation_back(client, monkeypatch):
    h = alice(client)
    working = mail.send

    def boom(*a, **k):
        raise mail.MailError("relay down")

    def invitation_codes():
        with closing(db.connect()) as conn:
            return [r[0] for r in conn.execute("SELECT code FROM invites WHERE invitation = 1")]
    # a first invitation goes with the code it minted
    monkeypatch.setattr(mail, "send", boom)
    r = send(client, h)
    assert r.status_code == 503 and "could not send" in r.json()["detail"]
    assert row() is None and invitation_codes() == []
    # a re-send leaves the invitation already in the inbox working, its code alive as it was
    monkeypatch.setattr(mail, "send", working)
    send(client, h)
    first, sent = last_link("/invite"), row()
    code_before = dict(code_row(sent["code"]))
    monkeypatch.setattr(mail, "send", boom)
    assert send(client, h, what="Reading notes, part 2").status_code == 503
    assert row() == sent and dict(code_row(sent["code"])) == code_before
    with browser(client) as bob:
        assert bob.get(f"/invite?token={first}").status_code == 200
    # a code the failed send minted goes too
    with closing(db.connect()) as conn:
        accounts.set_invite_disabled(conn, sent["code"], True, "test")
        conn.commit()
    assert send(client, h).status_code == 503
    assert row() == sent and sent["code"] not in invitation_codes() and invitation_codes() == []


# --- the page -----------------------------------------------------------------

def test_the_invitation_page_signed_out(client):
    h = alice(client)
    send(client, h, what="<b>Notes</b>")
    token, code = last_link("/invite"), row()["code"]
    with browser(client) as bob:
        r = bob.get("/invite?token=nope")
        assert r.status_code == 404 and "This invitation is not valid any more" in r.text
        r = bob.get(f"/invite?token={token}")
        assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
        assert r.headers["referrer-policy"] == "no-referrer"                  # the address carries the token
        page = r.text
        assert "@alice invited you to join the page “&lt;b&gt;Notes&lt;/b&gt;”" in page and "<b>Notes</b>" not in page
        assert "on The lab (lab.example.org)" in page and "The invitation works until" in page
        here = f"/invite?token={token}"
        register_link = "/register?" + urlencode({"invite": code, "email": "bob@example.org", "next": here})
        assert f"href='{register_link.replace('&', '&amp;')}'>Create an account</a>" in page
        assert f"href='/login?{urlencode({'next': here})}'>Sign in</a>" in page
        # a code that no longer works is left out of the link
        with closing(db.connect()) as conn:
            accounts.set_invite_disabled(conn, code, True, "test")
            conn.commit()
        register_link = "/register?" + urlencode({"email": "bob@example.org", "next": here})
        assert f"href='{register_link.replace('&', '&amp;')}'>Create an account</a>" in bob.get(here).text
        set_setting("registration", "closed")
        page = bob.get(here).text
        assert "Create an account" not in page and "not taking new accounts right now. If you have one, sign in." in page


def test_the_invitation_page_for_an_address_with_an_account(client):
    with browser(client) as other:
        register(other, "carol")
    h = alice(client)
    send(client, h, email="carol@example.org")
    token = last_link("/invite")
    with browser(client) as carol:
        page = carol.get(f"/invite?token={token}").text
    here = f"/invite?token={token}"
    assert "Sign in with your Gamma Cloud account to accept." in page
    assert f"<a class='btn btn--primary' href='/login?{urlencode({'next': here})}'>Sign in</a>" in page
    assert "Create an account" not in page


def test_the_invitation_page_signed_in(client):
    h = alice(client)
    send(client, h)
    token = last_link("/invite")
    here = f"/invite?token={token}"
    with browser(client) as bob:
        register(bob, "bob", email="bob@work.example")
        page = bob.get(here).text
        assert "Confirm <b>bob@work.example</b> first" in page and "Send the link again" in page
        assert "Continue as" not in page
        r = bob.post("/api/verify/resend", json={"next": here})
        assert r.status_code == 200
        assert bob.post("/api/verify", json={"token": last_link("/verify")}).json()["next"] == here
        page = bob.get(here).text
        assert f"href='/invite/continue?token={token}'>Continue as @bob</a>" in page
        assert "Use another account" in page and "bob@work.example" in page


def test_continue_goes_to_the_server_with_the_invitation_id(client):
    h = alice(client)
    send(client, h)
    token = last_link("/invite")
    here = f"/invite?token={token}"
    with browser(client) as bob:
        r = bob.get(f"/invite/continue?token={token}", follow_redirects=False)
        assert r.status_code == 302 and r.headers["location"] == "/login?" + urlencode({"next": here})
        register(bob, "bob", email="bob@work.example")
        r = bob.get(f"/invite/continue?token={token}", follow_redirects=False)
        assert r.headers["location"] == here                                  # not confirmed yet
        verify(bob)
        assert bob.get("/invite/continue?token=nope", follow_redirects=False).headers["location"] == "/invite?token=nope"
        r = bob.get(f"/invite/continue?token={token}", follow_redirects=False)
        assert r.status_code == 302
        url = urlsplit(r.headers["location"])
        assert f"{url.scheme}://{url.netloc}{url.path}" == f"{SERVER}/api/auth/cloud/start"
        assert parse_qs(url.query) == {"next": [SHARE_NEXT], "invite": [INVITE_ID]}
        accepted = row()["accepted_at"]
        assert accepted
        bob.get(f"/invite/continue?token={token}", follow_redirects=False)
        assert row()["accepted_at"] == accepted                             # the first time is kept


# --- registering from it ------------------------------------------------------

def test_the_register_page_from_an_invitation(client):
    h = alice(client)
    send(client, h)
    token, code = last_link("/invite"), row()["code"]
    here = f"/invite?token={token}"
    with browser(client) as bob:
        page = bob.get("/register?" + urlencode({"invite": code, "email": "bob@example.org", "next": here})).text
        assert "name=email type=email value='bob@example.org'" in page and f"value='{code}'" in page
        assert "we mail it a link to confirm it" in page                      # the address is not confirmed yet
        assert f"<input type=hidden name=next value='{here}'>" in page and "name=invitation" not in page
        assert f"<a href='/login?{urlencode({'next': here})}'>Sign in</a>" in page
        assert "we mail it a link" not in bob.get("/register").text


@pytest.mark.parametrize("email", ["Bob@Example.org", "bob@work.example"])
def test_registering_from_an_invitation_needs_the_verify_link(client, email):
    """The invitation's link gets forwarded and shared, so registering from
    it confirms no address, not even the one it was mailed to: the verify
    link does, and brings the person back to the invitation."""
    h = alice(client)
    send(client, h)
    token, code = last_link("/invite"), row()["code"]
    here = f"/invite?token={token}"
    with browser(client) as bob:
        # "invitation", the field an older register page sent, confirms nothing now
        r = bob.post("/api/register", json={"email": email, "username": "bob", "password": "correct horse battery",
                                            "invite": code, "next": here, "invitation": token})
        assert r.status_code == 201, r.text
        assert r.json()["account"]["email_verified"] is False and r.json()["mailed"] is True
        assert r.json()["redirect"] == here                                  # which asks to confirm first
        assert mail.outbox[-1]["to"] == email.lower() and "Confirm <b>" in bob.get(here).text
        assert accounts.invite_state(code_row(code)) == "spent"
        assert bob.get(f"/invite/continue?token={token}", follow_redirects=False).headers["location"] == here
        assert "d.next || '/'" in bob.get(f"/verify?token={last_link('/verify')}").text
        r = bob.post("/api/verify", json={"token": last_link("/verify")})
        assert r.json()["account"]["email_verified"] is True and r.json()["next"] == here
        assert bob.get(f"/invite/continue?token={token}", follow_redirects=False).headers["location"].startswith(
            f"{SERVER}/api/auth/cloud/start?")


def test_registering_without_an_invitation_goes_home(client, monkeypatch):
    """A register page with no next lands on the Overview, and so does a
    verify mail that could not be sent, whose notice offers the resend."""
    with browser(client) as carol:
        r = carol.post("/api/register", json={"email": "carol@example.org", "username": "carol",
                                              "password": "correct horse battery", "invite": invite()})
        assert r.json()["redirect"] == "/" and carol.post("/api/verify", json={"token": last_link("/verify")}
                                                          ).json()["next"] == "/"

    def boom(*a, **k):
        raise mail.MailError("relay down")
    monkeypatch.setattr(mail, "send", boom)
    with browser(client) as dave:
        r = dave.post("/api/register", json={"email": "dave@example.org", "username": "dave",
                                             "password": "correct horse battery", "invite": invite(),
                                             "next": "/invite?token=abc_DEF-123"})
        assert r.status_code == 201 and r.json()["mailed"] is False and r.json()["redirect"] == "/?mail=failed"


def test_a_next_a_browser_would_read_as_another_host_goes_home(client):
    """A browser drops a tab or a line break from a URL: "/<tab>/evil.example"
    is "//evil.example", another host."""
    for n, bad in enumerate(("/\t/evil.example", "/\n/evil.example")):
        with browser(client) as eve:
            r = eve.post("/api/register", json={"email": f"eve{n}@example.org", "username": f"eve{n}",
                                                "password": "correct horse battery", "invite": invite(), "next": bad})
            assert r.status_code == 201 and r.json()["redirect"] == "/"
            assert eve.post("/api/verify", json={"token": last_link("/verify")}).json()["next"] == "/"
            page = eve.get("/register?" + urlencode({"next": bad})).text
            assert "evil.example" not in page
    page = client.get("/login?" + urlencode({"next": "/\t/evil.example"})).text
    assert 'location.href = "/"' in page and "evil.example" not in page
    register(client)
    assert client.get("/login?" + urlencode({"next": "/\n/evil.example"}), follow_redirects=False
                      ).headers["location"] == "/"


def test_a_provider_sign_up_from_the_register_page_comes_back(client, monkeypatch):
    for key, value in (("GITHUB_CLIENT_ID", "ghid"), ("GITHUB_CLIENT_SECRET", "ghsecret")):
        monkeypatch.setattr(config, key, value)
    monkeypatch.setattr(providers, "fetch_identity", lambda provider, code, *, verifier, nonce:
                        Identity("github", "42", "bob@example.org", True, "Bob", "bob"))
    h = alice(client)
    send(client, h)
    token, code = last_link("/invite"), row()["code"]
    here = f"/invite?token={token}"
    with browser(client) as bob:
        assert f'"next": "{here}"' in bob.get("/register?" + urlencode({"next": here})).text   # the tiles' next
        r = bob.post("/api/oauth/github/start", json={"next": here})
        state = parse_qs(urlsplit(r.json()["url"]).query)["state"][0]
        r = bob.get("/oauth/github/callback", params={"code": "c", "state": state}, follow_redirects=False)
        assert r.headers["location"] == "/signup/finish"
        r = bob.post("/api/oauth/signup", json={"username": "bob", "invite": code})
        assert r.status_code == 201 and r.json()["redirect"] == here


def test_the_sign_in_page_comes_back_to_the_invitation(client):
    register(client)
    here = "/invite?token=abc_DEF-123"
    login = "/login?" + urlencode({"next": here})
    assert client.get(login, follow_redirects=False).headers["location"] == here   # already signed in
    client.post("/api/logout")
    assert f'location.href = "{here}"' in client.get(login).text


# --- the operator's view, the purge, the upgrade ------------------------------

def test_the_admin_list_leaves_out_invitation_codes(client, capsys):
    from gammacloud import app as server
    h = alice(client)
    make_admin("alice")
    own = client.post("/api/admin/invites", json={"uses": 3, "note": "beta"}).json()["invite"]["code"]
    send(client, h)
    send(client, h, email="carol@example.org", invite_id=INVITE_ID + "2")
    unused, spent = row()["code"], row(INVITE_ID + "2")["code"]
    with browser(client) as carol:
        register(carol, "carol", code=spent)

    def listed():
        codes = [i["code"] for i in client.get("/api/admin/invites").json()["invites"]]
        manage.main(["invites"])
        out = capsys.readouterr().out
        return [c for c in (own, unused, spent) if c in codes and c in out]
    assert listed() == [own]
    with closing(db.connect()) as conn:
        conn.execute("UPDATE server_invitations SET expires_at = ?", (db.after(-1),))
        conn.commit()
    server.purge()
    assert row() is None and code_row(unused) is None and accounts.invite_state(code_row(spent)) == "spent"
    assert listed() == [own]                                    # the spent one stays hidden once its invitation is gone


def test_the_purge_drops_expired_invitations_and_a_purged_inviter_s(client):
    from gammacloud import app as server
    h = alice(client)
    send(client, h)
    send(client, h, invite_id=INVITE_ID + "2")
    token, code = last_link("/invite"), row(INVITE_ID + "2")["code"]
    with closing(db.connect()) as conn:
        conn.execute("UPDATE server_invitations SET expires_at = ? WHERE invite_id = ?", (db.after(-1), INVITE_ID))
        conn.commit()
    expired = row()["code"]
    with browser(client) as bob:
        assert bob.get(f"/invite?token={last_link('/invite')}").status_code == 200
    server.purge()
    assert row() is None and code_row(expired) is None and row(INVITE_ID + "2")
    with closing(db.connect()) as conn:
        accounts.delete(conn, row(INVITE_ID + "2")["inviter_account_id"])
        conn.commit()
    with browser(client) as bob:
        assert bob.get(f"/invite?token={token}").status_code == 200        # still valid through the grace period
    with closing(db.connect()) as conn:
        accounts.purge(conn, row(INVITE_ID + "2")["inviter_account_id"])
        conn.commit()
    assert row(INVITE_ID + "2") is None and code_row(code) is None


def test_an_expired_invitation_is_not_valid_before_the_purge(client):
    h = alice(client)
    send(client, h)
    with closing(db.connect()) as conn:
        conn.execute("UPDATE server_invitations SET expires_at = ?", (db.after(-1),))
        conn.commit()
    with browser(client) as bob:
        assert bob.get(f"/invite?token={last_link('/invite')}").status_code == 404


def test_step_13_adds_the_invitations_table():
    code = invite()
    with closing(db.connect()) as conn:
        conn.execute("DROP TABLE server_invitations")
        conn.execute("ALTER TABLE invites DROP COLUMN invitation")
        conn.execute("PRAGMA user_version = 12")
        conn.commit()
    assert steps_after(12)[0] == "server_invitations"
    assert db.ensure_current() == steps_after(12)
    assert db.ensure_current() == []
    with closing(db.connect()) as conn:
        assert conn.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
        assert conn.execute("SELECT COUNT(*) FROM server_invitations").fetchone()[0] == 0
        assert conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'idx_server_invitations_expires'").fetchone()
        assert "client_id" in [r[1] for r in conn.execute("PRAGMA table_info(server_invitations)")]
        assert conn.execute("SELECT invitation FROM invites WHERE code = ?", (code,)).fetchone()[0] == 0  # an admin's
        db._step_server_invitations(conn)                                      # re-runnable
        with pytest.raises(sqlite3.IntegrityError):                           # one row per server and id
            for _ in range(2):
                conn.execute("INSERT INTO server_invitations (token_hash, server_url, invite_id, kind, email, "
                             "email_canon, inviter_account_id, created_at, expires_at) VALUES (?, ?, ?, 'server', "
                             "'a@example.org', 'a@example.org', 'x', ?, ?)",
                             (db.new_token(), SERVER, INVITE_ID, db.now(), db.now()))

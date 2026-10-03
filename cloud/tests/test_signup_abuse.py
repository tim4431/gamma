"""What stands between open registration and one person making accounts
without limit: alias-aware uniqueness, the throwaway-domain list, and
rate-limit buckets that neither an IPv6 prefix nor a mail alias can walk
out of."""

from contextlib import closing

import pytest
from conftest import invite, last_link, set_setting

from gammacloud import accounts, db, mail, ratelimit


def signup(client, email, username, code=None):
    return client.post("/api/register", json={"email": email, "username": username,
                                              "password": "correct horse battery", "invite": code or invite()})


# --- one inbox, one account --------------------------------------------------

def test_email_canon_folds_aliases_of_one_inbox():
    # Gmail ignores dots and a +tag; both reach one mailbox.
    assert accounts.email_canon("f.o.o+gamma@gmail.com") == "foo@gmail.com"
    assert accounts.email_canon("foo@googlemail.com") == "foo@gmail.com"
    # a +tag alone elsewhere among the known providers
    assert accounts.email_canon("foo+x@outlook.com") == "foo@outlook.com"
    assert accounts.email_canon("f.o.o@outlook.com") == "f.o.o@outlook.com"
    # an unlisted domain is taken literally: a+b there may be its own mailbox
    assert accounts.email_canon("a+b@example.org") == "a+b@example.org"
    assert accounts.email_canon("a.b@example.org") == "a.b@example.org"
    # nothing to fold, and nothing to crash on
    assert accounts.email_canon("+x@gmail.com") == "+x@gmail.com"
    assert accounts.email_canon("nonsense") == "nonsense"


def test_one_gmail_inbox_cannot_become_two_accounts(client):
    code = invite(uses=10)
    assert signup(client, "foo@gmail.com", "foo", code).status_code == 201
    for alias in ("f.o.o@gmail.com", "foo+gamma@gmail.com", "F.o.O+x@GoogleMail.com"):
        r = signup(client, alias, "foo-two", code)
        assert r.status_code == 409, alias
        assert "already an account" in r.json()["detail"]


def test_an_unlisted_domain_keeps_its_tagged_addresses_apart(client):
    code = invite(uses=10)
    assert signup(client, "a@example.org", "aaa", code).status_code == 201
    assert signup(client, "a+b@example.org", "bbb", code).status_code == 201


def test_an_alias_signs_in_and_resets_the_account_it_names(client):
    assert signup(client, "foo+gamma@gmail.com", "foo").status_code == 201
    client.post("/api/verify", json={"token": last_link("/verify")})
    client.post("/api/logout")
    # the address as typed, and a bare alias of the same inbox
    for login in ("foo+gamma@gmail.com", "foo@gmail.com", "f.o.o@gmail.com"):
        ratelimit.clear()
        r = client.post("/api/login", json={"login": login, "password": "correct horse battery"})
        assert r.status_code == 200, login
        client.post("/api/logout")
    # the reset mail always goes to the address the account stores
    mail.outbox.clear()
    r = client.post("/api/reset/request", json={"email": "foo@gmail.com"})
    assert r.status_code == 200
    assert mail.outbox[-1]["to"] == "foo+gamma@gmail.com"


def test_email_change_refuses_an_alias_of_another_account(client):
    code = invite(uses=10)
    signup(client, "foo@gmail.com", "foo", code)
    signup(client, "bar@gmail.com", "bar", code)  # signed in as bar now
    client.post("/api/verify", json={"token": last_link("/verify")})
    r = client.post("/api/email/change", json={"new_email": "f.o.o+x@gmail.com",
                                               "password": "correct horse battery"})
    assert r.status_code == 409 and "already an account" in r.json()["detail"]
    # its own inbox under another alias is fine
    r = client.post("/api/email/change", json={"new_email": "b.a.r+notes@gmail.com",
                                               "password": "correct horse battery"})
    assert r.status_code == 200


def test_existing_rows_are_backfilled_by_the_upgrade(client):
    """The step writes email_canon for accounts that predate the column."""
    signup(client, "f.o.o@gmail.com", "foo")
    with closing(db.connect()) as conn:
        conn.execute("UPDATE accounts SET email_canon = ''")
        conn.commit()
    with closing(db.connect()) as conn:
        db._step_email_canon(conn)
        db._step_email_canon(conn)  # re-runnable, as every step must be
        conn.commit()
        assert conn.execute("SELECT email_canon FROM accounts").fetchone()[0] == "foo@gmail.com"


# --- throwaway mail ----------------------------------------------------------

def test_disposable_domains_are_refused_at_registration(client):
    code = invite(uses=10)
    for email in ("x@mailinator.com", "x@sub.mailinator.com", "x@YOPMAIL.com", "x@guerrillamail.net"):
        r = signup(client, email, "xyz", code)
        assert r.status_code == 400, email
        assert "not accepted" in r.json()["detail"]
    assert signup(client, "x@example.org", "xyz", code).status_code == 201


def test_the_blocklist_can_be_extended_without_a_release(client):
    set_setting("blocked_email_domains", "spam.example")
    code = invite(uses=10)
    assert signup(client, "x@spam.example", "xyz", code).status_code == 400
    assert signup(client, "x@deep.sub.spam.example", "xyz", code).status_code == 400
    assert signup(client, "x@notspam.example", "xyz", code).status_code == 201


def test_a_reset_still_works_for_an_address_whose_domain_is_now_blocked(client):
    signup(client, "x@example.org", "xyz")
    set_setting("blocked_email_domains", "example.org")
    assert client.post("/api/reset/request", json={"email": "x@example.org"}).status_code == 200


# --- the rate-limit bucket ---------------------------------------------------

@pytest.mark.parametrize("ip, expected", [
    ("203.0.113.9", "203.0.113.9"),                               # IPv4: one address, one customer
    ("2001:db8:1:2:3:4:5:6", "2001:db8:1:2::/64"),                # IPv6: the whole /64 is one customer
    ("2001:db8:1:2::1", "2001:db8:1:2::/64"),
    ("2001:db8:1:3::1", "2001:db8:1:3::/64"),                     # a different /64 is a different one
    ("::ffff:203.0.113.9", "203.0.113.9"),                        # not every v4-mapped address in one bucket
    ("?", "?"),                                                   # no peer address
    ("garbage", "garbage"),
])
def test_ip_bucket(ip, expected):
    assert ratelimit.ip_bucket(ip) == expected


def test_a_prefix_rotation_shares_the_register_allowance(client):
    set_setting("registration", "open")
    body = {"password": "correct horse battery"}
    codes = []
    for i in range(7):
        r = client.post("/api/register", json={"email": f"p{i}@example.org", "username": f"person{i}", **body},
                        headers={"cf-connecting-ip": f"2001:db8:aa:bb::{i + 1}"})
        codes.append(r.status_code)
    # five registrations an hour, counted across the /64 rather than per address
    assert codes == [201] * 5 + [429, 429]
    # a different /64 has its own allowance
    r = client.post("/api/register", json={"email": "q@example.org", "username": "quentin", **body},
                    headers={"cf-connecting-ip": "2001:db8:aa:cc::1"})
    assert r.status_code == 201


def test_the_password_form_and_provider_sign_up_count_separately(client):
    """Five an hour on the form, twenty through Google/GitHub: neither eats the other's allowance."""
    set_setting("registration", "open")
    ip = {"cf-connecting-ip": "203.0.113.50"}
    for _ in range(5):  # refused for want of a provider flow, but counted
        assert client.post("/api/oauth/signup", json={"username": "someone"}, headers=ip).status_code != 429
    body = {"password": "correct horse battery"}
    codes = [client.post("/api/register", json={"email": f"s{i}@example.org", "username": f"sep{i}", **body},
                         headers=ip).status_code for i in range(6)]
    assert codes == [201] * 5 + [429]
    assert client.post("/api/oauth/signup", json={"username": "someone"}, headers=ip).status_code != 429


# --- one inbox, one allowance -------------------------------------------------
# by_login and by_email resolve every alias of an inbox to the one account, so
# the per-name windows count the inbox (accounts.login_bucket), not the spelling.

ALIASES = ("foo@gmail.com", "foo+1@gmail.com", "f.oo@gmail.com", "F.O.O+x@googlemail.com")


def test_login_bucket_is_the_inbox_or_the_username():
    assert len({accounts.login_bucket(a) for a in ALIASES}) == 1
    assert accounts.login_bucket("a+b@example.org") == "a+b@example.org"  # unlisted domain: taken literally
    assert accounts.login_bucket("  Alice ") == "alice"


def test_login_aliases_share_one_allowance(client):
    signup(client, "foo@gmail.com", "foo")
    client.post("/api/verify", json={"token": last_link("/verify")})
    client.post("/api/logout")
    # ten wrong guesses, each under another spelling and from another address,
    # so only the per-name window can be what refuses the eleventh
    for i in range(10):
        r = client.post("/api/login", json={"login": ALIASES[i % len(ALIASES)], "password": "wrong"},
                        headers={"cf-connecting-ip": f"203.0.113.{i + 1}"})
        assert r.status_code == 401, i
    r = client.post("/api/login", json={"login": "foo+11@gmail.com", "password": "correct horse battery"},
                    headers={"cf-connecting-ip": "203.0.113.100"})
    assert r.status_code == 429
    # the authorize page's sign-in counts in the same window
    from test_oidc import authorize_params, pkce, request_id_from
    rid = request_id_from(client.get("/authorize", params=authorize_params(pkce()[1])).text)
    r = client.post("/authorize/login", json={"request_id": rid, "login": "f.o.o@gmail.com",
                                              "password": "correct horse battery"},
                    headers={"cf-connecting-ip": "203.0.113.101"})
    assert r.status_code == 429
    # another inbox is not affected
    r = client.post("/api/login", json={"login": "bar@gmail.com", "password": "wrong"},
                    headers={"cf-connecting-ip": "203.0.113.102"})
    assert r.status_code == 401


def test_reset_mails_to_one_inbox_share_one_allowance(client):
    signup(client, "foo@gmail.com", "foo")
    mail.outbox.clear()
    # three an hour per inbox; the per-IP window (five) is not what trips here
    for alias in ALIASES[:3]:
        assert client.post("/api/reset/request", json={"email": alias}).status_code == 200, alias
    assert client.post("/api/reset/request", json={"email": ALIASES[3]}).status_code == 429
    assert [m["to"] for m in mail.outbox] == ["foo@gmail.com"] * 3
    # another inbox from the same address still gets its mail
    assert client.post("/api/reset/request", json={"email": "bar@gmail.com"}).status_code == 200
